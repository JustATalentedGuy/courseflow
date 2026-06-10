"""phase7 spaced repetition

Revision ID: 0003_phase7_spaced_repetition
Revises: 0002_phase6_quiz_results
Create Date: 2026-06-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase7_spaced_repetition"
down_revision: Union[str, None] = "0002_phase6_quiz_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "concept_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept", sa.String(), nullable=False),
        sa.Column("ease_factor", sa.Float(), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("next_review_date", sa.Date(), nullable=False),
        sa.Column("last_score", sa.Float(), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "video_id",
            "concept",
            name="uq_concept_cards_owner_video_concept",
        ),
    )
    op.create_index(op.f("ix_concept_cards_user_id"), "concept_cards", ["user_id"], unique=False)
    op.create_index(op.f("ix_concept_cards_video_id"), "concept_cards", ["video_id"], unique=False)
    op.create_index(
        "ix_concept_cards_user_due",
        "concept_cards",
        ["user_id", "next_review_date"],
        unique=False,
    )

    op.create_table(
        "concept_review_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("concept", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["concept_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_result_id"], ["quiz_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "quiz_result_id",
            "concept",
            name="uq_concept_review_events_quiz_result_concept",
        ),
    )
    op.create_index(
        op.f("ix_concept_review_events_card_id"),
        "concept_review_events",
        ["card_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_concept_review_events_quiz_result_id"),
        "concept_review_events",
        ["quiz_result_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_concept_review_events_user_id"),
        "concept_review_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_concept_review_events_video_id"),
        "concept_review_events",
        ["video_id"],
        unique=False,
    )
    op.create_index(
        "ix_concept_review_events_user_reviewed",
        "concept_review_events",
        ["user_id", "reviewed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_concept_review_events_user_reviewed", table_name="concept_review_events")
    op.drop_index(op.f("ix_concept_review_events_video_id"), table_name="concept_review_events")
    op.drop_index(op.f("ix_concept_review_events_user_id"), table_name="concept_review_events")
    op.drop_index(op.f("ix_concept_review_events_quiz_result_id"), table_name="concept_review_events")
    op.drop_index(op.f("ix_concept_review_events_card_id"), table_name="concept_review_events")
    op.drop_table("concept_review_events")
    op.drop_index("ix_concept_cards_user_due", table_name="concept_cards")
    op.drop_index(op.f("ix_concept_cards_video_id"), table_name="concept_cards")
    op.drop_index(op.f("ix_concept_cards_user_id"), table_name="concept_cards")
    op.drop_table("concept_cards")
