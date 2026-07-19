# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.core.settings import Settings
from app.domain.rooms import (
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionType,
    SourceAvailability,
)
from app.providers.openf1 import OPENF1_HIGH_FREQUENCY_ENDPOINTS, OpenF1RestClient
from app.services.historical import HistoricalOpenF1Adapter
from app.storage.backfill_repository import SqlOpenF1BackfillJobRepository
from app.storage.database import Database
from app.storage.models import (
    NormalizedRaceEventRecord,
    OpenF1BackfillJobRecord,
    RaceRoomRecord,
    RawProviderEventRecord,
)
from app.storage.room_repository import SqlRaceRoomRepository

CORE_BACKFILL_ENDPOINTS = (
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
)


class BackfillStatus(StrEnum):
    DRY_RUN = "dry_run"
    COMPLETED = "completed"
    PARTIAL = "partial"
    LOCKED = "locked"


class SessionResolution(BaseModel):
    session_key: str
    meeting_key: str | None = None
    room_slug: str | None = None
    match_method: str
    confidence: str
    candidate_count: int
    date_delta_hours: float | None = None
    normalized_provider_session_name: str
    provider_session: dict[str, Any] = Field(exclude=True)


class RoomFinalizationResult(BaseModel):
    room_slug: str
    normalized_event_count: int
    endpoint_counts: dict[str, int]
    source_availability: SourceAvailability
    replay_available: bool
    results_available: bool


class BackfillSummary(BaseModel):
    status: BackfillStatus
    season: int
    session_key: str
    meeting_key: str | None = None
    room_slug: str | None = None
    match_method: str
    confidence: str
    candidate_count: int
    endpoints: list[str]
    skipped_completed_endpoints: list[str] = Field(default_factory=list)
    rows_fetched: int = 0
    rows_processed: int = 0
    rows_inserted: int = 0
    rows_deduplicated: int = 0
    normalized_event_count: int = 0
    source_availability: SourceAvailability = SourceAvailability.UNAVAILABLE
    replay_available: bool = False
    results_available: bool = False


class OpenF1RoomFinalizer:
    """Derive public room availability exclusively from persisted provider data."""

    _rank = {
        SourceAvailability.UNAVAILABLE: 0,
        SourceAvailability.RESULTS_ONLY: 1,
        SourceAvailability.TIMING_ONLY: 2,
        SourceAvailability.LIMITED: 3,
        SourceAvailability.TELEMETRY: 4,
    }

    def __init__(self, database: Database) -> None:
        self.database = database

    async def finalize(self, session_key: str) -> RoomFinalizationResult:
        async with self.database.session_factory() as session:
            room = (
                await session.execute(
                    select(RaceRoomRecord)
                    .where(RaceRoomRecord.session_key == session_key)
                    .with_for_update()
                )
            ).scalar_one()
            counts = {
                endpoint: int(count)
                for endpoint, count in (
                    await session.execute(
                        select(
                            RawProviderEventRecord.provider_endpoint,
                            func.count(RawProviderEventRecord.id),
                        )
                        .where(RawProviderEventRecord.session_key == session_key)
                        .group_by(RawProviderEventRecord.provider_endpoint)
                    )
                ).all()
            }
            normalized_count = int(
                (
                    await session.execute(
                        select(func.count(NormalizedRaceEventRecord.id)).where(
                            NormalizedRaceEventRecord.session_key == session_key
                        )
                    )
                ).scalar_one()
            )
            last_event_at = (
                await session.execute(
                    select(func.max(NormalizedRaceEventRecord.event_time)).where(
                        NormalizedRaceEventRecord.session_key == session_key
                    )
                )
            ).scalar_one_or_none()

            has_results = counts.get("session_result", 0) > 0
            current = SourceAvailability(room.source_availability)
            availability = self.classify(counts, normalized_count, current=current)
            replay_available = availability in {
                SourceAvailability.LIMITED,
                SourceAvailability.TELEMETRY,
            }
            values: dict[str, Any] = {
                "ingestion_status": (
                    IngestionStatus.READY.value
                    if replay_available
                    else IngestionStatus.PARTIAL.value
                    if availability is not SourceAvailability.UNAVAILABLE
                    else IngestionStatus.UNAVAILABLE.value
                ),
                "source_availability": availability.value,
                "replay_available": replay_available,
                "results_available": bool(room.results_available or has_results),
                "telemetry_quality": availability.value,
                "last_event_at": last_event_at or room.last_event_at,
                "is_development": False,
                "updated_at": datetime.now(UTC),
            }
            if replay_available:
                values.update(
                    status=RoomStatus.COMPLETED.value,
                    mode=RoomMode.ARCHIVED.value,
                    eligibility_status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL.value,
                )
            elif availability is SourceAvailability.UNAVAILABLE:
                values.update(
                    status=RoomStatus.PENDING.value,
                    replay_available=False,
                    eligibility_status=RoomEligibilityStatus.PROVIDER_PENDING.value,
                )
            await session.execute(
                update(RaceRoomRecord).where(RaceRoomRecord.id == room.id).values(**values)
            )
            await session.commit()
            return RoomFinalizationResult(
                room_slug=room.slug,
                normalized_event_count=normalized_count,
                endpoint_counts=counts,
                source_availability=availability,
                replay_available=replay_available,
                results_available=bool(room.results_available or has_results),
            )

    @classmethod
    def classify(
        cls,
        counts: dict[str, int],
        normalized_count: int,
        *,
        current: SourceAvailability = SourceAvailability.UNAVAILABLE,
    ) -> SourceAvailability:
        has_metadata = counts.get("sessions", 0) > 0
        has_drivers = counts.get("drivers", 0) > 0
        has_timing = any(counts.get(name, 0) > 0 for name in ("laps", "position", "intervals"))
        has_results = counts.get("session_result", 0) > 0
        has_high_frequency = (
            sum(counts.get(name, 0) for name in OPENF1_HIGH_FREQUENCY_ENDPOINTS) >= 100
        )
        replay_ready = has_metadata and has_drivers and has_timing and normalized_count > 0
        if replay_ready and has_high_frequency:
            availability = SourceAvailability.TELEMETRY
        elif replay_ready:
            availability = SourceAvailability.LIMITED
        elif has_timing and normalized_count > 0:
            availability = SourceAvailability.TIMING_ONLY
        elif has_results:
            availability = SourceAvailability.RESULTS_ONLY
        else:
            availability = SourceAvailability.UNAVAILABLE
        return current if cls._rank[current] > cls._rank[availability] else availability


class OpenF1HistoricalBackfillService:
    def __init__(
        self,
        *,
        settings: Settings,
        client: OpenF1RestClient,
        adapter: HistoricalOpenF1Adapter,
        jobs: SqlOpenF1BackfillJobRepository,
        rooms: SqlRaceRoomRepository,
        database: Database,
        finalizer: OpenF1RoomFinalizer,
        cli_safe: bool = False,
    ) -> None:
        self.settings = settings
        self.client = client
        self.adapter = adapter
        self.jobs = jobs
        self.rooms = rooms
        self.database = database
        self.finalizer = finalizer
        self.cli_safe = cli_safe

    async def resolve(
        self,
        *,
        season: int,
        room_slug: str | None = None,
        session_key: str | None = None,
        meeting_key: str | None = None,
    ) -> SessionResolution:
        room = await self.rooms.get_room(room_slug) if room_slug else None
        if room is None and session_key:
            room = await self.rooms.get_room_by_session(session_key)
        resolved_key = session_key or (room.session_key if room else None)
        if resolved_key:
            rows = await self.client.sessions(session_key=resolved_key)
            if len(rows) != 1:
                raise ValueError("Existing session_key did not resolve uniquely")
            return self._resolution(rows[0], room, "existing_session_key", 1)

        sessions = await self.client.sessions(year=season)
        expected_type = room.session_type if room else None
        candidates = [
            row
            for row in sessions
            if expected_type is None or self._session_type(row) is expected_type
        ]
        expected_meeting = meeting_key or (room.meeting_key if room else None)
        if expected_meeting:
            exact = [
                row for row in candidates if str(row.get("meeting_key")) == str(expected_meeting)
            ]
            if len(exact) == 1:
                return self._resolution(exact[0], room, "meeting_key_and_session_type", 1)
            if len(exact) > 1:
                candidates = exact

        if room is None:
            raise ValueError("A room slug is required when session_key is not supplied")
        ranked: list[tuple[float, float, dict[str, Any]]] = []
        for row in candidates:
            start = self._date(row.get("date_start"))
            if start is None:
                continue
            delta = abs((start - self._aware(room.scheduled_start)).total_seconds()) / 3600
            if delta > 72:
                continue
            provider_text = self._text(
                " ".join(
                    str(row.get(key) or "")
                    for key in ("meeting_name", "circuit_short_name", "country_name")
                )
            )
            room_text = self._text(f"{room.race_name} {room.circuit_name} {room.country}")
            overlap = len(set(provider_text.split()) & set(room_text.split()))
            ranked.append((overlap - delta / 100, delta, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            raise ValueError("No confident OpenF1 session match was found")
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.05:
            raise ValueError("OpenF1 session match is ambiguous")
        return self._resolution(ranked[0][2], room, "metadata_date_type", len(ranked), ranked[0][1])

    async def run(
        self,
        *,
        season: int,
        room_slug: str | None = None,
        session_key: str | None = None,
        meeting_key: str | None = None,
        endpoints: list[str] | None = None,
        include_high_frequency: bool = False,
        dry_run: bool = False,
        resume: bool = False,
        force_retry_failed: bool = False,
    ) -> BackfillSummary:
        if self.settings.app_process_role != "ingestor" and not self.cli_safe:
            raise RuntimeError("Historical backfill requires the ingestor role or explicit CLI")
        selected = list(dict.fromkeys(endpoints or CORE_BACKFILL_ENDPOINTS))
        if include_high_frequency:
            selected.extend(name for name in ("car_data", "location") if name not in selected)
        elif set(selected) & OPENF1_HIGH_FREQUENCY_ENDPOINTS:
            raise ValueError("High-frequency endpoints require --include-high-frequency")
        resolution = await self.resolve(
            season=season,
            room_slug=room_slug,
            session_key=session_key,
            meeting_key=meeting_key,
        )
        self._require_completed(resolution.provider_session)
        if dry_run:
            return self._summary(BackfillStatus.DRY_RUN, season, resolution, selected)
        if resolution.room_slug is None:
            raise ValueError(
                "No Apex Arena room is bound to this session_key; use --room-slug to match one"
            )

        async with self.database.backfill_lease(season, resolution.session_key) as acquired:
            if not acquired:
                return self._summary(BackfillStatus.LOCKED, season, resolution, selected)
            if resolution.room_slug:
                await self.rooms.bind_provider_session(
                    resolution.room_slug,
                    meeting_key=resolution.meeting_key,
                    session_key=resolution.session_key,
                )
            job = await self.jobs.prepare(
                season=season,
                meeting_key=resolution.meeting_key,
                session_key=resolution.session_key,
                room_slug=resolution.room_slug,
                endpoints=["sessions", *selected],
                metadata={
                    "match_method": resolution.match_method,
                    "confidence": resolution.confidence,
                    "candidate_count": resolution.candidate_count,
                },
                force_retry_failed=force_retry_failed,
            )
            completed = set(job.completed_endpoints)
            skipped: list[str] = []
            for endpoint in ["sessions", *selected]:
                if endpoint in completed:
                    skipped.append(endpoint)
                    continue
                try:
                    await self.jobs.start_endpoint(season, resolution.session_key, endpoint)
                    result = await self.adapter.ingest_session(
                        resolution.session_key, [endpoint], update_room=False
                    )
                    await self.jobs.complete_endpoint(
                        season,
                        resolution.session_key,
                        endpoint,
                        fetched=result.fetched_records,
                        processed=result.raw_inserted + result.duplicates,
                        inserted=result.normalized_inserted,
                        deduplicated=result.duplicates,
                    )
                except Exception as exc:
                    await self.jobs.fail(season, resolution.session_key, endpoint, exc)
                    # Endpoint gaps are isolated and reported as a partial job.
                    # Durable successful checkpoints are never discarded merely
                    # because one optional OpenF1 dataset is unavailable.
                    continue
            final = await self.finalizer.finalize(resolution.session_key)
            refreshed = await self.jobs.get(season, resolution.session_key)
            assert refreshed is not None
            partial = bool(refreshed.failed_endpoint)
            await self.jobs.finish(season, resolution.session_key, partial=partial)
            summary = self._summary(
                BackfillStatus.PARTIAL if partial else BackfillStatus.COMPLETED,
                season,
                resolution,
                selected,
            )
            summary.skipped_completed_endpoints = skipped
            summary.rows_fetched = refreshed.rows_fetched
            summary.rows_processed = refreshed.rows_processed
            summary.rows_inserted = refreshed.rows_inserted
            summary.rows_deduplicated = refreshed.rows_deduplicated
            summary.normalized_event_count = final.normalized_event_count
            summary.source_availability = final.source_availability
            summary.replay_available = final.replay_available
            summary.results_available = final.results_available
            return summary

    @staticmethod
    def _summary(
        status: BackfillStatus,
        season: int,
        resolution: SessionResolution,
        endpoints: list[str],
    ) -> BackfillSummary:
        return BackfillSummary(
            status=status,
            season=season,
            session_key=resolution.session_key,
            meeting_key=resolution.meeting_key,
            room_slug=resolution.room_slug,
            match_method=resolution.match_method,
            confidence=resolution.confidence,
            candidate_count=resolution.candidate_count,
            endpoints=endpoints,
        )

    def _resolution(
        self,
        row: dict[str, Any],
        room: RaceRoom | None,
        method: str,
        candidates: int,
        delta: float | None = None,
    ) -> SessionResolution:
        key = row.get("session_key")
        if key is None:
            raise ValueError("Matched provider session has no session_key")
        session_type = self._session_type(row)
        return SessionResolution(
            session_key=str(key),
            meeting_key=str(row["meeting_key"]) if row.get("meeting_key") is not None else None,
            room_slug=room.slug if room else None,
            match_method=method,
            confidence="high" if method != "metadata_date_type" or (delta or 0) <= 12 else "medium",
            candidate_count=candidates,
            date_delta_hours=delta,
            normalized_provider_session_name=(session_type.value if session_type else "unknown"),
            provider_session=row,
        )

    @staticmethod
    def _session_type(row: dict[str, Any]) -> SessionType | None:
        return SessionType.from_provider_name(
            str(row.get("session_name") or row.get("session_type") or "")
        )

    @staticmethod
    def _require_completed(row: dict[str, Any]) -> None:
        end = OpenF1HistoricalBackfillService._date(row.get("date_end"))
        if end is None or end > datetime.now(UTC):
            raise ValueError("Historical backfill only accepts completed provider sessions")

    @staticmethod
    def _date(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return OpenF1HistoricalBackfillService._aware(parsed)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def backfill_job_status(record: OpenF1BackfillJobRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": str(record.id),
        "status": record.status,
        "session_key": record.session_key,
        "room_slug": record.room_slug,
        "current_endpoint": record.cursor_state.get("current_endpoint"),
        "rows_fetched": record.rows_fetched,
        "rows_processed": record.rows_processed,
        "rows_deduplicated": record.rows_deduplicated,
        "last_successful_endpoint": (
            record.completed_endpoints[-1] if record.completed_endpoints else None
        ),
        "last_error_category": record.last_error_code,
        "last_successful_session": (
            record.session_key if record.status in {"completed", "partial"} else None
        ),
        "updated_at": record.updated_at.isoformat(),
    }
