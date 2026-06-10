import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import PermanentAPIError, TemporaryAPIError
from app.db.session import AsyncSessionLocal
from app.models.course import Course
from app.models.video import Video
from app.services.embedder import embed_and_store_notes
from app.services.notes_service import generate_notes_for_video, get_notes_for_video
from app.services.quota import QuotaManager
from app.services.transcript import extract_transcript, store_transcript
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


def get_next_midnight_utc() -> datetime:
    now = datetime.now(UTC)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=30, microsecond=0)


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}

    def runner():
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _estimate_tokens(video: Video) -> int:
    if video.duration_seconds:
        return max(300, min(video.duration_seconds * 8, 6000))
    return 1000


async def _set_video_failed(video_id: str, user_id: str, error: str) -> None:
    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is not None:
            video.status = "failed"
            video.error_message = error
            await db.commit()


async def _store_note_chunks(video: Video, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        notes = await get_notes_for_video(db, UUID(user_id), video.id)
        await embed_and_store_notes(notes, video, db)


async def _process_video(video_id: str, user_id: str, task_id: str) -> str:
    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video)
            .options(selectinload(Video.transcript))
            .where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is None:
            raise PermanentAPIError("Video not found")

        video.status = "processing"
        video.celery_task_id = task_id
        video.error_message = None
        await db.commit()

        quota = QuotaManager()
        try:
            estimated_tokens = _estimate_tokens(video)
            if not await quota.can_process_video(user_id, estimated_tokens):
                scheduled_for = get_next_midnight_utc()
                video.status = "deferred"
                video.scheduled_for = scheduled_for
                await db.commit()
                logger.warning(
                    "video.processing.deferred",
                    task_id=task_id,
                    video_id=video_id,
                    user_id=user_id,
                    scheduled_for=scheduled_for.isoformat(),
                )
                if not celery_app.conf.task_always_eager:
                    process_video_task.apply_async(args=[video_id, user_id], eta=scheduled_for)
                return "deferred"
        finally:
            await quota.close()

        transcript = await extract_transcript(video, db)
        await store_transcript(transcript, video, db)

    async with AsyncSessionLocal() as db:
        notes = await generate_notes_for_video(db, UUID(user_id), UUID(video_id))

    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is None:
            raise PermanentAPIError("Video disappeared during processing")
        await _store_note_chunks(video, user_id)

    quota = QuotaManager()
    try:
        await quota.increment(user_id, "llm_requests", 1)
        await quota.increment(user_id, "llm_tokens", notes.token_count)
    finally:
        await quota.close()

    return "completed"


@celery_app.task(
    bind=True,
    max_retries=3,
    name="app.tasks.video_tasks.process_video_task",
)
def process_video_task(self, video_id: str, user_id: str):
    started = time.perf_counter()
    task_id = self.request.id
    logger.info(
        "video.processing.started",
        task_id=task_id,
        video_id=video_id,
        user_id=user_id,
    )
    try:
        outcome = _run(_process_video(video_id, user_id, task_id))
        logger.info(
            "video.processing.finished",
            task_id=task_id,
            video_id=video_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome=outcome,
        )
        return outcome
    except TemporaryAPIError as exc:
        if self.request.retries >= self.max_retries:
            _run(_set_video_failed(video_id, user_id, str(exc)))
            logger.error(
                "video.processing.finished",
                task_id=task_id,
                video_id=video_id,
                user_id=user_id,
                duration_s=round(time.perf_counter() - started, 3),
                outcome="failed",
                error_type=type(exc).__name__,
            )
            return "failed"
        countdown = min(60 * (2**self.request.retries), 900)
        logger.warning(
            "video.processing.retrying",
            task_id=task_id,
            video_id=video_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="retry",
            retry_in_s=countdown,
        )
        raise self.retry(exc=exc, countdown=countdown)
    except PermanentAPIError as exc:
        _run(_set_video_failed(video_id, user_id, str(exc)))
        logger.error(
            "video.processing.finished",
            task_id=task_id,
            video_id=video_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="failed",
            error_type=type(exc).__name__,
        )
        return "failed"
    except Exception as exc:
        _run(_set_video_failed(video_id, user_id, str(exc)))
        logger.exception(
            "video.processing.finished",
            task_id=task_id,
            video_id=video_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="failed",
            error_type=type(exc).__name__,
        )
        return "failed"


async def _dispatch_course_tasks(course_id: str, user_id: str) -> int:
    async with AsyncSessionLocal() as db:
        course = await db.scalar(
            select(Course)
            .options(selectinload(Course.videos))
            .where(Course.id == UUID(course_id), Course.user_id == UUID(user_id))
        )
        if course is None:
            raise PermanentAPIError("Course not found")
        for video in course.videos:
            process_video_task.apply_async(args=[str(video.id), user_id])
        return len(course.videos)


@celery_app.task(name="app.tasks.video_tasks.dispatch_course_tasks")
def dispatch_course_tasks(course_id: str, user_id: str):
    started = time.perf_counter()
    task_id = dispatch_course_tasks.request.id
    try:
        count = _run(_dispatch_course_tasks(course_id, user_id))
        logger.info(
            "course.dispatch.finished",
            task_id=task_id,
            course_id=course_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="completed",
            video_count=count,
        )
        return count
    except Exception:
        logger.exception(
            "course.dispatch.finished",
            task_id=task_id,
            course_id=course_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="failed",
        )
        raise
