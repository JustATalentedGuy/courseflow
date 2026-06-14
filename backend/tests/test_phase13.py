import base64
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import DiagramQuotaWaitError
from app.core.security import hash_password
from app.models.course import Course
from app.models.diagram import DiagramAsset
from app.models.notes import Notes
from app.models.transcript import Transcript
from app.models.user import User
from app.models.video import Video
from app.services.diagram_quota import CloudflareQuotaManager
from app.services.diagram_service import (
    discover_diagrams_for_notes,
    materialize_notes_markdown,
    parse_diagram_markers,
)
from app.services.image_provider import CloudflareImageProvider
from app.services.mermaid_renderer import validate_mermaid


def test_marker_parser_preserves_order_duplicates_and_section_context():
    markdown = """
## Hash Indexes
Explain buckets and collisions.
{{DIAGRAM: Hash table collision handling}}
More detail.
{{DIAGRAM: Hash table collision handling}}

## B-Trees
{{DIAGRAM: B-tree node split}}
"""
    markers = parse_diagram_markers(markdown)

    assert [marker.index for marker in markers] == [0, 1, 2]
    assert [marker.caption for marker in markers] == [
        "Hash table collision handling",
        "Hash table collision handling",
        "B-tree node split",
    ]
    assert "Hash Indexes" in markers[0].context
    assert "B-Trees" in markers[2].context


def test_mermaid_validation_rejects_unsafe_and_oversized_sources():
    assert validate_mermaid("flowchart LR\nA[Client] --> B[Server]")
    with pytest.raises(ValueError, match="not allowed"):
        validate_mermaid("flowchart LR\nA --> B\nclick A https://example.com")
    with pytest.raises(ValueError, match="Unsupported"):
        validate_mermaid("pie\n title Usage")


@pytest.mark.asyncio
async def test_cloudflare_provider_uses_multipart_and_normalizes_webp(monkeypatch):
    source = BytesIO()
    Image.new("RGB", (64, 32), "blue").save(source, "PNG")
    encoded = base64.b64encode(source.getvalue()).decode()
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request_kwargs"] = kwargs
            return httpx.Response(
                200,
                json={"result": {"image": encoded}},
                headers={"cf-ray": "test-request-id"},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("app.services.image_provider.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "cloudflare_account_id", "account-id")
    monkeypatch.setattr(settings, "cloudflare_api_token", "api-token")

    generated = await CloudflareImageProvider().generate(
        "A detailed architecture diagram",
        "No decorative text",
    )

    request_kwargs = captured["request_kwargs"]
    assert "json" not in request_kwargs
    assert request_kwargs["files"]["prompt"] == (None, "A detailed architecture diagram")
    assert request_kwargs["files"]["negative_prompt"] == (None, "No decorative text")
    assert request_kwargs["files"]["width"] == (None, "1024")
    assert request_kwargs["files"]["height"] == (None, "768")
    assert generated.content[:4] == b"RIFF"
    assert generated.content[8:12] == b"WEBP"
    assert generated.content_type == "image/webp"
    assert (generated.width, generated.height) == (1024, 768)
    assert generated.request_id == "test-request-id"


@pytest.mark.asyncio
async def test_discovery_preserves_completed_asset_across_matching_note_version(db_session):
    user, course, video, notes = await _diagram_notes(db_session)
    rows = await discover_diagrams_for_notes(db_session, notes, video)
    rows[0].state = "completed"
    rows[0].object_uri = f"minio://{user.id}/{video.id}/diagrams/{rows[0].id}/1.png"
    await db_session.commit()
    original_id = rows[0].id

    notes.content_version = 2
    notes.source_markdown = notes.source_markdown.replace("  ", " ")
    await db_session.commit()
    reconciled = await discover_diagrams_for_notes(db_session, notes, video)

    assert reconciled[0].id == original_id
    assert reconciled[0].note_version == 2
    assert reconciled[0].state == "completed"


@pytest.mark.asyncio
async def test_materialization_replaces_completed_and_pending_markers(db_session, monkeypatch):
    user, course, video, notes = await _diagram_notes(db_session, marker_count=2)
    rows = await discover_diagrams_for_notes(db_session, notes, video)
    rows[0].state = "completed"
    rows[0].object_uri = f"minio://{user.id}/{video.id}/diagrams/{rows[0].id}/1.png"
    rows[0].alt_text = "Hash buckets with collision chains"
    await db_session.commit()
    monkeypatch.setattr(
        "app.services.diagram_service.generate_presigned_url",
        lambda uri: _async_value("https://objects.test/diagram.png"),
    )

    markdown = await materialize_notes_markdown(db_session, notes)

    assert "{{DIAGRAM:" not in markdown
    assert "![Hash buckets with collision chains](https://objects.test/diagram.png)" in markdown
    assert "**Diagram pending:** B-tree node split" in markdown


@pytest.mark.asyncio
async def test_cloudflare_quota_reservations_are_atomic(db_session, monkeypatch):
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    quota = CloudflareQuotaManager(redis)
    monkeypatch.setattr(settings, "cloudflare_daily_neuron_budget", 500)
    monkeypatch.setattr(settings, "cloudflare_image_concurrency", 1)

    first = await quota.reserve(db_session, 400)
    with pytest.raises(DiagramQuotaWaitError, match="busy"):
        await quota.reserve(db_session, 100)
    await quota.release(first)
    reserved = await quota.reserve(db_session, 400)
    await quota.reconcile(reserved)
    with pytest.raises(DiagramQuotaWaitError, match="daily"):
        await quota.reserve(db_session, 101)
    await redis.aclose()


@pytest.mark.asyncio
async def test_course_generate_route_discovers_and_queues_markers(
    client,
    db_session,
    monkeypatch,
):
    token, user = await _api_user(client, db_session, "diagrams@example.com")
    _, course, _, _ = await _diagram_notes(db_session, user=user, marker_count=2)
    queued: list[str] = []
    monkeypatch.setattr(
        "app.api.v1.diagrams.process_diagram_task.apply_async",
        lambda args, **kwargs: queued.append(args[0]),
    )

    response = await client.post(
        f"/api/v1/courses/{course.id}/diagrams/generate",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["discovered"] == 2
    assert response.json()["queued"] == 2
    assert len(queued) == 2


@pytest.mark.asyncio
async def test_diagram_routes_enforce_user_isolation(client, db_session):
    _, owner = await _api_user(client, db_session, "diagram-owner@example.com")
    token, _ = await _api_user(client, db_session, "diagram-other@example.com")
    _, _, video, notes = await _diagram_notes(db_session, user=owner)
    await discover_diagrams_for_notes(db_session, notes, video)

    response = await client.get(
        f"/api/v1/videos/{video.id}/diagrams",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def _async_value(value):
    return value


async def _api_user(client, db_session, email: str):
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    user = await db_session.scalar(select(User).where(User.email == email))
    return login.json()["access_token"], user


async def _diagram_notes(db_session, user=None, marker_count: int = 1):
    if user is None:
        user = User(
            email=f"diagram-{datetime.now(UTC).timestamp()}@example.com",
            hashed_password=hash_password("password123"),
        )
        db_session.add(user)
        await db_session.flush()
    course = Course(
        user_id=user.id,
        title="Systems Design 2.0",
        playlist_url="https://youtube.com/playlist?list=diagrams",
        playlist_id=f"diagrams-{datetime.now(UTC).timestamp()}",
        video_count=1,
        status="completed",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id=f"diagram-video-{datetime.now(UTC).timestamp()}",
        title="Indexes",
        position=1,
        duration_seconds=600,
        status="completed",
    )
    db_session.add(video)
    await db_session.flush()
    transcript = Transcript(
        video_id=video.id,
        user_id=user.id,
        source="youtube_captions",
        language="en",
        duration_seconds=600,
        full_text="Hash indexes map keys into buckets. B-tree nodes split as they fill.",
        word_count=12,
        segments_json=[
            {"start": 0, "end": 5, "text": "Hash indexes map keys into buckets.", "speaker": None},
            {"start": 5, "end": 10, "text": "B-tree nodes split as they fill.", "speaker": None},
        ],
        fetched_at=datetime.now(UTC),
    )
    db_session.add(transcript)
    captions = ["Hash table collision handling", "B-tree node split"][:marker_count]
    source = (
        "# Indexes\n\n## Index Structures\n\n"
        + "\n\n".join(f"{{{{DIAGRAM: {caption}}}}}" for caption in captions)
        + "\n\nKey Concepts:\n- indexes\n- trees\n- hashing"
    )
    notes = Notes(
        video_id=video.id,
        course_id=course.id,
        user_id=user.id,
        source_model="local/test",
        source_markdown=source,
        content_version=1,
        full_markdown=source,
        summary="This lesson explains database indexes.",
        sections_json=[],
        concepts_json=["indexes"],
        has_images=False,
        image_count=0,
        token_count=0,
        generated_at=datetime.now(UTC),
    )
    db_session.add(notes)
    await db_session.commit()
    await db_session.refresh(video, attribute_names=["transcript", "notes", "course"])
    return user, course, video, notes
