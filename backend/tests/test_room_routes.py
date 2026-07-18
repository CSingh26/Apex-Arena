# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.room_routes import (
    change_playback,
    list_event_weekends,
    list_race_rooms,
    message_evidence,
    race_room_detail,
    room_diagnostics,
    room_messages,
    start_replay,
)
from app.api.room_schemas import PlaybackRequest, ReplayRequest
from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RaceRoom,
    RoomMessage,
    RoomMode,
    RoomPlaybackState,
    RoomStatus,
    SessionType,
    SourceAvailability,
    WeekendStatus,
)
from app.services.discussion import DiscussionMetrics
from app.services.race_state import RaceState
from app.services.room_replay import ReplayUnavailableError


def api_room(
    *,
    source_availability: SourceAvailability = SourceAvailability.TELEMETRY,
    mode: RoomMode = RoomMode.DEVELOPMENT,
) -> RaceRoom:
    return RaceRoom(
        slug="day3-validation-room",
        session_key="day3-session",
        season=2026,
        round_number=99,
        race_name="Day 3 Validation Race",
        official_name="Apex Arena Day 3 Validation Race",
        circuit_name="Apex Validation Circuit",
        country="Development",
        scheduled_start=datetime(2026, 7, 17, 12, tzinfo=UTC),
        status=RoomStatus.READY,
        mode=mode,
        total_laps=12,
        source_availability=source_availability,
        is_development=True,
    )


def api_message(room_id: UUID, sequence: int, **updates: object) -> RoomMessage:
    values: dict[str, object] = {
        "room_id": room_id,
        "agent_id": "mira-vale",
        "sequence": sequence,
        "lap_number": 4,
        "topic": MessageTopic.STRATEGY,
        "message_type": MessageType.ANALYSIS,
        "content": "The pit stop is confirmed; its outcome remains uncertain.",
        "confidence": Confidence.MEDIUM,
        "evidence_status": EvidenceStatus.GROUNDED,
        "generated_by": "deterministic",
    }
    values.update(updates)
    return RoomMessage.model_validate(values)


def route_services(room: RaceRoom) -> SimpleNamespace:
    playback = RoomPlaybackState(room_id=room.id)
    room_repository = SimpleNamespace(
        get_room=AsyncMock(return_value=room),
        get_agents=AsyncMock(return_value=[]),
        get_playback=AsyncMock(return_value=playback),
        list_rooms=AsyncMock(return_value=([room], 1)),
        list_messages=AsyncMock(return_value=[]),
        get_message=AsyncMock(return_value=None),
        message_evidence=AsyncMock(return_value=[]),
    )
    return SimpleNamespace(
        rooms=SimpleNamespace(ensure_catalog=AsyncMock()),
        room_repository=room_repository,
        room_replay=SimpleNamespace(
            start=AsyncMock(return_value=playback),
            pause=AsyncMock(return_value=playback),
            resume=AsyncMock(return_value=playback),
            set_speed=AsyncMock(return_value=playback),
            seek_to_lap=AsyncMock(return_value=playback),
            seek_to_phase=AsyncMock(return_value=playback),
            seek_to_sequence=AsyncMock(return_value=playback),
            seek_to_session_time=AsyncMock(return_value=playback),
        ),
        settings=SimpleNamespace(
            app_env="test",
            room_diagnostics_enabled=True,
        ),
    )


@pytest.mark.asyncio
async def test_room_catalog_forwards_mode_search_sort_and_pagination() -> None:
    room = api_room().model_copy(update={"is_development": False})
    services = route_services(room)

    response = await list_race_rooms(
        services,
        season=2026,
        room_status=RoomStatus.READY,
        mode=RoomMode.DEVELOPMENT,
        search="validation",
        sort="latest_activity",
        limit=12,
        offset=3,
    )

    assert response.total == 1
    assert response.rooms == [room]
    services.room_repository.list_rooms.assert_awaited_once_with(
        season=2026,
        status=RoomStatus.READY,
        mode=RoomMode.DEVELOPMENT,
        search="validation",
        sort="latest_activity",
        limit=12,
        offset=3,
    )


@pytest.mark.asyncio
async def test_public_room_catalog_defensively_excludes_validation_fixture() -> None:
    room = api_room()
    services = route_services(room)

    response = await list_race_rooms(
        services,
        season=2026,
        room_status=None,
        mode=None,
        search=None,
        sort="race_date_desc",
        limit=20,
        offset=0,
    )

    assert response.rooms == []
    assert response.total == 0


@pytest.mark.asyncio
async def test_grouped_event_catalog_forwards_authoritative_filters() -> None:
    room = api_room()
    services = route_services(room)
    services.rooms.grouped_events = AsyncMock(return_value=([], 0))

    response = await list_event_weekends(
        services,
        season=2026,
        event_status=WeekendStatus.COMPLETED,
        session_type=SessionType.SPRINT,
        is_sprint_weekend=True,
        search="Spa",
        limit=12,
        offset=3,
    )

    assert response.events == []
    assert response.total == 0
    services.rooms.grouped_events.assert_awaited_once_with(
        season=2026,
        status=WeekendStatus.COMPLETED,
        session_type=SessionType.SPRINT,
        is_sprint_weekend=True,
        search="Spa",
        limit=12,
        offset=3,
    )


@pytest.mark.asyncio
async def test_public_detail_hides_development_fixture_outside_explicit_test_mode() -> None:
    room = api_room()
    services = route_services(room)
    services.settings.app_env = "staging"

    with pytest.raises(HTTPException) as error:
        await race_room_detail(room.slug, services)

    assert error.value.status_code == 404
    services.room_repository.get_agents.assert_not_awaited()


@pytest.mark.asyncio
async def test_future_placeholder_room_is_rejected_before_replay_starts() -> None:
    room = api_room(source_availability=SourceAvailability.UNAVAILABLE).model_copy(
        update={
            "slug": "2027-future-grand-prix-race",
            "is_development": False,
            "scheduled_start": datetime(2027, 7, 18, 12, tzinfo=UTC),
            "status": RoomStatus.PENDING,
            "mode": RoomMode.REPLAY,
            "session_key": None,
            "replay_available": False,
        }
    )
    services = route_services(room)

    with pytest.raises(HTTPException) as error:
        await start_replay(room.slug, services, ReplayRequest())

    assert error.value.status_code == 409
    assert "has not started" in str(error.value.detail)
    services.room_replay.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_detail_has_timing_only_notice_and_safe_diagnostics_flag() -> None:
    room = api_room(source_availability=SourceAvailability.TIMING_ONLY)
    services = route_services(room)

    response = await race_room_detail(room.slug, services)

    assert "Timing data" in response.data_notice
    assert "limited" in response.data_notice
    assert response.diagnostics_available is True


@pytest.mark.asyncio
async def test_messages_forward_all_filters_and_return_next_cursor_at_page_boundary() -> None:
    room = api_room()
    services = route_services(room)
    services.room_repository.list_messages.return_value = [
        api_message(room.id, 8),
        api_message(room.id, 9),
    ]

    response = await room_messages(
        room.slug,
        services,
        after_sequence=7,
        agent_id="mira-vale",
        topic=MessageTopic.STRATEGY,
        message_type=MessageType.ANALYSIS,
        lap_from=3,
        lap_to=9,
        sequence_from=8,
        sequence_to=12,
        limit=2,
    )

    assert [message.sequence for message in response.messages] == [8, 9]
    assert response.next_cursor == 9
    services.room_repository.list_messages.assert_awaited_once_with(
        room.id,
        after_sequence=7,
        agent_id="mira-vale",
        topic=MessageTopic.STRATEGY,
        message_type=MessageType.ANALYSIS,
        lap_from=3,
        lap_to=9,
        sequence_from=8,
        sequence_to=12,
        limit=2,
    )


@pytest.mark.asyncio
async def test_evidence_route_rejects_message_from_another_room() -> None:
    room = api_room()
    services = route_services(room)

    with pytest.raises(HTTPException) as error:
        await message_evidence(room.slug, uuid4(), services)

    assert error.value.status_code == 404
    services.room_repository.message_evidence.assert_not_awaited()


@pytest.mark.asyncio
async def test_evidence_route_returns_traceable_trigger_snapshot_quality_and_generation() -> None:
    room = api_room()
    services = route_services(room)
    snapshot_id = uuid4()
    message = api_message(room.id, 1, trigger_snapshot_id=snapshot_id)
    evidence = MessageEvidence(
        message_id=message.id,
        evidence_key="pit_duration",
        evidence_type="normalized_event",
        source_provider="fixture",
        source_reference="event-123",
        metric_name="pit_duration",
        metric_value=2.41,
        context={
            "event_sequence": 5,
            "lap_number": 4,
            "data_quality": "complete",
        },
    )
    services.room_repository.get_message.return_value = message
    services.room_repository.message_evidence.return_value = [evidence]

    response = await message_evidence(room.slug, message.id, services)

    assert response.trigger_event == {
        "event_id": "event-123",
        "event_sequence": 5,
        "lap_number": 4,
        "source_provider": "fixture",
    }
    assert response.snapshot_reference == str(snapshot_id)
    assert response.data_quality_flags == ["complete"]
    assert response.generation_mode == "deterministic"
    assert response.confidence == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "method", "kwargs"),
    [
        ("start", "start", {"restart": False}),
        ("restart", "start", {"restart": True}),
        ("resume", "resume", {}),
    ],
)
async def test_replay_route_dispatches_explicit_actions(
    action: str,
    method: str,
    kwargs: dict[str, object],
) -> None:
    room = api_room()
    services = route_services(room)

    response = await start_replay(
        room.slug,
        services,
        ReplayRequest.model_validate({"action": action}),
    )

    assert response.room == room
    replay_method = getattr(services.room_replay, method)
    if kwargs:
        replay_method.assert_awaited_once_with(room, **kwargs)
    else:
        replay_method.assert_awaited_once_with(room)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "method", "expected"),
    [
        ({"action": "pause"}, "pause", None),
        ({"action": "resume"}, "resume", None),
        ({"action": "set_speed", "playback_speed": 4}, "set_speed", 4),
        ({"action": "seek_to_lap", "lap_number": 8}, "seek_to_lap", 8),
        ({"action": "seek_to_phase", "phase": "Q2"}, "seek_to_phase", "Q2"),
        (
            {"action": "seek_to_session_time", "session_time": 932.5},
            "seek_to_session_time",
            932.5,
        ),
        (
            {"action": "seek_to_sequence", "sequence": 12},
            "seek_to_sequence",
            12,
        ),
    ],
)
async def test_playback_route_dispatches_each_control(
    payload: dict[str, object],
    method: str,
    expected: int | float | str | None,
) -> None:
    room = api_room()
    services = route_services(room)

    await change_playback(room.slug, PlaybackRequest.model_validate(payload), services)

    replay_method = getattr(services.room_replay, method)
    if expected is None:
        replay_method.assert_awaited_once_with(room)
    else:
        replay_method.assert_awaited_once_with(room, expected)


@pytest.mark.asyncio
async def test_playback_route_maps_unavailable_seek_to_conflict() -> None:
    room = api_room()
    services = route_services(room)
    services.room_replay.seek_to_lap.side_effect = ReplayUnavailableError(
        "Replay lap is outside the available event range"
    )

    with pytest.raises(HTTPException) as error:
        await change_playback(
            room.slug,
            PlaybackRequest(action="seek_to_lap", lap_number=99),
            services,
        )

    assert error.value.status_code == 409
    assert "outside" in error.value.detail


@pytest.mark.asyncio
async def test_diagnostics_are_hidden_in_production_when_debug_flag_is_off() -> None:
    services = route_services(api_room())
    services.settings.app_env = "production"
    services.settings.room_diagnostics_enabled = False

    with pytest.raises(HTTPException) as error:
        await room_diagnostics("day3-validation-room", services)

    assert error.value.status_code == 404
    services.rooms.ensure_catalog.assert_not_awaited()


@pytest.mark.asyncio
async def test_diagnostics_aggregate_counts_recent_events_state_and_metrics() -> None:
    room = api_room()
    services = route_services(room)
    timestamp = datetime(2026, 7, 17, 12, tzinfo=UTC)
    event = NormalizedRaceEvent(
        session_key="day3-session",
        source="fixture",
        event_time=timestamp,
        received_at=timestamp,
        sequence_number=14,
        event_type=RaceEventType.SESSION_FINISH,
        dedup_key="finish",
        is_replay=True,
    )
    services.raw_event_repository = SimpleNamespace(count=AsyncMock(return_value=14))
    services.normalized_event_repository = SimpleNamespace(
        count=AsyncMock(return_value=14),
        max_sequence=AsyncMock(return_value=14),
        list_for_session=AsyncMock(return_value=[event]),
    )
    services.snapshot_repository = SimpleNamespace(count=AsyncMock(return_value=2))
    services.ordering_buffer = SimpleNamespace(pending=lambda _: 0)
    services.race_state = SimpleNamespace(
        get_state=AsyncMock(
            return_value=RaceState(
                session_key="day3-session",
                status="finished",
                sequence_number=14,
                is_replay=True,
            )
        )
    )
    services.openf1_live = SimpleNamespace(status=lambda: {"connection_state": "DISCONNECTED"})
    services.room_discussion = SimpleNamespace(
        metrics=DiscussionMetrics(trigger_count=8, generated_message_count=14)
    )

    response = await room_diagnostics(room.slug, services)

    assert response.raw_event_count == 14
    assert response.normalized_event_count == 14
    assert response.snapshot_count == 2
    assert response.latest_event_sequence == 14
    assert response.latest_events[0]["event_type"] == "SESSION_FINISH"
    assert response.race_state["status"] == "finished"
    assert response.discussion["generated_message_count"] == 14
    services.normalized_event_repository.list_for_session.assert_awaited_once_with(
        "day3-session", after_sequence=0, limit=20
    )
