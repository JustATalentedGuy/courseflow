from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.core.security import hash_password
from app.models.chunk import NoteChunk
from app.models.course import Course
from app.models.user import User
from app.models.video import Video
from app.schemas.notes import NotesSection, VideoNotes
from app.services.embedder import (
    EMBEDDING_DIM,
    embed_and_store_notes,
    embed_texts,
    get_embedding_model,
)
from app.services.chunker import chunk_notes_for_embedding
from app.services.notes_service import validate_and_store_notes
from app.services.search import semantic_search


def test_embedding_model_loads_without_error():
    model = get_embedding_model()

    assert model is not None


def test_embed_texts_returns_correct_dimension():
    vectors = embed_texts(["hello world"])

    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIM


def test_embed_texts_rejects_empty_strings():
    with pytest.raises(ValueError):
        embed_texts([""])


def test_embedding_chunks_stay_within_model_limit():
    video = Video(
        id="00000000-0000-0000-0000-000000000001",
        course_id="00000000-0000-0000-0000-000000000002",
        user_id="00000000-0000-0000-0000-000000000003",
        youtube_video_id="long-section",
        title="Long Section",
        position=0,
    )
    notes = notes_for_video(
        video,
        [("Long Topic", " ".join(f"token{index}" for index in range(1200)))],
    )

    chunks = chunk_notes_for_embedding(notes)

    assert len(chunks) >= 3
    assert all(len(chunk.text.split()) <= 500 for chunk in chunks)


@pytest.mark.asyncio
async def test_embed_and_store_creates_chunks_in_db(db_session):
    user, course, video = await create_user_course_video(db_session, "phase5-embed@example.com")
    notes = notes_for_video(
        video,
        [
            ("Binary Search Trees", "Binary search trees keep ordered nodes for efficient lookup."),
            ("Insertion", "BST insertion compares keys and walks left or right before linking a new node."),
            ("Traversal", "In-order traversal visits tree nodes in sorted order."),
        ],
    )
    await validate_and_store_notes(notes, db_session)

    records = await embed_and_store_notes(notes, video, db_session)
    stored = (await db_session.scalars(select(NoteChunk).where(NoteChunk.video_id == video.id))).all()

    assert len(records) == 3
    assert len(stored) == 3
    assert all(chunk.user_id == user.id for chunk in stored)
    assert all(chunk.embedding is not None and len(chunk.embedding) == EMBEDDING_DIM for chunk in stored)


@pytest.mark.asyncio
async def test_semantic_search_returns_relevant_results(db_session):
    user, course, video = await create_user_course_video(db_session, "phase5-relevant@example.com")
    notes = notes_for_video(
        video,
        [
            (
                "Binary Search Trees",
                "Binary search trees support a BST insertion algorithm by comparing keys at each node.",
            ),
            ("Sorting", "Merge sort divides arrays and combines sorted halves."),
        ],
    )
    await validate_and_store_notes(notes, db_session)
    await embed_and_store_notes(notes, video, db_session)

    results = await semantic_search(
        query="BST insertion algorithm",
        user_id=str(user.id),
        course_id=None,
        top_k=5,
        db=db_session,
    )

    assert results
    assert "binary search" in results[0].text.lower() or "tree" in results[0].text.lower()
    assert results[0].similarity_score > 0.5


@pytest.mark.asyncio
async def test_semantic_search_is_user_scoped(db_session):
    owner, course, video = await create_user_course_video(db_session, "phase5-owner@example.com")
    outsider, _, _ = await create_user_course_video(db_session, "phase5-outsider@example.com")
    notes = notes_for_video(video, [("Machine Learning", "Machine learning models learn patterns from data.")])
    await validate_and_store_notes(notes, db_session)
    await embed_and_store_notes(notes, video, db_session)

    results = await semantic_search(
        query="machine learning",
        user_id=str(outsider.id),
        course_id=None,
        top_k=5,
        db=db_session,
    )

    assert owner.id != outsider.id
    assert results == []


@pytest.mark.asyncio
async def test_semantic_search_empty_query_returns_empty(db_session):
    user, _, _ = await create_user_course_video(db_session, "phase5-empty@example.com")

    results = await semantic_search(query="", user_id=str(user.id), course_id=None, top_k=5, db=db_session)

    assert results == []


@pytest.mark.asyncio
async def test_timestamp_url_format(db_session):
    user, course, video = await create_user_course_video(db_session, "phase5-timestamp@example.com")
    db_session.add(
        NoteChunk(
            video_id=video.id,
            course_id=course.id,
            user_id=user.id,
            text="Graph traversal uses queues for breadth first search.",
            start_seconds=125.5,
            end_seconds=140,
            section_heading="Traversal",
            chunk_index=0,
            embedding=embed_texts(["Graph traversal uses queues for breadth first search."])[0],
        )
    )
    await db_session.commit()

    results = await semantic_search(
        query="breadth first traversal",
        user_id=str(user.id),
        course_id=None,
        top_k=1,
        db=db_session,
    )

    assert results[0].timestamp_url.endswith("&t=125s")


@pytest.mark.asyncio
async def test_search_course_filter_works(db_session):
    user, course_a, video_a = await create_user_course_video(db_session, "phase5-filter@example.com", "Course A")
    course_b = Course(
        user_id=user.id,
        title="Course B",
        playlist_url="https://youtu.be/course-b",
        playlist_id="single:course-b",
        video_count=1,
        status="completed",
    )
    db_session.add(course_b)
    await db_session.flush()
    video_b = Video(
        course_id=course_b.id,
        user_id=user.id,
        youtube_video_id="course-b",
        title="Video B",
        position=0,
        status="completed",
    )
    db_session.add(video_b)
    await db_session.commit()
    await db_session.refresh(video_b)

    for video, topic in [(video_a, "Binary Trees"), (video_b, "Binary Heaps")]:
        notes = notes_for_video(video, [(topic, f"{topic} explain binary data structure operations.")])
        await validate_and_store_notes(notes, db_session)
        await embed_and_store_notes(notes, video, db_session)

    results = await semantic_search(
        query="binary data structure",
        user_id=str(user.id),
        course_id=str(course_a.id),
        top_k=10,
        db=db_session,
    )

    assert results
    assert all(result.course_id == str(course_a.id) for result in results)


@pytest.mark.asyncio
async def test_hnsw_index_exists_after_embedding(db_session):
    user, course, video = await create_user_course_video(db_session, "phase5-index@example.com")
    notes = notes_for_video(video, [("Indexing", "Vector indexes make semantic search fast.")])
    await validate_and_store_notes(notes, db_session)

    await embed_and_store_notes(notes, video, db_session)
    index_name = await db_session.scalar(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'note_chunks'
              AND indexname = 'note_chunks_embedding_idx'
            """
        )
    )

    assert index_name == "note_chunks_embedding_idx"


@pytest.mark.asyncio
async def test_search_api_and_suggestions(client, db_session):
    token = await register_and_login(client, "phase5-api@example.com")
    user = await db_session.scalar(select(User).where(User.email == "phase5-api@example.com"))
    course, video = await create_course_video_for_user(db_session, user)
    notes = notes_for_video(video, [("Dynamic Programming", "Dynamic programming reuses memoized answers.")])
    await validate_and_store_notes(notes, db_session)
    await embed_and_store_notes(notes, video, db_session)

    search_response = await client.post(
        "/api/v1/search",
        json={"query": "memoized dynamic programming", "top_k": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    suggest_response = await client.get(
        "/api/v1/search/suggest?q=Dynamic",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert search_response.status_code == 200
    assert search_response.json()[0]["section_heading"] == "Dynamic Programming"
    assert suggest_response.status_code == 200
    assert suggest_response.json() == ["Dynamic Programming"]


async def register_and_login(client, email: str) -> str:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    return response.json()["access_token"]


async def create_user_course_video(db_session, email: str, course_title: str = "Phase 5 Course"):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course, video = await create_course_video_for_user(db_session, user, course_title)
    return user, course, video


async def create_course_video_for_user(db_session, user: User, course_title: str = "Phase 5 Course"):
    course = Course(
        user_id=user.id,
        title=course_title,
        playlist_url=f"https://youtu.be/{course_title.lower().replace(' ', '-')}",
        playlist_id=f"single:{course_title.lower().replace(' ', '-')}",
        video_count=1,
        status="completed",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id=f"{course_title.lower().replace(' ', '-')}-video",
        title=f"{course_title} Video",
        position=0,
        status="completed",
    )
    db_session.add(video)
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(video)
    return course, video


def notes_for_video(video: Video, sections: list[tuple[str, str]]) -> VideoNotes:
    section_models = [
        NotesSection(
            heading=heading,
            level=2,
            content=f"{content}\n\nKey Concepts:\n- {heading.lower()}",
            concepts=[heading.lower()],
        )
        for heading, content in sections
    ]
    section_markdown = "\n\n".join(
        f"## {section.heading}\n\n{section.content}" for section in section_models
    )
    full_markdown = (
        f"# {video.title}\n\n"
        "## Summary\n\n"
        "This video explains searchable technical concepts with structured notes.\n\n"
        f"{section_markdown}"
    )
    return VideoNotes(
        video_id=str(video.id),
        course_id=str(video.course_id),
        title=video.title,
        source_model="groq/llama-3.3-70b",
        sections=section_models,
        summary="This video explains searchable technical concepts with structured notes.",
        full_markdown=full_markdown,
        has_images=False,
        image_count=0,
        generated_at=datetime.now(UTC).isoformat(),
        token_count=100,
    )
