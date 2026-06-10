import os
from collections.abc import AsyncGenerator, Generator

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://courseflow:password@localhost:5432/courseflow",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test_secret_key_for_phase1")
os.environ.setdefault("ENVIRONMENT", "test")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Generator[None, None, None]:
    from app.workers.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def clean_state(migrated_database: None) -> AsyncGenerator[None, None]:
    from app.db.session import AsyncSessionLocal
    from app.core.config import settings

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                TRUNCATE TABLE
                    concept_review_events,
                    concept_cards,
                    quiz_results,
                    note_chunks,
                    notes,
                    transcripts,
                    videos,
                    courses,
                    users
                RESTART IDENTITY CASCADE
                """
            )
        )
        await session.commit()

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()
    yield


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client
