# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domain.models import EventImportance, EventOrigin, NormalizedRaceEvent, RaceEventType
from app.services.normalization import OpenF1EventNormalizer
from app.services.raw_events import RawEventInput, RawProviderEventService

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.intelligence_recovery import IntelligenceProjection


class NormalizedPersistResult(BaseModel):
    record_id: UUID
    is_new: bool


class NormalizedEventRepository(Protocol):
    async def insert(self, event: NormalizedRaceEvent) -> NormalizedPersistResult: ...

    async def max_sequence(self, session_key: str) -> int: ...

    async def latest_session_key(self) -> str | None: ...

    async def count(self, session_key: str | None = None) -> int: ...

    async def list_for_session(
        self,
        session_key: str,
        after_sequence: int = 0,
        limit: int = 100,
        *,
        before_sequence: int | None = None,
        event_types: list[RaceEventType] | None = None,
        driver_number: int | None = None,
        lap_number: int | None = None,
        minimum_importance: EventImportance | None = None,
        event_origin: EventOrigin | None = None,
        before_time: datetime | None = None,
    ) -> list[NormalizedRaceEvent]: ...


class EventConsumer(Protocol):
    async def consume(self, event: NormalizedRaceEvent) -> None: ...


class PipelineResult(BaseModel):
    raw_inserted: int = 0
    raw_duplicates: int = 0
    normalized_inserted: int = 0
    normalized_duplicates: int = 0
    buffered: int = 0

    def add(self, other: PipelineResult) -> None:
        self.raw_inserted += other.raw_inserted
        self.raw_duplicates += other.raw_duplicates
        self.normalized_inserted += other.normalized_inserted
        self.normalized_duplicates += other.normalized_duplicates
        self.buffered += other.buffered


class EventDeduplicator:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._expires_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def is_duplicate(self, dedup_key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            expired = [key for key, expiry in self._expires_at.items() if expiry <= now]
            for key in expired:
                self._expires_at.pop(key, None)
            if dedup_key in self._expires_at:
                return True
            self._expires_at[dedup_key] = now + self.ttl_seconds
            return False

    async def forget(self, dedup_key: str) -> None:
        async with self._lock:
            self._expires_at.pop(dedup_key, None)


class EventOrderingBuffer:
    """Event-time watermark buffer; adapters flush at batch or idle boundaries."""

    def __init__(self, window_ms: int) -> None:
        self.window = timedelta(milliseconds=max(0, window_ms))
        self._buffers: dict[str, list[tuple[datetime, int, NormalizedRaceEvent]]] = defaultdict(
            list
        )
        self._latest_event_time: dict[str, datetime] = {}
        self._counter = itertools.count()

    def add(self, event: NormalizedRaceEvent) -> list[NormalizedRaceEvent]:
        session_key = event.session_key
        heapq.heappush(
            self._buffers[session_key],
            (event.event_time, next(self._counter), event),
        )
        latest = max(event.event_time, self._latest_event_time.get(session_key, event.event_time))
        self._latest_event_time[session_key] = latest
        return self._pop_until(session_key, latest - self.window)

    def flush(self, session_key: str) -> list[NormalizedRaceEvent]:
        events: list[NormalizedRaceEvent] = []
        buffer = self._buffers.get(session_key, [])
        while buffer:
            events.append(heapq.heappop(buffer)[2])
        self._buffers.pop(session_key, None)
        self._latest_event_time.pop(session_key, None)
        return events

    def pending(self, session_key: str | None = None) -> int:
        if session_key is not None:
            return len(self._buffers.get(session_key, []))
        return sum(len(buffer) for buffer in self._buffers.values())

    def requeue(self, events: list[NormalizedRaceEvent]) -> None:
        """Retain the unacknowledged tail when a flush fails mid-batch."""
        for event in events:
            key = event.session_key
            heapq.heappush(self._buffers[key], (event.event_time, next(self._counter), event))
            self._latest_event_time[key] = max(
                event.event_time, self._latest_event_time.get(key, event.event_time)
            )

    def _pop_until(self, session_key: str, watermark: datetime) -> list[NormalizedRaceEvent]:
        events: list[NormalizedRaceEvent] = []
        buffer = self._buffers[session_key]
        while buffer and buffer[0][0] <= watermark:
            events.append(heapq.heappop(buffer)[2])
        return events


class SequenceNumberService:
    def __init__(self, repository: NormalizedEventRepository) -> None:
        self.repository = repository
        self._sequences: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def next(self, session_key: str) -> int:
        async with self._locks[session_key]:
            if session_key not in self._sequences:
                self._sequences[session_key] = await self.repository.max_sequence(session_key)
            self._sequences[session_key] += 1
            return self._sequences[session_key]


class RaceEventProcessor:
    def __init__(
        self,
        *,
        raw_events: RawProviderEventService,
        normalizer: OpenF1EventNormalizer,
        normalized_repository: NormalizedEventRepository,
        deduplicator: EventDeduplicator,
        ordering_buffer: EventOrderingBuffer,
        sequence_numbers: SequenceNumberService,
        consumers: list[EventConsumer] | None = None,
        critical_projection: IntelligenceProjection | None = None,
    ) -> None:
        self.raw_events = raw_events
        self.normalizer = normalizer
        self.normalized_repository = normalized_repository
        self.deduplicator = deduplicator
        self.ordering_buffer = ordering_buffer
        self.sequence_numbers = sequence_numbers
        self.consumers = consumers or []
        self.critical_projection = critical_projection
        # One intake owner per session through reduction and derived publication,
        # not merely through sequence allocation. This is process-local ownership.
        self._session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._initialized_sessions: set[str] = set()

    async def initialize_session(self, session_key: str) -> None:
        """Warm internal consumers before either live transport can admit facts."""
        async with self._session_scope(session_key):
            await self._initialize_session(session_key)

    @asynccontextmanager
    async def _session_scope(self, session_key: str):
        # Lock order: processor session, context admission, private engine.
        async with self._session_locks[session_key]:
            if self.critical_projection is None:
                yield
            else:
                async with self.critical_projection.context_pool.pin(session_key):
                    yield

    async def _initialize_session(self, session_key: str) -> None:
        if self.critical_projection is not None:
            await self.critical_projection.initialize_session(session_key)
            return
        if session_key in self._initialized_sessions:
            return
        for consumer in self.consumers:
            restore = getattr(consumer, "restore_session", None)
            if restore is not None:
                await restore(session_key, self.normalized_repository)
        # Failed/cancelled reconstruction is retryable; no new source was admitted.
        self._initialized_sessions.add(session_key)

    async def recover_session(self, session_key: str) -> None:
        """Retry durable critical work without replaying historical notifications."""
        async with self._session_scope(session_key):
            await self._initialize_session(session_key)

    async def ingest(self, raw: RawEventInput) -> PipelineResult:
        session_key = str(raw.session_key or raw.raw_payload.get("session_key") or "unknown")
        async with self._session_scope(session_key):
            await self._initialize_session(session_key)
            return await self._ingest(raw)

    async def _ingest(self, raw: RawEventInput) -> PipelineResult:
        raw_result = await self.raw_events.persist(raw)
        if not raw_result.is_new and not raw_result.needs_normalization:
            return PipelineResult(raw_duplicates=1)

        event = self.normalizer.normalize(raw, raw_result.record_id)
        ready = self.ordering_buffer.add(event)
        result = PipelineResult(
            raw_inserted=int(raw_result.is_new),
            raw_duplicates=int(not raw_result.is_new),
            buffered=self.ordering_buffer.pending(),
        )
        result.add(await self._persist_ready(ready))
        result.buffered = self.ordering_buffer.pending()
        return result

    async def ingest_batch(self, events: list[RawEventInput]) -> PipelineResult:
        result = PipelineResult()
        sessions: dict[str, list[RawEventInput]] = {}
        for raw in events:
            resolved_session = raw.session_key or raw.raw_payload.get("session_key") or "unknown"
            sessions.setdefault(str(resolved_session), []).append(raw)
        # Preserve each session's arrival order and its pin through final flush.
        # Never hold several session locks/pool slots while waiting for another.
        for session_key, rows in sessions.items():
            async with self._session_scope(session_key):
                await self._initialize_session(session_key)
                for raw in rows:
                    result.add(await self._ingest(raw))
                result.add(await self._flush_session(session_key))
        result.buffered = self.ordering_buffer.pending()
        return result

    async def flush_session(self, session_key: str) -> PipelineResult:
        async with self._session_scope(session_key):
            await self._initialize_session(session_key)
            return await self._flush_session(session_key)

    async def _flush_session(self, session_key: str) -> PipelineResult:
        result = await self._persist_ready(self.ordering_buffer.flush(session_key))
        result.buffered = self.ordering_buffer.pending()
        return result

    async def _persist_ready(self, events: list[NormalizedRaceEvent]) -> PipelineResult:
        result = PipelineResult()
        for index, event in enumerate(events):
            try:
                result.add(await self._persist_ordered(event))
            except BaseException:
                self.ordering_buffer.requeue(events[index:])
                raise
        return result

    async def _persist_ordered(self, event: NormalizedRaceEvent) -> PipelineResult:
        if self.critical_projection is not None:
            committed, new = await self.critical_projection.append(event)
            for persisted in committed:
                await self._notify_consumers(persisted)
            if committed:
                for consumer in self.consumers:
                    finish = getattr(consumer, "finish_committed_bundle", None)
                    if finish is None:
                        continue
                    try:
                        await finish(event.session_key)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error(
                            "Committed state consumer failed consumer=%s error=%s",
                            type(consumer).__name__,
                            type(exc).__name__,
                        )
            if event.raw_event_id:
                await self.raw_events.mark_status(
                    event.raw_event_id, "normalized" if new else "duplicate"
                )
            return PipelineResult(
                normalized_inserted=len(committed), normalized_duplicates=int(not new)
            )
        if await self.deduplicator.is_duplicate(event.dedup_key):
            if event.raw_event_id:
                await self.raw_events.mark_status(event.raw_event_id, "duplicate")
            return PipelineResult(normalized_duplicates=1)

        sequence_number = await self.sequence_numbers.next(event.session_key)
        sequenced = event.model_copy(update={"sequence_number": sequence_number})
        try:
            persisted = await self.normalized_repository.insert(sequenced)
        except BaseException:
            await self.deduplicator.forget(event.dedup_key)
            raise
        if not persisted.is_new:
            if event.raw_event_id:
                await self.raw_events.mark_status(event.raw_event_id, "duplicate")
            return PipelineResult(normalized_duplicates=1)

        await self._notify_consumers(sequenced)
        result = PipelineResult(normalized_inserted=1)
        for consumer in self.consumers:
            drain = getattr(consumer, "drain_derived", None)
            if drain is None:
                continue
            for derived in drain(sequenced.session_key):
                result.add(await self._persist_derived(derived))
        if event.raw_event_id:
            await self.raw_events.mark_status(event.raw_event_id, "normalized")
        return result

    async def _persist_derived(self, event: NormalizedRaceEvent) -> PipelineResult:
        if await self.deduplicator.is_duplicate(event.dedup_key):
            return PipelineResult(normalized_duplicates=1)
        sequence_number = await self.sequence_numbers.next(event.session_key)
        sequenced = event.model_copy(update={"sequence_number": sequence_number})
        persisted = await self.normalized_repository.insert(sequenced)
        if not persisted.is_new:
            return PipelineResult(normalized_duplicates=1)
        await self._notify_consumers(sequenced)
        return PipelineResult(normalized_inserted=1)

    async def _notify_consumers(self, event: NormalizedRaceEvent) -> None:
        for consumer in self.consumers:
            if self.critical_projection is not None and consumer in (
                self.critical_projection.public_state,
                self.critical_projection.coordinator,
            ):
                continue
            try:
                consume = (
                    getattr(consumer, "consume_committed_event", consumer.consume)
                    if self.critical_projection is not None
                    else consumer.consume
                )
                await consume(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Persistence is authoritative. Redis or discussion outages must
                # not turn an already committed provider event into a failed row.
                logger.error(
                    "Normalized event consumer failed consumer=%s error=%s",
                    type(consumer).__name__,
                    type(exc).__name__,
                )
