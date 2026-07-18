# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.domain.models import MeetingLifecycleStatus, RaceMeeting, RaceWeekendSession
from app.domain.rooms import (
    IngestionStatus,
    PublicSessionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionType,
    SourceAvailability,
    WeekendStatus,
)
from app.services.rooms import RaceRoomService


def meeting(
    *,
    status: MeetingLifecycleStatus = MeetingLifecycleStatus.COMPLETED,
    race_name: str = "Belgian Grand Prix",
    country: str = "Belgium",
    circuit_name: str = "Circuit de Spa-Francorchamps",
    race_date: date = date(2026, 7, 19),
    round_number: int = 13,
    sessions: list[RaceWeekendSession] | None = None,
) -> RaceMeeting:
    return RaceMeeting(
        season_year=2026,
        round_number=round_number,
        race_name=race_name,
        circuit_id="spa",
        circuit_name=circuit_name,
        locality="Spa",
        country=country,
        race_date=race_date,
        race_start=datetime.combine(race_date, datetime.min.time(), UTC).replace(hour=13),
        status=status,
        is_target=True,
        sessions=sessions or [],
    )


def openf1_session(**updates: Any) -> dict[str, Any]:
    session: dict[str, Any] = {
        "session_key": 9839,
        "session_name": "Race",
        "year": 2026,
        "country_name": "Belgium",
        "country_code": "BEL",
        "circuit_short_name": "Spa-Francorchamps",
        "date_start": "2026-07-19T13:02:00Z",
    }
    session.update(updates)
    return session


class FakeRoomRepository:
    def __init__(self) -> None:
        self.seed_calls = 0
        self.upserts: list[RaceRoom] = []
        self.rooms: dict[str, RaceRoom] = {}
        self.cleanup_calls: list[str] = []
        self.list_calls: list[dict[str, object]] = []

    async def seed_agents(self, agents: list[object]) -> None:
        assert len(agents) == 5
        self.seed_calls += 1

    async def upsert_room(self, room: RaceRoom, agent_ids: list[str]) -> RaceRoom:
        assert len(agent_ids) == 5
        self.upserts.append(room)
        current = self.rooms.get(room.slug)
        if current is not None and current.message_count:
            room = room.model_copy(
                update={
                    "id": current.id,
                    "message_count": current.message_count,
                    "current_lap": current.current_lap,
                    "last_event_at": current.last_event_at,
                    "status": current.status,
                    "mode": current.mode,
                    "source_availability": current.source_availability,
                    "telemetry_quality": current.telemetry_quality,
                }
            )
        self.rooms[room.slug] = room
        return room

    async def delete_empty_development_room(self, slug: str) -> bool:
        self.cleanup_calls.append(slug)
        return True

    async def get_room(self, slug: str) -> RaceRoom | None:
        return self.rooms.get(slug)

    async def list_rooms(self, **filters: object) -> tuple[list[RaceRoom], int]:
        self.list_calls.append(filters)
        rooms = [room for room in self.rooms.values() if not room.is_development]
        season = filters.get("season")
        if season is not None:
            rooms = [room for room in rooms if room.season == season]
        rooms.sort(key=lambda room: room.scheduled_start)
        return rooms, len(rooms)


class FakeSeason:
    def __init__(self, meetings: list[RaceMeeting]) -> None:
        self.meetings = meetings
        self.calls = 0

    async def calendar(self, year: int) -> list[RaceMeeting]:
        assert year == 2026
        self.calls += 1
        await asyncio.sleep(0)
        return self.meetings


class FakeOpenF1:
    def __init__(
        self,
        sessions: list[dict[str, Any]],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.session_rows = sessions
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    async def sessions(self, **filters: Any) -> list[dict[str, Any]]:
        self.calls.append(filters)
        if self.failure is not None:
            raise self.failure
        return self.session_rows


class FakeFixture:
    def __init__(self) -> None:
        self.seed_count = 0

    async def seed(self) -> int:
        self.seed_count += 1
        return 14


@pytest.mark.asyncio
async def test_completed_meeting_matches_openf1_race_and_persists_provider_metadata() -> None:
    repository = FakeRoomRepository()
    season = FakeSeason([meeting()])
    openf1 = FakeOpenF1([openf1_session()])
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        season,  # type: ignore[arg-type]
        2026,
        openf1=openf1,  # type: ignore[arg-type]
    )

    synchronized = await service.force_sync()

    room = repository.rooms["2026-belgian-grand-prix-race"]
    assert synchronized == 1
    assert room.session_key == "9839"
    assert room.country_code == "BEL"
    assert room.actual_start == datetime(2026, 7, 19, 13, 2, tzinfo=UTC)
    assert room.status is RoomStatus.PENDING
    assert room.mode is RoomMode.REPLAY
    assert room.source_availability is SourceAvailability.UNAVAILABLE
    assert room.eligibility_status is RoomEligibilityStatus.PROVIDER_PENDING
    assert room.ingestion_status is IngestionStatus.PENDING
    assert room.replay_available is False
    assert room.telemetry_quality == "openf1_session_discovered"
    assert openf1.calls == [{"year": 2026}]


@pytest.mark.asyncio
async def test_session_match_can_use_circuit_when_provider_country_name_differs() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )

    await service.sync_meetings(
        [meeting()],
        [openf1_session(country_name="Kingdom of Belgium")],
    )

    room = repository.rooms["2026-belgian-grand-prix-race"]
    assert room.session_key == "9839"
    assert room.source_availability is SourceAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_nonmatching_or_failed_provider_does_not_create_false_historical_room() -> None:
    nonmatches = [
        openf1_session(session_name="Qualifying"),
        openf1_session(session_key=2, year=2025),
        openf1_session(session_key=3, date_start="2026-08-19T13:00:00Z"),
        openf1_session(
            session_key=4,
            country_name="Italy",
            circuit_short_name="Monza",
        ),
    ]
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([meeting()]),  # type: ignore[arg-type]
        2026,
        openf1=FakeOpenF1(nonmatches),  # type: ignore[arg-type]
    )

    await service.force_sync()

    assert repository.rooms == {}

    failed_repository = FakeRoomRepository()
    failed_service = RaceRoomService(
        failed_repository,  # type: ignore[arg-type]
        FakeSeason([meeting()]),  # type: ignore[arg-type]
        2026,
        openf1=FakeOpenF1([], failure=TimeoutError("private provider detail")),  # type: ignore[arg-type]
    )
    await failed_service.force_sync()
    assert failed_repository.rooms == {}


@pytest.mark.asyncio
async def test_lifecycle_creates_live_room_but_keeps_upcoming_session_calendar_only() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )

    await service.sync_meetings(
        [
            meeting(status=MeetingLifecycleStatus.LIVE, race_name="Live Grand Prix"),
            meeting(
                status=MeetingLifecycleStatus.UPCOMING,
                race_name="Future Grand Prix",
                race_date=date(2026, 7, 26),
            ),
        ],
        [openf1_session(country_name="Belgium")],
        now=datetime(2026, 7, 19, 14, tzinfo=UTC),
    )

    live = repository.rooms["2026-live-grand-prix-race"]
    assert (live.status, live.mode, live.source_availability) == (
        RoomStatus.PENDING,
        RoomMode.REPLAY,
        SourceAvailability.UNAVAILABLE,
    )
    assert "2026-future-grand-prix-race" not in repository.rooms


@pytest.mark.asyncio
async def test_concurrent_catalog_reads_are_idempotent_and_side_effect_free() -> None:
    repository = FakeRoomRepository()
    season = FakeSeason([meeting()])
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        season,  # type: ignore[arg-type]
        2026,
        openf1=FakeOpenF1([openf1_session()]),  # type: ignore[arg-type]
    )

    await asyncio.gather(*(service.ensure_catalog() for _ in range(10)))

    assert season.calls == 1
    assert repository.seed_calls == 0
    assert repository.upserts == []
    assert repository.rooms == {}


@pytest.mark.asyncio
async def test_resync_preserves_dynamic_room_state_via_repository_upsert_contract() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )
    race = meeting()

    await service.sync_meetings([race], [openf1_session()])
    slug = "2026-belgian-grand-prix-race"
    repository.rooms[slug] = repository.rooms[slug].model_copy(
        update={
            "message_count": 14,
            "current_lap": 8,
            "status": RoomStatus.PAUSED,
            "mode": RoomMode.DEVELOPMENT,
        }
    )
    await service.sync_meetings([race], [openf1_session()])

    preserved = repository.rooms[slug]
    assert preserved.message_count == 14
    assert preserved.current_lap == 8
    assert preserved.status is RoomStatus.PAUSED
    assert len(repository.upserts) == 2


@pytest.mark.asyncio
async def test_force_sync_seeds_fixture_and_reports_room_count() -> None:
    repository = FakeRoomRepository()
    fixture = FakeFixture()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([meeting()]),  # type: ignore[arg-type]
        2026,
        fixture=fixture,  # type: ignore[arg-type]
        openf1=FakeOpenF1([openf1_session()]),  # type: ignore[arg-type]
    )

    count = await service.force_sync()

    assert count == 2
    assert fixture.seed_count == 1
    assert repository.cleanup_calls == ["development-day2-validation"]
    assert set(repository.rooms) == {
        "day3-validation-room",
        "2026-belgian-grand-prix-race",
    }


@pytest.mark.asyncio
async def test_catalog_read_does_not_seed_or_publish_the_development_fixture() -> None:
    repository = FakeRoomRepository()
    fixture = FakeFixture()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
        fixture=fixture,  # type: ignore[arg-type]
    )

    await service.ensure_catalog()
    await service.ensure_catalog()

    assert fixture.seed_count == 0
    assert repository.cleanup_calls == []
    assert "day3-validation-room" not in repository.rooms


def sprint_weekend() -> RaceMeeting:
    return meeting(
        sessions=[
            RaceWeekendSession(
                name="Sprint Qualifying",
                starts_at=datetime(2026, 7, 17, 10, tzinfo=UTC),
            ),
            RaceWeekendSession(
                name="Sprint",
                starts_at=datetime(2026, 7, 18, 10, tzinfo=UTC),
            ),
            RaceWeekendSession(
                name="Qualifying",
                starts_at=datetime(2026, 7, 18, 15, tzinfo=UTC),
            ),
        ]
    )


def standard_weekend() -> RaceMeeting:
    return meeting(
        sessions=[
            RaceWeekendSession(
                name="Qualifying",
                starts_at=datetime(2026, 7, 18, 15, tzinfo=UTC),
            )
        ]
    )


def sprint_provider_sessions() -> list[dict[str, Any]]:
    common = {
        "meeting_key": 77,
        "meeting_name": "Belgian Grand Prix",
        "year": 2026,
        "country_name": "Belgium",
        "country_code": "BEL",
        "circuit_short_name": "Spa-Francorchamps",
    }
    return [
        {
            **common,
            "session_key": 7701,
            "session_name": "Sprint Shootout",
            "date_start": "2026-07-17T10:00:00Z",
        },
        {
            **common,
            "session_key": 7702,
            "session_name": "Sprint Race",
            "date_start": "2026-07-18T10:00:00Z",
        },
        {
            **common,
            "session_key": 7703,
            "session_name": "Qualifying",
            "date_start": "2026-07-18T15:00:00Z",
        },
        {
            **common,
            "session_key": 7704,
            "session_name": "Race",
            "date_start": "2026-07-19T13:00:00Z",
        },
    ]


@pytest.mark.asyncio
async def test_standard_weekend_creates_distinct_qualifying_and_race_identities() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )
    provider_sessions = [
        {
            **openf1_session(),
            "meeting_key": 61,
            "meeting_name": "Belgian Grand Prix",
            "session_key": 6101,
            "session_name": "Qualifying",
            "date_start": "2026-07-18T15:00:00Z",
        },
        {
            **openf1_session(),
            "meeting_key": 61,
            "meeting_name": "Belgian Grand Prix",
            "session_key": 6102,
        },
    ]

    await service.sync_meetings(
        [standard_weekend()],
        provider_sessions,
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert set(repository.rooms) == {
        "2026-belgian-grand-prix-qualifying",
        "2026-belgian-grand-prix-race",
    }
    assert [
        item.session_type
        for item in sorted(repository.rooms.values(), key=lambda room: room.scheduled_start)
    ] == [SessionType.QUALIFYING, SessionType.RACE]


@pytest.mark.asyncio
async def test_sprint_shootout_normalizes_and_repeat_sync_creates_no_duplicates() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )
    race = sprint_weekend()
    sessions = sprint_provider_sessions()

    await service.sync_meetings([race], sessions, now=datetime(2026, 7, 20, tzinfo=UTC))
    await service.sync_meetings([race], sessions, now=datetime(2026, 7, 20, tzinfo=UTC))

    assert len(repository.rooms) == 4
    assert {room.session_type for room in repository.rooms.values()} == {
        SessionType.SPRINT_QUALIFYING,
        SessionType.SPRINT,
        SessionType.QUALIFYING,
        SessionType.RACE,
    }
    assert {room.meeting_key for room in repository.rooms.values()} == {"77"}
    assert len(repository.upserts) == 4


@pytest.mark.asyncio
async def test_grouped_sprint_event_preserves_official_session_order() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )
    await service.sync_meetings(
        [sprint_weekend()],
        sprint_provider_sessions(),
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    events, total = await service.grouped_events(
        status=WeekendStatus.COMPLETED,
        session_type=SessionType.SPRINT,
        is_sprint_weekend=True,
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert total == 1
    assert events[0].is_sprint_weekend is True
    assert [item.session_type for item in events[0].sessions] == [
        SessionType.SPRINT_QUALIFYING,
        SessionType.SPRINT,
        SessionType.QUALIFYING,
        SessionType.RACE,
    ]
    assert all(item.status is PublicSessionStatus.COMPLETED for item in events[0].sessions)
    assert all(not item.room_eligible for item in events[0].sessions)
    assert all(
        item.eligibility is RoomEligibilityStatus.PROVIDER_PENDING for item in events[0].sessions
    )
    assert repository.list_calls[-1]["include_unavailable"] is True


@pytest.mark.asyncio
async def test_missing_sprint_provider_data_remains_read_only_and_unavailable() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )
    only_race = [sprint_provider_sessions()[-1]]
    await service.sync_meetings(
        [sprint_weekend()], only_race, now=datetime(2026, 7, 20, tzinfo=UTC)
    )

    events, _ = await service.grouped_events(now=datetime(2026, 7, 20, tzinfo=UTC))
    sprint = next(item for item in events[0].sessions if item.session_type is SessionType.SPRINT)

    assert sprint.room_slug is None
    assert sprint.room_eligible is False
    assert sprint.eligibility is RoomEligibilityStatus.PROVIDER_PENDING
    assert sprint.data_availability is SourceAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_completed_and_upcoming_event_groups_are_both_oldest_first() -> None:
    early = meeting(
        race_name="Australian Grand Prix",
        race_date=date(2026, 3, 8),
        round_number=1,
        status=MeetingLifecycleStatus.COMPLETED,
    )
    recent = meeting(
        race_name="British Grand Prix",
        race_date=date(2026, 7, 5),
        round_number=9,
        status=MeetingLifecycleStatus.COMPLETED,
    )
    next_race = meeting(
        race_name="Hungarian Grand Prix",
        race_date=date(2026, 7, 26),
        round_number=10,
        status=MeetingLifecycleStatus.UPCOMING,
    )
    finale = meeting(
        race_name="Abu Dhabi Grand Prix",
        race_date=date(2026, 12, 6),
        round_number=24,
        status=MeetingLifecycleStatus.UPCOMING,
    )
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([finale, recent, next_race, early]),  # type: ignore[arg-type]
        2026,
    )

    events, _ = await service.grouped_events(now=datetime(2026, 7, 18, tzinfo=UTC))

    assert [event.event_name for event in events] == [
        "Australian Grand Prix",
        "British Grand Prix",
        "Hungarian Grand Prix",
        "Abu Dhabi Grand Prix",
    ]
