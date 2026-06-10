from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.models.course import Course
from app.models.notes import Notes
from app.models.quiz import QuizResult
from app.models.srs import ConceptCard, ConceptReviewEvent
from app.models.user import User
from app.models.video import Video
from app.services.srs import (
    DAILY_REVIEW_CAPACITY,
    generate_study_plan,
    get_due_cards_today,
    get_srs_stats,
    seed_cards_from_quiz_result,
    sm2_update,
)


def test_sm2_correct_answer_increases_interval():
    card = ConceptCard(ease_factor=2.5, interval_days=6, repetitions=2)

    updated = sm2_update(card, score=0.9)

    assert updated.interval_days > 6
    assert updated.repetitions == 3


def test_sm2_wrong_answer_resets_interval():
    card = ConceptCard(ease_factor=2.5, interval_days=21, repetitions=5)

    updated = sm2_update(card, score=0.2)

    assert updated.interval_days == 1
    assert updated.repetitions == 0


def test_sm2_ease_factor_floor_is_1_3():
    card = ConceptCard(ease_factor=1.4, interval_days=1, repetitions=0)

    for _ in range(5):
        card = sm2_update(card, score=0.0)

    assert card.ease_factor >= 1.3


def test_sm2_next_review_date_is_in_future():
    card = ConceptCard(ease_factor=2.5, interval_days=1, repetitions=0)

    updated = sm2_update(card, score=0.8)

    assert updated.next_review_date >= datetime.now(UTC).date()


@pytest.mark.asyncio
async def test_seed_cards_creates_new_cards_from_quiz(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-seed@example.com")
    result = await create_quiz_result(
        db_session,
        user,
        video,
        [{"concept": f"concept {index}", "score": 0.7} for index in range(5)],
        "seed-five",
    )

    await seed_cards_from_quiz_result(result, db_session)
    await db_session.commit()
    cards = (
        await db_session.scalars(select(ConceptCard).where(ConceptCard.user_id == user.id))
    ).all()
    events = (
        await db_session.scalars(
            select(ConceptReviewEvent).where(ConceptReviewEvent.quiz_result_id == result.id)
        )
    ).all()

    assert len(cards) == 5
    assert len(events) == 5
    assert all(card.next_review_date == datetime.now(UTC).date() + timedelta(days=1) for card in cards)
    assert all(card.repetitions == 0 for card in cards)


@pytest.mark.asyncio
async def test_seed_cards_updates_existing_card(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-update@example.com")
    card = ConceptCard(
        user_id=user.id,
        video_id=video.id,
        concept="recursion",
        ease_factor=2.5,
        interval_days=6,
        repetitions=2,
        next_review_date=datetime.now(UTC).date(),
    )
    db_session.add(card)
    await db_session.commit()
    result = await create_quiz_result(
        db_session,
        user,
        video,
        [{"concept": "recursion", "score": 0.9}],
        "update-existing",
    )

    await seed_cards_from_quiz_result(result, db_session)
    await db_session.commit()
    cards = (
        await db_session.scalars(
            select(ConceptCard).where(
                ConceptCard.user_id == user.id,
                ConceptCard.video_id == video.id,
                ConceptCard.concept == "recursion",
            )
        )
    ).all()

    assert len(cards) == 1
    assert cards[0].interval_days > 6
    assert cards[0].repetitions == 3


@pytest.mark.asyncio
async def test_seed_cards_uses_final_attempt_and_is_idempotent(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-final@example.com")
    result = await create_quiz_result(
        db_session,
        user,
        video,
        [
            {"concept": "binary search", "score": 0.2},
            {"concept": "binary   search", "score": 0.9},
        ],
        "final-attempt",
    )

    await seed_cards_from_quiz_result(result, db_session)
    await seed_cards_from_quiz_result(result, db_session)
    await db_session.commit()
    card = await db_session.scalar(
        select(ConceptCard).where(ConceptCard.user_id == user.id)
    )
    event_count = len(
        (
            await db_session.scalars(
                select(ConceptReviewEvent).where(
                    ConceptReviewEvent.quiz_result_id == result.id
                )
            )
        ).all()
    )

    assert card.last_score == pytest.approx(0.9)
    assert card.repetitions == 0
    assert event_count == 1


@pytest.mark.asyncio
async def test_quiz_completion_automatically_seeds_cards(client, db_session, monkeypatch):
    token, user, course, video = await create_api_video(client, db_session, "srs-quiz@example.com")
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

    response = await client.post(
        "/api/v1/quiz/answer",
        json={"session_id": start.json()["session_id"], "answer": "Correct answer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card = await db_session.scalar(
        select(ConceptCard).where(
            ConceptCard.user_id == user.id,
            ConceptCard.video_id == video.id,
        )
    )

    assert response.status_code == 200
    assert response.json()["session_complete"] is True
    assert card is not None
    assert card.last_score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_get_due_cards_returns_only_due_and_orders_hardest(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-due@example.com")
    today = datetime.now(UTC).date()
    db_session.add_all(
        [
            make_card(user, video, "overdue easy", today - timedelta(days=1), ease=2.5),
            make_card(user, video, "overdue hard", today - timedelta(days=1), ease=1.5),
            make_card(user, video, "future", today + timedelta(days=1), ease=1.3),
        ]
    )
    await db_session.commit()

    due = await get_due_cards_today(str(user.id), db_session)

    assert [card.concept for card in due] == ["overdue hard", "overdue easy"]
    assert all(card.next_review_date <= today for card in due)


@pytest.mark.asyncio
async def test_manual_review_updates_card_and_history(client, db_session):
    token, user, course, video = await create_api_video(client, db_session, "srs-review@example.com")
    card = make_card(user, video, "recursion", datetime.now(UTC).date(), repetitions=2, interval=6)
    db_session.add(card)
    await db_session.commit()
    await db_session.refresh(card)

    response = await client.post(
        "/api/v1/srs/review",
        json={"card_id": str(card.id), "score": 0.9},
        headers={"Authorization": f"Bearer {token}"},
    )
    event = await db_session.scalar(
        select(ConceptReviewEvent).where(ConceptReviewEvent.card_id == card.id)
    )

    assert response.status_code == 200
    assert response.json()["interval_days"] > 6
    assert event is not None
    assert event.source == "manual"


@pytest.mark.asyncio
async def test_card_review_is_user_scoped(client, db_session):
    token_a, user_a, course, video = await create_api_video(client, db_session, "srs-owner@example.com")
    token_b = await register_and_login(client, "srs-other@example.com")
    card = make_card(user_a, video, "ownership", datetime.now(UTC).date())
    db_session.add(card)
    await db_session.commit()
    await db_session.refresh(card)

    response = await client.post(
        "/api/v1/srs/review",
        json={"card_id": str(card.id), "score": 0.8},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code in {403, 404}


@pytest.mark.asyncio
async def test_stats_retention_and_streak(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-stats@example.com")
    card = make_card(user, video, "statistics", datetime.now(UTC).date())
    db_session.add(card)
    await db_session.flush()
    today = datetime.now(UTC).date()
    for offset, score in [(0, 0.9), (1, 0.7), (2, 0.2)]:
        db_session.add(
            ConceptReviewEvent(
                card_id=card.id,
                user_id=user.id,
                video_id=video.id,
                quiz_result_id=None,
                concept=card.concept,
                score=score,
                source="manual",
                reviewed_at=datetime.combine(today - timedelta(days=offset), datetime.min.time(), UTC),
            )
        )
    await db_session.commit()

    stats = await get_srs_stats(str(user.id), db_session)

    assert stats.retention_rate == pytest.approx(2 / 3)
    assert stats.streak == 3


@pytest.mark.asyncio
async def test_stats_yesterday_preserves_streak_and_empty_history_is_zero(db_session):
    empty_user, _, _ = await create_video_with_notes(db_session, "srs-empty-stats@example.com")
    user, course, video = await create_video_with_notes(db_session, "srs-yesterday@example.com")
    card = make_card(user, video, "streak", datetime.now(UTC).date())
    db_session.add(card)
    await db_session.flush()
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    db_session.add(
        ConceptReviewEvent(
            card_id=card.id,
            user_id=user.id,
            video_id=video.id,
            quiz_result_id=None,
            concept=card.concept,
            score=0.8,
            source="manual",
            reviewed_at=datetime.combine(yesterday, datetime.min.time(), UTC),
        )
    )
    await db_session.commit()

    empty_stats = await get_srs_stats(str(empty_user.id), db_session)
    active_stats = await get_srs_stats(str(user.id), db_session)

    assert empty_stats.retention_rate == 0
    assert empty_stats.streak == 0
    assert active_stats.streak == 1


@pytest.mark.asyncio
async def test_stats_broken_streak_returns_zero(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-broken-streak@example.com")
    card = make_card(user, video, "broken streak", datetime.now(UTC).date())
    db_session.add(card)
    await db_session.flush()
    old_date = datetime.now(UTC).date() - timedelta(days=3)
    db_session.add(
        ConceptReviewEvent(
            card_id=card.id,
            user_id=user.id,
            video_id=video.id,
            quiz_result_id=None,
            concept=card.concept,
            score=0.8,
            source="manual",
            reviewed_at=datetime.combine(old_date, datetime.min.time(), UTC),
        )
    )
    await db_session.commit()

    stats = await get_srs_stats(str(user.id), db_session)

    assert stats.streak == 0


@pytest.mark.asyncio
async def test_exam_plan_rejects_past_date(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-past@example.com")

    with pytest.raises(ValueError):
        await generate_study_plan(
            str(user.id),
            exam_date=datetime.now(UTC).date() - timedelta(days=1),
            db=db_session,
        )


@pytest.mark.asyncio
async def test_exam_plan_with_no_cards_returns_empty(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-empty-plan@example.com")

    plan = await generate_study_plan(
        str(user.id),
        exam_date=datetime.now(UTC).date() + timedelta(days=30),
        db=db_session,
    )

    assert plan.total_cards == 0
    assert "add some courses" in plan.message.lower()


@pytest.mark.asyncio
async def test_exam_plan_rebalances_overloaded_days(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-rebalance@example.com")
    today = datetime.now(UTC).date()
    target = today + timedelta(days=2)
    db_session.add_all(
        [make_card(user, video, f"card {index}", target) for index in range(50)]
    )
    await db_session.commit()

    plan = await generate_study_plan(str(user.id), target, db_session)

    assert target in plan.overloaded_dates
    assert plan.can_complete is True
    assert plan.unscheduled_card_count == 0
    assert max(day.recommended_count for day in plan.days) <= DAILY_REVIEW_CAPACITY


@pytest.mark.asyncio
async def test_exam_plan_reports_unschedulable_pressure(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-pressure@example.com")
    today = datetime.now(UTC).date()
    exam_date = today + timedelta(days=1)
    db_session.add_all(
        [make_card(user, video, f"pressure {index}", exam_date) for index in range(200)]
    )
    await db_session.commit()

    plan = await generate_study_plan(str(user.id), exam_date, db_session)

    assert plan.can_complete is False
    assert plan.unscheduled_card_count == 140
    assert plan.recommended_start_date < today
    assert "cannot fit" in plan.message.lower()


@pytest.mark.asyncio
async def test_srs_dashboard_routes(client, db_session):
    token, user, course, video = await create_api_video(client, db_session, "srs-routes@example.com")
    today = datetime.now(UTC).date()
    db_session.add_all(
        [
            make_card(user, video, "due concept", today),
            make_card(user, video, "future concept", today + timedelta(days=2)),
        ]
    )
    await db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    due_response = await client.get("/api/v1/srs/due-today", headers=headers)
    cards_response = await client.get("/api/v1/srs/cards", headers=headers)
    stats_response = await client.get("/api/v1/srs/stats", headers=headers)
    plan_response = await client.post(
        "/api/v1/srs/exam-plan",
        json={"exam_date": (today + timedelta(days=7)).isoformat()},
        headers=headers,
    )
    past_response = await client.post(
        "/api/v1/srs/exam-plan",
        json={"exam_date": (today - timedelta(days=1)).isoformat()},
        headers=headers,
    )

    assert due_response.status_code == 200
    assert [item["concept"] for item in due_response.json()] == ["due concept"]
    assert cards_response.status_code == 200
    assert len(cards_response.json()) == 2
    assert stats_response.status_code == 200
    assert stats_response.json()["total_cards"] == 2
    assert plan_response.status_code == 200
    assert plan_response.json()["total_cards"] == 2
    assert past_response.status_code == 400


@pytest.mark.asyncio
async def test_card_uniqueness_constraint(db_session):
    user, course, video = await create_video_with_notes(db_session, "srs-unique@example.com")
    today = datetime.now(UTC).date()
    db_session.add_all(
        [
            make_card(user, video, "recursion", today),
            make_card(user, video, "recursion", today),
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def register_and_login(client, email: str) -> str:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    return response.json()["access_token"]


async def create_api_video(client, db_session, email: str):
    token = await register_and_login(client, email)
    user = await db_session.scalar(select(User).where(User.email == email))
    course, video = await create_course_video_notes(db_session, user)
    return token, user, course, video


async def create_video_with_notes(db_session, email: str):
    user = User(email=email, hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course, video = await create_course_video_notes(db_session, user)
    return user, course, video


async def create_course_video_notes(db_session, user: User):
    slug = user.email.split("@")[0]
    course = Course(
        user_id=user.id,
        title="SRS Course",
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
        title="SRS Video",
        position=0,
        status="completed",
    )
    db_session.add(video)
    await db_session.flush()
    db_session.add(
        Notes(
            video_id=video.id,
            course_id=course.id,
            user_id=user.id,
            source_model="groq/llama-3.3-70b",
            full_markdown="## Recursion\n\nRecursion solves a problem with smaller calls.",
            summary="This lesson explains recursion.",
            sections_json=[
                {
                    "heading": "Recursion",
                    "level": 2,
                    "content": "Recursion solves a problem with smaller calls.",
                    "concepts": ["recursion"],
                }
            ],
            concepts_json=["recursion"],
            has_images=False,
            image_count=0,
            token_count=20,
            generated_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(video)
    return course, video


async def create_quiz_result(db_session, user, video, results, session_id):
    result = QuizResult(
        video_id=video.id,
        user_id=user.id,
        session_id=session_id,
        mode="full_review",
        total_questions=len(results),
        average_score=sum(item["score"] for item in results) / len(results),
        weak_concepts=[],
        results_json=results,
        completed_at=datetime.now(UTC),
    )
    db_session.add(result)
    await db_session.flush()
    return result


def make_card(
    user,
    video,
    concept,
    next_review_date,
    *,
    ease=2.5,
    repetitions=0,
    interval=1,
):
    return ConceptCard(
        user_id=user.id,
        video_id=video.id,
        concept=concept,
        ease_factor=ease,
        interval_days=interval,
        repetitions=repetitions,
        next_review_date=next_review_date,
    )
