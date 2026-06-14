from enum import Enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class VideoStatus(str, Enum):
    PENDING = "pending"
    DEFERRED = "deferred"
    PROCESSING = "processing"
    RATE_LIMITED = "rate_limited"
    BATCH_PROCESSING = "batch_processing"
    COMPLETED = "completed"
    FAILED = "failed"
    MANUAL = "manual"


class VideoResponse(BaseModel):
    id: UUID
    course_id: UUID
    youtube_video_id: str
    title: str
    position: int
    duration_seconds: int | None
    status: str
    transcript_source: str | None
    celery_task_id: str | None
    scheduled_for: datetime | None
    error_message: str | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
