# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.providers.openf1 import OPENF1_ENDPOINTS, OpenF1RestClient
from app.services.event_pipeline import PipelineResult, RaceEventProcessor
from app.services.raw_events import RawEventInput

logger = logging.getLogger(__name__)

DEFAULT_HISTORICAL_ENDPOINTS = (
    "sessions",
    "drivers",
    "position",
    "intervals",
    "laps",
    "pit",
    "stints",
    "race_control",
    "weather",
)


class IngestionRunSummary(BaseModel):
    id: UUID
    provider: str
    session_key: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    last_event_at: datetime | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_inserted: int = 0
    duplicates: int = 0
    normalized_inserted: int = 0


class IngestionRunRepository(Protocol):
    async def start(
        self, *, provider: str, session_key: str, metadata: dict[str, Any]
    ) -> UUID: ...

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str,
        result: PipelineResult,
        last_event_at: datetime | None,
        last_error: str | None = None,
    ) -> None: ...

    async def latest(self) -> IngestionRunSummary | None: ...


class SnapshotCounter(Protocol):
    async def count(self, session_key: str | None = None) -> int: ...


class HistoricalIngestionResult(BaseModel):
    run_id: UUID
    session_key: str
    endpoints: list[str]
    fetched_records: int
    raw_inserted: int
    duplicates: int
    normalized_inserted: int
    normalized_duplicates: int
    snapshots: int


class HistoricalOpenF1Adapter:
    """Fetch a session over REST and feed replay records through the live pipeline."""

    def __init__(
        self,
        *,
        client: OpenF1RestClient,
        processor: RaceEventProcessor,
        runs: IngestionRunRepository,
        snapshots: SnapshotCounter,
        max_records_per_endpoint: int,
    ) -> None:
        self.client = client
        self.processor = processor
        self.runs = runs
        self.snapshots = snapshots
        self.max_records_per_endpoint = max_records_per_endpoint

    async def ingest_session(
        self,
        session_key: str,
        endpoints: list[str] | None = None,
    ) -> HistoricalIngestionResult:
        selected = self._validate_endpoints(endpoints)
        run_id = await self.runs.start(
            provider="openf1",
            session_key=session_key,
            metadata={"adapter": "historical_rest", "endpoints": selected},
        )
        before_snapshots = await self.snapshots.count(session_key)
        received_at = datetime.now(UTC)
        records: list[RawEventInput] = []
        endpoint_counts: dict[str, int] = {}
        try:
            for endpoint in selected:
                fetch = getattr(self.client, endpoint)
                payloads = await fetch(session_key=session_key)
                limited = payloads[: self.max_records_per_endpoint]
                endpoint_counts[endpoint] = len(limited)
                records.extend(
                    self._raw_event(endpoint, payload, session_key, received_at)
                    for payload in limited
                )

            records.sort(key=lambda event: event.event_time or event.received_at)
            result = await self.processor.ingest_batch(records)
            last_event_at = max(
                (event.event_time for event in records if event.event_time is not None),
                default=None,
            )
            await self.runs.finish(
                run_id,
                status="completed",
                result=result,
                last_event_at=last_event_at,
            )
        except Exception as exc:
            safe_error = type(exc).__name__
            await self.runs.finish(
                run_id,
                status="failed",
                result=PipelineResult(),
                last_event_at=None,
                last_error=safe_error,
            )
            logger.error(
                "Historical OpenF1 ingestion failed session=%s error=%s",
                session_key,
                safe_error,
            )
            raise

        snapshot_count = await self.snapshots.count(session_key) - before_snapshots
        logger.info(
            "Historical OpenF1 ingestion completed session=%s fetched=%s inserted=%s",
            session_key,
            len(records),
            result.normalized_inserted,
        )
        return HistoricalIngestionResult(
            run_id=run_id,
            session_key=session_key,
            endpoints=selected,
            fetched_records=sum(endpoint_counts.values()),
            raw_inserted=result.raw_inserted,
            duplicates=result.raw_duplicates + result.normalized_duplicates,
            normalized_inserted=result.normalized_inserted,
            normalized_duplicates=result.normalized_duplicates,
            snapshots=max(0, snapshot_count),
        )

    @staticmethod
    def _validate_endpoints(endpoints: list[str] | None) -> list[str]:
        selected = list(dict.fromkeys(endpoints or DEFAULT_HISTORICAL_ENDPOINTS))
        unsupported = sorted(set(selected) - OPENF1_ENDPOINTS)
        if unsupported:
            raise ValueError(f"Unsupported historical endpoints: {', '.join(unsupported)}")
        if not selected:
            raise ValueError("At least one historical endpoint is required")
        return selected

    @classmethod
    def _raw_event(
        cls,
        endpoint: str,
        payload: dict[str, Any],
        session_key: str,
        received_at: datetime,
    ) -> RawEventInput:
        provider_event_id = payload.get("_id") or payload.get("id")
        return RawEventInput(
            provider="openf1",
            provider_endpoint=endpoint,
            provider_event_id=str(provider_event_id) if provider_event_id is not None else None,
            session_key=str(payload.get("session_key") or session_key),
            event_time=cls._payload_time(payload),
            received_at=received_at,
            raw_payload=payload,
            is_replay=True,
        )

    @staticmethod
    def _payload_time(payload: dict[str, Any]) -> datetime | None:
        for field in ("date", "date_start", "event_time"):
            value = payload.get(field)
            if not isinstance(value, str) or not value:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None
