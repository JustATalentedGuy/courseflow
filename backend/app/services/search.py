import asyncio
from uuid import UUID

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import NoteChunk
from app.models.video import Video
from app.schemas.search import SearchResult
from app.services.embedder import embed_texts

QUERY_EMBEDDING_TIMEOUT_SECONDS = 90


def _timestamp_url(youtube_video_id: str, start_seconds: float | None) -> str:
    return f"https://youtube.com/watch?v={youtube_video_id}&t={int(start_seconds or 0)}s"


def _similarity_score(distance: float | None) -> float:
    if distance is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(distance)))


async def semantic_search(
    query: str,
    user_id: str,
    course_id: str | None,
    top_k: int,
    db: AsyncSession,
) -> list[SearchResult]:
    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    capped_top_k = min(max(top_k, 1), 20)
    try:
        query_embedding = (
            await asyncio.wait_for(
                asyncio.to_thread(embed_texts, [cleaned_query]),
                timeout=QUERY_EMBEDDING_TIMEOUT_SECONDS,
            )
        )[0]
    except TimeoutError as exc:
        raise RuntimeError(
            "CourseFlow search is still warming up the local embedding model. "
            "Retry the same request in a minute."
        ) from exc
    distance = NoteChunk.embedding.cosine_distance(query_embedding).label("distance")

    statement = (
        select(NoteChunk, Video, distance)
        .join(Video, Video.id == NoteChunk.video_id)
        .where(NoteChunk.user_id == UUID(user_id), NoteChunk.embedding.is_not(None))
        .order_by(distance.asc())
        .limit(capped_top_k)
    )
    if course_id is not None:
        statement = statement.where(NoteChunk.course_id == UUID(course_id))

    rows = (await db.execute(statement)).all()
    return [
        SearchResult(
            chunk_id=str(chunk.id),
            video_id=str(video.id),
            video_title=video.title,
            course_id=str(chunk.course_id),
            section_heading=chunk.section_heading or "",
            text=chunk.text,
            similarity_score=_similarity_score(row_distance),
            start_seconds=chunk.start_seconds or 0.0,
            timestamp_url=_timestamp_url(video.youtube_video_id, chunk.start_seconds),
        )
        for chunk, video, row_distance in rows
    ]


async def suggest_search_terms(partial_query: str, user_id: str, db: AsyncSession) -> list[str]:
    partial = partial_query.strip()
    if not partial:
        return []

    statement = (
        select(distinct(NoteChunk.section_heading))
        .where(
            NoteChunk.user_id == UUID(user_id),
            NoteChunk.section_heading.is_not(None),
            NoteChunk.section_heading.ilike(f"%{partial}%"),
        )
        .order_by(NoteChunk.section_heading.asc())
        .limit(10)
    )
    return [heading for heading in (await db.scalars(statement)).all() if heading]
