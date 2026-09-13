# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.api import room_routes
from app.api.room_streaming import _sse, race_room_stream
from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageTopic,
    MessageType,
    RoomMessage,
    RoomPlaybackState,
)
from app.storage.room_repository import DiscussionPage
from tests.test_room_routes import api_room


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class FakeRoomEventBus:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.latest_calls: list[str] = []
        self.read_calls: list[tuple[str, str, int, int]] = []
        self.latest_id = "4-0"
        self.error: Exception | None = None

    async def latest_room_stream_id(self, room_id: str) -> str:
        self.latest_calls.append(room_id)
        if self.error is not None:
            raise self.error
        return self.latest_id

    async def read_room_stream(
        self,
        room_id: str,
        after_id: str,
        *,
        count: int,
        block_ms: int,
    ) -> list[dict[str, Any]]:
        self.read_calls.append((room_id, after_id, count, block_ms))
        if self.error is not None:
            raise self.error
        records, self.records = self.records, []
        return records


def stream_message(room_id: UUID, sequence: int) -> RoomMessage:
    return RoomMessage(
        room_id=room_id,
        agent_id="nova",
        sequence=sequence,
        lap_number=5,
        topic=MessageTopic.SUMMARY,
        message_type=MessageType.SUMMARY,
        content=f"Grounded summary {sequence}",
        confidence=Confidence.MEDIUM,
        evidence_status=EvidenceStatus.PARTIAL,
    )


def stream_services(
    room_id: UUID,
    *,
    messages: list[RoomMessage] | None = None,
    event_bus: FakeRoomEventBus | None = None,
    discussion_generation: int = 1,
) -> SimpleNamespace:
    stored = messages or []

    async def list_message_page(
        _room_id: UUID,
        *,
        expected_generation: int | None,
        after_sequence: int,
        limit: int,
    ) -> DiscussionPage:
        reset_required = (
            expected_generation is not None and expected_generation != discussion_generation
        )
        effective_after = 0 if reset_required else after_sequence
        page = [
            item.model_copy(update={"discussion_generation": discussion_generation})
            for item in stored
            if item.sequence > effective_after
        ][:limit]
        return DiscussionPage(
            discussion_generation=discussion_generation,
            messages=page,
            next_cursor=page[-1].sequence if len(page) == limit else None,
            reset_required=reset_required,
        )

    return SimpleNamespace(
        settings=SimpleNamespace(
            room_stream_backlog_limit=250,
            sse_heartbeat_seconds=3,
        ),
        room_repository=SimpleNamespace(
            list_message_page=AsyncMock(side_effect=list_message_page),
            get_playback=AsyncMock(
                return_value=RoomPlaybackState(
                    room_id=room_id,
                    discussion_generation=discussion_generation,
                )
            ),
        ),
        event_bus=event_bus or FakeRoomEventBus(),
        room_replay=SimpleNamespace(
            with_session_clock=AsyncMock(
                side_effect=lambda _session_key, playback: playback.model_copy(
                    update={"session_clock": datetime(2026, 7, 19, 13, 45, tzinfo=UTC)}
                )
            )
        ),
    )


def event_data(chunk: str) -> dict[str, Any]:
    line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


async def test_playback_is_revalidated_after_catch_up_observes_restart():
    room_id = uuid4()
    old = RoomPlaybackState(room_id=room_id, current_message_sequence=1)
    bus = FakeRoomEventBus(
        [
            {
                "stream_id": "5-0",
                "kind": "playback_state",
                "sequence_number": 1,
                "discussion_generation": 1,
                "data": old.model_dump(mode="json"),
            }
        ]
    )
    services = stream_services(room_id, event_bus=bus)
    new_message = stream_message(room_id, 1).model_copy(update={"discussion_generation": 2})
    new_page = DiscussionPage(discussion_generation=2, messages=[new_message], next_cursor=None)
    services.room_repository.list_message_page.side_effect = [
        DiscussionPage(discussion_generation=1, messages=[], next_cursor=None),
        DiscussionPage(discussion_generation=1, messages=[], next_cursor=None),
        new_page,
        DiscussionPage(discussion_generation=2, messages=[], next_cursor=None),
    ]
    stream = race_room_stream(ConnectedRequest(), services, room_id, 0)
    try:
        frames = [await anext(stream) for _ in range(6)]
        assert "discussion_generation" in frames[3]
        assert event_data(frames[3])["discussion_generation"] == 2
        assert frames[5] == ": heartbeat\n\n"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_stream_announces_connection_then_replays_backlog_and_playback() -> None:
    room_id = uuid4()
    bus = FakeRoomEventBus()
    services = stream_services(
        room_id,
        messages=[stream_message(room_id, 6), stream_message(room_id, 7)],
        event_bus=bus,
    )
    stream = race_room_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        room_id,
        5,
    )

    connected = await anext(stream)
    generation = await anext(stream)
    first = await anext(stream)
    second = await anext(stream)
    playback = await anext(stream)
    await stream.aclose()

    assert "event: connection_status" in connected
    assert event_data(connected) == {"status": "connected"}
    assert event_data(generation)["discussion_generation"] == 1
    assert "event: room_message" in first and "id: 1:6" in first
    assert "event: room_message" in second and "id: 1:7" in second
    assert "event: playback_state" in playback
    assert bus.latest_calls == [str(room_id)]
    services.room_repository.list_message_page.assert_awaited_once_with(
        room_id,
        expected_generation=None,
        after_sequence=5,
        limit=250,
    )


@pytest.mark.asyncio
async def test_stream_handoff_skips_redis_duplicate_and_emits_only_new_message() -> None:
    room_id = uuid4()
    duplicate = stream_message(room_id, 6)
    new_message = stream_message(room_id, 7)
    bus = FakeRoomEventBus(
        [
            {
                "stream_id": "5-0",
                "kind": "room_message",
                "sequence_number": 6,
                "discussion_generation": 1,
                "data": duplicate.model_dump(mode="json"),
            },
            {
                "stream_id": "6-0",
                "kind": "room_message",
                "sequence_number": 7,
                "discussion_generation": 1,
                "data": new_message.model_dump(mode="json"),
            },
        ]
    )
    services = stream_services(room_id, messages=[duplicate], event_bus=bus)
    stream = race_room_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        room_id,
        5,
    )

    await anext(stream)  # connection
    await anext(stream)  # discussion generation
    await anext(stream)  # persisted sequence 6
    await anext(stream)  # playback
    live = await anext(stream)
    await stream.aclose()

    assert "id: 1:7" in live
    assert event_data(live)["sequence"] == 7
    assert bus.read_calls == [(str(room_id), "4-0", 100, 3000)]


@pytest.mark.asyncio
async def test_stream_sends_heartbeat_when_no_live_records_arrive() -> None:
    room_id = uuid4()
    bus = FakeRoomEventBus()
    services = stream_services(room_id, event_bus=bus)
    stream = race_room_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        room_id,
        0,
    )

    await anext(stream)  # connection
    await anext(stream)  # discussion generation
    await anext(stream)  # playback
    heartbeat = await anext(stream)
    await stream.aclose()

    assert heartbeat == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_stream_degrades_safely_when_redis_is_unavailable() -> None:
    room_id = uuid4()
    bus = FakeRoomEventBus()
    bus.error = ConnectionError("redis://user:secret@private-host")
    services = stream_services(room_id, event_bus=bus)
    stream = race_room_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        room_id,
        0,
    )

    connected = await anext(stream)
    await anext(stream)  # discussion generation
    await anext(stream)  # playback
    degraded = await anext(stream)
    await stream.aclose()

    assert event_data(connected) == {"status": "connected"}
    assert event_data(degraded) == {"status": "degraded"}
    assert "private-host" not in degraded
    assert bus.read_calls[0][1] == "$"


@pytest.mark.asyncio
async def test_stream_route_prefers_numeric_last_event_id_for_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = api_room()
    services = SimpleNamespace(
        rooms=SimpleNamespace(ensure_catalog=AsyncMock()),
        room_repository=SimpleNamespace(get_room=AsyncMock(return_value=room)),
    )
    recovered: list[int] = []

    async def capture_stream(
        request: object,
        runtime: object,
        room_id: UUID,
        after_sequence: int,
        session_key: str | None = None,
        discussion_generation: int | None = None,
    ):
        recovered.append(after_sequence)
        assert discussion_generation is None
        assert session_key == room.session_key
        yield _sse("connection_status", {"status": "connected"})

    monkeypatch.setattr(room_routes, "race_room_stream", capture_stream)
    response = await room_routes.stream_race_room(
        room.slug,
        SimpleNamespace(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        after_sequence=4,
        discussion_generation=None,
        last_event_id="9",
    )

    chunk = await anext(response.body_iterator)

    assert "connection_status" in chunk
    assert recovered == [9]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("last_event_id", "query_generation", "query_sequence", "expected"),
    [
        ("2:3", 9, 99, (2, 3)),
        ("7", 2, 4, (2, 7)),
        ("malformed", 2, 4, (2, 4)),
        ("9007199254740992:3", 2, 4, (2, 4)),
    ],
)
async def test_stream_route_reconstructs_generation_aware_cursor_without_cross_epoch_max(
    monkeypatch: pytest.MonkeyPatch,
    last_event_id: str,
    query_generation: int,
    query_sequence: int,
    expected: tuple[int, int],
) -> None:
    room = api_room()
    services = SimpleNamespace(
        rooms=SimpleNamespace(ensure_catalog=AsyncMock()),
        room_repository=SimpleNamespace(get_room=AsyncMock(return_value=room)),
    )
    recovered: list[tuple[int | None, int]] = []

    async def capture_stream(
        request: object,
        runtime: object,
        room_id: UUID,
        after_sequence: int,
        session_key: str | None = None,
        discussion_generation: int | None = None,
    ):
        recovered.append((discussion_generation, after_sequence))
        yield _sse("connection_status", {"status": "connected"})

    monkeypatch.setattr(room_routes, "race_room_stream", capture_stream)
    response = await room_routes.stream_race_room(
        room.slug,
        SimpleNamespace(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        after_sequence=query_sequence,
        discussion_generation=query_generation,
        last_event_id=last_event_id,
    )
    await anext(response.body_iterator)

    assert recovered == [expected]


def test_room_sse_payload_is_compact_parseable_and_optionally_identified() -> None:
    chunk = _sse("room_status", {"status": "replaying"}, "14")

    assert chunk.startswith("id: 14\nevent: room_status\n")
    assert chunk.endswith("\n\n")
    assert event_data(chunk) == {"status": "replaying"}


@pytest.mark.asyncio
async def test_room_backlog_pages_the_complete_reconnect_history() -> None:
    room_id = uuid4()
    messages = [stream_message(room_id, n) for n in range(1, 5)]
    services = stream_services(room_id)
    services.settings.room_stream_backlog_limit = 2

    async def list_message_page(
        _room: UUID,
        *,
        expected_generation: int | None,
        after_sequence: int,
        limit: int,
    ) -> DiscussionPage:
        page = [item for item in messages if item.sequence > after_sequence][:limit]
        return DiscussionPage(
            discussion_generation=1,
            messages=page,
            next_cursor=page[-1].sequence if len(page) == limit else None,
            reset_required=expected_generation not in {None, 1},
        )

    services.room_repository.list_message_page.side_effect = list_message_page
    stream = race_room_stream(ConnectedRequest(), services, room_id, 0)
    try:
        await anext(stream)
        await anext(stream)  # discussion generation
        frames = [await anext(stream) for _ in range(4)]
        assert [event_data(frame)["sequence"] for frame in frames] == [1, 2, 3, 4]
        assert "id:" not in await anext(stream)
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_non_message_redis_frames_do_not_advance_the_message_cursor() -> None:
    room_id = uuid4()
    message = stream_message(room_id, 1)
    bus = FakeRoomEventBus(
        [
            {
                "stream_id": "5-0",
                "kind": "room_status",
                "sequence_number": 99,
                "data": {"status": "replaying"},
            },
            {
                "stream_id": "6-0",
                "kind": "room_message",
                "sequence_number": 1,
                "discussion_generation": 1,
                "data": message.model_dump(mode="json"),
            },
        ]
    )
    stream = race_room_stream(
        ConnectedRequest(),
        stream_services(room_id, event_bus=bus),
        room_id,
        0,
    )
    try:
        await anext(stream)  # connection
        await anext(stream)  # discussion generation
        await anext(stream)  # playback
        room_status = await anext(stream)
        room_message = await anext(stream)

        assert "event: room_status" in room_status
        assert "id:" not in room_status
        assert room_message.startswith("id: 1:1\n")
        assert event_data(room_message)["sequence"] == 1
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_discussion_reset_restarts_an_existing_viewer_at_sequence_one() -> None:
    """Regression: a reset must invalidate the old numeric cursor for every viewer."""
    room_id = uuid4()
    restarted = stream_message(room_id, 1).model_copy(update={"discussion_generation": 2})
    bus = FakeRoomEventBus()
    stream = race_room_stream(
        ConnectedRequest(),
        stream_services(
            room_id,
            messages=[restarted],
            event_bus=bus,
            discussion_generation=2,
        ),
        room_id,
        7,
        discussion_generation=1,
    )
    try:
        await anext(stream)  # connection
        reset = await anext(stream)
        first_restarted_message = await anext(stream)

        assert "event: discussion_generation" in reset
        assert event_data(reset)["discussion_generation"] == 2
        assert "event: room_message" in first_restarted_message
        assert event_data(first_restarted_message)["sequence"] == 1
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_stale_redis_backlog_cannot_resurrect_a_previous_generation() -> None:
    room_id = uuid4()
    current = stream_message(room_id, 1).model_copy(update={"discussion_generation": 2})
    stale = stream_message(room_id, 8).model_copy(update={"discussion_generation": 1})
    bus = FakeRoomEventBus(
        [
            {
                "stream_id": "9-0",
                "kind": "room_message",
                "sequence_number": 8,
                "discussion_generation": 1,
                "data": stale.model_dump(mode="json"),
            }
        ]
    )
    stream = race_room_stream(
        ConnectedRequest(),
        stream_services(
            room_id,
            messages=[current],
            event_bus=bus,
            discussion_generation=2,
        ),
        room_id,
        7,
        discussion_generation=1,
    )
    try:
        await anext(stream)  # connection
        generation = await anext(stream)
        first = await anext(stream)
        await anext(stream)  # playback
        heartbeat = await anext(stream)

        assert event_data(generation)["discussion_generation"] == 2
        assert first.startswith("id: 2:1\n")
        assert event_data(first)["content"] != stale.content
        assert heartbeat == ": heartbeat\n\n"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_playback_state_recovers_durable_messages_through_its_high_water() -> None:
    room_id = uuid4()
    message = stream_message(room_id, 1)
    initial_backlog_read = False

    async def list_message_page(
        _room_id: UUID,
        *,
        expected_generation: int | None,
        after_sequence: int,
        limit: int,
    ) -> DiscussionPage:
        nonlocal initial_backlog_read
        if not initial_backlog_read:
            initial_backlog_read = True
            page: list[RoomMessage] = []
        else:
            page = ([message] if message.sequence > after_sequence else [])[:limit]
        return DiscussionPage(
            discussion_generation=1,
            messages=page,
            next_cursor=page[-1].sequence if len(page) == limit else None,
            reset_required=expected_generation not in {None, 1},
        )

    advanced_playback = RoomPlaybackState(room_id=room_id, current_message_sequence=1)
    bus = FakeRoomEventBus(
        [
            {
                "stream_id": "5-0",
                "kind": "playback_state",
                "sequence_number": 1,
                "discussion_generation": 1,
                "data": advanced_playback.model_dump(mode="json"),
            },
            {
                "stream_id": "6-0",
                "kind": "room_message",
                "sequence_number": 1,
                "discussion_generation": 1,
                "data": message.model_dump(mode="json"),
            },
        ]
    )
    services = stream_services(room_id, event_bus=bus)
    services.room_repository.list_message_page.side_effect = list_message_page
    stream = race_room_stream(ConnectedRequest(), services, room_id, 0)
    try:
        await anext(stream)  # connection
        await anext(stream)  # discussion generation
        await anext(stream)  # initial playback
        recovered_message = await anext(stream)
        playback_state = await anext(stream)
        next_frame = await anext(stream)

        assert recovered_message.startswith("id: 1:1\n")
        assert event_data(recovered_message)["sequence"] == 1
        assert "event: playback_state" in playback_state
        assert "id:" not in playback_state
        assert event_data(playback_state)["current_message_sequence"] == 1
        assert next_frame == ": heartbeat\n\n"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_live_room_catch_up_yields_after_one_bounded_page() -> None:
    room_id = uuid4()
    calls: list[int] = []
    services = stream_services(room_id)
    services.settings.room_stream_backlog_limit = 2

    async def list_message_page(
        _room_id: UUID,
        *,
        expected_generation: int | None,
        after_sequence: int,
        limit: int,
    ) -> DiscussionPage:
        calls.append(after_sequence)
        if len(calls) == 1:
            page: list[RoomMessage] = []
        else:
            page = [
                stream_message(room_id, sequence)
                for sequence in range(after_sequence + 1, after_sequence + limit + 1)
            ]
        return DiscussionPage(
            discussion_generation=1,
            messages=page,
            next_cursor=page[-1].sequence if len(page) == limit else None,
            reset_required=expected_generation not in {None, 1},
        )

    services.room_repository.list_message_page.side_effect = list_message_page
    stream = race_room_stream(ConnectedRequest(), services, room_id, 0)
    try:
        await anext(stream)  # connection
        await anext(stream)  # discussion generation
        await anext(stream)  # playback
        recovered_one = await anext(stream)
        recovered_two = await anext(stream)
        yielded_frame = await anext(stream)

        assert recovered_one.startswith("id: 1:1\n")
        assert recovered_two.startswith("id: 1:2\n")
        assert yielded_frame == ": heartbeat\n\n"
        assert calls == [0, 0]
    finally:
        await stream.aclose()
