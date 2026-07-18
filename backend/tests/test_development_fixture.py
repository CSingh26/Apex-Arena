# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.models import RaceEventType
from app.services.development_fixture import (
    DAY3_FIXTURE_SESSION_KEY,
    DevelopmentFixtureService,
    day3_validation_events,
)
from app.services.event_pipeline import NormalizedPersistResult


def test_day3_fixture_is_deterministic_ordered_and_covers_major_discussion_paths() -> None:
    first = day3_validation_events()
    second = day3_validation_events()

    assert [event.model_dump(mode="json", exclude={"id", "processed_at"}) for event in first] == [
        event.model_dump(mode="json", exclude={"id", "processed_at"}) for event in second
    ]
    assert len(first) == 14
    assert [event.sequence_number for event in first] == list(range(1, 15))
    assert [event.event_time for event in first] == sorted(event.event_time for event in first)
    assert len({event.dedup_key for event in first}) == len(first)
    assert all(event.session_key == DAY3_FIXTURE_SESSION_KEY for event in first)
    assert all(event.is_replay and event.source == "apex_day3_fixture" for event in first)

    required = {
        RaceEventType.SESSION_START,
        RaceEventType.LAP_COMPLETED,
        RaceEventType.POSITION_CHANGE,
        RaceEventType.OVERTAKE,
        RaceEventType.FASTEST_LAP,
        RaceEventType.PIT_STOP,
        RaceEventType.TYRE_CHANGE,
        RaceEventType.YELLOW_FLAG,
        RaceEventType.WEATHER_UPDATE,
        RaceEventType.RETIREMENT,
        RaceEventType.SAFETY_CAR,
        RaceEventType.SESSION_FINISH,
    }
    assert required <= {event.event_type for event in first}
    assert max(event.lap_number or 0 for event in first) == 12
    assert {driver for event in first for driver in event.driver_numbers} == {4, 63, 81}


def test_fixture_contains_explicit_uncertainty_pace_and_non_championship_evidence() -> None:
    events = day3_validation_events()
    weather = next(event for event in events if event.event_type is RaceEventType.WEATHER_UPDATE)
    trend = next(
        event
        for event in events
        if event.event_type is RaceEventType.LAP_COMPLETED and "pace_trend_seconds" in event.payload
    )
    finish = next(event for event in events if event.event_type is RaceEventType.SESSION_FINISH)

    assert weather.payload["rainfall"] is None
    assert weather.payload["data_quality"] == "incomplete"
    assert trend.payload["pace_trend_seconds"] == -0.68
    assert len(trend.payload["representative_laps"]) == 3
    assert "no championship points" in str(finish.payload["season_context"]).lower()


class IdempotentEventRepository:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def insert(self, event: object) -> NormalizedPersistResult:
        key = event.dedup_key  # type: ignore[attr-defined]
        is_new = key not in self.keys
        self.keys.add(key)
        return NormalizedPersistResult(record_id=uuid4(), is_new=is_new)


@pytest.mark.asyncio
async def test_fixture_seeding_is_idempotent() -> None:
    repository = IdempotentEventRepository()
    fixture = DevelopmentFixtureService(repository)  # type: ignore[arg-type]

    assert await fixture.seed() == 14
    assert await fixture.seed() == 0
    assert len(repository.keys) == 14
