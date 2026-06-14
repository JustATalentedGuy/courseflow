import re
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from groq import AsyncGroq
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import (
    GroqQuotaWaitError,
    NotesValidationError,
    UserIsolationError,
    ValidationError,
)
from app.models.course import Course
from app.models.groq import GroqUsageEvent, NoteGenerationChunk
from app.models.notes import Notes
from app.models.video import Video
from app.schemas.notes import NotesSection, VideoNotes
from app.schemas.chunk import TranscriptChunk
from app.services.chunker import chunk_transcript_for_notes, estimate_model_tokens
from app.services.notes_generator import (
    MAX_COMPLETION_TOKENS,
    build_notes_messages,
    generate_groq_notes_for_chunk,
    generate_notes_for_chunk,
    stitch_chunk_notes,
)
from app.services.quota import QuotaManager
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


def notes_record_to_schema(
    record: Notes,
    video: Video,
    *,
    full_markdown: str | None = None,
) -> VideoNotes:
    return VideoNotes(
        video_id=str(record.video_id),
        course_id=str(record.course_id),
        title=video.title,
        source_model=record.source_model,
        sections=[NotesSection.model_validate(section) for section in record.sections_json],
        summary=record.summary,
        full_markdown=full_markdown if full_markdown is not None else record.full_markdown,
        has_images=record.has_images,
        image_count=record.image_count,
        generated_at=record.generated_at.isoformat(),
        token_count=record.token_count,
        prompt_token_count=record.prompt_token_count,
        completion_token_count=record.completion_token_count,
        cached_token_count=record.cached_token_count,
        request_count=record.request_count,
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
    from app.services.diagram_service import materialize_notes_markdown

    markdown = await materialize_notes_markdown(db, video.notes)
    return notes_record_to_schema(video.notes, video, full_markdown=markdown)


async def get_notes_for_course(db: AsyncSession, user_id: UUID, course_id: UUID) -> list[VideoNotes]:
    course = await db.scalar(
        select(Course)
        .options(selectinload(Course.videos).selectinload(Video.notes))
        .where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")
    from app.services.diagram_service import materialize_notes_markdown

    result: list[VideoNotes] = []
    for video in course.videos:
        if video.notes is not None:
            markdown = await materialize_notes_markdown(db, video.notes)
            result.append(notes_record_to_schema(video.notes, video, full_markdown=markdown))
    return result


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
        previous_source = existing.source_markdown or existing.full_markdown
        if previous_source != validated.full_markdown:
            record.content_version = (record.content_version or 1) + 1
        record.source_model = validated.source_model
        record.source_markdown = validated.full_markdown
        record.full_markdown = validated.full_markdown
        record.summary = validated.summary
        record.sections_json = [section.model_dump() for section in validated.sections]
        record.concepts_json = concepts
        record.has_images = validated.has_images
        record.image_count = validated.image_count
        record.token_count = validated.token_count
        record.prompt_token_count = validated.prompt_token_count
        record.completion_token_count = validated.completion_token_count
        record.cached_token_count = validated.cached_token_count
        record.request_count = validated.request_count
        record.generated_at = generated_at
    else:
        record = Notes(
            video_id=video.id,
            course_id=video.course_id,
            user_id=video.user_id,
            source_model=validated.source_model,
            source_markdown=validated.full_markdown,
            content_version=1,
            full_markdown=validated.full_markdown,
            summary=validated.summary,
            sections_json=[section.model_dump() for section in validated.sections],
            concepts_json=concepts,
            has_images=validated.has_images,
            image_count=validated.image_count,
            token_count=validated.token_count,
            prompt_token_count=validated.prompt_token_count,
            completion_token_count=validated.completion_token_count,
            cached_token_count=validated.cached_token_count,
            request_count=validated.request_count,
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
    from app.services.diagram_service import reconcile_diagrams_after_note_update

    await reconcile_diagrams_after_note_update(db, record, video)
    return record


def model_for_quality(quality: str) -> str:
    return settings.groq_auto_model if quality == "standard" else settings.groq_high_quality_model


def _prompt_fingerprint(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    model: str,
    mode: str,
) -> str:
    payload = {
        "model": model,
        "mode": mode,
        "messages": build_notes_messages(chunk, chunk_index, total_chunks, None),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _estimated_request_tokens(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    previous_summary: str | None,
) -> int:
    messages = build_notes_messages(chunk, chunk_index, total_chunks, previous_summary)
    prompt_tokens = sum(estimate_model_tokens(message["content"]) for message in messages)
    return prompt_tokens + MAX_COMPLETION_TOKENS


async def prepare_generation_chunks(
    db: AsyncSession,
    video: Video,
    *,
    mode: str,
    model: str,
    reset: bool = False,
) -> list[NoteGenerationChunk]:
    if video.transcript is None:
        raise ValidationError("Video transcript is required before notes generation")
    transcript = transcript_record_to_schema(video.transcript, video.youtube_video_id)
    transcript_chunks = chunk_transcript_for_notes(transcript)

    if reset:
        await db.execute(
            delete(NoteGenerationChunk).where(
                NoteGenerationChunk.video_id == video.id,
                NoteGenerationChunk.mode == mode,
            )
        )
        await db.flush()

    existing = list(
        await db.scalars(
            select(NoteGenerationChunk)
            .where(
                NoteGenerationChunk.video_id == video.id,
                NoteGenerationChunk.mode == mode,
            )
            .order_by(NoteGenerationChunk.chunk_index)
        )
    )
    if existing:
        return existing

    rows = [
        NoteGenerationChunk(
            video_id=video.id,
            course_id=video.course_id,
            user_id=video.user_id,
            mode=mode,
            chunk_index=chunk.chunk_index,
            model=model,
            prompt_fingerprint=_prompt_fingerprint(
                chunk,
                chunk.chunk_index,
                len(transcript_chunks),
                model,
                mode,
            ),
            transcript_text=chunk.text,
            state="pending",
            estimated_tokens=_estimated_request_tokens(
                chunk,
                chunk.chunk_index,
                len(transcript_chunks),
                None,
            ),
        )
        for chunk in transcript_chunks
    ]
    db.add_all(rows)
    await db.commit()
    return rows


def _chunk_schema(row: NoteGenerationChunk) -> TranscriptChunk:
    return TranscriptChunk(
        text=row.transcript_text,
        start_seconds=0,
        end_seconds=1,
        chunk_index=row.chunk_index,
    )


def _error_headers(exc: Exception) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _error_body(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else str(exc)


async def _generate_pending_chunks(
    db: AsyncSession,
    video: Video,
    chunks: list[NoteGenerationChunk],
    model: str,
) -> None:
    configured_key = settings.groq_api_key not in {"", "your_groq_key_here"}
    groq_client = (
        AsyncGroq(api_key=settings.groq_api_key, max_retries=0)
        if configured_key
        else None
    )
    quota = QuotaManager() if configured_key else None

    try:
        for index, row in enumerate(chunks):
            if row.state == "completed" and row.response_markdown:
                continue

            previous_summary = None
            if index > 0 and chunks[index - 1].response_markdown:
                previous_summary = _summary_from_markdown(chunks[index - 1].response_markdown)
            chunk = _chunk_schema(row)
            estimated_tokens = _estimated_request_tokens(
                chunk,
                row.chunk_index,
                len(chunks),
                previous_summary,
            )
            row.estimated_tokens = estimated_tokens
            row.prompt_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "model": model,
                        "mode": row.mode,
                        "messages": build_notes_messages(
                            chunk,
                            row.chunk_index,
                            len(chunks),
                            previous_summary,
                        ),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            row.state = "processing"
            row.retry_at = None
            row.error_message = None
            await db.commit()

            if groq_client is None or quota is None:
                markdown = await generate_notes_for_chunk(
                    chunk,
                    row.chunk_index,
                    len(chunks),
                    previous_summary,
                    None,
                )
                row.response_markdown = markdown
                row.prompt_tokens = estimate_model_tokens(chunk.text)
                row.completion_tokens = estimate_model_tokens(markdown)
                row.cached_tokens = 0
                row.charged_tokens = row.prompt_tokens + row.completion_tokens
                row.state = "completed"
                await db.commit()
                continue

            reservation = await quota.reserve(db, model, estimated_tokens)
            try:
                result = await generate_groq_notes_for_chunk(
                    chunk,
                    row.chunk_index,
                    len(chunks),
                    previous_summary,
                    groq_client,
                    model,
                )
            except Exception as exc:
                if getattr(exc, "status_code", None) == 429:
                    headers = _error_headers(exc)
                    await quota.release(reservation)
                    wait_error = await quota.wait_from_headers(
                        model,
                        headers,
                        _error_body(exc),
                    )
                    row.state = "rate_limited"
                    row.retry_at = datetime.now(UTC) + timedelta(seconds=wait_error.retry_after)
                    row.error_message = str(wait_error)
                    await db.commit()
                    raise wait_error from exc
                await quota.release(reservation)
                row.state = "pending"
                row.error_message = str(exc)
                await db.commit()
                raise

            await quota.reconcile(reservation, result.charged_tokens, result.headers)
            row.response_markdown = result.markdown
            row.prompt_tokens = result.prompt_tokens
            row.completion_tokens = result.completion_tokens
            row.cached_tokens = result.cached_tokens
            row.charged_tokens = result.charged_tokens
            row.request_id = result.request_id
            row.state = "completed"
            db.add(
                GroqUsageEvent(
                    model=model,
                    user_id=video.user_id,
                    video_id=video.id,
                    course_id=video.course_id,
                    mode=row.mode,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    cached_tokens=result.cached_tokens,
                    charged_tokens=result.charged_tokens,
                    request_id=result.request_id,
                )
            )
            await db.commit()
    except GroqQuotaWaitError as exc:
        current = next((chunk for chunk in chunks if chunk.state != "completed"), None)
        if current is not None:
            current.state = "rate_limited"
            current.retry_at = datetime.now(UTC) + timedelta(seconds=exc.retry_after)
            current.error_message = str(exc)
            await db.commit()
        raise
    finally:
        close_client = getattr(groq_client, "close", None)
        if close_client is not None:
            await close_client()
        if quota is not None:
            await quota.close()


async def generate_notes_for_video(
    db: AsyncSession,
    user_id: UUID,
    video_id: UUID,
    *,
    quality: str = "standard",
    reset: bool = False,
) -> VideoNotes:
    video = await get_video_for_user(db, user_id, video_id)
    if video.transcript is None:
        raise ValidationError("Video transcript is required before notes generation")
    mode = quality
    model = model_for_quality(quality)
    chunks = await prepare_generation_chunks(
        db,
        video,
        mode=mode,
        model=model,
        reset=reset,
    )
    await _generate_pending_chunks(db, video, chunks, model)

    refreshed_chunks = list(
        await db.scalars(
            select(NoteGenerationChunk)
            .where(
                NoteGenerationChunk.video_id == video.id,
                NoteGenerationChunk.mode == mode,
            )
            .order_by(NoteGenerationChunk.chunk_index)
        )
    )
    if not refreshed_chunks or any(row.state != "completed" for row in refreshed_chunks):
        raise ValidationError("Notes generation chunks are incomplete")

    full_markdown = await stitch_chunk_notes(
        [row.response_markdown or "" for row in refreshed_chunks],
        video.title,
    )
    sections = parse_notes_sections(full_markdown)
    summary = _summary_from_markdown(full_markdown)
    prompt_tokens = sum(row.prompt_tokens for row in refreshed_chunks)
    completion_tokens = sum(row.completion_tokens for row in refreshed_chunks)
    cached_tokens = sum(row.cached_tokens for row in refreshed_chunks)
    configured_key = settings.groq_api_key not in {"", "your_groq_key_here"}
    request_count = 0
    if configured_key:
        generation_started_at = min(
            (row.created_at for row in refreshed_chunks if row.created_at is not None),
            default=datetime.now(UTC),
        )
        request_count = int(
            await db.scalar(
                select(func.count(GroqUsageEvent.id)).where(
                    GroqUsageEvent.video_id == video.id,
                    GroqUsageEvent.created_at >= generation_started_at,
                    GroqUsageEvent.mode.in_(
                        [mode, f"{mode}:rate_limited", f"batch:{mode}"]
                    ),
                )
            )
            or 0
        )
    video_notes = VideoNotes(
        video_id=str(video.id),
        course_id=str(video.course_id),
        title=video.title,
        source_model=f"groq/{model}" if configured_key else "local/deterministic",
        sections=sections,
        summary=summary,
        full_markdown=full_markdown,
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=prompt_tokens + completion_tokens,
        prompt_token_count=prompt_tokens,
        completion_token_count=completion_tokens,
        cached_token_count=cached_tokens,
        request_count=request_count,
    )
    record = await validate_and_store_notes(video_notes, db, owner_id=user_id)
    from app.services.diagram_service import materialize_notes_markdown

    markdown = await materialize_notes_markdown(db, record)
    return notes_record_to_schema(record, video, full_markdown=markdown)
