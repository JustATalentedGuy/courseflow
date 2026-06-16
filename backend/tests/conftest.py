import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession


development_database_url = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://courseflow:password@localhost:5432/courseflow",
)
development_url = make_url(development_database_url)
test_database_url = os.environ.get(
    "TEST_DATABASE_URL",
    development_url.set(database=f"{development_url.database}_test").render_as_string(
        hide_password=False
    ),
)
if make_url(test_database_url).database == development_url.database:
    raise RuntimeError("Tests must not use the development database")
os.environ["DATABASE_URL"] = test_database_url
development_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_parts = urlsplit(development_redis_url)
test_redis_url = os.environ.get(
    "TEST_REDIS_URL",
    urlunsplit(
        (
            redis_parts.scheme,
            redis_parts.netloc,
            "/15",
            redis_parts.query,
            redis_parts.fragment,
        )
    ),
)
if urlsplit(test_redis_url).path == redis_parts.path:
    raise RuntimeError("Tests must not use the development Redis database")
os.environ["REDIS_URL"] = test_redis_url
os.environ.setdefault("SECRET_KEY", "test_secret_key_for_phase1")
os.environ.setdefault("ENVIRONMENT", "test")


async def _ensure_test_database() -> None:
    test_url = make_url(test_database_url)
    admin_url = test_url.set(
        drivername="postgresql",
        database="postgres",
    ).render_as_string(hide_password=False)
    connection = await asyncpg.connect(admin_url)
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            test_url.database,
        )
        if not exists:
            database_name = test_url.database.replace('"', '""')
            await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Generator[None, None, None]:
    from app.workers.celery_app import celery_app

    asyncio.run(_ensure_test_database())
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
                    cloudflare_usage_events,
                    diagram_assets,
                    youtube_edge_jobs,
                    edge_fetcher_tokens,
                    whisper_transcription_chunks,
                    groq_usage_events,
                    note_generation_chunks,
                    groq_batch_jobs,
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
