"""Remove the retired synthetic Validation Room schema flag.

Revision ID: 20260808_0010
Revises: 20260720_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0010"
down_revision: str | None = "20260720_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Synthetic rooms never represented real sessions and may have persisted
    # from a local development environment. Remove dependent rows first because
    # the legacy foreign keys do not cascade.
    development_rooms = "SELECT id FROM race_rooms WHERE is_development IS TRUE"
    development_messages = f"SELECT id FROM room_messages WHERE room_id IN ({development_rooms})"
    op.execute(
        sa.text(f"DELETE FROM message_evidence WHERE message_id IN ({development_messages})")
    )
    op.execute(sa.text(f"DELETE FROM room_messages WHERE room_id IN ({development_rooms})"))
    op.execute(sa.text(f"DELETE FROM race_room_agents WHERE room_id IN ({development_rooms})"))
    op.execute(sa.text(f"DELETE FROM room_playback_states WHERE room_id IN ({development_rooms})"))
    op.execute(sa.text("DELETE FROM race_rooms WHERE is_development IS TRUE"))
    op.drop_column("race_rooms", "is_development")


def downgrade() -> None:
    op.add_column(
        "race_rooms",
        sa.Column("is_development", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
