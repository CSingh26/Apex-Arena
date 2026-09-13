"""Bounded per-generation agent claim memory."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260913_0023"
down_revision: str | None = "20260913_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "agent_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("discussion_generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("agent_id", sa.String(40), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="standing"),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="undecided"),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("lap_number", sa.Integer(), nullable=True),
        sa.Column("subjects", JSON_TYPE, nullable=False),
        sa.Column("summary", sa.String(400), nullable=False),
        sa.Column("evidence_keys", JSON_TYPE, nullable=False),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["room_id"], ["race_rooms.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agent_profiles.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["room_messages.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["agent_claims.id"]),
    )
    op.create_index("ix_agent_claims_room_id", "agent_claims", ["room_id"])
    op.create_index("ix_agent_claims_agent_id", "agent_claims", ["agent_id"])
    op.create_index("ix_agent_claims_room_agent", "agent_claims", ["room_id", "agent_id"])
    # Recall always filters by room and generation and cuts at the consumed
    # cursor, so the retained order is part of the index rather than a sort.
    op.create_index(
        "ix_agent_claims_recall",
        "agent_claims",
        ["room_id", "discussion_generation", "source_sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_claims_recall", table_name="agent_claims")
    op.drop_index("ix_agent_claims_room_agent", table_name="agent_claims")
    op.drop_index("ix_agent_claims_agent_id", table_name="agent_claims")
    op.drop_index("ix_agent_claims_room_id", table_name="agent_claims")
    op.drop_table("agent_claims")
