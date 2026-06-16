"""hybrid edge youtube fetcher

Revision ID: 0007_edge_youtube_fetcher
Revises: 0006_diagram_enrichment
Create Date: 2026-06-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_edge_youtube_fetcher"
down_revision: Union[str, None] = "0006_diagram_enrichment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "edge_fetcher_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edge_fetcher_tokens_user_id", "edge_fetcher_tokens", ["user_id"])

    op.create_table(
        "youtube_edge_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("state", sa.String(), server_default="pending", nullable=False),
        sa.Column("youtube_url", sa.Text(), nullable=False),
        sa.Column("youtube_video_id", sa.String()),
        sa.Column("lease_token", sa.String(length=64)),
        sa.Column("leased_until", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String()),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String()),
        sa.Column("submitted_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("audio_object_uri", sa.Text()),
        sa.Column("audio_content_type", sa.String()),
        sa.Column("audio_size_bytes", sa.Integer()),
        sa.Column("audio_duration_seconds", sa.Float()),
        sa.Column("audio_sha256", sa.String(length=64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "course_id", "video_id", "job_type", "state"):
        op.create_index(f"ix_youtube_edge_jobs_{column}", "youtube_edge_jobs", [column])
    op.create_index(
        "ix_youtube_edge_jobs_claim",
        "youtube_edge_jobs",
        ["state", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "uq_youtube_edge_jobs_video_transcript_active",
        "youtube_edge_jobs",
        ["video_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'video_transcript' AND state != 'completed'"),
    )


def downgrade() -> None:
    op.drop_table("youtube_edge_jobs")
    op.drop_table("edge_fetcher_tokens")
