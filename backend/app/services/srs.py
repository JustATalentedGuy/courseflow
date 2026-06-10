import math
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UserIsolationError
from app.models.quiz import QuizResult
from app.models.srs import ConceptCard, ConceptReviewEvent
from app.schemas.srs import SrsStats, StudyPlan, StudyPlanDay

DAILY_REVIEW_CAPACITY = 30


def normalise_concept(concept: str) -> str:
    return re.sub(r"\s+", " ", concept).strip()


def sm2_update(card: ConceptCard, score: float) -> ConceptCard:
    score = max(0.0, min(1.0, float(score)))
    q = int(score * 5)

    if q < 3:
        card.repetitions = 0
        card.interval_days = 1
    else:
        if card.repetitions == 0:
            card.interval_days = 1
        elif card.repetitions == 1:
            card.interval_days = 6
        else:
            card.interval_days = round(card.interval_days * card.ease_factor)
        card.repetitions += 1

    card.interval_days = max(1, card.interval_days)
    card.ease_factor = max(
        1.3,
        card.ease_factor + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02),
    )
    now = datetime.now(UTC)
    card.next_review_date = now.date() + timedelta(days=card.interval_days)
    card.last_score = score
    card.last_reviewed_at = now
    return card


def _final_concept_scores(result: QuizResult) -> dict[str, float]:
    final_scores: dict[str, float] = {}
    for item in result.results_json:
        concept = normalise_concept(str(item.get("concept", "")))
        if concept:
            final_scores[concept] = max(0.0, min(1.0, float(item.get("score", 0.0))))
    return final_scores


async def seed_cards_from_quiz_result(result: QuizResult, db: AsyncSession) -> list[ConceptCard]:
    today = datetime.now(UTC).date()
    reviewed_at = result.completed_at or datetime.now(UTC)
    cards: list[ConceptCard] = []

    for concept, score in _final_concept_scores(result).items():
        created_id = await db.scalar(
            insert(ConceptCard)
            .values(
                user_id=result.user_id,
                video_id=result.video_id,
                concept=concept,
                ease_factor=2.5,
                interval_days=1,
                repetitions=0,
                next_review_date=today + timedelta(days=1),
                last_score=score,
                last_reviewed_at=reviewed_at,
            )
            .on_conflict_do_nothing(
                constraint="uq_concept_cards_owner_video_concept",
            )
            .returning(ConceptCard.id)
        )
        card = await db.scalar(
            select(ConceptCard)
            .where(
                ConceptCard.user_id == result.user_id,
                ConceptCard.video_id == result.video_id,
                ConceptCard.concept == concept,
            )
            .with_for_update()
        )
        if card is None:
            continue

        event_id = await db.scalar(
            insert(ConceptReviewEvent)
            .values(
                card_id=card.id,
                user_id=result.user_id,
                video_id=result.video_id,
                quiz_result_id=result.id,
                concept=concept,
                score=score,
                source="quiz",
                reviewed_at=reviewed_at,
            )
            .on_conflict_do_nothing(
                constraint="uq_concept_review_events_quiz_result_concept",
            )
            .returning(ConceptReviewEvent.id)
        )
        if event_id is None:
            cards.append(card)
            continue
        if created_id is None:
            sm2_update(card, score)
        cards.append(card)

    return cards


async def get_due_cards_today(user_id: str, db: AsyncSession) -> list[ConceptCard]:
    today = datetime.now(UTC).date()
    return list(
        (
            await db.scalars(
                select(ConceptCard)
                .where(
                    ConceptCard.user_id == UUID(user_id),
                    ConceptCard.next_review_date <= today,
                )
                .order_by(
                    ConceptCard.next_review_date.asc(),
                    ConceptCard.ease_factor.asc(),
                )
            )
        ).all()
    )


async def get_all_cards(user_id: str, db: AsyncSession) -> list[ConceptCard]:
    return list(
        (
            await db.scalars(
                select(ConceptCard)
                .where(ConceptCard.user_id == UUID(user_id))
                .order_by(ConceptCard.next_review_date.asc(), ConceptCard.concept.asc())
            )
        ).all()
    )


async def review_card(
    user_id: str,
    card_id: UUID,
    score: float,
    db: AsyncSession,
) -> ConceptCard:
    card = await db.scalar(
        select(ConceptCard)
        .where(ConceptCard.id == card_id, ConceptCard.user_id == UUID(user_id))
        .with_for_update()
    )
    if card is None:
        raise UserIsolationError("Concept card not found")

    sm2_update(card, score)
    db.add(
        ConceptReviewEvent(
            card_id=card.id,
            user_id=card.user_id,
            video_id=card.video_id,
            quiz_result_id=None,
            concept=card.concept,
            score=max(0.0, min(1.0, float(score))),
            source="manual",
            reviewed_at=card.last_reviewed_at,
        )
    )
    await db.commit()
    await db.refresh(card)
    return card


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _recommended_start_date(targets: list[date], capacity: int) -> date | None:
    if not targets:
        return None
    counts = Counter(targets)
    cumulative = 0
    recommendation = targets[0]
    for target in sorted(counts):
        cumulative += counts[target]
        required_days = math.ceil(cumulative / capacity)
        candidate = target - timedelta(days=required_days - 1)
        recommendation = min(recommendation, candidate)
    return recommendation


async def generate_study_plan(
    user_id: str,
    exam_date: date,
    db: AsyncSession,
) -> StudyPlan:
    today = datetime.now(UTC).date()
    if exam_date <= today:
        raise ValueError("Exam date must be in the future")

    cards = await get_all_cards(user_id, db)
    dates = _date_range(today, exam_date)
    if not cards:
        return StudyPlan(
            exam_date=exam_date,
            days_remaining=(exam_date - today).days,
            total_cards=0,
            daily_capacity=DAILY_REVIEW_CAPACITY,
            days=[
                StudyPlanDay(
                    date=day,
                    scheduled_count=0,
                    recommended_count=0,
                    capacity=DAILY_REVIEW_CAPACITY,
                )
                for day in dates
            ],
            overloaded_dates=[],
            unscheduled_card_count=0,
            can_complete=True,
            recommended_start_date=None,
            message="Add some courses and complete quizzes to create concept cards first.",
        )

    targets = [
        min(max(card.next_review_date, today), exam_date)
        for card in cards
    ]
    scheduled = Counter(targets)
    overloaded = sorted(
        day for day, count in scheduled.items() if count > DAILY_REVIEW_CAPACITY
    )

    remaining = {day: DAILY_REVIEW_CAPACITY for day in dates}
    recommended = Counter()
    unscheduled = 0
    for target in sorted(targets):
        assigned = next(
            (
                day
                for day in reversed(dates)
                if day <= target and remaining[day] > 0
            ),
            None,
        )
        if assigned is None:
            unscheduled += 1
            continue
        recommended[assigned] += 1
        remaining[assigned] -= 1

    start_recommendation = _recommended_start_date(targets, DAILY_REVIEW_CAPACITY)
    can_complete = unscheduled == 0
    if can_complete and overloaded:
        message = "The plan rebalances overloaded review days into earlier available dates."
    elif can_complete:
        message = "The current review schedule fits within the daily study capacity."
    else:
        message = (
            f"{unscheduled} cards cannot fit before the exam at "
            f"{DAILY_REVIEW_CAPACITY} reviews per day. Start earlier if possible."
        )

    return StudyPlan(
        exam_date=exam_date,
        days_remaining=(exam_date - today).days,
        total_cards=len(cards),
        daily_capacity=DAILY_REVIEW_CAPACITY,
        days=[
            StudyPlanDay(
                date=day,
                scheduled_count=scheduled[day],
                recommended_count=recommended[day],
                capacity=DAILY_REVIEW_CAPACITY,
            )
            for day in dates
        ],
        overloaded_dates=overloaded,
        unscheduled_card_count=unscheduled,
        can_complete=can_complete,
        recommended_start_date=start_recommendation,
        message=message,
    )


def _calculate_streak(review_dates: set[date], today: date) -> int:
    if not review_dates:
        return 0
    cursor = today if today in review_dates else today - timedelta(days=1)
    if cursor not in review_dates:
        return 0
    streak = 0
    while cursor in review_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def get_srs_stats(user_id: str, db: AsyncSession) -> SrsStats:
    user_uuid = UUID(user_id)
    today = datetime.now(UTC).date()
    total_cards = int(
        await db.scalar(
            select(func.count()).select_from(ConceptCard).where(ConceptCard.user_id == user_uuid)
        )
        or 0
    )
    due_today = int(
        await db.scalar(
            select(func.count())
            .select_from(ConceptCard)
            .where(
                ConceptCard.user_id == user_uuid,
                ConceptCard.next_review_date <= today,
            )
        )
        or 0
    )
    events = list(
        (
            await db.scalars(
                select(ConceptReviewEvent)
                .where(ConceptReviewEvent.user_id == user_uuid)
                .order_by(ConceptReviewEvent.reviewed_at.asc())
            )
        ).all()
    )
    retention = (
        sum(event.score >= 0.6 for event in events) / len(events)
        if events
        else 0.0
    )
    review_dates = {event.reviewed_at.astimezone(UTC).date() for event in events}
    return SrsStats(
        total_cards=total_cards,
        due_today=due_today,
        retention_rate=retention,
        streak=_calculate_streak(review_dates, today),
    )
