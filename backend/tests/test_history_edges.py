# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta

import pytest

from app.domain.models import RaceEventType
from app.services.race_state import RaceStateEngine
from tests.test_history_integration import START, control
from tests.test_race_history import fact
from tests.test_race_state import SnapshotRepository


@pytest.mark.asyncio
async def test_payload_only_lap_identity_reconciles_supported_interval():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(control(1, 0, neutralization="green"))
    event = fact(
        2,
        lap=None,
        lap_number=1,
        lap_duration=90,
        date_start=(START + timedelta(seconds=10)).isoformat(),
    )
    await engine.apply(event)
    lap = (await engine.export_factual_context("history-test")).history.drivers["4"].laps[0]
    assert "control_unknown" not in lap.exclusions


@pytest.mark.asyncio
async def test_other_driver_truncation_changes_global_best_authority():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(fact(1, driver=4, lap=1, lap_duration=90))
    for lap in range(1, 122):
        await engine.apply(fact(lap + 1, driver=16, lap=lap, lap_duration=91))
    driver = (await engine.get_state("history-test")).drivers["4"]
    assert driver.best_lap_availability == "partial"
    assert driver.best_lap_duration is None


@pytest.mark.asyncio
async def test_legacy_pit_view_replaces_same_driver_lap_and_limits_driver_roster():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(fact(1, RaceEventType.PIT_STOP, lap=3, pit_duration=20))
    await engine.apply(fact(2, RaceEventType.PIT_STOP, lap=3, pit_duration=21))
    state = await engine.get_state("history-test")
    assert len(state.pit_stop_history) == len(state.drivers["4"].pit_stops) == 1
    assert state.pit_stop_history[0]["pit_duration"] == 21
    for number in range(1, 66):
        await engine.apply(
            fact(number + 2, RaceEventType.PIT_STOP, driver=number, lap=4, pit_duration=20)
        )
    state = await engine.get_state("history-test")
    assert len({row["driver_number"] for row in state.pit_stop_history}) <= 64
