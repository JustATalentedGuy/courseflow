from uuid import UUID

import re

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user, get_db, get_redis
from app.core.exceptions import UserIsolationError
from app.models.user import User
from app.models.video import Video
from app.schemas.manual_assist import ManualNotesRequest, ManualNotesResult, ManualPrompt
from app.schemas.notes import VideoNotes
from app.schemas.transcript import NormalisedTranscript
from app.schemas.video import VideoResponse
from app.services.notes_service import (
    generate_notes_for_video,
    get_notes_for_video,
    get_video_for_user,
)
from app.services.export import export_notes_markdown, export_notes_pdf
from app.services.manual_assist import generate_manual_prompt, submit_manual_notes
from app.services.transcript import transcript_record_to_schema

router = APIRouter(prefix="/videos", tags=["videos"])


def _download_filename(title: str, extension: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-") or "courseflow-notes"
    return f"{slug}.{extension}"


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VideoResponse:
    video = await get_video_for_user(db, current_user.id, video_id)
    return VideoResponse.model_validate(video)


@router.get("/{video_id}/transcript", response_model=NormalisedTranscript)
async def get_video_transcript(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NormalisedTranscript:
    video = await db.scalar(
        select(Video)
        .options(selectinload(Video.transcript))
        .where(Video.id == video_id, Video.user_id == current_user.id)
    )
    if video is None or video.transcript is None:
        raise UserIsolationError("Transcript not found")
    return transcript_record_to_schema(video.transcript, video.youtube_video_id)


@router.get("/{video_id}/notes", response_model=VideoNotes)
async def get_video_notes(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VideoNotes:
    return await get_notes_for_video(db, current_user.id, video_id)


@router.get("/{video_id}/notes/raw", response_class=PlainTextResponse)
async def get_video_notes_raw(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    notes = await get_notes_for_video(db, current_user.id, video_id)
    return notes.full_markdown


@router.post("/{video_id}/notes/regenerate", response_model=VideoNotes)
async def regenerate_video_notes(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VideoNotes:
    return await generate_notes_for_video(db, current_user.id, video_id)


@router.get("/{video_id}/manual-prompt", response_model=ManualPrompt)
async def get_manual_prompt(
    video_id: UUID,
    chunk: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ManualPrompt:
    return await generate_manual_prompt(video_id, chunk, current_user.id, db, redis)


@router.post("/{video_id}/manual-notes", response_model=ManualNotesResult)
async def post_manual_notes(
    video_id: UUID,
    payload: ManualNotesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ManualNotesResult:
    return await submit_manual_notes(
        video_id,
        payload.chunk_index,
        payload.response,
        current_user.id,
        db,
        redis,
    )


@router.get("/{video_id}/export/markdown")
async def download_notes_markdown(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    video = await get_video_for_user(db, current_user.id, video_id)
    content = await export_notes_markdown(video_id, current_user.id, db)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_download_filename(video.title, "md")}"'
            )
        },
    )


@router.get("/{video_id}/export/pdf")
async def download_notes_pdf(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    video = await get_video_for_user(db, current_user.id, video_id)
    content = await export_notes_pdf(video_id, current_user.id, db)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_download_filename(video.title, "pdf")}"'
            )
        },
    )
