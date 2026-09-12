"""Add a durable room discussion generation.

Revision ID: 20260912_0018
Revises: 20260912_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0018"
down_revision: str | None = "20260912_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "race_rooms",
        sa.Column("discussion_generation", sa.BigInteger(), nullable=True),
    )
    op.execute("UPDATE race_rooms SET discussion_generation = 1")
    op.alter_column(
        "race_rooms",
        "discussion_generation",
        existing_type=sa.BigInteger(),
        nullable=False,
        server_default=sa.text("1"),
    )


def downgrade() -> None:
    op.drop_column("race_rooms", "discussion_generation")
