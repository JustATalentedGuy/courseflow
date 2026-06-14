import inspect
import json
import tempfile
from pathlib import Path
from uuid import UUID

from groq import AsyncGroq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.groq import GroqBatchJob, GroqUsageEvent, NoteGenerationChunk
from app.models.video import Video
from app.services.notes_generator import (
    MAX_COMPLETION_TOKENS,
    build_notes_messages,
)
from app.schemas.chunk import TranscriptChunk

TERMINAL_BATCH_STATES = {"completed", "failed", "expired", "cancelled"}


def _custom_id(row: NoteGenerationChunk) -> str:
    return f"{row.video_id}:{row.mode}:{row.chunk_index}"


def _batch_line(row: NoteGenerationChunk, total_chunks: int) -> dict:
    chunk = TranscriptChunk(
        text=row.transcript_text,
        start_seconds=0,
        end_seconds=1,
        chunk_index=row.chunk_index,
    )
    return {
        "custom_id": _custom_id(row),
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": row.model,
            "messages": build_notes_messages(
                chunk,
                row.chunk_index,
                total_chunks,
                None,
            ),
            "temperature": 0.2,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
        },
    }


async def submit_video_batch(
    db: AsyncSession,
    video_id: UUID,
    user_id: UUID,
) -> GroqBatchJob:
    if not settings.groq_batch_enabled:
        raise ValidationError("Groq Batch is disabled")
    video = await db.scalar(
        select(Video).where(Video.id == video_id, Video.user_id == user_id)
    )
    if video is None:
        raise ValidationError("Video not found")
    chunks = list(
        await db.scalars(
            select(NoteGenerationChunk)
            .where(
                NoteGenerationChunk.video_id == video_id,
                NoteGenerationChunk.mode == "standard",
                NoteGenerationChunk.state != "completed",
                NoteGenerationChunk.groq_batch_job_id.is_(None),
            )
            .order_by(NoteGenerationChunk.chunk_index)
        )
    )
    if not chunks:
        raise ValidationError("No pending Scout chunks are available for Batch")

    total_chunks = max(row.chunk_index for row in chunks) + 1
    payload = "\n".join(
        json.dumps(_batch_line(row, total_chunks), separators=(",", ":"))
        for row in chunks
    )
    client = AsyncGroq(api_key=settings.groq_api_key)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsonl",
            encoding="utf-8",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        with temporary_path.open("rb") as batch_file:
            uploaded = await client.files.create(file=batch_file, purpose="batch")
        batch = await client.batches.create(
            completion_window="24h",
            endpoint="/v1/chat/completions",
            input_file_id=uploaded.id,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    job = GroqBatchJob(
        model=settings.groq_auto_model,
        status=batch.status,
        groq_batch_id=batch.id,
        input_file_id=uploaded.id,
        metadata_json={
            "video_id": str(video.id),
            "user_id": str(video.user_id),
            "chunk_ids": [str(row.id) for row in chunks],
        },
    )
    db.add(job)
    await db.flush()
    for row in chunks:
        row.state = "batch_processing"
        row.groq_batch_job_id = job.id
        row.retry_at = None
    video.status = "batch_processing"
    video.scheduled_for = None
    await db.commit()
    await db.refresh(job)
    return job


async def _response_text(response) -> str:
    text_method = getattr(response, "text", None)
    if callable(text_method):
        value = text_method()
        return await value if inspect.isawaitable(value) else value
    content = getattr(response, "content", b"")
    if inspect.isawaitable(content):
        content = await content
    return content.decode("utf-8") if isinstance(content, bytes) else str(content)


def _content_from_body(body: dict) -> str:
    message = body["choices"][0]["message"]
    return str(message.get("content") or "").strip()


async def _apply_output(
    db: AsyncSession,
    job: GroqBatchJob,
    output_text: str,
) -> None:
    chunks = {
        _custom_id(row): row
        for row in await db.scalars(
            select(NoteGenerationChunk).where(
                NoteGenerationChunk.groq_batch_job_id == job.id
            )
        )
    }
    for line in output_text.splitlines():
        if not line.strip():
            continue
        result = json.loads(line)
        response = result.get("response") or {}
        if response.get("status_code") != 200:
            continue
        row = chunks.get(result.get("custom_id"))
        if row is None or row.state == "completed":
            continue
        body = response.get("body") or {}
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        cached_tokens = int(details.get("cached_tokens") or usage.get("cached_tokens") or 0)
        request_id = response.get("request_id") or result.get("id")
        row.response_markdown = _content_from_body(body)
        row.prompt_tokens = prompt_tokens
        row.completion_tokens = completion_tokens
        row.cached_tokens = cached_tokens
        row.charged_tokens = max(prompt_tokens + completion_tokens - cached_tokens, 0)
        row.request_id = request_id
        row.state = "completed"
        db.add(
            GroqUsageEvent(
                model=row.model,
                user_id=row.user_id,
                video_id=row.video_id,
                course_id=row.course_id,
                mode=f"batch:{row.mode}",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                charged_tokens=row.charged_tokens,
                request_id=request_id,
            )
        )
    await db.commit()


async def poll_batch(db: AsyncSession, job_id: UUID) -> str:
    job = await db.get(GroqBatchJob, job_id)
    if job is None or not job.groq_batch_id:
        raise ValidationError("Groq Batch job not found")
    client = AsyncGroq(api_key=settings.groq_api_key)
    batch = await client.batches.retrieve(job.groq_batch_id)
    job.status = batch.status
    job.output_file_id = getattr(batch, "output_file_id", None)
    job.error_file_id = getattr(batch, "error_file_id", None)
    await db.commit()

    if job.output_file_id and batch.status in TERMINAL_BATCH_STATES:
        output = await client.files.content(job.output_file_id)
        await _apply_output(db, job, await _response_text(output))
    return batch.status
