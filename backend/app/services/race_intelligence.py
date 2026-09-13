# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domain.intelligence import (
    BattleState,
    BattleUpdate,
    OvertakeContext,
    PositionChange,
    RaceIntelligenceConfig,
    RaceIntelligenceDiagnostics,
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
from app.services.control_state import racing_inference_blocked
from app.services.event_importance import EventImportancePolicy
from app.services.event_pipeline import NormalizedEventRepository
from app.services.overtake_intelligence import OvertakeDetector
from app.services.position_intelligence import PositionTracker
from app.services.qualifying_intelligence import QualifyingEngine
from app.services.race_state import RaceState, RaceStateEngine
from app.services.strategy_events import strategy_event
from app.services.strategy_intelligence import (
    DEFAULT_SEMANTIC_IDENTITY,
    StrategyIntelligence,
)

logger = logging.getLogger(__name__)
DEFAULT_PIT_TRANSITION_SECONDS = 45.0
PIT_TRANSITION_GRACE_SECONDS = 5.0
SESSION_END_TYPES = {RaceEventType.SESSION_END, RaceEventType.SESSION_FINISH}


class BattleSummaryRepository(Protocol):
    async def upsert_resolved(self, battle: BattleState) -> None: ...


@dataclass
class IntelligenceEffects:
    derived: list[NormalizedRaceEvent]
    resolved_battles: list[BattleState]


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
        self.qualifying = QualifyingEngine(cooldown_seconds=self.config.event_cooldown_seconds)
        self.importance = EventImportancePolicy(cooldown_seconds=self.config.event_cooldown_seconds)
        self._pending_overtakes: dict[str, dict[int, PositionChange]] = defaultdict(dict)
        self._derived: dict[str, list[NormalizedRaceEvent]] = defaultdict(list)
        self._last_source_sequence: dict[str, int] = {}
        self._position_change_causes: dict[str, Counter[str]] = defaultdict(Counter)
        # Collaborators are supplied lazily in several call sites, so resolve the
        # semantic identity defensively rather than requiring a live engine here.
        self.strategy = StrategyIntelligence(
            semantic_identity=getattr(race_state, "algorithm_version", None)
            or DEFAULT_SEMANTIC_IDENTITY,
        )

    async def consume(self, event: NormalizedRaceEvent) -> None:
        if event.event_origin is EventOrigin.DERIVED:
            return
        state = await self.race_state.get_state(event.session_key)
        effects = await self.advance_applied_source(event, state, factual_owner=self.race_state)
        self._derived[event.session_key].extend(effects.derived)
        if self.battle_summaries is not None:
            for battle in effects.resolved_battles:
                await self.battle_summaries.upsert_resolved(battle)

    async def advance_source(
        self, event: NormalizedRaceEvent, state: RaceState
    ) -> IntelligenceEffects:
        """Produce a critical transaction's effects without external writes."""
        return await self.advance_applied_source(event, state, factual_owner=self.race_state)

    async def advance_applied_source(
        self, event, state, *, factual_owner: RaceStateEngine, emit_effects=True
    ):
        summaries = await self._advance(
            event,
            state,
            emit_effects=emit_effects,
            persist_summaries=False,
            factual_state=factual_owner,
        )
        delta = await factual_owner.evaluate_owned_facts(
            event, state, lambda context: self.strategy.advance(event, state, context)
        )
        current = self.battles.current_for_session(event.session_key)
        for battle in [*current, *summaries]:
            battle.strategy_context = delta.battle_contexts.get(
                f"{battle.lead_driver_number}:{battle.chasing_driver_number}"
            )
        derived = self.drain_derived(event.session_key)
        # Enrich serialized battle rows and resolved summaries before transaction assembly.
        for row in derived:
            if "battle" in row.payload:
                battle = row.payload["battle"]
                context = delta.battle_contexts.get(
                    f"{battle['lead_driver_number']}:{battle['chasing_driver_number']}"
                )
                battle["strategy_context"] = context.model_dump(mode="json") if context else None
        if emit_effects:
            for transition in delta.transitions:
                row = strategy_event(event, transition)
                if row is not None:
                    derived.append(self._score(row))
        await factual_owner.set_intelligence(
            event.session_key,
            current_battles=current,
            qualifying=self.qualifying.state_for(event.session_key),
            strategy_frame=delta.frame,
            return_state=False,
        )
        return IntelligenceEffects(derived, summaries)

    async def restore_session(
        self,
        session_key: str,
        repository: NormalizedEventRepository,
        *,
        through_sequence: int | None = None,
    ) -> None:
        """Reconstruct internal source history; never regenerate durable effects.

        Caller owns this processor's session lock. A fixed durable prefix is read
        in bounded pages; independently running writers still require coordination.
        Snapshots/shared Redis cannot reconstruct pending detector/cooldown state.
        """
        self.reset_session(session_key)
        scratch = RaceStateEngine(
            self.race_state.snapshots,
            retain_applied_dedup_keys=False,
            algorithm_version=self.race_state.algorithm_version,
        )
        await scratch.install_state(RaceState(session_key=session_key))
        prefix_end = (
            await repository.max_sequence(session_key)
            if through_sequence is None
            else through_sequence
        )
        cursor = 0
        try:
            while cursor < prefix_end:
                page_start = cursor
                page = await repository.list_for_session(
                    session_key, after_sequence=cursor, limit=1000
                )
                if not page:
                    raise RuntimeError("Intelligence source prefix is incomplete")
                for event in page:
                    if event.sequence_number > prefix_end:
                        break
                    if event.sequence_number <= cursor:
                        raise RuntimeError("Intelligence source prefix is not strictly ordered")
                    state = await scratch.apply(event, persist_snapshot=False)
                    if event.event_origin is not EventOrigin.DERIVED:
                        await self.advance_applied_source(
                            event, state, emit_effects=False, factual_owner=scratch
                        )
                    cursor = event.sequence_number
                    # Async locks on private in-memory engines do not suspend.
                    # Yield within a buffered page so cold replay cannot starve
                    # lease renewal, cancellation or unrelated sessions.
                    await asyncio.sleep(0)
                if cursor == page_start:
                    raise RuntimeError("Intelligence source prefix is incomplete")
            await self.race_state._install_reconstruction(
                await scratch._take_reconstruction(session_key)
            )
        except BaseException:
            self.reset_session(session_key)
            raise

    async def _advance(
        self,
        event: NormalizedRaceEvent,
        state: RaceState,
        *,
        emit_effects: bool,
        persist_summaries: bool = True,
        factual_state: RaceStateEngine | None = None,
    ) -> list[BattleState]:
        if event.event_origin is EventOrigin.DERIVED:
            return []
        if event.sequence_number <= self._last_source_sequence.get(event.session_key, 0):
            return []
        self._last_source_sequence[event.session_key] = event.sequence_number
        blocked = racing_inference_blocked(state.control)
        if blocked:
            self.overtakes.reject_pending_for_session(
                event.session_key,
                "SESSION_ENDED"
                if state.control.lifecycle.value == "finished"
                else "RACING_NEUTRALIZED",
            )
            self._pending_overtakes.pop(event.session_key, None)
        candidates = [] if blocked else self._advance_overtakes(event, state)
        if event.event_type in SESSION_END_TYPES:
            self.overtakes.reject_pending_for_session(event.session_key, "SESSION_ENDED")
            self._pending_overtakes.pop(event.session_key, None)

        changes = self.positions.apply(event, state)
        for change in changes:
            self._position_change_causes[event.session_key][change.cause.value] += 1
        candidates.extend(self.positions.events_for(changes, source_event=event))
        for change in changes:
            if blocked or change.position_delta <= 0:
                continue
            confirmed = self.overtakes.apply(change, self._overtake_context(change, event, state))
            if confirmed is not None:
                candidates.append(self._enrich_overtake(confirmed, event, state))
            if self.overtakes.is_pending(change):
                self._pending_overtakes[event.session_key][change.driver_number] = change
            else:
                self._pending_overtakes[event.session_key].pop(change.driver_number, None)

        battle_updates = self.battles.apply(event, state)
        for candidate in candidates:
            if candidate.event_type is RaceEventType.OVERTAKE:
                battle_updates.extend(self.battles.apply(candidate, state))
        resolved = [
            update.battle.model_copy(deep=True)
            for update in battle_updates
            if update.event_type is RaceEventType.BATTLE_ENDED
        ]
        if emit_effects and persist_summaries and self.battle_summaries is not None:
            for update in battle_updates:
                if update.event_type is RaceEventType.BATTLE_ENDED:
                    await self.battle_summaries.upsert_resolved(update.battle)
        candidates.extend(
            self._battle_event(event, update)
            for update in battle_updates
            if update.event_type is not None
        )
        lap_witness = None
        if event.event_type is RaceEventType.LAP_COMPLETED and state.session_type in {
            "QUALIFYING",
            "SPRINT_QUALIFYING",
        }:
            lap_witness = await (factual_state or self.race_state).source_lap_witness(event)
        candidates.extend(self.qualifying.apply(event, state, lap_witness=lap_witness))
        scored = [self._score(candidate) for candidate in candidates]
        # Evaluate cooldown transitions even during warm-up, but discard effects.
        eligible = [candidate for candidate in scored if self.importance.should_emit(candidate)]
        if emit_effects:
            self._derived[event.session_key].extend(eligible)
        return resolved

    def drain_derived(self, session_key: str) -> list[NormalizedRaceEvent]:
        events = self._derived.pop(session_key, [])
        return [event.model_copy(deep=True) for event in events]

    def diagnostics_for_session(self, session_key: str) -> RaceIntelligenceDiagnostics:
        current_battles = self.battles.current_for_session(session_key)
        return RaceIntelligenceDiagnostics(
            position_states=self.positions.state_count(session_key),
            tracked_battles=self.battles.tracked_count(session_key),
            current_battles=len(current_battles),
            maximum_battle_history=self.battles.maximum_history_size(session_key),
            pending_overtakes=len(self._pending_overtakes.get(session_key, {})),
            buffered_derived_events=len(self._derived.get(session_key, [])),
            overtake_confirmations=self.overtakes.confirmation_count(session_key),
            overtake_rejections_by_reason=self.overtakes.rejections_for_session(session_key),
            position_changes_by_cause=dict(
                sorted(self._position_change_causes.get(session_key, {}).items())
            ),
        )

    def _advance_overtakes(
        self,
        event: NormalizedRaceEvent,
        state: RaceState,
    ) -> list[NormalizedRaceEvent]:
        if event.event_type is not RaceEventType.POSITION_SAMPLE:
            return []
        confirmed: list[NormalizedRaceEvent] = []
        for driver, pending in list(self._pending_overtakes[event.session_key].items()):
            if event.sequence_number <= pending.source_sequence:
                continue
            participants = {pending.driver_number, *pending.related_driver_numbers}
            if not participants.intersection(event.driver_numbers):
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
                confirmed.append(self._enrich_overtake(overtake, event, state))
                self._pending_overtakes[event.session_key].pop(driver, None)
            elif not self.overtakes.is_pending(pending):
                self._pending_overtakes[event.session_key].pop(driver, None)
        return confirmed

    @staticmethod
    def _enrich_overtake(
        overtake: NormalizedRaceEvent,
        source: NormalizedRaceEvent,
        state: RaceState,
    ) -> NormalizedRaceEvent:
        return overtake.model_copy(
            update={
                "meeting_id": source.meeting_id,
                "session_id": source.session_id,
                "lap_number": source.lap_number or state.current_lap,
                "is_replay": source.is_replay,
            }
        )

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
        primary = state.drivers.get(str(change.driver_number))
        target_number = change.related_driver_numbers[0] if change.related_driver_numbers else None
        target = state.drivers.get(str(target_number)) if target_number is not None else None
        return OvertakeContext(
            session_type=str(state.session_type or ""),
            observed_at=event.event_time,
            interval_before=interval,
            pit_data_available=pit_available,
            location_available=state.has_locations,
            both_running=both_running,
            ordering_persisted=(
                primary is not None
                and target is not None
                and primary.position is not None
                and target.position is not None
                and primary.position < target.position
            ),
            pit_transition=RaceIntelligenceCoordinator._recent_pit_transition(
                participants, event.event_time
            ),
        )

    @staticmethod
    def _recent_pit_transition(participants: list[object], observed_at: datetime) -> bool:
        for participant in participants:
            if participant is None:
                continue
            pit_stops = getattr(participant, "pit_stops", [])
            for pit_stop in reversed(pit_stops[-3:]):
                value = pit_stop.get("date") or pit_stop.get("event_time")
                if not isinstance(value, str):
                    continue
                try:
                    pit_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                duration = RaceIntelligenceCoordinator._float(
                    pit_stop.get("lane_duration") or pit_stop.get("pit_duration")
                )
                window = (duration or DEFAULT_PIT_TRANSITION_SECONDS) + (
                    PIT_TRANSITION_GRACE_SECONDS
                )
                if pit_at <= observed_at <= pit_at + timedelta(seconds=window):
                    return True
        return False

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
        level, score, eligible = self.importance.classify(event)
        logger.debug(
            "event_importance session=%s event=%s level=%s score=%.2f agent_eligible=%s",
            event.session_key,
            event.event_type.value,
            level.value,
            score,
            eligible,
        )
        return event.model_copy(
            update={
                "id": uuid5(NAMESPACE_URL, f"apexarena:{event.dedup_key}"),
                "processed_at": event.event_time,
                "sequence_number": self._last_source_sequence[event.session_key],
                "importance_level": level,
                "importance": score,
            }
        )

    def reset_session(self, session_key: str) -> None:
        self.strategy.reset_session(session_key)
        self.positions.reset_session(session_key)
        self.overtakes.reset_session(session_key)
        self.battles.reset_session(session_key)
        self.qualifying.reset_session(session_key)
        self.importance.reset_session(session_key)
        self._pending_overtakes.pop(session_key, None)
        self._derived.pop(session_key, None)
        self._last_source_sequence.pop(session_key, None)
        self._position_change_causes.pop(session_key, None)

    @staticmethod
    def _float(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
