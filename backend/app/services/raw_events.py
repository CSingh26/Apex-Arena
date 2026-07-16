# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RawEventInput(BaseModel):
    provider: str = "openf1"
    provider_endpoint: str
    raw_payload: dict[str, Any]
    provider_event_id: str | None = None
    session_key: str | None = None
    session_id: UUID | None = None
    event_time: datetime | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_replay: bool = False


class RawEventCreate(BaseModel):
    provider: str
    provider_endpoint: str
    provider_event_id: str
    deterministic_hash: str
    session_key: str | None
    session_id: UUID | None
    event_time: datetime | None
    received_at: datetime
    raw_payload: dict[str, Any]
    payload_hash: str
    processing_status: str = "pending"


class RawEventRepositoryResult(BaseModel):
    record_id: UUID
    is_new: bool


class RawEventRepository(Protocol):
    async def insert(self, event: RawEventCreate) -> RawEventRepositoryResult: ...

    async def count(self, session_key: str | None = None) -> int: ...


class RawPersistResult(BaseModel):
    record_id: UUID
    is_new: bool
    provider_event_id: str
    deterministic_hash: str
    payload_hash: str
    session_key: str | None
    event_time: datetime | None
    received_at: datetime


class RawEventCounters(BaseModel):
    inserted: int = 0
    duplicates: int = 0


class RawProviderEventService:
    """Secret-safe, deterministic, idempotent raw provider persistence boundary."""

    def __init__(self, repository: RawEventRepository) -> None:
        self.repository = repository
        self._counters = RawEventCounters()

    @property
    def counters(self) -> RawEventCounters:
        return self._counters.model_copy()

    async def persist(self, raw: RawEventInput) -> RawPersistResult:
        canonical_payload = json.dumps(
            raw.raw_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
        provider_event_id = self._provider_event_id(raw, payload_hash)
        identity = "|".join(
            (
                raw.provider,
                raw.provider_endpoint,
                raw.session_key or "",
                provider_event_id,
                payload_hash,
            )
        )
        deterministic_hash = hashlib.sha256(identity.encode()).hexdigest()
        create = RawEventCreate(
            provider=raw.provider,
            provider_endpoint=raw.provider_endpoint,
            provider_event_id=provider_event_id,
            deterministic_hash=deterministic_hash,
            session_key=raw.session_key,
            session_id=raw.session_id,
            event_time=raw.event_time,
            received_at=raw.received_at,
            raw_payload=raw.raw_payload,
            payload_hash=payload_hash,
        )
        result = await self.repository.insert(create)
        if result.is_new:
            self._counters.inserted += 1
        else:
            self._counters.duplicates += 1
        logger.info(
            "Raw provider event %s provider=%s endpoint=%s session=%s",
            "inserted" if result.is_new else "duplicate",
            raw.provider,
            raw.provider_endpoint,
            raw.session_key or "unknown",
        )
        return RawPersistResult(
            record_id=result.record_id,
            is_new=result.is_new,
            provider_event_id=provider_event_id,
            deterministic_hash=deterministic_hash,
            payload_hash=payload_hash,
            session_key=raw.session_key,
            event_time=raw.event_time,
            received_at=raw.received_at,
        )

    @staticmethod
    def _provider_event_id(raw: RawEventInput, payload_hash: str) -> str:
        explicit = raw.provider_event_id
        if explicit is None:
            explicit = raw.raw_payload.get("_id") or raw.raw_payload.get("id")
        return str(explicit) if explicit is not None else f"hash:{payload_hash}"
