# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.room_schemas import PlaybackRequest, ReplayRequest, RoomMessageFilters
from app.domain.rooms import MessageTopic, MessageType


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0, 4.0, 8.0])
def test_playback_schema_accepts_only_documented_safe_speeds(speed: float) -> None:
    payload = PlaybackRequest(action="set_speed", playback_speed=speed)  # type: ignore[arg-type]

    assert payload.playback_speed == speed


@pytest.mark.parametrize("speed", [0.25, 0.75, 3.0, 16.0])
def test_playback_schema_rejects_unsupported_speeds(speed: float) -> None:
    with pytest.raises(ValidationError):
        PlaybackRequest(action="set_speed", playback_speed=speed)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "set_speed"},
        {"action": "seek_to_sequence"},
        {"action": "seek_to_lap"},
        {"action": "seek_to_phase"},
        {"action": "seek_to_session_time"},
        {"action": "seek_to_sequence", "sequence": -1},
        {"action": "seek_to_lap", "lap_number": -1},
        {"action": "seek_to_session_time", "session_time": -0.1},
    ],
)
def test_playback_schema_requires_nonnegative_action_value(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlaybackRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "pause"},
        {"action": "resume"},
        {"action": "seek_to_sequence", "sequence": 0},
        {"action": "seek_to_lap", "lap_number": 0},
        {"action": "seek_to_phase", "phase": "SQ3"},
        {"action": "seek_to_session_time", "session_time": 0},
    ],
)
def test_playback_schema_accepts_each_control_action(payload: dict[str, object]) -> None:
    assert PlaybackRequest.model_validate(payload).action == payload["action"]


def test_replay_schema_defaults_to_start_and_supports_explicit_restart() -> None:
    assert ReplayRequest().action == "start"
    assert ReplayRequest(action="restart").action == "restart"
    assert ReplayRequest(action="resume").action == "resume"

    with pytest.raises(ValidationError):
        ReplayRequest(action="delete")  # type: ignore[arg-type]


def test_room_message_filters_parse_agent_topic_type_and_lap_window() -> None:
    filters = RoomMessageFilters(
        agent_id="mira-vale",
        topic=MessageTopic.STRATEGY,
        message_type=MessageType.ANALYSIS,
        lap_from=3,
        lap_to=9,
    )

    assert filters.model_dump(mode="json") == {
        "agent_id": "mira-vale",
        "topic": "strategy",
        "message_type": "analysis",
        "lap_from": 3,
        "lap_to": 9,
    }


@pytest.mark.parametrize("field", ["lap_from", "lap_to"])
def test_room_message_filters_reject_negative_laps(field: str) -> None:
    with pytest.raises(ValidationError):
        RoomMessageFilters.model_validate({field: -1})
