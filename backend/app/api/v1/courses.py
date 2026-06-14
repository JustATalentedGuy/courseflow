from uuid import UUID

import re

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.course import (
    CourseCreate,
    CourseDetail,
    CourseResponse,
    CourseStatusResponse,
)
from app.schemas.notes import VideoNotes
from app.services.course_service import (
    delete_course,
    get_course,
    get_course_status,
    list_courses,
)
from app.services.ingestion import ingest_playlist
from app.services.export import (
    export_anki_deck,
    export_course_notes_markdown,
    export_course_notes_pdf,
)
from app.services.notes_service import get_notes_for_course
from app.tasks.video_tasks import dispatch_course_tasks

router = APIRouter(prefix="/courses", tags=["courses"])


@router.post("", response_model=CourseResponse, status_code=201)
async def create(
    payload: CourseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CourseResponse:
    course = await ingest_playlist(payload.playlist_url, current_user.id, db)
    dispatch_course_tasks.delay(str(course.id), str(current_user.id))
    return CourseResponse.model_validate(course)


@router.get("", response_model=list[CourseResponse])
async def list_user_courses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CourseResponse]:
    courses = await list_courses(db, current_user.id)
    return [CourseResponse.model_validate(course) for course in courses]


@router.get("/{course_id}", response_model=CourseDetail)
async def get_user_course(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CourseDetail:
    course = await get_course(db, current_user.id, course_id)
    return CourseDetail.model_validate(course)


@router.get("/{course_id}/status", response_model=CourseStatusResponse)
async def get_user_course_status(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CourseStatusResponse:
    counts = await get_course_status(db, current_user.id, course_id)
    return CourseStatusResponse(**counts)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_course(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await delete_course(db, current_user.id, course_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{course_id}/notes", response_model=list[VideoNotes])
async def get_course_notes(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[VideoNotes]:
    return await get_notes_for_course(db, current_user.id, course_id)


@router.get("/{course_id}/export/anki")
async def download_course_anki(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    course = await get_course(db, current_user.id, course_id)
    content = await export_anki_deck(course_id, current_user.id, db)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", course.title).strip("-") or "courseflow"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}.apkg"'},
    )


def _course_export_filename(title: str, extension: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-") or "courseflow"
    return f"{slug}-notes.{extension}"


@router.get("/{course_id}/export/notes/markdown")
async def download_course_notes_markdown(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    course = await get_course(db, current_user.id, course_id)
    content = await export_course_notes_markdown(course_id, current_user.id, db)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_course_export_filename(course.title, "md")}"'
            )
        },
    )


@router.get("/{course_id}/export/notes/pdf")
async def download_course_notes_pdf(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    course = await get_course(db, current_user.id, course_id)
    content = await export_course_notes_pdf(course_id, current_user.id, db)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_course_export_filename(course.title, "pdf")}"'
            )
        },
    )
