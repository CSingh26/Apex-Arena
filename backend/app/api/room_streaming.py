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
    session_key: str | None = None,
) -> AsyncIterator[str]:
    cursor = after_sequence

    async def catch_up(bound: int | None = None, *, max_messages: int | None = None):
        nonlocal cursor
        page_limit = max(1, services.settings.room_stream_backlog_limit)
        remaining_messages = max_messages
        while not await request.is_disconnected():
            query_limit = (
                page_limit if remaining_messages is None else min(page_limit, remaining_messages)
            )
            messages = await services.room_repository.list_messages(
                room_id, after_sequence=cursor, limit=query_limit
            )
            previous = cursor
            emitted = 0
            for message in messages:
                if message.sequence <= cursor or (bound is not None and message.sequence > bound):
                    continue
                cursor = message.sequence
                emitted += 1
                yield _sse("room_message", message.model_dump(mode="json"), str(cursor))
            if remaining_messages is not None:
                remaining_messages -= emitted
                if remaining_messages <= 0:
                    break
            if cursor == previous or len(messages) < query_limit:
                break
            if bound is not None and cursor >= bound:
                break

    try:
        redis_id = await services.event_bus.latest_room_stream_id(str(room_id))
    except Exception as exc:
        logger.error("Race room stream cursor unavailable error=%s", type(exc).__name__)
        redis_id = "$"
    yield _sse("connection_status", {"status": "connected"})
    async for frame in catch_up():
        yield frame

    # Enrich exactly as the replay coordinator does: this first frame otherwise
    # overwrites the clock the detail request already supplied, and the map
    # would lose its replay position on every reconnect.
    playback = await services.room_replay.with_session_clock(
        session_key, await services.room_repository.get_playback(room_id)
    )
    yield _sse("playback_state", playback.model_dump(mode="json"))
    while not await request.is_disconnected():
        try:
            records = await services.event_bus.read_room_stream(
                str(room_id),
                redis_id,
                count=100,
                # Cap the blocking read like the session stream does: it bounds how
                # long a managed Redis connection is held open per client.
                block_ms=min(10_000, services.settings.sse_heartbeat_seconds * 1000),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Race room stream degraded error=%s", type(exc).__name__)
            yield _sse("connection_status", {"status": "degraded"})
            async for frame in catch_up(
                max_messages=max(1, services.settings.room_stream_backlog_limit)
            ):
                yield frame
            await asyncio.sleep(1)
            continue
        if not records:
            async for frame in catch_up(
                max_messages=max(1, services.settings.room_stream_backlog_limit)
            ):
                yield frame
            yield ": heartbeat\n\n"
            continue
        for record in records:
            redis_id = str(record["stream_id"])
            sequence = int(record.get("sequence_number") or 0)
            is_message = record["kind"] == "room_message"
            if is_message:
                if sequence <= cursor:
                    continue
                if sequence > cursor + 1:
                    async for frame in catch_up(sequence - 1):
                        yield frame
                cursor = max(cursor, sequence)
            elif record["kind"] == "playback_state":
                message_high_water = int(record["data"].get("current_message_sequence") or 0)
                if message_high_water > cursor:
                    async for frame in catch_up(message_high_water):
                        yield frame
            yield _sse(
                str(record["kind"]),
                record["data"],
                str(sequence) if is_message and sequence else None,
            )


def _sse(event: str, data: object, event_id: str | None = None) -> str:
    encoded = json.dumps(data, default=str, separators=(",", ":"))
    lines = [f"event: {event}", f"data: {encoded}"]
    if event_id:
        lines.insert(0, f"id: {event_id}")
    return "\n".join(lines) + "\n\n"
