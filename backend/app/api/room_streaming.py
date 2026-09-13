# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request

from app.domain.rooms import MAX_DISCUSSION_GENERATION
from app.services.container import AppServices

logger = logging.getLogger(__name__)


def discussion_event_id(generation: int, sequence: int) -> str:
    return f"{generation}:{sequence}"


def parse_discussion_event_id(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    generation, separator, sequence = value.partition(":")
    if not separator or not generation.isdigit() or not sequence.isdigit():
        return None
    parsed_generation, parsed_sequence = int(generation), int(sequence)
    if parsed_generation < 1 or parsed_generation > MAX_DISCUSSION_GENERATION:
        return None
    return parsed_generation, parsed_sequence


async def race_room_stream(
    request: Request,
    services: AppServices,
    room_id: UUID,
    after_sequence: int,
    session_key: str | None = None,
    discussion_generation: int | None = None,
) -> AsyncIterator[str]:
    cursor = after_sequence
    cursor_generation = discussion_generation
    generation_announced = False

    async def catch_up(bound: int | None = None, *, max_messages: int | None = None):
        nonlocal cursor, cursor_generation, generation_announced
        page_limit = max(1, services.settings.room_stream_backlog_limit)
        remaining_messages = max_messages
        while not await request.is_disconnected():
            query_limit = (
                page_limit if remaining_messages is None else min(page_limit, remaining_messages)
            )
            page = await services.room_repository.list_message_page(
                room_id,
                expected_generation=cursor_generation,
                after_sequence=cursor,
                limit=query_limit,
            )
            if cursor_generation != page.discussion_generation:
                previous_generation = cursor_generation
                cursor_generation = page.discussion_generation
                cursor = 0
                generation_announced = True
                yield _sse(
                    "discussion_generation",
                    {
                        "room_id": str(room_id),
                        "discussion_generation": cursor_generation,
                        "reset_required": previous_generation is not None,
                    },
                    discussion_event_id(cursor_generation, 0),
                )
            elif not generation_announced:
                generation_announced = True
                yield _sse(
                    "discussion_generation",
                    {
                        "room_id": str(room_id),
                        "discussion_generation": cursor_generation,
                        "reset_required": False,
                    },
                    discussion_event_id(cursor_generation, cursor),
                )
            previous = cursor
            emitted = 0
            for message in page.messages:
                if (
                    message.discussion_generation != cursor_generation
                    or message.sequence <= cursor
                    or (bound is not None and message.sequence > bound)
                ):
                    continue
                cursor = message.sequence
                emitted += 1
                yield _sse(
                    "room_message",
                    message.model_dump(mode="json"),
                    discussion_event_id(cursor_generation, cursor),
                )
            if remaining_messages is not None:
                remaining_messages -= emitted
                if remaining_messages <= 0:
                    break
            if cursor == previous or len(page.messages) < query_limit:
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

    playback = await services.room_replay.with_session_clock(
        session_key, await services.room_repository.get_playback(room_id)
    )
    if playback.discussion_generation == cursor_generation:
        yield _sse("playback_state", playback.model_dump(mode="json"))
    while not await request.is_disconnected():
        try:
            records = await services.event_bus.read_room_stream(
                str(room_id),
                redis_id,
                count=100,
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

        # PostgreSQL is authoritative. One reconciliation per Redis batch makes
        # a lost reset notification recoverable without trusting stream history.
        async for frame in catch_up(
            max_messages=max(1, services.settings.room_stream_backlog_limit)
        ):
            yield frame
        if not records:
            yield ": heartbeat\n\n"
            continue
        for record in records:
            redis_id = str(record["stream_id"])
            kind = str(record["kind"])
            if kind == "discussion_generation":
                continue
            sequence = int(record.get("sequence_number") or 0)
            record_generation_raw = record.get("discussion_generation")
            record_generation = (
                int(record_generation_raw) if record_generation_raw is not None else None
            )
            record_data = record.get("data")
            is_message = kind == "room_message"
            if is_message:
                if (
                    not isinstance(record_data, dict)
                    or record_generation != cursor_generation
                    or record_data.get("discussion_generation") != cursor_generation
                    or sequence <= cursor
                ):
                    continue
                if sequence > cursor + 1:
                    async for frame in catch_up(sequence - 1):
                        yield frame
                if record_generation != cursor_generation or sequence <= cursor:
                    continue
                cursor = sequence
            elif kind == "playback_state":
                if (
                    not isinstance(record_data, dict)
                    or record_generation != cursor_generation
                    or record_data.get("discussion_generation") != cursor_generation
                ):
                    continue
                message_high_water = int(record_data.get("current_message_sequence") or 0)
                if message_high_water > cursor:
                    async for frame in catch_up(message_high_water):
                        yield frame
                if record_generation != cursor_generation:
                    continue
            yield _sse(
                kind,
                record_data,
                discussion_event_id(cursor_generation, sequence)
                if is_message and sequence
                else None,
            )


def _sse(event: str, data: object, event_id: str | None = None) -> str:
    encoded = json.dumps(data, default=str, separators=(",", ":"))
    lines = [f"event: {event}", f"data: {encoded}"]
    if event_id:
        lines.insert(0, f"id: {event_id}")
    return "\n".join(lines) + "\n\n"
