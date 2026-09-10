# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.domain.models import NormalizedRaceEvent
from app.domain.rooms import RoomMessage
from app.services.race_state import RaceState, RaceStateEngine

logger = logging.getLogger(__name__)


class RedisStore:
    def __init__(
        self,
        redis_url: str,
        *,
        socket_timeout: int = 5,
        connect_timeout: int = 5,
        health_check_interval: int = 30,
    ) -> None:
        # rediss:// URLs negotiate TLS automatically. The health-check interval
        # revives connections that a managed provider closed while idle.
        self.client: Redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=connect_timeout,
            health_check_interval=health_check_interval,
            retry_on_timeout=True,
        )

    async def health_check(self, timeout_seconds: float = 2.0) -> tuple[bool, str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                healthy = await self.client.ping()
            return bool(healthy), "connected" if healthy else "unavailable"
        except Exception as exc:
            return False, f"unavailable ({type(exc).__name__})"

    async def close(self) -> None:
        await self.client.aclose()


class RedisPublishError(RuntimeError):
    pass


class EventBus:
    """Redis Streams transport for normalized events, race state, and live status."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._diagnostics: dict[str, dict[str, Any]] = {}
        self._clients: dict[str, int] = {}

    def diagnostics(self, session_key: str) -> dict[str, Any]:
        key = self._safe_key(session_key)
        return {
            "last_successful_event_publish_at": None,
            "last_successful_state_publish_at": None,
            "last_error": None,
            **self._diagnostics.get(key, {}),
            "active_session_sse_clients": self._clients.get(key, 0),
        }

    def session_client_connected(self, session_key: str) -> None:
        key = self._safe_key(session_key)
        self._clients[key] = self._clients.get(key, 0) + 1

    def session_client_disconnected(self, session_key: str) -> None:
        key = self._safe_key(session_key)
        count = self._clients.get(key, 0) - 1
        if count > 0:
            self._clients[key] = count
        else:
            self._clients.pop(key, None)

    async def latest_state(self, session_key: str) -> RaceState | None:
        records = await self.redis.xrevrange(self.state_stream(session_key), count=1)
        if not records:
            return None
        state = RaceState.model_validate_json(records[0][1]["data"])
        return state if state.session_key == session_key else None

    async def publish_event(self, event: NormalizedRaceEvent) -> str:
        return await self._publish(
            self.event_stream(event.session_key),
            {
                "kind": "event",
                "sequence_number": str(event.sequence_number),
                "data": event.model_dump_json(),
            },
            maxlen=2000,
        )

    async def publish_state(self, state: RaceState) -> str:
        return await self._publish(
            self.state_stream(state.session_key),
            {
                "kind": "state",
                "sequence_number": str(state.sequence_number),
                "data": state.model_dump_json(),
            },
            maxlen=500,
        )

    async def publish_connection_status(self, status: dict[str, Any]) -> str:
        return await self._publish(
            "apex:live:status",
            {
                "kind": "connection_status",
                "data": json.dumps(status, sort_keys=True, default=str),
            },
            maxlen=200,
        )

    async def latest_connection_status(self) -> dict[str, Any] | None:
        records = await self.redis.xrevrange("apex:live:status", count=1)
        if not records:
            return None
        _, values = records[0]
        payload = json.loads(values["data"])
        return payload if isinstance(payload, dict) else None

    async def publish_room_message(self, message: RoomMessage) -> str:
        return await self._publish(
            self.room_stream(str(message.room_id)),
            {
                "kind": "room_message",
                "sequence_number": str(message.sequence),
                "data": message.model_dump_json(),
            },
            maxlen=5000,
        )

    async def publish_room_state(self, room_id: str, state: dict[str, Any]) -> str:
        return await self._publish(
            self.room_stream(room_id),
            {
                "kind": "playback_state",
                "sequence_number": str(state.get("current_message_sequence") or 0),
                "data": json.dumps(state, default=str),
            },
            maxlen=5000,
        )

    async def publish_room_status(self, room_id: str, status: dict[str, Any]) -> str:
        return await self._publish(
            self.room_stream(room_id),
            {"kind": "room_status", "data": json.dumps(status, default=str)},
            maxlen=5000,
        )

    async def read_room_stream(
        self,
        room_id: str,
        after_id: str,
        *,
        count: int = 100,
        block_ms: int = 10000,
    ) -> list[dict[str, Any]]:
        response = await self.redis.xread(
            {self.room_stream(room_id): after_id}, count=count, block=block_ms
        )
        return self._decode_streams(response)

    async def latest_room_stream_id(self, room_id: str) -> str:
        records = await self.redis.xrevrange(self.room_stream(room_id), count=1)
        return str(records[0][0]) if records else "0-0"

    async def read_events(
        self, session_key: str, after_id: str = "0-0", count: int = 100
    ) -> list[dict[str, Any]]:
        streams = await self.redis.xread(
            {self.event_stream(session_key): after_id},
            count=count,
        )
        return self._decode_streams(streams)

    async def read_session_streams(
        self,
        session_key: str,
        last_ids: dict[str, str] | None = None,
        *,
        count: int = 100,
        block_ms: int = 10000,
    ) -> list[dict[str, Any]]:
        streams = {
            self.event_stream(session_key): "$",
            self.state_stream(session_key): "$",
            "apex:live:status": "$",
        }
        if last_ids:
            streams.update(last_ids)
        response = await self.redis.xread(streams, count=count, block=block_ms)
        return self._decode_streams(response)

    @classmethod
    def event_stream(cls, session_key: str) -> str:
        return f"apex:events:{cls._safe_key(session_key)}"

    @classmethod
    def state_stream(cls, session_key: str) -> str:
        return f"apex:state:{cls._safe_key(session_key)}"

    @classmethod
    def room_stream(cls, room_id: str) -> str:
        return f"apex:rooms:{cls._safe_key(room_id)}"

    async def _publish(self, stream: str, values: dict[str, str], maxlen: int) -> str:
        kind = values.get("kind")
        key = stream.rsplit(":", 1)[-1]
        try:
            result = await self.redis.xadd(stream, values, maxlen=maxlen, approximate=True)
            if kind in {"event", "state"}:
                if len(self._diagnostics) > 256:
                    self._diagnostics.pop(next(iter(self._diagnostics)))
                details = self._diagnostics.setdefault(key, {})
                details[f"last_successful_{kind}_publish_at"] = datetime.now(UTC).isoformat()
                details["last_error"] = None
            return result
        except Exception as exc:
            if kind in {"event", "state"}:
                self._diagnostics.setdefault(key, {})["last_error"] = {
                    "type": type(exc).__name__,
                    "at": datetime.now(UTC).isoformat(),
                }
            logger.error("Redis publish failed stream=%s error=%s", stream, type(exc).__name__)
            raise RedisPublishError(f"Redis publish failed ({type(exc).__name__})") from exc

    @staticmethod
    def _decode_streams(streams: list[object]) -> list[dict[str, Any]]:
        decoded: list[dict[str, Any]] = []
        for stream_name, messages in streams:  # type: ignore[misc]
            for stream_id, values in messages:
                decoded.append(
                    {
                        "stream": stream_name,
                        "stream_id": stream_id,
                        "kind": values.get("kind", "event"),
                        "sequence_number": int(values.get("sequence_number", 0)),
                        "data": json.loads(values["data"]),
                    }
                )
        return decoded

    @staticmethod
    def _safe_key(session_key: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", session_key)[:100]


class RaceEventRedisPublisher:
    def __init__(self, event_bus: EventBus, state_engine: RaceStateEngine) -> None:
        self.event_bus = event_bus
        self.state_engine = state_engine

    async def consume(self, event: NormalizedRaceEvent) -> None:
        await self.event_bus.publish_event(event)
        state = await self.state_engine.get_state(event.session_key)
        await self.event_bus.publish_state(state)
