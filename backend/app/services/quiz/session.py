import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import QuizSessionExpiredError, UserIsolationError, ValidationError
from app.models.chunk import NoteChunk
from app.models.quiz import QuizResult
from app.models.video import Video
from app.schemas.quiz import WeakConceptSummary
from app.services.embedder import embed_texts
from app.services.quiz.agent import (
    evaluate_answer_llm,
    generate_question_llm,
    generate_session_summary,
    select_next_concept,
    should_continue,
    update_session_state,
)
from app.services.quiz.state import ConceptRecord, QuizState, initialise_quiz_state
from app.services.srs import seed_cards_from_quiz_result

SESSION_TTL_SECONDS = 2 * 60 * 60


def _session_key(session_id: str) -> str:
    return f"quiz:session:{session_id}"


async def _load_video(db: AsyncSession, user_id: UUID, video_id: UUID) -> Video:
    video = await db.scalar(
        select(Video)
        .options(selectinload(Video.notes))
        .where(Video.id == video_id, Video.user_id == user_id)
    )
    if video is None:
        raise UserIsolationError("Video not found")
    if video.notes is None or not video.notes.concepts_json:
        raise ValidationError("Video notes with concepts are required before starting a quiz")
    return video


async def _prior_weak_concepts(
    db: AsyncSession,
    user_id: UUID,
    video_id: UUID,
) -> list[ConceptRecord]:
    rows = (
        await db.scalars(
            select(QuizResult)
            .where(QuizResult.user_id == user_id, QuizResult.video_id == video_id)
            .order_by(QuizResult.completed_at.desc())
        )
    ).all()
    records: dict[str, ConceptRecord] = {}
    for result in rows:
        for item in result.results_json:
            score = float(item.get("score", 0.0))
            concept = str(item.get("concept", "")).strip()
            if not concept:
                continue
            record = records.get(
                concept,
                ConceptRecord(concept=concept, times_asked=0, times_correct=0, last_score=score),
            )
            record["times_asked"] += 1
            record["times_correct"] += int(score >= 0.6)
            record["last_score"] = score
            records[concept] = record
    return [record for record in records.values() if record["last_score"] < 0.6]


async def retrieve_concept_context(
    db: AsyncSession,
    state: QuizState,
) -> str:
    concept = state.get("current_concept") or ""
    if not concept:
        return ""
    query_embedding = embed_texts([concept])[0]
    distance = NoteChunk.embedding.cosine_distance(query_embedding)
    chunk = await db.scalar(
        select(NoteChunk)
        .where(
            NoteChunk.video_id == UUID(state["video_id"]),
            NoteChunk.user_id == UUID(state["user_id"]),
            NoteChunk.embedding.is_not(None),
        )
        .order_by(distance.asc())
        .limit(1)
    )
    if chunk is not None:
        return chunk.text

    video = await _load_video(db, UUID(state["user_id"]), UUID(state["video_id"]))
    for section in video.notes.sections_json:
        if concept.lower() in str(section.get("heading", "")).lower():
            return str(section.get("content", ""))
    return video.notes.full_markdown[:3000]


async def _prepare_question(state: QuizState, db: AsyncSession) -> QuizState:
    selected = select_next_concept(state)
    state.update(selected)
    if state["session_complete"] or not state["current_concept"]:
        return state
    state["current_context"] = await retrieve_concept_context(db, state)
    state["key_gap"] = None
    state["current_question"] = await asyncio.to_thread(generate_question_llm, state)
    state["user_answer"] = None
    state["messages"].append({"role": "assistant", "content": state["current_question"]})
    return state


async def _save_session(redis: Redis, state: QuizState, ttl: int = SESSION_TTL_SECONDS) -> None:
    await redis.set(_session_key(state["session_id"]), json.dumps(state), ex=ttl)


async def _load_session(redis: Redis, session_id: str, user_id: UUID) -> QuizState:
    payload = await redis.get(_session_key(session_id))
    if payload is None:
        raise QuizSessionExpiredError("Quiz session expired or was not found")
    state: QuizState = json.loads(payload)
    if state["user_id"] != str(user_id):
        raise UserIsolationError("Quiz session belongs to another user")
    return state


async def start_quiz_session(
    db: AsyncSession,
    redis: Redis,
    user_id: UUID,
    video_id: UUID,
    mode: str,
) -> QuizState:
    video = await _load_video(db, user_id, video_id)
    weak = await _prior_weak_concepts(db, user_id, video_id)
    concepts = [str(concept) for concept in video.notes.concepts_json if str(concept).strip()]
    if not concepts:
        raise ValidationError("Video notes do not contain usable quiz concepts")
    if mode == "weak_spot" and weak:
        weak_names = {record["concept"] for record in weak}
        concepts = [concept for concept in concepts if concept in weak_names] or concepts

    state = initialise_quiz_state(
        video_id=str(video.id),
        user_id=str(user_id),
        mode=mode,
        all_concepts=concepts,
        weak_concepts=weak,
        session_id=str(uuid4()),
    )
    await _prepare_question(state, db)
    await _save_session(redis, state)
    return state


async def _complete_session(state: QuizState, db: AsyncSession, redis: Redis) -> QuizResult:
    state.update(generate_session_summary(state))
    scores = [float(item["score"]) for item in state["results"]]
    average = sum(scores) / len(scores) if scores else 0.0
    weak = [
        {"concept": record["concept"], "last_score": record["last_score"]}
        for record in state["weak_concepts"]
    ]
    record = QuizResult(
        video_id=UUID(state["video_id"]),
        user_id=UUID(state["user_id"]),
        session_id=state["session_id"],
        mode=state["mode"],
        total_questions=len(state["results"]),
        average_score=average,
        weak_concepts=weak,
        results_json=state["results"],
        completed_at=datetime.now(UTC),
    )
    db.add(record)
    await db.flush()
    await seed_cards_from_quiz_result(record, db)
    await db.commit()
    await db.refresh(record)
    await redis.delete(_session_key(state["session_id"]))
    return record


async def answer_quiz_session(
    db: AsyncSession,
    redis: Redis,
    user_id: UUID,
    session_id: str,
    answer: str,
) -> tuple[QuizState, bool]:
    state = await _load_session(redis, session_id, user_id)
    state["user_answer"] = answer.strip()
    evaluation = await asyncio.to_thread(
        evaluate_answer_llm,
        state.get("current_concept") or "",
        state.get("current_context") or "",
        state["user_answer"],
    )
    state["answer_score"] = evaluation["score"]
    state["answer_feedback"] = evaluation["feedback"]
    state["key_gap"] = evaluation["key_gap"]
    state["messages"].append({"role": "user", "content": state["user_answer"]})
    state.update(update_session_state(state))

    action = should_continue(state)
    if action == "end_session":
        await _complete_session(state, db, redis)
        return state, True

    if action == "probe_deeper":
        concept = state.get("current_concept") or ""
        state["times_probed"][concept] = state["times_probed"].get(concept, 0) + 1
        state["current_question"] = await asyncio.to_thread(generate_question_llm, state)
        state["messages"].append({"role": "assistant", "content": state["current_question"]})
        state["user_answer"] = None
    else:
        await _prepare_question(state, db)

    await _save_session(redis, state)
    return state, False


async def list_quiz_results(
    db: AsyncSession,
    user_id: UUID,
    video_id: UUID,
) -> list[QuizResult]:
    await _load_video(db, user_id, video_id)
    return list(
        (
            await db.scalars(
                select(QuizResult)
                .where(QuizResult.user_id == user_id, QuizResult.video_id == video_id)
                .order_by(QuizResult.completed_at.desc())
            )
        ).all()
    )


async def aggregate_weak_concepts(
    db: AsyncSession,
    user_id: UUID,
) -> list[WeakConceptSummary]:
    results = (
        await db.scalars(select(QuizResult).where(QuizResult.user_id == user_id))
    ).all()
    aggregate: dict[str, dict] = {}
    for result in results:
        for item in result.results_json:
            score = float(item.get("score", 0.0))
            if score >= 0.6:
                continue
            concept = str(item.get("concept", "")).strip()
            if not concept:
                continue
            current = aggregate.setdefault(
                concept,
                {"scores": [], "video_ids": set()},
            )
            current["scores"].append(score)
            current["video_ids"].add(str(result.video_id))

    summaries = [
        WeakConceptSummary(
            concept=concept,
            attempts=len(data["scores"]),
            average_score=sum(data["scores"]) / len(data["scores"]),
            video_ids=sorted(data["video_ids"]),
        )
        for concept, data in aggregate.items()
    ]
    return sorted(summaries, key=lambda item: item.average_score)
