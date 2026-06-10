from pydantic import BaseModel, field_validator, model_validator


class TranscriptChunk(BaseModel):
    text: str
    start_seconds: float
    end_seconds: float
    chunk_index: int

    @model_validator(mode="after")
    def validate_chunk(self) -> "TranscriptChunk":
        if not self.text.strip():
            raise ValueError("Transcript chunk text cannot be empty")
        if self.start_seconds >= self.end_seconds:
            raise ValueError("Transcript chunk start_seconds must be less than end_seconds")
        return self


class TextChunk(BaseModel):
    chunk_id: str
    video_id: str
    course_id: str
    user_id: str
    text: str
    start_seconds: float
    end_seconds: float
    section_heading: str
    embedding: list[float]
    chunk_index: int

    @field_validator("embedding")
    @classmethod
    def embedding_must_be_384_dimensions(cls, value: list[float]) -> list[float]:
        if len(value) != 384:
            raise ValueError("embedding must be 384-dimensional")
        return value

    @model_validator(mode="after")
    def validate_chunk(self) -> "TextChunk":
        if not self.text.strip():
            raise ValueError("chunk text cannot be empty")
        if self.start_seconds >= self.end_seconds:
            raise ValueError("start_seconds must be less than end_seconds")
        return self
