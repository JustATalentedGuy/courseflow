from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.db.base import Base


class NoteChunk(Base):
    __tablename__ = "note_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    start_seconds = Column(Float)
    end_seconds = Column(Float)
    section_heading = Column(String)
    chunk_index = Column(Integer, nullable=False)
    embedding = mapped_column(Vector(384))

    video = relationship("Video", back_populates="chunks")
