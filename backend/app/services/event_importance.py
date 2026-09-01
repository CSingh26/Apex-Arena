# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime

from app.domain.models import (
    EventConfidence,
    EventImportance,
    NormalizedRaceEvent,
    RaceEventType,
)

ROUTINE_TYPES = {
    RaceEventType.DRIVER_UPDATE,
    RaceEventType.POSITION_SAMPLE,
    RaceEventType.INTERVAL_SAMPLE,
    RaceEventType.CAR_DATA_SAMPLE,
    RaceEventType.LOCATION_SAMPLE,
    RaceEventType.WEATHER_UPDATE,
}
CRITICAL_TYPES = {RaceEventType.RED_FLAG}
IMPORTANT_TYPES = {
    RaceEventType.OVERTAKE,
    RaceEventType.BATTLE_INTENSIFIED,
    RaceEventType.DRS_RANGE_ENTERED,
    RaceEventType.QUALIFYING_CUTOFF_CHANGE,
}
BYPASS_COOLDOWN_TYPES = {RaceEventType.RED_FLAG, RaceEventType.OVERTAKE}


class EventImportancePolicy:
    def __init__(self, *, cooldown_seconds: float = 20) -> None:
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._last_emitted: dict[tuple[str, RaceEventType, tuple[int, ...]], datetime] = {}

    def classify(
        self, event: NormalizedRaceEvent
    ) -> tuple[EventImportance, float, bool]:
        if event.event_origin.value == "DERIVED" and event.confidence_level is EventConfidence.LOW:
            return EventImportance.LOW, 0.2, False
        if event.event_type in CRITICAL_TYPES:
            return EventImportance.CRITICAL, 1.0, True
        if event.event_type is RaceEventType.OVERTAKE and event.position_after == 1:
            return EventImportance.MAJOR, 0.92, True
        if event.event_type is RaceEventType.OVERTAKE:
            return EventImportance.IMPORTANT, 0.82, True
        if event.event_type in IMPORTANT_TYPES:
            return EventImportance.IMPORTANT, 0.7, True
        if event.event_type in ROUTINE_TYPES:
            return EventImportance.LOW, 0.1, False
        if event.event_type in {
            RaceEventType.PERSONAL_BEST,
            RaceEventType.FASTEST_LAP,
            RaceEventType.PIT_ENTRY,
            RaceEventType.PIT_STOP,
            RaceEventType.PIT_EXIT,
            RaceEventType.ELIMINATION_RISK,
            RaceEventType.PROVISIONAL_POLE,
        }:
            return EventImportance.NORMAL, 0.5, False
        return EventImportance.NORMAL, 0.4, False

    def should_emit(self, event: NormalizedRaceEvent) -> bool:
        if event.event_type in BYPASS_COOLDOWN_TYPES:
            return True
        key = (event.session_key, event.event_type, tuple(event.driver_numbers))
        previous = self._last_emitted.get(key)
        if previous is not None:
            elapsed = (event.event_time - previous).total_seconds()
            if elapsed < self.cooldown_seconds:
                return False
        self._last_emitted[key] = event.event_time
        return True
