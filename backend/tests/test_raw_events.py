# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from uuid import UUID, uuid4

import pytest

from app.services.raw_events import (
    RawEventCreate,
    RawEventInput,
    RawEventRepositoryResult,
    RawProviderEventService,
)


class InMemoryRawRepository:
    def __init__(self) -> None:
        self.events: dict[str, tuple[UUID, RawEventCreate]] = {}

    async def insert(self, event: RawEventCreate) -> RawEventRepositoryResult:
        existing = self.events.get(event.deterministic_hash)
        if existing:
            return RawEventRepositoryResult(record_id=existing[0], is_new=False)
        record_id = uuid4()
        self.events[event.deterministic_hash] = (record_id, event)
        return RawEventRepositoryResult(record_id=record_id, is_new=True)

    async def count(self, session_key: str | None = None) -> int:
        if session_key is None:
            return len(self.events)
        return sum(event.session_key == session_key for _, event in self.events.values())

    async def mark_status(self, record_id: UUID, status: str) -> None:
        return None


@pytest.mark.asyncio
async def test_identical_payload_is_inserted_once() -> None:
    repository = InMemoryRawRepository()
    service = RawProviderEventService(repository)
    raw = RawEventInput(
        provider_endpoint="position",
        session_key="latest",
        raw_payload={"_id": 10, "driver_number": 4, "position": 2},
    )

    first = await service.persist(raw)
    second = await service.persist(raw)

    assert first.is_new is True
    assert second.is_new is False
    assert first.record_id == second.record_id
    assert await repository.count() == 1
    assert service.counters.inserted == 1
    assert service.counters.duplicates == 1


@pytest.mark.asyncio
async def test_changed_payload_creates_new_version_with_same_provider_id() -> None:
    repository = InMemoryRawRepository()
    service = RawProviderEventService(repository)

    first = await service.persist(
        RawEventInput(
            provider_endpoint="laps",
            provider_event_id="lap-4-12",
            raw_payload={"lap_number": 12, "duration": 92.1},
        )
    )
    second = await service.persist(
        RawEventInput(
            provider_endpoint="laps",
            provider_event_id="lap-4-12",
            raw_payload={"lap_number": 12, "duration": 91.9},
        )
    )

    assert first.is_new is True
    assert second.is_new is True
    assert first.deterministic_hash != second.deterministic_hash
    assert await repository.count() == 2


@pytest.mark.asyncio
async def test_missing_provider_id_uses_payload_hash() -> None:
    service = RawProviderEventService(InMemoryRawRepository())

    result = await service.persist(
        RawEventInput(provider_endpoint="weather", raw_payload={"rainfall": 0})
    )

    assert result.provider_event_id.startswith("hash:")
    assert len(result.payload_hash) == 64


@pytest.mark.asyncio
async def test_raw_payload_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    service = RawProviderEventService(InMemoryRawRepository())
    caplog.set_level(logging.INFO)

    await service.persist(
        RawEventInput(
            provider_endpoint="sessions",
            raw_payload={"credential": "must-never-appear-in-logs"},
        )
    )

    assert "must-never-appear-in-logs" not in caplog.text
