"""Align Race Rooms schema with PostgreSQL model metadata.

Revision ID: 20260716_0004
Revises: 20260716_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0004"
down_revision: str | None = "20260716_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("specialties", "personality", "style_rules", "supported_topics"):
        op.alter_column(
            "agent_profiles",
            column,
            type_=postgresql.JSONB(astext_type=sa.Text()),
            postgresql_using=f"{column}::jsonb",
        )
    op.alter_column(
        "message_evidence",
        "context",
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="context::jsonb",
    )
    for table, columns in {
        "agent_profiles": ("created_at", "updated_at"),
        "message_evidence": ("created_at",),
        "race_room_agents": ("joined_at",),
        "race_rooms": ("created_at", "updated_at"),
        "room_messages": ("created_at",),
        "room_playback_states": ("updated_at",),
    }.items():
        for column in columns:
            op.alter_column(table, column, nullable=False)
    op.drop_constraint("race_rooms_slug_key", "race_rooms", type_="unique")
    op.drop_index("ix_race_rooms_slug", table_name="race_rooms")
    op.create_index("ix_race_rooms_slug", "race_rooms", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_race_rooms_slug", table_name="race_rooms")
    op.create_unique_constraint("race_rooms_slug_key", "race_rooms", ["slug"])
    op.create_index("ix_race_rooms_slug", "race_rooms", ["slug"], unique=False)
    for table, columns in {
        "agent_profiles": ("created_at", "updated_at"),
        "message_evidence": ("created_at",),
        "race_room_agents": ("joined_at",),
        "race_rooms": ("created_at", "updated_at"),
        "room_messages": ("created_at",),
        "room_playback_states": ("updated_at",),
    }.items():
        for column in columns:
            op.alter_column(table, column, nullable=True)
    op.alter_column("message_evidence", "context", type_=sa.JSON())
    for column in ("specialties", "personality", "style_rules", "supported_topics"):
        op.alter_column("agent_profiles", column, type_=sa.JSON())
