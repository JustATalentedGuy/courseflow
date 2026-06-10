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
    source_model: Literal[
        "groq/llama-3.3-70b",
        "manual/claude",
        "manual/other",
        "manual/user",
    ]
    sections: list[NotesSection]
    summary: str
    full_markdown: str
    has_images: bool
    image_count: int
    generated_at: str
    token_count: int

    @model_validator(mode="after")
    def validate_notes(self) -> "VideoNotes":
        if "##" not in self.full_markdown:
            raise ValueError("full_markdown must contain at least one level-2 heading")
        sentence_count = sum(self.summary.count(mark) for mark in ".!?")
        if sentence_count < 1 or sentence_count > 5:
            raise ValueError("summary must be 1-5 sentences")
        concepts = [concept for section in self.sections for concept in section.concepts]
        if not concepts:
            raise ValueError("notes must include at least one concept")
        return self
