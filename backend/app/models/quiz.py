from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class QuizResult(Base):
    __tablename__ = "quiz_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String, unique=True, nullable=False)
    mode = Column(String, nullable=False)
    total_questions = Column(Integer, nullable=False)
    average_score = Column(Float, nullable=False)
    weak_concepts = Column(JSONB, default=list)
    results_json = Column(JSONB, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)
