from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.video import VideoResponse


class CourseCreate(BaseModel):
    playlist_url: str


class CourseSeed(BaseModel):
    title: str
    playlist_url: str
    playlist_id: str


class CourseResponse(BaseModel):
    id: UUID
    title: str
    playlist_url: str
    playlist_id: str
    video_count: int
    status: str
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class CourseDetail(CourseResponse):
    videos: list[VideoResponse]


class CourseStatusResponse(BaseModel):
    course_id: UUID
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    deferred: int
    deferred_until: datetime | None = None
    quota_remaining: dict[str, int] = Field(default_factory=dict)
