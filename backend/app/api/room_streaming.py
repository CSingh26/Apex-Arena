# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request

from app.services.container import AppServices

logger = logging.getLogger(__name__)


async def race_room_stream(
    request: Request,
    services: AppServices,
    room_id: UUID,
    after_sequence: int,
) -> AsyncIterator[str]:
    cursor = after_sequence
    try:
        redis_id = await services.event_bus.latest_room_stream_id(str(room_id))
    except Exception as exc:
        logger.error("Race room stream cursor unavailable error=%s", type(exc).__name__)
        redis_id = "$"
    yield _sse("connection_status", {"status": "connected"})
    messages = await services.room_repository.list_messages(
        room_id,
        after_sequence=cursor,
        limit=services.settings.room_stream_backlog_limit,
    )
    for message in messages:
        cursor = max(cursor, message.sequence)
        yield _sse("room_message", message.model_dump(mode="json"), str(message.sequence))

    playback = await services.room_repository.get_playback(room_id)
    yield _sse("playback_state", playback.model_dump(mode="json"))
    while not await request.is_disconnected():
        try:
            records = await services.event_bus.read_room_stream(
                str(room_id),
                redis_id,
                count=100,
                block_ms=services.settings.sse_heartbeat_seconds * 1000,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Race room stream degraded error=%s", type(exc).__name__)
            yield _sse("connection_status", {"status": "degraded"})
            await asyncio.sleep(1)
            continue
        if not records:
            yield ": heartbeat\n\n"
            continue
        for record in records:
            redis_id = str(record["stream_id"])
            sequence = int(record.get("sequence_number") or 0)
            if record["kind"] == "room_message" and sequence <= cursor:
                continue
            cursor = max(cursor, sequence)
            yield _sse(
                str(record["kind"]),
                record["data"],
                str(sequence) if sequence else None,
            )


def _sse(event: str, data: object, event_id: str | None = None) -> str:
    encoded = json.dumps(data, default=str, separators=(",", ":"))
    lines = [f"event: {event}", f"data: {encoded}"]
    if event_id:
        lines.insert(0, f"id: {event_id}")
    return "\n".join(lines) + "\n\n"
