# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.domain.models import NormalizedRaceEvent
from app.services.normalization import OpenF1EventNormalizer
from app.services.raw_events import RawEventInput, RawProviderEventService

logger = logging.getLogger(__name__)


class NormalizedPersistResult(BaseModel):
    record_id: UUID
    is_new: bool


class NormalizedEventRepository(Protocol):
    async def insert(self, event: NormalizedRaceEvent) -> NormalizedPersistResult: ...

    async def max_sequence(self, session_key: str) -> int: ...

    async def latest_session_key(self) -> str | None: ...

    async def count(self, session_key: str | None = None) -> int: ...

    async def list_for_session(
        self, session_key: str, after_sequence: int = 0, limit: int = 100
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
    ) -> None:
        self.raw_events = raw_events
        self.normalizer = normalizer
        self.normalized_repository = normalized_repository
        self.deduplicator = deduplicator
        self.ordering_buffer = ordering_buffer
        self.sequence_numbers = sequence_numbers
        self.consumers = consumers or []

    async def ingest(self, raw: RawEventInput) -> PipelineResult:
        raw_result = await self.raw_events.persist(raw)
        if not raw_result.is_new:
            return PipelineResult(raw_duplicates=1)

        event = self.normalizer.normalize(raw, raw_result.record_id)
        ready = self.ordering_buffer.add(event)
        result = PipelineResult(raw_inserted=1, buffered=self.ordering_buffer.pending())
        for ordered_event in ready:
            result.add(await self._persist_ordered(ordered_event))
        result.buffered = self.ordering_buffer.pending()
        return result

    async def ingest_batch(self, events: list[RawEventInput]) -> PipelineResult:
        result = PipelineResult()
        session_keys: set[str] = set()
        for raw in events:
            resolved_session = raw.session_key or raw.raw_payload.get("session_key") or "unknown"
            session_keys.add(str(resolved_session))
            result.add(await self.ingest(raw))
        for session_key in session_keys:
            result.add(await self.flush_session(session_key))
        result.buffered = self.ordering_buffer.pending()
        return result

    async def flush_session(self, session_key: str) -> PipelineResult:
        result = PipelineResult()
        for event in self.ordering_buffer.flush(session_key):
            result.add(await self._persist_ordered(event))
        result.buffered = self.ordering_buffer.pending()
        return result

    async def _persist_ordered(self, event: NormalizedRaceEvent) -> PipelineResult:
        if await self.deduplicator.is_duplicate(event.dedup_key):
            if event.raw_event_id:
                await self.raw_events.mark_status(event.raw_event_id, "duplicate")
            return PipelineResult(normalized_duplicates=1)

        sequence_number = await self.sequence_numbers.next(event.session_key)
        sequenced = event.model_copy(update={"sequence_number": sequence_number})
        persisted = await self.normalized_repository.insert(sequenced)
        if not persisted.is_new:
            if event.raw_event_id:
                await self.raw_events.mark_status(event.raw_event_id, "duplicate")
            return PipelineResult(normalized_duplicates=1)

        if event.raw_event_id:
            await self.raw_events.mark_status(event.raw_event_id, "normalized")
        for consumer in self.consumers:
            try:
                await consumer.consume(sequenced)
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
        return PipelineResult(normalized_inserted=1)
