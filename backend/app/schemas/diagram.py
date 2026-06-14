from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


DiagramState = Literal[
    "pending",
    "spec_generating",
    "rendering",
    "rate_limited",
    "completed",
    "failed",
    "skipped",
    "stale",
]
DiagramMode = Literal["structured", "illustrative"]


class DiagramAssetResponse(BaseModel):
    id: UUID
    video_id: UUID
    course_id: UUID
    marker_index: int
    original_caption: str
    detailed_prompt: str | None
    alt_text: str | None
    render_mode: DiagramMode | None
    mermaid_source: str | None
    provider: str | None
    model: str | None
    state: DiagramState
    retry_at: datetime | None
    image_url: str | None = None
    width: int | None
    height: int | None
    revision: int
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)


class DiagramStatusResponse(BaseModel):
    course_id: UUID
    discovered: int
    pending: int
    processing: int
    waiting: int
    completed: int
    failed: int
    skipped: int
    stale: int


class DiagramGenerateResponse(BaseModel):
    course_id: UUID
    discovered: int
    queued: int


class DiagramRegenerateRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=5000)
    mode: DiagramMode | None = None
