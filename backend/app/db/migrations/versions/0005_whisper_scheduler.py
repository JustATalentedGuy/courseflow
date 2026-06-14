"""durable whisper scheduling

Revision ID: 0005_whisper_scheduler
Revises: 0004_groq_throughput
Create Date: 2026-06-14 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_whisper_scheduler"
down_revision: Union[str, None] = "0004_groq_throughput"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "groq_usage_events",
        sa.Column("audio_seconds", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "whisper_transcription_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("offset_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("billable_seconds", sa.Integer(), nullable=False),
        sa.Column("audio_fingerprint", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("segments_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String()),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "video_id",
            "chunk_index",
            name="uq_whisper_chunk_video_index",
        ),
    )
    op.create_index(
        "ix_whisper_transcription_chunks_video_id",
        "whisper_transcription_chunks",
        ["video_id"],
    )
    op.create_index(
        "ix_whisper_transcription_chunks_course_id",
        "whisper_transcription_chunks",
        ["course_id"],
    )
    op.create_index(
        "ix_whisper_transcription_chunks_user_id",
        "whisper_transcription_chunks",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_table("whisper_transcription_chunks")
    op.drop_column("groq_usage_events", "audio_seconds")
