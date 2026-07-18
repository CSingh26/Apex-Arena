# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from app.domain.rooms import RaceRoom, RoomPlaybackState, RoomStatus
from app.services.discussion import RaceRoomDiscussionEngine
from app.services.race_state import RaceStateEngine
from app.services.session_semantics import normalize_qualifying_phase
from app.storage.redis import EventBus
from app.storage.repositories import SqlNormalizedEventRepository
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)


class ReplayUnavailableError(RuntimeError):
    pass


class RoomReplayCoordinator:
    """Process persisted normalized events according to durable room playback state."""

    def __init__(
        self,
        rooms: SqlRaceRoomRepository,
        events: SqlNormalizedEventRepository,
        discussion: RaceRoomDiscussionEngine,
        race_state: RaceStateEngine,
        event_bus: EventBus,
        base_interval_seconds: float = 0.6,
    ) -> None:
        self.rooms = rooms
        self.events = events
        self.discussion = discussion
        self.race_state = race_state
        self.event_bus = event_bus
        self.base_interval_seconds = base_interval_seconds
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def start(self, room: RaceRoom, *, restart: bool = False) -> RoomPlaybackState:
        if room.session_key is None:
            raise ReplayUnavailableError("No normalized session is linked to this room")
        available = await self.events.list_for_session(room.session_key, limit=1)
        if not available:
            raise ReplayUnavailableError("No normalized events are available for replay")
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            await self._cancel(room.id)
            if restart:
                await self.rooms.reset_discussion(room.id)
                self.discussion.reset_session(room.session_key, str(room.id))
                await self.race_state.reset_session(room.session_key)
                playback = await self.rooms.update_playback(
                    room.id,
                    current_event_sequence=0,
                    current_message_sequence=0,
                    current_lap=0,
                    playback_speed=1,
                    is_paused=False,
                    started_at=datetime.now(UTC),
                )
                await self._publish_status(str(room.id), {"status": "discussion_reset"})
            else:
                playback = await self.rooms.update_playback(
                    room.id,
                    is_paused=False,
                    started_at=datetime.now(UTC),
                )
            await self.rooms.update_room_status(room.id, RoomStatus.REPLAYING)
            await self._publish(room.id, playback, RoomStatus.REPLAYING)
            self._tasks[room.id] = asyncio.create_task(
                self._run(room), name=f"room-replay:{room.slug}"
            )
            return playback

    async def pause(self, room: RaceRoom) -> RoomPlaybackState:
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self.rooms.update_playback(room.id, is_paused=True)
            await self.rooms.update_room_status(room.id, RoomStatus.PAUSED)
            await self._publish(room.id, playback, RoomStatus.PAUSED)
            return playback

    async def resume(self, room: RaceRoom) -> RoomPlaybackState:
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self.rooms.update_playback(room.id, is_paused=False)
            await self.rooms.update_room_status(room.id, RoomStatus.REPLAYING)
            if room.id not in self._tasks or self._tasks[room.id].done():
                self._tasks[room.id] = asyncio.create_task(
                    self._run(room), name=f"room-replay:{room.slug}"
                )
            await self._publish(room.id, playback, RoomStatus.REPLAYING)
            return playback

    async def set_speed(self, room: RaceRoom, speed: float) -> RoomPlaybackState:
        if speed not in {0.5, 1.0, 2.0, 4.0, 8.0}:
            raise ValueError("Unsupported playback speed")
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self.rooms.update_playback(room.id, playback_speed=speed)
            await self._publish(room.id, playback, room.status)
            return playback

    async def seek_to_sequence(self, room: RaceRoom, sequence: int) -> RoomPlaybackState:
        if room.session_key is None:
            raise ReplayUnavailableError("No normalized session is linked to this room")
        maximum = await self.events.max_sequence(room.session_key)
        if sequence > maximum:
            raise ReplayUnavailableError("Replay sequence is outside the available event range")
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self._rebuild_to_sequence(room, sequence)
            room_status = await self._status_after_seek(room, sequence, maximum)
            await self._publish(room.id, playback, room_status)
            return playback

    async def seek_to_lap(self, room: RaceRoom, lap_number: int) -> RoomPlaybackState:
        if room.session_key is None:
            raise ReplayUnavailableError("No normalized session is linked to this room")
        sequence = await self.events.sequence_for_lap(room.session_key, lap_number)
        if sequence is None:
            raise ReplayUnavailableError("Replay lap is outside the available event range")
        maximum = await self.events.max_sequence(room.session_key)
        target_sequence = max(0, sequence - 1)
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self._rebuild_to_sequence(
                room,
                target_sequence,
                displayed_lap=lap_number,
            )
            room_status = await self._status_after_seek(
                room,
                target_sequence,
                maximum,
            )
            await self._publish(room.id, playback, room_status)
            return playback

    async def seek_to_phase(self, room: RaceRoom, phase: str) -> RoomPlaybackState:
        """Seek qualifying replays using provider-confirmed Q/SQ boundaries."""

        if room.session_key is None:
            raise ReplayUnavailableError("No normalized session is linked to this room")
        normalized_phase = normalize_qualifying_phase(phase, room.session_type)
        if normalized_phase is None:
            raise ReplayUnavailableError("Replay phase is not valid for this session")
        sequence = await self._sequence_for_phase(room.session_key, normalized_phase)
        if sequence is None:
            raise ReplayUnavailableError(
                "Replay phase boundary is not available from provider data"
            )
        maximum = await self.events.max_sequence(room.session_key)
        target_sequence = max(0, sequence - 1)
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self._rebuild_to_sequence(room, target_sequence)
            room_status = await self._status_after_seek(room, target_sequence, maximum)
            await self._publish(room.id, playback, room_status)
            return playback

    async def seek_to_session_time(
        self,
        room: RaceRoom,
        session_time: float,
    ) -> RoomPlaybackState:
        if room.session_key is None:
            raise ReplayUnavailableError("No normalized session is linked to this room")
        if session_time < 0:
            raise ReplayUnavailableError("Replay session time cannot be negative")
        sequence = await self._sequence_for_session_time(room.session_key, session_time)
        if sequence is None:
            raise ReplayUnavailableError("Replay session time is outside the available range")
        maximum = await self.events.max_sequence(room.session_key)
        target_sequence = max(0, sequence - 1)
        async with self._locks.setdefault(room.id, asyncio.Lock()):
            playback = await self._rebuild_to_sequence(room, target_sequence)
            room_status = await self._status_after_seek(room, target_sequence, maximum)
            await self._publish(room.id, playback, room_status)
            return playback

    async def close(self) -> None:
        await asyncio.gather(
            *(self._cancel(room_id) for room_id in list(self._tasks)),
            return_exceptions=True,
        )

    async def _run(self, room: RaceRoom) -> None:
        assert room.session_key is not None
        try:
            while True:
                should_wait = False
                async with self._locks.setdefault(room.id, asyncio.Lock()):
                    playback = await self.rooms.get_playback(room.id)
                    if playback.is_paused:
                        should_wait = True
                    else:
                        events = await self.events.list_for_session(
                            room.session_key,
                            after_sequence=playback.current_event_sequence,
                            limit=1,
                        )
                        if not events:
                            completed = await self.rooms.update_playback(room.id, is_paused=True)
                            await self.rooms.update_room_status(room.id, RoomStatus.COMPLETED)
                            await self._publish(room.id, completed, RoomStatus.COMPLETED)
                            await self._publish_status(str(room.id), {"status": "replay_complete"})
                            return
                        event = events[0]
                        await self.race_state.consume(event)
                        await self.discussion.consume(event)
                        message_sequence = await self.rooms.max_message_sequence(room.id)
                        advanced = await self.rooms.update_playback(
                            room.id,
                            current_event_sequence=event.sequence_number,
                            current_message_sequence=message_sequence,
                            current_lap=event.lap_number,
                        )
                        await self.rooms.update_room_status(
                            room.id,
                            RoomStatus.REPLAYING,
                            current_lap=event.lap_number,
                            last_event_at=event.event_time,
                        )
                        await self._publish(room.id, advanced, RoomStatus.REPLAYING)
                if should_wait:
                    await asyncio.sleep(0.1)
                    continue
                await asyncio.sleep(self.base_interval_seconds / advanced.playback_speed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Room replay failed room=%s error=%s", room.slug, type(exc).__name__)
            await self.rooms.update_room_status(room.id, RoomStatus.FAILED)
            await self._publish_status(
                str(room.id), {"status": "failed", "detail": "Replay processing failed"}
            )

    async def _rebuild_to_sequence(
        self,
        room: RaceRoom,
        target_sequence: int,
        *,
        displayed_lap: int | None = None,
    ) -> RoomPlaybackState:
        assert room.session_key is not None
        await self.race_state.reset_session(room.session_key)
        self.discussion.reset_session(room.session_key, str(room.id))
        cursor = 0
        last_lap: int | None = None
        while cursor < target_sequence:
            events = await self.events.list_for_session(
                room.session_key,
                after_sequence=cursor,
                limit=250,
            )
            eligible = [event for event in events if event.sequence_number <= target_sequence]
            if not eligible:
                break
            for event in eligible:
                await self.race_state.consume(event)
                await self.discussion.consume(event)
                cursor = event.sequence_number
                if event.lap_number is not None:
                    last_lap = event.lap_number
            if len(eligible) < len(events):
                break
        message_sequence = await self.rooms.max_message_sequence_for_event(
            room.id,
            room.session_key,
            target_sequence,
        )
        return await self.rooms.update_playback(
            room.id,
            current_event_sequence=target_sequence,
            current_message_sequence=message_sequence,
            current_lap=displayed_lap if displayed_lap is not None else last_lap or 0,
        )

    async def _status_after_seek(
        self,
        room: RaceRoom,
        target_sequence: int,
        maximum_sequence: int,
    ) -> RoomStatus:
        if room.status == RoomStatus.COMPLETED and target_sequence < maximum_sequence:
            await self.rooms.update_room_status(room.id, RoomStatus.PAUSED)
            return RoomStatus.PAUSED
        return room.status

    async def _sequence_for_phase(self, session_key: str, phase: str) -> int | None:
        cursor = 0
        while True:
            events = await self.events.list_for_session(
                session_key,
                after_sequence=cursor,
                limit=250,
            )
            if not events:
                return None
            for event in events:
                if str(event.payload.get("session_phase") or "").upper() == phase:
                    return event.sequence_number
            cursor = events[-1].sequence_number
            if len(events) < 250:
                return None

    async def _sequence_for_session_time(
        self,
        session_key: str,
        session_time: float,
    ) -> int | None:
        cursor = 0
        first_event_time: datetime | None = None
        last_sequence: int | None = None
        while True:
            events = await self.events.list_for_session(
                session_key,
                after_sequence=cursor,
                limit=250,
            )
            if not events:
                return last_sequence if session_time == 0 else None
            if first_event_time is None:
                first_event_time = events[0].event_time
            for event in events:
                last_sequence = event.sequence_number
                supplied_time = event.payload.get("session_time")
                try:
                    elapsed = (
                        float(supplied_time)
                        if supplied_time is not None
                        else (event.event_time - first_event_time).total_seconds()
                    )
                except (TypeError, ValueError):
                    elapsed = (event.event_time - first_event_time).total_seconds()
                if elapsed >= session_time:
                    return event.sequence_number
            cursor = events[-1].sequence_number
            if len(events) < 250:
                return None

    async def _publish(
        self, room_id: UUID, playback: RoomPlaybackState, status: RoomStatus
    ) -> None:
        try:
            await self.event_bus.publish_room_state(str(room_id), playback.model_dump(mode="json"))
        except Exception as exc:
            logger.error("Playback publication failed error=%s", type(exc).__name__)
        await self._publish_status(str(room_id), {"status": status.value})

    async def _publish_status(self, room_id: str, status: dict[str, str]) -> None:
        try:
            await self.event_bus.publish_room_status(room_id, status)
        except Exception as exc:
            logger.error("Room status publication failed error=%s", type(exc).__name__)

    async def _cancel(self, room_id: UUID) -> None:
        task = self._tasks.pop(room_id, None)
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
