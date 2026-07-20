# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.settings import Settings
from app.domain.rooms import RaceRoom, SessionType
from app.providers.openf1 import OpenF1RestClient
from app.services.openf1_backfill import (
    BackfillStatus,
    OpenF1HistoricalBackfillService,
)
from app.services.rooms import RaceRoomService
from app.storage.database import Database
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)


class ProviderPublicationState(StrEnum):
    AWAITING_SESSION_METADATA = "awaiting_session_metadata"
    METADATA_AVAILABLE = "metadata_available"
    AWAITING_HISTORICAL_DATA = "awaiting_historical_data"
    PARTIALLY_AVAILABLE = "partially_available"
    BACKFILL_READY = "backfill_ready"
    BACKFILL_RUNNING = "backfill_running"
    REPLAY_READY = "replay_ready"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BACKFILL_FAILED_RETRYABLE = "backfill_failed_retryable"
    BACKFILL_FAILED_TERMINAL = "backfill_failed_terminal"


class EndpointDiagnostic(BaseModel):
    endpoint: str
    http_status: int | None = None
    row_count: int = 0
    latest_provider_timestamp: str | None = None
    retryable: bool = True
    required_for_replay: bool = False
    normalizer_supported: bool = True
    backfill_state: str = "not_started"


class ReconciliationPassSummary(BaseModel):
    started_at: datetime
    completed_at: datetime | None = None
    sessions_examined: int = 0
    sessions_matched: int = 0
    sessions_awaiting_provider: int = 0
    sessions_queued_for_backfill: int = 0
    sessions_finalized: int = 0
    current_room_slug: str | None = None
    current_session_key: str | None = None
    current_endpoint: str | None = None
    latest_provider_timestamp: str | None = None
    last_retry_time: datetime | None = None
    next_retry_time: datetime | None = None
    last_safe_error_category: str | None = None
    advisory_lock_owned: bool = False
    endpoint_diagnostics: list[EndpointDiagnostic] = Field(default_factory=list)


class RecentSessionReconciliationService:
    """Upgrade recently completed provider-pending rooms after OpenF1 publishes data."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        rooms: RaceRoomService,
        room_repository: SqlRaceRoomRepository,
        client: OpenF1RestClient,
        backfill: OpenF1HistoricalBackfillService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.rooms = rooms
        self.room_repository = room_repository
        self.client = client
        self.backfill = backfill
        self._last_summary: ReconciliationPassSummary | None = None
        self._running = False

    @property
    def status(self) -> dict[str, Any]:
        summary = self._last_summary
        return {
            "enabled": self.settings.recent_session_reconciliation_enabled,
            "task_state": "running" if self._running else "idle",
            "last_reconciliation_start": (
                summary.started_at.isoformat() if summary is not None else None
            ),
            "last_reconciliation_completion": (
                summary.completed_at.isoformat()
                if summary is not None and summary.completed_at is not None
                else None
            ),
            "sessions_examined": summary.sessions_examined if summary is not None else 0,
            "sessions_matched": summary.sessions_matched if summary is not None else 0,
            "sessions_awaiting_provider": (
                summary.sessions_awaiting_provider if summary is not None else 0
            ),
            "sessions_queued_for_backfill": (
                summary.sessions_queued_for_backfill if summary is not None else 0
            ),
            "sessions_finalized": summary.sessions_finalized if summary is not None else 0,
            "current_room_slug": summary.current_room_slug if summary is not None else None,
            "current_session_key": summary.current_session_key if summary is not None else None,
            "current_endpoint": summary.current_endpoint if summary is not None else None,
            "latest_provider_timestamp": (
                summary.latest_provider_timestamp if summary is not None else None
            ),
            "last_retry_time": (
                summary.last_retry_time.isoformat()
                if summary is not None and summary.last_retry_time is not None
                else None
            ),
            "next_retry_time": (
                summary.next_retry_time.isoformat()
                if summary is not None and summary.next_retry_time is not None
                else None
            ),
            "last_safe_error_category": (
                summary.last_safe_error_category if summary is not None else None
            ),
            "advisory_lock_owned": summary.advisory_lock_owned if summary is not None else False,
        }

    async def run_once(self, *, now: datetime | None = None) -> ReconciliationPassSummary:
        observed_at = self._aware(now or datetime.now(UTC))
        summary = ReconciliationPassSummary(
            started_at=observed_at,
            last_retry_time=observed_at,
            next_retry_time=self._next_retry_time(observed_at),
        )
        self._last_summary = summary
        if not self.settings.recent_session_reconciliation_enabled:
            summary.completed_at = datetime.now(UTC)
            return summary
        if self.settings.app_process_role not in {"ingestor", "combined", "all"}:
            summary.last_safe_error_category = "role_not_allowed"
            summary.completed_at = datetime.now(UTC)
            return summary

        self._running = True
        try:
            async with self.database.reconciliation_lease() as acquired:
                summary.advisory_lock_owned = acquired
                if not acquired:
                    summary.last_safe_error_category = "reconciliation_locked"
                    return summary
                try:
                    await self.rooms.force_sync()
                except Exception as exc:
                    logger.warning(
                        "Recent session catalog refresh failed error=%s",
                        type(exc).__name__,
                    )

                candidates = await self.room_repository.list_recent_reconciliation_candidates(
                    now=observed_at,
                    lookback_days=self.settings.recent_session_reconciliation_lookback_days,
                    grace_minutes=self.settings.recent_session_provider_grace_minutes,
                    limit=self.settings.recent_session_auto_backfill_max_sessions,
                )
                for room in candidates:
                    await self._reconcile_room(room, summary)
        finally:
            self._running = False
            summary.completed_at = datetime.now(UTC)
        return summary

    async def _reconcile_room(
        self, room: RaceRoom, summary: ReconciliationPassSummary
    ) -> ProviderPublicationState:
        summary.sessions_examined += 1
        summary.current_room_slug = room.slug
        try:
            resolution = await self.backfill.resolve(season=room.season, room_slug=room.slug)
        except Exception as exc:
            summary.sessions_awaiting_provider += 1
            summary.last_safe_error_category = type(exc).__name__
            return ProviderPublicationState.AWAITING_SESSION_METADATA

        summary.sessions_matched += 1
        summary.current_session_key = resolution.session_key
        diagnostics = await self.inspect_endpoints(
            session_key=resolution.session_key,
            session_type=room.session_type,
        )
        summary.endpoint_diagnostics.extend(diagnostics)
        latest = max(
            (
                item.latest_provider_timestamp
                for item in diagnostics
                if item.latest_provider_timestamp is not None
            ),
            default=None,
        )
        summary.latest_provider_timestamp = latest or summary.latest_provider_timestamp
        if not self._has_replay_core(diagnostics):
            summary.sessions_awaiting_provider += 1
            await self.room_repository.bind_provider_session(
                room.slug,
                meeting_key=resolution.meeting_key,
                session_key=resolution.session_key,
            )
            self.rooms.invalidate_catalog()
            return ProviderPublicationState.AWAITING_HISTORICAL_DATA

        await self.room_repository.bind_provider_session(
            room.slug,
            meeting_key=resolution.meeting_key,
            session_key=resolution.session_key,
        )
        if not self.settings.recent_session_auto_backfill_enabled:
            self.rooms.invalidate_catalog()
            return ProviderPublicationState.BACKFILL_READY

        summary.sessions_queued_for_backfill += 1
        try:
            result = await self.backfill.run(
                season=room.season,
                room_slug=room.slug,
                endpoints=self._endpoints_for(room.session_type),
                include_high_frequency=False,
                dry_run=False,
                resume=True,
                force_retry_failed=True,
            )
        except Exception as exc:
            summary.last_safe_error_category = type(exc).__name__
            return ProviderPublicationState.BACKFILL_FAILED_RETRYABLE
        self.rooms.invalidate_catalog()
        if result.status in {BackfillStatus.COMPLETED, BackfillStatus.PARTIAL}:
            if result.replay_available:
                summary.sessions_finalized += 1
                return ProviderPublicationState.REPLAY_READY
            return ProviderPublicationState.PARTIALLY_AVAILABLE
        return ProviderPublicationState.BACKFILL_RUNNING

    async def inspect_endpoints(
        self, *, session_key: str, session_type: SessionType
    ) -> list[EndpointDiagnostic]:
        diagnostics: list[EndpointDiagnostic] = []
        required = self._required_for_replay(session_type)
        for endpoint in self._endpoints_for(session_type):
            try:
                payloads = await getattr(self.client, endpoint)(session_key=session_key)
            except Exception as exc:
                diagnostics.append(
                    EndpointDiagnostic(
                        endpoint=endpoint,
                        http_status=getattr(getattr(exc, "response", None), "status_code", None),
                        retryable=True,
                        required_for_replay=endpoint in required,
                        backfill_state="provider_error",
                    )
                )
                continue
            diagnostics.append(
                EndpointDiagnostic(
                    endpoint=endpoint,
                    http_status=200,
                    row_count=len(payloads),
                    latest_provider_timestamp=self._latest_timestamp(payloads),
                    retryable=len(payloads) == 0,
                    required_for_replay=endpoint in required,
                    backfill_state="available" if payloads else "empty",
                )
            )
        return diagnostics

    @staticmethod
    def _endpoints_for(session_type: SessionType) -> list[str]:
        if session_type in {SessionType.QUALIFYING, SessionType.SPRINT_QUALIFYING}:
            return [
                "drivers",
                "laps",
                "position",
                "race_control",
                "weather",
                "session_result",
                "starting_grid",
            ]
        return [
            "drivers",
            "laps",
            "position",
            "intervals",
            "pit",
            "stints",
            "race_control",
            "weather",
            "session_result",
            "starting_grid",
        ]

    @staticmethod
    def _required_for_replay(session_type: SessionType) -> set[str]:
        if session_type in {SessionType.QUALIFYING, SessionType.SPRINT_QUALIFYING}:
            return {"drivers", "laps", "position"}
        return {"drivers", "laps"}

    @classmethod
    def _has_replay_core(cls, diagnostics: list[EndpointDiagnostic]) -> bool:
        rows = {item.endpoint: item.row_count for item in diagnostics}
        has_drivers = rows.get("drivers", 0) > 0
        has_timing = any(
            rows.get(endpoint, 0) > 0 for endpoint in ("laps", "position", "intervals")
        )
        return has_drivers and has_timing

    def _next_retry_time(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.settings.recent_session_reconciliation_interval_seconds)

    @staticmethod
    def _latest_timestamp(rows: list[dict[str, Any]]) -> str | None:
        values: list[str] = []
        for row in rows:
            for key in ("date", "date_start", "date_end"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    values.append(value)
        return max(values) if values else None

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
