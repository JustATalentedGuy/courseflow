from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import UserIsolationError, ValidationError
from app.core.security import hash_password, verify_password
from app.models.course import Course
from app.models.edge import EdgeFetcherToken, YouTubeEdgeJob
from app.models.video import Video
from app.schemas.edge import (
    EdgeAudioUploadComplete,
    EdgeAudioUploadInit,
    EdgeCaptionSubmit,
    EdgeClaimedJob,
    EdgeMetadataSubmit,
)
from app.schemas.transcript import NormalisedTranscript
from app.services.object_storage import build_object_uri, generate_presigned_upload_url
from app.services.transcript import _caption_segments_to_transcript, store_transcript


EDGE_TOKEN_PREFIX = "cfedge"
LEASE_STATES = {"leased", "uploading"}


def edge_mode_enabled() -> bool:
    return settings.youtube_fetch_mode.lower() in {"edge", "hybrid", "local_edge"}


async def create_fetcher_token(
    db: AsyncSession,
    user_id: UUID,
    name: str,
) -> tuple[EdgeFetcherToken, str]:
    secret = secrets.token_urlsafe(32)
    token_id = secrets.token_hex(8)
    token = f"{EDGE_TOKEN_PREFIX}_{token_id}_{secret}"
    row = EdgeFetcherToken(
        user_id=user_id,
        name=name,
        token_prefix=f"{EDGE_TOKEN_PREFIX}_{token_id[:8]}",
        token_hash=hash_password(token),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, token


async def list_fetcher_tokens(db: AsyncSession, user_id: UUID) -> list[EdgeFetcherToken]:
    return list(
        await db.scalars(
            select(EdgeFetcherToken)
            .where(EdgeFetcherToken.user_id == user_id)
            .order_by(EdgeFetcherToken.created_at.desc())
        )
    )


async def revoke_fetcher_token(db: AsyncSession, user_id: UUID, token_id: UUID) -> None:
    row = await db.scalar(
        select(EdgeFetcherToken).where(
            EdgeFetcherToken.id == token_id,
            EdgeFetcherToken.user_id == user_id,
        )
    )
    if row is None:
        raise UserIsolationError("Fetcher token not found")
    row.revoked = True
    await db.commit()


async def authenticate_fetcher_token(db: AsyncSession, raw_token: str) -> EdgeFetcherToken:
    if not raw_token.startswith(f"{EDGE_TOKEN_PREFIX}_"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid edge token")
    rows = list(await db.scalars(select(EdgeFetcherToken).where(EdgeFetcherToken.revoked.is_(False))))
    for row in rows:
        if verify_password(raw_token, row.token_hash):
            row.last_seen_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(row)
            return row
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid edge token")


async def create_metadata_edge_job(
    db: AsyncSession,
    user_id: UUID,
    youtube_url: str,
    parsed_playlist_id: str,
) -> Course:
    course = Course(
        user_id=user_id,
        title="Importing YouTube course",
        playlist_url=youtube_url,
        playlist_id=parsed_playlist_id,
        video_count=0,
        status="waiting_for_metadata",
    )
    db.add(course)
    await db.flush()
    db.add(
        YouTubeEdgeJob(
            user_id=user_id,
            course_id=course.id,
            job_type="playlist_metadata",
            state="pending",
            youtube_url=youtube_url,
        )
    )
    await db.commit()
    await db.refresh(course)
    return course


async def ensure_transcript_edge_job(db: AsyncSession, video: Video) -> YouTubeEdgeJob:
    existing = await db.scalar(
        select(YouTubeEdgeJob).where(
            YouTubeEdgeJob.video_id == video.id,
            YouTubeEdgeJob.job_type == "video_transcript",
            YouTubeEdgeJob.state != "completed",
        )
    )
    if existing:
        return existing
    job = YouTubeEdgeJob(
        user_id=video.user_id,
        course_id=video.course_id,
        video_id=video.id,
        job_type="video_transcript",
        state="pending",
        youtube_url=f"https://www.youtube.com/watch?v={video.youtube_video_id}",
        youtube_video_id=video.youtube_video_id,
    )
    db.add(job)
    video.status = "waiting_for_transcript"
    video.error_message = None
    video.scheduled_for = None
    video.celery_task_id = None
    await db.commit()
    await db.refresh(job)
    return job


async def claim_edge_jobs(
    db: AsyncSession,
    token: EdgeFetcherToken,
    worker_id: str,
    limit: int,
) -> list[EdgeClaimedJob]:
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=settings.edge_fetcher_lease_seconds)
    limit = min(limit, settings.edge_fetcher_poll_limit)
    query = (
        select(YouTubeEdgeJob)
        .options(selectinload(YouTubeEdgeJob.video))
        .where(
            YouTubeEdgeJob.user_id == token.user_id,
            or_(
                YouTubeEdgeJob.state == "pending",
                and_(YouTubeEdgeJob.state == "retrying", YouTubeEdgeJob.next_attempt_at <= now),
                and_(YouTubeEdgeJob.state.in_(LEASE_STATES), YouTubeEdgeJob.leased_until < now),
            ),
        )
        .order_by(YouTubeEdgeJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(await db.scalars(query))
    claimed: list[EdgeClaimedJob] = []
    for row in rows:
        lease_token = secrets.token_urlsafe(32)
        row.state = "leased"
        row.lease_token = lease_token
        row.leased_until = lease_until
        row.lease_owner = worker_id
        row.attempt_count += 1
        row.error_message = None
        claimed.append(
            EdgeClaimedJob(
                id=row.id,
                lease_token=lease_token,
                job_type=row.job_type,
                course_id=row.course_id,
                video_id=row.video_id,
                youtube_url=row.youtube_url,
                youtube_video_id=row.youtube_video_id,
                title=row.video.title if row.video else None,
                position=row.video.position if row.video else None,
            )
        )
    await db.commit()
    return claimed


async def heartbeat_edge_job(
    db: AsyncSession,
    token: EdgeFetcherToken,
    lease_token: str,
    worker_id: str,
) -> None:
    row = await _job_by_lease(db, token, lease_token)
    row.lease_owner = worker_id
    row.leased_until = datetime.now(UTC) + timedelta(seconds=settings.edge_fetcher_lease_seconds)
    await db.commit()


async def submit_metadata(
    db: AsyncSession,
    token: EdgeFetcherToken,
    job_id: UUID,
    payload: EdgeMetadataSubmit,
) -> Course:
    job = await _job_by_id_and_lease(db, token, job_id, payload.lease_token)
    if job.job_type != "playlist_metadata":
        raise ValidationError("Job is not a playlist metadata job")
    course = await db.get(Course, job.course_id)
    if course is None:
        raise UserIsolationError("Course not found")
    if job.idempotency_key == payload.idempotency_key and job.state == "completed":
        return course
    if not payload.entries:
        raise ValidationError("Playlist contains no available videos")

    course.title = payload.course_title or "Untitled YouTube Course"
    course.playlist_id = payload.playlist_id
    course.video_count = len(payload.entries)
    course.status = "processing"

    existing = list(await db.scalars(select(Video).where(Video.course_id == course.id)))
    if not existing:
        for entry in sorted(payload.entries, key=lambda item: item.position):
            db.add(
                Video(
                    course_id=course.id,
                    user_id=token.user_id,
                    youtube_video_id=entry.youtube_video_id,
                    title=entry.title or "Untitled video",
                    position=entry.position,
                    duration_seconds=entry.duration_seconds,
                    status="waiting_for_transcript",
                )
            )
        await db.flush()
        videos = list(await db.scalars(select(Video).where(Video.course_id == course.id)))
        for video in videos:
            await ensure_transcript_edge_job(db, video)

    job.state = "completed"
    job.idempotency_key = payload.idempotency_key
    job.submitted_payload = payload.model_dump()
    job.lease_token = None
    await db.commit()
    await db.refresh(course)
    return course


async def submit_caption_transcript(
    db: AsyncSession,
    token: EdgeFetcherToken,
    job_id: UUID,
    payload: EdgeCaptionSubmit,
) -> Video:
    job = await _job_by_id_and_lease(db, token, job_id, payload.lease_token)
    video = await _video_for_transcript_job(db, job)
    if job.idempotency_key == payload.idempotency_key and job.state == "completed":
        return video
    transcript = _caption_segments_to_transcript(
        youtube_video_id=video.youtube_video_id,
        source="youtube_captions",
        language=payload.language,
        captions=[segment.model_dump() for segment in payload.segments],
    )
    await _complete_transcript_job(db, job, video, transcript, payload.idempotency_key)
    return video


async def init_audio_upload(
    db: AsyncSession,
    token: EdgeFetcherToken,
    job_id: UUID,
    payload: EdgeAudioUploadInit,
) -> tuple[str, str]:
    job = await _job_by_id_and_lease(db, token, job_id, payload.lease_token)
    video = await _video_for_transcript_job(db, job)
    max_bytes = settings.edge_audio_max_upload_mb * 1024 * 1024
    if payload.size_bytes <= 0 or payload.size_bytes > max_bytes:
        raise ValidationError(f"Audio upload must be between 1 byte and {settings.edge_audio_max_upload_mb} MB")
    if payload.content_type not in {"audio/mpeg", "audio/mp3", "audio/mp4", "audio/webm", "audio/ogg", "audio/wav"}:
        raise ValidationError("Unsupported audio content type")
    object_name = (
        f"{token.user_id}/{video.id}/edge-audio/{job.id}/"
        f"{hashlib.sha256(payload.idempotency_key.encode()).hexdigest()[:16]}.mp3"
    )
    object_uri = build_object_uri(object_name)
    upload_url = await generate_presigned_upload_url(
        object_uri,
        payload.content_type,
        expires_seconds=900,
    )
    job.state = "uploading"
    job.idempotency_key = payload.idempotency_key
    job.audio_object_uri = object_uri
    job.audio_content_type = payload.content_type
    job.audio_size_bytes = payload.size_bytes
    job.audio_duration_seconds = payload.duration_seconds
    job.audio_sha256 = payload.sha256.lower()
    await db.commit()
    return upload_url, object_uri


async def complete_audio_upload(
    db: AsyncSession,
    token: EdgeFetcherToken,
    job_id: UUID,
    payload: EdgeAudioUploadComplete,
) -> Video:
    job = await _job_by_id_and_lease(db, token, job_id, payload.lease_token)
    video = await _video_for_transcript_job(db, job)
    if payload.object_uri != job.audio_object_uri:
        raise ValidationError("Audio object URI does not match initialized upload")
    if payload.sha256.lower() != (job.audio_sha256 or ""):
        raise ValidationError("Audio checksum does not match initialized upload")
    job.audio_size_bytes = payload.size_bytes
    job.audio_duration_seconds = payload.duration_seconds or job.audio_duration_seconds
    job.state = "uploaded"
    job.lease_token = None
    video.status = "transcribing"
    video.error_message = None
    await db.commit()
    from app.tasks.video_tasks import process_uploaded_audio_task

    process_uploaded_audio_task.delay(str(video.id), str(token.user_id), str(job.id))
    return video


async def fail_edge_job(
    db: AsyncSession,
    token: EdgeFetcherToken,
    job_id: UUID,
    lease_token: str,
    message: str,
    retry_after_seconds: int | None,
    permanent: bool,
) -> None:
    job = await _job_by_id_and_lease(db, token, job_id, lease_token)
    if permanent:
        job.state = "failed"
        if job.video_id:
            video = await db.get(Video, job.video_id)
            if video:
                video.status = "failed"
                video.error_message = message
        else:
            course = await db.get(Course, job.course_id)
            if course:
                course.status = "partial"
        job.error_message = message
        job.lease_token = None
        await db.commit()
        return
    job.state = "retrying"
    job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=retry_after_seconds or 300)
    job.error_message = message
    job.lease_token = None
    await db.commit()


async def requeue_missing_transcripts(
    db: AsyncSession,
    user_id: UUID,
    course_id: UUID,
) -> tuple[int, int]:
    course = await db.scalar(
        select(Course)
        .options(selectinload(Course.videos).selectinload(Video.transcript))
        .where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")
    queued = 0
    skipped = 0
    for video in course.videos:
        if video.transcript is not None:
            skipped += 1
            continue
        await ensure_transcript_edge_job(db, video)
        queued += 1
    return queued, skipped


async def edge_status(db: AsyncSession, token: EdgeFetcherToken) -> dict[str, int | datetime | None]:
    rows = await db.execute(
        select(YouTubeEdgeJob.state, func.count())
        .where(YouTubeEdgeJob.user_id == token.user_id)
        .group_by(YouTubeEdgeJob.state)
    )
    counts = {state: count for state, count in rows.all()}
    return {
        "pending": int(counts.get("pending", 0)),
        "leased": int(counts.get("leased", 0)) + int(counts.get("uploading", 0)),
        "retrying": int(counts.get("retrying", 0)),
        "failed": int(counts.get("failed", 0)),
        "last_seen_at": token.last_seen_at,
    }


async def _job_by_lease(
    db: AsyncSession,
    token: EdgeFetcherToken,
    lease_token: str,
) -> YouTubeEdgeJob:
    row = await db.scalar(
        select(YouTubeEdgeJob).where(
            YouTubeEdgeJob.user_id == token.user_id,
            YouTubeEdgeJob.lease_token == lease_token,
            YouTubeEdgeJob.state.in_(["leased", "uploading"]),
        )
    )
    if row is None:
        raise ValidationError("Edge job lease is no longer valid")
    return row


async def _job_by_id_and_lease(
    db: AsyncSession,
    token: EdgeFetcherToken,
    job_id: UUID,
    lease_token: str,
) -> YouTubeEdgeJob:
    row = await db.scalar(
        select(YouTubeEdgeJob).where(
            YouTubeEdgeJob.id == job_id,
            YouTubeEdgeJob.user_id == token.user_id,
            YouTubeEdgeJob.lease_token == lease_token,
            YouTubeEdgeJob.state.in_(["leased", "uploading"]),
        )
    )
    if row is None:
        raise ValidationError("Edge job lease is no longer valid")
    return row


async def _video_for_transcript_job(db: AsyncSession, job: YouTubeEdgeJob) -> Video:
    if job.job_type != "video_transcript" or job.video_id is None:
        raise ValidationError("Job is not a transcript job")
    video = await db.scalar(
        select(Video)
        .options(selectinload(Video.transcript))
        .where(Video.id == job.video_id, Video.user_id == job.user_id)
    )
    if video is None:
        raise UserIsolationError("Video not found")
    return video


async def _complete_transcript_job(
    db: AsyncSession,
    job: YouTubeEdgeJob,
    video: Video,
    transcript: NormalisedTranscript,
    idempotency_key: str,
) -> None:
    await store_transcript(transcript, video, db)
    job.state = "completed"
    job.idempotency_key = idempotency_key
    job.submitted_payload = {"source": transcript.source, "word_count": transcript.word_count}
    job.lease_token = None
    video.status = "pending"
    video.error_message = None
    video.celery_task_id = None
    await db.commit()
    from app.tasks.video_tasks import process_video_task

    process_video_task.delay(str(video.id), str(video.user_id))
