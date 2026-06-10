from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class Video(Base):
    __tablename__ = "videos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    youtube_video_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    position = Column(Integer, nullable=False)
    duration_seconds = Column(Integer)
    status = Column(String, default="pending")
    transcript_source = Column(String)
    celery_task_id = Column(String)
    scheduled_for = Column(DateTime(timezone=True))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    course = relationship("Course", back_populates="videos")
    transcript = relationship("Transcript", back_populates="video", cascade="all, delete-orphan", uselist=False)
    notes = relationship("Notes", back_populates="video", cascade="all, delete-orphan", uselist=False)
    chunks = relationship("NoteChunk", back_populates="video", cascade="all, delete-orphan")
