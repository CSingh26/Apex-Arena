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
