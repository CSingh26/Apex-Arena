# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.domain.models import NormalizedRaceEvent
from app.services.event_pipeline import (
    EventDeduplicator,
    EventOrderingBuffer,
    NormalizedPersistResult,
    RaceEventProcessor,
    SequenceNumberService,
)
from app.services.normalization import OpenF1EventNormalizer
from app.services.raw_events import (
    RawEventCreate,
    RawEventInput,
    RawEventRepositoryResult,
    RawProviderEventService,
)


class RawRepository:
    def __init__(self) -> None:
        self.events: dict[str, UUID] = {}
        self.statuses: dict[UUID, str] = {}

    async def insert(self, event: RawEventCreate) -> RawEventRepositoryResult:
        if event.deterministic_hash in self.events:
            return RawEventRepositoryResult(
                record_id=self.events[event.deterministic_hash], is_new=False
            )
        record_id = uuid4()
        self.events[event.deterministic_hash] = record_id
        return RawEventRepositoryResult(record_id=record_id, is_new=True)

    async def count(self, session_key: str | None = None) -> int:
        return len(self.events)

    async def mark_status(self, record_id: UUID, status: str) -> None:
        self.statuses[record_id] = status


class NormalizedRepository:
    def __init__(self) -> None:
        self.events: dict[str, NormalizedRaceEvent] = {}

    async def insert(self, event: NormalizedRaceEvent) -> NormalizedPersistResult:
        if event.dedup_key in self.events:
            return NormalizedPersistResult(record_id=self.events[event.dedup_key].id, is_new=False)
        self.events[event.dedup_key] = event
        return NormalizedPersistResult(record_id=event.id, is_new=True)

    async def max_sequence(self, session_key: str) -> int:
        return max(
            (
                event.sequence_number
                for event in self.events.values()
                if event.session_key == session_key
            ),
            default=0,
        )

    async def latest_session_key(self) -> str | None:
        events = sorted(self.events.values(), key=lambda event: event.processed_at)
        return events[-1].session_key if events else None

    async def count(self, session_key: str | None = None) -> int:
        if session_key is None:
            return len(self.events)
        return sum(event.session_key == session_key for event in self.events.values())

    async def list_for_session(
        self, session_key: str, after_sequence: int = 0, limit: int = 100
    ) -> list[NormalizedRaceEvent]:
        return sorted(
            [
                event
                for event in self.events.values()
                if event.session_key == session_key and event.sequence_number > after_sequence
            ],
            key=lambda event: event.sequence_number,
        )[:limit]


class Consumer:
    def __init__(self) -> None:
        self.events: list[NormalizedRaceEvent] = []

    async def consume(self, event: NormalizedRaceEvent) -> None:
        self.events.append(event)


def processor() -> tuple[RaceEventProcessor, NormalizedRepository, Consumer]:
    normalized = NormalizedRepository()
    consumer = Consumer()
    pipeline = RaceEventProcessor(
        raw_events=RawProviderEventService(RawRepository()),
        normalizer=OpenF1EventNormalizer(),
        normalized_repository=normalized,
        deduplicator=EventDeduplicator(),
        ordering_buffer=EventOrderingBuffer(window_ms=1500),
        sequence_numbers=SequenceNumberService(normalized),
        consumers=[consumer],
    )
    return pipeline, normalized, consumer


@pytest.mark.asyncio
async def test_duplicate_raw_event_does_not_create_normalized_duplicate() -> None:
    pipeline, normalized, _ = processor()
    raw = RawEventInput(
        provider_endpoint="position",
        session_key="spa-race",
        raw_payload={"_id": 1, "driver_number": 4, "position": 1},
    )

    await pipeline.ingest(raw)
    duplicate = await pipeline.ingest(raw)
    await pipeline.flush_session("spa-race")

    assert duplicate.raw_duplicates == 1
    assert await normalized.count() == 1


@pytest.mark.asyncio
async def test_normalized_dedup_ignores_different_provider_ids() -> None:
    pipeline, normalized, _ = processor()
    base = {
        "provider_endpoint": "weather",
        "session_key": "spa-race",
        "event_time": datetime(2026, 7, 19, 13, tzinfo=UTC),
        "raw_payload": {"rainfall": 0},
    }

    await pipeline.ingest(RawEventInput(**base, provider_event_id="one"))
    await pipeline.ingest(RawEventInput(**base, provider_event_id="two"))
    result = await pipeline.flush_session("spa-race")

    assert result.normalized_duplicates == 1
    assert await normalized.count() == 1


@pytest.mark.asyncio
async def test_out_of_order_events_receive_monotonic_event_time_sequences() -> None:
    pipeline, normalized, consumer = processor()
    start = datetime(2026, 7, 19, 13, tzinfo=UTC)
    late = RawEventInput(
        provider_endpoint="laps",
        provider_event_id="late",
        session_key="spa-race",
        event_time=start + timedelta(seconds=2),
        raw_payload={"driver_number": 4, "lap_number": 2},
    )
    early = RawEventInput(
        provider_endpoint="laps",
        provider_event_id="early",
        session_key="spa-race",
        event_time=start,
        raw_payload={"driver_number": 4, "lap_number": 1},
    )

    await pipeline.ingest(late)
    await pipeline.ingest(early)
    await pipeline.flush_session("spa-race")

    events = await normalized.list_for_session("spa-race")
    assert [event.lap_number for event in events] == [1, 2]
    assert [event.sequence_number for event in events] == [1, 2]
    assert [event.sequence_number for event in consumer.events] == [1, 2]
