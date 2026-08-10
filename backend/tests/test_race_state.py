# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.models import NormalizedRaceEvent, RaceEventType, RaceStateSnapshot
from app.services.race_state import RaceStateEngine, SnapshotPersistResult


class SnapshotRepository:
    def __init__(self) -> None:
        self.snapshots: list[RaceStateSnapshot] = []

    async def insert(self, snapshot: RaceStateSnapshot) -> SnapshotPersistResult:
        self.snapshots.append(snapshot)
        return SnapshotPersistResult(record_id=snapshot.id, is_new=True)

    async def latest(self, session_key: str) -> RaceStateSnapshot | None:
        matches = [snapshot for snapshot in self.snapshots if snapshot.session_key == session_key]
        return matches[-1] if matches else None

    async def count(self, session_key: str | None = None) -> int:
        if session_key is None:
            return len(self.snapshots)
        return sum(snapshot.session_key == session_key for snapshot in self.snapshots)

    async def delete_for_session(self, session_key: str) -> None:
        self.snapshots = [
            snapshot for snapshot in self.snapshots if snapshot.session_key != session_key
        ]


def event(
    event_type: RaceEventType,
    sequence: int,
    payload: dict[str, object],
    *,
    driver_number: int | None = 4,
    lap_number: int | None = None,
    dedup_key: str | None = None,
) -> NormalizedRaceEvent:
    now = datetime(2026, 7, 19, 13, sequence, tzinfo=UTC)
    return NormalizedRaceEvent(
        session_key="spa-race",
        source="openf1_historical",
        raw_event_id=uuid4(),
        event_time=now,
        received_at=now,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[driver_number] if driver_number is not None else [],
        lap_number=lap_number,
        payload=payload,
        dedup_key=dedup_key or f"event-{sequence}",
        is_replay=True,
    )


@pytest.mark.asyncio
async def test_position_lap_and_interval_update_driver_state() -> None:
    engine = RaceStateEngine(SnapshotRepository())

    await engine.apply(event(RaceEventType.POSITION_SAMPLE, 1, {"position": 2}))
    await engine.apply(
        event(
            RaceEventType.INTERVAL_SAMPLE,
            2,
            {"gap_to_leader": 1.4, "interval": 0.6},
        )
    )
    state = await engine.apply(
        event(RaceEventType.LAP_COMPLETED, 3, {"lap_number": 12}, lap_number=12)
    )

    assert state.drivers["4"].position == 2
    assert state.drivers["4"].gap_to_leader == 1.4
    assert state.current_lap == 12
    assert state.sequence_number == 3


@pytest.mark.asyncio
async def test_driver_profiles_can_be_primed_without_advancing_replay_state() -> None:
    engine = RaceStateEngine(SnapshotRepository())
    profile = event(
        RaceEventType.DRIVER_UPDATE,
        59,
        {
            "resolved_driver_name": "George RUSSELL",
            "resolved_broadcast_name": "G RUSSELL",
            "resolved_team_name": "Mercedes",
        },
        driver_number=63,
    )

    primed = await engine.prime_driver_profiles("spa-race", [profile])
    state = await engine.apply(
        event(RaceEventType.POSITION_SAMPLE, 1, {"position": 1}, driver_number=63)
    )

    assert primed.sequence_number == 0
    assert state.sequence_number == 1
    assert state.drivers["63"].full_name == "George RUSSELL"
    assert state.drivers["63"].team_name == "Mercedes"


@pytest.mark.asyncio
async def test_pit_control_and_weather_updates_are_applied() -> None:
    engine = RaceStateEngine(SnapshotRepository())

    await engine.apply(event(RaceEventType.PIT_STOP, 1, {"lap_number": 13}))
    await engine.apply(
        event(
            RaceEventType.YELLOW_FLAG,
            2,
            {"flag": "YELLOW", "message": "YELLOW IN SECTOR 2"},
            driver_number=None,
        )
    )
    state = await engine.apply(
        event(
            RaceEventType.WEATHER_UPDATE,
            3,
            {"track_temperature": 31.2, "rainfall": 0},
            driver_number=None,
        )
    )

    assert len(state.pit_stop_history) == 1
    assert state.race_control_state["event_type"] == "YELLOW_FLAG"
    assert state.weather["track_temperature"] == 31.2


@pytest.mark.asyncio
async def test_repeated_event_is_not_applied_twice() -> None:
    engine = RaceStateEngine(SnapshotRepository())
    pit = event(RaceEventType.PIT_STOP, 1, {"lap_number": 13}, dedup_key="same-pit")

    await engine.apply(pit)
    state = await engine.apply(pit)

    assert len(state.pit_stop_history) == 1


@pytest.mark.asyncio
async def test_high_frequency_samples_keep_only_valid_latest_values() -> None:
    engine = RaceStateEngine(SnapshotRepository())

    await engine.apply(
        event(
            RaceEventType.CAR_DATA_SAMPLE,
            1,
            {"speed": 312.4, "throttle": 87, "brake": 0, "n_gear": 7, "rpm": 11_420, "drs": 12},
        )
    )
    state = await engine.apply(
        event(
            RaceEventType.LOCATION_SAMPLE,
            2,
            {"x": 134.2, "y": -22.7, "z": 1.8},
        )
    )

    driver = state.drivers["4"]
    assert driver.telemetry == {
        "speed": 312.4,
        "throttle": 87.0,
        "brake": 0.0,
        "gear": 7,
        "rpm": 11_420,
        "drs": True,
    }
    assert driver.location == {"x": 134.2, "y": -22.7, "z": 1.8}
    assert state.has_telemetry is True
    assert state.has_locations is True


@pytest.mark.asyncio
async def test_invalid_or_stale_high_frequency_samples_do_not_replace_state() -> None:
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(event(RaceEventType.CAR_DATA_SAMPLE, 2, {"speed": 300}))
    state = await engine.apply(
        event(RaceEventType.CAR_DATA_SAMPLE, 1, {"speed": 999}, dedup_key="stale")
    )

    assert state.sequence_number == 2
    assert state.drivers["4"].telemetry == {"speed": 300.0}


@pytest.mark.asyncio
async def test_snapshot_is_persisted_on_configured_interval() -> None:
    snapshots = SnapshotRepository()
    engine = RaceStateEngine(snapshots, snapshot_every_n_events=2)

    await engine.apply(event(RaceEventType.POSITION_SAMPLE, 1, {"position": 2}))
    await engine.apply(event(RaceEventType.LAP_COMPLETED, 2, {"lap_number": 1}, lap_number=1))

    assert await snapshots.count("spa-race") == 1
    snapshot = await snapshots.latest("spa-race")
    assert snapshot is not None
    assert snapshot.current_lap == 1
    assert snapshot.sequence_number == 2


@pytest.mark.asyncio
async def test_reset_clears_cached_dedup_state_and_persisted_snapshots() -> None:
    snapshots = SnapshotRepository()
    engine = RaceStateEngine(snapshots, snapshot_every_n_events=1)
    pit = event(RaceEventType.PIT_STOP, 1, {"lap_number": 3}, dedup_key="pit-once")

    await engine.apply(pit)
    assert await snapshots.count("spa-race") == 1
    assert len((await engine.get_state("spa-race")).pit_stop_history) == 1

    await engine.reset_session("spa-race")

    assert await snapshots.count("spa-race") == 0
    assert (await engine.get_state("spa-race")).sequence_number == 0
    replayed = await engine.apply(pit)
    assert len(replayed.pit_stop_history) == 1
