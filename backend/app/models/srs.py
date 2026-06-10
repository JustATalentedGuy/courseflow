from uuid import uuid4

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class ConceptCard(Base):
    __tablename__ = "concept_cards"
    __table_args__ = (
        UniqueConstraint("user_id", "video_id", "concept", name="uq_concept_cards_owner_video_concept"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    concept = Column(String, nullable=False)
    ease_factor = Column(Float, nullable=False, default=2.5)
    interval_days = Column(Integer, nullable=False, default=1)
    repetitions = Column(Integer, nullable=False, default=0)
    next_review_date = Column(Date, nullable=False)
    last_score = Column(Float)
    last_reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ConceptReviewEvent(Base):
    __tablename__ = "concept_review_events"
    __table_args__ = (
        UniqueConstraint(
            "quiz_result_id",
            "concept",
            name="uq_concept_review_events_quiz_result_concept",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    card_id = Column(UUID(as_uuid=True), ForeignKey("concept_cards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(UUID(as_uuid=True), ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    quiz_result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("quiz_results.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    concept = Column(String, nullable=False)
    score = Column(Float, nullable=False)
    source = Column(String, nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
