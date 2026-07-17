# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.storage.repositories import SqlNormalizedEventRepository

DAY3_FIXTURE_SESSION_KEY = "day3-validation"


def day3_validation_events() -> list[NormalizedRaceEvent]:
    """A deterministic, explicitly synthetic race used only outside production."""
    start = datetime(2026, 7, 17, 10, tzinfo=UTC)
    specs: list[tuple[RaceEventType, int | None, list[int], dict[str, object]]] = [
        (RaceEventType.SESSION_START, 0, [4, 81, 63], {"status": "started"}),
        (
            RaceEventType.LAP_COMPLETED,
            1,
            [4],
            {"lap_duration": 91.8, "position": 6, "data_quality": "complete"},
        ),
        (
            RaceEventType.POSITION_CHANGE,
            2,
            [81],
            {"previous_position": 7, "position": 6, "cause": "start_phase"},
        ),
        (
            RaceEventType.OVERTAKE,
            3,
            [4, 81],
            {"previous_position": 6, "position": 5, "overtaken_driver_number": 81},
        ),
        (
            RaceEventType.LAP_COMPLETED,
            4,
            [4],
            {"lap_duration": 91.1, "position": 5, "data_quality": "complete"},
        ),
        (
            RaceEventType.FASTEST_LAP,
            5,
            [63],
            {"lap_duration": 89.94, "previous_best": 90.31, "data_quality": "complete"},
        ),
        (
            RaceEventType.PIT_STOP,
            6,
            [4],
            {"pit_duration": 2.41, "position": 8, "traffic_gap": 4.8},
        ),
        (
            RaceEventType.TYRE_CHANGE,
            6,
            [4],
            {"compound": "MEDIUM", "tyre_age_at_start": 0},
        ),
        (
            RaceEventType.YELLOW_FLAG,
            7,
            [4, 81, 63],
            {"message": "Yellow flag in sector two", "flag": "YELLOW"},
        ),
        (
            RaceEventType.WEATHER_UPDATE,
            8,
            [4, 81, 63],
            {"air_temperature": 23.4, "rainfall": None, "data_quality": "incomplete"},
        ),
        (
            RaceEventType.LAP_COMPLETED,
            9,
            [4],
            {
                "lap_duration": 90.42,
                "representative_laps": [91.1, 90.8, 90.42],
                "pace_trend_seconds": -0.68,
                "data_quality": "complete",
            },
        ),
        (
            RaceEventType.RETIREMENT,
            10,
            [81],
            {"status": "retired", "reason": "not supplied", "data_quality": "partial"},
        ),
        (
            RaceEventType.SAFETY_CAR,
            11,
            [4, 63],
            {"message": "Safety car deployed", "flag": "SAFETY_CAR"},
        ),
        (
            RaceEventType.SESSION_FINISH,
            12,
            [4, 63, 81],
            {
                "classification": [4, 63],
                "season_context": "Synthetic validation fixture; no championship points apply.",
                "data_quality": "fixture",
            },
        ),
    ]
    events: list[NormalizedRaceEvent] = []
    for sequence, (event_type, lap, drivers, payload) in enumerate(specs, start=1):
        event_time = start + timedelta(seconds=sequence * 30)
        events.append(
            NormalizedRaceEvent(
                session_key=DAY3_FIXTURE_SESSION_KEY,
                source="apex_day3_fixture",
                event_time=event_time,
                received_at=event_time,
                sequence_number=sequence,
                event_type=event_type,
                driver_numbers=drivers,
                lap_number=lap,
                importance=0.9
                if event_type in {RaceEventType.SAFETY_CAR, RaceEventType.SESSION_FINISH}
                else 0.7,
                confidence=1.0,
                payload=payload,
                dedup_key=f"day3-fixture:{sequence}:{event_type.value}",
                is_replay=True,
            )
        )
    return events


class DevelopmentFixtureService:
    def __init__(self, events: SqlNormalizedEventRepository) -> None:
        self.events = events

    async def seed(self) -> int:
        inserted = 0
        for event in day3_validation_events():
            result = await self.events.insert(event)
            inserted += int(result.is_new)
        return inserted
