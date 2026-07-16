# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.domain.rooms import RaceRoom, RoomMode, RoomStatus, SourceAvailability
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
    ) -> None:
        self.repository = repository
        self.season = season
        self.season_year = season_year
        self._catalog_ready = False

    async def ensure_catalog(self) -> None:
        if self._catalog_ready:
            return
        agents = active_agent_profiles()
        await self.repository.seed_agents(agents)
        agent_ids = [agent.id for agent in agents]
        await self.repository.upsert_room(self._development_room(), agent_ids)
        try:
            meetings = await self.season.calendar(self.season_year)
        except Exception as exc:
            logger.warning("Race room calendar sync unavailable error=%s", type(exc).__name__)
        else:
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
            agent_count=5,
            is_featured=meeting.is_target,
        )

    def _development_room(self) -> RaceRoom:
        return RaceRoom(
            slug="development-day2-validation",
            session_key="day2-validation",
            season=self.season_year,
            race_name="Day 2 Validation Session",
            official_name="Apex Arena Development Validation",
            circuit_name="Synthetic validation circuit",
            country="Development fixture",
            scheduled_start=datetime(2026, 7, 16, 12, tzinfo=UTC),
            status=RoomStatus.READY,
            mode=RoomMode.DEVELOPMENT,
            source_availability=SourceAvailability.LIMITED,
            agent_count=5,
            is_featured=True,
            is_development=True,
        )
