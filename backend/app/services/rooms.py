# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.domain.rooms import RaceRoom, RoomMode, RoomStatus, SourceAvailability
from app.providers.openf1 import OpenF1RestClient
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
        openf1: OpenF1RestClient | None = None,
    ) -> None:
        self.repository = repository
        self.season = season
        self.season_year = season_year
        self.fixture = fixture
        self.openf1 = openf1
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
                await self._retire_legacy_fixture()
                self._foundation_ready = True
            try:
                meetings = await self.season.calendar(self.season_year)
            except Exception as exc:
                self._retry_after = time.monotonic() + 60
                logger.warning("Race room calendar sync unavailable error=%s", type(exc).__name__)
                return
            sessions = await self._historical_sessions()
            for meeting in meetings:
                session = self._match_session(meeting, sessions)
                await self.repository.upsert_room(
                    self._from_meeting(meeting, session), agent_ids
                )
            self._catalog_ready = True

    async def sync_meetings(
        self,
        meetings: list[RaceMeeting],
        sessions: list[dict[str, Any]] | None = None,
    ) -> None:
        agents = active_agent_profiles()
        await self.repository.seed_agents(agents)
        agent_ids = [agent.id for agent in agents]
        for meeting in meetings:
            session = self._match_session(meeting, sessions or [])
            await self.repository.upsert_room(self._from_meeting(meeting, session), agent_ids)
        self._catalog_ready = True

    async def force_sync(self) -> int:
        """Refresh deterministic foundations and the external season catalog on demand."""
        async with self._sync_lock:
            agents = active_agent_profiles()
            await self.repository.seed_agents(agents)
            agent_ids = [agent.id for agent in agents]
            count = 0
            if self.fixture is not None:
                await self.repository.upsert_room(self._development_room(), agent_ids)
                await self.fixture.seed()
                await self._retire_legacy_fixture()
                self._foundation_ready = True
                count += 1
            meetings = await self.season.calendar(self.season_year)
            sessions = await self._historical_sessions()
            for meeting in meetings:
                session = self._match_session(meeting, sessions)
                await self.repository.upsert_room(
                    self._from_meeting(meeting, session), agent_ids
                )
            self._catalog_ready = True
            self._retry_after = 0.0
            return count + len(meetings)

    async def _retire_legacy_fixture(self) -> None:
        cleanup = getattr(self.repository, "delete_empty_development_room", None)
        if cleanup is not None:
            await cleanup("development-day2-validation")

    @staticmethod
    def _slug(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized[:150]

    def _from_meeting(
        self,
        meeting: RaceMeeting,
        session: dict[str, Any] | None = None,
    ) -> RaceRoom:
        if meeting.status == MeetingLifecycleStatus.COMPLETED:
            status = RoomStatus.READY
            mode = RoomMode.ARCHIVED
            availability = (
                SourceAvailability.LIMITED
                if session is not None
                else SourceAvailability.RESULTS_ONLY
            )
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
            session_key=(str(session["session_key"]) if session is not None else None),
            season=meeting.season_year,
            round_number=meeting.round_number,
            race_name=meeting.race_name,
            official_name=meeting.race_name,
            circuit_name=meeting.circuit_name,
            country=meeting.country,
            country_code=(
                str(session["country_code"]) if session and session.get("country_code") else None
            ),
            scheduled_start=meeting.race_start,
            actual_start=self._session_start(session),
            status=status,
            mode=mode,
            source_availability=availability,
            telemetry_quality=(
                "openf1_historical_available" if session is not None else "metadata_only"
            ),
            agent_count=5,
            is_featured=meeting.is_target,
        )

    async def _historical_sessions(self) -> list[dict[str, Any]]:
        if self.openf1 is None:
            return []
        try:
            return await self.openf1.sessions(
                year=self.season_year,
                session_name="Race",
            )
        except Exception as exc:
            logger.warning(
                "OpenF1 room session matching unavailable error=%s",
                type(exc).__name__,
            )
            return []

    def _match_session(
        self,
        meeting: RaceMeeting,
        sessions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        meeting_country = self._slug(meeting.country)
        meeting_circuit = self._slug(meeting.circuit_name)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for session in sessions:
            if str(session.get("session_name") or "").casefold() != "race":
                continue
            if str(session.get("year") or meeting.season_year) != str(meeting.season_year):
                continue
            start = self._session_start(session)
            if start is None:
                continue
            distance = abs((start.date() - meeting.race_date).days)
            if distance > 2:
                continue
            country = self._slug(str(session.get("country_name") or ""))
            circuit = self._slug(str(session.get("circuit_short_name") or ""))
            if country != meeting_country and not (
                circuit and (circuit in meeting_circuit or meeting_circuit in circuit)
            ):
                continue
            ranked.append((float(distance), session))
        return min(ranked, key=lambda item: item[0])[1] if ranked else None

    @staticmethod
    def _session_start(session: dict[str, Any] | None) -> datetime | None:
        if session is None or not session.get("date_start"):
            return None
        try:
            value = datetime.fromisoformat(str(session["date_start"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

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
