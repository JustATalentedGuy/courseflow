import math
import re
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ManualChunkIndexError, NotesValidationError
from app.schemas.manual_assist import ManualNotesResult, ManualPrompt
from app.schemas.notes import VideoNotes
from app.services.chunker import chunk_transcript_for_notes
from app.services.notes_generator import NOTES_SYSTEM_PROMPT, stitch_chunk_notes
from app.services.notes_service import (
    _summary_from_markdown,
    get_video_for_user,
    parse_notes_sections,
    validate_and_store_notes,
)
from app.services.transcript import (
    extract_transcript,
    store_transcript,
    transcript_record_to_schema,
)

MANUAL_CHUNKS_TTL_SECONDS = 24 * 60 * 60


def _manual_key(video_id: UUID, user_id: UUID) -> str:
    return f"manual:chunks:{video_id}:{user_id}"


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _validate_chunk_index(chunk_index: int, total_chunks: int) -> None:
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise ManualChunkIndexError(
            f"Chunk index {chunk_index} is out of range for {total_chunks} chunks"
        )


def _validate_manual_response(response: str) -> str:
    cleaned = response.strip()
    if not cleaned:
        raise NotesValidationError("Manual notes response cannot be empty")
    if re.search(r"^##\s+\S", cleaned, flags=re.MULTILINE) is None:
        raise NotesValidationError("Manual notes must contain at least one ## heading")
    return cleaned


async def _load_video_chunks(
    video_id: UUID,
    user_id: UUID,
    db: AsyncSession,
):
    video = await get_video_for_user(db, user_id, video_id)
    if video.transcript is None:
        transcript = await extract_transcript(video, db)
        await store_transcript(transcript, video, db)
    else:
        transcript = transcript_record_to_schema(video.transcript, video.youtube_video_id)
    return video, chunk_transcript_for_notes(transcript)


async def generate_manual_prompt(
    video_id: UUID,
    chunk_index: int,
    user_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> ManualPrompt:
    video, chunks = await _load_video_chunks(video_id, user_id, db)
    _validate_chunk_index(chunk_index, len(chunks))

    previous_context = ""
    if chunk_index > 0:
        previous_response = await redis.hget(
            _manual_key(video_id, user_id),
            f"response:{chunk_index - 1}",
        )
        if previous_response:
            previous_context = (
                "\n\nPrevious chunk summary:\n"
                f"{_summary_from_markdown(previous_response)}\n"
                "Continue without repeating that material."
            )

    chunk = chunks[chunk_index]
    prompt_text = (
        "SYSTEM INSTRUCTIONS\n"
        f"{NOTES_SYSTEM_PROMPT}\n\n"
        "TASK\n"
        f"This is transcript chunk {chunk_index + 1} of {len(chunks)} for "
        f'the video "{video.title}".'
        f"{previous_context}\n\n"
        "TRANSCRIPT CHUNK\n"
        f"{chunk.text}\n\n"
        "FORMATTING REQUIREMENTS\n"
        "- Return Markdown only.\n"
        "- Include at least one ## main heading.\n"
        "- End each main section with a Key Concepts bullet list.\n"
        "- Preserve any image placeholder tokens exactly."
    )
    return ManualPrompt(
        prompt_text=prompt_text,
        chunk_index=chunk_index,
        total_chunks=len(chunks),
        estimated_tokens=_estimate_tokens(prompt_text),
        video_title=video.title,
    )


async def submit_manual_notes(
    video_id: UUID,
    chunk_index: int,
    llm_response: str,
    user_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> ManualNotesResult:
    video, chunks = await _load_video_chunks(video_id, user_id, db)
    total_chunks = len(chunks)
    _validate_chunk_index(chunk_index, total_chunks)
    cleaned_response = _validate_manual_response(llm_response)

    key = _manual_key(video_id, user_id)
    await redis.hset(
        key,
        mapping={
            "total_chunks": str(total_chunks),
            f"response:{chunk_index}": cleaned_response,
        },
    )
    await redis.expire(key, MANUAL_CHUNKS_TTL_SECONDS)

    fields = await redis.hkeys(key)
    received = sorted(
        int(field.split(":", 1)[1])
        for field in fields
        if field.startswith("response:")
    )
    if len(received) < total_chunks:
        if video.status != "completed":
            video.status = "manual"
            await db.commit()
        return ManualNotesResult(
            status="partial",
            received_chunks=received,
            total_chunks=total_chunks,
        )

    chunk_markdowns = [
        (await redis.hget(key, f"response:{index}")) or ""
        for index in range(total_chunks)
    ]
    if any(not markdown for markdown in chunk_markdowns):
        raise NotesValidationError("One or more manual chunks are missing")

    full_markdown = await stitch_chunk_notes(chunk_markdowns, video.title)
    sections = parse_notes_sections(full_markdown)
    summary = _summary_from_markdown(full_markdown)
    image_count = len(re.findall(r"!\[[^\]]*]\([^)]+\)", full_markdown))
    video_notes = VideoNotes(
        video_id=str(video.id),
        course_id=str(video.course_id),
        title=video.title,
        source_model="manual/user",
        sections=sections,
        summary=summary,
        full_markdown=full_markdown,
        has_images=image_count > 0,
        image_count=image_count,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=0,
    )
    record = await validate_and_store_notes(video_notes, db, owner_id=user_id)
    return ManualNotesResult(
        status="complete",
        notes_id=str(record.id),
        received_chunks=received,
        total_chunks=total_chunks,
    )
