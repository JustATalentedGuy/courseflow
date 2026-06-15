from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.models.course import Course
from app.models.user import User
from app.models.video import Video
from app.services.mcp_service import (
    ask_courses_for_mcp,
    list_courses_for_mcp,
    search_courses_for_mcp,
)


@pytest.mark.asyncio
async def test_list_courses_is_user_scoped(db_session):
    owner = User(email="mcp-owner@example.com", hashed_password=hash_password("password"))
    other = User(email="mcp-other@example.com", hashed_password=hash_password("password"))
    db_session.add_all([owner, other])
    await db_session.flush()
    owned_course = Course(
        user_id=owner.id,
        title="Owned course",
        playlist_url="https://youtube.com/playlist?list=owned",
        playlist_id="owned",
        video_count=2,
        status="partial",
    )
    other_course = Course(
        user_id=other.id,
        title="Other course",
        playlist_url="https://youtube.com/playlist?list=other",
        playlist_id="other",
        video_count=1,
        status="completed",
    )
    db_session.add_all([owned_course, other_course])
    await db_session.flush()
    db_session.add_all(
        [
            Video(
                course_id=owned_course.id,
                user_id=owner.id,
                youtube_video_id="owned-1",
                title="Owned 1",
                position=1,
                status="completed",
            ),
            Video(
                course_id=owned_course.id,
                user_id=owner.id,
                youtube_video_id="owned-2",
                title="Owned 2",
                position=2,
                status="pending",
            ),
        ]
    )
    await db_session.commit()

    courses = await list_courses_for_mcp(db_session, owner.id)

    assert [course["title"] for course in courses] == ["Owned course"]
    assert courses[0]["completed_videos"] == 1


@pytest.mark.asyncio
async def test_search_rejects_another_users_course(db_session):
    owner = User(email="search-owner@example.com", hashed_password=hash_password("password"))
    other = User(email="search-other@example.com", hashed_password=hash_password("password"))
    db_session.add_all([owner, other])
    await db_session.flush()
    course = Course(
        id=uuid4(),
        user_id=other.id,
        title="Private",
        playlist_url="https://youtube.com/playlist?list=private",
        playlist_id="private",
        video_count=0,
        status="completed",
    )
    db_session.add(course)
    await db_session.commit()

    with pytest.raises(ValueError, match="does not belong"):
        await search_courses_for_mcp(
            db_session,
            owner.id,
            "replication",
            course_id=str(course.id),
        )


@pytest.mark.asyncio
async def test_ask_courses_returns_grounded_answer(db_session, monkeypatch):
    source = {
        "course_id": str(uuid4()),
        "video_id": str(uuid4()),
        "video_title": "Replication",
        "section_heading": "Quorums",
        "text": "A write quorum and read quorum should overlap.",
        "similarity_score": 0.91,
        "timestamp_url": "https://youtube.com/watch?v=test&t=42s",
    }

    async def fake_search(*args, **kwargs):
        return [source]

    class FakeCompletions:
        async def create(self, **kwargs):
            assert "[1] Replication - Quorums" in kwargs["messages"][1]["content"]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Quorums overlap [1]."))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr("app.services.mcp_service.search_courses_for_mcp", fake_search)

    result = await ask_courses_for_mcp(
        db_session,
        uuid4(),
        "How do quorums work?",
        groq_client=fake_client,
    )

    assert result["answer"] == "Quorums overlap [1]."
    assert result["sources"] == [source]
