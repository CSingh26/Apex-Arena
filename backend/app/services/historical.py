# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.rooms import IngestionStatus, SourceAvailability
from app.providers.openf1 import OPENF1_ENDPOINTS, OpenF1RestClient
from app.services.driver_identity import DriverIdentity, DriverIdentityResolver
from app.services.event_pipeline import PipelineResult, RaceEventProcessor
from app.services.raw_events import RawEventInput
from app.services.session_semantics import (
    normalize_qualifying_phase,
    normalize_session_type,
    phase_result_rows,
)

logger = logging.getLogger(__name__)

DEFAULT_HISTORICAL_ENDPOINTS = (
    "sessions",
    "drivers",
    "laps",
    "position",
    "intervals",
    "stints",
    "pit",
    "race_control",
    "weather",
    "session_result",
    "starting_grid",
)

HISTORICAL_INGESTION_STAGES: dict[str, tuple[str, ...]] = {
    "metadata": ("sessions", "drivers"),
    "timing": ("laps", "position", "intervals"),
    "strategy": ("stints", "pit"),
    "context": ("race_control", "weather"),
    "classification": ("session_result", "starting_grid"),
    # High-frequency endpoints are opt-in and still use a session_key plus the
    # configured per-endpoint cap.  They are never part of the default backfill.
    "deep_telemetry": ("car_data", "location"),
}


class HistoricalDataAvailability(StrEnum):
    REPLAY_READY = "replay_ready"
    PARTIAL = "partial"
    RESULTS_ONLY = "results_only"
    UNAVAILABLE = "unavailable"


class HistoricalIngestionError(RuntimeError):
    """Safe aggregate failure that never embeds provider response content."""


class HistoricalStageResult(BaseModel):
    name: str
    status: IngestionStatus
    endpoints: list[str]
    fetched_records: int = 0
    failed_endpoints: list[str] = Field(default_factory=list)


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
    async def start(self, *, provider: str, session_key: str, metadata: dict[str, Any]) -> UUID: ...

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


class RoomAvailabilityUpdater(Protocol):
    async def update_ingestion_availability(
        self,
        *,
        session_key: str,
        ingestion_status: IngestionStatus,
        source_availability: SourceAvailability,
        replay_available: bool,
        results_available: bool,
        telemetry_quality: str,
    ) -> bool: ...


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
    status: IngestionStatus = IngestionStatus.READY
    data_availability: HistoricalDataAvailability = HistoricalDataAvailability.PARTIAL
    endpoint_counts: dict[str, int] = Field(default_factory=dict)
    failed_endpoints: list[str] = Field(default_factory=list)
    stages: list[HistoricalStageResult] = Field(default_factory=list)


class HistoricalBatchResult(BaseModel):
    completed: list[HistoricalIngestionResult] = Field(default_factory=list)
    failed_session_keys: list[str] = Field(default_factory=list)


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
        room_availability: RoomAvailabilityUpdater | None = None,
    ) -> None:
        self.client = client
        self.processor = processor
        self.runs = runs
        self.snapshots = snapshots
        self.max_records_per_endpoint = max_records_per_endpoint
        self.room_availability = room_availability
        self.identity_resolver = DriverIdentityResolver()

    async def ingest_session(
        self,
        session_key: str,
        endpoints: list[str] | None = None,
        *,
        availability_baseline: dict[str, int] | None = None,
    ) -> HistoricalIngestionResult:
        selected = self._validate_endpoints(endpoints)
        selected_stages = self._selected_stages(selected)
        run_id = await self.runs.start(
            provider="openf1",
            session_key=session_key,
            metadata={
                "adapter": "historical_rest",
                "endpoints": selected,
                "stages": [name for name, _ in selected_stages],
            },
        )
        before_snapshots = await self.snapshots.count(session_key)
        received_at = datetime.now(UTC)
        endpoint_counts: dict[str, int] = dict(availability_baseline or {})
        run_endpoint_counts: dict[str, int] = {}
        failed_endpoints: list[str] = []
        stages: list[HistoricalStageResult] = []
        result = PipelineResult()
        all_records: list[RawEventInput] = []
        driver_registry: dict[int, DriverIdentity] = {}
        normalized_session_type: str | None = None
        try:
            for stage_name, stage_endpoints in selected_stages:
                stage_records: list[RawEventInput] = []
                stage_failures: list[str] = []
                for endpoint in stage_endpoints:
                    fetch = getattr(self.client, endpoint)
                    try:
                        payloads = await fetch(session_key=session_key)
                    except Exception as exc:
                        stage_failures.append(endpoint)
                        failed_endpoints.append(endpoint)
                        logger.warning(
                            "Historical endpoint unavailable session=%s endpoint=%s error=%s",
                            session_key,
                            endpoint,
                            type(exc).__name__,
                        )
                        continue
                    limited = payloads[: self.max_records_per_endpoint]
                    endpoint_counts[endpoint] = len(limited)
                    run_endpoint_counts[endpoint] = len(limited)
                    if endpoint == "drivers":
                        driver_registry.update(self.identity_resolver.build_registry(limited))
                    if endpoint == "sessions":
                        normalized_session_type = self._session_type(limited, session_key)
                    stage_records.extend(
                        self._raw_event(
                            endpoint,
                            self._decorate_payload(
                                endpoint,
                                payload,
                                normalized_session_type,
                                driver_registry,
                            ),
                            session_key,
                            received_at,
                        )
                        for payload in limited
                    )
                stage_records.sort(key=lambda event: event.event_time or event.received_at)
                all_records.extend(stage_records)
                stage_status = (
                    IngestionStatus.FAILED
                    if stage_failures and not stage_records
                    else IngestionStatus.PARTIAL
                    if stage_failures
                    else IngestionStatus.READY
                )
                stages.append(
                    HistoricalStageResult(
                        name=stage_name,
                        status=stage_status,
                        endpoints=list(stage_endpoints),
                        fetched_records=len(stage_records),
                        failed_endpoints=stage_failures,
                    )
                )

            all_records.sort(key=lambda event: event.event_time or event.received_at)
            if all_records:
                result.add(await self.processor.ingest_batch(all_records))
            fetched_records = sum(run_endpoint_counts.values())
            if failed_endpoints and fetched_records == 0 and not availability_baseline:
                raise HistoricalIngestionError("All selected historical datasets failed")
            ingestion_status = (
                IngestionStatus.PARTIAL
                if failed_endpoints
                else IngestionStatus.READY
                if fetched_records
                else IngestionStatus.UNAVAILABLE
            )
            data_availability = self._availability(endpoint_counts)
            if self.room_availability is not None:
                await self.room_availability.update_ingestion_availability(
                    session_key=session_key,
                    ingestion_status=ingestion_status,
                    source_availability=self._source_availability(
                        data_availability, endpoint_counts
                    ),
                    replay_available=(data_availability == HistoricalDataAvailability.REPLAY_READY),
                    results_available=endpoint_counts.get("session_result", 0) > 0,
                    telemetry_quality=data_availability.value,
                )
            last_event_at = max(
                (event.event_time for event in all_records if event.event_time is not None),
                default=None,
            )
            await self.runs.finish(
                run_id,
                status="partial" if failed_endpoints else "completed",
                result=result,
                last_event_at=last_event_at,
            )
        except Exception as exc:
            safe_error = type(exc).__name__
            if self.room_availability is not None:
                try:
                    await self.room_availability.update_ingestion_availability(
                        session_key=session_key,
                        ingestion_status=IngestionStatus.FAILED,
                        source_availability=SourceAvailability.UNAVAILABLE,
                        replay_available=False,
                        results_available=False,
                        telemetry_quality="ingestion_failed",
                    )
                except Exception as availability_exc:
                    logger.error(
                        "Historical room availability update failed session=%s error=%s",
                        session_key,
                        type(availability_exc).__name__,
                    )
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
            sum(run_endpoint_counts.values()),
            result.normalized_inserted,
        )
        return HistoricalIngestionResult(
            run_id=run_id,
            session_key=session_key,
            endpoints=selected,
            fetched_records=sum(run_endpoint_counts.values()),
            raw_inserted=result.raw_inserted,
            duplicates=result.raw_duplicates + result.normalized_duplicates,
            normalized_inserted=result.normalized_inserted,
            normalized_duplicates=result.normalized_duplicates,
            snapshots=max(0, snapshot_count),
            status=ingestion_status,
            data_availability=data_availability,
            endpoint_counts=endpoint_counts,
            failed_endpoints=failed_endpoints,
            stages=stages,
        )

    async def retry_failed_session(
        self,
        previous: HistoricalIngestionResult,
    ) -> HistoricalIngestionResult:
        if not previous.failed_endpoints:
            return previous
        return await self.ingest_session(
            previous.session_key,
            previous.failed_endpoints,
            availability_baseline=previous.endpoint_counts,
        )

    async def ingest_sessions(
        self,
        session_keys: list[str],
        endpoints: list[str] | None = None,
    ) -> HistoricalBatchResult:
        """Backfill sessions independently so one provider gap cannot abort a season."""

        batch = HistoricalBatchResult()
        for session_key in dict.fromkeys(session_keys):
            try:
                batch.completed.append(await self.ingest_session(session_key, endpoints))
            except Exception as exc:
                batch.failed_session_keys.append(session_key)
                logger.error(
                    "Historical session backfill failed session=%s error=%s",
                    session_key,
                    type(exc).__name__,
                )
        return batch

    @staticmethod
    def _validate_endpoints(endpoints: list[str] | None) -> list[str]:
        selected = list(dict.fromkeys(endpoints or DEFAULT_HISTORICAL_ENDPOINTS))
        unsupported = sorted(set(selected) - OPENF1_ENDPOINTS)
        if unsupported:
            raise ValueError(f"Unsupported historical endpoints: {', '.join(unsupported)}")
        if not selected:
            raise ValueError("At least one historical endpoint is required")
        return selected

    @staticmethod
    def _selected_stages(selected: list[str]) -> list[tuple[str, tuple[str, ...]]]:
        remaining = set(selected)
        result: list[tuple[str, tuple[str, ...]]] = []
        for stage_name, endpoints in HISTORICAL_INGESTION_STAGES.items():
            included = tuple(endpoint for endpoint in endpoints if endpoint in remaining)
            if included:
                result.append((stage_name, included))
                remaining.difference_update(included)
        if remaining:
            result.append(
                (
                    "additional",
                    tuple(endpoint for endpoint in selected if endpoint in remaining),
                )
            )
        return result

    @staticmethod
    def _session_type(payloads: list[dict[str, Any]], session_key: str) -> str | None:
        for payload in payloads:
            if str(payload.get("session_key") or session_key) != session_key:
                continue
            normalized = normalize_session_type(
                payload.get("session_name") or payload.get("session_type")
            )
            if normalized is not None:
                return normalized.value
        return None

    def _decorate_payload(
        self,
        endpoint: str,
        payload: dict[str, Any],
        session_type: str | None,
        driver_registry: dict[int, DriverIdentity],
    ) -> dict[str, Any]:
        decorated = self.identity_resolver.enrich(payload, driver_registry)
        resolved_type = normalize_session_type(
            decorated.get("session_name") or decorated.get("session_type") or session_type
        )
        if resolved_type is not None:
            decorated["normalized_session_type"] = resolved_type.value
        phase = normalize_qualifying_phase(
            decorated.get("qualifying_phase") or decorated.get("session_phase"),
            resolved_type,
        )
        if phase is not None:
            decorated["session_phase"] = phase
        if endpoint == "session_result" and resolved_type is not None:
            rows = phase_result_rows(decorated, resolved_type)
            if rows:
                decorated["phase_results"] = rows
        return decorated

    @staticmethod
    def _availability(endpoint_counts: dict[str, int]) -> HistoricalDataAvailability:
        available = {endpoint for endpoint, count in endpoint_counts.items() if count > 0}
        replay_context = available & {
            "position",
            "intervals",
            "stints",
            "pit",
            "race_control",
        }
        if {"sessions", "drivers", "laps"} <= available and replay_context:
            return HistoricalDataAvailability.REPLAY_READY
        timing = available & {"laps", "position", "intervals", "race_control"}
        if "session_result" in available and not timing:
            return HistoricalDataAvailability.RESULTS_ONLY
        if available:
            return HistoricalDataAvailability.PARTIAL
        return HistoricalDataAvailability.UNAVAILABLE

    @staticmethod
    def _source_availability(
        availability: HistoricalDataAvailability,
        endpoint_counts: dict[str, int],
    ) -> SourceAvailability:
        if availability == HistoricalDataAvailability.PARTIAL:
            if any(endpoint_counts.get(name, 0) for name in ("laps", "position", "intervals")):
                return SourceAvailability.TIMING_ONLY
            if endpoint_counts.get("session_result", 0):
                return SourceAvailability.RESULTS_ONLY
            return SourceAvailability.UNAVAILABLE
        return {
            HistoricalDataAvailability.REPLAY_READY: SourceAvailability.LIMITED,
            HistoricalDataAvailability.RESULTS_ONLY: SourceAvailability.RESULTS_ONLY,
            HistoricalDataAvailability.UNAVAILABLE: SourceAvailability.UNAVAILABLE,
        }[availability]

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
