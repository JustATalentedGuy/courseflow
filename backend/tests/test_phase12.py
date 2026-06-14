from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import GroqQuotaWaitError, ValidationError
from app.core.security import hash_password
from app.models.course import Course
from app.models.groq import GroqUsageEvent, WhisperTranscriptionChunk
from app.models.notes import Notes
from app.models.user import User
from app.models.video import Video
from app.schemas.transcript import TranscriptSegment
from app.services.export import (
    build_course_notes_markdown,
    export_course_notes_markdown,
    export_course_notes_pdf,
)
from app.services.quota import ModelQuotaProfile, QuotaManager, _parse_duration_seconds
from app.services.transcript import (
    AudioChunk,
    _audio_download_timeout_seconds,
    _download_audio,
    _split_audio_if_needed,
    _prepare_whisper_rows,
    _transcribe_chunk,
    transcribe_with_whisper,
)


@pytest.mark.asyncio
async def test_course_export_combines_notes_in_playlist_order(db_session):
    user, course, videos = await _completed_course(db_session)

    _, markdown = await build_course_notes_markdown(course.id, user.id, db_session)

    assert markdown.startswith("# System Design\n")
    assert markdown.index("Lesson 1: First Lesson") < markdown.index("Lesson 2: Second Lesson")
    assert "[First Lesson](#lesson-1)" in markdown
    assert "[Second Lesson](#lesson-2)" in markdown
    assert markdown.count("# First Lesson") == 0
    assert "### Summary" in markdown
    assert markdown.count("courseflow-page-break") == 1
    assert (await export_course_notes_markdown(course.id, user.id, db_session)).decode() == markdown


@pytest.mark.asyncio
async def test_course_export_rejects_incomplete_course(db_session):
    user, course, videos = await _completed_course(db_session)
    videos[1].status = "processing"
    await db_session.commit()

    with pytest.raises(ValidationError, match="every video"):
        await export_course_notes_markdown(course.id, user.id, db_session)


@pytest.mark.asyncio
async def test_course_export_routes_return_named_markdown_and_pdf(
    client,
    db_session,
    monkeypatch,
):
    token, user = await _api_user(client, db_session, "course-export@example.com")
    _, course, _ = await _completed_course(db_session, user=user)
    monkeypatch.setattr(
        "app.services.export._render_pdf_document",
        lambda document: b"%PDF-course",
    )
    headers = {"Authorization": f"Bearer {token}"}

    markdown = await client.get(
        f"/api/v1/courses/{course.id}/export/notes/markdown",
        headers=headers,
    )
    pdf = await client.get(
        f"/api/v1/courses/{course.id}/export/notes/pdf",
        headers=headers,
    )

    assert markdown.status_code == 200
    assert 'filename="System-Design-notes.md"' in markdown.headers["content-disposition"]
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert 'filename="System-Design-notes.pdf"' in pdf.headers["content-disposition"]


def test_fractional_header_durations_round_up():
    assert _parse_duration_seconds("7.66s", 60) == 8
    assert _parse_duration_seconds("2m59.56s", 60) == 180
    assert _parse_duration_seconds("0.25", 60) == 1


def test_audio_download_timeout_scales_with_video_duration():
    assert _audio_download_timeout_seconds(None) == 120
    assert _audio_download_timeout_seconds(10 * 60) == 120
    assert _audio_download_timeout_seconds(60 * 60) == 360
    assert _audio_download_timeout_seconds(4 * 60 * 60) == 900


def test_audio_download_uses_duration_aware_timeout_and_low_bitrate_format(
    monkeypatch,
    tmp_path,
):
    captured = {}
    video = SimpleNamespace(
        youtube_video_id="long-video",
        duration_seconds=60 * 60,
    )
    output = tmp_path / "long-video.mp3"
    output.write_bytes(b"audio")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("app.services.transcript.subprocess.run", fake_run)

    result = _download_audio(video, tmp_path)

    assert result == output
    assert captured["timeout"] == 360
    format_index = captured["command"].index("--format")
    assert captured["command"][format_index + 1] == "bestaudio[abr<=96]/bestaudio/best"


def test_large_audio_is_split_without_full_file_decode(monkeypatch, tmp_path):
    audio_path = tmp_path / "long.mp3"
    audio_path.write_bytes(b"x" * (25 * 1024 * 1024 + 1))
    extracted = []

    monkeypatch.setattr(
        "app.services.transcript._probe_audio_duration_seconds",
        lambda path: 4466.0,
    )

    def fake_extract(source, destination, start, duration):
        extracted.append((start, duration))
        destination.write_bytes(f"{start}:{duration}".encode())

    monkeypatch.setattr("app.services.transcript._extract_audio_chunk", fake_extract)

    chunks = _split_audio_if_needed(audio_path, tmp_path)

    assert extracted == [(0, 2400), (2398, 2068)]
    assert [chunk.offset_seconds for chunk in chunks] == [0, 2398]
    assert [chunk.duration_seconds for chunk in chunks] == [2400, 2068]


@pytest.mark.asyncio
async def test_stale_headers_cannot_grant_capacity_back(db_session):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    model = settings.groq_auto_model
    keys = quota._keys(model, datetime.now(UTC))

    await quota.observe_headers(
        model,
        {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "100",
            "x-ratelimit-reset-requests": "60s",
        },
    )
    await quota.observe_headers(
        model,
        {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "500",
            "x-ratelimit-reset-requests": "60s",
        },
    )

    assert int(await redis.get(keys[9])) == 900
    await redis.aclose()


@pytest.mark.asyncio
async def test_atomic_reservations_prevent_parallel_rpm_oversubscription(
    db_session,
    monkeypatch,
):
    import app.services.quota as quota_module

    model = settings.groq_auto_model
    original = quota_module.MODEL_QUOTAS[model]
    monkeypatch.setitem(
        quota_module.MODEL_QUOTAS,
        model,
        ModelQuotaProfile(
            model=model,
            rpm=1,
            rpd=original.rpd,
            tpm=original.tpm,
            tpd=original.tpd,
        ),
    )
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)

    reservation = await quota.reserve(db_session, model, 100)
    with pytest.raises(GroqQuotaWaitError) as raised:
        await quota.reserve(db_session, model, 100)

    assert raised.value.dimension == "minute_requests"
    await quota.release(reservation)
    await redis.aclose()


@pytest.mark.asyncio
async def test_429_release_does_not_consume_quota(db_session):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    model = settings.groq_auto_model
    reservation = await quota.reserve(db_session, model, 100)
    await quota.release(reservation)
    error = await quota.wait_from_headers(
        model,
        {
            "retry-after": "2",
            "x-ratelimit-limit-tokens": "30000",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "2s",
        },
        "tokens per minute exceeded",
    )
    usage = await quota.model_usage(db_session, model)

    assert error.dimension == "minute_tokens"
    assert error.retry_after == 2
    assert usage["requests_day"]["used"] == 0
    assert usage["requests_minute"]["reserved"] == 0
    await redis.aclose()


@pytest.mark.asyncio
async def test_whisper_rows_use_minimum_billable_duration(db_session):
    user, course, video = await _course_video(db_session, "whisper-min@example.com")
    chunk = AudioChunk(
        path=Path("short.mp3"),
        offset_seconds=0,
        duration_seconds=2.2,
        fingerprint="stable",
    )

    rows = await _prepare_whisper_rows(db_session, video, [chunk])

    assert rows[0].duration_seconds == 3
    assert rows[0].billable_seconds == 10


@pytest.mark.asyncio
async def test_whisper_raw_response_headers_are_captured(tmp_path):
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"audio")
    response = SimpleNamespace(
        segments=[SimpleNamespace(start=0, end=1, text="hello world")],
        x_groq=SimpleNamespace(id="groq-audio-1"),
    )

    class RawResponse:
        headers = {"x-ratelimit-remaining-requests": "1999"}

        async def parse(self):
            return response

    async def create(**kwargs):
        return RawResponse()

    client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=create),
            )
        )
    )
    segments, headers, request_id = await _transcribe_chunk(
        client,
        AudioChunk(audio_path, 3, 1, "fingerprint"),
    )

    assert segments[0].start == 3
    assert headers["x-ratelimit-remaining-requests"] == "1999"
    assert request_id == "groq-audio-1"


@pytest.mark.asyncio
async def test_whisper_completed_chunk_survives_429_and_is_not_repeated(
    db_session,
    monkeypatch,
    tmp_path,
):
    user, course, video = await _course_video(db_session, "whisper-retry@example.com")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    calls = {0: 0, 1: 0}
    blocked = {"value": False}

    def fake_download(video_obj, temp_path):
        path = temp_path / "audio.mp3"
        path.write_bytes(b"audio")
        return path

    def fake_split(audio_path, temp_path):
        first = temp_path / "chunk-0.mp3"
        second = temp_path / "chunk-1.mp3"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        return [
            AudioChunk(first, 0, 12, "first"),
            AudioChunk(second, 10, 12, "second"),
        ]

    async def fake_transcribe(client, chunk):
        index = 0 if chunk.fingerprint == "first" else 1
        calls[index] += 1
        if index == 1 and not blocked["value"]:
            blocked["value"] = True
            raise Fake429()
        segment = TranscriptSegment(
            start=chunk.offset_seconds,
            end=chunk.offset_seconds + 5,
            text=f"chunk {index} transcript",
            speaker=None,
        )
        return [segment], {}, f"request-{index}"

    monkeypatch.setattr("app.services.transcript._download_audio", fake_download)
    monkeypatch.setattr("app.services.transcript._split_audio_if_needed", fake_split)
    monkeypatch.setattr("app.services.transcript._transcribe_chunk", fake_transcribe)
    monkeypatch.setattr("app.services.transcript.AsyncGroq", FakeGroqClient)
    monkeypatch.setattr("app.services.transcript.QuotaManager", FakeWhisperQuota)

    with pytest.raises(GroqQuotaWaitError):
        await transcribe_with_whisper(video, db_session)
    completed = await db_session.scalar(
        select(func.count(WhisperTranscriptionChunk.id)).where(
            WhisperTranscriptionChunk.video_id == video.id,
            WhisperTranscriptionChunk.state == "completed",
        )
    )
    transcript = await transcribe_with_whisper(video, db_session)
    ledger_count = await db_session.scalar(
        select(func.count(GroqUsageEvent.id)).where(
            GroqUsageEvent.video_id == video.id,
            GroqUsageEvent.model == settings.groq_whisper_model,
        )
    )

    assert completed == 1
    assert calls == {0: 1, 1: 2}
    assert transcript.full_text == "chunk 0 transcript chunk 1 transcript"
    assert ledger_count == 2


class Fake429(Exception):
    status_code = 429
    response = SimpleNamespace(
        headers={"retry-after": "2"},
        text="audio seconds per hour exceeded",
    )


class FakeGroqClient:
    def __init__(self, **kwargs):
        pass

    async def close(self):
        pass


class FakeWhisperQuota:
    async def reserve_whisper(self, db, audio_seconds):
        return SimpleNamespace(audio_seconds=audio_seconds)

    async def release(self, reservation):
        pass

    async def reconcile(self, reservation, **kwargs):
        pass

    async def wait_from_headers(self, model, headers, error_body=None):
        return GroqQuotaWaitError(
            "hour audio wait",
            model=model,
            window="minute",
            retry_after=2,
            dimension="hour_audio",
        )

    async def close(self):
        pass


async def _api_user(client, db_session, email):
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    user = await db_session.scalar(select(User).where(User.email == email))
    return login.json()["access_token"], user


async def _completed_course(db_session, user=None):
    if user is None:
        user = User(
            email=f"export-{datetime.now(UTC).timestamp()}@example.com",
            hashed_password=hash_password("password123"),
        )
        db_session.add(user)
        await db_session.flush()
    course = Course(
        user_id=user.id,
        title="System Design",
        playlist_url="https://youtube.com/playlist?list=system-design",
        playlist_id=f"system-design-{user.id}",
        video_count=2,
        status="completed",
    )
    db_session.add(course)
    await db_session.flush()
    videos = []
    for position, title in enumerate(["First Lesson", "Second Lesson"]):
        video = Video(
            course_id=course.id,
            user_id=user.id,
            youtube_video_id=f"video-{position}-{user.id}",
            title=title,
            position=position,
            status="completed",
        )
        db_session.add(video)
        await db_session.flush()
        db_session.add(
            Notes(
                video_id=video.id,
                course_id=course.id,
                user_id=user.id,
                source_model="local/test",
                full_markdown=(
                    f"# {title}\n\n"
                    "## Summary\n\nA useful summary.\n\n"
                    "## Topic\n\nDetailed notes.\n\n"
                    "Key Concepts:\n- design"
                ),
                summary="A useful summary.",
                sections_json=[
                    {
                        "heading": "Topic",
                        "level": 2,
                        "content": "Detailed notes.",
                        "concepts": ["design"],
                    }
                ],
                concepts_json=["design"],
                generated_at=datetime.now(UTC),
            )
        )
        videos.append(video)
    await db_session.commit()
    return user, course, videos


async def _course_video(db_session, email):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course = Course(
        user_id=user.id,
        title="Whisper",
        playlist_url="https://youtu.be/whisper",
        playlist_id=f"single:{user.id}",
        video_count=1,
        status="pending",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id=f"whisper-{user.id}",
        title="Whisper Lesson",
        position=0,
        duration_seconds=24,
        status="pending",
    )
    db_session.add(video)
    await db_session.commit()
    return user, course, video
