from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(max_length=500)
    course_id: UUID | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    chunk_id: str
    video_id: str
    video_title: str
    course_id: str
    section_heading: str
    text: str
    similarity_score: float
    start_seconds: float
    timestamp_url: str
