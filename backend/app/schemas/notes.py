import re
from typing import Literal

from pydantic import BaseModel, model_validator


class NotesSection(BaseModel):
    heading: str
    level: int
    content: str
    concepts: list[str]


class VideoNotes(BaseModel):
    video_id: str
    course_id: str
    title: str
    source_model: str
    sections: list[NotesSection]
    summary: str
    full_markdown: str
    has_images: bool
    image_count: int
    generated_at: str
    token_count: int
    prompt_token_count: int = 0
    completion_token_count: int = 0
    cached_token_count: int = 0
    request_count: int = 0

    @model_validator(mode="after")
    def validate_notes(self) -> "VideoNotes":
        if "##" not in self.full_markdown:
            raise ValueError("full_markdown must contain at least one level-2 heading")
        sentence_count = len(
            [
                sentence
                for sentence in re.split(r"(?<=[.!?])\s+", self.summary.strip())
                if sentence.strip()
            ]
        )
        if sentence_count < 1 or sentence_count > 5:
            raise ValueError("summary must be 1-5 sentences")
        concepts = [concept for section in self.sections for concept in section.concepts]
        if not concepts:
            raise ValueError("notes must include at least one concept")
        return self


class NotesRegenerateRequest(BaseModel):
    quality: Literal["standard", "high"] = "high"
