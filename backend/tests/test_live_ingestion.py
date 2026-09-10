# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services.rooms import RaceRoomService
from tests.test_event_pipeline import processor
from tests.test_live_room_repair import START, monza, provider
from tests.test_race_rooms_service import FakeOpenF1, FakeRoomRepository, FakeSeason


class LiveProvider(FakeOpenF1):
    def __init__(self):
        super().__init__([provider()])
        self.rows = {
            "drivers": [dict(session_key=901, driver_number=16, full_name="Charles Leclerc")],
            "position": [
                dict(session_key=901, driver_number=16, position=1, date=START.isoformat())
            ],
            "location": [
                dict(session_key=901, driver_number=16, x=100, y=200, z=1, date=START.isoformat())
            ],
        }
        self.fail = set()
        self.reads = []

    async def live_get(self, endpoint, **filters):
        self.reads.append((endpoint, filters))
        if endpoint in self.fail:
            raise TimeoutError("secret-must-not-escape")
        if endpoint == "sessions":
            return self.session_rows
        return self.rows.get(endpoint, [])


class Finalizer:
    def __init__(self):
        self.completed = []

    async def finalize(self, key, *, live=False, live_capture=False):
        if not live:
            self.completed.append(key)


class Bus:
    def __init__(self):
        self.statuses = []

    async def publish_connection_status(self, status):
        self.statuses.append(status)

    async def publish_room_status(self, room_id, status):
        pass


async def runtime(settings):
    from app.services.live_ingestion import LiveSessionIngestionService

    repo = FakeRoomRepository()
    client = LiveProvider()
    rooms = RaceRoomService(repo, FakeSeason([monza()]), 2026, openf1=client)
    pipeline, normalized, consumer = processor()
    finalizer = Finalizer()
    bus = Bus()
    service = LiveSessionIngestionService(
        settings=settings,
        rooms=rooms,
        client=client,
        processor=pipeline,
        repository=repo,
        finalizer=finalizer,
        event_bus=bus,
    )
    return SimpleNamespace(
        service=service,
        repo=repo,
        client=client,
        rooms=rooms,
        normalized=normalized,
        consumer=consumer,
        finalizer=finalizer,
        bus=bus,
    )


@pytest.mark.asyncio
async def test_live_rest_uses_one_canonical_key_and_publishes_normalized_location(settings):
    r = await runtime(settings)
    await r.service.run_once(now=START + timedelta(seconds=1))
    events = list(r.normalized.events.values())
    assert any(e.event_type.value == "LOCATION_SAMPLE" for e in events)
    assert {e.session_key for e in events} == {"901"}
    assert not any(e.is_replay for e in events)
    assert all(q["session_key"] == "901" for ep, q in r.client.reads if ep != "sessions")
    assert r.service.status["connection_state"] == "LIVE"


@pytest.mark.asyncio
async def test_delayed_session_resolution_retries_with_deadline_and_recovers(settings):
    r = await runtime(settings)
    r.client.session_rows = []
    await r.service.run_once(now=START)
    assert r.service.status["connection_state"] == "WAITING_FOR_SESSION_KEY"
    reads = len(r.client.reads)
    await r.service.run_once(now=START + timedelta(seconds=2))
    assert len(r.client.reads) == reads
    r.client.session_rows = [provider()]
    await r.service.run_once(now=START + timedelta(seconds=5))
    assert r.service.status["current_session_key"] == "901"
    assert r.service.status["connection_state"] == "LIVE"


@pytest.mark.asyncio
async def test_temporary_endpoint_failure_keeps_room_and_retries_without_duplicates(settings):
    r = await runtime(settings)
    r.client.fail = {"location"}
    await r.service.run_once(now=START)
    assert r.repo.rooms["2026-italian-grand-prix-race"].status.value == "live"
    assert "secret-must-not-escape" not in str(r.service.status)
    assert r.service.status["endpoints"]["location"]["state"] == "PROVIDER_UNAVAILABLE"
    r.client.fail.clear()
    await r.service.run_once(now=START + timedelta(seconds=15))
    assert r.service.status["endpoints"]["location"]["state"] == "LIVE"
    count = len(r.normalized.events)
    await r.service.run_once(now=START + timedelta(seconds=30))
    assert len(r.normalized.events) == count


@pytest.mark.asyncio
async def test_processor_failure_keeps_entire_poll_window_retryable(settings):
    r = await runtime(settings)
    ingest_batch = r.service.processor.ingest_batch
    failed = False

    async def fail_once(rows):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("durable processor unavailable")
        await ingest_batch(rows)

    r.service.processor.ingest_batch = fail_once
    with pytest.raises(RuntimeError):
        await r.service.run_once(now=START)
    failed_window = [
        (endpoint, filters)
        for endpoint, filters in r.client.reads
        if endpoint != "sessions"
    ]
    assert failed_window

    r.client.reads.clear()
    await r.service.run_once(now=START)
    retry_window = [
        (endpoint, filters)
        for endpoint, filters in r.client.reads
        if endpoint != "sessions"
    ]

    assert retry_window == failed_window
    assert any(
        event.event_type.value == "LOCATION_SAMPLE" for event in r.normalized.events.values()
    )


@pytest.mark.asyncio
async def test_successful_poll_clears_stale_provider_error(settings):
    r = await runtime(settings)
    r.client.fail = {"sessions"}
    await r.service.run_once(now=START)
    assert r.service.status["connection_state"] == "PROVIDER_UNAVAILABLE"
    assert r.service.status["error"] == "TimeoutError"

    r.client.fail.clear()
    await r.service.run_once(now=START + timedelta(seconds=5))

    assert r.service.status["connection_state"] == "LIVE"
    assert r.service.status["error"] is None


@pytest.mark.asyncio
async def test_provider_session_end_finalizes_without_deleting_events(settings):
    r = await runtime(settings)
    await r.service.run_once(now=START)
    count = len(r.normalized.events)
    r.client.session_rows = [
        {**provider(), "date_end": (START + timedelta(seconds=30)).isoformat()}
    ]
    await r.service.run_once(now=START + timedelta(seconds=61))
    assert r.finalizer.completed == ["901"]
    assert len(r.normalized.events) >= count
    assert r.service.status["connection_state"] == "SESSION_COMPLETE"


@pytest.mark.asyncio
async def test_concurrent_viewer_requests_share_the_same_ingestion_state(settings):
    import asyncio

    r = await runtime(settings)
    await asyncio.gather(r.service.run_once(now=START), r.service.run_once(now=START))
    endpoints = [ep for ep, _ in r.client.reads if ep != "sessions"]
    assert len(endpoints) == len(set(endpoints))
    assert len({event.dedup_key for event in r.consumer.events}) == len(r.consumer.events)


@pytest.mark.parametrize(
    "session_type", list(__import__("app.domain.rooms", fromlist=["SessionType"]).SessionType)
)
def test_session_types_cannot_resolve_to_the_race_key(session_type):
    from app.domain.rooms import SessionType
    from app.services.provider_matching import OpenF1SessionMatcher

    match = OpenF1SessionMatcher().match_session(
        monza(), [provider()], session_type, scheduled_start=START
    )
    assert match.resolved is (session_type is SessionType.RACE)


@pytest.mark.asyncio
async def test_provider_finished_status_completes_before_scheduled_end(settings):
    r = await runtime(settings)
    await r.service.run_once(now=START)
    r.client.session_rows = [{**provider(), "status": "Finished"}]
    await r.service.run_once(now=START + timedelta(minutes=90))
    assert r.finalizer.completed == ["901"]


@pytest.mark.asyncio
async def test_finalizer_retries_when_next_poll_has_no_new_rows(settings):
    from unittest.mock import AsyncMock

    r = await runtime(settings)
    r.service.finalizer.finalize = AsyncMock(side_effect=[TimeoutError(), None])
    with pytest.raises(TimeoutError):
        await r.service.run_once(now=START)
    await r.service.run_once(now=START + timedelta(seconds=5))
    assert r.service.finalizer.finalize.await_count == 2


@pytest.mark.asyncio
async def test_all_endpoint_failures_report_provider_unavailable(settings):
    from app.services.live_ingestion import ENDPOINT_INTERVALS

    r = await runtime(settings)
    r.client.fail = set(ENDPOINT_INTERVALS)
    await r.service.run_once(now=START)
    assert r.service.status["connection_state"] == "PROVIDER_UNAVAILABLE"
    assert r.service.status["provider_connected"] is False


@pytest.mark.asyncio
async def test_initial_position_read_includes_drivers_without_recent_changes(settings):
    r = await runtime(settings)
    await r.service.run_once(now=START + timedelta(minutes=30))
    first = next(filters for ep, filters in r.client.reads if ep == "position")
    assert not any(name.startswith("date") for name in first)
    r.client.reads.clear()
    await r.service.run_once(now=START + timedelta(minutes=31))
    next_read = next(filters for ep, filters in r.client.reads if ep == "position")
    assert any(name.startswith("date") for name in next_read)
