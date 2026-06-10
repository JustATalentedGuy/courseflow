"""phase6 quiz results

Revision ID: 0002_phase6_quiz_results
Revises: 0001_phase1_foundation
Create Date: 2026-06-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase6_quiz_results"
down_revision: Union[str, None] = "0001_phase1_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quiz_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("average_score", sa.Float(), nullable=False),
        sa.Column("weak_concepts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("results_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index(op.f("ix_quiz_results_user_id"), "quiz_results", ["user_id"], unique=False)
    op.create_index(op.f("ix_quiz_results_video_id"), "quiz_results", ["video_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quiz_results_video_id"), table_name="quiz_results")
    op.drop_index(op.f("ix_quiz_results_user_id"), table_name="quiz_results")
    op.drop_table("quiz_results")
