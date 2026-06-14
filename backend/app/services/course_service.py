from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import UserIsolationError
from app.core.config import settings
from app.models.course import Course
from app.schemas.course import CourseSeed
from app.schemas.video import VideoStatus
from app.services.quota import QuotaManager


async def create_course(db: AsyncSession, user_id: UUID, payload: CourseSeed) -> Course:
    course = Course(
        user_id=user_id,
        title=payload.title,
        playlist_url=payload.playlist_url,
        playlist_id=payload.playlist_id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


async def list_courses(db: AsyncSession, user_id: UUID) -> list[Course]:
    result = await db.scalars(
        select(Course)
        .where(Course.user_id == user_id)
        .order_by(Course.created_at.desc())
    )
    return list(result)


async def get_course(db: AsyncSession, user_id: UUID, course_id: UUID) -> Course:
    course = await db.scalar(
        select(Course)
        .options(selectinload(Course.videos))
        .where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")
    return course


async def get_course_status(db: AsyncSession, user_id: UUID, course_id: UUID) -> dict:
    course = await get_course(db, user_id, course_id)
    counts = {status.value: 0 for status in VideoStatus}
    for video in course.videos:
        counts[video.status] = counts.get(video.status, 0) + 1
    deferred_dates = [video.scheduled_for for video in course.videos if video.scheduled_for is not None]
    retry_dates = [
        video.scheduled_for
        for video in course.videos
        if video.status == VideoStatus.RATE_LIMITED.value and video.scheduled_for is not None
    ]
    quota = QuotaManager()
    try:
        model_usage = await quota.model_usage(db, settings.groq_auto_model)
        quota_remaining = {
            "llm_requests": model_usage["requests_day"]["remaining"],
            "llm_tokens": model_usage["tokens_day"]["remaining"],
        }
        whisper_usage = await quota.whisper_usage(db)
        quota_remaining.update(
            {
                "whisper_requests": whisper_usage["requests_day"]["remaining"],
                "whisper_audio_seconds_hour": whisper_usage["audio_hour"]["remaining"],
                "whisper_audio_seconds_day": whisper_usage["audio_day"]["remaining"],
            }
        )
    finally:
        await quota.close()
    return {
        "course_id": course.id,
        "total": len(course.videos),
        "pending": counts.get(VideoStatus.PENDING.value, 0),
        "processing": counts.get(VideoStatus.PROCESSING.value, 0),
        "rate_limited": counts.get(VideoStatus.RATE_LIMITED.value, 0),
        "batch_processing": counts.get(VideoStatus.BATCH_PROCESSING.value, 0),
        "completed": counts.get(VideoStatus.COMPLETED.value, 0),
        "failed": counts.get(VideoStatus.FAILED.value, 0),
        "deferred": counts.get(VideoStatus.DEFERRED.value, 0),
        "deferred_until": min(deferred_dates) if deferred_dates else None,
        "next_retry_at": min(retry_dates) if retry_dates else None,
        "quota_remaining": quota_remaining,
    }


async def delete_course(db: AsyncSession, user_id: UUID, course_id: UUID) -> None:
    course = await get_course(db, user_id, course_id)
    await db.delete(course)
    await db.commit()
