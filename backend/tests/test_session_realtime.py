# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

from app.domain.intelligence import BattleIntensity, BattleState, BattleStatus, BattleTrend
from app.services.race_state import DriverRaceState, RaceState
from app.services.session_realtime import location_state, telemetry_state, timing_state


def state() -> RaceState:
    return RaceState(
        session_key="spa-race",
        session_type="RACE",
        status="running",
        current_lap=16,
        sequence_number=88,
        last_updated_at=datetime(2026, 7, 19, 14, 30, tzinfo=UTC),
        drivers={
            "4": DriverRaceState(
                driver_number=4,
                full_name="Lando Norris",
                team_name="McLaren",
                position=1,
                best_lap_duration=101.234,
                latest_lap_duration=101.234,
                stint={"compound": "SOFT", "lap_start": 12},
                telemetry={"speed": 312.6, "throttle": 92.0, "brake": 0.0, "gear": 7, "drs": True},
                telemetry_updated_at=datetime(2026, 7, 19, 14, 30, tzinfo=UTC),
                location={"x": 30.5, "y": -12.2, "z": 3.0},
                location_updated_at=datetime(2026, 7, 19, 14, 30, tzinfo=UTC),
            ),
            "1": DriverRaceState(
                driver_number=1,
                full_name="Max Verstappen",
                team_name="Red Bull Racing",
                position=2,
                gap_to_leader=1.75,
                best_lap_duration=101.901,
                latest_lap_duration=102.2,
                stint={"compound": "MEDIUM", "lap_start": 7},
                pit_stops=[{"lap_number": 6}],
                location={"x": -10.0, "y": 42.0, "z": 2.0},
            ),
        },
    )


def test_timing_state_derives_sorted_race_rows_and_tyre_age() -> None:
    timing = timing_state(state())

    assert timing.mode == "race"
    assert timing.track_status == "GREEN"
    assert [row.driver_number for row in timing.drivers] == [4, 1]
    leader, second = timing.drivers
    assert leader.abbreviation == "NOR"
    assert leader.tyre_compound == "SOFT"
    assert leader.tyre_age_laps == 5
    assert leader.is_fastest is True
    assert leader.is_personal_best is True
    assert second.gap_to_leader == 1.75
    assert second.pit_stop_count == 1


def test_timing_state_exposes_backend_normalized_driver_battle_context() -> None:
    race_state = state()
    observed_at = datetime(2026, 7, 19, 13, 12, tzinfo=UTC)
    race_state.current_battles = [
        BattleState(
            id="race:4:1",
            session_key=race_state.session_key,
            lead_driver_number=4,
            chasing_driver_number=1,
            lead_position=1,
            chasing_position=2,
            interval_seconds=0.72,
            closest_interval_seconds=0.68,
            interval_history=[0.9, 0.8, 0.72],
            started_at=observed_at,
            last_updated_at=observed_at,
            trend=BattleTrend.CLOSING,
            intensity=BattleIntensity.INTENSE,
            status=BattleStatus.INTENSE,
            within_one_second=True,
        )
    ]

    timing = timing_state(race_state)
    leader = next(row for row in timing.drivers if row.driver_number == 4)
    chaser = next(row for row in timing.drivers if row.driver_number == 1)

    assert leader.battle_context.status == "UNDER_PRESSURE"
    assert leader.battle_context.behind_driver_number == 1
    assert chaser.battle_context.status == "CLOSING"
    assert chaser.battle_context.ahead_driver_number == 4
    assert chaser.battle_context.ahead_interval_seconds == 0.72


def test_telemetry_and_location_views_are_bounded_and_provider_neutral() -> None:
    race_state = state()
    telemetry = telemetry_state(race_state, 4)
    locations = location_state(race_state)

    assert telemetry.available is True
    assert telemetry.speed_kph == 312.6
    assert telemetry.drs_active is True
    assert telemetry_state(race_state, 99).available is False
    assert locations.available is True
    assert [row.driver_number for row in locations.drivers] == [4, 1]
    assert locations.drivers[0].abbreviation == "NOR"
