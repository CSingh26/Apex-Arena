# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.domain.rooms import RaceRoom, RoomMode, RoomStatus, SourceAvailability
from app.services.rooms import RaceRoomService


def meeting(
    *,
    status: MeetingLifecycleStatus = MeetingLifecycleStatus.COMPLETED,
    race_name: str = "Belgian Grand Prix",
    country: str = "Belgium",
    circuit_name: str = "Circuit de Spa-Francorchamps",
    race_date: date = date(2026, 7, 19),
) -> RaceMeeting:
    return RaceMeeting(
        season_year=2026,
        round_number=13,
        race_name=race_name,
        circuit_id="spa",
        circuit_name=circuit_name,
        locality="Spa",
        country=country,
        race_date=race_date,
        race_start=datetime.combine(race_date, datetime.min.time(), UTC).replace(hour=13),
        status=status,
        is_target=True,
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

    await service.ensure_catalog()

    room = repository.rooms["2026-belgian-grand-prix-race"]
    assert room.session_key == "9839"
    assert room.country_code == "BEL"
    assert room.actual_start == datetime(2026, 7, 19, 13, 2, tzinfo=UTC)
    assert room.status is RoomStatus.READY
    assert room.mode is RoomMode.ARCHIVED
    assert room.source_availability is SourceAvailability.LIMITED
    assert room.telemetry_quality == "openf1_historical_available"
    assert openf1.calls == [{"year": 2026, "session_name": "Race"}]


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
    assert room.source_availability is SourceAvailability.LIMITED


@pytest.mark.asyncio
async def test_nonmatching_or_failed_provider_falls_back_to_results_only_metadata() -> None:
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

    await service.ensure_catalog()

    room = repository.rooms["2026-belgian-grand-prix-race"]
    assert room.session_key is None
    assert room.source_availability is SourceAvailability.RESULTS_ONLY
    assert room.telemetry_quality == "metadata_only"

    failed_repository = FakeRoomRepository()
    failed_service = RaceRoomService(
        failed_repository,  # type: ignore[arg-type]
        FakeSeason([meeting()]),  # type: ignore[arg-type]
        2026,
        openf1=FakeOpenF1([], failure=TimeoutError("private provider detail")),  # type: ignore[arg-type]
    )
    await failed_service.ensure_catalog()
    fallback = failed_repository.rooms["2026-belgian-grand-prix-race"]
    assert fallback.source_availability is SourceAvailability.RESULTS_ONLY


@pytest.mark.asyncio
async def test_lifecycle_maps_live_and_upcoming_rooms_without_claiming_telemetry() -> None:
    repository = FakeRoomRepository()
    service = RaceRoomService(
        repository,  # type: ignore[arg-type]
        FakeSeason([]),  # type: ignore[arg-type]
        2026,
    )

    await service.sync_meetings(
        [
            meeting(status=MeetingLifecycleStatus.LIVE, race_name="Live Grand Prix"),
            meeting(status=MeetingLifecycleStatus.UPCOMING, race_name="Future Grand Prix"),
        ]
    )

    live = repository.rooms["2026-live-grand-prix-race"]
    upcoming = repository.rooms["2026-future-grand-prix-race"]
    assert (live.status, live.mode, live.source_availability) == (
        RoomStatus.READY,
        RoomMode.LIVE,
        SourceAvailability.LIMITED,
    )
    assert (upcoming.status, upcoming.mode, upcoming.source_availability) == (
        RoomStatus.PENDING,
        RoomMode.REPLAY,
        SourceAvailability.UNAVAILABLE,
    )


@pytest.mark.asyncio
async def test_concurrent_catalog_requests_are_idempotent() -> None:
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
    assert repository.seed_calls == 1
    assert len(repository.upserts) == 1
    assert len(repository.rooms) == 1


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
    assert set(repository.rooms) == {
        "day3-validation-room",
        "2026-belgian-grand-prix-race",
    }
