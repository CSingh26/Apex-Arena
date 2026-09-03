# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import stream_session
from app.api.streaming import format_sse, session_event_stream
from app.core.settings import Settings
from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.race_state import RaceState


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class FakeEventBus:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []

    @staticmethod
    def event_stream(session_key: str) -> str:
        return f"events:{session_key}"

    @staticmethod
    def state_stream(session_key: str) -> str:
        return f"states:{session_key}"

    async def read_session_streams(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        records, self.records = self.records, []
        return records


@pytest.mark.asyncio
async def test_stream_replays_missed_events_then_sends_current_state(settings: Settings) -> None:
    event = NormalizedRaceEvent(
        session_key="spa-race",
        source="openf1_historical",
        event_time=datetime(2026, 7, 19, 13, tzinfo=UTC),
        received_at=datetime(2026, 7, 19, 13, tzinfo=UTC),
        sequence_number=8,
        event_type=RaceEventType.LAP_COMPLETED,
        dedup_key="stream-dedup",
        is_replay=True,
    )

    async def list_events(*_: Any, **__: Any) -> list[NormalizedRaceEvent]:
        return [event]

    async def current_state(_: str) -> RaceState:
        return RaceState(session_key="spa-race", sequence_number=8, is_replay=True)

    runtime = SimpleNamespace(
        settings=settings,
        normalized_event_repository=SimpleNamespace(list_for_session=list_events),
        race_state=SimpleNamespace(get_state=current_state),
    )
    stream = session_event_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        runtime,  # type: ignore[arg-type]
        "spa-race",
        7,
    )

    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    assert "event: event" in first
    assert "id: 8" in first
    assert "event: state" in second
    assert '"sequence_number":8' in second


@pytest.mark.asyncio
async def test_replay_stream_bounds_persisted_backlog_to_applied_state(
    settings: Settings,
) -> None:
    future_event = NormalizedRaceEvent(
        session_key="spa-race",
        source="openf1_historical",
        event_time=datetime(2026, 7, 19, 13, tzinfo=UTC),
        received_at=datetime(2026, 7, 19, 13, tzinfo=UTC),
        sequence_number=9,
        event_type=RaceEventType.LAP_COMPLETED,
        dedup_key="stream-future-dedup",
        is_replay=True,
    )

    async def list_events(
        _session_key: str,
        *,
        after_sequence: int = 0,
        before_sequence: int | None = None,
        **__: Any,
    ) -> list[NormalizedRaceEvent]:
        return [
            event
            for event in [future_event]
            if event.sequence_number > after_sequence
            and (before_sequence is None or event.sequence_number <= before_sequence)
        ]

    async def current_state(_: str) -> RaceState:
        return RaceState(session_key="spa-race", sequence_number=8, is_replay=True)

    runtime = SimpleNamespace(
        settings=settings,
        normalized_event_repository=SimpleNamespace(list_for_session=list_events),
        race_state=SimpleNamespace(get_state=current_state),
    )
    stream = session_event_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        runtime,  # type: ignore[arg-type]
        "spa-race",
        0,
    )

    first = await anext(stream)
    await stream.aclose()

    assert "event: state" in first
    assert '"sequence_number":8' in first
    assert '"sequence_number":9' not in first


@pytest.mark.asyncio
async def test_replay_stream_does_not_emit_future_redis_events(settings: Settings) -> None:
    future_event = NormalizedRaceEvent(
        session_key="spa-race",
        source="openf1_historical",
        event_time=datetime(2026, 7, 19, 13, tzinfo=UTC),
        received_at=datetime(2026, 7, 19, 13, tzinfo=UTC),
        sequence_number=9,
        event_type=RaceEventType.LAP_COMPLETED,
        dedup_key="stream-redis-future-dedup",
        is_replay=True,
    )

    async def list_events(*_: Any, **__: Any) -> list[NormalizedRaceEvent]:
        return []

    async def current_state(_: str) -> RaceState:
        return RaceState(session_key="spa-race", sequence_number=8, is_replay=True)

    runtime = SimpleNamespace(
        settings=settings,
        normalized_event_repository=SimpleNamespace(list_for_session=list_events),
        race_state=SimpleNamespace(get_state=current_state),
        event_bus=FakeEventBus(
            [
                {
                    "stream": "events:spa-race",
                    "stream_id": "1-0",
                    "kind": "event",
                    "sequence_number": 9,
                    "data": future_event.model_dump(mode="json"),
                }
            ]
        ),
    )
    stream = session_event_stream(
        ConnectedRequest(),  # type: ignore[arg-type]
        runtime,  # type: ignore[arg-type]
        "spa-race",
        0,
    )

    state = await anext(stream)
    live = await anext(stream)
    await stream.aclose()

    assert "event: state" in state
    assert live == ": heartbeat\n\n"


def test_sse_format_is_compact_and_parseable() -> None:
    message = format_sse("connection_status", {"status": "CONNECTED"})
    data_line = next(line for line in message.splitlines() if line.startswith("data: "))

    assert message.endswith("\n\n")
    assert json.loads(data_line.removeprefix("data: ")) == {"status": "CONNECTED"}


@pytest.mark.asyncio
async def test_session_stream_prefers_numeric_last_event_id_for_reconnect(monkeypatch) -> None:
    recovered_sequences: list[int] = []

    async def fake_stream(
        _request: Any,
        _services: Any,
        _session_key: str,
        recovered_sequence: int,
    ):
        recovered_sequences.append(recovered_sequence)
        yield ": heartbeat\n\n"

    monkeypatch.setattr("app.api.routes.session_event_stream", fake_stream)
    response = await stream_session(
        "spa-race",
        ConnectedRequest(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        last_sequence_number=4,
        last_event_id="9",
    )

    await anext(response.body_iterator)

    assert recovered_sequences == [9]
