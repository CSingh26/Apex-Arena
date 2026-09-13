# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.models import NormalizedRaceEvent, RaceEventType


def fact(sequence, kind=RaceEventType.LAP_COMPLETED, *, driver=4, lap=None, **payload):
    return NormalizedRaceEvent(
        id=UUID(int=sequence),
        session_key="history-test",
        source="synthetic",
        event_time=datetime(2026, 9, 12, tzinfo=UTC) + timedelta(seconds=sequence),
        received_at=datetime(2026, 9, 12, tzinfo=UTC),
        sequence_number=sequence,
        event_type=kind,
        driver_numbers=[driver] if driver is not None else [],
        lap_number=lap,
        payload=payload,
        dedup_key=f"history-{sequence}",
    )


def test_lap_correction_replaces_same_lap_and_keeps_source_evidence():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    original = SessionHistory(session_key="history-test")
    first = apply_history(original, fact(1, lap=7, lap_duration=91.2), neutralization="green")
    corrected = apply_history(first, fact(2, lap=7, lap_duration=90.5), neutralization="green")
    assert not original.drivers
    assert len(corrected.drivers["4"].laps) == 1
    assert corrected.drivers["4"].laps[0].duration_seconds == 90.5
    assert corrected.drivers["4"].laps[0].evidence.event_id == UUID(int=2)
    assert first.drivers["4"].laps[0].duration_seconds == 91.2


def test_explicit_deletion_changes_target_lap_not_latest_and_survives_correction():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = SessionHistory(session_key="history-test")
    for seq, lap in [(1, 7), (2, 8)]:
        state = apply_history(state, fact(seq, lap=lap, lap_duration=90), neutralization="green")
    state = apply_history(state, fact(3, RaceEventType.LAP_DELETED, lap=7))
    state = apply_history(state, fact(4, lap=7, lap_duration=89.5), neutralization="green")
    assert [lap.deleted for lap in state.drivers["4"].laps] == [True, False]
    assert state.drivers["4"].laps[0].deletion_evidence.event_id == UUID(int=3)


def test_unidentified_deleted_lap_does_not_guess_a_target():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = apply_history(
        SessionHistory(session_key="history-test"), fact(1, lap=7, lap_duration=90)
    )
    state = apply_history(state, fact(2, RaceEventType.LAP_DELETED))
    assert not state.drivers["4"].laps[0].deleted
    assert state.unresolved_deletions == 1


def test_explicit_deletion_before_lap_payload_is_not_lost():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = apply_history(
        SessionHistory(session_key="history-test"), fact(1, RaceEventType.LAP_DELETED, lap=7)
    )
    state = apply_history(state, fact(2, lap=7, lap_duration=88), neutralization="green")
    assert state.drivers["4"].laps[0].deleted
    assert state.drivers["4"].laps[0].deletion_evidence.event_id == UUID(int=1)


@pytest.mark.parametrize(
    ("original_control", "expected_exclusion"),
    [("safety_car", "neutralized"), ("unknown", "control_unknown")],
)
def test_late_duration_correction_preserves_partial_lap_control_context(
    original_control, expected_exclusion
):
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history
    from app.services.strategy_estimates import estimate_pace

    state = apply_history(
        SessionHistory(session_key="history-test"),
        fact(
            1,
            RaceEventType.STINT_UPDATE,
            stint_number=1,
            lap_start=1,
            compound="MEDIUM",
        ),
    )
    state = apply_history(
        state,
        fact(2, lap=7, lap_duration=None),
        neutralization=original_control,
    )
    state = apply_history(state, fact(3, lap=7, lap_duration=119), neutralization="green")
    assert state.drivers["4"].laps[0].exclusions == [expected_exclusion]
    assert estimate_pace(state.drivers["4"]).sample_laps == []


def test_stint_correction_and_used_tyre_age_are_not_confused_with_new_tyres():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history, tyre_age_at_lap

    state = SessionHistory(session_key="history-test")
    state = apply_history(
        state,
        fact(
            1,
            RaceEventType.STINT_UPDATE,
            stint_number=2,
            lap_start=20,
            lap_end=25,
            compound="MEDIUM",
            tyre_age_at_start=3,
        ),
    )
    state = apply_history(
        state,
        fact(
            2,
            RaceEventType.STINT_UPDATE,
            stint_number=2,
            lap_start=20,
            lap_end=28,
            compound="MEDIUM",
            tyre_age_at_start=3,
        ),
    )
    stint = state.drivers["4"].stints[0]
    assert len(state.drivers["4"].stints) == 1
    assert tyre_age_at_lap(stint, 25) == 9
    assert tyre_age_at_lap(stint, 19) is None
    assert tyre_age_at_lap(stint, 29) is None


def test_pit_lane_time_is_not_claimed_as_net_pit_loss_and_marks_in_lap():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = apply_history(
        SessionHistory(session_key="history-test"), fact(1, lap=10, lap_duration=109)
    )
    state = apply_history(
        state, fact(2, RaceEventType.PIT_STOP, lap=10, lane_duration=22.2, stop_duration=2.1)
    )
    pit = state.drivers["4"].pits[0]
    assert pit.lane_seconds == 22.2
    assert pit.stationary_seconds == 2.1
    assert "pit_in" in state.drivers["4"].laps[0].exclusions


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, True, "broken"])
def test_invalid_timing_cannot_become_a_pace_observation(duration):
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = apply_history(
        SessionHistory(session_key="history-test"), fact(1, lap=1, lap_duration=duration)
    )
    assert state.drivers["4"].laps[0].duration_seconds is None
    assert "missing_duration" in state.drivers["4"].laps[0].exclusions


def test_history_bounds_do_not_resurrect_old_laps_or_overwrite_newer_weather():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = SessionHistory(session_key="history-test")
    for lap in range(1, 151):
        state = apply_history(state, fact(lap, lap=lap, lap_duration=90))
    state = apply_history(state, fact(151, lap=1, lap_duration=88))
    assert len(state.drivers["4"].laps) == 120
    assert state.drivers["4"].laps[0].lap_number == 31
    for seq in range(152, 220):
        state = apply_history(
            state,
            fact(seq, RaceEventType.WEATHER_UPDATE, driver=None, rainfall=1, air_temperature=25),
        )
    late = fact(220, RaceEventType.WEATHER_UPDATE, driver=None, rainfall=0)
    late.event_time = datetime(2026, 9, 12, tzinfo=UTC)
    state = apply_history(state, late)
    assert len(state.weather) == 60
    assert state.weather[-1].rainfall is True
    assert state.lap_history_truncated


def test_snapshot_round_trip_and_repeated_sequence_are_deterministic():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    event = fact(1, lap=2, lap_duration=91, is_pit_out_lap=True)
    state = apply_history(
        SessionHistory(session_key="history-test"), event, neutralization="safety_car"
    )
    restored = SessionHistory.model_validate_json(state.model_dump_json())
    assert apply_history(restored, event) == state
    assert set(state.drivers["4"].laps[0].exclusions) == {"pit_out", "neutralized"}


def test_provider_rainfall_flag_cannot_support_intensity_and_unknown_age_stays_unknown():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history, tyre_age_at_lap

    state = apply_history(
        SessionHistory(session_key="history-test"),
        fact(1, RaceEventType.STINT_UPDATE, stint_number=1, lap_start=1, compound="SOFT"),
    )
    assert tyre_age_at_lap(state.drivers["4"].stints[0], 4) is None
    state = apply_history(state, fact(2, RaceEventType.WEATHER_UPDATE, driver=None, rainfall=9))
    assert state.weather[-1].rainfall is None


@pytest.mark.parametrize("lap", [7, None])
def test_source_deletion_without_driver_leaves_bounded_diagnostic(lap):
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = apply_history(
        SessionHistory(session_key="history-test"),
        fact(1, RaceEventType.LAP_DELETED, driver=None, lap=lap),
    )
    assert state.drivers == {}
    assert state.unresolved_deletions == 1

    saturated = SessionHistory(session_key="history-test", unresolved_deletions=100_000)
    saturated = apply_history(
        saturated,
        fact(1, RaceEventType.LAP_DELETED, driver=None, lap=lap),
    )
    assert saturated.unresolved_deletions == 100_000


def test_history_bounds_cover_drivers_stints_and_pits_with_sticky_flags():
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = SessionHistory(session_key="history-test")
    for driver in range(1, 66):
        state = apply_history(
            state,
            fact(driver, driver=driver, lap=1, lap_duration=90),
        )
    assert len(state.drivers) == 64
    assert state.driver_history_truncated

    sequence = 66
    for stint in range(1, 30):
        state = apply_history(
            state,
            fact(
                sequence,
                RaceEventType.STINT_UPDATE,
                driver=1,
                stint_number=stint,
                lap_start=stint,
            ),
        )
        sequence += 1
    assert [row.stint_number for row in state.drivers["1"].stints] == list(range(6, 30))
    assert state.stint_history_truncated

    for lap in range(1, 30):
        state = apply_history(
            state,
            fact(sequence, RaceEventType.PIT_STOP, driver=1, lap=lap, lane_duration=20),
        )
        sequence += 1
    assert [row.lap_number for row in state.drivers["1"].pits] == list(range(6, 30))
    assert state.pit_history_truncated


def test_history_rejects_foreign_session_and_ignores_derived_or_lower_sequence_events():
    from app.domain.models import EventOrigin
    from app.domain.strategy import SessionHistory
    from app.services.race_history import apply_history

    state = apply_history(
        SessionHistory(session_key="history-test"),
        fact(2, lap=2, lap_duration=90),
    )
    foreign = fact(3, lap=3, lap_duration=90)
    foreign.session_key = "different-session"
    with pytest.raises(ValueError, match="History session mismatch"):
        apply_history(state, foreign)

    derived = fact(3, lap=3, lap_duration=90)
    derived.event_origin = EventOrigin.DERIVED
    assert apply_history(state, derived) == state
    assert apply_history(state, fact(1, lap=1, lap_duration=89)) == state
