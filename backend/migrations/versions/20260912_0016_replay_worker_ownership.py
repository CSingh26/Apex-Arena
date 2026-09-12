"""Fence replay workers with durable, expiring ownership.

Revision ID: 20260912_0016
Revises: 20260911_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0016"
down_revision: str | Sequence[str] | None = "20260911_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("room_playback_states", sa.Column("replay_owner_token", sa.Uuid(), nullable=True))
    op.add_column(
        "room_playback_states",
        sa.Column("replay_owner_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("room_playback_states", "replay_owner_expires_at")
    op.drop_column("room_playback_states", "replay_owner_token")
