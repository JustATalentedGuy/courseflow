import asyncio
import hashlib
import inspect
import math
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from groq import AsyncGroq
from sqlalchemy import select
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
)

from app.core.config import settings
from app.core.exceptions import (
    PermanentAPIError,
    GroqQuotaWaitError,
    QuotaExhaustedError,
    TemporaryAPIError,
    TranscriptExtractionError,
    TranscriptValidationError,
)
from app.models.transcript import Transcript
from app.models.groq import GroqUsageEvent, WhisperTranscriptionChunk
from app.models.video import Video
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.external_api import call_external_async
from app.services.object_storage import delete_object, read_object
from app.services.quota import QuotaManager
from app.services.youtube_access import (
    build_transcript_api,
    is_youtube_block_error,
    redact_youtube_error,
    ytdlp_proxy_args,
    youtube_block_message,
)

logger = structlog.get_logger()

MAX_GROQ_FILE_BYTES = 25 * 1024 * 1024
CHUNK_OVERLAP_MS = 2000
MAX_GROQ_CHUNK_SECONDS = 2400


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_seconds: float
    duration_seconds: float
    fingerprint: str


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
        if isinstance(caption, dict):
            text = caption.get("text", "")
            start_value = caption.get("start", 0)
            duration_value = caption.get("duration", 0)
        else:
            text = getattr(caption, "text", "")
            start_value = getattr(caption, "start", 0)
            duration_value = getattr(caption, "duration", 0)
        cleaned = clean_transcript_text(text)
        if not cleaned:
            continue
        start = float(start_value)
        duration = float(duration_value)
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


def _fetch_youtube_captions_once(youtube_video_id: str) -> NormalisedTranscript | None:
    try:
        transcript_list = build_transcript_api().list(youtube_video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(["en"])
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(["en"])
        fetched = transcript.fetch()
        captions = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
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
            fetched = transcript.fetch()
            captions = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
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


def fetch_youtube_captions(youtube_video_id: str) -> NormalisedTranscript | None:
    attempts = 3 if settings.youtube_proxy_url else 1
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_youtube_captions_once(youtube_video_id)
        except (RequestBlocked, IpBlocked):
            if attempt == attempts:
                raise
            time.sleep(0.5 * attempt)
    return None


async def _ensure_whisper_quota(video: Video) -> None:
    del video
    if not settings.groq_api_key or settings.groq_api_key == "your_groq_key_here":
        raise QuotaExhaustedError("Groq API key is not configured for Whisper fallback")


def _download_audio(video: Video, temp_path: Path) -> Path:
    output_template = str(temp_path / f"{video.youtube_video_id}.%(ext)s")
    url = f"https://www.youtube.com/watch?v={video.youtube_video_id}"
    timeout_seconds = _audio_download_timeout_seconds(video.duration_seconds)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--quiet",
                "--no-warnings",
                "--format",
                "bestaudio[abr<=96]/bestaudio/best",
                "--socket-timeout",
                "30",
                "--retries",
                "5",
                "--fragment-retries",
                "5",
                "--retry-sleep",
                "exp=1:5",
                *ytdlp_proxy_args(),
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
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TemporaryAPIError(
            f"YouTube audio download timed out after {timeout_seconds} seconds"
        ) from exc
    if result.returncode != 0:
        safe_error = redact_youtube_error(result.stderr.strip())
        if is_youtube_block_error(safe_error):
            error_type = TemporaryAPIError if settings.youtube_proxy_url else PermanentAPIError
            raise error_type(youtube_block_message())
        detail = safe_error.splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise PermanentAPIError(f"YouTube audio download failed{suffix}")

    mp3_path = temp_path / f"{video.youtube_video_id}.mp3"
    if not mp3_path.exists():
        matches = list(temp_path.glob("*.mp3"))
        if not matches:
            raise TranscriptExtractionError("Audio download failed")
        return matches[0]
    return mp3_path


def _audio_download_timeout_seconds(duration_seconds: int | None) -> int:
    duration_minutes = max(1, math.ceil((duration_seconds or 0) / 60))
    calculated = (
        duration_minutes * settings.youtube_audio_download_timeout_seconds_per_minute
    )
    return max(
        settings.youtube_audio_download_min_timeout_seconds,
        min(settings.youtube_audio_download_max_timeout_seconds, calculated),
    )


def _probe_audio_duration_seconds(audio_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TemporaryAPIError("Audio duration probe timed out after 30 seconds") from exc
    if result.returncode != 0:
        raise TranscriptExtractionError("Could not determine downloaded audio duration")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise TranscriptExtractionError("Downloaded audio duration is invalid") from exc
    if duration <= 0:
        raise TranscriptExtractionError("Downloaded audio duration must be positive")
    return duration


def _extract_audio_chunk(
    audio_path: Path,
    chunk_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(audio_path),
                "-t",
                f"{duration_seconds:.3f}",
                "-codec",
                "copy",
                "-y",
                str(chunk_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TemporaryAPIError("Audio chunk extraction timed out after 120 seconds") from exc
    if result.returncode != 0 or not chunk_path.exists():
        raise TranscriptExtractionError("Audio chunk extraction failed")


def _split_audio_if_needed(audio_path: Path, temp_path: Path) -> list[AudioChunk]:
    def audio_chunk(path: Path, offset_seconds: float, duration_seconds: float) -> AudioChunk:
        return AudioChunk(
            path=path,
            offset_seconds=offset_seconds,
            duration_seconds=duration_seconds,
            fingerprint=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    total_duration = _probe_audio_duration_seconds(audio_path)
    if audio_path.stat().st_size <= MAX_GROQ_FILE_BYTES:
        return [audio_chunk(audio_path, 0, total_duration)]

    chunks: list[AudioChunk] = []
    nominal_start = 0.0
    chunk_index = 0
    overlap_seconds = CHUNK_OVERLAP_MS / 1000
    while nominal_start < total_duration:
        nominal_end = min(nominal_start + MAX_GROQ_CHUNK_SECONDS, total_duration)
        chunk_start = max(nominal_start - overlap_seconds, 0)
        chunk_duration = nominal_end - chunk_start
        chunk_path = temp_path / f"whisper_chunk_{chunk_index}.mp3"
        _extract_audio_chunk(
            audio_path,
            chunk_path,
            chunk_start,
            chunk_duration,
        )
        if chunk_path.stat().st_size > MAX_GROQ_FILE_BYTES:
            raise TranscriptExtractionError("Audio chunk exceeds Groq's 25 MB upload limit")
        chunks.append(
            audio_chunk(
                chunk_path,
                chunk_start,
                chunk_duration,
            )
        )
        nominal_start = nominal_end
        chunk_index += 1

    return chunks


async def _transcribe_chunk(
    client: AsyncGroq,
    chunk: AudioChunk,
) -> tuple[list[TranscriptSegment], dict[str, str], str | None]:
    audio_bytes = chunk.path.read_bytes()

    async def create_transcription():
        return await client.audio.transcriptions.with_raw_response.create(
            file=(chunk.path.name, audio_bytes),
            model=settings.groq_whisper_model,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    raw_response = await call_external_async(
        create_transcription,
        "Groq Whisper",
        passthrough_status_codes={429},
    )
    response = raw_response.parse()
    if inspect.isawaitable(response):
        response = await response
    headers = {str(key).lower(): str(value) for key, value in raw_response.headers.items()}
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
    x_groq = getattr(response, "x_groq", None)
    request_id = headers.get("x-request-id") or getattr(x_groq, "id", None)
    return segments, headers, request_id


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


def _error_headers(exc: Exception) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _error_body(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    text = getattr(response, "text", "") if response is not None else ""
    return text if isinstance(text, str) and text else str(exc)


async def _prepare_whisper_rows(
    db: AsyncSession,
    video: Video,
    chunks: list[AudioChunk],
) -> list[WhisperTranscriptionChunk]:
    existing = list(
        await db.scalars(
            select(WhisperTranscriptionChunk)
            .where(WhisperTranscriptionChunk.video_id == video.id)
            .order_by(WhisperTranscriptionChunk.chunk_index)
        )
    )
    if len(existing) != len(chunks):
        await db.execute(
            delete(WhisperTranscriptionChunk).where(
                WhisperTranscriptionChunk.video_id == video.id
            )
        )
        existing = []

    if not existing:
        existing = [
            WhisperTranscriptionChunk(
                video_id=video.id,
                course_id=video.course_id,
                user_id=video.user_id,
                chunk_index=index,
                offset_seconds=chunk.offset_seconds,
                duration_seconds=max(1, math.ceil(chunk.duration_seconds)),
                billable_seconds=max(10, math.ceil(chunk.duration_seconds)),
                audio_fingerprint=chunk.fingerprint,
                state="pending",
                segments_json=[],
            )
            for index, chunk in enumerate(chunks)
        ]
        db.add_all(existing)
        await db.commit()
        return existing

    changed = False
    for row, chunk in zip(existing, chunks, strict=True):
        if row.audio_fingerprint != chunk.fingerprint:
            row.offset_seconds = chunk.offset_seconds
            row.duration_seconds = max(1, math.ceil(chunk.duration_seconds))
            row.billable_seconds = max(10, math.ceil(chunk.duration_seconds))
            row.audio_fingerprint = chunk.fingerprint
            row.state = "pending"
            row.segments_json = []
            row.request_id = None
            row.retry_at = None
            row.error_message = None
            changed = True
    if changed:
        await db.commit()
    return existing


async def _transcribe_audio_path(
    video: Video,
    db: AsyncSession,
    audio_path: Path,
    temp_path: Path,
) -> NormalisedTranscript:
    chunks = await asyncio.to_thread(_split_audio_if_needed, audio_path, temp_path)
    client = AsyncGroq(api_key=settings.groq_api_key, max_retries=0)
    quota = QuotaManager()
    rows = await _prepare_whisper_rows(db, video, chunks)
    chunk_segments: list[list[TranscriptSegment]] = []
    try:
        for chunk, row in zip(chunks, rows, strict=True):
            if row.state == "completed" and row.segments_json:
                chunk_segments.append(
                    [
                        TranscriptSegment.model_validate(segment)
                        for segment in row.segments_json
                    ]
                )
                continue

            reservation = await quota.reserve_whisper(db, row.billable_seconds)
            row.state = "processing"
            row.retry_at = None
            row.error_message = None
            await db.commit()
            try:
                segments, headers, request_id = await _transcribe_chunk(client, chunk)
            except Exception as exc:
                await quota.release(reservation)
                if getattr(exc, "status_code", None) == 429:
                    wait_error = await quota.wait_from_headers(
                        settings.groq_whisper_model,
                        _error_headers(exc),
                        _error_body(exc),
                    )
                    row.state = "rate_limited"
                    row.retry_at = datetime.now(UTC) + timedelta(
                        seconds=wait_error.retry_after
                    )
                    row.error_message = str(wait_error)
                    await db.commit()
                    raise wait_error from exc
                row.state = "pending"
                row.error_message = str(exc)
                await db.commit()
                raise

            await quota.reconcile(
                reservation,
                headers=headers,
                audio_seconds=row.billable_seconds,
            )
            row.state = "completed"
            row.segments_json = [segment.model_dump() for segment in segments]
            row.request_id = request_id
            row.error_message = None
            db.add(
                GroqUsageEvent(
                    model=settings.groq_whisper_model,
                    user_id=video.user_id,
                    video_id=video.id,
                    course_id=video.course_id,
                    mode="whisper",
                    prompt_tokens=0,
                    completion_tokens=0,
                    cached_tokens=0,
                    charged_tokens=0,
                    audio_seconds=row.billable_seconds,
                    request_id=request_id or f"whisper-{row.id}",
                )
            )
            await db.commit()
            chunk_segments.append(segments)
    finally:
        await quota.close()
        await client.close()

    segments = _stitch_segments(chunk_segments)
    if not segments:
        raise TranscriptExtractionError("Whisper returned no transcript segments")

    full_text = _collapse_spaces(" ".join(segment.text for segment in segments))
    return NormalisedTranscript(
        video_id=video.youtube_video_id,
        source="groq_whisper",
        language="en",
        duration_seconds=max(segment.end for segment in segments),
        segments=segments,
        full_text=full_text,
        word_count=len(full_text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )


async def transcribe_uploaded_audio(
    video: Video,
    db: AsyncSession,
    object_uri: str,
) -> NormalisedTranscript:
    await _ensure_whisper_quota(video)

    with tempfile.TemporaryDirectory(prefix="courseflow-whisper-") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            audio_path = temp_path / "edge_audio.mp3"
            audio_path.write_bytes(await read_object(object_uri))
            return await _transcribe_audio_path(video, db, audio_path, temp_path)
        finally:
            for path in temp_path.glob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)


async def transcribe_with_whisper(
    video: Video,
    db: AsyncSession,
) -> NormalisedTranscript:
    await _ensure_whisper_quota(video)

    with tempfile.TemporaryDirectory(prefix="courseflow-whisper-") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            audio_path = await asyncio.to_thread(_download_audio, video, temp_path)
            return await _transcribe_audio_path(video, db, audio_path, temp_path)
        finally:
            for path in temp_path.glob("*"):
                if path.is_file():
                    path.unlink(missing_ok=True)


async def extract_transcript(video: Video, db: AsyncSession) -> NormalisedTranscript:
    try:
        transcript = await asyncio.to_thread(fetch_youtube_captions, video.youtube_video_id)
    except Exception as exc:
        logger.warning(
            "transcript.youtube.failed",
            video_id=video.youtube_video_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        transcript = None

    if transcript is None:
        try:
            transcript = await transcribe_with_whisper(video, db)
        except GroqQuotaWaitError:
            raise
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
