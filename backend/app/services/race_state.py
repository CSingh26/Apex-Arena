# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.models import (
    NormalizedRaceEvent,
    RaceEventType,
    RaceStateSnapshot,
)


class DriverRaceState(BaseModel):
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


class RaceState(BaseModel):
    session_key: str
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
    last_updated_at: datetime | None = None
    sequence_number: int = 0
    is_replay: bool = False


class SnapshotPersistResult(BaseModel):
    record_id: UUID
    is_new: bool


class RaceStateSnapshotRepository(Protocol):
    async def insert(self, snapshot: RaceStateSnapshot) -> SnapshotPersistResult: ...

    async def latest(self, session_key: str) -> RaceStateSnapshot | None: ...

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
    ) -> None:
        self.snapshots = snapshots
        self.snapshot_every_n_events = max(1, snapshot_every_n_events)
        self._states: dict[str, RaceState] = {}
        self._applied_dedup_keys: dict[str, set[str]] = defaultdict(set)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def consume(self, event: NormalizedRaceEvent) -> None:
        await self.apply(event)

    async def apply(self, event: NormalizedRaceEvent) -> RaceState:
        async with self._locks[event.session_key]:
            state = await self._load_state(event.session_key)
            if event.dedup_key in self._applied_dedup_keys[event.session_key]:
                return state.model_copy(deep=True)
            self._applied_dedup_keys[event.session_key].add(event.dedup_key)

            self._apply_event(state, event)
            state.sequence_number = event.sequence_number
            state.last_updated_at = event.event_time
            state.is_replay = event.is_replay

            if (
                event.sequence_number % self.snapshot_every_n_events == 0
                or event.event_type == RaceEventType.SESSION_FINISH
            ):
                await self._persist_snapshot(state, event)
            return state.model_copy(deep=True)

    async def get_state(self, session_key: str) -> RaceState:
        async with self._locks[session_key]:
            return (await self._load_state(session_key)).model_copy(deep=True)

    async def reset_session(self, session_key: str) -> None:
        async with self._locks[session_key]:
            self._states.pop(session_key, None)
            self._applied_dedup_keys.pop(session_key, None)
            delete_for_session = getattr(self.snapshots, "delete_for_session", None)
            if delete_for_session is not None:
                await delete_for_session(session_key)

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
        normalized_session_type = payload.get("normalized_session_type")
        if normalized_session_type:
            state.session_type = str(normalized_session_type)
        session_phase = payload.get("session_phase")
        if session_phase:
            state.current_phase = str(session_phase)
            if state.current_phase not in state.phase_history:
                state.phase_history.append(state.current_phase)
        if event.lap_number is not None:
            state.current_lap = max(state.current_lap or 0, event.lap_number)
        if event_type in {RaceEventType.SESSION_START, RaceEventType.RACE_START}:
            state.status = str(payload.get("status") or "started")
        elif event_type == RaceEventType.SESSION_STATUS:
            state.status = str(payload.get("status") or payload.get("message") or "unknown")
        elif event_type == RaceEventType.QUALIFYING_PHASE:
            state.status = str(payload.get("status") or "running")
        elif event_type == RaceEventType.DRIVER_UPDATE:
            driver = self._driver(state, event)
            driver.full_name = self._optional_text(
                payload.get("resolved_driver_name") or payload.get("full_name")
            )
            driver.broadcast_name = self._optional_text(
                payload.get("resolved_broadcast_name") or payload.get("broadcast_name")
            )
            driver.team_name = self._optional_text(
                payload.get("resolved_team_name") or payload.get("team_name")
            )
        elif event_type == RaceEventType.SESSION_FINISH:
            state.status = "finished"
        elif event_type == RaceEventType.POSITION_SAMPLE:
            driver = self._driver(state, event)
            driver.position = self._optional_int(payload.get("position"))
        elif event_type == RaceEventType.INTERVAL_SAMPLE:
            driver = self._driver(state, event)
            driver.gap_to_leader = payload.get("gap_to_leader")
            driver.interval = payload.get("interval")
        elif event_type == RaceEventType.LAP_COMPLETED:
            lap_number = event.lap_number or self._optional_int(payload.get("lap_number"))
            if lap_number is not None:
                state.current_lap = max(state.current_lap or 0, lap_number)
            driver = self._driver(state, event)
            driver.last_lap = dict(payload)
            duration = self._optional_float(payload.get("lap_duration"))
            if state.current_phase and duration is not None:
                previous = driver.best_laps_by_phase.get(state.current_phase)
                if previous is None or duration < previous:
                    driver.best_laps_by_phase[state.current_phase] = duration
        elif event_type == RaceEventType.PIT_STOP:
            pit_stop = dict(payload)
            state.pit_stop_history.append(pit_stop)
            self._driver(state, event).pit_stops.append(pit_stop)
        elif event_type == RaceEventType.STINT_UPDATE:
            self._driver(state, event).stint = dict(payload)
        elif event_type == RaceEventType.LAP_DELETED:
            self._driver(state, event).last_lap = {
                **self._driver(state, event).last_lap,
                "deleted": True,
                "deletion_message": payload.get("message"),
            }
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

    @staticmethod
    def _driver(state: RaceState, event: NormalizedRaceEvent) -> DriverRaceState:
        driver_number = event.driver_numbers[0] if event.driver_numbers else 0
        return state.drivers.setdefault(str(driver_number), DriverRaceState())

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
