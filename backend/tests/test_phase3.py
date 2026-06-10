from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.exceptions import NotesValidationError
from app.core.security import hash_password
from app.models.course import Course
from app.models.user import User
from app.models.video import Video
from app.schemas.chunk import TranscriptChunk
from app.schemas.notes import NotesSection, VideoNotes
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.chunker import chunk_transcript_for_notes
from app.services.image_pipeline import (
    ImagePlaceholder,
    inject_placeholder_tokens,
    restore_images_in_notes,
)
from app.services.notes_generator import generate_notes_for_chunk, stitch_chunk_notes
from app.services.notes_service import validate_and_store_notes
from app.services.transcript import store_transcript


@pytest.mark.asyncio
async def test_notes_generated_from_clean_transcript():
    chunk = TranscriptChunk(
        text=(
            "Dynamic programming solves problems by reusing overlapping subproblem results. "
            "A concrete example is computing Fibonacci numbers with a table instead of repeated recursion."
        ),
        start_seconds=0,
        end_seconds=30,
        chunk_index=0,
    )

    markdown = await generate_notes_for_chunk(chunk, 0, 1, None, None)

    assert "##" in markdown
    assert "Key Concepts:" in markdown
    assert markdown.count("**") % 2 == 0


@pytest.mark.asyncio
async def test_multi_chunk_notes_stitched_correctly():
    chunk_notes = [
        "## Recursion\n\nRecursion breaks a problem into smaller calls.\n\nKey Concepts:\n- recursion\n- base case\n- stack",
        "## Recursion\n\nThis duplicate heading should merge cleanly.\n\nKey Concepts:\n- recursion\n- overlap\n- memoization",
        "## Dynamic Programming\n\nMemoization stores repeated subproblem answers.\n\nKey Concepts:\n- memoization\n- table\n- subproblem",
    ]

    stitched = await stitch_chunk_notes(chunk_notes, "Algorithms")

    assert stitched.count("## Recursion") == 1
    assert "## Summary" in stitched
    assert "## Dynamic Programming" in stitched


@pytest.mark.asyncio
async def test_notes_validation_rejects_empty_output(db_session):
    notes = VideoNotes.model_construct(
        video_id="00000000-0000-0000-0000-000000000000",
        course_id="00000000-0000-0000-0000-000000000000",
        title="Empty",
        source_model="groq/llama-3.3-70b",
        sections=[],
        summary="Empty output.",
        full_markdown="",
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=10,
    )

    with pytest.raises(NotesValidationError):
        await validate_and_store_notes(notes, db_session)


@pytest.mark.asyncio
async def test_notes_validation_rejects_no_headings(db_session):
    notes = VideoNotes.model_construct(
        video_id="00000000-0000-0000-0000-000000000000",
        course_id="00000000-0000-0000-0000-000000000000",
        title="No Headings",
        source_model="groq/llama-3.3-70b",
        sections=[NotesSection(heading="Concepts", level=2, content="Body", concepts=["concept"])],
        summary="This has no headings.",
        full_markdown="This markdown has enough content to be longer than one hundred characters, but it deliberately has no heading markers at all.",
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=10,
    )

    with pytest.raises(NotesValidationError):
        await validate_and_store_notes(notes, db_session)


def test_placeholder_token_injection():
    text = "The graph shows a linear relationship"
    placeholders = [ImagePlaceholder(token="{{IMG_001}}", position=10)]

    result = inject_placeholder_tokens(text, placeholders)

    assert "{{IMG_001}}" in result


def test_image_restore_handles_missing_token():
    notes_markdown = "## Linear Models\n\nThe graph shows a linear relationship."
    placeholders = [
        ImagePlaceholder(
            token="{{IMG_001}}",
            position=10,
            url="https://minio.local/frame.jpg",
            description="linear graph",
        )
    ]

    restored = restore_images_in_notes(notes_markdown, placeholders)

    assert "{{IMG_001}}" not in restored
    assert "![linear graph](https://minio.local/frame.jpg)" in restored


def test_image_restore_handles_duplicate_token():
    notes_markdown = "## Graphs\n\n{{IMG_001}}\n\nThe same graph again: {{IMG_001}}"
    placeholders = [
        ImagePlaceholder(
            token="{{IMG_001}}",
            position=10,
            url="https://minio.local/frame.jpg",
            description="graph",
        )
    ]

    restored = restore_images_in_notes(notes_markdown, placeholders)

    assert restored.count("![graph](https://minio.local/frame.jpg)") == 1
    assert "{{IMG_001}}" not in restored


def test_chunk_boundaries_are_at_sentence_ends():
    transcript = long_transcript()

    chunks = chunk_transcript_for_notes(transcript)

    assert len(chunks) > 1
    assert all(chunk.text[-1] in ".!?" for chunk in chunks)


@pytest.mark.asyncio
async def test_notes_stored_in_db_with_correct_user_scope(client, db_session):
    token_a, video = await create_user_course_video_and_transcript(
        client,
        db_session,
        "notes-owner@example.com",
    )
    token_b = await register_and_login(client, "notes-other@example.com")
    notes = valid_video_notes(video)

    record = await validate_and_store_notes(notes, db_session)

    assert record.user_id == video.user_id

    allowed = await client.get(
        f"/api/v1/videos/{video.id}/notes",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    denied = await client.get(
        f"/api/v1/videos/{video.id}/notes",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert allowed.status_code == 200
    assert denied.status_code in {403, 404}


@pytest.mark.asyncio
async def test_video_status_updated_to_completed_after_notes(client, db_session):
    token, video = await create_user_course_video_and_transcript(
        client,
        db_session,
        "status-owner@example.com",
    )

    await validate_and_store_notes(valid_video_notes(video), db_session)

    response = await client.get(
        f"/api/v1/videos/{video.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


async def register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    return response.json()["access_token"]


async def create_user_course_video_and_transcript(client, db_session, email: str):
    token = await register_and_login(client, email)
    user = await db_session.scalar(select(User).where(User.email == email))
    course = Course(
        user_id=user.id,
        title="Notes Course",
        playlist_url="https://youtu.be/notes-video",
        playlist_id="single:notes-video",
        video_count=1,
        status="pending",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id="notes-video",
        title="Notes Video",
        position=0,
        status="pending",
    )
    db_session.add(video)
    await db_session.commit()
    await db_session.refresh(video)
    await store_transcript(short_transcript(video.youtube_video_id), video, db_session)
    await db_session.refresh(video)
    return token, video


def short_transcript(youtube_video_id: str) -> NormalisedTranscript:
    segments = [
        TranscriptSegment(
            start=0,
            end=40,
            text=(
                "Dynamic programming stores overlapping subproblem results. "
                "The Fibonacci example shows how memoization avoids repeated recursive calls. "
                "This technique improves performance by trading memory for fewer computations."
            ),
            speaker=None,
        )
    ]
    full_text = " ".join(segment.text for segment in segments)
    return NormalisedTranscript(
        video_id=youtube_video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=40,
        segments=segments,
        full_text=full_text,
        word_count=len(full_text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def long_transcript() -> NormalisedTranscript:
    sentences = [
        f"Sentence {index} explains a stable concept with enough words for chunking."
        for index in range(260)
    ]
    text = " ".join(sentences)
    segment = TranscriptSegment(start=0, end=260, text=text, speaker=None)
    return NormalisedTranscript(
        video_id="long-video",
        source="youtube_captions",
        language="en",
        duration_seconds=260,
        segments=[segment],
        full_text=text,
        word_count=len(text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )


def valid_video_notes(video: Video) -> VideoNotes:
    full_markdown = (
        "# Notes Video\n\n"
        "## Summary\n\n"
        "This video explains dynamic programming through memoization and overlapping subproblems.\n\n"
        "## Dynamic Programming\n\n"
        "Dynamic programming stores results from **overlapping subproblems** so they can be reused. "
        "The Fibonacci example shows how a table or cache avoids repeated recursive calls and improves performance.\n\n"
        "Key Concepts:\n"
        "- dynamic programming\n"
        "- memoization\n"
        "- overlapping subproblems\n"
    )
    return VideoNotes(
        video_id=str(video.id),
        course_id=str(video.course_id),
        title=video.title,
        source_model="groq/llama-3.3-70b",
        sections=[
            NotesSection(
                heading="Dynamic Programming",
                level=2,
                content=(
                    "Dynamic programming stores results from **overlapping subproblems** so they can be reused.\n\n"
                    "Key Concepts:\n- dynamic programming\n- memoization\n- overlapping subproblems"
                ),
                concepts=["dynamic programming", "memoization", "overlapping subproblems"],
            )
        ],
        summary="This video explains dynamic programming through memoization and overlapping subproblems.",
        full_markdown=full_markdown,
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=50,
    )
