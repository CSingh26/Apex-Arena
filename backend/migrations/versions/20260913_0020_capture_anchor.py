"""Separate operational capture anchor from a correctable displayed schedule."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0020"
down_revision: str | None = "20260912_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "race_rooms", sa.Column("capture_anchor_start", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("race_rooms", "capture_anchor_start")
