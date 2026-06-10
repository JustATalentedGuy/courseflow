from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String, nullable=False)
    language = Column(String, default="en")
    duration_seconds = Column(Float, nullable=False)
    full_text = Column(Text, nullable=False)
    word_count = Column(Integer, nullable=False)
    segments_json = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    video = relationship("Video", back_populates="transcript")
