from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import GroqQuotaWaitError, ValidationError
from app.core.security import hash_password
from app.models.course import Course
from app.models.groq import GroqUsageEvent, NoteGenerationChunk
from app.models.notes import Notes
from app.models.user import User
from app.models.video import Video
from app.schemas.chunk import TranscriptChunk
from app.schemas.notes import NotesSection, VideoNotes
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.chunker import chunk_transcript_for_notes
from app.services.groq_batch import submit_video_batch
from app.services.notes_generator import GroqChunkResult, generate_groq_notes_for_chunk
from app.services.notes_service import generate_notes_for_video, model_for_quality
from app.services.quota import QuotaManager
from app.services.transcript import store_transcript


def test_summary_validation_ignores_non_terminal_abbreviation_punctuation():
    notes = VideoNotes(
        video_id=str(UUID_ZERO),
        course_id=str(UUID_ZERO),
        title="Consistent Hashing",
        source_model="groq/test",
        sections=[
            NotesSection(
                heading="Consistent Hashing",
                level=2,
                content="A useful explanation.",
                concepts=["consistent hashing"],
            )
        ],
        summary=(
            "The ring may use e.g., three points per partition. "
            "Rebalancing limits movement. Other schemes can also be used."
        ),
        full_markdown="## Consistent Hashing\n\nA useful explanation.",
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=20,
    )

    assert notes.summary.startswith("The ring")


def test_quality_profiles_route_standard_to_scout_and_high_to_70b():
    assert model_for_quality("standard") == settings.groq_auto_model
    assert model_for_quality("high") == settings.groq_high_quality_model


@pytest.mark.asyncio
async def test_quota_usage_api_returns_both_model_profiles(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "quota-api@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "quota-api@example.com", "password": "password123"},
    )
    response = await client.get(
        "/api/v1/quota/usage",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    models = {item["model"]: item for item in response.json()["models"]}
    assert models[settings.groq_auto_model]["tokens_day"]["limit"] == 500_000
    assert models[settings.groq_high_quality_model]["tokens_day"]["limit"] == 100_000


@pytest.mark.asyncio
async def test_failed_video_retry_route_requeues_existing_work(client, db_session, monkeypatch):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "retry-video@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "retry-video@example.com", "password": "password123"},
    )
    user = await db_session.scalar(
        select(User).where(User.email == "retry-video@example.com")
    )
    course = Course(
        user_id=user.id,
        title="Retry Course",
        playlist_url="https://youtu.be/retry",
        playlist_id="single:retry",
        video_count=1,
        status="partial",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id="retry",
        title="Retry Lesson",
        position=0,
        status="failed",
        error_message="Generated notes did not pass validation.",
    )
    db_session.add(video)
    await db_session.commit()
    captured = {}
    monkeypatch.setattr(
        "app.api.v1.videos.process_video_task.apply_async",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    response = await client.post(
        f"/api/v1/videos/{video.id}/retry",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["error_message"] is None
    assert captured["task_id"] == response.json()["celery_task_id"]


@pytest.mark.asyncio
async def test_70b_tpm_exhaustion_is_a_short_wait(db_session):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    keys = quota._window_keys(settings.groq_high_quality_model, datetime.now(UTC))
    profile = quota.profile(settings.groq_high_quality_model)
    await redis.mset({keys[0]: 0, keys[1]: 0, keys[2]: 0, keys[3]: profile.tpm})

    with pytest.raises(GroqQuotaWaitError) as raised:
        await quota.reserve(db_session, settings.groq_high_quality_model, 1)

    assert raised.value.window == "minute"
    assert raised.value.retry_after <= 61
    await redis.aclose()


@pytest.mark.asyncio
async def test_scout_ordinary_daily_usage_remains_active(db_session):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    keys = quota._window_keys(settings.groq_auto_model, datetime.now(UTC))
    await redis.mset({keys[0]: 8, keys[1]: 13_400, keys[2]: 0, keys[3]: 0})

    reservation = await quota.reserve(db_session, settings.groq_auto_model, 5_000)

    assert reservation.model == settings.groq_auto_model
    await quota.release(reservation)
    await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
        ("model", "target"),
        [
        ("meta-llama/llama-4-scout-17b-16e-instruct", 500_000),
        ("llama-3.3-70b-versatile", 100_000),
    ],
)
async def test_daily_token_targets_stop_synchronous_calls(db_session, model, target):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    keys = quota._window_keys(model, datetime.now(UTC))
    await redis.mset({keys[0]: 1, keys[1]: target, keys[2]: 0, keys[3]: 0})

    with pytest.raises(GroqQuotaWaitError) as raised:
        await quota.reserve(db_session, model, 1)

    assert raised.value.window == "daily"
    await redis.aclose()


@pytest.mark.asyncio
async def test_global_reservations_are_shared_but_models_are_independent(db_session):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    first = QuotaManager(redis)
    second = QuotaManager(redis)
    now = datetime.now(UTC)
    high_keys = first._window_keys(settings.groq_high_quality_model, now)
    high_profile = first.profile(settings.groq_high_quality_model)
    await redis.mset(
        {
            high_keys[0]: 0,
            high_keys[1]: 0,
            high_keys[2]: 0,
            high_keys[3]: high_profile.tpm,
        }
    )

    with pytest.raises(GroqQuotaWaitError):
        await first.reserve(db_session, settings.groq_high_quality_model, 1)
    scout_reservation = await second.reserve(db_session, settings.groq_auto_model, 100)
    usage = await first.model_usage(db_session, settings.groq_auto_model)

    assert usage["requests_minute"]["reserved"] == 1
    await first.release(scout_reservation)
    await redis.aclose()


@pytest.mark.asyncio
async def test_cached_tokens_are_recorded_but_not_charged():
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(cached_tokens=30),
    )
    response = SimpleNamespace(
        id="completion-1",
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content="## Topic\n\nNotes."))],
    )

    class RawResponse:
        headers = {"x-request-id": "request-1"}

        async def parse(self):
            return response

    async def create(**kwargs):
        return RawResponse()

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=create),
            )
        )
    )
    result = await generate_groq_notes_for_chunk(
        TranscriptChunk(text="A lesson.", start_seconds=0, end_seconds=1, chunk_index=0),
        0,
        1,
        None,
        client,
        settings.groq_auto_model,
    )

    assert result.cached_tokens == 30
    assert result.charged_tokens == 120


def test_approximately_1800_word_lesson_is_one_automatic_request():
    text = " ".join(["lesson"] * 1800) + "."
    transcript = NormalisedTranscript(
        video_id="one-request",
        source="youtube_captions",
        language="en",
        duration_seconds=900,
        segments=[TranscriptSegment(start=0, end=900, text=text, speaker=None)],
        full_text=text,
        word_count=1800,
        fetched_at=datetime.now(UTC).isoformat(),
    )

    assert len(chunk_transcript_for_notes(transcript)) == 1


@pytest.mark.asyncio
async def test_partial_chunk_progress_survives_429_without_duplicate_calls(
    db_session,
    monkeypatch,
):
    user, video = await _video_with_transcript(db_session, word_count=4000)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.services.notes_service.AsyncGroq", lambda **kwargs: object())
    monkeypatch.setattr("app.services.notes_service.QuotaManager", FakeQuotaManager)
    calls: dict[int, int] = {}
    blocked = {"raised": False}

    async def fake_generate(chunk, chunk_index, total_chunks, previous_summary, client, model):
        calls[chunk_index] = calls.get(chunk_index, 0) + 1
        if chunk_index == 1 and not blocked["raised"]:
            blocked["raised"] = True
            raise Fake429()
        markdown = (
            f"## Topic {chunk_index + 1}\n\n"
            "This section explains a durable course concept with a concrete example and enough detail "
            "to create useful study notes for later review.\n\n"
            "Key Concepts:\n- durable progress\n- request ledger\n- retry scheduling"
        )
        return GroqChunkResult(
            markdown=markdown,
            model=model,
            prompt_tokens=100,
            completion_tokens=40,
            cached_tokens=10,
            charged_tokens=130,
            request_id=f"request-{chunk_index}-{calls[chunk_index]}",
            headers={},
        )

    monkeypatch.setattr(
        "app.services.notes_service.generate_groq_notes_for_chunk",
        fake_generate,
    )

    with pytest.raises(GroqQuotaWaitError):
        await generate_notes_for_video(db_session, user.id, video.id, quality="standard")

    completed_before_retry = await db_session.scalar(
        select(func.count(NoteGenerationChunk.id)).where(
            NoteGenerationChunk.video_id == video.id,
            NoteGenerationChunk.state == "completed",
        )
    )
    notes = await generate_notes_for_video(
        db_session,
        user.id,
        video.id,
        quality="standard",
    )
    ledger_count = await db_session.scalar(
        select(func.count(GroqUsageEvent.id)).where(GroqUsageEvent.video_id == video.id)
    )
    stored = await db_session.scalar(select(Notes).where(Notes.video_id == video.id))

    assert completed_before_retry >= 1
    assert calls[0] == 1
    assert ledger_count == sum(calls.values()) - 1
    assert notes.request_count == ledger_count
    assert stored.request_count == ledger_count


@pytest.mark.asyncio
async def test_batch_is_never_invoked_when_feature_flag_is_disabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "groq_batch_enabled", False)

    with pytest.raises(ValidationError, match="disabled"):
        await submit_video_batch(
            db_session,
            UUID_ZERO,
            UUID_ZERO,
        )


UUID_ZERO = UUID(int=0)


class Fake429(Exception):
    status_code = 429
    response = SimpleNamespace(headers={"retry-after": "2"})


class FakeQuotaManager:
    async def reserve(self, db, model, estimated_tokens):
        return SimpleNamespace(model=model, estimated_tokens=estimated_tokens)

    async def release(self, reservation):
        return None

    async def reconcile(self, reservation, charged_tokens, headers):
        return None

    async def wait_from_headers(self, model, headers, error_body=None):
        return GroqQuotaWaitError(
            "short wait",
            model=model,
            window="minute",
            retry_after=2,
        )

    async def close(self):
        return None


async def _video_with_transcript(db_session, word_count: int):
    user = User(
        email="phase11@example.com",
        hashed_password=hash_password("password123"),
    )
    db_session.add(user)
    await db_session.flush()
    course = Course(
        user_id=user.id,
        title="Throughput",
        playlist_url="https://youtu.be/throughput",
        playlist_id="single:throughput",
        video_count=1,
        status="pending",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id="throughput",
        title="Throughput Lesson",
        position=0,
        status="pending",
    )
    db_session.add(video)
    await db_session.commit()
    text = " ".join(f"concept{index}." for index in range(word_count))
    transcript = NormalisedTranscript(
        video_id=video.youtube_video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=1200,
        segments=[TranscriptSegment(start=0, end=1200, text=text, speaker=None)],
        full_text=text,
        word_count=word_count,
        fetched_at=datetime.now(UTC).isoformat(),
    )
    await store_transcript(transcript, video, db_session)
    await db_session.refresh(video)
    return user, video
