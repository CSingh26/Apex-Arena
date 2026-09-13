# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only exact factual history. A bounded unavailable result is not latest data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy import String, case, cast, func, select, text

from app.domain.history import (
    COMPACT_SCHEMA_VERSION,
    HISTORY_SCHEMA_VERSION,
    MAX_CHECKPOINT_BYTES,
)
from app.domain.models import EventOrigin, NormalizedRaceEvent
from app.services.history_codec import decode_context
from app.services.race_state import RaceState
from app.storage.models import NormalizedRaceEventRecord, SessionHistoryCheckpointRecord
from app.storage.repositories import SqlRaceStateSnapshotRepository

FAMILIES = frozenset({"laps", "stints", "pits", "weather", "control"})
MAX_RESPONSE_BYTES = 512 * 1024


class DetailUnavailable(RuntimeError):
    pass


class DetailSelectionError(ValueError):
    pass


class DetailBusyError(RuntimeError):
    """No queue: callers may retry after one second."""


class DetailViewChangedError(RuntimeError):
    pass


class DetailNotFoundError(RuntimeError):
    pass


@dataclass
class InflightRead:
    task: asyncio.Task
    waiters: int = 0


@dataclass
class ReadBudget:
    deadline: float
    rows: int = 0
    source_bytes: int = 0
    transforms: int = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise DetailUnavailable("deadline")


class HistoryDetailReader:
    def __init__(self, database, *, algorithm_version: str):
        self.database = database
        self.algorithm_version = algorithm_version
        self.snapshots = SqlRaceStateSnapshotRepository(database)
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._inflight: dict[tuple, InflightRead] = {}
        self._closed = False

    def _require_open(self):
        if self._closed:
            raise DetailBusyError("History detail reader is closed")

    async def close(self):
        self._closed = True
        tasks = {row.task for row in self._inflight.values() if not row.task.done()}
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=0.25)
        # Done callbacks alone release slots, including stubborn work that has
        # not exited after bounded shutdown cleanup. No new admission is allowed.
        self._cache.clear()
        self._cache_bytes = 0

    async def read_room(self, slug, *, drivers, families, rooms, states, progress):
        self._require_open()
        async with asyncio.timeout(2):
            fence = await rooms.get_history_fence(slug)
            if fence is None:
                raise DetailNotFoundError("Room not found")
            if not fence.session_key:
                return {"availability": "unavailable", "reason": "session_unlinked"}
            if fence.mode == "live":
                state = await states.get_state(fence.session_key)
                acknowledged = await progress.load(fence.session_key)
                if state.is_replay:
                    return {"availability": "unavailable", "reason": "live_view_mismatch"}
                if (
                    acknowledged is None
                    or state.sequence_number > acknowledged.completed_through_sequence
                ):
                    return {"availability": "unavailable", "reason": "cursor_unacknowledged"}
                result = await self.read(
                    fence.session_key,
                    drivers=drivers,
                    families=families,
                    view=state,
                    projection_status=self._projection_status(acknowledged, state),
                    fence=repr(fence.delivery_key()),
                )
            else:
                if fence.current_event_sequence is None:
                    return {"availability": "unavailable", "reason": "playback_missing"}
                acknowledged = await progress.load(fence.session_key)
                if (
                    acknowledged is None
                    or fence.current_event_sequence > acknowledged.completed_through_sequence
                ):
                    return {"availability": "unavailable", "reason": "cursor_unacknowledged"}
                status = "replay"
                if acknowledged is None or acknowledged.algorithm_version != self.algorithm_version:
                    status = "unavailable"
                elif acknowledged.historical_effects_unverified:
                    status = "historical_effects_unverified"
                elif acknowledged.pending_source_id is not None:
                    status = "pending"
                result = await self.read(
                    fence.session_key,
                    drivers=drivers,
                    families=families,
                    cursor=fence.current_event_sequence,
                    projection_status=status,
                    fence=repr(fence.delivery_key()),
                )
            current = await rooms.get_history_fence(slug)
            if current is None or current.delivery_key() != fence.delivery_key():
                raise DetailViewChangedError("Room view changed; refresh and retry detail")
            return {
                **result,
                "room_id": str(fence.room_id),
                "room_mode": fence.mode,
                "discussion_generation": fence.discussion_generation,
            }

    def _projection_status(self, progress, state):
        if progress is None or progress.algorithm_version != self.algorithm_version:
            return "unavailable"
        if progress.historical_effects_unverified:
            return "historical_effects_unverified"
        if progress.pending_source_id is not None:
            return "pending"
        if state.sequence_number != progress.completed_through_sequence:
            return "stale"
        return "replay" if state.is_replay else "current"

    async def read_session(self, session_key, *, drivers, families, states, progress, cursor=None):
        self._require_open()
        async with asyncio.timeout(2):
            acknowledged = await progress.load(session_key)
            if acknowledged is None:
                raise DetailNotFoundError("Session history not found")
            state = await states.get_state(session_key)
            if (
                state.sequence_number if cursor is None else cursor
            ) > acknowledged.completed_through_sequence:
                return {"availability": "unavailable", "reason": "cursor_unacknowledged"}
            status = self._projection_status(acknowledged, state)
            if cursor is not None:
                if status == "current":
                    status = "replay"
                return await self.read(
                    session_key,
                    drivers=drivers,
                    families=families,
                    cursor=cursor,
                    projection_status=status,
                )
            return await self.read(
                session_key,
                drivers=drivers,
                families=families,
                view=state,
                projection_status=status,
            )

    async def read(
        self,
        session_key: str,
        *,
        drivers: list[int],
        families: list[str],
        view: RaceState | None = None,
        cursor: int | None = None,
        projection_status: str = "current",
        fence: str = "",
    ) -> dict:
        self._require_open()
        self._validate_selection(drivers, families)
        key = (
            session_key,
            tuple(drivers),
            tuple(families),
            cursor,
            projection_status,
            fence,
            view.sequence_number if view else None,
            view.analysis_time if view else None,
            view.history_detail_status if view else None,
            view.history_reference.model_dump_json() if view and view.history_reference else None,
        )
        current = self._inflight.get(key)
        if current is None:
            if len(self._inflight) >= 2:
                raise DetailBusyError("History detail reader is busy; retry shortly")
            task = asyncio.create_task(
                self._read(
                    session_key,
                    drivers=drivers,
                    families=families,
                    view=view,
                    cursor=cursor,
                    projection_status=projection_status,
                    fence=fence,
                )
            )
            current = InflightRead(task)
            self._inflight[key] = current

            def finished(done):
                # Also covers cancellation before the coroutine starts. A slot
                # survives all caller cancellations until the actual task exits.
                if self._inflight.get(key) is current:
                    self._inflight.pop(key, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
        if current.waiters >= 4 or sum(row.waiters for row in self._inflight.values()) >= 8:
            raise DetailBusyError("History detail reader is busy; retry shortly")
        current.waiters += 1
        try:
            async with asyncio.timeout(2):
                result = await asyncio.shield(current.task)
                # Coalesced callers never receive the same mutable response.
                return json.loads(json.dumps(result, separators=(",", ":")))
        finally:
            current.waiters -= 1
            if current.waiters == 0 and not current.task.done():
                current.task.cancel()
                await asyncio.wait({current.task}, timeout=0.25)

    @staticmethod
    def _validate_selection(drivers, families):
        if (
            not 1 <= len(drivers) <= 2
            or len(set(drivers)) != len(drivers)
            or any(
                isinstance(number, bool)
                or not isinstance(number, int)
                or not 1 <= number <= 100_000
                for number in drivers
            )
            or not families
            or len(set(families)) != len(families)
            or not set(families) <= FAMILIES
        ):
            raise DetailSelectionError("Invalid detail selection")

    async def _read(
        self,
        session_key: str,
        *,
        drivers: list[int],
        families: list[str],
        view: RaceState | None = None,
        cursor: int | None = None,
        projection_status: str = "current",
        fence: str = "",
    ) -> dict:
        started = time.monotonic()
        budget = ReadBudget(started + 2)
        accepted = view.model_copy(deep=True) if view is not None else None
        try:
            async with asyncio.timeout_at(budget.deadline):
                if accepted is None:
                    if cursor is None or isinstance(cursor, bool) or cursor < 0:
                        raise DetailSelectionError("Invalid detail cursor")
                    try:
                        snapshot = await self.snapshots.at_or_before(
                            session_key,
                            cursor,
                            algorithm_identity=self.algorithm_version,
                            snapshot_schema_version=COMPACT_SCHEMA_VERSION,
                            lower_sequence=max(0, cursor - 2047),
                            statement_deadline=budget.deadline,
                        )
                        if snapshot is not None:
                            accepted = RaceState.model_validate(snapshot.state)
                    except ValueError as exc:
                        raise DetailUnavailable("invalid_compact_snapshot") from exc
                    if snapshot is None:
                        raise DetailUnavailable("compatible_snapshot_missing")
                    if (
                        accepted.sequence_number != snapshot.sequence_number
                        or accepted.session_key != snapshot.session_key
                    ):
                        raise DetailUnavailable("snapshot_cursor_mismatch")
                self._validate_view(session_key, accepted)
                reference = accepted.history_reference
                target = accepted.sequence_number if cursor is None else cursor
                if target < accepted.sequence_number:
                    raise DetailUnavailable("view_cursor_mismatch")
                key = (
                    session_key,
                    self.algorithm_version,
                    reference.checksum,
                    reference.relevant_sequence,
                    str(reference.relevant_event_id),
                    tuple(sorted(drivers)),
                    tuple(sorted(families)),
                    fence,
                )
                cached = self._cached(key) if target == accepted.sequence_number else None
                if cached is None:
                    context = await self._context(session_key, reference, budget)
                    if (
                        context.analysis_time is None
                        or context.analysis_time > accepted.analysis_time
                    ):
                        raise DetailUnavailable("analysis_time_mismatch")
                    # The compact snapshot certifies that H→S had no relevant
                    # inputs. Its canonical clock accounts for those omitted raw rows.
                    context.analysis_time = accepted.analysis_time
                    if target > accepted.sequence_number:
                        await self._scan(context, accepted.sequence_number, target, budget)
                        reference = reference.model_copy(
                            update={
                                "relevant_sequence": context.relevant_sequence,
                                "relevant_event_id": context.relevant_event_id,
                            }
                        )
                        accepted.history_sequence = context.relevant_sequence
                        accepted.history_reference = reference
                        accepted.analysis_time = context.analysis_time
                        accepted.sequence_number = target
                        key = (
                            session_key,
                            self.algorithm_version,
                            reference.checksum,
                            reference.relevant_sequence,
                            str(reference.relevant_event_id),
                            tuple(sorted(drivers)),
                            tuple(sorted(families)),
                            fence,
                        )
                    data = self._select(context, drivers, families)
                    cached = json.dumps(data, separators=(",", ":"), allow_nan=False)
                    if len(cached.encode("utf-8")) > MAX_RESPONSE_BYTES - 4096:
                        raise DetailUnavailable("response_bytes")
                    self._remember(key, cached)
                result = {
                    "availability": "available"
                    if projection_status in {"current", "replay"}
                    else "partial",
                    "projection_status": projection_status,
                    "session_key": session_key,
                    "view_sequence": accepted.sequence_number,
                    "history_sequence": accepted.history_sequence,
                    "analysis_time": accepted.analysis_time.isoformat(),
                    "history_reference": accepted.history_reference.model_dump(mode="json"),
                    "data": json.loads(cached),
                }
                budget.check()
                if (
                    len(json.dumps(result, separators=(",", ":")).encode("utf-8"))
                    > MAX_RESPONSE_BYTES
                ):
                    raise DetailUnavailable("response_bytes")
                return result
        except (DetailUnavailable, TimeoutError) as exc:
            return {
                "availability": "unavailable",
                "reason": str(exc) or "deadline",
                "session_key": session_key,
                "view_sequence": cursor
                if cursor is not None
                else view.sequence_number
                if view
                else None,
                "history_sequence": accepted.history_sequence if accepted else None,
                "history_detail_status": accepted.history_detail_status
                if accepted
                else "unavailable",
                "projection_status": projection_status,
            }

    def _validate_view(self, session_key, state):
        reference = state.history_reference
        if state.session_key != session_key:
            raise DetailUnavailable("view_session_mismatch")
        if state.history_detail_status == "checkpoint_bytes":
            raise DetailUnavailable("checkpoint_bytes")
        if reference is None or state.analysis_time is None:
            raise DetailUnavailable("legacy_history_unverified")
        if (
            reference.schema_version != HISTORY_SCHEMA_VERSION
            or state.compact_schema_version != COMPACT_SCHEMA_VERSION
            or reference.algorithm_version != self.algorithm_version
        ):
            raise DetailUnavailable("incompatible_history")
        if (
            state.history_sequence != reference.relevant_sequence
            or reference.relevant_sequence > state.sequence_number
        ):
            raise DetailUnavailable("history_cursor_mismatch")

    async def _query(self, query, budget):
        budget.check()
        remaining = budget.deadline - time.monotonic()
        async with self.database.session_factory() as session:
            await session.execute(
                text("SELECT set_config('statement_timeout', :value, true)"),
                {"value": str(max(1, min(500, int(remaining * 1000))))},
            )
            return (await session.execute(query)).mappings().all()

    async def _context(self, session_key, reference, budget):
        row = SessionHistoryCheckpointRecord
        payload = case(
            (func.octet_length(row.encoded) <= MAX_CHECKPOINT_BYTES, row.encoded), else_=None
        )
        rows = await self._query(
            select(
                row.source_id, row.source_sequence, row.checksum, payload.label("encoded")
            ).where(
                row.session_key == session_key,
                row.algorithm_version == self.algorithm_version,
                row.schema_version == HISTORY_SCHEMA_VERSION,
                row.source_sequence == reference.base_sequence,
            ),
            budget,
        )
        if not rows:
            raise DetailUnavailable("checkpoint_missing")
        saved = rows[0]
        encoded = saved["encoded"]
        if encoded is None:
            raise DetailUnavailable("checkpoint_bytes")
        if (
            saved["source_id"] != reference.base_event_id
            or saved["checksum"] != reference.checksum
            or hashlib.sha256(encoded.encode("utf-8")).hexdigest() != reference.checksum
        ):
            raise DetailUnavailable("checkpoint_checksum")
        try:
            context = decode_context(encoded, self.algorithm_version)
        except (KeyError, TypeError, ValueError) as exc:
            raise DetailUnavailable("invalid_checkpoint") from exc
        if (
            context.session_key != session_key
            or context.relevant_sequence != reference.base_sequence
            or context.history.sequence != reference.base_sequence
            or context.relevant_event_id != reference.base_event_id
        ):
            raise DetailUnavailable("checkpoint_source_mismatch")
        source = NormalizedRaceEventRecord
        origins = await self._query(
            select(source.id).where(
                source.id == reference.base_event_id,
                source.session_key == session_key,
                source.sequence_number == reference.base_sequence,
                source.event_origin == EventOrigin.SOURCE_FACT.value,
            ),
            budget,
        )
        if not origins:
            raise DetailUnavailable("checkpoint_source_missing")
        await self._scan(context, reference.base_sequence, reference.relevant_sequence, budget)
        if (
            context.relevant_sequence != reference.relevant_sequence
            or context.relevant_event_id != reference.relevant_event_id
        ):
            raise DetailUnavailable("history_source_mismatch")
        return context

    async def _scan(self, context, after, through, budget):
        span = through - after
        if span < 0 or budget.rows + span > 2048:
            raise DetailUnavailable("source_scan_limit")
        budget.rows += span
        row = NormalizedRaceEventRecord
        size = func.octet_length(cast(row.payload, String))
        columns = [column for column in row.__table__.c if column.name != "payload"]
        while after < through:
            rows = await self._query(
                select(
                    *columns,
                    size.label("payload_bytes"),
                    case((size <= 64 * 1024, row.payload), else_=None).label("payload"),
                )
                .where(
                    row.session_key == context.session_key,
                    row.sequence_number > after,
                    row.sequence_number <= through,
                )
                .order_by(row.sequence_number)
                .limit(64),
                budget,
            )
            if not rows:
                raise DetailUnavailable("source_gap")
            for saved in rows:
                if saved["sequence_number"] != after + 1:
                    raise DetailUnavailable("source_gap")
                if saved["payload"] is None:
                    raise DetailUnavailable("source_payload_bytes")
                budget.source_bytes += saved["payload_bytes"]
                if budget.source_bytes > 8 * 1024 * 1024:
                    raise DetailUnavailable("source_bytes")
                event = NormalizedRaceEvent.model_validate(dict(saved))
                if context.is_relevant_source(event) and budget.transforms >= 128:
                    raise DetailUnavailable("relevant_transform_limit")
                before = context.relevant_sequence
                context.advance_owned(event)
                if context.relevant_sequence != before:
                    budget.transforms += 1
                    if budget.transforms > 128:
                        raise DetailUnavailable("relevant_transform_limit")
                after = event.sequence_number
                budget.check()
                await asyncio.sleep(0)

    @staticmethod
    def _select(context, drivers, families):
        selected = {}
        for driver in drivers:
            history = context.history.drivers.get(str(driver))
            if history is not None:
                selected[str(driver)] = history.model_dump(
                    mode="json", include=set(families) & {"laps", "stints", "pits"}
                )
        data = {
            "drivers": selected,
            "truncation": context.history.model_dump(
                exclude={"session_key", "sequence", "drivers", "weather", "unresolved_deletions"}
            ),
            "unresolved_deletions": context.history.unresolved_deletions,
        }
        if "weather" in families:
            data["weather"] = [row.model_dump(mode="json") for row in context.history.weather]
        if "control" in families:
            data["control"] = context.control.model_dump(mode="json")
        return data

    def _cached(self, key):
        saved = self._cache.get(key)
        if saved is None:
            return None
        expires, encoded, size = saved
        if expires < time.monotonic():
            del self._cache[key]
            self._cache_bytes -= size
            return None
        self._cache.move_to_end(key)
        return encoded

    def _remember(self, key, encoded):
        if self._closed:
            return
        size = len(encoded.encode("utf-8")) + len(repr(key).encode("utf-8")) + 128
        if key in self._cache:
            self._cache_bytes -= self._cache.pop(key)[2]
        self._cache[key] = (time.monotonic() + 60, encoded, size)
        self._cache_bytes += size
        while len(self._cache) > 32 or self._cache_bytes > 8 * 1024 * 1024:
            self._cache_bytes -= self._cache.popitem(last=False)[1][2]
