# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded telemetry reads, honest availability and missing-channel handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.models import (
    EventConfidence,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.domain.telemetry import MAX_SAMPLES_PER_DRIVER, MAX_SCANNED_EVENTS
from app.services.telemetry_history import (
    TelemetryHistoryService,
    TelemetrySelectionError,
)

START = datetime(2026, 7, 17, 12, tzinfo=UTC)


def car_data(
    sequence: int,
    driver: int,
    *,
    lap: int | None = 5,
    payload: dict | None = None,
) -> NormalizedRaceEvent:
    return NormalizedRaceEvent(
        id=uuid4(),
        session_key="race",
        source="openf1",
        event_origin=EventOrigin.SOURCE_FACT,
        event_type=RaceEventType.CAR_DATA_SAMPLE,
        event_time=START + timedelta(seconds=sequence),
        received_at=START + timedelta(seconds=sequence),
        sequence_number=sequence,
        driver_numbers=[driver],
        primary_driver_number=driver,
        lap_number=lap,
        confidence=1.0,
        confidence_level=EventConfidence.HIGH,
        payload=payload
        if payload is not None
        else {"speed": 280.0, "throttle": 100.0, "brake": 0.0, "rpm": 11000, "gear": 7},
        dedup_key=f"car:{driver}:{sequence}",
    )


class FakeEvents:
    """Applies the same filters the SQL repository applies."""

    def __init__(self, events: list[NormalizedRaceEvent]) -> None:
        self.events = events
        self.queries: list[dict] = []

    async def list_for_session(self, session_key, **kwargs):
        self.queries.append(kwargs)
        driver = kwargs.get("driver_number")
        lap = kwargs.get("lap_number")
        before = kwargs.get("before_sequence")
        limit = kwargs.get("limit", 100)
        rows = [
            event
            for event in self.events
            if event.session_key == session_key
            and (driver is None or driver in event.driver_numbers)
            and (lap is None or event.lap_number == lap)
            and (before is None or event.sequence_number <= before)
        ]
        return sorted(rows, key=lambda event: event.sequence_number)[:limit]


def service(events, *, sequence: int = 10_000) -> TelemetryHistoryService:
    states = SimpleNamespace(
        get_state=lambda key: _state(sequence),
    )
    return TelemetryHistoryService(FakeEvents(events), states)


async def _state(sequence: int):
    return SimpleNamespace(sequence_number=sequence)


# --- Selection bounds -------------------------------------------------------


@pytest.mark.parametrize("drivers", [[], [1, 2, 3], [0]])
async def test_unsupported_selections_are_refused(drivers):
    with pytest.raises(TelemetrySelectionError):
        await service([]).read("race", drivers=drivers)


async def test_duplicate_driver_numbers_collapse_to_one_series():
    events = [car_data(1, 4), car_data(2, 4)]
    window = await service(events).read("race", drivers=[4, 4])
    assert [driver.driver_number for driver in window.drivers] == [4]


# --- Honest availability ----------------------------------------------------


async def test_a_session_with_no_telemetry_says_so_rather_than_returning_empty():
    window = await service([]).read("race", drivers=[4])
    assert window.availability == "unavailable"
    assert window.reason == "no_telemetry_retained"
    assert window.drivers[0].samples == []
    assert window.drivers[0].channels == []


async def test_a_lap_with_no_telemetry_is_distinguished_from_a_bare_session():
    window = await service([car_data(1, 4, lap=5)]).read("race", drivers=[4], lap_number=9)
    assert window.availability == "unavailable"
    assert window.reason == "no_telemetry_for_lap"


async def test_one_driver_missing_telemetry_is_reported_as_partial():
    window = await service([car_data(1, 4)]).read("race", drivers=[4, 16])
    assert window.availability == "partial"
    assert window.reason == "telemetry_missing_for_some_drivers"
    missing = next(d for d in window.drivers if d.driver_number == 16)
    assert missing.samples == []
    assert missing.channels == []


async def test_both_drivers_populated_reads_as_available():
    window = await service([car_data(1, 4), car_data(2, 16)]).read("race", drivers=[4, 16])
    assert window.availability == "available"
    assert window.reason is None
    assert {driver.driver_number for driver in window.drivers} == {4, 16}


# --- Channel honesty --------------------------------------------------------


async def test_channels_list_only_what_the_provider_actually_published():
    events = [car_data(1, 4, payload={"speed": 300.0, "rpm": 11500})]
    window = await service(events).read("race", drivers=[4])
    driver = window.drivers[0]
    assert driver.channels == ["speed", "rpm"]
    # A channel with no data stays null; it must never be charted as zero.
    assert driver.samples[0].brake is None
    assert driver.samples[0].throttle is None


async def test_a_sample_with_no_usable_channel_is_discarded():
    window = await service([car_data(1, 4, payload={"unrelated": 1})]).read("race", drivers=[4])
    assert window.drivers[0].samples == []


async def test_drs_is_carried_only_when_it_is_a_real_boolean():
    decoded = await service([car_data(1, 4, payload={"speed": 250.0, "drs": True})]).read(
        "race", drivers=[4]
    )
    assert decoded.drivers[0].samples[0].drs is True
    assert "drs" in decoded.drivers[0].channels

    raw_code = await service([car_data(2, 4, payload={"speed": 250.0, "drs": 12})]).read(
        "race", drivers=[4]
    )
    # An undecoded provider code is not a DRS state.
    assert raw_code.drivers[0].samples[0].drs is None
    assert "drs" not in raw_code.drivers[0].channels


async def test_units_accompany_every_published_channel():
    window = await service([car_data(1, 4)]).read("race", drivers=[4])
    assert window.units["speed"] == "km/h"
    assert window.units["rpm"] == "rpm"
    assert set(window.drivers[0].channels) <= set(window.units)


# --- Bounds -----------------------------------------------------------------


async def test_series_is_truncated_to_the_most_recent_window():
    events = [car_data(index, 4) for index in range(1, MAX_SAMPLES_PER_DRIVER + 51)]
    window = await service(events).read("race", drivers=[4])
    driver = window.drivers[0]
    assert len(driver.samples) == MAX_SAMPLES_PER_DRIVER
    assert driver.samples_truncated is True
    # The tail is what a comparison is asking about.
    assert driver.samples[-1].sequence == MAX_SAMPLES_PER_DRIVER + 50


async def test_reads_never_pass_the_consumed_cursor():
    events = [car_data(5, 4), car_data(500, 4)]
    window = await service(events, sequence=100).read("race", drivers=[4])
    assert [sample.sequence for sample in window.drivers[0].samples] == [5]
    assert window.view_sequence == 100


async def test_a_requested_cursor_cannot_exceed_acknowledged_progress():
    events = [car_data(5, 4), car_data(500, 4)]
    window = await service(events, sequence=100).read("race", drivers=[4], cursor=400)
    assert window.view_sequence == 100


async def test_hitting_the_scan_ceiling_is_reported_rather_than_hidden():
    events = [car_data(index, 4) for index in range(1, MAX_SCANNED_EVENTS + 1)]
    window = await service(events).read("race", drivers=[4])
    assert window.scan_limited is True
    assert window.availability == "partial"
    assert window.reason == "scan_limit_reached"
