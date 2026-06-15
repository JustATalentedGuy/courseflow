from uuid import UUID

from groq import AsyncGroq
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.course import Course
from app.models.video import Video
from app.services.search import semantic_search


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

    results = await semantic_search(
        query=query,
        user_id=str(user_id),
        course_id=course_id,
        top_k=min(max(limit, 1), 10),
        db=db,
    )
    return [
        {
            "course_id": result.course_id,
            "video_id": result.video_id,
            "video_title": result.video_title,
            "section_heading": result.section_heading,
            "text": result.text,
            "similarity_score": result.similarity_score,
            "timestamp_url": result.timestamp_url,
        }
        for result in results
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

    response = await client.chat.completions.create(
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
    )
    answer = response.choices[0].message.content or ""
    return {"answer": answer.strip(), "sources": sources}
