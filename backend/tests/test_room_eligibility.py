# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

from app.domain.rooms import (
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SourceAvailability,
)
from app.services.room_eligibility import RoomEligibilityService

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def existing_room(**updates: object) -> RaceRoom:
    values: dict[str, object] = {
        "slug": "2026-belgian-grand-prix-race",
        "season": 2026,
        "round_number": 13,
        "race_name": "Belgian Grand Prix",
        "official_name": "Belgian Grand Prix",
        "circuit_name": "Circuit de Spa-Francorchamps",
        "country": "Belgium",
        "scheduled_start": datetime(2026, 7, 19, 13, tzinfo=UTC),
        "status": RoomStatus.PENDING,
        "mode": RoomMode.REPLAY,
        "source_availability": SourceAvailability.UNAVAILABLE,
    }
    values.update(updates)
    return RaceRoom.model_validate(values)


def test_stale_future_row_never_overrides_read_only_calendar_policy() -> None:
    room = existing_room()
    result = RoomEligibilityService().evaluate(
        scheduled_start=room.scheduled_start,
        actual_status=room.status,
        provider_session_available=False,
        existing_room=room,
        now=NOW,
    )

    assert result.status is RoomEligibilityStatus.FUTURE_READ_ONLY
    assert result.can_create is False
    assert result.can_open is False
    assert result.can_replay is False


def test_completed_provider_session_is_eligible_for_historical_room() -> None:
    result = RoomEligibilityService().evaluate(
        scheduled_start=datetime(2026, 7, 17, 13, tzinfo=UTC),
        actual_status="completed",
        provider_session_available=True,
        data_availability=SourceAvailability.LIMITED,
        replay_available=True,
        results_available=True,
        now=NOW,
    )

    assert result.status is RoomEligibilityStatus.ELIGIBLE_HISTORICAL
    assert result.can_create is True
    assert result.can_open is True
    assert result.can_replay is True


def test_started_session_without_provider_data_remains_pending() -> None:
    result = RoomEligibilityService().evaluate(
        scheduled_start=datetime(2026, 7, 18, 11, tzinfo=UTC),
        actual_status="live",
        provider_session_available=False,
        now=NOW,
    )

    assert result.status is RoomEligibilityStatus.PROVIDER_PENDING
    assert result.can_create is False
    assert result.can_open is False
