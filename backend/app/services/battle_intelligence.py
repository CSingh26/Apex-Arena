# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from collections import defaultdict

from app.domain.intelligence import (
    BattleIntensity,
    BattleState,
    BattleStatus,
    BattleTrend,
    BattleUpdate,
    RaceIntelligenceConfig,
)
from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.race_state import RaceState

RACE_LIKE_SESSIONS = {"RACE", "SPRINT"}
logger = logging.getLogger(__name__)
RESOLVING_TYPES = {
    RaceEventType.PIT_ENTRY,
    RaceEventType.PIT_STOP,
    RaceEventType.PIT_EXIT,
    RaceEventType.DRIVER_STOPPED,
    RaceEventType.DRIVER_RETIRED,
    RaceEventType.RETIREMENT,
    RaceEventType.OVERTAKE,
    RaceEventType.SESSION_END,
    RaceEventType.SESSION_FINISH,
}


class BattleEngine:
    """Incremental adjacent-position battle lifecycle with bounded interval history."""

    def __init__(self, config: RaceIntelligenceConfig | None = None) -> None:
        self.config = config or RaceIntelligenceConfig()
        self._battles: dict[str, dict[str, BattleState]] = defaultdict(dict)

    @property
    def current_battles(self) -> list[BattleState]:
        return [
            battle.model_copy(deep=True)
            for session in self._battles.values()
            for battle in session.values()
            if battle.status in {BattleStatus.ACTIVE, BattleStatus.INTENSE}
        ]

    def current_for_session(self, session_key: str) -> list[BattleState]:
        return [
            battle.model_copy(deep=True)
            for battle in self._battles.get(session_key, {}).values()
            if battle.status in {BattleStatus.ACTIVE, BattleStatus.INTENSE}
        ]

    def reset_session(self, session_key: str) -> None:
        self._battles.pop(session_key, None)

    def apply(
        self,
        event: NormalizedRaceEvent,
        race_state: RaceState,
    ) -> list[BattleUpdate]:
        if str(race_state.session_type or "").upper() not in RACE_LIKE_SESSIONS:
            return []
        if event.event_type in RESOLVING_TYPES:
            return self._resolve_for_event(event)
        if event.event_type is not RaceEventType.INTERVAL_SAMPLE or not event.driver_numbers:
            return []
        interval = self._interval(event)
        if interval is None:
            return []
        chaser_number = event.driver_numbers[0]
        chaser = race_state.drivers.get(str(chaser_number))
        if chaser is None or chaser.position is None or chaser.position <= 1:
            return []
        leader = next(
            (
                driver
                for driver in race_state.drivers.values()
                if driver.position == chaser.position - 1 and driver.driver_number is not None
            ),
            None,
        )
        if leader is None or leader.driver_number is None:
            return []

        battle_id = self._battle_id(event.session_key, leader.driver_number, chaser_number)
        session = self._battles[event.session_key]
        battle = session.get(battle_id)
        if battle is None:
            if interval > self.config.battle_start_interval_seconds:
                return []
            battle = BattleState(
                id=battle_id,
                session_key=event.session_key,
                lead_driver_number=leader.driver_number,
                chasing_driver_number=chaser_number,
                lead_position=leader.position or chaser.position - 1,
                chasing_position=chaser.position,
                interval_seconds=interval,
                closest_interval_seconds=interval,
                interval_history=[interval],
                started_at=event.event_time,
                last_updated_at=event.event_time,
                lap_number=event.lap_number or race_state.current_lap,
            )
            session[battle_id] = battle
            return []

        battle.last_updated_at = event.event_time
        battle.lead_position = leader.position or battle.lead_position
        battle.chasing_position = chaser.position
        battle.interval_seconds = interval
        battle.closest_interval_seconds = min(battle.closest_interval_seconds, interval)
        battle.interval_history.append(interval)
        battle.interval_history = battle.interval_history[-self.config.battle_trend_window :]
        battle.trend = self._trend(battle.interval_history)
        updates: list[BattleUpdate] = []

        if battle.status is BattleStatus.POTENTIAL:
            if interval > self.config.battle_start_interval_seconds:
                session.pop(battle_id, None)
                return []
            battle.close_samples += 1
            if battle.close_samples < self.config.battle_start_samples:
                return []
            battle.status = BattleStatus.ACTIVE
            battle.intensity = self._intensity(interval)
            updates.append(self._update(battle, RaceEventType.BATTLE_STARTED))
        else:
            updates.append(self._update(battle, None))

        previous_intensity = battle.intensity
        battle.intensity = self._intensity(interval)
        if battle.intensity is BattleIntensity.INTENSE:
            battle.status = BattleStatus.INTENSE
            if previous_intensity is not BattleIntensity.INTENSE:
                updates.append(self._update(battle, RaceEventType.BATTLE_INTENSIFIED))
        elif battle.status is BattleStatus.INTENSE:
            battle.status = BattleStatus.ACTIVE

        if interval <= self.config.battle_intense_interval_seconds and not battle.within_one_second:
            battle.within_one_second = True
            battle.drs_status = "WITHIN_ONE_SECOND"
            updates.append(self._update(battle, RaceEventType.DRS_RANGE_ENTERED))
        elif interval > self.config.proximity_exit_seconds and battle.within_one_second:
            battle.within_one_second = False
            battle.drs_status = "OUTSIDE_ONE_SECOND"
            updates.append(self._update(battle, RaceEventType.DRS_RANGE_EXITED))

        if interval > self.config.battle_end_interval_seconds:
            battle.end_samples += 1
            if battle.end_samples >= self.config.battle_end_samples:
                return [self._resolve(event.session_key, battle_id, "gap_opened")]
        else:
            battle.end_samples = 0
        return self._deduplicate_updates(updates)

    def _resolve_for_event(self, event: NormalizedRaceEvent) -> list[BattleUpdate]:
        participants = set(event.driver_numbers)
        session = self._battles[event.session_key]
        if event.event_type in {RaceEventType.SESSION_END, RaceEventType.SESSION_FINISH}:
            ids = list(session)
        else:
            ids = [
                battle_id
                for battle_id, battle in session.items()
                if participants.intersection(
                    {battle.lead_driver_number, battle.chasing_driver_number}
                )
            ]
        return [
            self._resolve(event.session_key, battle_id, event.event_type.value)
            for battle_id in ids
        ]

    def _resolve(self, session_key: str, battle_id: str, reason: str) -> BattleUpdate:
        battle = self._battles[session_key].pop(battle_id)
        battle.status = BattleStatus.RESOLVED
        battle.resolution_reason = reason
        return self._update(battle, RaceEventType.BATTLE_ENDED)

    def _trend(self, history: list[float]) -> BattleTrend:
        if len(history) < 3:
            return BattleTrend.STABLE
        change = history[-1] - history[0]
        if change <= -self.config.battle_trend_minimum_change:
            return BattleTrend.CLOSING
        if change >= self.config.battle_trend_minimum_change:
            return BattleTrend.FALLING_BACK
        return BattleTrend.STABLE

    def _intensity(self, interval: float) -> BattleIntensity:
        if interval <= self.config.battle_intense_interval_seconds:
            return BattleIntensity.INTENSE
        if interval <= 1.5:
            return BattleIntensity.CLOSE
        return BattleIntensity.BUILDING

    @staticmethod
    def _update(battle: BattleState, event_type: RaceEventType | None) -> BattleUpdate:
        if event_type is not None:
            logger.info(
                "battle_transition session=%s battle=%s event=%s interval=%.3f "
                "trend=%s intensity=%s",
                battle.session_key,
                battle.id,
                event_type.value,
                battle.interval_seconds,
                battle.trend.value,
                battle.intensity.value,
            )
        return BattleUpdate(battle=battle.model_copy(deep=True), event_type=event_type)

    @staticmethod
    def _deduplicate_updates(updates: list[BattleUpdate]) -> list[BattleUpdate]:
        if any(update.event_type is not None for update in updates):
            updates = [update for update in updates if update.event_type is not None]
        seen: set[RaceEventType | None] = set()
        result: list[BattleUpdate] = []
        for update in updates:
            event_type = update.event_type
            if event_type in seen:
                continue
            seen.add(event_type)  # type: ignore[arg-type]
            result.append(update)
        return result

    @staticmethod
    def _interval(event: NormalizedRaceEvent) -> float | None:
        value = event.interval_seconds
        if value is None:
            value = event.payload.get("interval")  # type: ignore[assignment]
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _battle_id(session_key: str, leader: int, chaser: int) -> str:
        return f"{session_key}:{leader}:{chaser}"


def rank_battles(
    battles: list[BattleState],
    selected_driver: int | None = None,
    *,
    limit: int = 3,
) -> list[BattleState]:
    """Rank current cards and collapse connected train edges for presentation."""

    components: list[set[int]] = []
    for battle in battles:
        pair = {battle.lead_driver_number, battle.chasing_driver_number}
        matches = [component for component in components if component.intersection(pair)]
        if not matches:
            components.append(set(pair))
            continue
        merged = set(pair)
        for component in matches:
            merged.update(component)
            components.remove(component)
        components.append(merged)

    intensity_score = {
        BattleIntensity.BUILDING: 1,
        BattleIntensity.CLOSE: 2,
        BattleIntensity.INTENSE: 3,
    }

    def score(battle: BattleState) -> tuple[int, int, float]:
        selected = int(
            selected_driver in {battle.lead_driver_number, battle.chasing_driver_number}
        )
        return selected, intensity_score[battle.intensity], -battle.interval_seconds

    ranked: list[BattleState] = []
    used: set[int] = set()
    for battle in sorted(battles, key=score, reverse=True):
        pair = {battle.lead_driver_number, battle.chasing_driver_number}
        if used.intersection(pair):
            continue
        card = battle.model_copy(deep=True)
        card.train_size = next(
            (len(component) for component in components if pair.issubset(component)), 2
        )
        ranked.append(card)
        used.update(pair)
        if len(ranked) >= limit:
            break
    return ranked
