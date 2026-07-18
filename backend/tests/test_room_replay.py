# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import (
    RaceRoom,
    RoomMode,
    RoomPlaybackState,
    RoomStatus,
    SourceAvailability,
)
from app.services.room_replay import ReplayUnavailableError, RoomReplayCoordinator


def replay_room(*, session_key: str | None = "day3-session") -> RaceRoom:
    return RaceRoom(
        slug="day3-validation-room",
        session_key=session_key,
        season=2026,
        round_number=99,
        race_name="Day 3 Validation Race",
        official_name="Apex Arena Day 3 Validation Race",
        circuit_name="Apex Validation Circuit",
        country="Development",
        scheduled_start=datetime(2026, 7, 17, 12, tzinfo=UTC),
        status=RoomStatus.READY,
        mode=RoomMode.DEVELOPMENT,
        total_laps=12,
        source_availability=SourceAvailability.TELEMETRY,
        is_development=True,
    )


def replay_event(sequence: int, lap: int) -> NormalizedRaceEvent:
    timestamp = datetime(2026, 7, 17, 12, 0, sequence, tzinfo=UTC)
    return NormalizedRaceEvent(
        session_key="day3-session",
        source="fixture",
        event_time=timestamp,
        received_at=timestamp,
        sequence_number=sequence,
        event_type=RaceEventType.LAP_COMPLETED,
        driver_numbers=[4],
        lap_number=lap,
        payload={"lap_number": lap},
        dedup_key=f"day3:{sequence}",
        is_replay=True,
    )


class FakeRoomRepository:
    def __init__(self, room: RaceRoom) -> None:
        self.room = room
        self.playback = RoomPlaybackState(room_id=room.id)
        self.status_updates: list[tuple[RoomStatus, int | None]] = []
        self.reset_count = 0
        self.message_sequence = 0
        self.event_message_sequences: dict[int, int] = {}
        self.event_message_queries: list[tuple[str, int]] = []
        self.terminal_status = asyncio.Event()

    async def get_playback(self, room_id: UUID) -> RoomPlaybackState:
        assert room_id == self.room.id
        return self.playback.model_copy(deep=True)

    async def update_playback(
        self,
        room_id: UUID,
        *,
        current_event_sequence: int | None = None,
        current_message_sequence: int | None = None,
        current_lap: int | None = None,
        playback_speed: float | None = None,
        is_paused: bool | None = None,
        started_at: datetime | None = None,
    ) -> RoomPlaybackState:
        assert room_id == self.room.id
        updates: dict[str, object] = {"updated_at": datetime.now(UTC)}
        for key, value in (
            ("current_event_sequence", current_event_sequence),
            ("current_message_sequence", current_message_sequence),
            ("current_lap", current_lap),
            ("playback_speed", playback_speed),
            ("is_paused", is_paused),
            ("started_at", started_at),
        ):
            if value is not None:
                updates[key] = value
        self.playback = self.playback.model_copy(update=updates)
        return self.playback.model_copy(deep=True)

    async def update_room_status(
        self,
        room_id: UUID,
        status: RoomStatus,
        *,
        current_lap: int | None = None,
        last_event_at: datetime | None = None,
    ) -> None:
        assert room_id == self.room.id
        self.status_updates.append((status, current_lap))
        updates: dict[str, object] = {"status": status}
        if current_lap is not None:
            updates["current_lap"] = current_lap
        if last_event_at is not None:
            updates["last_event_at"] = last_event_at
        self.room = self.room.model_copy(update=updates)
        if status in {RoomStatus.COMPLETED, RoomStatus.FAILED}:
            self.terminal_status.set()

    async def reset_discussion(self, room_id: UUID) -> None:
        assert room_id == self.room.id
        self.reset_count += 1
        self.message_sequence = 0
        self.event_message_sequences.clear()

    async def max_message_sequence(self, room_id: UUID) -> int:
        assert room_id == self.room.id
        return self.message_sequence

    async def max_message_sequence_for_event(
        self,
        room_id: UUID,
        session_key: str,
        target_sequence: int,
    ) -> int:
        assert room_id == self.room.id
        assert session_key == self.room.session_key
        self.event_message_queries.append((session_key, target_sequence))
        return max(
            (
                message_sequence
                for event_sequence, message_sequence in self.event_message_sequences.items()
                if event_sequence <= target_sequence
            ),
            default=0,
        )


class FakeEventRepository:
    def __init__(self, events: list[NormalizedRaceEvent]) -> None:
        self.events = events
        self.reads: list[tuple[str, int, int]] = []

    async def list_for_session(
        self,
        session_key: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[NormalizedRaceEvent]:
        self.reads.append((session_key, after_sequence, limit))
        return [
            event
            for event in self.events
            if event.session_key == session_key and event.sequence_number > after_sequence
        ][:limit]

    async def sequence_for_lap(self, session_key: str, lap_number: int) -> int | None:
        return next(
            (
                event.sequence_number
                for event in self.events
                if event.session_key == session_key and event.lap_number == lap_number
            ),
            None,
        )

    async def max_sequence(self, session_key: str) -> int:
        return max(
            (
                event.sequence_number
                for event in self.events
                if event.session_key == session_key
            ),
            default=0,
        )


class FakeDiscussion:
    def __init__(
        self,
        rooms: FakeRoomRepository,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.rooms = rooms
        self.failure = failure
        self.consumed: list[int] = []
        self.resets: list[tuple[str, str]] = []
        self.block_on_sequence: int | None = None
        self.consume_started = asyncio.Event()
        self.consume_release = asyncio.Event()
        self._blocked_once = False

    async def consume(self, event: NormalizedRaceEvent) -> None:
        if self.failure is not None:
            raise self.failure
        self.consumed.append(event.sequence_number)
        if self.block_on_sequence == event.sequence_number and not self._blocked_once:
            self._blocked_once = True
            self.consume_started.set()
            await self.consume_release.wait()
        if event.sequence_number not in self.rooms.event_message_sequences:
            self.rooms.message_sequence += 1
            self.rooms.event_message_sequences[event.sequence_number] = (
                self.rooms.message_sequence
            )

    def reset_session(self, session_key: str, room_id: str) -> None:
        self.resets.append((session_key, room_id))


class FakeRaceState:
    def __init__(self) -> None:
        self.consumed: list[int] = []
        self.resets: list[str] = []

    async def consume(self, event: NormalizedRaceEvent) -> None:
        self.consumed.append(event.sequence_number)

    async def reset_session(self, session_key: str) -> None:
        self.resets.append(session_key)


class FakeEventBus:
    def __init__(self) -> None:
        self.states: list[dict[str, object]] = []
        self.statuses: list[dict[str, object]] = []
        self.fail = False

    async def publish_room_state(self, room_id: str, state: dict[str, object]) -> str:
        if self.fail:
            raise ConnectionError("redis://user:secret@private-host")
        self.states.append({"room_id": room_id, **state})
        return "1-0"

    async def publish_room_status(self, room_id: str, status: dict[str, object]) -> str:
        if self.fail:
            raise ConnectionError("redis://user:secret@private-host")
        self.statuses.append({"room_id": room_id, **status})
        return "1-0"


def coordinator(
    room: RaceRoom,
    events: list[NormalizedRaceEvent],
    *,
    interval: float = 0,
    discussion_failure: Exception | None = None,
) -> tuple[
    RoomReplayCoordinator,
    FakeRoomRepository,
    FakeEventRepository,
    FakeDiscussion,
    FakeRaceState,
    FakeEventBus,
]:
    rooms = FakeRoomRepository(room)
    event_repository = FakeEventRepository(events)
    discussion = FakeDiscussion(rooms, failure=discussion_failure)
    race_state = FakeRaceState()
    event_bus = FakeEventBus()
    replay = RoomReplayCoordinator(
        rooms,  # type: ignore[arg-type]
        event_repository,  # type: ignore[arg-type]
        discussion,  # type: ignore[arg-type]
        race_state,  # type: ignore[arg-type]
        event_bus,  # type: ignore[arg-type]
        base_interval_seconds=interval,
    )
    return replay, rooms, event_repository, discussion, race_state, event_bus


@pytest.mark.asyncio
async def test_start_consumes_events_in_order_and_completes_durably() -> None:
    room = replay_room()
    replay, rooms, _, discussion, race_state, bus = coordinator(
        room, [replay_event(1, 1), replay_event(2, 2)]
    )

    started = await replay.start(room)
    await asyncio.wait_for(rooms.terminal_status.wait(), timeout=1)

    assert started.is_paused is False
    assert started.started_at is not None
    assert discussion.consumed == [1, 2]
    assert race_state.consumed == [1, 2]
    assert rooms.playback.current_event_sequence == 2
    assert rooms.playback.current_message_sequence == 2
    assert rooms.playback.current_lap == 2
    assert rooms.playback.is_paused is True
    assert [status for status, _ in rooms.status_updates] == [
        RoomStatus.REPLAYING,
        RoomStatus.REPLAYING,
        RoomStatus.REPLAYING,
        RoomStatus.COMPLETED,
    ]
    assert bus.statuses[-1]["status"] == "replay_complete"
    await replay.close()


@pytest.mark.asyncio
async def test_restart_resets_discussion_state_and_replays_from_sequence_zero() -> None:
    room = replay_room()
    replay, rooms, events, discussion, race_state, bus = coordinator(
        room, [replay_event(1, 1)]
    )
    rooms.playback = rooms.playback.model_copy(
        update={
            "current_event_sequence": 99,
            "current_message_sequence": 88,
            "current_lap": 12,
            "playback_speed": 8,
        }
    )

    restarted = await replay.start(room, restart=True)
    await asyncio.wait_for(rooms.terminal_status.wait(), timeout=1)

    assert restarted.current_event_sequence == 0
    assert restarted.current_message_sequence == 0
    assert restarted.current_lap == 0
    assert restarted.playback_speed == 1
    assert rooms.reset_count == 1
    assert discussion.resets == [("day3-session", str(room.id))]
    assert race_state.resets == ["day3-session"]
    assert events.reads[-2:] == [("day3-session", 0, 1), ("day3-session", 1, 1)]
    assert any(status["status"] == "discussion_reset" for status in bus.statuses)
    await replay.close()


@pytest.mark.asyncio
async def test_pause_prevents_consumption_until_resume_then_completes() -> None:
    room = replay_room()
    replay, rooms, _, discussion, _, _ = coordinator(
        room,
        [replay_event(1, 1), replay_event(2, 2)],
        interval=0.01,
    )

    await replay.start(room)
    paused = await replay.pause(room)
    await asyncio.sleep(0.02)

    assert paused.is_paused is True
    assert discussion.consumed == []
    assert rooms.room.status is RoomStatus.PAUSED

    resumed = await replay.resume(rooms.room)
    await asyncio.wait_for(rooms.terminal_status.wait(), timeout=1)

    assert resumed.is_paused is False
    assert discussion.consumed == [1, 2]
    await replay.close()


@pytest.mark.asyncio
async def test_speed_and_seek_controls_update_durable_playback_and_publish() -> None:
    room = replay_room()
    replay, rooms, _, discussion, race_state, bus = coordinator(
        room,
        [replay_event(3, 2), replay_event(7, 5)],
    )

    speed = await replay.set_speed(room, 4)
    by_sequence = await replay.seek_to_sequence(room, 6)
    by_lap = await replay.seek_to_lap(room, 5)

    assert speed.playback_speed == 4
    assert by_sequence.current_event_sequence == 6
    assert by_sequence.current_message_sequence == 1
    assert by_sequence.current_lap == 2
    assert by_lap.current_event_sequence == 6
    assert by_lap.current_message_sequence == 1
    assert by_lap.current_lap == 5
    assert rooms.playback.current_event_sequence == 6
    assert rooms.event_message_queries == [
        ("day3-session", 6),
        ("day3-session", 6),
    ]
    assert discussion.consumed == [3, 3]
    assert discussion.resets == [
        ("day3-session", str(room.id)),
        ("day3-session", str(room.id)),
    ]
    assert race_state.consumed == [3, 3]
    assert race_state.resets == ["day3-session", "day3-session"]
    assert len(bus.states) == 3


@pytest.mark.asyncio
async def test_running_replay_and_seek_are_serialized_into_one_coherent_state() -> None:
    room = replay_room()
    replay, rooms, _, discussion, race_state, _ = coordinator(
        room,
        [
            replay_event(1, 1),
            replay_event(2, 2),
            replay_event(3, 3),
            replay_event(4, 4),
        ],
        interval=1,
    )
    discussion.block_on_sequence = 1

    await replay.start(room)
    await asyncio.wait_for(discussion.consume_started.wait(), timeout=1)
    seek_task = asyncio.create_task(replay.seek_to_sequence(room, 3))
    await asyncio.sleep(0)

    assert seek_task.done() is False
    assert rooms.playback.current_event_sequence == 0

    discussion.consume_release.set()
    sought = await asyncio.wait_for(seek_task, timeout=1)
    paused = await replay.pause(rooms.room)

    assert sought.current_event_sequence == 3
    assert sought.current_message_sequence == 3
    assert sought.current_lap == 3
    assert paused.current_event_sequence == 3
    assert paused.is_paused is True
    assert discussion.consumed == [1, 1, 2, 3]
    assert race_state.consumed == [1, 1, 2, 3]
    assert race_state.resets == ["day3-session"]
    assert rooms.event_message_queries == [("day3-session", 3)]
    assert 4 not in discussion.consumed
    await replay.close()


@pytest.mark.asyncio
async def test_speed_and_seek_controls_reject_values_outside_available_range() -> None:
    room = replay_room()
    replay, _, _, _, _, _ = coordinator(room, [replay_event(3, 2), replay_event(7, 5)])

    with pytest.raises(ValueError, match="Unsupported playback speed"):
        await replay.set_speed(room, 3)
    with pytest.raises(ReplayUnavailableError, match="sequence"):
        await replay.seek_to_sequence(room, 8)
    with pytest.raises(ReplayUnavailableError, match="lap"):
        await replay.seek_to_lap(room, 4)


@pytest.mark.asyncio
async def test_seeking_completed_room_before_finish_transitions_it_to_paused() -> None:
    room = replay_room().model_copy(update={"status": RoomStatus.COMPLETED})
    replay, rooms, _, _, _, bus = coordinator(
        room,
        [replay_event(1, 1), replay_event(2, 2), replay_event(3, 3)],
    )

    playback = await replay.seek_to_sequence(room, 2)

    assert playback.current_event_sequence == 2
    assert playback.current_message_sequence == 2
    assert rooms.room.status is RoomStatus.PAUSED
    assert rooms.status_updates == [(RoomStatus.PAUSED, None)]
    assert bus.statuses[-1]["status"] == "paused"


@pytest.mark.asyncio
async def test_start_rejects_rooms_without_a_replayable_session() -> None:
    room_without_session = replay_room(session_key=None)
    replay, _, _, _, _, _ = coordinator(room_without_session, [])

    with pytest.raises(ReplayUnavailableError, match="No normalized session"):
        await replay.start(room_without_session)

    room_without_events = replay_room()
    replay, _, _, _, _, _ = coordinator(room_without_events, [])
    with pytest.raises(ReplayUnavailableError, match="No normalized events"):
        await replay.start(room_without_events)


@pytest.mark.asyncio
async def test_processing_failure_marks_room_failed_without_exposing_error_detail() -> None:
    room = replay_room()
    replay, rooms, _, _, race_state, bus = coordinator(
        room,
        [replay_event(1, 1)],
        discussion_failure=RuntimeError("postgresql://user:secret@private-host/database"),
    )

    await replay.start(room)
    await asyncio.wait_for(rooms.terminal_status.wait(), timeout=1)

    assert race_state.consumed == [1]
    assert rooms.playback.current_event_sequence == 0
    assert bus.statuses[-1]["status"] == "failed"
    assert bus.statuses[-1]["detail"] == "Replay processing failed"
    assert "private-host" not in str(bus.statuses[-1])
    await replay.close()


@pytest.mark.asyncio
async def test_event_bus_outage_does_not_stop_replay_progress() -> None:
    room = replay_room()
    replay, rooms, _, discussion, _, bus = coordinator(room, [replay_event(1, 1)])
    bus.fail = True

    await replay.start(room)
    await asyncio.wait_for(rooms.terminal_status.wait(), timeout=1)

    assert discussion.consumed == [1]
    assert rooms.playback.current_event_sequence == 1
    assert bus.states == []
    assert bus.statuses == []
    await replay.close()
