# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import (
    ChatGenerationStatus,
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SourceAvailability,
)
from app.services.room_chat_generation import HistoricalRoomChatGenerator
from tests.fixtures.race_room_events import race_room_event


class FakeRooms:
    def __init__(self, rooms: list[RaceRoom], outcomes: list[bool]) -> None:
        self.rooms = rooms
        self.outcomes = outcomes
        self.active_count = 0
        self.statuses: list[tuple[str, ChatGenerationStatus]] = []
        self.archived = 0

    async def list_chat_generation_candidates(self, **_: object) -> list[RaceRoom]:
        return self.rooms

    async def mark_generation_status(
        self,
        room_id,
        status: ChatGenerationStatus,
        *,
        generation_version: str,
        error: str | None = None,
    ) -> None:
        self.statuses.append((str(room_id), status))

    async def archive_generated_messages(self, room_id, generation_version: str) -> int:
        self.archived += self.active_count
        self.active_count = 0
        return self.archived

    async def insert_message(self, message, evidence):
        inserted = self.outcomes.pop(0)
        if not inserted:
            return message, False
        self.active_count += 1
        return message.model_copy(update={"id": uuid4(), "sequence": self.active_count}), True


class FakeEvents:
    def __init__(self, events_by_session: dict[str, list[NormalizedRaceEvent]]) -> None:
        self.events_by_session = events_by_session

    async def list_for_session(
        self, session_key: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[NormalizedRaceEvent]:
        events = [
            event
            for event in self.events_by_session.get(session_key, [])
            if event.sequence_number > after_sequence
        ]
        return events[:limit]


def room(slug: str = "2026-australian-grand-prix-race", session_key: str = "test-race-room"):
    return RaceRoom(
        slug=slug,
        event_slug=slug.removesuffix("-race"),
        session_key=session_key,
        season=2026,
        round_number=1,
        race_name="Australian Grand Prix",
        official_name="Australian Grand Prix",
        circuit_name="Albert Park",
        country="Australia",
        scheduled_start=datetime(2026, 3, 8, tzinfo=UTC),
        status=RoomStatus.READY,
        mode=RoomMode.ARCHIVED,
        eligibility_status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
        ingestion_status=IngestionStatus.READY,
        source_availability=SourceAvailability.LIMITED,
        replay_available=True,
    )


def critical_event(session_key: str = "test-race-room") -> NormalizedRaceEvent:
    return race_room_event(RaceEventType.SAFETY_CAR).model_copy(
        update={
            "session_key": session_key,
            "source": "openf1_historical",
            "dedup_key": f"{session_key}:safety-car",
        }
    )


async def run_generator(
    rooms: FakeRooms,
    events: FakeEvents,
    *,
    force_regenerate: bool = False,
    max_messages_per_room: int | None = 100,
):
    generator = HistoricalRoomChatGenerator(
        rooms=rooms,  # type: ignore[arg-type]
        events=events,  # type: ignore[arg-type]
        topic_cooldown_seconds=0,
    )
    return await generator.run(
        season=2026,
        completed_only=True,
        room_slug=None,
        dry_run=False,
        force_regenerate=force_regenerate,
        max_rooms=None,
        max_messages_per_room=max_messages_per_room,
        generation_version="v1",
    )


@pytest.mark.asyncio
async def test_room_summary_counts_actual_database_rows_inserted() -> None:
    rooms = FakeRooms([room()], [True, True, True])
    events = FakeEvents({"test-race-room": [critical_event()]})
    before = rooms.active_count

    summary = await run_generator(rooms, events)

    result = summary.results[0]
    assert result.triggers_selected == 1
    assert result.messages_inserted == 3
    assert result.messages_skipped == 0
    assert before + summary.messages_inserted == rooms.active_count


@pytest.mark.asyncio
async def test_top_level_summary_equals_sum_of_room_summaries() -> None:
    first = room("first-room", "session-one")
    second = room("second-room", "session-two")
    rooms = FakeRooms([first, second], [True, True, True, True, False, True])
    events = FakeEvents(
        {
            "session-one": [critical_event("session-one")],
            "session-two": [critical_event("session-two")],
        }
    )

    summary = await run_generator(rooms, events)

    assert summary.messages_inserted == sum(item.messages_inserted for item in summary.results)
    assert [item.messages_inserted for item in summary.results] == [3, 2]
    assert [item.messages_skipped for item in summary.results] == [0, 1]


@pytest.mark.asyncio
async def test_rerun_reports_zero_inserted_and_correct_skipped_count() -> None:
    rooms = FakeRooms([room()], [False])
    events = FakeEvents({"test-race-room": [critical_event()]})

    summary = await run_generator(rooms, events)

    result = summary.results[0]
    assert result.messages_inserted == 0
    assert result.messages_skipped == 1
    assert summary.messages_inserted == 0


@pytest.mark.asyncio
async def test_partial_generation_retains_accurate_counts() -> None:
    rooms = FakeRooms([room()], [True, True, True, True, True, True])
    events = FakeEvents(
        {
            "test-race-room": [
                critical_event().model_copy(update={"sequence_number": 1, "dedup_key": "one"}),
                critical_event().model_copy(update={"sequence_number": 2, "dedup_key": "two"}),
            ]
        }
    )

    summary = await run_generator(rooms, events, max_messages_per_room=3)

    result = summary.results[0]
    assert result.status == ChatGenerationStatus.PARTIAL.value
    assert result.messages_inserted == 3
    assert result.messages_skipped == 0


@pytest.mark.asyncio
async def test_force_regeneration_archives_before_counting_new_rows() -> None:
    rooms = FakeRooms([room()], [True, True, True])
    rooms.active_count = 3
    events = FakeEvents({"test-race-room": [critical_event()]})

    summary = await run_generator(rooms, events, force_regenerate=True)

    result = summary.results[0]
    assert result.archived_messages == 3
    assert result.messages_inserted == 3
    assert rooms.active_count == 3
