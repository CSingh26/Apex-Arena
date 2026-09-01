# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from app.domain.intelligence import BattleState
from app.storage.database import Database
from app.storage.models import BattleSummaryRecord


class SqlBattleSummaryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert_resolved(self, battle: BattleState) -> None:
        values = {
            "battle_key": battle.id,
            "session_key": battle.session_key,
            "lead_driver_number": battle.lead_driver_number,
            "chasing_driver_number": battle.chasing_driver_number,
            "lead_position": battle.lead_position,
            "chasing_position": battle.chasing_position,
            "started_at": battle.started_at,
            "ended_at": battle.last_updated_at,
            "closest_interval_seconds": battle.closest_interval_seconds,
            "peak_intensity": battle.intensity.value,
            "outcome": battle.resolution_reason,
            "context": {
                "trend": battle.trend.value,
                "train_size": battle.train_size,
                "tyre_context": battle.tyre_context,
            },
        }
        statement = insert(BattleSummaryRecord).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[BattleSummaryRecord.battle_key],
            set_={key: value for key, value in values.items() if key != "battle_key"},
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()
