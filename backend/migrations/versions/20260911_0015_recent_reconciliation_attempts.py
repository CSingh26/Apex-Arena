"""Track durable recent-session reconciliation attempts.

Revision ID: 20260911_0015
Revises: 20260902_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0015"
down_revision: str | Sequence[str] | None = "20260902_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "race_rooms",
        sa.Column("reconciliation_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_race_rooms_reconciliation_attempted_at",
        "race_rooms",
        ["reconciliation_attempted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_race_rooms_reconciliation_attempted_at", table_name="race_rooms")
    op.drop_column("race_rooms", "reconciliation_attempted_at")
