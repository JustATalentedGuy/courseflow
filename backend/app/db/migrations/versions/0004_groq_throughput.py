"""groq throughput scheduling

Revision ID: 0004_groq_throughput
Revises: 0003_phase7_spaced_repetition
Create Date: 2026-06-11 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_groq_throughput"
down_revision: Union[str, None] = "0003_phase7_spaced_repetition"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("prompt_token_count", sa.Integer(), server_default="0"))
    op.add_column("notes", sa.Column("completion_token_count", sa.Integer(), server_default="0"))
    op.add_column("notes", sa.Column("cached_token_count", sa.Integer(), server_default="0"))
    op.add_column("notes", sa.Column("request_count", sa.Integer(), server_default="0"))

    op.create_table(
        "groq_batch_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("groq_batch_id", sa.String()),
        sa.Column("input_file_id", sa.String()),
        sa.Column("output_file_id", sa.String()),
        sa.Column("error_file_id", sa.String()),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("groq_batch_id"),
    )

    op.create_table(
        "note_generation_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_fingerprint", sa.String(), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("response_markdown", sa.Text()),
        sa.Column("estimated_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("charged_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request_id", sa.String()),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("groq_batch_job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["groq_batch_job_id"], ["groq_batch_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "video_id",
            "mode",
            "chunk_index",
            name="uq_note_generation_chunk_video_mode_index",
        ),
    )
    op.create_index("ix_note_generation_chunks_video_id", "note_generation_chunks", ["video_id"])
    op.create_index("ix_note_generation_chunks_course_id", "note_generation_chunks", ["course_id"])
    op.create_index("ix_note_generation_chunks_user_id", "note_generation_chunks", ["user_id"])

    op.create_table(
        "groq_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("charged_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request_id", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_groq_usage_events_model", "groq_usage_events", ["model"])
    op.create_index("ix_groq_usage_events_user_id", "groq_usage_events", ["user_id"])
    op.create_index("ix_groq_usage_events_video_id", "groq_usage_events", ["video_id"])
    op.create_index("ix_groq_usage_events_course_id", "groq_usage_events", ["course_id"])
    op.create_index("ix_groq_usage_events_created_at", "groq_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("groq_usage_events")
    op.drop_table("note_generation_chunks")
    op.drop_table("groq_batch_jobs")
    op.drop_column("notes", "request_count")
    op.drop_column("notes", "cached_token_count")
    op.drop_column("notes", "completion_token_count")
    op.drop_column("notes", "prompt_token_count")
