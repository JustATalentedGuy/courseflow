import io
import zipfile
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import ManualChunkIndexError, NotesValidationError
from app.core.security import hash_password
from app.models.course import Course
from app.models.notes import Notes
from app.models.srs import ConceptCard
from app.models.transcript import Transcript
from app.models.user import User
from app.models.video import Video
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.export import (
    export_anki_deck,
    export_notes_markdown,
    export_notes_pdf,
)
from app.services.manual_assist import generate_manual_prompt, submit_manual_notes
from app.services.notes_generator import NOTES_SYSTEM_PROMPT
from app.services.transcript import store_transcript

VALID_MARKDOWN = """
## Dynamic Programming

Dynamic programming reuses the results of overlapping subproblems so repeated work can be
avoided. Memoization stores results on demand, while tabulation builds a table from smaller
states toward the final answer. The Fibonacci sequence is a concrete example because naive
recursion computes the same values many times.

Key Concepts:
- dynamic programming
- memoization
- tabulation
- overlapping subproblems
""".strip()


@pytest.fixture
async def redis_client():
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_manual_prompt_for_single_chunk_video(db_session, redis_client):
    user, course, video = await create_video_with_transcript(
        db_session,
        "manual-prompt@example.com",
        short_transcript("manual-prompt"),
    )

    response = await generate_manual_prompt(video.id, 0, user.id, db_session, redis_client)

    assert response.total_chunks == 1
    assert NOTES_SYSTEM_PROMPT[:50] in response.prompt_text
    assert response.estimated_tokens > 0
    assert response.video_title == video.title


@pytest.mark.asyncio
async def test_manual_prompt_extracts_missing_transcript(
    db_session,
    redis_client,
    monkeypatch,
):
    user = User(
        email="manual-missing-transcript@example.com",
        hashed_password=hash_password("password123"),
    )
    db_session.add(user)
    await db_session.flush()
    course, video = await create_course_video(db_session, user)
    extracted = short_transcript(video.youtube_video_id)

    async def fake_extract(video_obj, db):
        return extracted

    monkeypatch.setattr("app.services.manual_assist.extract_transcript", fake_extract)

    response = await generate_manual_prompt(
        video.id,
        0,
        user.id,
        db_session,
        redis_client,
    )

    assert response.total_chunks == 1
    assert await db_session.scalar(
        select(Transcript).where(Transcript.video_id == video.id)
    ) is not None


@pytest.mark.asyncio
async def test_manual_prompt_chunk_index_out_of_range(db_session, redis_client):
    user, course, video = await create_video_with_transcript(
        db_session,
        "manual-range@example.com",
        long_transcript("manual-range"),
    )

    with pytest.raises(ManualChunkIndexError):
        await generate_manual_prompt(video.id, 99, user.id, db_session, redis_client)


@pytest.mark.asyncio
async def test_submit_manual_notes_stores_notes(db_session, redis_client):
    user, course, video = await create_video_with_transcript(
        db_session,
        "manual-store@example.com",
        short_transcript("manual-store"),
    )

    result = await submit_manual_notes(
        video.id,
        0,
        VALID_MARKDOWN,
        user.id,
        db_session,
        redis_client,
    )
    notes = await db_session.scalar(select(Notes).where(Notes.video_id == video.id))

    assert result.status == "complete"
    assert result.notes_id == str(notes.id)
    assert notes.source_model == "manual/user"
    assert video.status == "completed"


@pytest.mark.asyncio
async def test_submit_invalid_manual_notes_rejected(db_session, redis_client):
    user, course, video = await create_video_with_transcript(
        db_session,
        "manual-invalid@example.com",
        short_transcript("manual-invalid"),
    )

    with pytest.raises(NotesValidationError):
        await submit_manual_notes(
            video.id,
            0,
            "just some text with no structure",
            user.id,
            db_session,
            redis_client,
        )


@pytest.mark.asyncio
async def test_multi_chunk_returns_partial_until_all_submitted(db_session, redis_client):
    user, course, video = await create_video_with_transcript(
        db_session,
        "manual-multi@example.com",
        long_transcript("manual-multi"),
    )
    prompt = await generate_manual_prompt(video.id, 0, user.id, db_session, redis_client)
    assert prompt.total_chunks > 1

    for index in range(prompt.total_chunks - 1):
        result = await submit_manual_notes(
            video.id,
            index,
            chunk_markdown(index),
            user.id,
            db_session,
            redis_client,
        )
        assert result.status == "partial"
        if index == 0:
            replacement = await submit_manual_notes(
                video.id,
                0,
                chunk_markdown(0).replace("## Chunk 1", "## Replacement Chunk"),
                user.id,
                db_session,
                redis_client,
            )
            assert replacement.status == "partial"

    final = await submit_manual_notes(
        video.id,
        prompt.total_chunks - 1,
        chunk_markdown(prompt.total_chunks - 1),
        user.id,
        db_session,
        redis_client,
    )

    assert final.status == "complete"
    assert final.received_chunks == list(range(prompt.total_chunks))
    stored = await db_session.scalar(select(Notes).where(Notes.video_id == video.id))
    assert "## Replacement Chunk" in stored.full_markdown


@pytest.mark.asyncio
async def test_pdf_export_returns_valid_pdf(db_session):
    user, course, video = await create_video_with_notes(db_session, "pdf-export@example.com")

    pdf_bytes = await export_notes_pdf(video.id, user.id, db_session)

    assert pdf_bytes[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_markdown_export_matches_stored_notes(db_session):
    user, course, video = await create_video_with_notes(db_session, "md-export@example.com")
    stored_notes = await db_session.scalar(select(Notes).where(Notes.video_id == video.id))

    markdown_bytes = await export_notes_markdown(video.id, user.id, db_session)

    assert markdown_bytes.decode() == stored_notes.full_markdown


@pytest.mark.asyncio
async def test_anki_export_returns_apkg(db_session):
    user, course, video = await create_video_with_notes(db_session, "anki-export@example.com")
    db_session.add(
        ConceptCard(
            user_id=user.id,
            video_id=video.id,
            concept="dynamic programming",
            ease_factor=2.5,
            interval_days=1,
            repetitions=0,
            next_review_date=datetime.now(UTC).date(),
        )
    )
    await db_session.commit()

    apkg_bytes = await export_anki_deck(course.id, user.id, db_session)
    archive = zipfile.ZipFile(io.BytesIO(apkg_bytes))

    assert "collection.anki2" in archive.namelist()


@pytest.mark.asyncio
async def test_pdf_presigned_urls_refreshed_before_render(db_session, monkeypatch):
    user, course, video = await create_video_with_notes(
        db_session,
        "pdf-images@example.com",
        image_url="minio://frames/video/frame.jpg",
    )
    calls: list[tuple[str, int]] = []

    async def fake_presigned_url(uri: str, expires_seconds: int = 3600) -> str:
        calls.append((uri, expires_seconds))
        return "https://storage.test/fresh-frame.jpg"

    def fake_render(document: str) -> bytes:
        assert "https://storage.test/fresh-frame.jpg" in document
        return b"%PDF-fake"

    monkeypatch.setattr("app.services.export.generate_presigned_url", fake_presigned_url)
    monkeypatch.setattr("app.services.export._render_pdf_document", fake_render)

    pdf_bytes = await export_notes_pdf(video.id, user.id, db_session)

    assert pdf_bytes.startswith(b"%PDF")
    assert calls == [("minio://frames/video/frame.jpg", 3600)]


@pytest.mark.asyncio
async def test_phase9_routes(client, db_session):
    token, user, course, video = await create_api_video(client, db_session, "phase9-routes@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    prompt = await client.get(
        f"/api/v1/videos/{video.id}/manual-prompt?chunk=0",
        headers=headers,
    )
    submitted = await client.post(
        f"/api/v1/videos/{video.id}/manual-notes",
        json={"chunk_index": 0, "response": VALID_MARKDOWN},
        headers=headers,
    )
    out_of_range = await client.get(
        f"/api/v1/videos/{video.id}/manual-prompt?chunk=99",
        headers=headers,
    )
    markdown = await client.get(
        f"/api/v1/videos/{video.id}/export/markdown",
        headers=headers,
    )
    pdf = await client.get(
        f"/api/v1/videos/{video.id}/export/pdf",
        headers=headers,
    )
    db_session.add(
        ConceptCard(
            user_id=user.id,
            video_id=video.id,
            concept="manual notes",
            ease_factor=2.5,
            interval_days=1,
            repetitions=0,
            next_review_date=datetime.now(UTC).date(),
        )
    )
    await db_session.commit()
    anki = await client.get(
        f"/api/v1/courses/{course.id}/export/anki",
        headers=headers,
    )

    assert prompt.status_code == 200
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "complete"
    assert out_of_range.status_code == 422
    assert markdown.status_code == 200
    assert markdown.content.decode().startswith("# Phase 9 Video")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert anki.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(anki.content))


async def create_api_video(client, db_session, email: str):
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    user = await db_session.scalar(select(User).where(User.email == email))
    course, video = await create_course_video(db_session, user)
    await store_transcript(short_transcript(video.youtube_video_id), video, db_session)
    return login.json()["access_token"], user, course, video


async def create_video_with_transcript(db_session, email: str, transcript: NormalisedTranscript):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course, video = await create_course_video(db_session, user)
    await store_transcript(transcript, video, db_session)
    await db_session.refresh(video)
    return user, course, video


async def create_video_with_notes(db_session, email: str, image_url: str | None = None):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course, video = await create_course_video(db_session, user, status="completed")
    image = f"\n\n![Lecture frame]({image_url})" if image_url else ""
    full_markdown = (
        "# Phase 9 Video\n\n"
        "## Summary\n\n"
        "This lesson explains dynamic programming and memoization.\n\n"
        "## Dynamic Programming\n\n"
        "Dynamic programming stores overlapping subproblem results and reuses them. "
        "Memoization and tabulation avoid repeated work in recursive algorithms."
        f"{image}\n\n"
        "Key Concepts:\n- dynamic programming\n- memoization\n- tabulation"
    )
    db_session.add(
        Notes(
            video_id=video.id,
            course_id=course.id,
            user_id=user.id,
            source_model="groq/llama-3.3-70b",
            full_markdown=full_markdown,
            summary="This lesson explains dynamic programming and memoization.",
            sections_json=[
                {
                    "heading": "Dynamic Programming",
                    "level": 2,
                    "content": "Dynamic programming stores overlapping subproblem results.",
                    "concepts": ["dynamic programming", "memoization", "tabulation"],
                }
            ],
            concepts_json=["dynamic programming", "memoization", "tabulation"],
            has_images=image_url is not None,
            image_count=1 if image_url else 0,
            token_count=40,
            generated_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    await db_session.refresh(video)
    return user, course, video


async def create_course_video(db_session, user: User, status: str = "pending"):
    course = Course(
        user_id=user.id,
        title="Phase 9 Course",
        playlist_url="https://youtu.be/phase9",
        playlist_id=f"single:{user.email}",
        video_count=1,
        status=status,
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id=f"phase9-{user.id}",
        title="Phase 9 Video",
        position=0,
        status=status,
    )
    db_session.add(video)
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(video)
    return course, video


def short_transcript(video_id: str) -> NormalisedTranscript:
    text = (
        "Dynamic programming stores overlapping subproblem results. "
        "Memoization avoids repeated recursive calls and improves performance."
    )
    segment = TranscriptSegment(start=0, end=30, text=text, speaker=None)
    return NormalisedTranscript(
        video_id=video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=30,
        segments=[segment],
        full_text=text,
        word_count=len(text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def long_transcript(video_id: str) -> NormalisedTranscript:
    sentences = [
        (
            f"Lesson sentence {index} explains dynamic programming state transitions "
            "with memoization examples and careful complexity analysis."
        )
        for index in range(420)
    ]
    text = " ".join(sentences)
    segment = TranscriptSegment(start=0, end=420, text=text, speaker=None)
    return NormalisedTranscript(
        video_id=video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=420,
        segments=[segment],
        full_text=text,
        word_count=len(text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def chunk_markdown(index: int) -> str:
    return (
        f"## Chunk {index + 1}\n\n"
        "This section explains a distinct part of the lecture using enough detail to produce "
        "a useful combined note. It covers state transitions, memoization, examples, complexity, "
        "and practical reasoning without repeating the other chunks.\n\n"
        "Key Concepts:\n"
        f"- chunk concept {index + 1}\n"
        "- state transitions\n"
        "- memoization"
    )
