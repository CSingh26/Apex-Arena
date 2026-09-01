# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.intelligence import RaceIntelligenceConfig
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceStateEngine, SnapshotPersistResult

START = datetime(2026, 7, 19, 13, tzinfo=UTC)


class Snapshots:
    async def insert(self, snapshot: object) -> SnapshotPersistResult:
        return SnapshotPersistResult(record_id=snapshot.id, is_new=True)  # type: ignore[attr-defined]

    async def latest(self, session_key: str) -> None:
        return None


class BattleSummaries:
    def __init__(self) -> None:
        self.saved: list[object] = []

    async def upsert_resolved(self, battle: object) -> None:
        self.saved.append(battle)

    async def count(self, session_key: str | None = None) -> int:
        return 0

    async def delete_for_session(self, session_key: str) -> None:
        return None


def source_event(
    event_type: RaceEventType,
    *,
    driver: int,
    second: int,
    sequence: int,
    position: int | None = None,
    interval: float | None = None,
) -> NormalizedRaceEvent:
    observed_at = START + timedelta(seconds=second)
    payload: dict[str, object] = {
        "driver_number": driver,
        "normalized_session_type": "RACE",
    }
    if position is not None:
        payload["position"] = position
    if interval is not None:
        payload["interval"] = interval
    return NormalizedRaceEvent(
        session_key="race",
        source="openf1",
        event_time=observed_at,
        received_at=observed_at,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[driver],
        interval_seconds=interval,
        payload=payload,
        dedup_key=f"{event_type.value}:{driver}:{sequence}",
    )


async def consume(
    state: RaceStateEngine,
    coordinator: RaceIntelligenceCoordinator,
    event: NormalizedRaceEvent,
) -> list[NormalizedRaceEvent]:
    await state.consume(event)
    await coordinator.consume(event)
    return coordinator.drain_derived(event.session_key)


@pytest.mark.asyncio
async def test_coordinator_enriches_current_battle_state_with_bounded_history() -> None:
    race_state = RaceStateEngine(Snapshots(), snapshot_every_n_events=100)  # type: ignore[arg-type]
    coordinator = RaceIntelligenceCoordinator(
        race_state,
        config=RaceIntelligenceConfig(battle_start_samples=3, battle_trend_window=5),
    )
    await consume(
        race_state,
        coordinator,
        source_event(RaceEventType.POSITION_SAMPLE, driver=16, position=4, second=0, sequence=1),
    )
    await consume(
        race_state,
        coordinator,
        source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=5, second=0, sequence=2),
    )
    derived: list[NormalizedRaceEvent] = []
    for index, gap in enumerate((1.8, 1.7, 1.6), start=3):
        derived.extend(
            await consume(
                race_state,
                coordinator,
                source_event(
                    RaceEventType.INTERVAL_SAMPLE,
                    driver=4,
                    interval=gap,
                    second=index,
                    sequence=index,
                ),
            )
        )

    state = await race_state.get_state("race")
    assert len(state.current_battles) == 1
    assert state.current_battles[0].chasing_driver_number == 4
    assert len(state.current_battles[0].interval_history) <= 5
    assert RaceEventType.BATTLE_STARTED in {event.event_type for event in derived}


@pytest.mark.asyncio
async def test_coordinator_confirms_overtake_on_later_source_sample_and_never_rederives() -> None:
    race_state = RaceStateEngine(Snapshots(), snapshot_every_n_events=100)  # type: ignore[arg-type]
    coordinator = RaceIntelligenceCoordinator(
        race_state,
        config=RaceIntelligenceConfig(
            overtake_confirmation_seconds=2,
            overtake_confirmation_samples=2,
        ),
    )
    setup = [
        source_event(RaceEventType.POSITION_SAMPLE, driver=16, position=4, second=0, sequence=1),
        source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=5, second=0, sequence=2),
        source_event(RaceEventType.INTERVAL_SAMPLE, driver=4, interval=0.7, second=1, sequence=3),
        source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=4, second=2, sequence=4),
        source_event(RaceEventType.POSITION_SAMPLE, driver=16, position=5, second=2, sequence=5),
    ]
    for item in setup:
        await consume(race_state, coordinator, item)

    confirmed = await consume(
        race_state,
        coordinator,
        source_event(RaceEventType.INTERVAL_SAMPLE, driver=16, interval=0.8, second=4, sequence=6),
    )
    overtake = next(event for event in confirmed if event.event_type is RaceEventType.OVERTAKE)
    assert overtake.primary_driver_number == 4
    assert overtake.secondary_driver_number == 16

    derived_input = overtake.model_copy(
        update={"sequence_number": 7, "event_origin": EventOrigin.DERIVED}
    )
    await race_state.consume(derived_input)
    await coordinator.consume(derived_input)
    assert coordinator.drain_derived("race") == []
    final_state = await race_state.get_state("race")
    assert final_state.recent_events[-1].event_type is RaceEventType.OVERTAKE
    assert len(final_state.recent_events) <= 20


@pytest.mark.asyncio
async def test_resolved_battle_is_persisted_once_as_summary() -> None:
    race_state = RaceStateEngine(Snapshots(), snapshot_every_n_events=100)  # type: ignore[arg-type]
    summaries = BattleSummaries()
    coordinator = RaceIntelligenceCoordinator(
        race_state,
        config=RaceIntelligenceConfig(battle_start_samples=3),
        battle_summaries=summaries,
    )
    setup = [
        source_event(RaceEventType.POSITION_SAMPLE, driver=16, position=4, second=0, sequence=1),
        source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=5, second=0, sequence=2),
        source_event(RaceEventType.INTERVAL_SAMPLE, driver=4, interval=1.8, second=1, sequence=3),
        source_event(RaceEventType.INTERVAL_SAMPLE, driver=4, interval=1.7, second=2, sequence=4),
        source_event(RaceEventType.INTERVAL_SAMPLE, driver=4, interval=1.6, second=3, sequence=5),
    ]
    for item in setup:
        await consume(race_state, coordinator, item)

    pit = source_event(RaceEventType.PIT_ENTRY, driver=4, second=4, sequence=6)
    await consume(race_state, coordinator, pit)
    await coordinator.consume(pit)

    assert len(summaries.saved) == 1
    resolved = summaries.saved[0]
    assert resolved.status.value == "RESOLVED"  # type: ignore[attr-defined]
    assert resolved.resolution_reason == "PIT_ENTRY"  # type: ignore[attr-defined]
