# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections import defaultdict
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domain.intelligence import (
    BattleState,
    BattleUpdate,
    OvertakeContext,
    PositionChange,
    RaceIntelligenceConfig,
)
from app.domain.models import (
    DerivationEvidence,
    EventConfidence,
    EventDerivation,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.services.battle_intelligence import BattleEngine
from app.services.event_importance import EventImportancePolicy
from app.services.overtake_intelligence import OvertakeDetector
from app.services.position_intelligence import PositionTracker
from app.services.qualifying_intelligence import QualifyingEngine
from app.services.race_state import RaceState, RaceStateEngine


class BattleSummaryRepository(Protocol):
    async def upsert_resolved(self, battle: BattleState) -> None: ...


class RaceIntelligenceCoordinator:
    """Derive bounded race meaning after authoritative source-state reduction."""

    def __init__(
        self,
        race_state: RaceStateEngine,
        *,
        config: RaceIntelligenceConfig | None = None,
        battle_summaries: BattleSummaryRepository | None = None,
    ) -> None:
        self.race_state = race_state
        self.config = config or RaceIntelligenceConfig()
        self.battle_summaries = battle_summaries
        self.positions = PositionTracker()
        self.overtakes = OvertakeDetector(self.config)
        self.battles = BattleEngine(self.config)
        self.qualifying = QualifyingEngine(
            cooldown_seconds=self.config.event_cooldown_seconds
        )
        self.importance = EventImportancePolicy(
            cooldown_seconds=self.config.event_cooldown_seconds
        )
        self._pending_overtakes: dict[str, dict[int, PositionChange]] = defaultdict(dict)
        self._derived: dict[str, list[NormalizedRaceEvent]] = defaultdict(list)
        self._last_source_sequence: dict[str, int] = {}

    async def consume(self, event: NormalizedRaceEvent) -> None:
        if event.event_origin is EventOrigin.DERIVED:
            return
        if event.sequence_number <= self._last_source_sequence.get(event.session_key, 0):
            return
        self._last_source_sequence[event.session_key] = event.sequence_number
        state = await self.race_state.get_state(event.session_key)
        candidates = self._advance_overtakes(event, state)

        changes = self.positions.apply(event, state)
        candidates.extend(self.positions.events_for(changes, source_event=event))
        for change in changes:
            if change.position_delta <= 0:
                continue
            self._pending_overtakes[event.session_key][change.driver_number] = change
            confirmed = self.overtakes.apply(change, self._overtake_context(change, event, state))
            if confirmed is not None:
                candidates.append(confirmed)
                self._pending_overtakes[event.session_key].pop(change.driver_number, None)

        battle_updates = self.battles.apply(event, state)
        if self.battle_summaries is not None:
            for update in battle_updates:
                if update.event_type is RaceEventType.BATTLE_ENDED:
                    await self.battle_summaries.upsert_resolved(update.battle)
        candidates.extend(
            self._battle_event(event, update)
            for update in battle_updates
            if update.event_type is not None
        )
        candidates.extend(self.qualifying.apply(event, state))
        scored = [self._score(candidate) for candidate in candidates]
        self._derived[event.session_key].extend(
            candidate for candidate in scored if self.importance.should_emit(candidate)
        )
        await self.race_state.set_intelligence(
            event.session_key,
            current_battles=self.battles.current_for_session(event.session_key),
            qualifying=self.qualifying.state_for(event.session_key),
        )

    def drain_derived(self, session_key: str) -> list[NormalizedRaceEvent]:
        events = self._derived.pop(session_key, [])
        return [event.model_copy(deep=True) for event in events]

    def _advance_overtakes(
        self,
        event: NormalizedRaceEvent,
        state: RaceState,
    ) -> list[NormalizedRaceEvent]:
        confirmed: list[NormalizedRaceEvent] = []
        for driver, pending in list(self._pending_overtakes[event.session_key].items()):
            if event.sequence_number <= pending.source_sequence:
                continue
            advanced = pending.model_copy(
                update={
                    "observed_at": event.event_time,
                    "source_sequence": event.sequence_number,
                }
            )
            overtake = self.overtakes.apply(
                advanced,
                self._overtake_context(pending, event, state),
            )
            if overtake is not None:
                confirmed.append(overtake)
                self._pending_overtakes[event.session_key].pop(driver, None)
        return confirmed

    @staticmethod
    def _overtake_context(
        change: PositionChange,
        event: NormalizedRaceEvent,
        state: RaceState,
    ) -> OvertakeContext:
        driver = state.drivers.get(str(change.driver_number))
        interval = RaceIntelligenceCoordinator._float(driver.interval if driver else None)
        participants = [
            state.drivers.get(str(number))
            for number in [change.driver_number, *change.related_driver_numbers]
        ]
        both_running = all(
            participant is not None
            and participant.status not in {"RETIRED", "STOPPED", "DNF", "DNS"}
            and not participant.in_pit
            for participant in participants
        )
        pit_available = bool(state.pit_stop_history) or any(
            bool(participant and participant.stint) for participant in participants
        )
        return OvertakeContext(
            session_type=str(state.session_type or ""),
            observed_at=event.event_time,
            interval_before=interval,
            pit_data_available=pit_available,
            location_available=state.has_locations,
            both_running=both_running,
        )

    @staticmethod
    def _battle_event(
        source: NormalizedRaceEvent,
        update: BattleUpdate,
    ) -> NormalizedRaceEvent:
        battle = update.battle
        event_type = update.event_type
        assert isinstance(event_type, RaceEventType)
        return NormalizedRaceEvent(
            meeting_id=source.meeting_id,
            session_id=source.session_id,
            session_key=source.session_key,
            source="apexarena",
            event_origin=EventOrigin.DERIVED,
            event_time=source.event_time,
            received_at=source.received_at,
            event_type=event_type,
            primary_driver_number=battle.chasing_driver_number,
            secondary_driver_number=battle.lead_driver_number,
            position_before=battle.chasing_position,
            position_after=battle.chasing_position,
            interval_seconds=battle.interval_seconds,
            lap_number=battle.lap_number,
            confidence=0.9,
            confidence_level=EventConfidence.HIGH,
            derivation=EventDerivation(
                algorithm="battle_engine_v1",
                evidence=[
                    DerivationEvidence(
                        kind="bounded_interval_history",
                        observed_at=source.event_time,
                        event_id=source.id,
                        value=",".join(str(value) for value in battle.interval_history),
                    )
                ],
                exclusions_checked=["pit_transition", "driver_status", "session_type"],
            ),
            payload={"battle": battle.model_dump(mode="json")},
            dedup_key=(
                f"battle:{event_type.value.lower()}:{source.session_key}:"
                f"{battle.lead_driver_number}:{battle.chasing_driver_number}:"
                f"{source.sequence_number}"
            ),
            is_replay=source.is_replay,
        )

    def _score(self, event: NormalizedRaceEvent) -> NormalizedRaceEvent:
        level, score, _ = self.importance.classify(event)
        return event.model_copy(
            update={
                "id": uuid5(NAMESPACE_URL, f"apexarena:{event.dedup_key}"),
                "processed_at": event.event_time,
                "importance_level": level,
                "importance": score,
            }
        )

    def reset_session(self, session_key: str) -> None:
        self.positions.reset_session(session_key)
        self.overtakes.reset_session(session_key)
        self.battles.reset_session(session_key)
        self.qualifying.reset_session(session_key)
        self.importance.reset_session(session_key)
        self._pending_overtakes.pop(session_key, None)
        self._derived.pop(session_key, None)
        self._last_source_sequence.pop(session_key, None)

    @staticmethod
    def _float(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
