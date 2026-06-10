from typing import Literal

from pydantic import BaseModel, Field


class ManualPrompt(BaseModel):
    prompt_text: str
    chunk_index: int
    total_chunks: int
    estimated_tokens: int
    video_title: str


class ManualNotesRequest(BaseModel):
    chunk_index: int = Field(ge=0)
    response: str = Field(min_length=1)


class ManualNotesResult(BaseModel):
    status: Literal["partial", "complete"]
    notes_id: str | None = None
    received_chunks: list[int]
    total_chunks: int
