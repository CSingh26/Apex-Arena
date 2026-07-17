# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime

from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.domain.rooms import RaceRoom, RoomMode, RoomStatus, SourceAvailability
from app.services.development_fixture import DAY3_FIXTURE_SESSION_KEY, DevelopmentFixtureService
from app.services.room_agents import active_agent_profiles
from app.services.season import SeasonService
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)


class RaceRoomService:
    def __init__(
        self,
        repository: SqlRaceRoomRepository,
        season: SeasonService,
        season_year: int,
        fixture: DevelopmentFixtureService | None = None,
    ) -> None:
        self.repository = repository
        self.season = season
        self.season_year = season_year
        self.fixture = fixture
        self._catalog_ready = False
        self._foundation_ready = False
        self._retry_after = 0.0
        self._sync_lock = asyncio.Lock()

    async def ensure_catalog(self) -> None:
        if self._catalog_ready or time.monotonic() < self._retry_after:
            return
        async with self._sync_lock:
            if self._catalog_ready:
                return
            agents = active_agent_profiles()
            await self.repository.seed_agents(agents)
            agent_ids = [agent.id for agent in agents]
            if not self._foundation_ready and self.fixture is not None:
                await self.repository.upsert_room(self._development_room(), agent_ids)
                await self.fixture.seed()
                self._foundation_ready = True
            try:
                meetings = await self.season.calendar(self.season_year)
            except Exception as exc:
                self._retry_after = time.monotonic() + 60
                logger.warning("Race room calendar sync unavailable error=%s", type(exc).__name__)
                return
            for meeting in meetings:
                await self.repository.upsert_room(self._from_meeting(meeting), agent_ids)
            self._catalog_ready = True

    async def sync_meetings(self, meetings: list[RaceMeeting]) -> None:
        agents = active_agent_profiles()
        await self.repository.seed_agents(agents)
        agent_ids = [agent.id for agent in agents]
        for meeting in meetings:
            await self.repository.upsert_room(self._from_meeting(meeting), agent_ids)
        self._catalog_ready = True

    @staticmethod
    def _slug(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized[:150]

    def _from_meeting(self, meeting: RaceMeeting) -> RaceRoom:
        if meeting.status == MeetingLifecycleStatus.COMPLETED:
            status = RoomStatus.READY
            mode = RoomMode.ARCHIVED
            availability = SourceAvailability.RESULTS_ONLY
        elif meeting.status == MeetingLifecycleStatus.LIVE:
            status = RoomStatus.READY
            mode = RoomMode.LIVE
            availability = SourceAvailability.LIMITED
        else:
            status = RoomStatus.PENDING
            mode = RoomMode.REPLAY
            availability = SourceAvailability.UNAVAILABLE
        return RaceRoom(
            slug=f"{meeting.season_year}-{self._slug(meeting.race_name)}-race",
            season=meeting.season_year,
            round_number=meeting.round_number,
            race_name=meeting.race_name,
            official_name=meeting.race_name,
            circuit_name=meeting.circuit_name,
            country=meeting.country,
            scheduled_start=meeting.race_start,
            status=status,
            mode=mode,
            source_availability=availability,
            telemetry_quality="metadata_only",
            agent_count=5,
            is_featured=meeting.is_target,
        )

    def _development_room(self) -> RaceRoom:
        return RaceRoom(
            slug="day3-validation-room",
            session_key=DAY3_FIXTURE_SESSION_KEY,
            season=self.season_year,
            race_name="Day 3 Validation Room",
            official_name="Apex Arena Day 3 Development Validation",
            circuit_name="Synthetic validation circuit",
            country="Development fixture",
            scheduled_start=datetime(2026, 7, 16, 12, tzinfo=UTC),
            status=RoomStatus.READY,
            mode=RoomMode.DEVELOPMENT,
            source_availability=SourceAvailability.LIMITED,
            telemetry_quality="deterministic_fixture",
            total_laps=12,
            agent_count=5,
            is_featured=True,
            is_development=True,
        )
