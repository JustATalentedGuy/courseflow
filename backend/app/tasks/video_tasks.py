import asyncio
import threading
import time
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import GroqQuotaWaitError, PermanentAPIError, TemporaryAPIError
from app.db.session import AsyncSessionLocal
from app.models.course import Course
from app.models.edge import YouTubeEdgeJob
from app.models.groq import GroqBatchJob, NoteGenerationChunk
from app.models.video import Video
from app.services.edge_fetcher import edge_mode_enabled, ensure_transcript_edge_job
from app.services.embedder import embed_and_store_notes
from app.services.groq_batch import TERMINAL_BATCH_STATES, poll_batch, submit_video_batch
from app.services.notes_service import generate_notes_for_video, get_notes_for_video
from app.services.object_storage import delete_object
from app.services.transcript import extract_transcript, store_transcript, transcribe_uploaded_audio
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
        video.scheduled_for = None
        await db.commit()

        if video.transcript is None:
            if edge_mode_enabled():
                await ensure_transcript_edge_job(db, video)
                return "waiting_for_transcript"
            transcript = await extract_transcript(video, db)
            await store_transcript(transcript, video, db)

    async with AsyncSessionLocal() as db:
        await generate_notes_for_video(
            db,
            UUID(user_id),
            UUID(video_id),
            quality="standard",
        )

    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is None:
            raise PermanentAPIError("Video disappeared during processing")
        await _store_note_chunks(video, user_id)

    return "completed"


async def _schedule_quota_retry(
    video_id: str,
    user_id: str,
    exc: GroqQuotaWaitError,
) -> tuple[str, datetime | None]:
    now = datetime.now(UTC)
    daily = exc.window == "daily"
    if daily and settings.groq_batch_enabled:
        async with AsyncSessionLocal() as db:
            job = await submit_video_batch(db, UUID(video_id), UUID(user_id))
        if not celery_app.conf.task_always_eager:
            poll_groq_batch_task.apply_async(args=[str(job.id)], countdown=300)
        return "batch_processing", None

    scheduled_for = (
        get_next_midnight_utc()
        if daily
        else now
        + timedelta(
            seconds=max(1, exc.retry_after)
            + (int(hashlib.sha256(video_id.encode()).hexdigest()[:2], 16) % 3)
        )
    )
    status = "deferred" if daily else "rate_limited"
    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is not None:
            video.status = status
            video.scheduled_for = scheduled_for
            video.error_message = str(exc)
            await db.commit()

    logger.warning(
        "video.processing.quota_wait",
        video_id=video_id,
        user_id=user_id,
        model=exc.model,
        window=exc.window,
        status=status,
        scheduled_for=scheduled_for.isoformat(),
        batch_enabled=settings.groq_batch_enabled,
    )
    if not celery_app.conf.task_always_eager:
        process_video_task.apply_async(args=[video_id, user_id], eta=scheduled_for)
    return status, scheduled_for


async def _schedule_uploaded_audio_retry(
    video_id: str,
    user_id: str,
    exc: GroqQuotaWaitError,
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    scheduled_for = (
        get_next_midnight_utc()
        if exc.window == "daily"
        else now
        + timedelta(
            seconds=max(1, exc.retry_after)
            + (int(hashlib.sha256(video_id.encode()).hexdigest()[:2], 16) % 3)
        )
    )
    status = "deferred" if exc.window == "daily" else "rate_limited"
    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is not None:
            video.status = status
            video.scheduled_for = scheduled_for
            video.error_message = str(exc)
            await db.commit()
    return status, scheduled_for


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
    except GroqQuotaWaitError as exc:
        status, scheduled_for = _run(_schedule_quota_retry(video_id, user_id, exc))
        logger.info(
            "video.processing.finished",
            task_id=task_id,
            video_id=video_id,
            user_id=user_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome=status,
            scheduled_for=scheduled_for.isoformat() if scheduled_for else None,
        )
        return status
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


async def _process_uploaded_audio(video_id: str, user_id: str, job_id: str) -> str:
    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video)
            .options(selectinload(Video.transcript))
            .where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        job = await db.scalar(
            select(YouTubeEdgeJob).where(
                YouTubeEdgeJob.id == UUID(job_id),
                YouTubeEdgeJob.video_id == UUID(video_id),
                YouTubeEdgeJob.user_id == UUID(user_id),
            )
        )
        if video is None or job is None:
            raise PermanentAPIError("Uploaded audio job not found")
        if video.transcript is not None:
            return "completed"
        if not job.audio_object_uri:
            raise PermanentAPIError("Uploaded audio object is missing")
        video.status = "transcribing"
        video.error_message = None
        await db.commit()
        transcript = await transcribe_uploaded_audio(video, db, job.audio_object_uri)
        await store_transcript(transcript, video, db)
        job.state = "completed"
        await db.commit()
        try:
            await delete_object(job.audio_object_uri)
        except Exception:
            logger.warning("edge.audio.cleanup.failed", job_id=job_id, object_uri=job.audio_object_uri)

    async with AsyncSessionLocal() as db:
        await generate_notes_for_video(db, UUID(user_id), UUID(video_id), quality="standard")

    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == UUID(video_id), Video.user_id == UUID(user_id))
        )
        if video is None:
            raise PermanentAPIError("Video disappeared during uploaded audio processing")
        await _store_note_chunks(video, user_id)
    return "completed"


@celery_app.task(
    bind=True,
    max_retries=3,
    name="app.tasks.video_tasks.process_uploaded_audio_task",
)
def process_uploaded_audio_task(self, video_id: str, user_id: str, job_id: str):
    started = time.perf_counter()
    try:
        outcome = _run(_process_uploaded_audio(video_id, user_id, job_id))
        logger.info(
            "edge.audio.processing.finished",
            task_id=self.request.id,
            video_id=video_id,
            user_id=user_id,
            job_id=job_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome=outcome,
        )
        return outcome
    except GroqQuotaWaitError as exc:
        status, scheduled_for = _run(_schedule_uploaded_audio_retry(video_id, user_id, exc))
        process_uploaded_audio_task.apply_async(args=[video_id, user_id, job_id], eta=scheduled_for)
        return status
    except TemporaryAPIError as exc:
        if self.request.retries >= self.max_retries:
            _run(_set_video_failed(video_id, user_id, str(exc)))
            return "failed"
        raise self.retry(exc=exc, countdown=min(60 * (2**self.request.retries), 900))
    except PermanentAPIError as exc:
        _run(_set_video_failed(video_id, user_id, str(exc)))
        return "failed"
    except Exception as exc:
        _run(_set_video_failed(video_id, user_id, str(exc)))
        logger.exception(
            "edge.audio.processing.finished",
            task_id=self.request.id,
            video_id=video_id,
            user_id=user_id,
            job_id=job_id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="failed",
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
        requested_count = len(course.videos)
        queue = list(
            await db.scalars(
                select(Video)
                .join(Course, Course.id == Video.course_id)
                .where(
                    Video.status == "pending",
                    Video.celery_task_id.is_(None),
                )
                .order_by(Course.created_at, Video.position)
            )
        )
        assignments = [(video, str(uuid4())) for video in queue]
        for video, task_id in assignments:
            video.celery_task_id = task_id
        await db.commit()

        for video, task_id in assignments:
            process_video_task.apply_async(
                args=[str(video.id), str(video.user_id)],
                task_id=task_id,
            )
        return requested_count


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


async def _poll_groq_batch(job_id: str) -> str:
    async with AsyncSessionLocal() as db:
        status = await poll_batch(db, UUID(job_id))
        job = await db.get(GroqBatchJob, UUID(job_id))
        if job is None:
            raise PermanentAPIError("Groq Batch job disappeared")
        video_id = UUID(job.metadata_json["video_id"])
        user_id = UUID(job.metadata_json["user_id"])
        if status not in TERMINAL_BATCH_STATES:
            return status

        incomplete = await db.scalar(
            select(NoteGenerationChunk.id).where(
                NoteGenerationChunk.groq_batch_job_id == job.id,
                NoteGenerationChunk.state != "completed",
            )
        )
        if status == "completed" and incomplete is None:
            await generate_notes_for_video(
                db,
                user_id,
                video_id,
                quality="standard",
            )
        else:
            rows = list(
                await db.scalars(
                    select(NoteGenerationChunk).where(
                        NoteGenerationChunk.groq_batch_job_id == job.id,
                        NoteGenerationChunk.state != "completed",
                    )
                )
            )
            for row in rows:
                row.state = "pending"
                row.groq_batch_job_id = None
            video = await db.get(Video, video_id)
            if video is not None:
                video.status = "deferred"
                video.scheduled_for = get_next_midnight_utc()
            await db.commit()
            if not celery_app.conf.task_always_eager:
                process_video_task.apply_async(
                    args=[str(video_id), str(user_id)],
                    eta=get_next_midnight_utc(),
                )
            return "deferred"

    async with AsyncSessionLocal() as db:
        video = await db.scalar(
            select(Video).where(Video.id == video_id, Video.user_id == user_id)
        )
        if video is not None:
            await _store_note_chunks(video, str(user_id))
    return "completed"


@celery_app.task(name="app.tasks.video_tasks.poll_groq_batch_task")
def poll_groq_batch_task(job_id: str):
    status = _run(_poll_groq_batch(job_id))
    if status not in TERMINAL_BATCH_STATES and status != "deferred":
        poll_groq_batch_task.apply_async(args=[job_id], countdown=300)
    return status
