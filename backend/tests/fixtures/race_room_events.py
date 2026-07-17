# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.development_fixture import day3_validation_events


def race_room_event(
    event_type: RaceEventType = RaceEventType.PIT_STOP,
    *,
    sequence: int = 1,
    lap: int = 12,
    payload: dict[str, object] | None = None,
) -> NormalizedRaceEvent:
    timestamp = datetime(2026, 7, 16, 10, tzinfo=UTC) + timedelta(seconds=sequence)
    return NormalizedRaceEvent(
        session_key="test-race-room",
        source="fixture",
        event_time=timestamp,
        received_at=timestamp,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[4, 81],
        lap_number=lap,
        payload=payload or {},
        dedup_key=f"fixture:{event_type}:{sequence}",
        is_replay=True,
    )


def ten_lap_fixture() -> list[NormalizedRaceEvent]:
    return day3_validation_events()
