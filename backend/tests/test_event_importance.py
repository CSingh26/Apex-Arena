# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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


@pytest.mark.parametrize(
    "event_type",
    [
        RaceEventType.INTERVAL_SAMPLE,
        RaceEventType.LAP_COMPLETED,
        RaceEventType.STINT_UPDATE,
        RaceEventType.SESSION_STATUS,
    ],
)
def test_routine_samples_are_low_and_agent_ineligible(event_type: RaceEventType) -> None:
    level, score, eligible = EventImportancePolicy().classify(
        race_event(event_type)
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


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        (RaceEventType.SAFETY_CAR, (EventImportance.MAJOR, 0.9, True)),
        (RaceEventType.VIRTUAL_SAFETY_CAR, (EventImportance.MAJOR, 0.9, True)),
        (RaceEventType.YELLOW_FLAG, (EventImportance.IMPORTANT, 0.7, True)),
        (RaceEventType.PENALTY, (EventImportance.IMPORTANT, 0.7, True)),
        (RaceEventType.INVESTIGATION, (EventImportance.IMPORTANT, 0.7, True)),
        (RaceEventType.SESSION_FINISH, (EventImportance.IMPORTANT, 0.7, True)),
        (RaceEventType.PIT_STOP, (EventImportance.NORMAL, 0.5, False)),
    ],
)
def test_semantic_source_facts_receive_feed_worthy_importance(
    event_type: RaceEventType,
    expected: tuple[EventImportance, float, bool],
) -> None:
    assert EventImportancePolicy().classify(race_event(event_type)) == expected


def test_informational_repetition_is_cooled_down() -> None:
    policy = EventImportancePolicy(cooldown_seconds=20)
    first = race_event(RaceEventType.ELIMINATION_RISK, second=0)
    repeated = race_event(RaceEventType.ELIMINATION_RISK, second=1)
    later = race_event(RaceEventType.ELIMINATION_RISK, second=21)

    assert policy.should_emit(first)
    assert not policy.should_emit(repeated)
    assert policy.should_emit(later)
