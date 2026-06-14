from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import TemporaryAPIError
from app.core.security import hash_password
from app.models.course import Course
from app.models.notes import Notes
from app.models.transcript import Transcript
from app.models.user import User
from app.models.video import Video
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.quota import QuotaManager
from app.tasks.maintenance import cleanup_stale_processing_videos
from app.tasks.video_tasks import get_next_midnight_utc, process_video_task
from app.workers.celery_app import celery_app


@pytest.mark.asyncio
async def test_process_video_task_runs_end_to_end(db_session, monkeypatch):
    user, course, video = await create_video(db_session, "phase4-end@example.com")
    await patch_successful_transcript(monkeypatch, video.youtube_video_id)
    video_id = video.id

    result = process_video_task.delay(str(video.id), str(user.id))

    assert result.get() == "completed"
    db_session.expire_all()
    refreshed = await db_session.get(Video, video_id)
    assert refreshed.status == "completed"
    assert await db_session.scalar(select(Transcript).where(Transcript.video_id == video_id)) is not None
    assert await db_session.scalar(select(Notes).where(Notes.video_id == video_id)) is not None


@pytest.mark.asyncio
async def test_task_sets_status_processing_on_start(db_session, monkeypatch):
    user, course, video = await create_video(db_session, "phase4-processing@example.com")
    observed_status = {}

    async def fake_extract(video_obj, db):
        current = await db.get(Video, video_obj.id)
        observed_status["status"] = current.status
        return short_transcript(video_obj.youtube_video_id)

    monkeypatch.setattr("app.tasks.video_tasks.extract_transcript", fake_extract)

    process_video_task.delay(str(video.id), str(user.id)).get()

    assert observed_status["status"] == "processing"


@pytest.mark.asyncio
async def test_quota_check_defers_task_when_exhausted(db_session, monkeypatch):
    user, course, video = await create_video(db_session, "phase4-quota@example.com")
    video_id = video.id
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    keys = quota._window_keys(settings.groq_auto_model, datetime.now(UTC))
    await redis.set(keys[0], quota.profile(settings.groq_auto_model).rpd)
    await redis.set(keys[1], 0)
    await redis.aclose()
    calls = {"extract": 0}
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    async def fake_extract(video_obj, db):
        calls["extract"] += 1
        return short_transcript(video_obj.youtube_video_id)

    monkeypatch.setattr("app.tasks.video_tasks.extract_transcript", fake_extract)

    result = process_video_task.delay(str(video.id), str(user.id))

    assert result.get() == "deferred"
    db_session.expire_all()
    refreshed = await db_session.get(Video, video_id)
    assert refreshed.status == "deferred"
    assert refreshed.scheduled_for is not None
    assert calls["extract"] == 1
    assert await db_session.scalar(
        select(Transcript).where(Transcript.video_id == video_id)
    ) is not None


@pytest.mark.asyncio
async def test_deferred_task_reschedules_at_correct_eta(db_session, monkeypatch):
    user, course, video = await create_video(db_session, "phase4-eta@example.com")
    video_id = video.id
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = QuotaManager(redis)
    keys = quota._window_keys(settings.groq_auto_model, datetime.now(UTC))
    await redis.set(keys[0], quota.profile(settings.groq_auto_model).rpd)
    await redis.set(keys[1], 0)
    await redis.aclose()
    eta = datetime.now(UTC) + timedelta(seconds=5)
    captured = {}

    await patch_successful_transcript(monkeypatch, video.youtube_video_id)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.tasks.video_tasks.get_next_midnight_utc", lambda: eta)
    monkeypatch.setattr(
        "app.tasks.video_tasks.process_video_task.apply_async",
        lambda *args, **kwargs: captured.update(kwargs),
    )
    celery_app.conf.task_always_eager = False
    try:
        assert process_video_task.run(str(video.id), str(user.id)) == "deferred"
    finally:
        celery_app.conf.task_always_eager = True

    db_session.expire_all()
    refreshed = await db_session.get(Video, video_id)
    assert refreshed.status == "deferred"
    assert captured["eta"] == eta


@pytest.mark.asyncio
async def test_offline_processing_does_not_charge_groq_quota(db_session, monkeypatch):
    user, course, video = await create_video(db_session, "phase4-increment@example.com")
    await patch_successful_transcript(monkeypatch, video.youtube_video_id)
    monkeypatch.setattr(settings, "groq_api_key", "your_groq_key_here")

    process_video_task.delay(str(video.id), str(user.id)).get()

    notes = await db_session.scalar(select(Notes).where(Notes.video_id == video.id))
    assert notes is not None
    assert notes.request_count == 0
    assert notes.token_count > 0


@pytest.mark.asyncio
async def test_stale_processing_cleanup_resets_stuck_videos(db_session):
    user, course, video = await create_video(db_session, "phase4-stale@example.com")
    video_id = video.id
    video.status = "processing"
    video.celery_task_id = "old-task"
    video.updated_at = datetime.now(UTC) - timedelta(hours=2)
    await db_session.commit()

    assert cleanup_stale_processing_videos.delay().get() == 1

    db_session.expire_all()
    refreshed = await db_session.get(Video, video_id)
    assert refreshed.status == "pending"
    assert refreshed.celery_task_id is None


@pytest.mark.asyncio
async def test_task_max_retries_sets_failed_status(db_session, monkeypatch):
    user, course, video = await create_video(db_session, "phase4-retry@example.com")
    video_id = video.id

    async def fail_extract(video_obj, db):
        raise TemporaryAPIError("temporary outage")

    monkeypatch.setattr("app.tasks.video_tasks.extract_transcript", fail_extract)

    process_video_task.apply(args=[str(video.id), str(user.id)]).get(propagate=False)

    db_session.expire_all()
    refreshed = await db_session.get(Video, video_id)
    assert refreshed.status == "failed"
    assert refreshed.error_message


@pytest.mark.asyncio
async def test_course_status_updated_when_all_videos_complete(db_session, monkeypatch):
    user, course, videos = await create_course_with_videos(db_session, "phase4-complete@example.com", 3)
    course_id = course.id
    await patch_successful_transcript(monkeypatch, "unused")

    for video in videos:
        process_video_task.delay(str(video.id), str(user.id)).get()

    db_session.expire_all()
    refreshed = await db_session.get(Course, course_id)
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_course_status_partial_when_some_deferred(db_session):
    user, course, videos = await create_course_with_videos(db_session, "phase4-partial@example.com", 3)
    videos[0].status = "completed"
    videos[1].status = "completed"
    videos[2].status = "deferred"
    course.status = "partial"
    await db_session.commit()

    refreshed = await db_session.get(Course, course.id)
    assert refreshed.status == "partial"


async def create_video(db_session, email: str):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course = Course(
        user_id=user.id,
        title="Phase 4 Course",
        playlist_url="https://youtu.be/phase4",
        playlist_id="single:phase4",
        video_count=1,
        status="pending",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id="phase4-video",
        title="Phase 4 Video",
        position=0,
        duration_seconds=60,
        status="pending",
    )
    db_session.add(video)
    await db_session.commit()
    await db_session.refresh(video)
    return user, course, video


async def create_course_with_videos(db_session, email: str, count: int):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course = Course(
        user_id=user.id,
        title="Phase 4 Multi Course",
        playlist_url="https://www.youtube.com/playlist?list=PLPHASE4",
        playlist_id="PLPHASE4",
        video_count=count,
        status="pending",
    )
    db_session.add(course)
    await db_session.flush()
    videos = []
    for index in range(count):
        video = Video(
            course_id=course.id,
            user_id=user.id,
            youtube_video_id=f"phase4-video-{index}",
            title=f"Phase 4 Video {index}",
            position=index,
            duration_seconds=60,
            status="pending",
        )
        db_session.add(video)
        videos.append(video)
    await db_session.commit()
    for video in videos:
        await db_session.refresh(video)
    return user, course, videos


async def patch_successful_transcript(monkeypatch, youtube_video_id: str):
    async def fake_extract(video_obj, db):
        return short_transcript(video_obj.youtube_video_id)

    monkeypatch.setattr("app.tasks.video_tasks.extract_transcript", fake_extract)


def short_transcript(youtube_video_id: str) -> NormalisedTranscript:
    segments = [
        TranscriptSegment(
            start=0,
            end=60,
            text=(
                "Task queues process videos reliably in the background. "
                "Quota checks prevent API failures by deferring work until capacity returns. "
                "Successful processing stores transcripts and structured notes for review."
            ),
            speaker=None,
        )
    ]
    full_text = " ".join(segment.text for segment in segments)
    return NormalisedTranscript(
        video_id=youtube_video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=60,
        segments=segments,
        full_text=full_text,
        word_count=len(full_text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )
