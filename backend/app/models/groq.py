from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Float,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class NoteGenerationChunk(Base):
    __tablename__ = "note_generation_chunks"
    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "mode",
            "chunk_index",
            name="uq_note_generation_chunk_video_mode_index",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id = Column(
        UUID(as_uuid=True),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id = Column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode = Column(String, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    model = Column(String, nullable=False)
    prompt_fingerprint = Column(String, nullable=False)
    transcript_text = Column(Text, nullable=False)
    state = Column(String, nullable=False, default="pending")
    response_markdown = Column(Text)
    estimated_tokens = Column(Integer, nullable=False, default=0)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    charged_tokens = Column(Integer, nullable=False, default=0)
    request_id = Column(String)
    retry_at = Column(DateTime(timezone=True))
    error_message = Column(Text)
    groq_batch_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("groq_batch_jobs.id", ondelete="SET NULL"),
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class GroqUsageEvent(Base):
    __tablename__ = "groq_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    model = Column(String, nullable=False, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    video_id = Column(
        UUID(as_uuid=True),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id = Column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode = Column(String, nullable=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    charged_tokens = Column(Integer, nullable=False, default=0)
    audio_seconds = Column(Integer, nullable=False, default=0)
    request_id = Column(String, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class WhisperTranscriptionChunk(Base):
    __tablename__ = "whisper_transcription_chunks"
    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "chunk_index",
            name="uq_whisper_chunk_video_index",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id = Column(
        UUID(as_uuid=True),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id = Column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    offset_seconds = Column(Float, nullable=False, default=0)
    duration_seconds = Column(Integer, nullable=False)
    billable_seconds = Column(Integer, nullable=False)
    audio_fingerprint = Column(String, nullable=False)
    state = Column(String, nullable=False, default="pending")
    segments_json = Column(JSONB, nullable=False, default=list)
    request_id = Column(String)
    retry_at = Column(DateTime(timezone=True))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class GroqBatchJob(Base):
    __tablename__ = "groq_batch_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    model = Column(String, nullable=False)
    status = Column(String, nullable=False)
    groq_batch_id = Column(String, unique=True)
    input_file_id = Column(String)
    output_file_id = Column(String)
    error_file_id = Column(String)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
