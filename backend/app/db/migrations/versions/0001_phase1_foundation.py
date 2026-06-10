"""phase1 foundation

Revision ID: 0001_phase1_foundation
Revises:
Create Date: 2026-06-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_phase1_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("playlist_url", sa.String(), nullable=False),
        sa.Column("playlist_id", sa.String(), nullable=False),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_courses_user_id"), "courses", ["user_id"], unique=False)

    op.create_table(
        "videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("youtube_video_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("transcript_source", sa.String(), nullable=True),
        sa.Column("celery_task_id", sa.String(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_videos_course_id"), "videos", ["course_id"], unique=False)
    op.create_index(op.f("ix_videos_user_id"), "videos", ["user_id"], unique=False)

    op.create_table(
        "transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("segments_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index(op.f("ix_transcripts_user_id"), "transcripts", ["user_id"], unique=False)

    op.create_table(
        "notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_model", sa.String(), nullable=False),
        sa.Column("full_markdown", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("sections_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("concepts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("has_images", sa.Boolean(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index(op.f("ix_notes_course_id"), "notes", ["course_id"], unique=False)
    op.create_index(op.f("ix_notes_user_id"), "notes", ["user_id"], unique=False)

    op.create_table(
        "note_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=True),
        sa.Column("end_seconds", sa.Float(), nullable=True),
        sa.Column("section_heading", sa.String(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_note_chunks_course_id"), "note_chunks", ["course_id"], unique=False)
    op.create_index(op.f("ix_note_chunks_user_id"), "note_chunks", ["user_id"], unique=False)
    op.create_index(op.f("ix_note_chunks_video_id"), "note_chunks", ["video_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_note_chunks_video_id"), table_name="note_chunks")
    op.drop_index(op.f("ix_note_chunks_user_id"), table_name="note_chunks")
    op.drop_index(op.f("ix_note_chunks_course_id"), table_name="note_chunks")
    op.drop_table("note_chunks")
    op.drop_index(op.f("ix_notes_user_id"), table_name="notes")
    op.drop_index(op.f("ix_notes_course_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_index(op.f("ix_transcripts_user_id"), table_name="transcripts")
    op.drop_table("transcripts")
    op.drop_index(op.f("ix_videos_user_id"), table_name="videos")
    op.drop_index(op.f("ix_videos_course_id"), table_name="videos")
    op.drop_table("videos")
    op.drop_index(op.f("ix_courses_user_id"), table_name="courses")
    op.drop_table("courses")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
