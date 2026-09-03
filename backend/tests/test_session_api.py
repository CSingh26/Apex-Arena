# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import (
    season_weekends,
    session_capabilities,
    session_detail,
    session_events,
    weekend_sessions,
)
from app.api.schemas import RaceEventCategory
from app.domain.models import EventImportance, EventOrigin, RaceEventType
from app.domain.rooms import (
    CapabilityStatus,
    EventWeekend,
    PublicSessionStatus,
    RoomEligibilityStatus,
    SessionCapabilities,
    SessionRoomSummary,
    SessionType,
    SourceAvailability,
    WeekendStatus,
)


def catalog() -> tuple[EventWeekend, SessionRoomSummary]:
    session = SessionRoomSummary(
        session_type=SessionType.RACE,
        display_name="Race",
        scheduled_start=datetime(2026, 7, 19, 13, tzinfo=UTC),
        status=PublicSessionStatus.COMPLETED,
        eligibility=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
        data_availability=SourceAvailability.TIMING_ONLY,
        room_slug="2026-belgian-grand-prix-race",
        room_eligible=True,
    )
    return (
        EventWeekend(
            event_id=uuid4(),
            event_slug="2026-belgian-grand-prix",
            season=2026,
            round=13,
            event_name="Belgian Grand Prix",
            circuit_name="Circuit de Spa-Francorchamps",
            country="Belgium",
            weekend_start=datetime(2026, 7, 17, 10, tzinfo=UTC),
            weekend_end=datetime(2026, 7, 19, 17, tzinfo=UTC),
            weekend_status=WeekendStatus.COMPLETED,
            is_sprint_weekend=False,
            sessions=[session],
        ),
        session,
    )


class FakeRooms:
    def __init__(self, event: EventWeekend, session: SessionRoomSummary) -> None:
        self.event = event
        self.session = session

    async def grouped_events(self, **_: object) -> tuple[list[EventWeekend], int]:
        return [self.event], 1

    async def event_weekend(self, slug: str) -> EventWeekend | None:
        return self.event if slug == self.event.event_slug else None

    async def session_bootstrap(self, session_id: object):
        if session_id != self.session.session_id:
            return None
        return self.event, self.session, None

    @staticmethod
    def capabilities_for(_: object, *, location_samples: int | None = None) -> SessionCapabilities:
        return SessionCapabilities(
            timing=CapabilityStatus.AVAILABLE,
            telemetry=CapabilityStatus.UNAVAILABLE,
            location=CapabilityStatus.UNKNOWN,
            weather=CapabilityStatus.UNKNOWN,
            race_control=CapabilityStatus.PARTIAL,
            pit_stops=CapabilityStatus.PARTIAL,
            stints=CapabilityStatus.PARTIAL,
            results=CapabilityStatus.UNKNOWN,
            source="openf1",
        )


class FakeSessionLocations:
    def __init__(self, samples: int = 0) -> None:
        self.samples = samples

    async def sample_count(self, session_key: str) -> int:
        return self.samples


@pytest.mark.asyncio
async def test_session_first_routes_return_lightweight_metadata_and_capabilities() -> None:
    event, session = catalog()
    services = SimpleNamespace(
        rooms=FakeRooms(event, session),
        session_locations=FakeSessionLocations(),
    )

    season = await season_weekends(2026, services)
    sessions = await weekend_sessions(event.event_slug, services)
    capabilities = await session_capabilities(session.session_id, services)
    detail = await session_detail(session.session_id, services)

    assert season.events == [event]
    assert sessions.sessions == [session]
    assert capabilities.timing is CapabilityStatus.AVAILABLE
    assert capabilities.telemetry is CapabilityStatus.UNAVAILABLE
    assert detail.session.session_id == session.session_id
    assert detail.room_slug == session.room_slug


@pytest.mark.asyncio
async def test_session_first_routes_return_a_safe_not_found_for_unknown_identity() -> None:
    event, session = catalog()
    services = SimpleNamespace(
        rooms=FakeRooms(event, session),
        session_locations=FakeSessionLocations(),
    )

    with pytest.raises(HTTPException) as error:
        await session_detail(uuid4(), services)

    assert error.value.status_code == 404
    assert error.value.detail == "Session not found"


@pytest.mark.asyncio
async def test_session_events_forward_typed_bounded_filters() -> None:
    repository = SimpleNamespace(list_for_session=AsyncMock(return_value=[]))
    services = SimpleNamespace(normalized_event_repository=repository)
    before = datetime(2026, 7, 19, 14, tzinfo=UTC)

    response = await session_events(
        "11334",
        services,
        after_sequence_number=40,
        before_sequence_number=80,
        limit=80,
        event_type=[RaceEventType.OVERTAKE, RaceEventType.BATTLE_STARTED],
        category=RaceEventCategory.BATTLES,
        driver_number=4,
        lap_number=38,
        minimum_importance=EventImportance.IMPORTANT,
        event_origin=EventOrigin.DERIVED,
        before_time=before,
    )

    assert response.count == 0
    repository.list_for_session.assert_awaited_once_with(
        "11334",
        after_sequence=40,
        before_sequence=80,
        limit=80,
        event_types=[RaceEventType.OVERTAKE, RaceEventType.BATTLE_STARTED],
        driver_number=4,
        lap_number=38,
        minimum_importance=EventImportance.IMPORTANT,
        event_origin=EventOrigin.DERIVED,
        before_time=before,
    )
