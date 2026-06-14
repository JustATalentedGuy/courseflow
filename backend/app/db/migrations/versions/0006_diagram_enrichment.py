"""quota-aware diagram enrichment

Revision ID: 0006_diagram_enrichment
Revises: 0005_whisper_scheduler
Create Date: 2026-06-14 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_diagram_enrichment"
down_revision: Union[str, None] = "0005_whisper_scheduler"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("source_markdown", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "notes",
        sa.Column("content_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.execute("UPDATE notes SET source_markdown = full_markdown")

    op.create_table(
        "diagram_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notes_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_version", sa.Integer(), nullable=False),
        sa.Column("marker_index", sa.Integer(), nullable=False),
        sa.Column("marker_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("original_caption", sa.Text(), nullable=False),
        sa.Column("context_snapshot", sa.Text(), server_default="", nullable=False),
        sa.Column("detailed_prompt", sa.Text()),
        sa.Column("negative_prompt", sa.Text()),
        sa.Column("alt_text", sa.Text()),
        sa.Column("render_mode", sa.String()),
        sa.Column("mermaid_source", sa.Text()),
        sa.Column("provider", sa.String()),
        sa.Column("model", sa.String()),
        sa.Column("state", sa.String(), server_default="pending", nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("object_uri", sa.Text()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("checksum", sa.String(length=64)),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request_id", sa.String()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notes_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notes_id",
            "note_version",
            "marker_index",
            name="uq_diagram_asset_note_version_marker",
        ),
    )
    for column in ("notes_id", "video_id", "course_id", "user_id", "state"):
        op.create_index(f"ix_diagram_assets_{column}", "diagram_assets", [column])
    op.create_index(
        "ix_diagram_assets_marker_fingerprint",
        "diagram_assets",
        ["marker_fingerprint"],
    )

    op.create_table(
        "cloudflare_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("diagram_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("estimated_neurons", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["diagram_id"], ["diagram_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_cloudflare_usage_events_diagram_id",
        "cloudflare_usage_events",
        ["diagram_id"],
    )
    op.create_index(
        "ix_cloudflare_usage_events_user_id",
        "cloudflare_usage_events",
        ["user_id"],
    )
    op.create_index(
        "ix_cloudflare_usage_events_created_at",
        "cloudflare_usage_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("cloudflare_usage_events")
    op.drop_table("diagram_assets")
    op.drop_column("notes", "content_version")
    op.drop_column("notes", "source_markdown")
