from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class DiagramAsset(Base):
    __tablename__ = "diagram_assets"
    __table_args__ = (
        UniqueConstraint(
            "notes_id",
            "note_version",
            "marker_index",
            name="uq_diagram_asset_note_version_marker",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    notes_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
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
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note_version = Column(Integer, nullable=False)
    marker_index = Column(Integer, nullable=False)
    marker_fingerprint = Column(String(64), nullable=False, index=True)
    original_caption = Column(Text, nullable=False)
    context_snapshot = Column(Text, nullable=False, default="")
    detailed_prompt = Column(Text)
    negative_prompt = Column(Text)
    alt_text = Column(Text)
    render_mode = Column(String)
    mermaid_source = Column(Text)
    provider = Column(String)
    model = Column(String)
    state = Column(String, nullable=False, default="pending", index=True)
    retry_at = Column(DateTime(timezone=True))
    object_uri = Column(Text)
    width = Column(Integer)
    height = Column(Integer)
    checksum = Column(String(64))
    revision = Column(Integer, nullable=False, default=0)
    request_id = Column(String)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CloudflareUsageEvent(Base):
    __tablename__ = "cloudflare_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    diagram_id = Column(
        UUID(as_uuid=True),
        ForeignKey("diagram_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    estimated_neurons = Column(Integer, nullable=False)
    request_id = Column(String, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
