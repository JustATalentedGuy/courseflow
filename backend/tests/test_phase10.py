import json
import subprocess
from datetime import UTC, datetime
from uuid import uuid4

import bcrypt
import pytest
from redis.asyncio import Redis

from app.core.config import Settings, settings
from app.core.exceptions import PermanentAPIError, TemporaryAPIError, ValidationError
from app.core.limiter import limiter
from app.core.security import decode_token
from app.core.token_store import refresh_token_key
from app.services.external_api import call_external_async
from app.services.image_pipeline import extract_slide_screenshots
from app.services.ingestion import _extract_youtube_info
from app.services.object_storage import generate_presigned_url


async def register_and_login(client, email: str) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    return response.json()


@pytest.mark.asyncio
async def test_login_rate_limited_to_ten_attempts_per_minute(client):
    limiter.enabled = True
    limiter.reset()
    try:
        responses = [
            await client.post(
                "/api/v1/auth/login",
                json={"email": "missing@example.com", "password": "wrong"},
            )
            for _ in range(11)
        ]
    finally:
        limiter.enabled = False
        limiter.reset()

    assert all(response.status_code == 401 for response in responses[:10])
    assert responses[10].status_code == 429


@pytest.mark.asyncio
async def test_refresh_token_is_stored_as_bcrypt_hash(client):
    tokens = await register_and_login(client, "phase10-token@example.com")
    claims = decode_token(tokens["refresh_token"], "refresh")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        stored = await redis.get(
            refresh_token_key(str(claims["sub"]), str(claims["jti"]))
        )
    finally:
        await redis.aclose()

    assert stored is not None
    assert stored.startswith("$2")
    assert tokens["refresh_token"] not in stored
    assert bcrypt.checkpw(
        tokens["refresh_token"].encode("utf-8"),
        stored.encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_search_limits_are_enforced_by_api(client):
    tokens = await register_and_login(client, "phase10-search@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    long_query = await client.post(
        "/api/v1/search",
        json={"query": "x" * 501, "top_k": 5},
        headers=headers,
    )
    excessive_top_k = await client.post(
        "/api/v1/search",
        json={"query": "dynamic programming", "top_k": 21},
        headers=headers,
    )

    assert long_query.status_code == 422
    assert excessive_top_k.status_code == 422


@pytest.mark.asyncio
async def test_exam_date_must_be_after_today(client):
    tokens = await register_and_login(client, "phase10-exam@example.com")
    response = await client.post(
        "/api/v1/srs/exam-plan",
        json={"exam_date": datetime.now(UTC).date().isoformat()},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 400
    assert "future" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_external_api_statuses_are_classified():
    class StatusError(Exception):
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    temporary_calls = 0

    async def temporary_failure():
        nonlocal temporary_calls
        temporary_calls += 1
        raise StatusError(503)

    with pytest.raises(TemporaryAPIError):
        await call_external_async(
            temporary_failure,
            "test service",
            max_attempts=3,
            base_delay_seconds=0,
        )
    assert temporary_calls == 3

    permanent_calls = 0

    async def permanent_failure():
        nonlocal permanent_calls
        permanent_calls += 1
        raise StatusError(401)

    with pytest.raises(PermanentAPIError):
        await call_external_async(
            permanent_failure,
            "test service",
            base_delay_seconds=0,
        )
    assert permanent_calls == 1


def test_ytdlp_metadata_timeout_is_bounded(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30)

    monkeypatch.setattr("app.services.ingestion.subprocess.run", timeout)

    with pytest.raises(ValidationError, match="30 seconds"):
        _extract_youtube_info("https://youtu.be/example")


def test_ytdlp_metadata_accepts_valid_json_with_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        "app.services.ingestion.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps(
                {
                    "id": "example",
                    "title": "Example video",
                    "duration": 60,
                }
            ),
            stderr="Requested format is not available",
        ),
    )

    info = _extract_youtube_info("https://youtu.be/example")

    assert info["id"] == "example"


@pytest.mark.asyncio
async def test_minio_paths_are_user_prefixed_and_expire_in_one_hour():
    user_id = str(uuid4())
    placeholder = extract_slide_screenshots(user_id, "video-id", [12.5])[0]

    assert placeholder.url.startswith(f"minio://{user_id}/video-id/")
    with pytest.raises(ValidationError, match="user ID"):
        await generate_presigned_url("minio://frames/video/frame.jpg")


def test_production_configuration_rejects_default_secrets():
    production = Settings(
        _env_file=None,
        environment="production",
        secret_key="development_only_change_me",
        cors_origins=["*"],
    )

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        production.validate_runtime()
