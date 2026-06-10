import asyncpg
import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.models.course import Course
from app.models.user import User


def asyncpg_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.mark.asyncio
async def test_register_creates_user(client, db_session):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "learner@example.com", "password": "password123"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"]
    assert body["email"] == "learner@example.com"

    user = await db_session.scalar(select(User).where(User.email == "learner@example.com"))
    assert user is not None
    assert user.hashed_password != "password123"


@pytest.mark.asyncio
async def test_login_returns_tokens(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "learner@example.com", "password": "password123"},
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "learner@example.com", "password": "password123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "learner@example.com", "password": "password123"},
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "learner@example.com", "password": "not-the-password"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_without_token_returns_401(client):
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_issues_new_access_token(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "learner@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "learner@example.com", "password": "password123"},
    )
    tokens = login.json()

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json()["access_token"] != tokens["access_token"]


@pytest.mark.asyncio
async def test_user_isolation_courses(client, db_session):
    from app.models.video import Video

    await client.post(
        "/api/v1/auth/register",
        json={"email": "user-a@example.com", "password": "password123"},
    )
    await client.post(
        "/api/v1/auth/register",
        json={"email": "user-b@example.com", "password": "password123"},
    )
    login_a = await client.post(
        "/api/v1/auth/login",
        json={"email": "user-a@example.com", "password": "password123"},
    )
    login_b = await client.post(
        "/api/v1/auth/login",
        json={"email": "user-b@example.com", "password": "password123"},
    )

    user_a = await db_session.scalar(select(User).where(User.email == "user-a@example.com"))
    course = Course(
        user_id=user_a.id,
        title="Algorithms",
        playlist_url="https://www.youtube.com/playlist?list=PL123",
        playlist_id="PL123",
        video_count=1,
    )
    db_session.add(course)
    await db_session.flush()
    db_session.add(
        Video(
            course_id=course.id,
            user_id=user_a.id,
            youtube_video_id="video-a",
            title="Algorithms Intro",
            position=0,
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {login_b.json()['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json() == []
    courses_in_db = (await db_session.scalars(select(Course))).all()
    assert len(courses_in_db) == 1


@pytest.mark.asyncio
async def test_db_connection_async():
    connection = await asyncpg.connect(asyncpg_url())
    try:
        value = await connection.fetchval("SELECT 1")
    finally:
        await connection.close()

    assert value == 1


@pytest.mark.asyncio
async def test_pgvector_extension_loaded(db_session):
    result = await db_session.scalar(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )

    assert result == "vector"
