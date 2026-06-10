from uuid import UUID

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_redis
from app.models.user import User
from app.schemas.quiz import (
    QuizAnswerRequest,
    QuizAnswerResponse,
    QuizResultResponse,
    QuizStartRequest,
    QuizStartResponse,
    WeakConceptSummary,
)
from app.services.quiz.session import (
    aggregate_weak_concepts,
    answer_quiz_session,
    list_quiz_results,
    start_quiz_session,
)

router = APIRouter(prefix="/quiz", tags=["quiz"])


@router.post("/start", response_model=QuizStartResponse)
async def start_quiz(
    payload: QuizStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> QuizStartResponse:
    state = await start_quiz_session(db, redis, current_user.id, payload.video_id, payload.mode)
    return QuizStartResponse(
        session_id=state["session_id"],
        first_question=state["current_question"] or "",
        current_concept=state["current_concept"] or "",
        difficulty=state["current_difficulty"],
    )


@router.post("/answer", response_model=QuizAnswerResponse)
async def answer_quiz(
    payload: QuizAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> QuizAnswerResponse:
    state, complete = await answer_quiz_session(
        db,
        redis,
        current_user.id,
        payload.session_id,
        payload.answer,
    )
    return QuizAnswerResponse(
        score=float(state["answer_score"] or 0.0),
        feedback=state["answer_feedback"] or "",
        next_question=None if complete else state["current_question"],
        current_concept=state["current_concept"],
        difficulty=state["current_difficulty"],
        session_complete=complete,
    )


@router.get("/sessions/{video_id}", response_model=list[QuizResultResponse])
async def quiz_history(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[QuizResultResponse]:
    return [
        QuizResultResponse.model_validate(result)
        for result in await list_quiz_results(db, current_user.id, video_id)
    ]


@router.get("/weak-concepts", response_model=list[WeakConceptSummary])
async def weak_concepts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WeakConceptSummary]:
    return await aggregate_weak_concepts(db, current_user.id)
