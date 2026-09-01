# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models import (
    EventConfidence,
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.services.event_importance import EventImportancePolicy

START = datetime(2026, 7, 19, 13, tzinfo=UTC)


def race_event(
    event_type: RaceEventType,
    *,
    second: int = 0,
    position_after: int | None = None,
    confidence: EventConfidence = EventConfidence.HIGH,
) -> NormalizedRaceEvent:
    observed_at = START + timedelta(seconds=second)
    return NormalizedRaceEvent(
        session_key="race",
        source="apexarena" if event_type is not RaceEventType.INTERVAL_SAMPLE else "openf1",
        event_origin=(
            EventOrigin.SOURCE_FACT
            if event_type is RaceEventType.INTERVAL_SAMPLE
            else EventOrigin.DERIVED
        ),
        event_time=observed_at,
        received_at=observed_at,
        event_type=event_type,
        primary_driver_number=4,
        secondary_driver_number=16,
        position_after=position_after,
        confidence_level=confidence,
        dedup_key=f"{event_type.value}:{second}",
    )


def test_routine_samples_are_low_and_agent_ineligible() -> None:
    level, score, eligible = EventImportancePolicy().classify(
        race_event(RaceEventType.INTERVAL_SAMPLE)
    )
    assert (level, score, eligible) == (EventImportance.LOW, 0.1, False)


def test_confirmed_overtake_is_important_and_lead_change_is_major() -> None:
    policy = EventImportancePolicy()
    assert policy.classify(race_event(RaceEventType.OVERTAKE, position_after=4)) == (
        EventImportance.IMPORTANT,
        0.82,
        True,
    )
    assert policy.classify(race_event(RaceEventType.OVERTAKE, position_after=1)) == (
        EventImportance.MAJOR,
        0.92,
        True,
    )


def test_low_confidence_inference_is_not_agent_eligible() -> None:
    level, score, eligible = EventImportancePolicy().classify(
        race_event(RaceEventType.OVERTAKE, confidence=EventConfidence.LOW)
    )
    assert level is EventImportance.LOW
    assert score == 0.2
    assert eligible is False


def test_red_flag_is_critical_and_bypasses_repetition_cooldown() -> None:
    policy = EventImportancePolicy(cooldown_seconds=20)
    first = race_event(RaceEventType.RED_FLAG, second=0)
    repeated = race_event(RaceEventType.RED_FLAG, second=1)

    assert policy.should_emit(first)
    assert policy.should_emit(repeated)
    assert policy.classify(first) == (EventImportance.CRITICAL, 1.0, True)


def test_informational_repetition_is_cooled_down() -> None:
    policy = EventImportancePolicy(cooldown_seconds=20)
    first = race_event(RaceEventType.ELIMINATION_RISK, second=0)
    repeated = race_event(RaceEventType.ELIMINATION_RISK, second=1)
    later = race_event(RaceEventType.ELIMINATION_RISK, second=21)

    assert policy.should_emit(first)
    assert not policy.should_emit(repeated)
    assert policy.should_emit(later)
