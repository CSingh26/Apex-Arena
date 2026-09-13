# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.strategy import FactReference, PitLossEstimate

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def timing(number, gap, *, lap=20, age=0):
    from app.services.strategy_scenarios import StrategyTiming

    return StrategyTiming(
        driver_number=number,
        gap_to_leader_seconds=gap,
        lap_number=lap,
        evidence=FactReference(
            event_id=UUID(int=number),
            sequence=number,
            observed_at=NOW - timedelta(seconds=age),
            source="synthetic",
        ),
    )


def loss():
    references = [
        FactReference(
            event_id=UUID(int=number),
            sequence=number,
            observed_at=NOW,
            source="synthetic",
        )
        for number in range(100, 106)
    ]
    return PitLossEstimate(
        availability="available",
        seconds=20,
        lower_seconds=18,
        upper_seconds=22,
        evidence=references,
        evidence_roles={
            "baseline_laps": references[:3],
            "in_lap": references[3],
            "out_lap": references[4],
            "pit": references[5],
        },
    )


def test_pit_window_exposes_range_and_traffic_not_a_certain_future_position():
    from app.services.strategy_scenarios import project_pit_window

    result = project_pit_window(
        timing(4, 5),
        [timing(1, 0), timing(16, 20), timing(63, 26), timing(81, 40)],
        loss(),
        as_of=NOW,
        complete_field=True,
    )
    assert result.projected_gap_range_seconds == (23, 27)
    assert result.rejoin_position_range == (3, 4)
    assert result.traffic_driver_numbers == [63]
    assert result.confidence == "low"
    assert "field_gaps_held_constant" in result.assumptions


def test_partial_field_keeps_relative_comparison_but_does_not_invent_absolute_rank():
    from app.services.strategy_scenarios import project_pit_window

    result = project_pit_window(
        timing(4, 5), [timing(63, 26)], loss(), as_of=NOW, complete_field=False
    )
    assert result.rejoin_position_range is None
    assert result.traffic_driver_numbers == [63]
    assert result.availability == "partial"


@pytest.mark.parametrize(
    "rival",
    [
        lambda: timing(63, 26, age=31),
        lambda: timing(63, 26, lap=19),
        lambda: timing(63, 26, age=-1),
    ],
)
def test_stale_lapped_or_future_timing_cannot_support_a_full_field_prediction(rival):
    from app.services.strategy_scenarios import project_pit_window

    result = project_pit_window(timing(4, 5), [rival()], loss(), as_of=NOW, complete_field=True)
    assert result.rejoin_position_range is None
    assert result.traffic_driver_numbers == []
    assert result.availability == "partial"


def test_neutralization_does_not_apply_a_fabricated_discount_to_green_pit_loss():
    from app.services.strategy_scenarios import project_pit_window

    result = project_pit_window(
        timing(4, 5),
        [timing(63, 26)],
        loss(),
        as_of=NOW,
        complete_field=True,
        neutralization="safety_car",
    )
    assert result.projected_gap_range_seconds is None
    assert result.availability == "insufficient_evidence"
    assert "matching_neutralized_pit_loss_unavailable" in result.limitations


def test_unsupported_loss_cannot_produce_numeric_scenarios_after_model_copy_bypass():
    from app.services.strategy_scenarios import extra_stop_break_even, project_pit_window

    unsupported = loss().model_copy(update={"evidence": []})
    pit_window = project_pit_window(
        timing(4, 5),
        [timing(63, 26)],
        unsupported,
        as_of=NOW,
        complete_field=True,
    )
    assert pit_window.availability == "insufficient_evidence"
    assert pit_window.projected_gap_range_seconds is None
    assert (
        extra_stop_break_even(unsupported, laps_remaining=10).required_gain_seconds_per_lap is None
    )


@pytest.mark.parametrize(
    "update",
    [
        {"evidence": [None] * 6},
        {"seconds": "invalid"},
        {"seconds": 10**10_000},
        {"method": "unsupported"},
        {"evidence_roles": None},
    ],
    ids=["null-references", "invalid-number", "huge-number", "method", "missing-roles"],
)
def test_corrupted_loss_fails_closed_at_both_scenario_boundaries(update):
    from app.services.strategy_scenarios import extra_stop_break_even, project_pit_window

    corrupted = loss().model_copy(update=update)
    pit_window = project_pit_window(
        timing(4, 5),
        [timing(63, 26)],
        corrupted,
        as_of=NOW,
        complete_field=True,
    )
    extra_stop = extra_stop_break_even(corrupted, laps_remaining=10)

    assert pit_window.availability == "insufficient_evidence"
    assert pit_window.projected_gap_range_seconds is None
    assert extra_stop.required_gain_seconds_per_lap is None


def test_serialized_verified_estimate_supports_both_scenarios():
    from app.services.strategy_scenarios import extra_stop_break_even, project_pit_window

    restored = PitLossEstimate.model_validate_json(loss().model_dump_json())
    pit_window = project_pit_window(
        timing(4, 5),
        [timing(63, 26)],
        restored,
        as_of=NOW,
        complete_field=True,
    )
    extra_stop = extra_stop_break_even(restored, laps_remaining=10)

    assert pit_window.projected_gap_range_seconds == (23, 27)
    assert extra_stop.required_gain_seconds_per_lap == 2


@pytest.mark.parametrize(("driver_age", "rival_age"), [(30, 0), (0, 30)])
def test_individually_fresh_but_misaligned_timings_cannot_support_absolute_rank(
    driver_age, rival_age
):
    from app.services.strategy_scenarios import project_pit_window

    result = project_pit_window(
        timing(4, 5, age=driver_age),
        [timing(63, 26, age=rival_age)],
        loss(),
        as_of=NOW,
        complete_field=True,
    )
    assert result.availability == "partial"
    assert result.rejoin_position_range is None
    assert result.traffic_driver_numbers == []
    assert "timing_observations_not_aligned" in result.limitations


def test_coherent_timings_at_freshness_boundary_can_support_rank():
    from app.services.strategy_scenarios import project_pit_window

    result = project_pit_window(
        timing(4, 5, age=30),
        [timing(63, 26, age=30)],
        loss(),
        as_of=NOW,
        complete_field=True,
    )
    assert result.availability == "available"
    assert result.rejoin_position_range == (1, 2)


def test_extra_stop_comparison_reports_required_pace_gain_not_an_optimal_stop_count():
    from app.services.strategy_scenarios import extra_stop_break_even

    result = extra_stop_break_even(loss(), laps_remaining=10)
    assert result.required_gain_seconds_per_lap == 2
    assert result.required_gain_range_seconds_per_lap == (1.8, 2.2)
    assert result.confidence == "low"
    assert "fresh_tyre_pace_not_predicted" in result.assumptions


@pytest.mark.parametrize(
    "remaining",
    [None, 0, -1, True, 100_001, 10**10_000],
    ids=["missing", "zero", "negative", "boolean", "above-bound", "huge"],
)
def test_extra_stop_without_remaining_distance_stays_unknown(remaining):
    from app.services.strategy_scenarios import extra_stop_break_even

    assert (
        extra_stop_break_even(loss(), laps_remaining=remaining).required_gain_seconds_per_lap
        is None
    )


def test_extra_stop_accepts_declared_remaining_lap_boundary():
    from app.services.strategy_scenarios import extra_stop_break_even

    assert extra_stop_break_even(
        loss(), laps_remaining=100_000
    ).required_gain_seconds_per_lap == pytest.approx(0.0002)
