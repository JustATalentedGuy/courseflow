from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

QuizMode = Literal["quick_drill", "full_review", "weak_spot"]


class QuizStartRequest(BaseModel):
    video_id: UUID
    mode: QuizMode


class QuizStartResponse(BaseModel):
    session_id: str
    first_question: str
    current_concept: str
    difficulty: str


class QuizAnswerRequest(BaseModel):
    session_id: str
    answer: str = Field(min_length=1)


class QuizAnswerResponse(BaseModel):
    score: float
    feedback: str
    next_question: str | None
    current_concept: str | None
    difficulty: str
    session_complete: bool


class QuizResultResponse(BaseModel):
    id: UUID
    video_id: UUID
    session_id: str
    mode: str
    total_questions: int
    average_score: float
    weak_concepts: list
    results_json: list[dict]
    completed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WeakConceptSummary(BaseModel):
    concept: str
    attempts: int
    average_score: float
    video_ids: list[str]
