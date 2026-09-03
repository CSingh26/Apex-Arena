"""Backfill primary drivers for normalized source facts.

Revision ID: 20260902_0014
Revises: 20260902_0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_0014"
down_revision: str | Sequence[str] | None = "20260902_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE normalized_race_events
        SET primary_driver_number = (driver_numbers ->> 0)::integer
        WHERE
            event_origin = 'SOURCE_FACT'
            AND primary_driver_number IS NULL
            AND jsonb_array_length(driver_numbers) > 0
        """
    )


def downgrade() -> None:
    # The migration cannot distinguish its backfill from pre-existing values.
    pass
