from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


def normalise_spaces(value: str) -> str:
    return " ".join(value.split())


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None

    @model_validator(mode="after")
    def validate_segment(self) -> "TranscriptSegment":
        if self.end <= self.start:
            raise ValueError("Transcript segment end must be greater than start")
        self.text = normalise_spaces(self.text)
        return self


class NormalisedTranscript(BaseModel):
    video_id: str
    source: Literal["youtube_captions", "groq_whisper"]
    language: str
    duration_seconds: float
    segments: list[TranscriptSegment]
    full_text: str
    word_count: int
    fetched_at: str

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("duration_seconds must be positive")
        return value

    @model_validator(mode="after")
    def validate_transcript(self) -> "NormalisedTranscript":
        joined = normalise_spaces(" ".join(segment.text for segment in self.segments))
        self.full_text = normalise_spaces(self.full_text)
        banned = ("[Music]", "[Applause]", "[Laughter]")
        if any(annotation in self.full_text for annotation in banned):
            raise ValueError("Transcript contains non-speech annotations")
        if not self.full_text:
            raise ValueError("full_text cannot be empty")
        if self.full_text != joined:
            raise ValueError("full_text must equal joined segment text")
        if self.word_count != len(self.full_text.split()):
            raise ValueError("word_count must match full_text")
        return self
