# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
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
) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            room_stream_backlog_limit=250,
            sse_heartbeat_seconds=3,
        ),
        room_repository=SimpleNamespace(
            list_messages=AsyncMock(return_value=messages or []),
            get_playback=AsyncMock(return_value=RoomPlaybackState(room_id=room_id)),
        ),
        event_bus=event_bus or FakeRoomEventBus(),
    )


def event_data(chunk: str) -> dict[str, Any]:
    line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


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
    first = await anext(stream)
    second = await anext(stream)
    playback = await anext(stream)
    await stream.aclose()

    assert "event: connection_status" in connected
    assert event_data(connected) == {"status": "connected"}
    assert "event: room_message" in first and "id: 6" in first
    assert "event: room_message" in second and "id: 7" in second
    assert "event: playback_state" in playback
    assert bus.latest_calls == [str(room_id)]
    services.room_repository.list_messages.assert_awaited_once_with(
        room_id,
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
                "data": duplicate.model_dump(mode="json"),
            },
            {
                "stream_id": "6-0",
                "kind": "room_message",
                "sequence_number": 7,
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
    await anext(stream)  # persisted sequence 6
    await anext(stream)  # playback
    live = await anext(stream)
    await stream.aclose()

    assert "id: 7" in live
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
    ):
        recovered.append(after_sequence)
        yield _sse("connection_status", {"status": "connected"})

    monkeypatch.setattr(room_routes, "race_room_stream", capture_stream)
    response = await room_routes.stream_race_room(
        room.slug,
        SimpleNamespace(),  # type: ignore[arg-type]
        services,  # type: ignore[arg-type]
        after_sequence=4,
        last_event_id="9",
    )

    chunk = await anext(response.body_iterator)

    assert "connection_status" in chunk
    assert recovered == [9]


def test_room_sse_payload_is_compact_parseable_and_optionally_identified() -> None:
    chunk = _sse("room_status", {"status": "replaying"}, "14")

    assert chunk.startswith("id: 14\nevent: room_status\n")
    assert chunk.endswith("\n\n")
    assert event_data(chunk) == {"status": "replaying"}
