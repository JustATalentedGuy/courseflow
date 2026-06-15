import asyncio

from mcp.server.fastmcp import FastMCP
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.mcp_service import (
    ask_courses_for_mcp,
    configured_mcp_user_id,
    list_courses_for_mcp,
    search_courses_for_mcp,
)

mcp = FastMCP(
    "CourseFlow",
    instructions=(
        "Read-only access to one configured CourseFlow user's deployed course library. "
        "Use search_my_courses before making claims about course content."
    ),
)


async def check_database_connection() -> None:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=10)
    except TimeoutError as exc:
        raise RuntimeError(
            "Cannot reach the CourseFlow database. Check that the EC2 instance is running "
            "and that your current public IP is allowed in courseflow-sg on port 5432."
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            "Cannot connect to the CourseFlow database. Check DATABASE_URL, start the EC2 "
            "instance, and verify the courseflow-sg PostgreSQL rule for your current IP."
        ) from exc


@mcp.tool()
async def list_my_courses() -> list[dict[str, object]]:
    """List the configured user's courses and processing completion."""
    user_id = configured_mcp_user_id()
    async with AsyncSessionLocal() as db:
        return await list_courses_for_mcp(db, user_id)


@mcp.tool()
async def search_my_courses(
    query: str,
    course_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Semantically search course notes with lesson and timestamp citations."""
    user_id = configured_mcp_user_id()
    async with AsyncSessionLocal() as db:
        return await search_courses_for_mcp(db, user_id, query, course_id, limit)


@mcp.tool()
async def ask_my_courses(
    question: str,
    course_id: str | None = None,
    max_sources: int = 6,
) -> dict[str, object]:
    """Answer a question only from retrieved CourseFlow notes and return sources."""
    user_id = configured_mcp_user_id()
    async with AsyncSessionLocal() as db:
        return await ask_courses_for_mcp(db, user_id, question, course_id, max_sources)


def main() -> None:
    configured_mcp_user_id()
    asyncio.run(check_database_connection())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
