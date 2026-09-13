# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.control import CurrentControlProjection
from app.domain.history import COMPACT_SCHEMA_VERSION, HistoryReference, PreparedHistoryCheckpoint
from app.domain.intelligence import BattleState, QualifyingState
from app.domain.models import (
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
    RaceStateSnapshot,
)
from app.domain.strategy import FactReference, LapObservation
from app.domain.strategy_situations import StrategyFrame
from app.domain.telemetry import CHANNEL_BOUNDS
from app.services.control_normalization import decode_openf1_drs
from app.services.history_checkpointing import HistoryCheckpointTracker
from app.services.intelligence_context import SessionFactualContext
from app.services.race_history import tyre_age_at_lap
from app.services.session_semantics import session_phases
from app.services.strategy_estimates import estimate_pace, estimate_pit_loss
from app.services.weather_analysis import WeatherAnalysis, analyze_weather


class CompactPace(BaseModel):
    availability: str = "insufficient_evidence"
    representative_seconds: float | None = None
    pace_drift_seconds_per_lap: float | None = None
    sample_count: int = Field(default=0, ge=0, le=12)
    evidence: list[FactReference] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class CompactPitLoss(BaseModel):
    availability: str = "insufficient_evidence"
    seconds: float | None = None
    lower_seconds: float | None = None
    upper_seconds: float | None = None
    method: str = "observed_two_lap_excess"
    evidence: list[FactReference] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class DriverRaceState(BaseModel):
    driver_number: int | None = None
    full_name: str | None = None
    broadcast_name: str | None = None
    team_name: str | None = None
    position: int | None = None
    gap_to_leader: float | str | None = None
    interval: float | str | None = None
    last_lap: dict[str, Any] = Field(default_factory=dict)
    pit_stops: list[dict[str, Any]] = Field(default_factory=list)
    stint: dict[str, Any] = Field(default_factory=dict)
    best_laps_by_phase: dict[str, float] = Field(default_factory=dict)
    phase_results: list[dict[str, Any]] = Field(default_factory=list)
    grid_position: int | None = None
    final_position: int | None = None
    position_change: int | None = None
    status: str = "RUNNING"
    in_pit: bool = False
    latest_lap_duration: float | None = None
    best_lap_duration: float | None = None
    completed_lap: int | None = None
    tyre_age_laps: int | None = None
    tyre_age_basis: str = "unknown"
    tyre_age_evidence: list[FactReference] = Field(default_factory=list, max_length=3)
    best_lap_availability: str = "unknown"
    pace: CompactPace = Field(default_factory=CompactPace)
    pit_loss: CompactPitLoss = Field(default_factory=CompactPitLoss)
    telemetry: dict[str, float | int | bool] = Field(default_factory=dict)
    telemetry_updated_at: datetime | None = None
    location: dict[str, float] = Field(default_factory=dict)
    location_updated_at: datetime | None = None


class RaceState(BaseModel):
    session_key: str
    control: CurrentControlProjection = Field(
        default_factory=lambda data: CurrentControlProjection(session_key=data["session_key"])
    )
    session_type: str | None = None
    current_phase: str | None = None
    phase_history: list[str] = Field(default_factory=list)
    status: str = "unknown"
    current_lap: int | None = None
    drivers: dict[str, DriverRaceState] = Field(default_factory=dict)
    pit_stop_history: list[dict[str, Any]] = Field(default_factory=list)
    race_control_feed: list[dict[str, Any]] = Field(default_factory=list)
    race_control_state: dict[str, Any] = Field(default_factory=dict)
    weather: dict[str, Any] = Field(default_factory=dict)
    starting_grid: list[dict[str, Any]] = Field(default_factory=list)
    final_classification: list[dict[str, Any]] = Field(default_factory=list)
    current_battles: list[BattleState] = Field(default_factory=list)
    recent_events: list[NormalizedRaceEvent] = Field(default_factory=list)
    qualifying_intelligence: QualifyingState | None = None
    strategy_frame: StrategyFrame | None = None
    last_updated_at: datetime | None = None
    analysis_time: datetime | None = None
    history_sequence: int = 0
    history_reference: HistoryReference | None = None
    history_detail_status: str = "legacy_history_unverified"
    compact_schema_version: int = COMPACT_SCHEMA_VERSION
    weather_analysis: WeatherAnalysis = Field(default_factory=WeatherAnalysis)
    sequence_number: int = 0
    is_replay: bool = False

    @property
    def has_telemetry(self) -> bool:
        return any(driver.telemetry for driver in self.drivers.values())

    @property
    def has_locations(self) -> bool:
        return any(driver.location for driver in self.drivers.values())


class SnapshotPersistResult(BaseModel):
    record_id: UUID
    is_new: bool


class _ReconstructedPrefix:
    def __init__(self, state, context, tracker):
        self.session_key = state.session_key
        self._payload = (state, context, tracker)

    def take(self):
        if self._payload is None:
            raise RuntimeError("Reconstruction transfer already consumed")
        payload, self._payload = self._payload, None
        return payload


class RaceStateSnapshotRepository(Protocol):
    async def insert(self, snapshot: RaceStateSnapshot) -> SnapshotPersistResult: ...

    async def latest(self, session_key: str) -> RaceStateSnapshot | None: ...

    async def at_or_before(
        self,
        session_key: str,
        sequence: int,
        *,
        algorithm_identity: str,
        snapshot_schema_version: int,
        lower_sequence: int,
        statement_deadline: float,
    ) -> RaceStateSnapshot | None: ...

    async def count(self, session_key: str | None = None) -> int: ...

    async def delete_for_session(self, session_key: str) -> None: ...


class RaceStateEngine:
    """Deterministic, provider-independent session state reducer."""

    CONTROL_EVENT_TYPES = {
        RaceEventType.RACE_CONTROL,
        RaceEventType.SAFETY_CAR,
        RaceEventType.VIRTUAL_SAFETY_CAR,
        RaceEventType.RED_FLAG,
        RaceEventType.YELLOW_FLAG,
        RaceEventType.PENALTY,
        RaceEventType.INVESTIGATION,
    }

    def __init__(
        self,
        snapshots: RaceStateSnapshotRepository,
        snapshot_every_n_events: int = 10,
        live_state_reader: Callable[[str], Awaitable[RaceState | None]] | None = None,
        *,
        retain_applied_dedup_keys: bool = True,
        algorithm_version: str | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.snapshot_every_n_events = max(1, snapshot_every_n_events)
        self.live_state_reader = live_state_reader
        self.retain_applied_dedup_keys = retain_applied_dedup_keys
        self.algorithm_version = algorithm_version
        self._states: dict[str, RaceState] = {}
        self._factual_contexts: dict[str, SessionFactualContext] = {}
        self._history_checkpoints: dict[str, HistoryCheckpointTracker] = {}
        self._applied_dedup_keys: dict[str, set[str]] = defaultdict(set)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def consume(self, event: NormalizedRaceEvent, *, persist_snapshot: bool = True) -> None:
        await self.apply(event, persist_snapshot=persist_snapshot)

    async def source_lap_witness(self, event: NormalizedRaceEvent) -> LapObservation | None:
        """Detach only the addressed retained observation at this source cursor."""
        if (
            event.event_origin is not EventOrigin.SOURCE_FACT
            or event.event_type is not RaceEventType.LAP_COMPLETED
        ):
            return None
        async with self._locks[event.session_key]:
            state = self._states.get(event.session_key)
            context = self._factual_contexts.get(event.session_key)
            if state is None or context is None or state.sequence_number != event.sequence_number:
                return None
            number = event.primary_driver_number or (
                event.driver_numbers[0] if event.driver_numbers else None
            )
            history = context.history.drivers.get(str(number))
            lap_number = event.lap_number or self._optional_int(event.payload.get("lap_number"))
            lap = (
                next((row for row in history.laps if row.lap_number == lap_number), None)
                if history
                else None
            )
            return lap.model_copy(deep=True) if lap else None

    async def apply(
        self, event: NormalizedRaceEvent, *, persist_snapshot: bool = True
    ) -> RaceState:
        async with self._locks[event.session_key]:
            state = await self._load_state(event.session_key)
            if (
                event.dedup_key in self._applied_dedup_keys[event.session_key]
                or event.sequence_number <= state.sequence_number
            ):
                return state.model_copy(deep=True)
            if (
                self.algorithm_version
                and state.sequence_number > 0
                and event.session_key not in self._factual_contexts
            ):
                raise RuntimeError("Private history reconstruction required before mutation")
            if self.retain_applied_dedup_keys:
                self._applied_dedup_keys[event.session_key].add(event.dedup_key)

            self._apply_event(state, event)
            context = self._factual_contexts.setdefault(
                event.session_key, SessionFactualContext(session_key=event.session_key)
            )
            prior_relevant = context.relevant_sequence
            changed = context.advance_owned(event)
            state.analysis_time = context.analysis_time
            state.history_sequence = context.relevant_sequence
            if self.algorithm_version and prior_relevant != context.relevant_sequence:
                tracker = self._history_checkpoints.setdefault(
                    event.session_key, HistoryCheckpointTracker()
                )
                state.history_reference = tracker.advance(
                    context,
                    self.algorithm_version,
                    terminal=(
                        event.event_type
                        in {RaceEventType.SESSION_FINISH, RaceEventType.SESSION_END}
                        or isinstance(event.payload.get("control"), dict)
                        and event.payload["control"].get("lifecycle") == "finished"
                    ),
                )
                state.history_detail_status = tracker.status
            if context.control.sequence > state.control.sequence:
                state.control = CurrentControlProjection.model_validate(
                    context.control.model_dump(exclude={"history"})
                )
                state.status = state.control.lifecycle.value
            self._refresh_driver_history(state, context, changed)
            if state.session_type in {"QUALIFYING", "SPRINT_QUALIFYING"}:
                from app.services.qualifying_intelligence import reconcile_qualifying_bests

                if state.qualifying_intelligence is None:
                    state.qualifying_intelligence = QualifyingState(session_key=state.session_key)
                state.qualifying_intelligence.phase = state.current_phase
                reconcile_qualifying_bests(state.qualifying_intelligence, state)
            if event.event_origin is EventOrigin.SOURCE_FACT:
                if event.event_type in {RaceEventType.WEATHER_UPDATE, RaceEventType.WEATHER_CHANGE}:
                    state.weather_analysis = analyze_weather(
                        context.history,
                        as_of_sequence=event.sequence_number,
                        as_of_time=state.analysis_time,
                    )
                elif state.weather_analysis.current is not None:
                    weather = state.weather_analysis
                    weather.age_seconds = max(
                        0,
                        (
                            state.analysis_time - weather.current.evidence.observed_at
                        ).total_seconds(),
                    )
                    if weather.age_seconds > 300:
                        weather.availability = "stale"
            state.sequence_number = event.sequence_number
            state.last_updated_at = (
                event.event_time
                if event.is_replay or state.last_updated_at is None
                else max(state.last_updated_at, event.event_time)
            )
            state.is_replay = event.is_replay

            if persist_snapshot and (
                event.sequence_number % self.snapshot_every_n_events == 0
                or event.event_type == RaceEventType.SESSION_FINISH
            ):
                await self._persist_snapshot(state, event)
            return state.model_copy(deep=True)

    async def install_state(self, state: RaceState) -> None:
        """Install an internally rebuilt prefix without deleting durable snapshots."""
        async with self._locks[state.session_key]:
            self._states[state.session_key] = state.model_copy(deep=True)
            self._applied_dedup_keys.pop(state.session_key, None)

    async def export_factual_context(self, session_key: str) -> SessionFactualContext:
        """Explicit cold-path detached export, never called by generic getters."""
        async with self._locks[session_key]:
            return self._factual_contexts.get(
                session_key, SessionFactualContext(session_key=session_key)
            ).model_copy(deep=True)

    async def evaluate_owned_facts(self, source, candidate_state, evaluator):
        """Synchronously inspect this exact applied prefix; detach only bounded output."""
        from app.domain.strategy_situations import StrategyDelta

        async with self._locks[source.session_key]:
            state = self._states.get(source.session_key)
            context = self._factual_contexts.get(source.session_key)
            if (
                source.event_origin is not EventOrigin.SOURCE_FACT
                or state is None
                or context is None
                or state.sequence_number != source.sequence_number
                or candidate_state.session_key != source.session_key
                or candidate_state.sequence_number != source.sequence_number
            ):
                raise ValueError("owned factual cursor mismatch")
            result = evaluator(context)
            if not isinstance(result, StrategyDelta):
                raise TypeError("owned evaluator must return a bounded StrategyDelta")
            return StrategyDelta.model_validate(result.model_dump(mode="python"))

    async def _take_reconstruction(self, session_key: str) -> _ReconstructedPrefix:
        """Retire an exclusively owned scratch engine; no borrowed mutable tree."""
        async with self._locks[session_key]:
            state = self._states.pop(session_key)
            context = self._factual_contexts.pop(
                session_key, SessionFactualContext(session_key=session_key)
            )
            tracker = self._history_checkpoints.pop(session_key, None)
            self._applied_dedup_keys.pop(session_key, None)
            return _ReconstructedPrefix(state, context, tracker)

    async def _install_reconstruction(self, transfer: _ReconstructedPrefix) -> None:
        async with self._locks[transfer.session_key]:
            state, context, tracker = transfer.take()
            if (
                state.session_key != context.session_key
                or state.history_sequence != context.relevant_sequence
            ):
                raise ValueError("Reconstructed factual prefix mismatch")
            self._states[state.session_key] = state
            self._factual_contexts[state.session_key] = context
            if tracker is not None:
                self._history_checkpoints[state.session_key] = tracker
            else:
                self._history_checkpoints.pop(state.session_key, None)
            self._applied_dedup_keys.pop(state.session_key, None)

    async def prepared_history_checkpoint(
        self, session_key: str
    ) -> PreparedHistoryCheckpoint | None:
        async with self._locks[session_key]:
            tracker = self._history_checkpoints.get(session_key)
            return tracker.checkpoint if tracker else None

    async def discard_factual_context(
        self, session_key: str, *, discard_compact: bool = False
    ) -> None:
        """Only after coordinated idle-owner invalidation; durable data is untouched."""
        async with self._locks[session_key]:
            self._factual_contexts.pop(session_key, None)
            self._history_checkpoints.pop(session_key, None)
            self._applied_dedup_keys.pop(session_key, None)
            if discard_compact:
                self._states.pop(session_key, None)

    async def get_state(self, session_key: str) -> RaceState:
        async with self._locks[session_key]:
            current = await self._load_state(session_key)
            if self.live_state_reader is not None and not current.is_replay:
                try:
                    shared = await self.live_state_reader(session_key)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Shared state unavailable error=%s", type(exc).__name__
                    )
                    shared = None
                if shared is None:
                    snapshot = await self.snapshots.latest(session_key)
                    shared = RaceState.model_validate(snapshot.state) if snapshot else None
                if shared is not None and shared.sequence_number >= current.sequence_number:
                    current = shared
                    self._states[session_key] = current
            return current.model_copy(deep=True)

    async def reset_session(
        self, session_key: str, *, is_replay: bool = False, preserve_snapshots: bool = False
    ) -> None:
        async with self._locks[session_key]:
            self._states.pop(session_key, None)
            self._factual_contexts.pop(session_key, None)
            self._history_checkpoints.pop(session_key, None)
            if is_replay:
                self._states[session_key] = RaceState(session_key=session_key, is_replay=True)
            self._applied_dedup_keys.pop(session_key, None)
            delete_for_session = getattr(self.snapshots, "delete_for_session", None)
            if delete_for_session is not None and not preserve_snapshots:
                await delete_for_session(session_key)

    async def set_intelligence(
        self,
        session_key: str,
        *,
        current_battles: list[BattleState],
        qualifying: QualifyingState | None,
        strategy_frame: StrategyFrame | None = None,
        return_state: bool = True,
    ) -> RaceState | None:
        async with self._locks[session_key]:
            state = await self._load_state(session_key)
            state.current_battles = [battle.model_copy(deep=True) for battle in current_battles]
            state.qualifying_intelligence = (
                qualifying.model_copy(deep=True) if qualifying is not None else None
            )
            if strategy_frame is not None:
                state.strategy_frame = strategy_frame.model_copy(deep=True)
            return state.model_copy(deep=True) if return_state else None

    async def prime_driver_profiles(
        self,
        session_key: str,
        profiles: list[NormalizedRaceEvent],
    ) -> RaceState:
        """Populate stable driver identity before replaying timing samples.

        Historical providers can return the drivers endpoint after high-volume
        timing endpoints. These profiles are static session metadata, so they can
        be applied without advancing the replay sequence or revealing future
        race state.
        """

        async with self._locks[session_key]:
            state = await self._load_state(session_key)
            for profile in profiles:
                if (
                    profile.session_key != session_key
                    or profile.event_type != RaceEventType.DRIVER_UPDATE
                ):
                    continue
                self._apply_driver_profile(self._driver(state, profile), profile.payload)
            return state.model_copy(deep=True)

    async def _load_state(self, session_key: str) -> RaceState:
        if session_key in self._states:
            return self._states[session_key]
        snapshot = await self.snapshots.latest(session_key)
        state = (
            RaceState.model_validate(snapshot.state)
            if snapshot is not None
            else RaceState(session_key=session_key)
        )
        self._states[session_key] = state
        return state

    def _apply_event(self, state: RaceState, event: NormalizedRaceEvent) -> None:
        payload = event.payload
        event_type = event.event_type
        if event.event_origin is EventOrigin.DERIVED:
            state.recent_events.append(event.model_copy(deep=True))
            state.recent_events = state.recent_events[-20:]
        normalized_session_type = payload.get("normalized_session_type")
        if normalized_session_type:
            state.session_type = str(normalized_session_type)
        session_phase = str(payload.get("session_phase") or "").upper()
        order = session_phases(state.session_type) or ["Q1", "Q2", "Q3"]
        explicit_phase = event_type is RaceEventType.QUALIFYING_PHASE
        known_phase = session_phase in order
        forward_context = known_phase and (
            state.current_phase is None
            or state.current_phase in order
            and order.index(session_phase) >= order.index(state.current_phase)
        )
        if known_phase and (explicit_phase or forward_context):
            state.current_phase = session_phase
            if state.current_phase not in state.phase_history:
                state.phase_history.append(state.current_phase)
        if event.lap_number is not None:
            state.current_lap = max(state.current_lap or 0, event.lap_number)
        if event_type == RaceEventType.DRIVER_UPDATE:
            self._apply_driver_profile(self._driver(state, event), payload)
        elif event_type == RaceEventType.POSITION_SAMPLE:
            driver = self._driver(state, event)
            position = self._optional_int(payload.get("position"))
            if position is not None and driver.position is not None:
                driver.position_change = driver.position - position
            driver.position = position
        elif event_type == RaceEventType.INTERVAL_SAMPLE:
            driver = self._driver(state, event)
            driver.gap_to_leader = payload.get("gap_to_leader")
            driver.interval = payload.get("interval")
        elif event_type == RaceEventType.LAP_COMPLETED:
            lap_number = event.lap_number or self._optional_int(payload.get("lap_number"))
            if lap_number is not None:
                state.current_lap = max(state.current_lap or 0, lap_number)
            driver = self._driver(state, event)
            if lap_number is not None and lap_number >= (driver.completed_lap or 0):
                driver.last_lap = {
                    **(driver.last_lap if lap_number == driver.completed_lap else {}),
                    **payload,
                    "lap_number": lap_number,
                }
            duration = self._optional_float(payload.get("lap_duration"))
            if duration is not None and self._valid_duration(duration, maximum=300):
                driver.latest_lap_duration = duration
                if driver.best_lap_duration is None or duration < driver.best_lap_duration:
                    driver.best_lap_duration = duration
            if state.current_phase and duration is not None:
                previous = driver.best_laps_by_phase.get(state.current_phase)
                if previous is None or duration < previous:
                    driver.best_laps_by_phase[state.current_phase] = duration
        elif event_type == RaceEventType.PIT_STOP:
            pit_stop = dict(payload)
            pit_stop.setdefault("event_time", event.event_time.isoformat())
            driver = self._driver(state, event)
            pit_stop["driver_number"] = driver.driver_number
            pit_stop["lap_number"] = event.lap_number or payload.get("lap_number")
            roster = {row.get("driver_number") for row in state.pit_stop_history}
            if driver.driver_number not in roster and len(roster) >= 64:
                return
            if pit_stop["lap_number"] is not None:
                driver.pit_stops = [
                    row
                    for row in driver.pit_stops
                    if row.get("lap_number") != pit_stop["lap_number"]
                ]
            driver.pit_stops.append(pit_stop)
            driver.pit_stops = driver.pit_stops[-24:]
            state.pit_stop_history = [
                row
                for row in state.pit_stop_history
                if row.get("driver_number") != driver.driver_number
            ]
            state.pit_stop_history.extend(driver.pit_stops)
        elif event_type == RaceEventType.PIT_ENTRY:
            self._driver(state, event).in_pit = True
        elif event_type == RaceEventType.PIT_EXIT:
            self._driver(state, event).in_pit = False
        elif event_type in {
            RaceEventType.DRIVER_STOPPED,
            RaceEventType.DRIVER_RETIRED,
            RaceEventType.RETIREMENT,
        }:
            self._driver(state, event).status = (
                "STOPPED" if event_type is RaceEventType.DRIVER_STOPPED else "RETIRED"
            )
        elif event_type == RaceEventType.STINT_UPDATE:
            self._driver(state, event).stint = dict(payload)
        elif event_type == RaceEventType.CAR_DATA_SAMPLE:
            telemetry = self._telemetry(payload)
            if telemetry:
                driver = self._driver(state, event)
                driver.telemetry = telemetry
                driver.telemetry_updated_at = event.event_time
        elif event_type == RaceEventType.LOCATION_SAMPLE:
            location = self._location(payload)
            if location:
                driver = self._driver(state, event)
                driver.location = location
                driver.location_updated_at = event.event_time
        elif event_type == RaceEventType.LAP_DELETED:
            driver = self._driver(state, event)
            if driver.last_lap.get("lap_number") == event.lap_number:
                driver.last_lap = {**driver.last_lap, "deleted": True}
        elif event_type == RaceEventType.SESSION_RESULT:
            driver = self._driver(state, event)
            driver.final_position = self._optional_int(payload.get("position"))
            rows = payload.get("phase_results")
            if isinstance(rows, list):
                driver.phase_results = [dict(row) for row in rows if isinstance(row, dict)]
            result = dict(payload)
            state.final_classification = [
                existing
                for existing in state.final_classification
                if existing.get("driver_number") != payload.get("driver_number")
            ]
            state.final_classification.append(result)
            state.final_classification.sort(
                key=lambda row: self._optional_int(row.get("position")) or 10_000
            )
        elif event_type == RaceEventType.STARTING_GRID:
            driver = self._driver(state, event)
            driver.grid_position = self._optional_int(
                payload.get("position") or payload.get("grid_position")
            )
            grid_row = dict(payload)
            state.starting_grid = [
                existing
                for existing in state.starting_grid
                if existing.get("driver_number") != payload.get("driver_number")
            ]
            state.starting_grid.append(grid_row)
            state.starting_grid.sort(
                key=lambda row: (
                    self._optional_int(row.get("position") or row.get("grid_position")) or 10_000
                )
            )
        elif event_type in self.CONTROL_EVENT_TYPES:
            control_event = {
                "event_type": event_type.value,
                "event_time": event.event_time.isoformat(),
                **payload,
            }
            state.race_control_feed.append(control_event)
            state.race_control_feed = state.race_control_feed[-50:]
            state.race_control_state = {
                "event_type": event_type.value,
                "message": payload.get("message"),
                "flag": payload.get("flag"),
            }
        elif event_type in {RaceEventType.WEATHER_UPDATE, RaceEventType.WEATHER_CHANGE}:
            state.weather = dict(payload)
        elif event_type in {
            RaceEventType.BATTLE_STARTED,
            RaceEventType.BATTLE_INTENSIFIED,
            RaceEventType.DRS_RANGE_ENTERED,
            RaceEventType.DRS_RANGE_EXITED,
        }:
            battle_payload = payload.get("battle")
            if isinstance(battle_payload, dict):
                battle = BattleState.model_validate(battle_payload)
                state.current_battles = [
                    current for current in state.current_battles if current.id != battle.id
                ]
                state.current_battles.append(battle)
        elif event_type == RaceEventType.BATTLE_ENDED:
            battle_payload = payload.get("battle")
            if isinstance(battle_payload, dict) and battle_payload.get("id"):
                battle_id = str(battle_payload["id"])
                state.current_battles = [
                    current for current in state.current_battles if current.id != battle_id
                ]

    @staticmethod
    def _refresh_driver_history(
        state: RaceState, context: SessionFactualContext, changed: set[str]
    ) -> None:
        phases = session_phases(state.session_type)
        for key in changed:
            history = context.history.drivers.get(key)
            if history is None:
                continue
            driver = state.drivers.setdefault(key, DriverRaceState(driver_number=int(key)))
            completed = [lap for lap in history.laps if lap.duration_seconds is not None]
            latest = max(completed, key=lambda lap: lap.lap_number, default=None)
            valid = [lap for lap in completed if not lap.deleted]
            driver.completed_lap = latest.lap_number if latest else None
            driver.latest_lap_duration = (
                latest.duration_seconds if latest and not latest.deleted else None
            )
            if latest:
                driver.last_lap = {
                    "lap_number": latest.lap_number,
                    "lap_duration": driver.latest_lap_duration,
                    "deleted": latest.deleted,
                    "date_start": latest.interval_start.isoformat()
                    if latest.interval_start
                    else None,
                    "session_phase": latest.phase,
                    "is_pit_out_lap": "pit_out" in latest.exclusions,
                    **{
                        f"duration_sector_{index}": value
                        for index, value in enumerate(latest.sectors_seconds, 1)
                    },
                }
            else:
                driver.last_lap = {}
            incomplete = (
                context.history.lap_history_truncated
                or context.history.driver_history_truncated
                or bool(context.history.unresolved_deletions)
            )
            driver.best_lap_availability = (
                "partial" if incomplete else "available" if valid else "unknown"
            )
            driver.best_lap_duration = (
                min((lap.duration_seconds for lap in valid), default=None)
                if not incomplete
                else None
            )
            driver.best_laps_by_phase = {}
            if not incomplete:
                for lap in valid:
                    if lap.phase and (not phases or lap.phase in phases):
                        driver.best_laps_by_phase[lap.phase] = min(
                            driver.best_laps_by_phase.get(lap.phase, lap.duration_seconds),
                            lap.duration_seconds,
                        )
            stint = max(history.stints, key=lambda row: row.stint_number, default=None)
            driver.stint = stint.model_dump(exclude={"evidence"}) if stint else {}
            driver.tyre_age_laps = (
                tyre_age_at_lap(stint, latest.lap_number) if stint and latest else None
            )
            driver.tyre_age_basis = (
                "observed_stint_and_driver_completed_lap"
                if driver.tyre_age_laps is not None
                else "unknown"
            )
            driver.tyre_age_evidence = (
                [stint.evidence.model_copy(deep=True), latest.evidence.model_copy(deep=True)]
                if driver.tyre_age_laps is not None
                else []
            )
            pace = estimate_pace(history)
            driver.pace = CompactPace(
                availability=pace.availability,
                representative_seconds=pace.representative_seconds,
                pace_drift_seconds_per_lap=pace.pace_drift_seconds_per_lap,
                sample_count=len(pace.sample_laps),
                evidence=[row.model_copy(deep=True) for row in pace.evidence[-3:]],
                limitations=[*pace.limitations, "compact_evidence_subset"],
            )
            pit = max(history.pits, key=lambda row: row.lap_number, default=None)
            loss = estimate_pit_loss(history, pit.lap_number) if pit else None
            driver.pit_loss = (
                CompactPitLoss(
                    availability=loss.availability,
                    seconds=loss.seconds,
                    lower_seconds=loss.lower_seconds,
                    upper_seconds=loss.upper_seconds,
                    evidence=[row.model_copy(deep=True) for row in loss.evidence[-3:]],
                    limitations=[*loss.limitations, "compact_evidence_subset"],
                )
                if loss
                else CompactPitLoss()
            )

    @staticmethod
    def _driver(state: RaceState, event: NormalizedRaceEvent) -> DriverRaceState:
        driver_number = event.driver_numbers[0] if event.driver_numbers else 0
        return state.drivers.setdefault(
            str(driver_number), DriverRaceState(driver_number=driver_number)
        )

    @classmethod
    def _apply_driver_profile(cls, driver: DriverRaceState, payload: dict[str, Any]) -> None:
        driver.full_name = cls._optional_text(
            payload.get("resolved_driver_name") or payload.get("full_name")
        )
        driver.broadcast_name = cls._optional_text(
            payload.get("resolved_broadcast_name") or payload.get("broadcast_name")
        )
        driver.team_name = cls._optional_text(
            payload.get("resolved_team_name") or payload.get("team_name")
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        text = " ".join(str(value or "").split())
        return text or None

    @staticmethod
    def _valid_duration(value: float, *, maximum: float) -> bool:
        return math.isfinite(value) and 0 < value <= maximum

    @classmethod
    def _telemetry(cls, payload: dict[str, Any]) -> dict[str, float | int | bool]:
        # Provider field names differ from published channel names; the ranges
        # themselves are shared with the history reader.
        sources = {"gear": "n_gear"}
        normalized: dict[str, float | int | bool] = {}
        for target, (minimum, maximum) in CHANNEL_BOUNDS.items():
            value = cls._optional_float(payload.get(sources.get(target, target)))
            if value is not None and math.isfinite(value) and minimum <= value <= maximum:
                normalized[target] = int(value) if target in {"gear", "rpm"} else value
        drs = payload.get("drs")
        if isinstance(drs, int) and not isinstance(drs, bool):
            normalized["drs_code"] = drs
            observed = decode_openf1_drs(drs)
            if observed != "unknown":
                normalized["drs"] = observed == "open"
        return normalized

    @classmethod
    def _location(cls, payload: dict[str, Any]) -> dict[str, float]:
        location: dict[str, float] = {}
        for field in ("x", "y", "z"):
            value = cls._optional_float(payload.get(field))
            if value is None or not math.isfinite(value) or abs(value) > 100_000:
                return {}
            location[field] = value
        return location

    async def _persist_snapshot(
        self, state: RaceState, event: NormalizedRaceEvent
    ) -> SnapshotPersistResult:
        now = datetime.now(UTC)
        snapshot = RaceStateSnapshot(
            meeting_id=event.meeting_id,
            session_id=event.session_id,
            session_key=event.session_key,
            snapshot_time=event.event_time,
            sequence_number=event.sequence_number,
            current_lap=state.current_lap,
            session_status=state.status,
            state=state.model_dump(mode="json"),
            created_at=now,
        )
        return await self.snapshots.insert(snapshot)
