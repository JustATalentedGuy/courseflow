import asyncio
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from groq import AsyncGroq
from pydub import AudioSegment
from pydub.silence import detect_silence
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled, YouTubeTranscriptApi

from app.core.config import settings
from app.core.exceptions import (
    PermanentAPIError,
    QuotaExhaustedError,
    TemporaryAPIError,
    TranscriptExtractionError,
    TranscriptValidationError,
)
from app.models.transcript import Transcript
from app.models.video import Video
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.external_api import call_external_async

logger = structlog.get_logger()

MAX_GROQ_FILE_BYTES = 25 * 1024 * 1024
WHISPER_REQUEST_LIMIT = 2000
WHISPER_SECONDS_HOUR_LIMIT = 7200
CHUNK_OVERLAP_MS = 2000


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_seconds: float


def _collapse_spaces(text: str) -> str:
    return " ".join(text.split())


def _join_broken_caption_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text

    joined: list[str] = []
    for line in lines:
        if joined and not re.search(r"[.!?]$", joined[-1]):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return " ".join(joined)


def clean_transcript_text(raw_text: str, require_min_words: bool = False) -> str:
    text = re.sub(r"\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d+)?", " ", raw_text)
    text = re.sub(r"\[[\w\s]+\]", " ", text)
    text = re.sub(r"[♪♫♬]", " ", text)
    text = re.sub(r"\b(?:um+|uh+|hmm+)\b", " ", text, flags=re.IGNORECASE)
    text = _join_broken_caption_lines(text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)
    text = _collapse_spaces(text)

    if not text:
        raise TranscriptValidationError("Transcript text is empty after cleaning")
    if require_min_words and len(text.split()) <= 10:
        raise TranscriptValidationError("Transcript must contain more than 10 words")
    return text


def _caption_segments_to_transcript(
    youtube_video_id: str,
    source: str,
    language: str,
    captions: list[dict],
) -> NormalisedTranscript:
    segments: list[TranscriptSegment] = []

    for caption in captions:
        cleaned = clean_transcript_text(caption.get("text", ""))
        if not cleaned:
            continue
        start = float(caption.get("start", 0))
        duration = float(caption.get("duration", 0))
        end = start + duration
        if end <= start:
            continue
        segments.append(TranscriptSegment(start=start, end=end, text=cleaned, speaker=None))

    if not segments:
        raise TranscriptValidationError("Transcript contains no valid segments")

    full_text = _collapse_spaces(" ".join(segment.text for segment in segments))
    duration_seconds = max(segment.end for segment in segments)
    return NormalisedTranscript(
        video_id=youtube_video_id,
        source=source,
        language=language,
        duration_seconds=duration_seconds,
        segments=segments,
        full_text=full_text,
        word_count=len(full_text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def fetch_youtube_captions(youtube_video_id: str) -> NormalisedTranscript | None:
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(youtube_video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"])
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(["en"])
        captions = transcript.fetch()
        source = "youtube_captions"
        language = transcript.language_code
    except NoTranscriptFound:
        try:
            transcript = next(iter(transcript_list))
            logger.warning(
                "transcript.language.fallback",
                youtube_video_id=youtube_video_id,
                language=transcript.language_code,
            )
            captions = transcript.fetch()
            source = "youtube_captions"
            language = transcript.language_code
        except (NoTranscriptFound, StopIteration):
            return None
    except TranscriptsDisabled:
        return None

    return _caption_segments_to_transcript(
        youtube_video_id=youtube_video_id,
        source=source,
        language=language,
        captions=captions,
    )


async def _ensure_whisper_quota(video: Video) -> None:
    if not settings.groq_api_key or settings.groq_api_key == "your_groq_key_here":
        raise QuotaExhaustedError("Groq API key is not configured for Whisper fallback")

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        requests_key = f"groq:quota:{video.user_id}:whisper_requests"
        seconds_key = f"groq:quota:{video.user_id}:whisper_seconds_hour"
        request_count = int(await redis.get(requests_key) or 0)
        seconds_count = int(await redis.get(seconds_key) or 0)
        requested_seconds = int(video.duration_seconds or 0)

        if request_count >= WHISPER_REQUEST_LIMIT:
            raise QuotaExhaustedError("Groq Whisper request quota exhausted")
        if seconds_count + requested_seconds > WHISPER_SECONDS_HOUR_LIMIT:
            raise QuotaExhaustedError("Groq Whisper hourly audio-seconds quota exhausted")
    finally:
        await redis.aclose()


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = midnight + timedelta(days=1)
    return int((next_midnight - now).total_seconds())


async def _record_whisper_usage(video: Video, chunk_count: int) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        requests_key = f"groq:quota:{video.user_id}:whisper_requests"
        seconds_key = f"groq:quota:{video.user_id}:whisper_seconds_hour"
        requests = await redis.incrby(requests_key, chunk_count)
        seconds = await redis.incrby(seconds_key, int(video.duration_seconds or 0))
        if requests == chunk_count:
            await redis.expire(requests_key, _seconds_until_midnight_utc())
        if seconds == int(video.duration_seconds or 0):
            await redis.expire(seconds_key, 3600)
        logger.info(
            "quota.incremented",
            user_id=str(video.user_id),
            key="whisper",
            requests=chunk_count,
            seconds=int(video.duration_seconds or 0),
        )
    finally:
        await redis.aclose()


def _download_audio(video: Video, temp_path: Path) -> Path:
    output_template = str(temp_path / f"{video.youtube_video_id}.%(ext)s")
    url = f"https://www.youtube.com/watch?v={video.youtube_video_id}"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--quiet",
                "--no-warnings",
                "--format",
                "bestaudio/best",
                "--output",
                output_template,
                "--extract-audio",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "64K",
                "--postprocessor-args",
                "ffmpeg:-ac 1 -ar 16000",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TemporaryAPIError("YouTube audio download timed out after 30 seconds") from exc
    if result.returncode != 0:
        raise PermanentAPIError("YouTube audio download failed")

    mp3_path = temp_path / f"{video.youtube_video_id}.mp3"
    if not mp3_path.exists():
        matches = list(temp_path.glob("*.mp3"))
        if not matches:
            raise TranscriptExtractionError("Audio download failed")
        return matches[0]
    return mp3_path


def _silence_boundaries(audio: AudioSegment) -> list[int]:
    silences = detect_silence(audio, min_silence_len=700, silence_thresh=audio.dBFS - 16)
    return [int((start + end) / 2) for start, end in silences]


def _split_audio_if_needed(audio_path: Path, temp_path: Path) -> list[AudioChunk]:
    if audio_path.stat().st_size <= MAX_GROQ_FILE_BYTES:
        return [AudioChunk(path=audio_path, offset_seconds=0)]

    audio = AudioSegment.from_file(audio_path)
    bytes_per_ms = audio_path.stat().st_size / max(len(audio), 1)
    max_ms = int((MAX_GROQ_FILE_BYTES * 0.90) / bytes_per_ms)
    boundaries = _silence_boundaries(audio)

    chunks: list[AudioChunk] = []
    start_ms = 0
    chunk_index = 0
    while start_ms < len(audio):
        target_end = min(start_ms + max_ms, len(audio))
        nearby_boundaries = [
            boundary
            for boundary in boundaries
            if start_ms + 30_000 < boundary <= target_end
        ]
        end_ms = nearby_boundaries[-1] if nearby_boundaries else target_end
        chunk_start = max(start_ms - CHUNK_OVERLAP_MS, 0)
        chunk_audio = audio[chunk_start:end_ms]
        chunk_path = temp_path / f"whisper_chunk_{chunk_index}.mp3"
        chunk_audio.export(chunk_path, format="mp3", bitrate="64k")
        chunks.append(AudioChunk(path=chunk_path, offset_seconds=chunk_start / 1000))
        if end_ms >= len(audio):
            break
        start_ms = end_ms
        chunk_index += 1

    return chunks


async def _transcribe_chunk(client: AsyncGroq, chunk: AudioChunk) -> list[TranscriptSegment]:
    audio_bytes = chunk.path.read_bytes()

    async def create_transcription():
        return await client.audio.transcriptions.create(
            file=(chunk.path.name, audio_bytes),
            model="whisper-large-v3-turbo",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    response = await call_external_async(create_transcription, "Groq Whisper")
    raw_segments = getattr(response, "segments", None) or []
    segments: list[TranscriptSegment] = []
    for raw_segment in raw_segments:
        if isinstance(raw_segment, dict):
            raw_start = raw_segment.get("start", 0)
            raw_end = raw_segment.get("end", 0)
            text = raw_segment.get("text", "")
        else:
            raw_start = getattr(raw_segment, "start", 0)
            raw_end = getattr(raw_segment, "end", 0)
            text = getattr(raw_segment, "text", "")
        start = float(raw_start) + chunk.offset_seconds
        end = float(raw_end) + chunk.offset_seconds
        try:
            cleaned = clean_transcript_text(text)
        except TranscriptValidationError:
            continue
        if end > start and cleaned:
            segments.append(TranscriptSegment(start=start, end=end, text=cleaned, speaker=None))
    return segments


def _stitch_segments(chunks: list[list[TranscriptSegment]]) -> list[TranscriptSegment]:
    stitched: list[TranscriptSegment] = []
    seen_overlap_text: set[tuple[int, str]] = set()
    for chunk_segments in chunks:
        for segment in chunk_segments:
            key = (int(segment.start), segment.text.lower())
            if stitched and segment.start < stitched[-1].end:
                if key in seen_overlap_text or segment.text.lower() in stitched[-1].text.lower():
                    continue
            seen_overlap_text.add(key)
            stitched.append(segment)
    return stitched


async def transcribe_with_whisper(video: Video) -> NormalisedTranscript:
    await _ensure_whisper_quota(video)

    with tempfile.TemporaryDirectory(prefix="courseflow-whisper-") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            audio_path = await asyncio.to_thread(_download_audio, video, temp_path)
            chunks = await asyncio.to_thread(_split_audio_if_needed, audio_path, temp_path)
            client = AsyncGroq(api_key=settings.groq_api_key)
            chunk_segments = []
            for chunk in chunks:
                chunk_segments.append(await _transcribe_chunk(client, chunk))

            segments = _stitch_segments(chunk_segments)
            if not segments:
                raise TranscriptExtractionError("Whisper returned no transcript segments")

            full_text = _collapse_spaces(" ".join(segment.text for segment in segments))
            transcript = NormalisedTranscript(
                video_id=video.youtube_video_id,
                source="groq_whisper",
                language="en",
                duration_seconds=max(segment.end for segment in segments),
                segments=segments,
                full_text=full_text,
                word_count=len(full_text.split()),
                fetched_at=datetime.now(UTC).isoformat(),
            )
            await _record_whisper_usage(video, len(chunks))
            return transcript
        finally:
            for path in temp_path.glob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)


async def extract_transcript(video: Video, db: AsyncSession) -> NormalisedTranscript:
    try:
        transcript = await asyncio.to_thread(fetch_youtube_captions, video.youtube_video_id)
    except TranscriptValidationError as exc:
        logger.warning(
            "transcript.youtube.invalid",
            video_id=video.youtube_video_id,
            error=str(exc),
        )
        transcript = None

    if transcript is None:
        try:
            transcript = await transcribe_with_whisper(video)
        except QuotaExhaustedError:
            raise
        except (TemporaryAPIError, PermanentAPIError):
            raise
        except TranscriptExtractionError as exc:
            video.status = "failed"
            video.error_message = str(exc)
            await db.commit()
            raise
        except Exception as exc:
            message = "Video is unavailable or age-restricted"
            video.status = "failed"
            video.error_message = message
            await db.commit()
            raise TranscriptExtractionError(message) from exc

    video.transcript_source = transcript.source
    return transcript


def validate_transcript_for_storage(transcript: NormalisedTranscript) -> NormalisedTranscript:
    try:
        payload = transcript.model_dump()
        return NormalisedTranscript.model_validate(payload)
    except Exception as exc:
        raise TranscriptValidationError(str(exc)) from exc


async def store_transcript(
    transcript: NormalisedTranscript,
    video: Video,
    db: AsyncSession,
) -> Transcript:
    validated = validate_transcript_for_storage(transcript)

    if any(not segment.text.strip() for segment in validated.segments):
        raise TranscriptValidationError("Transcript contains empty segments")

    existing = await db.scalar(
        select(Transcript).where(
            Transcript.video_id == video.id,
            Transcript.user_id == video.user_id,
        )
    )

    fetched_at = datetime.fromisoformat(validated.fetched_at.replace("Z", "+00:00"))
    if existing:
        existing.source = validated.source
        existing.language = validated.language
        existing.duration_seconds = validated.duration_seconds
        existing.full_text = validated.full_text
        existing.word_count = validated.word_count
        existing.segments_json = [segment.model_dump() for segment in validated.segments]
        existing.fetched_at = fetched_at
        record = existing
    else:
        record = Transcript(
            video_id=video.id,
            user_id=video.user_id,
            source=validated.source,
            language=validated.language,
            duration_seconds=validated.duration_seconds,
            full_text=validated.full_text,
            word_count=validated.word_count,
            segments_json=[segment.model_dump() for segment in validated.segments],
            fetched_at=fetched_at,
        )
        db.add(record)

    video.transcript_source = validated.source
    await db.commit()
    await db.refresh(record)
    return record


def transcript_record_to_schema(record: Transcript, youtube_video_id: str) -> NormalisedTranscript:
    return NormalisedTranscript(
        video_id=youtube_video_id,
        source=record.source,
        language=record.language,
        duration_seconds=record.duration_seconds,
        segments=[TranscriptSegment.model_validate(segment) for segment in record.segments_json],
        full_text=record.full_text,
        word_count=record.word_count,
        fetched_at=record.fetched_at.isoformat(),
    )
