from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Notes(Base):
    __tablename__ = "notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_model = Column(String, nullable=False)
    full_markdown = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    sections_json = Column(JSONB, nullable=False)
    concepts_json = Column(JSONB, default=list)
    has_images = Column(Boolean, default=False)
    image_count = Column(Integer, default=0)
    token_count = Column(Integer, default=0)
    generated_at = Column(DateTime(timezone=True), nullable=False)

    video = relationship("Video", back_populates="notes")
