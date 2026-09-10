# SPDX-License-Identifier: AGPL-3.0-only
"""Scheduled, shared OpenF1 REST intake into the existing race event pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.settings import Settings
from app.domain.rooms import RaceRoom, RoomStatus
from app.providers.openf1 import LiveConnectionState, OpenF1LiveClient, OpenF1RestClient
from app.services.event_pipeline import RaceEventProcessor
from app.services.locations import (
    LocationIngestionService,
    SessionLocationService,
    _date_window,
    downsample,
    parse_location_rows,
)
from app.services.normalization import OpenF1EventNormalizer
from app.services.openf1_backfill import OpenF1RoomFinalizer
from app.services.race_state import RaceStateEngine
from app.services.raw_events import RawEventInput
from app.services.rooms import SESSION_DURATION, RaceRoomService
from app.storage.redis import EventBus
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)

# About 45 requests/minute for one live session, including catalog and geometry.
# Endpoint errors have separate backoff so a location outage cannot stop timing.
ENDPOINT_INTERVALS = {
    "drivers": 120,
    "position": 15,
    "intervals": 15,
    "laps": 30,
    "stints": 60,
    "pit": 30,
    "race_control": 15,
    "weather": 30,
    "car_data": 15,
    "location": 15,
}
DATED_ENDPOINTS = {
    "position",
    "intervals",
    "race_control",
    "weather",
    "car_data",
    "location",
}
RETRY_SECONDS = (5, 15, 30, 60)


def utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


@dataclass
class EndpointProgress:
    next_at: datetime | None = None
    cursor: datetime | None = None
    failures: int = 0
    state: str = "WAITING_FOR_PROVIDER"
    rows: int = 0
    error: str | None = None


@dataclass
class LiveSessionProgress:
    room: RaceRoom
    endpoints: dict[str, EndpointProgress] = field(default_factory=dict)
    last_event_at: datetime | None = None
    geometry_next_at: datetime | None = None
    finalize_next_at: datetime | None = None
    complete: bool = False
    seen: set[str] = field(default_factory=set)


class LiveSessionIngestionService:
    """One worker per existing advisory lease, independent of browser lifetime."""

    def __init__(
        self,
        *,
        settings: Settings,
        rooms: RaceRoomService,
        client: OpenF1RestClient,
        processor: RaceEventProcessor,
        repository: SqlRaceRoomRepository,
        finalizer: OpenF1RoomFinalizer,
        event_bus: EventBus,
        locations: SessionLocationService | None = None,
        location_ingestion: LocationIngestionService | None = None,
        mqtt_client: OpenF1LiveClient | None = None,
        race_state: RaceStateEngine | None = None,
    ) -> None:
        self.settings = settings
        self.rooms = rooms
        self.client = client
        self.processor = processor
        self.repository = repository
        self.finalizer = finalizer
        self.event_bus = event_bus
        self.locations = locations
        self.location_ingestion = location_ingestion
        self.mqtt_client = mqtt_client
        self.race_state = race_state
        self.sessions: dict[str, LiveSessionProgress] = {}
        self._catalog_next_at: datetime | None = None
        self._resolution_failures = 0
        self._lock = asyncio.Lock()
        self._status: dict[str, Any] = {
            "connection_state": "WAITING_FOR_SESSION_KEY",
            "current_session_key": None,
            "ingestion_running": False,
        }

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def run_once(self, *, now: datetime | None = None) -> None:
        async with self._lock:
            await self._run_once(utc(now or datetime.now(UTC)))

    async def _run_once(self, now: datetime) -> None:
        self._status["ingestion_running"] = True
        if self._catalog_next_at is None or now >= self._catalog_next_at:
            try:
                await self.rooms.force_sync(now=now, fresh_provider=True, live_window_only=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.rooms.provider_error = type(exc).__name__
                logger.warning("Live catalog failed error=%s", type(exc).__name__)
            delay = 60
            if self.rooms.provider_error:
                delay = self._resolution_delay()
            self._catalog_next_at = now + timedelta(seconds=delay)

        rooms, _ = await self.repository.list_rooms(
            season=self.settings.season_year,
            limit=500,
            include_unavailable=True,
        )
        active = [
            room
            for room in rooms
            if room.status is RoomStatus.LIVE and utc(room.scheduled_start) <= now
        ]
        pending = next((room for room in active if not room.session_key), None)
        if pending is not None:
            self._status.update(
                connection_state=(
                    "PROVIDER_UNAVAILABLE"
                    if self.rooms.provider_error
                    else "WAITING_FOR_SESSION_KEY"
                ),
                room_slug=pending.slug,
                current_session_key=None,
                calendar_state="LIVE",
                error=self.rooms.provider_error,
            )
            # An unresolved identity uses bounded retries, not the catalog's long TTL.
            if self._catalog_next_at == now + timedelta(seconds=60):
                self._catalog_next_at = now + timedelta(seconds=self._resolution_delay())
        elif not self.rooms.provider_error:
            self._resolution_failures = 0

        by_key = {str(row.get("session_key")): row for row in self.rooms._provider_sessions}
        for room in active:
            if room.session_key is not None and room.session_key not in self.sessions:
                if self.race_state is not None:
                    state = await self.race_state.get_state(room.session_key)
                    cursor = state.sequence_number
                    while True:
                        tail = await self.processor.normalized_repository.list_for_session(
                            room.session_key,
                            after_sequence=cursor,
                            limit=1000,
                        )
                        if not tail:
                            break
                        for event in tail:
                            await self.race_state.apply(event)
                            cursor = event.sequence_number
                        if len(tail) < 1000:
                            break
                self.sessions[room.session_key] = LiveSessionProgress(room=room)
        # Keep recently finished diagnostics, but bound in-memory weekend history.
        self.sessions = {
            key: value
            for key, value in self.sessions.items()
            if not value.complete or now - utc(value.room.scheduled_start) < timedelta(days=1)
        }
        for key, progress in self.sessions.items():
            if progress.complete:
                continue
            metadata = by_key.get(key)
            if metadata is None:
                continue
            await self._poll(progress, metadata, now)
        self._status["next_catalog_retry_at"] = self._catalog_next_at.isoformat()
        self._status["checked_at"] = now.isoformat()
        self._status["provider"] = "OpenF1"
        self._status["transport"] = "rest"
        if hasattr(self.event_bus, "diagnostics"):
            self._status["event_bus"] = self.event_bus.diagnostics(
                str(self._status.get("current_session_key") or "")
            )
        try:
            await self.event_bus.publish_connection_status(self.status)
        except Exception as exc:
            logger.warning("Live diagnostics publish failed error=%s", type(exc).__name__)

    def _resolution_delay(self) -> int:
        delay = RETRY_SECONDS[min(self._resolution_failures, len(RETRY_SECONDS) - 1)]
        self._resolution_failures += 1
        return delay

    async def _poll(
        self,
        progress: LiveSessionProgress,
        metadata: dict[str, Any],
        now: datetime,
    ) -> None:
        room = progress.room
        key = room.session_key
        assert key is not None
        end = self.rooms._session_end(metadata)
        start = self.rooms._session_start(metadata) or utc(room.scheduled_start)
        finished = (
            str(metadata.get("status") or "").casefold() in {"finished", "completed", "ended"}
            or (end is not None and utc(end) <= now)
            or now >= start + SESSION_DURATION[room.session_type]
        )
        rows_to_ingest = [
            RawEventInput(
                provider_endpoint="sessions",
                session_key=key,
                raw_payload=metadata,
                event_time=start,
            )
        ]
        completed_polls: list[tuple[EndpointProgress, datetime, int, int]] = []
        last_event = progress.last_event_at
        mqtt_active = bool(
            self.mqtt_client is not None
            and self.mqtt_client.connection_state is LiveConnectionState.CONNECTED
            and self.mqtt_client.current_session_key == key
            and self.mqtt_client.last_event_at is not None
            and (now - utc(self.mqtt_client.last_event_at)).total_seconds()
            <= self.settings.live_degraded_after_seconds
        )
        if mqtt_active:
            last_event = self.mqtt_client.last_event_at
        for endpoint, interval in ENDPOINT_INTERVALS.items():
            if mqtt_active or self.settings.openf1_ingestion_mode == "mqtt":
                continue
            state = progress.endpoints.setdefault(endpoint, EndpointProgress())
            if state.next_at is not None and now < state.next_at and not finished:
                continue
            filters: dict[str, Any] = {"session_key": key}
            window_end = (
                min(now + timedelta(seconds=1), utc(end) + timedelta(seconds=1))
                if end
                else now + timedelta(seconds=1)
            )
            initial_positions = endpoint == "position" and state.cursor is None
            if endpoint in DATED_ENDPOINTS and not initial_positions:
                # Bounded initial warmup and overlap recover late rows without refetching
                # entire high-frequency sessions. The existing backfill recovers history.
                lower = state.cursor or max(start, now - timedelta(seconds=60))
                filters.update(_date_window(lower - timedelta(seconds=5), window_end))
            try:
                rows = await self.client.live_get(endpoint, **filters)
                if any(str(row.get("session_key", key)) != key for row in rows):
                    raise ValueError("Provider returned a different session")
                if endpoint == "location" and self.locations is not None:
                    samples, _ = parse_location_rows(rows)
                    samples = downsample(samples, self.settings.location_sample_interval_ms)
                    await self.locations.record_live_samples(key, samples)
                selected = rows
                if endpoint in {"location", "car_data"} or initial_positions:
                    # Full GPS series lives in its existing time-indexed store. Only the
                    # newest fix per driver is fanned out as normalized live state.
                    latest: dict[str, dict[str, Any]] = {}
                    for row in sorted(rows, key=lambda row: str(row.get("date") or "")):
                        latest[str(row.get("driver_number"))] = row
                    selected = list(latest.values())
                for row in selected:
                    timestamp = OpenF1EventNormalizer._payload_time(row) or start
                    rows_to_ingest.append(
                        RawEventInput(
                            provider_endpoint=endpoint,
                            session_key=key,
                            raw_payload=row,
                            event_time=utc(timestamp),
                        )
                    )
                    if endpoint not in {"drivers", "stints"} and timestamp <= now:
                        last_event = max(last_event or utc(timestamp), utc(timestamp))
                completed_polls.append((state, window_end, interval, len(rows)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = RETRY_SECONDS[min(state.failures, len(RETRY_SECONDS) - 1)]
                state.failures += 1
                state.next_at = now + timedelta(seconds=delay)
                state.state = "PROVIDER_UNAVAILABLE"
                state.error = type(exc).__name__
                logger.warning(
                    "Live endpoint failed session_key=%s endpoint=%s error=%s retry=%s",
                    key,
                    endpoint,
                    state.error,
                    delay,
                )
        rows_to_ingest.sort(key=lambda row: row.event_time or row.received_at)
        fresh = []
        fingerprints = []
        for row in rows_to_ingest:
            fingerprint = hashlib.sha256(
                json.dumps(
                    [row.provider_endpoint, row.raw_payload],
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            if fingerprint not in progress.seen:
                fresh.append(row)
                fingerprints.append(fingerprint)
        if fresh:
            await self.processor.ingest_batch(fresh)
            # This is only a bounded poll cache. Durable processor deduplication is
            # still authoritative on restart and when older cache entries are evicted.
            if len(progress.seen) > 50_000:
                progress.seen.clear()
            progress.seen.update(fingerprints)
        for state, window_end, interval, row_count in completed_polls:
            state.cursor = window_end - timedelta(seconds=1)
            state.failures = 0
            state.next_at = now + timedelta(seconds=interval)
            state.state = "LIVE" if row_count else "WAITING_FOR_PROVIDER"
            state.rows = row_count
            state.error = None
        if self.race_state is not None:
            state = await self.race_state.get_state(key)
            finished = finished or state.status.casefold() == "finished"
        progress.last_event_at = last_event
        if (
            fresh
            or finished
            or progress.finalize_next_at is None
            or now >= progress.finalize_next_at
        ):
            await self.finalizer.finalize(key, live=not finished, live_capture=True)
            progress.finalize_next_at = now + timedelta(seconds=30)
        if self.race_state is not None:
            # Refresh shared state even when the last event's Redis publication
            # failed and the provider has no new rows to trigger another publish.
            try:
                await self.event_bus.publish_state(await self.race_state.get_state(key))
            except Exception as exc:
                logger.warning("Live state publish failed error=%s", type(exc).__name__)
        if self.locations is not None and self.location_ingestion is not None:
            if progress.geometry_next_at is None or now >= progress.geometry_next_at:
                progress.geometry_next_at = now + timedelta(seconds=60)
                try:
                    if await self.locations.geometry(key) is None:
                        await self.location_ingestion.rebuild_geometry(key, start, now)
                except Exception as exc:
                    logger.warning(
                        "Live geometry failed session_key=%s error=%s", key, type(exc).__name__
                    )
        age = (now - last_event).total_seconds() if last_event else None
        provider_connected = mqtt_active or any(
            s.error is None for s in progress.endpoints.values()
        )
        status = (
            "SESSION_COMPLETE"
            if finished
            else "PROVIDER_UNAVAILABLE"
            if progress.endpoints and not provider_connected
            else "WAITING_FOR_PROVIDER"
            if age is None
            else "STALE"
            if age > self.settings.live_degraded_after_seconds
            else "LIVE"
        )
        self._status.update(
            connection_state=status,
            current_session_key=key,
            meeting_key=room.meeting_key,
            room_slug=room.slug,
            event=room.official_name,
            session=room.session_type.value,
            internal_session_id=str(self.rooms._session_id_from_room(room)),
            calendar_state="COMPLETED" if finished else "LIVE",
            provider_session_resolved=True,
            provider_connected=provider_connected,
            error=None if provider_connected else self.rooms.provider_error,
            last_event_at=last_event.isoformat() if last_event else None,
            last_event_age=age,
            endpoints={
                name: {
                    "state": item.state,
                    "row_count": item.rows,
                    "error": item.error,
                    "next_retry_at": item.next_at.isoformat() if item.next_at else None,
                }
                for name, item in progress.endpoints.items()
            },
        )
        if finished:
            await self.event_bus.publish_room_status(
                str(room.id), {"status": "completed", "mode": "archived"}
            )
            progress.complete = True
            self.rooms.invalidate_catalog()
        logger.info(
            "Live session room=%s session_key=%s state=%s last_event_age=%s",
            room.slug,
            key,
            status,
            age,
        )
