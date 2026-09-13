# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.strategy import (
    DriverHistory,
    FactReference,
    LapObservation,
    PaceEstimate,
    PitLossEstimate,
    PitObservation,
    StintObservation,
    WeatherObservation,
)


def reference(number):
    return FactReference(
        event_id=UUID(int=number),
        sequence=number,
        observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        source="synthetic",
    )


def driver_with_laps(durations):
    return DriverHistory(
        laps=[
            LapObservation(lap_number=i, duration_seconds=duration, evidence=reference(i))
            for i, duration in enumerate(durations, 1)
        ],
        stints=[
            StintObservation(
                stint_number=1,
                lap_start=1,
                compound="MEDIUM",
                tyre_age_at_start=0,
                evidence=reference(100),
            )
        ],
    )


def test_representative_pace_excludes_pit_deleted_neutralized_and_outlier_laps():
    from app.services.strategy_estimates import estimate_pace

    driver = driver_with_laps([90, 90.2, 90.4, 130, 110, 70, 120])
    driver.laps[4].exclusions = ["pit_out"]
    driver.laps[5].deleted = True
    driver.laps[6].exclusions = ["neutralized"]
    result = estimate_pace(driver)
    assert result.representative_seconds == pytest.approx(90.2)
    assert result.sample_laps == [1, 2, 3]
    assert result.excluded_laps == [4, 5, 6, 7]
    assert [row.event_id for row in result.evidence] == [UUID(int=i) for i in (1, 2, 3, 100)]


def test_pace_never_mixes_stints_or_invents_missing_samples():
    from app.services.strategy_estimates import estimate_pace

    driver = driver_with_laps([90, 90, 90, 89, 89])
    driver.stints[0].lap_end = 3
    driver.stints.append(
        StintObservation(
            stint_number=2,
            lap_start=4,
            compound="SOFT",
            tyre_age_at_start=0,
            evidence=reference(101),
        )
    )
    result = estimate_pace(driver)
    assert result.representative_seconds is None
    assert result.availability == "insufficient_evidence"
    assert result.sample_laps == [4, 5]


def test_recent_observed_drift_has_uncertainty_and_is_not_a_tyre_life_prediction():
    from app.services.strategy_estimates import estimate_pace

    driver = driver_with_laps([90, 90.2, 90.4, 90.6, 90.8, 91])
    result = estimate_pace(driver)
    assert result.pace_drift_seconds_per_lap == pytest.approx(0.2)
    assert result.drift_range_seconds_per_lap[0] <= 0.2 <= result.drift_range_seconds_per_lap[1]
    assert result.expected_tyre_life_laps is None
    assert "fuel_and_traffic_not_isolated" in result.limitations
    assert result.confidence == "low"


def test_observed_pit_cycle_loss_uses_lap_excess_not_lane_duration():
    from app.services.strategy_estimates import estimate_pit_loss

    driver = driver_with_laps([90, 90, 90, 108, 99, 90])
    driver.laps[3].exclusions = ["pit_in"]
    driver.laps[4].exclusions = ["pit_out"]
    driver.pits = [
        PitObservation(lap_number=4, lane_seconds=22, stationary_seconds=2, evidence=reference(102))
    ]
    result = estimate_pit_loss(driver, 4)
    assert result.seconds == 27
    assert result.method == "observed_two_lap_excess"
    assert result.lower_seconds <= 27 <= result.upper_seconds
    assert result.confidence == "low"
    assert len(result.evidence) == 6


def test_lane_only_or_neutralized_pit_does_not_fabricate_green_pit_loss():
    from app.services.strategy_estimates import estimate_pit_loss

    driver = driver_with_laps([90, 90, 90, 108, 99])
    driver.laps[3].exclusions = ["pit_in", "neutralized"]
    driver.laps[4].exclusions = ["pit_out"]
    driver.pits = [PitObservation(lap_number=4, lane_seconds=22, evidence=reference(102))]
    assert estimate_pit_loss(driver, 4).seconds is None
    driver.laps = []
    assert estimate_pit_loss(driver, 4).seconds is None


@pytest.mark.parametrize(
    "pit_evidence",
    [
        reference(1),
        FactReference(
            event_id=UUID(int=102),
            sequence=1,
            observed_at=datetime(2026, 9, 12, tzinfo=UTC),
            source="synthetic",
        ),
    ],
    ids=["repeated-event-id", "conflicting-sequence-id"],
)
def test_pit_loss_estimator_fails_closed_for_conflicting_selected_evidence(pit_evidence):
    from app.services.strategy_estimates import estimate_pit_loss

    driver = driver_with_laps([90, 90, 90, 108, 99])
    driver.laps[3].exclusions = ["pit_in"]
    driver.laps[4].exclusions = ["pit_out"]
    driver.pits = [PitObservation(lap_number=4, lane_seconds=22, evidence=pit_evidence)]
    driver = DriverHistory.model_validate(driver.model_dump())

    result = estimate_pit_loss(driver, 4)

    assert result.availability == "insufficient_evidence"
    assert result.seconds is None
    assert result.evidence == []


def test_pace_window_is_bounded_and_late_old_stint_does_not_become_current():
    from app.services.strategy_estimates import estimate_pace

    driver = driver_with_laps([90] * 30)
    driver.stints.append(
        StintObservation(
            stint_number=2,
            lap_start=20,
            compound="HARD",
            tyre_age_at_start=0,
            evidence=reference(101),
        )
    )
    driver.stints.reverse()
    result = estimate_pace(driver)
    assert result.stint_number == 2
    assert result.sample_laps == list(range(20, 31))
    assert len(result.evidence) <= 13


@pytest.mark.parametrize(
    "build",
    [
        lambda: LapObservation(lap_number=1, duration_seconds=float("nan"), evidence=reference(1)),
        lambda: LapObservation(lap_number=1, duration_seconds=float("inf"), evidence=reference(1)),
        lambda: LapObservation(lap_number=1, duration_seconds=0, evidence=reference(1)),
        lambda: LapObservation(lap_number=1, sectors_seconds=[0], evidence=reference(1)),
        lambda: LapObservation(lap_number=1, sectors_seconds=[float("inf")], evidence=reference(1)),
        lambda: PitObservation(lap_number=1, lane_seconds=0, evidence=reference(1)),
        lambda: PitObservation(
            lap_number=1, stationary_seconds=float("nan"), evidence=reference(1)
        ),
        lambda: WeatherObservation(humidity=101, evidence=reference(1)),
        lambda: WeatherObservation(wind_speed=float("inf"), evidence=reference(1)),
    ],
)
def test_fact_models_reject_non_finite_or_out_of_domain_measurements(build):
    with pytest.raises(ValidationError):
        build()


def test_serialized_restore_revalidates_factual_measurements():
    valid = LapObservation(lap_number=1, duration_seconds=90, evidence=reference(1))
    payload = valid.model_dump_json().replace('"duration_seconds":90.0', '"duration_seconds":NaN')
    with pytest.raises(ValidationError):
        LapObservation.model_validate_json(payload)


def test_driver_history_rejects_duplicate_lap_identity_and_estimator_fails_safe_after_mutation():
    from app.services.strategy_estimates import estimate_pace

    duplicate = [
        LapObservation(lap_number=1, duration_seconds=90, evidence=reference(1)),
        LapObservation(lap_number=1, duration_seconds=91, evidence=reference(2)),
    ]
    with pytest.raises(ValidationError, match="duplicate lap"):
        DriverHistory(laps=duplicate)

    driver = driver_with_laps([90, 90.2, 90.4, 90.6, 90.8, 91])
    driver.laps.append(LapObservation(lap_number=1, duration_seconds=92, evidence=reference(7)))
    result = estimate_pace(driver)
    assert result.availability == "insufficient_evidence"
    assert result.representative_seconds is None


@pytest.mark.parametrize(
    "estimate",
    [
        lambda: PaceEstimate(availability="available"),
        lambda: PaceEstimate(
            availability="available",
            representative_seconds=float("inf"),
            observed_range_seconds=(89, 91),
        ),
        lambda: PaceEstimate(
            availability="available",
            representative_seconds=90,
            observed_range_seconds=(91, 89),
        ),
        lambda: PitLossEstimate(availability="available"),
        lambda: PitLossEstimate(
            availability="available",
            seconds=float("nan"),
            lower_seconds=18,
            upper_seconds=22,
            evidence=[reference(1)],
        ),
        lambda: PitLossEstimate(
            availability="available",
            seconds=20,
            lower_seconds=22,
            upper_seconds=18,
            evidence=[reference(1)],
        ),
    ],
)
def test_available_estimates_require_finite_ordered_complete_metrics(estimate):
    with pytest.raises(ValidationError):
        estimate()


def test_observed_two_lap_pit_loss_requires_all_six_evidence_facts():
    with pytest.raises(ValidationError, match="six evidence facts"):
        PitLossEstimate(
            availability="available",
            seconds=20,
            lower_seconds=18,
            upper_seconds=22,
            evidence=[reference(1)],
        )


@pytest.mark.parametrize(
    "references",
    [
        [reference(1)] * 6,
        [
            reference(1),
            reference(2),
            reference(3),
            reference(4),
            reference(5),
            FactReference(
                event_id=UUID(int=6),
                sequence=5,
                observed_at=datetime(2026, 9, 12, tzinfo=UTC),
                source="synthetic",
            ),
        ],
    ],
    ids=["repeated-source-identity", "conflicting-sequence-identity"],
)
def test_observed_pit_loss_requires_distinct_source_identities(references):
    with pytest.raises(ValidationError, match="distinct evidence"):
        PitLossEstimate(
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


def test_observed_pit_loss_requires_explicit_method_evidence_roles():
    references = [reference(number) for number in range(1, 7)]
    with pytest.raises(ValidationError, match="evidence roles"):
        PitLossEstimate(
            availability="available",
            seconds=20,
            lower_seconds=18,
            upper_seconds=22,
            evidence=references,
        )


def test_valid_estimates_round_trip_without_available_null_metrics():
    from app.services.strategy_estimates import estimate_pace, estimate_pit_loss
    from app.services.strategy_scenarios import (
        StrategyTiming,
        extra_stop_break_even,
        project_pit_window,
    )

    pace = estimate_pace(driver_with_laps([90, 90.2, 90.4]))
    restored = PaceEstimate.model_validate_json(pace.model_dump_json())
    assert restored.availability == "available"
    assert restored.representative_seconds == pytest.approx(90.2)

    driver = driver_with_laps([90, 90, 90, 108, 99])
    driver.laps[3].exclusions = ["pit_in"]
    driver.laps[4].exclusions = ["pit_out"]
    driver.pits = [PitObservation(lap_number=4, lane_seconds=22, evidence=reference(102))]
    loss = PitLossEstimate.model_validate_json(estimate_pit_loss(driver, 4).model_dump_json())
    assert loss.availability == "available"
    assert loss.lower_seconds <= loss.seconds <= loss.upper_seconds
    assert [row.event_id for row in loss.evidence_roles.baseline_laps] == [
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
    ]
    assert loss.evidence_roles.in_lap.event_id == UUID(int=4)
    assert loss.evidence_roles.out_lap.event_id == UUID(int=5)
    assert loss.evidence_roles.pit.event_id == UUID(int=102)
    driver_timing = StrategyTiming(
        driver_number=4,
        gap_to_leader_seconds=5,
        lap_number=20,
        evidence=reference(201),
    )
    rival_timing = StrategyTiming(
        driver_number=63,
        gap_to_leader_seconds=26,
        lap_number=20,
        evidence=reference(202),
    )
    assert (
        project_pit_window(
            driver_timing,
            [rival_timing],
            loss,
            as_of=driver_timing.evidence.observed_at,
            complete_field=True,
        ).projected_gap_range_seconds
        is not None
    )
    assert extra_stop_break_even(loss, laps_remaining=10).required_gain_seconds_per_lap == 2.7
