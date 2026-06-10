from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConceptCardResponse(BaseModel):
    id: UUID
    video_id: UUID
    concept: str
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_date: date
    last_score: float | None
    last_reviewed_at: datetime | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CardReviewRequest(BaseModel):
    card_id: UUID
    score: float = Field(ge=0.0, le=1.0)


class ExamPlanRequest(BaseModel):
    exam_date: date


class StudyPlanDay(BaseModel):
    date: date
    scheduled_count: int
    recommended_count: int
    capacity: int


class StudyPlan(BaseModel):
    exam_date: date
    days_remaining: int
    total_cards: int
    daily_capacity: int
    days: list[StudyPlanDay]
    overloaded_dates: list[date]
    unscheduled_card_count: int
    can_complete: bool
    recommended_start_date: date | None
    message: str


class SrsStats(BaseModel):
    total_cards: int
    due_today: int
    retention_rate: float
    streak: int
