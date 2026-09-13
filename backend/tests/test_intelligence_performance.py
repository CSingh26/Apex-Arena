# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.intelligence import RaceIntelligenceConfig
from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceStateEngine, SnapshotPersistResult

START = datetime(2026, 7, 19, 13, tzinfo=UTC)


class Snapshots:
    async def insert(self, snapshot: object) -> SnapshotPersistResult:
        return SnapshotPersistResult(record_id=snapshot.id, is_new=True)  # type: ignore[attr-defined]

    async def latest(self, session_key: str) -> None:
        return None


def event(
    event_type: RaceEventType,
    driver: int,
    value: float | int,
    sequence: int,
) -> NormalizedRaceEvent:
    observed_at = START + timedelta(milliseconds=sequence * 100)
    key = "position" if event_type is RaceEventType.POSITION_SAMPLE else "interval"
    return NormalizedRaceEvent(
        session_key="performance-race",
        source="openf1",
        event_time=observed_at,
        received_at=observed_at,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[driver],
        interval_seconds=float(value) if key == "interval" else None,
        payload={
            "driver_number": driver,
            key: value,
            "normalized_session_type": "RACE",
        },
        dedup_key=f"{event_type.value}:{driver}:{sequence}",
    )


@pytest.mark.asyncio
async def test_twenty_driver_interval_stream_keeps_intelligence_state_bounded() -> None:
    config = RaceIntelligenceConfig(battle_trend_window=5, battle_start_samples=3)
    race_state = RaceStateEngine(Snapshots(), snapshot_every_n_events=1_000_000)  # type: ignore[arg-type]
    coordinator = RaceIntelligenceCoordinator(race_state, config=config)
    sequence = 0

    for position in range(1, 21):
        sequence += 1
        source = event(RaceEventType.POSITION_SAMPLE, position, position, sequence)
        await race_state.consume(source)
        await coordinator.consume(source)
        coordinator.drain_derived(source.session_key)

    for cycle in range(100):
        for position in range(2, 21):
            sequence += 1
            interval = 1.85 - ((cycle + position) % 5) * 0.05
            source = event(RaceEventType.INTERVAL_SAMPLE, position, interval, sequence)
            await race_state.consume(source)
            await coordinator.consume(source)
            coordinator.drain_derived(source.session_key)

    diagnostics = coordinator.diagnostics_for_session("performance-race")
    assert diagnostics.position_states == 20
    assert diagnostics.tracked_battles == 19
    assert diagnostics.current_battles == 19
    assert diagnostics.maximum_battle_history <= config.battle_trend_window
    assert diagnostics.pending_overtakes == 0
    assert diagnostics.buffered_derived_events == 0
