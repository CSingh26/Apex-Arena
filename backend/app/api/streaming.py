# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

from app.services.container import AppServices

logger = logging.getLogger(__name__)


async def session_event_stream(
    request: Request,
    services: AppServices,
    session_key: str,
    last_sequence_number: int,
) -> AsyncIterator[str]:
    bus = getattr(services, "event_bus", None)
    if bus is not None:
        bus.session_client_connected(session_key)
    try:
        async for frame in _session_event_stream(
            request, services, session_key, last_sequence_number
        ):
            yield frame
    finally:
        if bus is not None:
            bus.session_client_disconnected(session_key)


async def _session_event_stream(
    request: Request,
    services: AppServices,
    session_key: str,
    last_sequence_number: int,
) -> AsyncIterator[str]:
    """Replay missed persisted events, send current state, then tail Redis Streams."""

    cursor = last_sequence_number
    state = await services.race_state.get_state(session_key)
    state_cursor = state.sequence_number
    replay_bound = state_cursor if state.is_replay else None

    async def catch_up(bound: int | None):
        nonlocal cursor
        if bound is not None and cursor >= bound:
            return
        page_limit = max(1, services.settings.engine_recent_events_limit)
        remaining_events = None if bound is not None else page_limit
        while not await request.is_disconnected():
            query_limit = page_limit if remaining_events is None else remaining_events
            missed = await services.normalized_event_repository.list_for_session(
                session_key,
                after_sequence=cursor,
                limit=query_limit,
                before_sequence=bound,
            )
            previous = cursor
            emitted = 0
            for event in missed:
                if remaining_events is not None and emitted >= remaining_events:
                    break
                if event.sequence_number <= cursor or (
                    bound is not None and event.sequence_number > bound
                ):
                    continue
                cursor = event.sequence_number
                emitted += 1
                yield format_sse("event", event.model_dump(mode="json"), event_id=str(cursor))
            if remaining_events is not None:
                remaining_events -= emitted
                if remaining_events <= 0:
                    break
            if cursor == previous or len(missed) < query_limit:
                break
            if bound is not None and cursor >= bound:
                break

    async for frame in catch_up(state_cursor):
        yield frame

    yield format_sse("state", state.model_dump(mode="json"))

    event_stream = services.event_bus.event_stream(session_key)
    state_stream = services.event_bus.state_stream(session_key)
    last_ids = {
        event_stream: "0-0",
        state_stream: "0-0",
        "apex:live:status": "$",
    }
    block_ms = min(10_000, services.settings.sse_heartbeat_seconds * 1000)

    while not await request.is_disconnected():
        try:
            records = await services.event_bus.read_session_streams(
                session_key,
                last_ids,
                count=services.settings.engine_recent_events_limit,
                block_ms=block_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Session stream degraded session=%s error=%s",
                session_key,
                type(exc).__name__,
            )
            yield format_sse(
                "stream_status",
                {"status": "degraded", "detail": "Redis stream temporarily unavailable"},
            )
            async for frame in catch_up(replay_bound):
                yield frame
            await asyncio.sleep(1)
            continue

        if not records:
            async for frame in catch_up(replay_bound):
                yield frame
            yield ": heartbeat\n\n"
            continue

        for record in records:
            stream_name = str(record["stream"])
            last_ids[stream_name] = str(record["stream_id"])
            kind = str(record["kind"])
            sequence_number = int(record.get("sequence_number") or 0)
            if kind == "event":
                if sequence_number <= cursor:
                    continue
                if replay_bound is not None and sequence_number > state_cursor:
                    continue
                if sequence_number > cursor + 1:
                    async for frame in catch_up(sequence_number - 1):
                        yield frame
                cursor = sequence_number
            elif kind == "state":
                if sequence_number <= state_cursor:
                    continue
                state_cursor = sequence_number
                if record["data"].get("is_replay"):
                    replay_bound = state_cursor
                    async for frame in catch_up(replay_bound):
                        yield frame
                else:
                    replay_bound = None
            yield format_sse(
                kind,
                record["data"],
                event_id=str(sequence_number) if kind == "event" and sequence_number else None,
            )


def format_sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.extend(
        (
            f"event: {event}",
            f"data: {json.dumps(data, separators=(',', ':'), default=str)}",
        )
    )
    return "\n".join(lines) + "\n\n"
