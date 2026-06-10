import re
from datetime import UTC, datetime
from uuid import UUID

from groq import AsyncGroq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import NotesValidationError, UserIsolationError, ValidationError
from app.models.course import Course
from app.models.notes import Notes
from app.models.video import Video
from app.schemas.notes import NotesSection, VideoNotes
from app.services.chunker import chunk_transcript_for_notes
from app.services.notes_generator import generate_notes_for_chunk, stitch_chunk_notes
from app.services.transcript import transcript_record_to_schema


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()])


def _summary_from_markdown(markdown: str) -> str:
    match = re.search(r"## Summary\s+(.*?)(?=\n## |\Z)", markdown, flags=re.DOTALL)
    if match:
        summary = " ".join(match.group(1).split())
        if summary:
            return summary
    plain = re.sub(r"#+\s*", "", markdown)
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", plain)
    sentence = re.split(r"(?<=[.!?])\s+", " ".join(plain.split()))[0]
    return sentence if re.search(r"[.!?]$", sentence) else f"{sentence}."


def _extract_concepts(block: str) -> list[str]:
    concepts_match = re.search(r"Key Concepts:\s*(.*)", block, flags=re.DOTALL | re.IGNORECASE)
    if not concepts_match:
        return []
    concepts: list[str] = []
    for line in concepts_match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            concept = stripped.lstrip("-* ").strip()
            if concept:
                concepts.append(concept)
    return concepts[:5]


def parse_notes_sections(markdown: str) -> list[NotesSection]:
    matches = list(re.finditer(r"^(##|###)\s+(.+)$", markdown, flags=re.MULTILINE))
    sections: list[NotesSection] = []
    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        if heading.lower() == "summary":
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        content = markdown[start:end].strip()
        concepts = _extract_concepts(content)
        if not concepts:
            words = [word.strip(".,:;!?").lower() for word in content.split() if len(word.strip(".,:;!?")) > 4]
            concepts = list(dict.fromkeys(words))[:5]
        sections.append(
            NotesSection(
                heading=heading,
                level=len(match.group(1)),
                content=content,
                concepts=concepts,
            )
        )
    return sections


def notes_record_to_schema(record: Notes, video: Video) -> VideoNotes:
    return VideoNotes(
        video_id=str(record.video_id),
        course_id=str(record.course_id),
        title=video.title,
        source_model=record.source_model,
        sections=[NotesSection.model_validate(section) for section in record.sections_json],
        summary=record.summary,
        full_markdown=record.full_markdown,
        has_images=record.has_images,
        image_count=record.image_count,
        generated_at=record.generated_at.isoformat(),
        token_count=record.token_count,
    )


async def get_video_for_user(db: AsyncSession, user_id: UUID, video_id: UUID) -> Video:
    video = await db.scalar(
        select(Video)
        .options(selectinload(Video.notes), selectinload(Video.transcript))
        .where(Video.id == video_id, Video.user_id == user_id)
    )
    if video is None:
        raise UserIsolationError("Video not found")
    return video


async def get_notes_for_video(db: AsyncSession, user_id: UUID, video_id: UUID) -> VideoNotes:
    video = await get_video_for_user(db, user_id, video_id)
    if video.notes is None:
        raise UserIsolationError("Notes not found")
    return notes_record_to_schema(video.notes, video)


async def get_notes_for_course(db: AsyncSession, user_id: UUID, course_id: UUID) -> list[VideoNotes]:
    course = await db.scalar(
        select(Course)
        .options(selectinload(Course.videos).selectinload(Video.notes))
        .where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")
    return [
        notes_record_to_schema(video.notes, video)
        for video in course.videos
        if video.notes is not None
    ]


def _validate_video_notes(notes: VideoNotes) -> None:
    if len(notes.full_markdown.strip()) <= 100:
        raise NotesValidationError("full_markdown must be non-empty and longer than 100 characters")
    if "##" not in notes.full_markdown:
        raise NotesValidationError("full_markdown must contain at least one ## heading")
    if _sentence_count(notes.summary) < 1 or _sentence_count(notes.summary) > 5:
        raise NotesValidationError("summary must be 1-5 sentences")
    concepts = [concept for section in notes.sections for concept in section.concepts]
    if not concepts:
        raise NotesValidationError("concepts_json must be non-empty")
    if notes.source_model.startswith("groq/") and notes.token_count == 0:
        raise NotesValidationError("Groq notes must record a non-zero token count")


async def validate_and_store_notes(
    notes: VideoNotes,
    db: AsyncSession,
    owner_id: UUID | None = None,
) -> Notes:
    try:
        validated = VideoNotes.model_validate(notes.model_dump())
    except Exception as exc:
        raise NotesValidationError(str(exc)) from exc
    _validate_video_notes(validated)

    video_statement = (
        select(Video)
        .options(selectinload(Video.course))
        .where(
            Video.id == UUID(validated.video_id),
            Video.course_id == UUID(validated.course_id),
        )
    )
    if owner_id is not None:
        video_statement = video_statement.where(Video.user_id == owner_id)
    video = await db.scalar(video_statement)
    if video is None:
        raise ValidationError("Video not found for notes")

    concepts = [concept for section in validated.sections for concept in section.concepts]
    generated_at = datetime.fromisoformat(validated.generated_at.replace("Z", "+00:00"))
    existing = await db.scalar(
        select(Notes).where(Notes.video_id == video.id, Notes.user_id == video.user_id)
    )
    if existing:
        record = existing
        record.source_model = validated.source_model
        record.full_markdown = validated.full_markdown
        record.summary = validated.summary
        record.sections_json = [section.model_dump() for section in validated.sections]
        record.concepts_json = concepts
        record.has_images = validated.has_images
        record.image_count = validated.image_count
        record.token_count = validated.token_count
        record.generated_at = generated_at
    else:
        record = Notes(
            video_id=video.id,
            course_id=video.course_id,
            user_id=video.user_id,
            source_model=validated.source_model,
            full_markdown=validated.full_markdown,
            summary=validated.summary,
            sections_json=[section.model_dump() for section in validated.sections],
            concepts_json=concepts,
            has_images=validated.has_images,
            image_count=validated.image_count,
            token_count=validated.token_count,
            generated_at=generated_at,
        )
        db.add(record)

    video.status = "completed"
    course = await db.scalar(
        select(Course)
        .options(selectinload(Course.videos))
        .where(Course.id == video.course_id, Course.user_id == video.user_id)
    )
    if course is not None:
        course.status = "completed" if all(item.status == "completed" for item in course.videos) else "partial"

    await db.commit()
    await db.refresh(record)
    return record


async def generate_notes_for_video(db: AsyncSession, user_id: UUID, video_id: UUID) -> VideoNotes:
    video = await get_video_for_user(db, user_id, video_id)
    if video.transcript is None:
        raise ValidationError("Video transcript is required before notes generation")

    transcript = transcript_record_to_schema(video.transcript, video.youtube_video_id)
    chunks = chunk_transcript_for_notes(transcript)
    groq_client = None
    if settings.groq_api_key and settings.groq_api_key != "your_groq_key_here":
        groq_client = AsyncGroq(api_key=settings.groq_api_key)

    chunk_markdowns: list[str] = []
    previous_summary: str | None = None
    for chunk in chunks:
        markdown = await generate_notes_for_chunk(
            chunk=chunk,
            chunk_index=chunk.chunk_index,
            total_chunks=len(chunks),
            previous_summary=previous_summary,
            groq_client=groq_client,
        )
        chunk_markdowns.append(markdown)
        previous_summary = _summary_from_markdown(markdown)

    full_markdown = await stitch_chunk_notes(chunk_markdowns, video.title)
    sections = parse_notes_sections(full_markdown)
    summary = _summary_from_markdown(full_markdown)
    video_notes = VideoNotes(
        video_id=str(video.id),
        course_id=str(video.course_id),
        title=video.title,
        source_model="groq/llama-3.3-70b",
        sections=sections,
        summary=summary,
        full_markdown=full_markdown,
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=sum(len(chunk.text.split()) for chunk in chunks),
    )
    await validate_and_store_notes(video_notes, db, owner_id=user_id)
    return video_notes
