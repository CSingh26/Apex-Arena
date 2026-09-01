# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.intelligence import (
    BattleIntensity,
    BattleState,
    BattleStatus,
    BattleTrend,
    RaceIntelligenceConfig,
)
from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.battle_intelligence import BattleEngine, rank_battles
from app.services.race_state import DriverRaceState, RaceState

START = datetime(2026, 7, 19, 13, 20, tzinfo=UTC)


def state(session_type: str = "RACE") -> RaceState:
    return RaceState(
        session_key="race",
        session_type=session_type,
        current_lap=20,
        drivers={
            "16": DriverRaceState(driver_number=16, position=4, interval=0),
            "4": DriverRaceState(driver_number=4, position=5, interval=2.2),
            "63": DriverRaceState(driver_number=63, position=6, interval=2.4),
        },
    )


def interval(driver: int, gap: float | None, *, second: int, sequence: int) -> NormalizedRaceEvent:
    observed_at = START + timedelta(seconds=second)
    return NormalizedRaceEvent(
        session_key="race",
        source="openf1",
        event_time=observed_at,
        received_at=observed_at,
        sequence_number=sequence,
        event_type=RaceEventType.INTERVAL_SAMPLE,
        driver_numbers=[driver],
        interval_seconds=gap,
        payload={"driver_number": driver, "interval": gap},
        dedup_key=f"interval:{driver}:{sequence}",
    )


def control_event(event_type: RaceEventType, driver: int, sequence: int) -> NormalizedRaceEvent:
    return NormalizedRaceEvent(
        session_key="race",
        source="openf1",
        event_time=START + timedelta(seconds=sequence),
        received_at=START + timedelta(seconds=sequence),
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[driver],
        dedup_key=f"{event_type.value}:{driver}:{sequence}",
    )


def engine() -> BattleEngine:
    return BattleEngine(
        RaceIntelligenceConfig(
            battle_start_samples=3,
            battle_end_samples=3,
            battle_trend_window=5,
            battle_trend_minimum_change=0.15,
        )
    )


def start_battle(service: BattleEngine, race: RaceState) -> BattleState:
    updates = []
    for index, gap in enumerate((1.8, 1.7, 1.6), start=1):
        updates = service.apply(interval(4, gap, second=index, sequence=index), race)
    assert updates[-1].event_type is RaceEventType.BATTLE_STARTED
    return updates[-1].battle


def test_battle_starts_only_after_sustained_close_samples() -> None:
    service = engine()
    race = state()

    assert service.apply(interval(4, 1.8, second=1, sequence=1), race) == []
    assert service.apply(interval(4, 2.4, second=2, sequence=2), race) == []
    assert service.current_battles == []

    battle = start_battle(service, race)

    assert battle.lead_driver_number == 16
    assert battle.chasing_driver_number == 4
    assert battle.status is BattleStatus.ACTIVE
    assert battle.trend is BattleTrend.CLOSING


def test_battle_trend_ignores_small_noise_and_detects_falling_back() -> None:
    race = state()
    stable_service = engine()
    for index, gap in enumerate((1.60, 1.62, 1.64), start=1):
        stable_updates = stable_service.apply(
            interval(4, gap, second=index, sequence=index), race
        )
    stable = stable_updates[-1].battle
    assert stable.trend is BattleTrend.STABLE

    falling_service = engine()
    for index, gap in enumerate((1.6, 1.8, 2.0), start=1):
        falling_updates = falling_service.apply(
            interval(4, gap, second=index, sequence=index), race
        )
    falling = falling_updates[-1].battle
    assert falling.trend is BattleTrend.FALLING_BACK


def test_battle_intensifies_and_proximity_uses_hysteresis() -> None:
    service = engine()
    race = state()
    start_battle(service, race)

    updates = service.apply(interval(4, 0.9, second=4, sequence=4), race)
    assert {update.event_type for update in updates} == {
        RaceEventType.BATTLE_INTENSIFIED,
        RaceEventType.DRS_RANGE_ENTERED,
    }
    assert updates[0].battle.intensity is BattleIntensity.INTENSE
    assert service.apply(interval(4, 1.1, second=5, sequence=5), race)[0].battle.within_one_second

    updates = service.apply(interval(4, 1.3, second=6, sequence=6), race)
    assert RaceEventType.DRS_RANGE_EXITED in {update.event_type for update in updates}


def test_sustained_gap_pit_retirement_and_overtake_resolve_battles() -> None:
    race = state()
    service = engine()
    start_battle(service, race)
    for index, gap in enumerate((3.1, 3.2, 3.3), start=4):
        updates = service.apply(interval(4, gap, second=index, sequence=index), race)
    assert updates[-1].event_type is RaceEventType.BATTLE_ENDED
    assert updates[-1].battle.status is BattleStatus.RESOLVED

    resolving_types = (
        RaceEventType.PIT_ENTRY,
        RaceEventType.DRIVER_RETIRED,
        RaceEventType.OVERTAKE,
    )
    for event_type in resolving_types:
        service = engine()
        start_battle(service, race)
        updates = service.apply(control_event(event_type, 4, sequence=10), race)
        assert updates[-1].event_type is RaceEventType.BATTLE_ENDED


def test_missing_interval_and_non_race_sessions_never_create_battles() -> None:
    for session_type in ("QUALIFYING", "SPRINT_QUALIFYING", "PRACTICE_1"):
        service = engine()
        race = state(session_type)
        for index in range(1, 5):
            assert service.apply(interval(4, 0.5, second=index, sequence=index), race) == []
    assert engine().apply(interval(4, None, second=1, sequence=1), state()) == []


def battle(
    lead: int,
    chaser: int,
    interval_seconds: float,
    intensity: BattleIntensity,
) -> BattleState:
    return BattleState(
        id=f"race:{lead}:{chaser}",
        session_key="race",
        lead_driver_number=lead,
        chasing_driver_number=chaser,
        lead_position=4,
        chasing_position=5,
        interval_seconds=interval_seconds,
        closest_interval_seconds=interval_seconds,
        interval_history=[interval_seconds],
        started_at=START,
        last_updated_at=START,
        intensity=intensity,
        status=BattleStatus.ACTIVE,
    )


def test_battle_ranking_prioritizes_selected_driver_and_deduplicates_train() -> None:
    battles = [
        battle(16, 4, 0.7, BattleIntensity.CLOSE),
        battle(4, 63, 0.5, BattleIntensity.INTENSE),
        battle(81, 1, 0.8, BattleIntensity.CLOSE),
    ]

    ranked = rank_battles(battles, selected_driver=16)

    assert ranked[0].lead_driver_number == 16
    visible_drivers = [
        driver
        for item in ranked
        for driver in (item.lead_driver_number, item.chasing_driver_number)
    ]
    assert len(visible_drivers) == len(set(visible_drivers))
    assert ranked[0].train_size == 3
