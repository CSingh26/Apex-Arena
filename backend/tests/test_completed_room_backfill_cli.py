# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.cli import backfill_completed_rooms
from app.domain.rooms import (
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionType,
    SourceAvailability,
)
from app.services.openf1_backfill import BackfillStatus, BackfillSummary


def room(slug: str, *, session_key: str | None = None) -> RaceRoom:
    return RaceRoom(
        slug=slug,
        event_slug=slug.removesuffix("-race"),
        meeting_key="55",
        session_key=session_key,
        season=2026,
        round_number=1,
        race_name="Australian Grand Prix",
        official_name="Australian Grand Prix",
        circuit_name="Albert Park",
        country="Australia",
        session_type=SessionType.RACE,
        scheduled_start=datetime(2026, 3, 8, tzinfo=UTC),
        status=RoomStatus.PENDING,
        mode=RoomMode.REPLAY,
        eligibility_status=RoomEligibilityStatus.PROVIDER_PENDING,
        ingestion_status=IngestionStatus.PENDING,
        source_availability=SourceAvailability.UNAVAILABLE,
        replay_available=False,
    )


class FakeRoomRepository:
    def __init__(self, rooms: list[RaceRoom]) -> None:
        self.rooms = {item.slug: item for item in rooms}
        self.candidate_kwargs: dict[str, object] = {}

    async def list_completed_backfill_candidates(self, **kwargs: object) -> list[RaceRoom]:
        self.candidate_kwargs = kwargs
        return list(self.rooms.values())

    async def get_room(self, slug: str) -> RaceRoom | None:
        value = self.rooms.get(slug)
        if value is None:
            return None
        return value.model_copy(
            update={
                "session_key": f"{slug}-session",
                "source_availability": SourceAvailability.LIMITED,
                "replay_available": True,
            }
        )


class FakeNormalizedEvents:
    async def count(self, session_key: str | None = None) -> int:
        return 12 if session_key else 0


class FakeServices:
    current: FakeServices | None = None

    def __init__(self, _settings: object) -> None:
        assert FakeServices.current is not None
        self.__dict__.update(FakeServices.current.__dict__)

    async def close(self) -> None:
        self.closed = True


class FakeBackfill:
    def __init__(self, **kwargs: object) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> BackfillSummary:
        self.calls.append(kwargs)
        slug = str(kwargs["room_slug"])
        return BackfillSummary(
            status=BackfillStatus.COMPLETED,
            season=2026,
            session_key=f"{slug}-session",
            meeting_key="55",
            room_slug=slug,
            match_method="metadata_date_type",
            confidence="high",
            candidate_count=1,
            endpoints=["drivers"],
            rows_fetched=10,
            rows_inserted=7,
            rows_deduplicated=3,
            normalized_event_count=12,
            source_availability=SourceAvailability.LIMITED,
            replay_available=True,
            results_available=True,
        )


@pytest.mark.asyncio
async def test_completed_room_batch_backfill_aggregates_room_results(monkeypatch) -> None:
    repository = FakeRoomRepository([room("first-race"), room("second-race")])
    services = SimpleNamespace(
        room_repository=repository,
        normalized_event_repository=FakeNormalizedEvents(),
        processor=SimpleNamespace(consumers=["redis"]),
        openf1=None,
        historical=None,
        backfill_jobs=None,
        database=None,
        room_finalizer=None,
        closed=False,
    )
    FakeServices.current = services  # type: ignore[assignment]
    monkeypatch.setattr(backfill_completed_rooms, "Settings", lambda **_: SimpleNamespace())
    monkeypatch.setattr(backfill_completed_rooms, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(backfill_completed_rooms, "AppServices", FakeServices)
    monkeypatch.setattr(backfill_completed_rooms, "OpenF1HistoricalBackfillService", FakeBackfill)

    args = backfill_completed_rooms.parser().parse_args(
        ["--season", "2026", "--max-rooms", "2", "--resume"]
    )

    code = await backfill_completed_rooms.run(args)

    assert code == 0
    assert services.processor.consumers == []
    assert repository.candidate_kwargs == {"season": 2026, "room_slug": None, "limit": 2}


@pytest.mark.asyncio
async def test_completed_room_batch_backfill_reports_failures(monkeypatch) -> None:
    class FailingBackfill(FakeBackfill):
        async def run(self, **kwargs: object) -> BackfillSummary:
            raise RuntimeError("no confident OpenF1 session match")

    services = SimpleNamespace(
        room_repository=FakeRoomRepository([room("first-race")]),
        normalized_event_repository=FakeNormalizedEvents(),
        processor=SimpleNamespace(consumers=[]),
        openf1=None,
        historical=None,
        backfill_jobs=None,
        database=None,
        room_finalizer=None,
        closed=False,
    )
    FakeServices.current = services  # type: ignore[assignment]
    monkeypatch.setattr(backfill_completed_rooms, "Settings", lambda **_: SimpleNamespace())
    monkeypatch.setattr(backfill_completed_rooms, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(backfill_completed_rooms, "AppServices", FakeServices)
    monkeypatch.setattr(
        backfill_completed_rooms,
        "OpenF1HistoricalBackfillService",
        FailingBackfill,
    )

    args = backfill_completed_rooms.parser().parse_args(["--season", "2026"])

    assert await backfill_completed_rooms.run(args) == 2
