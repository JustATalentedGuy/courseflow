from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class EdgeFetcherToken(Base):
    __tablename__ = "edge_fetcher_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    token_prefix = Column(String(length=16), nullable=False)
    token_hash = Column(String, nullable=False)
    revoked = Column(Boolean, server_default="false", nullable=False)
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")


class YouTubeEdgeJob(Base):
    __tablename__ = "youtube_edge_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    job_type = Column(String, nullable=False, index=True)
    state = Column(String, server_default="pending", nullable=False, index=True)
    youtube_url = Column(Text, nullable=False)
    youtube_video_id = Column(String)
    lease_token = Column(String(length=64))
    leased_until = Column(DateTime(timezone=True))
    lease_owner = Column(String)
    attempt_count = Column(Integer, server_default="0", nullable=False)
    next_attempt_at = Column(DateTime(timezone=True))
    idempotency_key = Column(String)
    submitted_payload = Column(JSONB)
    audio_object_uri = Column(Text)
    audio_content_type = Column(String)
    audio_size_bytes = Column(Integer)
    audio_duration_seconds = Column(Float)
    audio_sha256 = Column(String(length=64))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User")
    course = relationship("Course")
    video = relationship("Video")
