from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.srs import (
    CardReviewRequest,
    ConceptCardResponse,
    ExamPlanRequest,
    SrsStats,
    StudyPlan,
)
from app.services.srs import (
    generate_study_plan,
    get_all_cards,
    get_due_cards_today,
    get_srs_stats,
    review_card,
)

router = APIRouter(prefix="/srs", tags=["srs"])


@router.get("/due-today", response_model=list[ConceptCardResponse])
async def due_today(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConceptCardResponse]:
    return [
        ConceptCardResponse.model_validate(card)
        for card in await get_due_cards_today(str(current_user.id), db)
    ]


@router.get("/cards", response_model=list[ConceptCardResponse])
async def list_cards(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConceptCardResponse]:
    return [
        ConceptCardResponse.model_validate(card)
        for card in await get_all_cards(str(current_user.id), db)
    ]


@router.post("/review", response_model=ConceptCardResponse)
async def review(
    payload: CardReviewRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConceptCardResponse:
    card = await review_card(str(current_user.id), payload.card_id, payload.score, db)
    return ConceptCardResponse.model_validate(card)


@router.post("/exam-plan", response_model=StudyPlan)
async def exam_plan(
    payload: ExamPlanRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StudyPlan:
    try:
        return await generate_study_plan(str(current_user.id), payload.exam_date, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/stats", response_model=SrsStats)
async def stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SrsStats:
    return await get_srs_stats(str(current_user.id), db)
