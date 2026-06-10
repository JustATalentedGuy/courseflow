import asyncio
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.chunk import NoteChunk
from app.models.course import Course
from app.models.notes import Notes
from app.models.quiz import QuizResult
from app.models.user import User
from app.models.video import Video
from app.services.embedder import embed_texts
from app.services.quiz.agent import (
    build_quiz_graph,
    evaluate_answer_llm,
    select_next_concept,
    should_continue,
)
from app.services.quiz.session import SESSION_TTL_SECONDS, start_quiz_session
from app.services.quiz.state import initialise_quiz_state


def test_quiz_state_initialises_correctly():
    state = initialise_quiz_state(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        mode="quick_drill",
        all_concepts=["recursion", "base case"],
    )

    assert state["session_complete"] is False
    assert state["questions_asked"] == 0
    assert state["current_difficulty"] == "medium"
    assert len(state["all_concepts"]) > 0
    assert build_quiz_graph() is not None


def test_select_concept_prioritises_weak_concepts():
    state = initialise_quiz_state(
        "video",
        "user",
        all_concepts=["base case", "recursion"],
        weak_concepts=[
            {
                "concept": "recursion",
                "times_asked": 1,
                "times_correct": 0,
                "last_score": 0.3,
            }
        ],
    )

    next_state = select_next_concept(state)

    assert next_state["current_concept"] == "recursion"


def test_evaluate_answer_scores_correct_answer_highly():
    result = evaluate_answer_llm(
        "binary search",
        "Binary search halves the search space by comparing the target with the middle element.",
        "It divides the sorted array in half each time to find the element.",
    )

    assert result["score"] >= 0.7


def test_evaluate_answer_scores_wrong_answer_low():
    result = evaluate_answer_llm(
        "binary search",
        "Binary search halves the search space by comparing the target with the middle element.",
        "It checks every element one by one.",
    )

    assert result["score"] <= 0.4


def test_should_continue_probes_deeper_on_low_score():
    state = initialise_quiz_state("video", "user", all_concepts=["recursion"])
    state["current_concept"] = "recursion"
    state["answer_score"] = 0.3

    assert should_continue(state) == "probe_deeper"


def test_should_continue_ends_session_at_max_questions():
    state = initialise_quiz_state("video", "user", all_concepts=["recursion"])
    state["questions_asked"] = state["max_questions"]

    assert should_continue(state) == "end_session"


@pytest.mark.asyncio
async def test_session_stored_in_redis(db_session):
    user, course, video = await create_quizable_video(db_session, "quiz-redis@example.com")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    state = await start_quiz_session(db_session, redis, user.id, video.id, "quick_drill")
    ttl = await redis.ttl(f"quiz:session:{state['session_id']}")
    await redis.aclose()

    assert 0 < ttl <= SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_completed_session_written_to_postgres(client, db_session, monkeypatch):
    token, user, course, video = await create_api_quizable_video(
        client,
        db_session,
        "quiz-complete@example.com",
    )
    monkeypatch.setattr(
        "app.services.quiz.session.evaluate_answer_llm",
        lambda concept, context, answer: {
            "score": 0.8,
            "feedback": "Correct.",
            "key_gap": None,
        },
    )

    start = await client.post(
        "/api/v1/quiz/start",
        json={"video_id": str(video.id), "mode": "full_review"},
        headers={"Authorization": f"Bearer {token}"},
    )
    answer = await client.post(
        "/api/v1/quiz/answer",
        json={"session_id": start.json()["session_id"], "answer": "A correct explanation."},
        headers={"Authorization": f"Bearer {token}"},
    )

    result = await db_session.scalar(
        select(QuizResult).where(QuizResult.session_id == start.json()["session_id"])
    )
    assert answer.status_code == 200
    assert answer.json()["session_complete"] is True
    assert result is not None
    assert result.average_score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_quiz_result_is_user_scoped(client, db_session):
    token_a, user_a, course, video = await create_api_quizable_video(
        client,
        db_session,
        "quiz-owner@example.com",
    )
    token_b = await register_and_login(client, "quiz-other@example.com")
    db_session.add(
        QuizResult(
            video_id=video.id,
            user_id=user_a.id,
            session_id="scoped-session",
            mode="quick_drill",
            total_questions=1,
            average_score=0.4,
            weak_concepts=[{"concept": "recursion", "last_score": 0.4}],
            results_json=[{"concept": "recursion", "score": 0.4}],
            completed_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    allowed = await client.get(
        f"/api/v1/quiz/sessions/{video.id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    denied = await client.get(
        f"/api/v1/quiz/sessions/{video.id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert allowed.status_code == 200
    assert len(allowed.json()) == 1
    assert denied.status_code in {403, 404}


@pytest.mark.asyncio
async def test_abandoned_session_redis_key_expires(client, db_session):
    token, user, course, video = await create_api_quizable_video(
        client,
        db_session,
        "quiz-expire@example.com",
    )
    start = await client.post(
        "/api/v1/quiz/start",
        json={"video_id": str(video.id), "mode": "quick_drill"},
        headers={"Authorization": f"Bearer {token}"},
    )
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.expire(f"quiz:session:{start.json()['session_id']}", 1)
    await asyncio.sleep(1.2)
    await redis.aclose()

    response = await client.post(
        "/api/v1/quiz/answer",
        json={"session_id": start.json()["session_id"], "answer": "Late answer"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_weak_concepts_are_aggregated(client, db_session):
    token, user, course, video = await create_api_quizable_video(
        client,
        db_session,
        "quiz-weak@example.com",
    )
    db_session.add(
        QuizResult(
            video_id=video.id,
            user_id=user.id,
            session_id="weak-session",
            mode="quick_drill",
            total_questions=2,
            average_score=0.45,
            weak_concepts=[{"concept": "recursion", "last_score": 0.3}],
            results_json=[
                {"concept": "recursion", "score": 0.3},
                {"concept": "base case", "score": 0.6},
            ],
            completed_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/v1/quiz/weak-concepts",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()[0]["concept"] == "recursion"
    assert response.json()[0]["attempts"] == 1


async def register_and_login(client, email: str) -> str:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    return response.json()["access_token"]


async def create_api_quizable_video(client, db_session, email: str):
    token = await register_and_login(client, email)
    user = await db_session.scalar(select(User).where(User.email == email))
    course, video = await create_course_video_notes(db_session, user)
    return token, user, course, video


async def create_quizable_video(db_session, email: str):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course, video = await create_course_video_notes(db_session, user)
    return user, course, video


async def create_course_video_notes(db_session, user: User):
    slug = user.email.split("@")[0]
    course = Course(
        user_id=user.id,
        title="Quiz Course",
        playlist_url=f"https://youtu.be/{slug}",
        playlist_id=f"single:{slug}",
        video_count=1,
        status="completed",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id=f"{slug}-video",
        title="Algorithms",
        position=0,
        status="completed",
    )
    db_session.add(video)
    await db_session.flush()
    notes = Notes(
        video_id=video.id,
        course_id=course.id,
        user_id=user.id,
        source_model="groq/llama-3.3-70b",
        full_markdown=(
            "## Binary Search\n\nBinary search halves a sorted search space using the middle element."
        ),
        summary="This lesson explains binary search.",
        sections_json=[
            {
                "heading": "Binary Search",
                "level": 2,
                "content": "Binary search halves a sorted search space using the middle element.",
                "concepts": ["binary search"],
            }
        ],
        concepts_json=["binary search"],
        has_images=False,
        image_count=0,
        token_count=20,
        generated_at=datetime.now(UTC),
    )
    db_session.add(notes)
    db_session.add(
        NoteChunk(
            video_id=video.id,
            course_id=course.id,
            user_id=user.id,
            text="Binary search halves the sorted search space after comparing the middle element.",
            start_seconds=10,
            end_seconds=30,
            section_heading="Binary Search",
            chunk_index=0,
            embedding=embed_texts(["binary search"])[0],
        )
    )
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(video)
    return course, video
