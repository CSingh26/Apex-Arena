# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.strategy import FactReference, SessionHistory, WeatherObservation

BASE = datetime(2026, 9, 12, tzinfo=UTC)


def sample(sequence, second, **values):
    return WeatherObservation(
        evidence=FactReference(
            event_id=UUID(int=sequence),
            sequence=sequence,
            observed_at=BASE + timedelta(seconds=second),
            source="synthetic",
        ),
        **values,
    )


def analyze(rows, *, cursor=10, second=180, history_cursor=10):
    from app.services.weather_analysis import analyze_weather

    return analyze_weather(
        SessionHistory(session_key="weather", sequence=history_cursor, weather=rows),
        as_of_sequence=cursor,
        as_of_time=BASE + timedelta(seconds=second),
    )


def test_weather_changes_keep_units_and_exact_two_observation_evidence():
    result = analyze(
        [
            sample(
                1,
                0,
                track_temperature=40,
                air_temperature=25,
                humidity=60,
                wind_speed=2,
                wind_direction=350,
                rainfall=False,
            ),
            sample(
                2,
                120,
                track_temperature=37,
                air_temperature=24,
                humidity=64,
                wind_speed=3,
                wind_direction=10,
                rainfall=True,
            ),
        ]
    )
    assert result.availability == "available"
    assert result.rainfall_transition == "rain_detected"
    assert result.track_temperature_change_celsius == -3
    assert result.air_temperature_change_celsius == -1
    assert result.humidity_change_percentage_points == 4
    assert result.wind_speed_change_metres_per_second == 1
    assert result.wind_direction_change_degrees == 20
    assert result.comparison_seconds == 120
    assert [row.event_id for row in result.evidence] == [UUID(int=1), UUID(int=2)]
    assert "rainfall_presence_not_intensity" in result.limitations
    assert "not_a_track_wetness_or_grip_measurement" in result.limitations


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (True, False, "rain_no_longer_detected"),
        (True, True, "rain_present"),
        (False, False, "rain_absent"),
        (None, True, "rain_present"),
        (True, None, "unknown"),
    ],
)
def test_rainfall_presence_never_invents_intensity_or_dry_track(before, after, expected):
    result = analyze([sample(1, 0, rainfall=before), sample(2, 120, rainfall=after)])
    assert result.rainfall_transition == expected


def test_missing_latest_metric_does_not_carry_old_value_into_change():
    result = analyze([sample(1, 0, track_temperature=40), sample(2, 120, humidity=70)])
    assert result.current.track_temperature is None
    assert result.track_temperature_change_celsius is None
    assert result.humidity_change_percentage_points is None
    assert result.availability == "partial"
    assert result.comparison_seconds is None


def test_single_fresh_observation_is_partial_without_invented_change():
    result = analyze([sample(1, 120, rainfall=True)])
    assert result.availability == "partial"
    assert result.rainfall_transition == "rain_present"
    assert result.comparison_seconds is None
    assert len(result.evidence) == 1


def test_stale_weather_exposes_age_but_no_current_claim_or_delta():
    result = analyze([sample(1, 0, rainfall=False), sample(2, 120, rainfall=True)], second=421)
    assert result.availability == "stale"
    assert result.age_seconds == 301
    assert result.rainfall_transition == "unknown"
    assert result.track_temperature_change_celsius is None


def test_freshness_uses_replay_as_of_not_wall_clock():
    result = analyze([sample(1, 0, rainfall=False), sample(2, 120, rainfall=True)], second=420)
    assert result.availability == "available"
    assert result.age_seconds == 300


def test_future_observation_is_not_used_even_if_present_in_input():
    result = analyze([sample(1, 0, rainfall=False), sample(2, 240, rainfall=True)])
    assert result.rainfall_transition == "rain_absent"
    assert [row.sequence for row in result.evidence] == [1]


def test_rewind_requires_reconstructed_history_instead_of_current_corrected_window():
    result = analyze([sample(1, 0, rainfall=True)], cursor=5, history_cursor=10)
    assert result.availability == "unavailable"
    assert result.current is None
    assert "history_ahead_of_view" in result.limitations


def test_missing_or_empty_measurements_remain_unavailable():
    for rows in ([], [sample(1, 0)]):
        result = analyze(rows)
        assert result.availability == "unavailable"
        assert result.current is None


def test_old_baseline_does_not_support_a_current_weather_change():
    result = analyze([sample(1, 0, rainfall=False), sample(2, 901, rainfall=True)], second=902)
    assert result.availability == "partial"
    assert result.comparison_seconds is None
    assert result.rainfall_transition == "rain_present"


def test_same_timestamp_correction_is_not_counted_as_weather_change():
    result = analyze([sample(1, 120, rainfall=False), sample(2, 120, rainfall=True)])
    assert result.availability == "partial"
    assert result.rainfall_transition == "rain_present"
    assert [row.sequence for row in result.evidence] == [2]


def test_corrupted_or_oversized_history_fails_safe_before_analysis():
    from app.services.weather_analysis import analyze_weather

    state = SessionHistory(session_key="weather", sequence=10, weather=[sample(1, 120)])
    for invalid in (
        [None],
        [sample(1, 120)] * 61,
        [sample(1, 120).model_copy(update={"track_temperature": float("nan")})],
    ):
        result = analyze_weather(
            state.model_copy(update={"weather": invalid}), as_of_sequence=10, as_of_time=BASE
        )
        assert result.availability == "unavailable"
        assert result.current is None


def test_evidence_after_consumed_history_cursor_is_not_available():
    result = analyze([sample(11, 120, rainfall=True)])
    assert result.availability == "unavailable"
    assert result.evidence == []


def test_ambiguous_duplicate_source_identity_cannot_support_a_change():
    first = sample(1, 0, rainfall=False)
    second = sample(1, 120, rainfall=True)
    result = analyze([first, second])
    assert result.availability == "unavailable"


def test_naive_time_cannot_silently_mix_with_utc_history():
    from app.services.weather_analysis import analyze_weather

    result = analyze_weather(
        SessionHistory(session_key="weather", weather=[sample(1, 0)]),
        as_of_sequence=10,
        as_of_time=datetime(2026, 9, 12),
    )
    assert result.availability == "unavailable"
