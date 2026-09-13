"""Retain explicit catalog cancellation for the provider session identity."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0021"
down_revision: str | None = "20260913_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "race_rooms",
        sa.Column("provider_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("race_rooms", "provider_cancelled")
