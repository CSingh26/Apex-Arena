# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.intelligence import PositionChangeCause
from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.position_intelligence import PositionTracker
from app.services.race_state import DriverRaceState, RaceState

START = datetime(2026, 7, 19, 13, tzinfo=UTC)


def position_event(
    driver: int,
    position: int,
    *,
    second: int,
    sequence: int,
    **payload: object,
) -> NormalizedRaceEvent:
    observed_at = START + timedelta(seconds=second)
    return NormalizedRaceEvent(
        session_key="race",
        source="openf1",
        event_time=observed_at,
        received_at=observed_at,
        sequence_number=sequence,
        event_type=RaceEventType.POSITION_SAMPLE,
        driver_numbers=[driver],
        payload={"driver_number": driver, "position": position, **payload},
        dedup_key=f"position:{driver}:{position}:{sequence}",
    )


def race_state() -> RaceState:
    return RaceState(
        session_key="race",
        session_type="RACE",
        drivers={
            "4": DriverRaceState(driver_number=4, position=5),
            "16": DriverRaceState(driver_number=16, position=4),
        },
    )


def seed_pair(tracker: PositionTracker, state: RaceState) -> None:
    assert tracker.apply(position_event(16, 4, second=0, sequence=1), state) == []
    assert tracker.apply(position_event(4, 5, second=0, sequence=2), state) == []


def test_pair_swap_waits_for_coherent_order_before_confirming() -> None:
    tracker = PositionTracker()
    state = race_state()
    seed_pair(tracker, state)

    assert tracker.apply(position_event(4, 4, second=2, sequence=3), state) == []
    changes = tracker.apply(position_event(16, 5, second=2, sequence=4), state)

    observed = [
        (change.driver_number, change.position_before, change.position_after)
        for change in changes
    ]
    assert observed == [
        (4, 5, 4),
        (16, 4, 5),
    ]
    assert {change.cause for change in changes} == {PositionChangeCause.ON_TRACK_CANDIDATE}
    assert changes[0].position_delta == 1
    assert changes[1].position_delta == -1


def test_duplicate_and_stale_updates_do_not_create_changes() -> None:
    tracker = PositionTracker()
    state = race_state()
    seed_pair(tracker, state)

    assert tracker.apply(position_event(4, 5, second=1, sequence=3), state) == []
    assert tracker.apply(position_event(4, 4, second=-1, sequence=4), state) == []


def test_pit_transition_classifies_inherited_positions_without_overtake_semantics() -> None:
    tracker = PositionTracker()
    state = race_state()
    seed_pair(tracker, state)

    assert tracker.apply(position_event(4, 4, second=2, sequence=3), state) == []
    changes = tracker.apply(
        position_event(16, 5, second=2, sequence=4, in_pit=True), state
    )

    assert {change.cause for change in changes} == {PositionChangeCause.PIT_CYCLE}


def test_retirement_classifies_inherited_positions() -> None:
    tracker = PositionTracker()
    state = race_state()
    seed_pair(tracker, state)

    assert tracker.apply(position_event(4, 4, second=2, sequence=3), state) == []
    changes = tracker.apply(
        position_event(16, 5, second=2, sequence=4, status="RETIRED"), state
    )

    assert {change.cause for change in changes} == {PositionChangeCause.RETIREMENT_INHERITANCE}


def test_position_change_builds_directional_derived_events() -> None:
    tracker = PositionTracker()
    state = race_state()
    seed_pair(tracker, state)
    tracker.apply(position_event(4, 4, second=2, sequence=3), state)
    changes = tracker.apply(position_event(16, 5, second=2, sequence=4), state)

    events = tracker.events_for(changes, source_event=position_event(16, 5, second=2, sequence=4))

    assert [event.event_type for event in events] == [
        RaceEventType.POSITION_CHANGE,
        RaceEventType.POSITION_GAIN,
        RaceEventType.POSITION_CHANGE,
        RaceEventType.POSITION_LOSS,
    ]
    assert events[0].payload["cause"] == "ON_TRACK_CANDIDATE"
    assert events[0].primary_driver_number == 4
    assert events[0].secondary_driver_number == 16


def test_qualifying_order_never_becomes_on_track_candidate() -> None:
    tracker = PositionTracker()
    state = race_state()
    state.session_type = "QUALIFYING"
    seed_pair(tracker, state)
    tracker.apply(position_event(4, 4, second=2, sequence=3), state)

    changes = tracker.apply(position_event(16, 5, second=2, sequence=4), state)

    assert {change.cause for change in changes} == {
        PositionChangeCause.PENALTY_OR_CLASSIFICATION
    }
