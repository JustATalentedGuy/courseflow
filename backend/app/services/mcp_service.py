import asyncio
import re
from uuid import UUID

from groq import AsyncGroq
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import NoteChunk
from app.models.course import Course
from app.models.video import Video

MCP_GROQ_TIMEOUT_SECONDS = 60
MCP_SEARCH_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "between",
    "difference",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "with",
}


def _timestamp_url(youtube_video_id: str, start_seconds: float | None) -> str:
    return f"https://youtube.com/watch?v={youtube_video_id}&t={int(start_seconds or 0)}s"


def _mcp_search_terms(query: str) -> list[str]:
    terms = []
    for token in re.findall(r"[a-zA-Z0-9+#.\-]+", query.lower()):
        cleaned = token.strip(".-")
        if len(cleaned) < 2 or cleaned in MCP_SEARCH_STOP_WORDS:
            continue
        if cleaned not in terms:
            terms.append(cleaned)
    return terms[:8]


def _postgres_word_pattern(term: str) -> str:
    return rf"\m{re.escape(term)}\M"


def configured_mcp_user_id() -> UUID:
    if not settings.courseflow_mcp_user_id:
        raise RuntimeError("COURSEFLOW_MCP_USER_ID is required")
    try:
        return UUID(settings.courseflow_mcp_user_id)
    except ValueError as exc:
        raise RuntimeError("COURSEFLOW_MCP_USER_ID must be a valid UUID") from exc


async def list_courses_for_mcp(
    db: AsyncSession,
    user_id: UUID,
) -> list[dict[str, object]]:
    completed = func.sum(case((Video.status == "completed", 1), else_=0))
    statement = (
        select(
            Course.id,
            Course.title,
            Course.status,
            Course.video_count,
            Course.created_at,
            completed.label("completed_videos"),
        )
        .outerjoin(Video, Video.course_id == Course.id)
        .where(Course.user_id == user_id)
        .group_by(Course.id)
        .order_by(Course.created_at.desc())
    )
    rows = (await db.execute(statement)).all()
    return [
        {
            "course_id": str(row.id),
            "title": row.title,
            "status": row.status,
            "video_count": row.video_count,
            "completed_videos": int(row.completed_videos or 0),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


async def search_courses_for_mcp(
    db: AsyncSession,
    user_id: UUID,
    query: str,
    course_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    if not query.strip():
        raise ValueError("query must not be empty")
    if course_id is not None:
        try:
            parsed_course_id = UUID(course_id)
        except ValueError as exc:
            raise ValueError("course_id must be a valid UUID") from exc
        owned = await db.scalar(
            select(Course.id).where(Course.id == parsed_course_id, Course.user_id == user_id)
        )
        if owned is None:
            raise ValueError("course_id does not belong to the configured CourseFlow user")

    available = select(NoteChunk.id).where(NoteChunk.user_id == user_id).limit(1)
    if course_id is not None:
        available = available.where(NoteChunk.course_id == UUID(course_id))
    if await db.scalar(available) is None:
        return []

    terms = _mcp_search_terms(query)
    if not terms:
        terms = [query.strip().lower()]
    filters = []
    rank_parts = []
    for term in terms:
        pattern = _postgres_word_pattern(term)
        term_filter = or_(
            NoteChunk.text.op("~*")(pattern),
            NoteChunk.section_heading.op("~*")(pattern),
            Video.title.op("~*")(pattern),
        )
        filters.append(term_filter)
        rank_parts.extend(
            [
                case((Video.title.op("~*")(pattern), 3), else_=0),
                case((NoteChunk.section_heading.op("~*")(pattern), 2), else_=0),
                case((NoteChunk.text.op("~*")(pattern), 1), else_=0),
            ]
        )
    rank = sum(rank_parts).label("rank")
    statement = (
        select(NoteChunk, Video, rank)
        .join(Video, Video.id == NoteChunk.video_id)
        .where(NoteChunk.user_id == user_id, or_(*filters))
        .order_by(desc(rank), Video.position.asc(), NoteChunk.chunk_index.asc())
        .limit(min(max(limit, 1), 10))
    )
    if course_id is not None:
        statement = statement.where(NoteChunk.course_id == UUID(course_id))
    rows = (await db.execute(statement)).all()
    max_rank = max((int(row.rank or 0) for row in rows), default=1)
    return [
        {
            "course_id": str(chunk.course_id),
            "video_id": str(video.id),
            "video_title": video.title,
            "section_heading": chunk.section_heading or "",
            "text": chunk.text,
            "similarity_score": round(float(row_rank or 0) / max_rank, 4),
            "timestamp_url": _timestamp_url(video.youtube_video_id, chunk.start_seconds),
        }
        for chunk, video, row_rank in rows
    ]


async def ask_courses_for_mcp(
    db: AsyncSession,
    user_id: UUID,
    question: str,
    course_id: str | None = None,
    max_sources: int = 6,
    groq_client: AsyncGroq | None = None,
) -> dict[str, object]:
    sources = await search_courses_for_mcp(
        db,
        user_id,
        question,
        course_id=course_id,
        limit=min(max(max_sources, 1), 10),
    )
    if not sources:
        return {
            "answer": "I could not find relevant material in the configured CourseFlow library.",
            "sources": [],
        }

    context = "\n\n".join(
        (
            f"[{index}] {source['video_title']} - {source['section_heading']}\n"
            f"{source['text']}\nURL: {source['timestamp_url']}"
        )
        for index, source in enumerate(sources, start=1)
    )
    client = groq_client
    if client is None:
        if not settings.groq_api_key or settings.groq_api_key == "your_groq_key_here":
            raise RuntimeError("GROQ_API_KEY is required for ask_my_courses")
        client = AsyncGroq(api_key=settings.groq_api_key, max_retries=0)

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.groq_auto_model,
                temperature=0.2,
                max_completion_tokens=900,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Answer only from the supplied CourseFlow excerpts. "
                            "Cite supporting excerpts using [1], [2], and so on. "
                            "If the excerpts are insufficient, say what is missing."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Question: {question.strip()}\n\nCourseFlow excerpts:\n{context}",
                    },
                ],
            ),
            timeout=MCP_GROQ_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return {
            "answer": (
                "I found relevant CourseFlow excerpts, but the LLM answer step timed out. "
                "Use the returned sources to answer manually or retry the same question."
            ),
            "sources": sources,
        }
    answer = response.choices[0].message.content or ""
    return {"answer": answer.strip(), "sources": sources}
