"""Backfill importance metadata for normalized source events.

Revision ID: 20260902_0013
Revises: 20260901_0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_0013"
down_revision: str | None = "20260901_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE normalized_race_events
        SET
            importance_level = CASE
                WHEN event_type = 'RED_FLAG' THEN 'CRITICAL'
                WHEN event_type IN ('SAFETY_CAR', 'VIRTUAL_SAFETY_CAR') THEN 'MAJOR'
                WHEN event_type IN (
                    'OVERTAKE',
                    'BATTLE_INTENSIFIED',
                    'DRS_RANGE_ENTERED',
                    'QUALIFYING_CUTOFF_CHANGE',
                    'YELLOW_FLAG',
                    'PENALTY',
                    'INVESTIGATION',
                    'SESSION_START',
                    'SESSION_FINISH'
                ) THEN 'IMPORTANT'
                WHEN event_type IN (
                    'DRIVER_UPDATE',
                    'POSITION_SAMPLE',
                    'INTERVAL_SAMPLE',
                    'CAR_DATA_SAMPLE',
                    'LOCATION_SAMPLE',
                    'WEATHER_UPDATE',
                    'LAP_COMPLETED',
                    'STINT_UPDATE',
                    'SESSION_STATUS'
                ) THEN 'LOW'
                ELSE 'NORMAL'
            END,
            importance = CASE
                WHEN event_type = 'RED_FLAG' THEN 1.0
                WHEN event_type IN ('SAFETY_CAR', 'VIRTUAL_SAFETY_CAR') THEN 0.9
                WHEN event_type IN (
                    'OVERTAKE',
                    'BATTLE_INTENSIFIED',
                    'DRS_RANGE_ENTERED',
                    'QUALIFYING_CUTOFF_CHANGE',
                    'YELLOW_FLAG',
                    'PENALTY',
                    'INVESTIGATION',
                    'SESSION_START',
                    'SESSION_FINISH'
                ) THEN 0.7
                WHEN event_type IN (
                    'PERSONAL_BEST',
                    'FASTEST_LAP',
                    'PIT_ENTRY',
                    'PIT_STOP',
                    'PIT_EXIT',
                    'ELIMINATION_RISK',
                    'PROVISIONAL_POLE'
                ) THEN 0.5
                WHEN event_type IN (
                    'DRIVER_UPDATE',
                    'POSITION_SAMPLE',
                    'INTERVAL_SAMPLE',
                    'CAR_DATA_SAMPLE',
                    'LOCATION_SAMPLE',
                    'WEATHER_UPDATE',
                    'LAP_COMPLETED',
                    'STINT_UPDATE',
                    'SESSION_STATUS'
                ) THEN 0.1
                ELSE 0.4
            END
        WHERE event_origin = 'SOURCE_FACT'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE normalized_race_events
        SET importance_level = 'LOW', importance = NULL
        WHERE event_origin = 'SOURCE_FACT'
        """
    )
