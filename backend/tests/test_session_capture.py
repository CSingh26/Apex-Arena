# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta

import pytest

from app.domain.rooms import PublicSessionStatus, RoomStatus, SessionType
from tests.test_live_ingestion import runtime
from tests.test_live_room_repair import START, monza, provider


@pytest.mark.asyncio
async def test_catalog_and_restart_enroll_after_nominal_end(settings):
    r = await runtime(settings)
    r.client.session_rows = [{**provider(), "date_end": (START + timedelta(hours=2)).isoformat()}]
    now = START + timedelta(hours=5)
    assert (
        r.rooms._session_status(monza(), SessionType.RACE, START, r.client.session_rows[0], now)
        == PublicSessionStatus.LIVE
    )
    await r.service.run_once(now=now)
    assert "901" in r.service.sessions
    assert not r.service.sessions["901"].complete
    dated = [filters for endpoint, filters in r.client.reads if endpoint == "intervals"]
    assert dated and all(str(now.date()) in str(filters) for filters in dated)
    assert r.finalizer.completed == []


@pytest.mark.asyncio
async def test_schedule_end_does_not_finish_capture_or_cap_rest_window(settings):
    r = await runtime(settings)
    await r.service.run_once(now=START)
    r.client.session_rows = [
        {**provider(), "date_end": (START + timedelta(seconds=30)).isoformat()}
    ]
    await r.service.run_once(now=START + timedelta(seconds=61))
    assert not r.service.sessions["901"].complete
    assert r.finalizer.completed == []
    filters = [filters for endpoint, filters in r.client.reads if endpoint == "intervals"][-1]
    assert filters["date<"] == "2026-09-06T13:01:02"


@pytest.mark.asyncio
async def test_explicit_cancelled_provider_session_never_enrolls(settings):
    r = await runtime(settings)
    r.client.session_rows = [{**provider(), "is_cancelled": True}]
    await r.service.run_once(now=START)
    assert not r.service.sessions
    assert (
        r.rooms._session_status(
            monza(), SessionType.RACE, START, r.client.session_rows[0], START
        ).value
        == "cancelled"
    )
    from app.domain.rooms import SessionRoomSummary

    events, _ = await r.rooms.grouped_events(now=START)
    summary = events[0].sessions[0]
    restored = SessionRoomSummary.model_validate_json(summary.model_dump_json())
    assert restored.status.value == "cancelled"
    assert restored.capture_state == "cancelled"
    assert restored.sporting_status == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, False])
async def test_acknowledged_cancellation_cannot_be_reopened_by_cached_metadata(settings, flag):
    from app.services.raw_events import RawEventInput

    r = await runtime(settings)
    r.service.processor.consumers.append(r.service)
    await r.service.run_once(now=START)
    await r.service.processor.ingest(
        RawEventInput(
            provider_endpoint="sessions",
            session_key="901",
            raw_payload={"is_cancelled": True},
            received_at=START + timedelta(seconds=1),
        )
    )
    await r.service.processor.flush_session("901")
    assert (
        await r.service.admit_mqtt("position", {"session_key": "901"}, START + timedelta(seconds=1))
        == "cancelled"
    )
    r.rooms._provider_sessions = [
        provider() if flag is None else {**provider(), "is_cancelled": flag}
    ]
    r.client.reads.clear()
    await r.service.run_once(now=START + timedelta(seconds=2))
    assert (
        await r.service.admit_mqtt("position", {"session_key": "901"}, START + timedelta(seconds=3))
        == "cancelled"
    )
    assert not r.client.reads
    assert r.service.sessions["901"].stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_catalog_cancelled_observation_survives_metadata_outage(settings):
    r = await runtime(settings)
    await r.rooms.sync_meetings([monza()], [{**provider(), "is_cancelled": True}], now=START)
    r.rooms._meetings = []
    r.rooms._provider_sessions = []
    events, _ = await r.rooms.grouped_events(now=START + timedelta(seconds=1))
    summary = events[0].sessions[0]
    assert summary.status is PublicSessionStatus.CANCELLED
    assert summary.capture_state == "cancelled"
    assert summary.sporting_status == "unknown"


@pytest.mark.asyncio
async def test_consumed_terminal_stops_capture_and_prevents_restart_poll(settings):
    from app.services.race_state import RaceStateEngine
    from tests.test_race_state import SnapshotRepository

    r = await runtime(settings)
    state = RaceStateEngine(SnapshotRepository())
    r.service.race_state = state
    r.service.processor.consumers.insert(0, state)
    r.client.rows["race_control"] = [
        {
            "session_key": 901,
            "category": "SessionStatus",
            "message": "SESSION FINISHED",
            "date": START.isoformat(),
        }
    ]
    await r.service.run_once(now=START)
    assert r.service.sessions["901"].stop_reason == "terminal"
    assert r.finalizer.completed == ["901"]
    r.service.sessions.clear()
    r.client.reads.clear()
    await r.service.run_once(now=START + timedelta(seconds=1))
    assert not r.client.reads
    assert r.service.sessions["901"].stop_reason == "terminal"
    assert r.service.status["connection_state"] == "SESSION_COMPLETE"


@pytest.mark.asyncio
async def test_missing_metadata_expires_at_stable_schedule_deadline(settings):
    r = await runtime(settings)
    await r.service.run_once(now=START)
    r.rooms._provider_sessions = []
    r.client.session_rows = []
    await r.service.run_once(now=START + timedelta(hours=12))
    assert r.service.sessions["901"].complete
    assert r.service.sessions["901"].stop_reason == "expired_unconfirmed"
    assert r.service.status["connection_state"] == "EXPIRED_UNCONFIRMED"
    assert r.finalizer.completed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [-1, 12, 48])
async def test_future_and_expired_sessions_do_not_enroll(settings, hours):
    r = await runtime(settings)
    await r.service.run_once(now=START + timedelta(hours=hours))
    assert not r.service.sessions


@pytest.mark.asyncio
async def test_persisted_completed_category_does_not_prevent_near_live_restart(settings):
    r = await runtime(settings)
    await r.rooms.sync_meetings([monza()], [provider()], now=START)
    for room in r.repo.rooms.values():
        room.status = RoomStatus.COMPLETED
    r.service._catalog_next_at = START + timedelta(hours=10)
    await r.service.run_once(now=START + timedelta(hours=5))
    assert "901" in r.service.sessions


@pytest.mark.asyncio
async def test_catalog_exposes_schedule_basis_and_consumed_terminal_only(settings):
    r = await runtime(settings)
    r.rooms.intelligence_algorithm_version = "synthetic-v1"

    async def terminal(keys, *, algorithm_version):
        return {"901"}

    r.repo.confirmed_terminal_sessions = terminal
    await r.rooms.sync_meetings([monza()], [provider()], now=START)
    events, _ = await r.rooms.grouped_events(now=START + timedelta(hours=5))
    race = next(
        item for event in events for item in event.sessions if item.session_type is SessionType.RACE
    )
    assert race.status is PublicSessionStatus.COMPLETED
    assert race.status_basis == "consumed_terminal_control"
    assert race.sporting_status == "finished"
    assert race.capture_state == "terminal"


@pytest.mark.asyncio
@pytest.mark.parametrize("outage", [False, True])
@pytest.mark.parametrize("seconds", [43199, 43200, 43201])
async def test_catalog_terminal_knowledge_does_not_expire(settings, outage, seconds):
    r = await runtime(settings)
    r.rooms.intelligence_algorithm_version = "synthetic-v1"

    async def terminal(keys, *, algorithm_version):
        assert keys == ["901"]
        return {"901"}

    r.repo.confirmed_terminal_sessions = terminal
    await r.rooms.sync_meetings([monza()], [provider()], now=START)
    if outage:
        r.rooms._meetings = []
        r.rooms._provider_sessions = []
    events, _ = await r.rooms.grouped_events(now=START + timedelta(seconds=seconds))
    race = events[0].sessions[0]
    assert race.capture_state == "terminal"
    assert race.status_basis == "consumed_terminal_control"
    assert race.sporting_status == "finished"


@pytest.mark.asyncio
async def test_metadata_outage_catalog_uses_same_capture_deadline(settings):
    r = await runtime(settings)
    await r.rooms.sync_meetings([monza()], [provider()], now=START)
    r.rooms._meetings = []
    r.rooms._provider_sessions = []
    events, _ = await r.rooms.grouped_events(now=START + timedelta(hours=5))
    race = events[0].sessions[0]
    assert race.status is PublicSessionStatus.LIVE
    assert race.capture_state == "watching"
    events, _ = await r.rooms.grouped_events(now=START + timedelta(hours=12))
    assert events[0].sessions[0].capture_state == "expired_unconfirmed"


@pytest.mark.asyncio
async def test_finalizer_refuses_unconfirmed_live_completion_before_database_io():
    from app.services.openf1_backfill import OpenF1RoomFinalizer

    with pytest.raises(ValueError, match="terminal"):
        await OpenF1RoomFinalizer(None).finalize("synthetic", live_capture=True, live=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["unknown", "expired", "cancelled", "terminal", "active"])
async def test_mqtt_dispatch_uses_enrolled_capture_admission(settings, reason):
    from datetime import UTC, datetime

    from app.providers.openf1 import OpenF1AuthService, OpenF1LiveClient

    r = await runtime(settings)
    await r.service.run_once(now=START)
    now = datetime.now(UTC)
    progress = r.service.sessions["901"]
    progress.room.scheduled_start = now - timedelta(seconds=1)
    if reason == "expired":
        progress.room.scheduled_start = now - timedelta(hours=13)
    if reason == "terminal":
        progress.terminal_confirmed = True
    if reason == "cancelled":
        progress.cancelled = True
    mqtt = OpenF1LiveClient(
        settings.model_copy(update={"event_ordering_buffer_ms": 0}),
        OpenF1AuthService(settings),
        processor=r.service.processor,
    )
    mqtt.admit_message = r.service.admit_mqtt
    before = len(r.normalized.events)
    await mqtt._handle_message(
        "v1/position",
        {
            "session_key": 999 if reason == "unknown" else 901,
            "driver_number": 4,
            "position": 2,
            "date": now.isoformat(),
        },
    )
    assert len(r.normalized.events) == before + (reason == "active")
    if reason != "active":
        assert mqtt.status()["capture_admission"] != "accepted"
