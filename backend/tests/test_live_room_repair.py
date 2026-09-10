# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.models import MeetingLifecycleStatus, RaceWeekendSession
from app.domain.rooms import RoomMode, RoomStatus, SessionType, SourceAvailability
from app.services.provider_matching import OpenF1SessionMatcher
from app.services.rooms import RaceRoomService
from tests.test_race_rooms_service import FakeOpenF1, FakeRoomRepository, FakeSeason, meeting

START = datetime(2026, 9, 6, 13, tzinfo=UTC)


def monza():
    return meeting(
        status=MeetingLifecycleStatus.LIVE,
        race_name="Italian Grand Prix",
        country="Italy",
        circuit_name="Autodromo Nazionale di Monza",
        race_date=date(2026, 9, 6),
        sessions=[RaceWeekendSession(name="Race", starts_at=START)],
    )


def provider():
    return dict(
        year=2026,
        meeting_key=900,
        session_key=901,
        session_name="Race",
        circuit_short_name="Monza",
        country_name="Italy",
        date_start="2026-09-06T15:00:00+02:00",
        date_end="2026-09-06T17:00:00+02:00",
    )


@pytest.mark.asyncio
async def test_calendar_live_creates_waiting_room_without_fabricating_coverage():
    repo = FakeRoomRepository()
    service = RaceRoomService(repo, FakeSeason([monza()]), 2026)
    await service.sync_meetings([monza()], [], now=START + timedelta(minutes=1))
    room = repo.rooms["2026-italian-grand-prix-race"]
    assert room.status == RoomStatus.LIVE
    assert room.mode == RoomMode.LIVE
    assert room.session_key is None
    assert room.source_availability == SourceAvailability.UNAVAILABLE
    assert service.eligibility.evaluate_room(room, now=START).can_open


@pytest.mark.asyncio
async def test_live_room_binds_provider_published_after_first_sync():
    repo = FakeRoomRepository()
    service = RaceRoomService(repo, FakeSeason([monza()]), 2026)
    await service.sync_meetings([monza()], [], now=START)
    await service.sync_meetings([monza()], [provider()], now=START + timedelta(seconds=15))
    room = repo.rooms["2026-italian-grand-prix-race"]
    assert room.session_key == "901"
    assert room.status == RoomStatus.LIVE
    events, _ = await service.grouped_events(now=START + timedelta(minutes=1))
    assert events[0].sessions[0].room_eligible


@pytest.mark.asyncio
async def test_durable_room_fallback_preserves_archived_provider_status():
    repo = FakeRoomRepository()
    writer = RaceRoomService(repo, FakeSeason([monza()]), 2026)
    await writer.sync_meetings([monza()], [provider()], now=START + timedelta(hours=5))
    slug = "2026-italian-grand-prix-race"
    repo.rooms[slug] = repo.rooms[slug].model_copy(
        update={
            "status": RoomStatus.COMPLETED,
            "mode": RoomMode.ARCHIVED,
            "replay_available": True,
        }
    )

    reader = RaceRoomService(repo, FakeSeason([]), 2026)
    events, _ = await reader.grouped_events(now=START + timedelta(days=1))

    assert events[0].sessions[0].provider_status == "ARCHIVED"


@pytest.mark.asyncio
async def test_discovery_error_is_not_cached_forever_or_called_not_published(monkeypatch):
    repo = FakeRoomRepository()
    client = FakeOpenF1([], failure=TimeoutError("private-token"))
    service = RaceRoomService(repo, FakeSeason([monza()]), 2026, openf1=client)
    monkeypatch.setattr("app.services.rooms.time.monotonic", lambda: 100)
    await service.ensure_catalog()
    events, _ = await service.grouped_events(now=START)
    assert events[0].sessions[0].provider_status == "PROVIDER_UNAVAILABLE"
    client.failure = None
    client.session_rows = [provider()]
    monkeypatch.setattr("app.services.rooms.time.monotonic", lambda: 161)
    await service.ensure_catalog()
    assert service._provider_sessions == [provider()]


@pytest.mark.parametrize("zone", ["UTC", "Europe/Rome", "America/Phoenix"])
def test_italian_timezone_matching_is_invariant(zone):
    expected = START.astimezone(ZoneInfo(zone))
    match = OpenF1SessionMatcher().match_session(
        monza(), [provider()], SessionType.RACE, scheduled_start=expected
    )
    assert match.session_key == "901"
    assert START.astimezone(ZoneInfo("America/Phoenix")).hour == 6


def test_naive_schedule_is_interpreted_as_utc_by_canonical_matcher():
    match = OpenF1SessionMatcher().match_session(
        monza(), [provider()], SessionType.RACE, scheduled_start=START.replace(tzinfo=None)
    )
    assert match.session_key == "901"


@pytest.mark.asyncio
async def test_upcoming_session_creates_no_room():
    repo = FakeRoomRepository()
    service = RaceRoomService(repo, FakeSeason([monza()]), 2026)
    await service.sync_meetings([monza()], [provider()], now=START - timedelta(minutes=1))
    assert not repo.rooms


@pytest.mark.asyncio
async def test_live_catalog_does_not_resynchronize_old_weekends():
    from unittest.mock import AsyncMock

    service = RaceRoomService(
        FakeRoomRepository(),
        FakeSeason([monza(), monza().model_copy(update={"race_date": date(2026, 3, 8)})]),
        2026,
        openf1=FakeOpenF1([provider()]),
    )
    service._synchronize = AsyncMock(return_value=1)
    await service.force_sync(now=START, live_window_only=True)
    meetings = service._synchronize.call_args.args[0]
    assert len(meetings) == 1
    assert meetings[0].race_date == START.date()


def test_provider_completion_overrides_planned_end_on_public_card():
    service = RaceRoomService(FakeRoomRepository(), FakeSeason([]), 2026)
    status = service._session_status(
        monza(),
        SessionType.RACE,
        START,
        {**provider(), "status": "Finished"},
        START + timedelta(minutes=90),
    )
    assert status.value == "completed"
