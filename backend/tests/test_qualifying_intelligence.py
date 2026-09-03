# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.qualifying_intelligence import QualifyingEngine, qualifying_cutoff
from app.services.race_state import DriverRaceState, RaceState

START = datetime(2026, 7, 18, 14, tzinfo=UTC)


def state(phase: str = "Q1", field_size: int = 22) -> RaceState:
    return RaceState(
        session_key="qualifying",
        session_type="QUALIFYING",
        current_phase=phase,
        drivers={
            str(number): DriverRaceState(driver_number=number, position=index)
            for index, number in enumerate(range(1, field_size + 1), start=1)
        },
    )


def event(
    event_type: RaceEventType,
    *,
    driver: int | None = None,
    second: int,
    sequence: int,
    **payload: object,
) -> NormalizedRaceEvent:
    observed_at = START + timedelta(seconds=second)
    return NormalizedRaceEvent(
        session_key="qualifying",
        source="openf1",
        event_time=observed_at,
        received_at=observed_at,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[] if driver is None else [driver],
        payload=dict(payload),
        dedup_key=f"{event_type.value}:{driver}:{sequence}",
    )


def test_cutoff_adapts_to_twenty_and_twenty_two_car_fields() -> None:
    assert qualifying_cutoff(20, "Q1") == 15
    assert qualifying_cutoff(20, "Q2") == 10
    assert qualifying_cutoff(22, "Q1") == 16
    assert qualifying_cutoff(22, "Q2") == 10
    assert qualifying_cutoff(22, "Q3") is None


def test_q1_cutoff_updates_as_the_timing_feed_discovers_the_field() -> None:
    service = QualifyingEngine()
    race = state("Q1", field_size=0)

    for sequence, driver in enumerate(range(1, 23), start=1):
        service.apply(
            event(
                RaceEventType.POSITION_SAMPLE,
                driver=driver,
                second=sequence,
                sequence=sequence,
                position=driver,
            ),
            race,
        )

    intelligence = service.state_for("qualifying")
    assert intelligence is not None
    assert intelligence.field_size == 22
    assert intelligence.cutoff_position == 16


def test_phase_changes_emit_session_phase_event_without_overtake_semantics() -> None:
    service = QualifyingEngine()
    race = state("Q1")
    assert service.apply(
        event(RaceEventType.QUALIFYING_PHASE, second=0, sequence=1, session_phase="Q1"),
        race,
    ) == []

    race.current_phase = "Q2"
    events = service.apply(
        event(RaceEventType.QUALIFYING_PHASE, second=900, sequence=2, session_phase="Q2"),
        race,
    )

    assert [item.event_type for item in events] == [RaceEventType.SESSION_PHASE_CHANGE]
    assert events[0].payload["phase_before"] == "Q1"
    assert events[0].payload["phase_after"] == "Q2"


def test_driver_moving_across_cutoff_emits_drop_zone_change() -> None:
    service = QualifyingEngine()
    race = state("Q1")
    service.apply(
        event(RaceEventType.POSITION_SAMPLE, driver=17, second=1, sequence=1, position=17),
        race,
    )

    events = service.apply(
        event(RaceEventType.POSITION_SAMPLE, driver=17, second=3, sequence=2, position=15),
        race,
    )

    assert [item.event_type for item in events] == [RaceEventType.QUALIFYING_CUTOFF_CHANGE]
    assert events[0].payload["movement"] == "OUT_OF_DROP_ZONE"
    assert events[0].payload["cutoff_position"] == 16


def test_provisional_pole_and_lap_bests_are_deterministic() -> None:
    service = QualifyingEngine()
    race = state("Q3")
    service.apply(
        event(RaceEventType.POSITION_SAMPLE, driver=4, second=1, sequence=1, position=2), race
    )
    pole = service.apply(
        event(RaceEventType.POSITION_SAMPLE, driver=4, second=2, sequence=2, position=1), race
    )
    assert pole[0].event_type is RaceEventType.PROVISIONAL_POLE

    first_lap = service.apply(
        event(
            RaceEventType.LAP_COMPLETED,
            driver=4,
            second=60,
            sequence=3,
            lap_number=4,
            lap_duration=82.5,
        ),
        race,
    )
    assert {item.event_type for item in first_lap} == {
        RaceEventType.PERSONAL_BEST,
        RaceEventType.FASTEST_LAP,
    }
    slower = service.apply(
        event(
            RaceEventType.LAP_COMPLETED,
            driver=4,
            second=120,
            sequence=4,
            lap_number=5,
            lap_duration=83.0,
        ),
        race,
    )
    assert slower == []


def test_elimination_risk_uses_cooldown_and_never_emits_overtake() -> None:
    service = QualifyingEngine(cooldown_seconds=20)
    race = state("Q1")
    service.apply(
        event(RaceEventType.POSITION_SAMPLE, driver=16, second=1, sequence=1, position=16),
        race,
    )
    risky = service.apply(
        event(
            RaceEventType.POSITION_SAMPLE,
            driver=16,
            second=100,
            sequence=2,
            position=16,
            time_remaining_seconds=90,
        ),
        race,
    )
    cooled_down = service.apply(
        event(
            RaceEventType.POSITION_SAMPLE,
            driver=16,
            second=105,
            sequence=3,
            position=16,
            time_remaining_seconds=85,
        ),
        race,
    )

    assert [item.event_type for item in risky] == [RaceEventType.ELIMINATION_RISK]
    assert cooled_down == []
    assert all(item.event_type is not RaceEventType.OVERTAKE for item in risky)
