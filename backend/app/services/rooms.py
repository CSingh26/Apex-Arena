# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.models import MeetingLifecycleStatus, RaceMeeting, RaceWeekendSession
from app.domain.rooms import (
    EventWeekend,
    IngestionStatus,
    PublicSessionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionRoomSummary,
    SessionType,
    SourceAvailability,
    WeekendStatus,
)
from app.providers.openf1 import OpenF1RestClient
from app.services.development_fixture import DAY3_FIXTURE_SESSION_KEY, DevelopmentFixtureService
from app.services.provider_matching import OpenF1SessionMatcher
from app.services.room_agents import active_agent_profiles
from app.services.room_eligibility import RoomEligibilityService
from app.services.season import SeasonService
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)

SESSION_DURATION = {
    SessionType.SPRINT_QUALIFYING: timedelta(hours=1, minutes=30),
    SessionType.SPRINT: timedelta(hours=2),
    SessionType.QUALIFYING: timedelta(hours=2),
    SessionType.RACE: timedelta(hours=4),
}


class RaceRoomService:
    """Session-generic catalog service retaining the stable Race Rooms public name."""

    def __init__(
        self,
        repository: SqlRaceRoomRepository,
        season: SeasonService,
        season_year: int,
        fixture: DevelopmentFixtureService | None = None,
        openf1: OpenF1RestClient | None = None,
        eligibility: RoomEligibilityService | None = None,
    ) -> None:
        self.repository = repository
        self.season = season
        self.season_year = season_year
        self.fixture = fixture
        self.openf1 = openf1
        self.eligibility = eligibility or RoomEligibilityService()
        self.session_matcher = OpenF1SessionMatcher()
        self._catalog_ready = False
        self._foundation_ready = False
        self._retry_after = 0.0
        self._sync_lock = asyncio.Lock()
        self._meetings: list[RaceMeeting] = []
        self._provider_sessions: list[dict[str, Any]] = []

    async def ensure_catalog(self) -> None:
        """Read provider metadata without creating rooms as a GET side effect."""
        if self._catalog_ready or time.monotonic() < self._retry_after:
            return
        async with self._sync_lock:
            if self._catalog_ready:
                return
            try:
                self._meetings = await self.season.calendar(self.season_year)
            except Exception as exc:
                self._retry_after = time.monotonic() + 60
                logger.warning("Race room calendar unavailable error=%s", type(exc).__name__)
                return
            self._provider_sessions = await self._historical_sessions()
            self._catalog_ready = True

    def invalidate_catalog(self) -> None:
        """Force the next public catalog read to observe fresh provider metadata."""

        self._catalog_ready = False
        self._retry_after = 0.0

    async def sync_meetings(
        self,
        meetings: list[RaceMeeting],
        sessions: list[dict[str, Any]] | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        """Explicit idempotent lifecycle operation used by jobs and tests."""
        self._meetings = meetings
        self._provider_sessions = sessions or []
        await self._synchronize(meetings, self._provider_sessions, now=now)
        self._catalog_ready = True

    async def force_sync(self) -> int:
        """Internal authenticated lifecycle operation; safe to retry."""
        async with self._sync_lock:
            count = 0
            if self.fixture is not None:
                agents = active_agent_profiles()
                await self.repository.seed_agents(agents)
                await self.repository.upsert_room(
                    self._development_room(), [agent.id for agent in agents]
                )
                await self.fixture.seed()
                await self._retire_legacy_fixture()
                self._foundation_ready = True
                count += 1
            meetings = await self.season.calendar(self.season_year)
            sessions = await self._historical_sessions()
            self._meetings = meetings
            self._provider_sessions = sessions
            count += await self._synchronize(meetings, sessions)
            self._catalog_ready = True
            self._retry_after = 0.0
            return count

    async def grouped_events(
        self,
        *,
        season: int | None = None,
        status: WeekendStatus | None = None,
        session_type: SessionType | None = None,
        is_sprint_weekend: bool | None = None,
        search: str | None = None,
        limit: int = 30,
        offset: int = 0,
        now: datetime | None = None,
    ) -> tuple[list[EventWeekend], int]:
        requested_season = season or self.season_year
        if requested_season != self.season_year:
            meetings = await self.season.calendar(requested_season, now=now)
            provider_sessions: list[dict[str, Any]] = []
        else:
            await self.ensure_catalog()
            meetings = self._meetings
            provider_sessions = self._provider_sessions

        rooms, _ = await self.repository.list_rooms(
            season=requested_season,
            sort="race_date_asc",
            limit=500,
            offset=0,
            # Grouped schedule summaries need pending/unavailable rows so they
            # can report authoritative provider_pending state without exposing
            # a room slug. The legacy flat public catalog intentionally keeps
            # those rows filtered out.
            include_unavailable=True,
        )
        public_rooms = {
            (room.round_number, room.session_type): room
            for room in rooms
            if not room.is_development
        }
        observed_at = self._aware(now or datetime.now(UTC))
        events = [
            self._event_weekend(
                meeting,
                provider_sessions,
                public_rooms,
                observed_at,
            )
            for meeting in meetings
        ]
        if not events and rooms:
            events = self._events_from_rooms(rooms, observed_at)
        if status is not None:
            events = [event for event in events if event.weekend_status is status]
        if session_type is not None:
            events = [
                event
                for event in events
                if any(item.session_type is session_type for item in event.sessions)
            ]
        if is_sprint_weekend is not None:
            events = [event for event in events if event.is_sprint_weekend is is_sprint_weekend]
        if search and search.strip():
            needle = search.strip().casefold()
            events = [
                event
                for event in events
                if needle
                in " ".join((event.event_name, event.circuit_name, event.country)).casefold()
            ]
        events.sort(key=self._event_sort_key)
        total = len(events)
        return events[offset : offset + limit], total

    async def _synchronize(
        self,
        meetings: list[RaceMeeting],
        sessions: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> int:
        observed_at = self._aware(now or datetime.now(UTC))
        agents = active_agent_profiles()
        await self.repository.seed_agents(agents)
        agent_ids = [agent.id for agent in agents]
        synchronized = 0
        for meeting in meetings:
            session_specs = self._competitive_sessions(meeting)
            matched_by_type = {
                session_type: self._match_session(
                    meeting, session_type, scheduled.starts_at, sessions
                )
                for session_type, scheduled in session_specs
            }
            weekend_start, weekend_end = self._provider_weekend_bounds(
                session_specs, matched_by_type
            )
            is_sprint = any(
                session_type in {SessionType.SPRINT_QUALIFYING, SessionType.SPRINT}
                for session_type, _ in session_specs
            )
            for session_type, scheduled in session_specs:
                provider_session = matched_by_type[session_type]
                public_status = self._session_status(
                    meeting, session_type, scheduled.starts_at, provider_session, observed_at
                )
                availability = self._availability(session_type, public_status, provider_session)
                results_available = False
                replay_available = False
                event_slug = self._event_slug(meeting)
                slug = self._room_slug(event_slug, session_type)
                existing = await self._existing_room(slug)
                decision = self.eligibility.evaluate(
                    scheduled_start=scheduled.starts_at,
                    actual_status=public_status.value,
                    provider_session_available=provider_session is not None,
                    data_availability=availability,
                    replay_available=replay_available,
                    results_available=results_available,
                    existing_room=existing,
                    now=observed_at,
                )
                # Future calendar entries remain read-only metadata. A stale future
                # row is not refreshed or treated as navigable.
                if not decision.can_create and not decision.can_open:
                    continue
                room = self._from_session(
                    meeting=meeting,
                    session_type=session_type,
                    scheduled=scheduled.starts_at,
                    provider_session=provider_session,
                    public_status=public_status,
                    availability=availability,
                    eligibility=(
                        RoomEligibilityStatus.PROVIDER_PENDING
                        if availability is SourceAvailability.UNAVAILABLE
                        else decision.status
                    ),
                    weekend_start=weekend_start,
                    weekend_end=weekend_end,
                    is_sprint_weekend=is_sprint,
                    replay_available=replay_available,
                    results_available=results_available,
                )
                await self.repository.upsert_room(room, agent_ids)
                synchronized += 1
        return synchronized

    def _event_weekend(
        self,
        meeting: RaceMeeting,
        provider_sessions: list[dict[str, Any]],
        rooms: dict[tuple[int | None, SessionType], RaceRoom],
        now: datetime,
    ) -> EventWeekend:
        session_specs = self._competitive_sessions(meeting)
        matched_by_type = {
            session_type: self._match_session(
                meeting, session_type, scheduled.starts_at, provider_sessions
            )
            for session_type, scheduled in session_specs
        }
        weekend_start, weekend_end = self._provider_weekend_bounds(session_specs, matched_by_type)
        weekend_status = self._weekend_status(meeting, weekend_start, weekend_end, now)
        summaries: list[SessionRoomSummary] = []
        matched_sessions: list[dict[str, Any]] = []
        for session_type, scheduled in session_specs:
            provider_session = matched_by_type[session_type]
            if provider_session is not None:
                matched_sessions.append(provider_session)
            public_status = self._session_status(
                meeting, session_type, scheduled.starts_at, provider_session, now
            )
            availability = self._availability(session_type, public_status, provider_session)
            existing = rooms.get((meeting.round_number, session_type))
            results_available = bool(existing.results_available) if existing is not None else False
            replay_available = bool(existing.replay_available) if existing is not None else False
            decision = self.eligibility.evaluate(
                scheduled_start=scheduled.starts_at,
                actual_status=public_status.value,
                provider_session_available=provider_session is not None,
                data_availability=(
                    existing.source_availability if existing is not None else availability
                ),
                replay_available=replay_available,
                results_available=results_available,
                existing_room=existing,
                now=now,
            )
            summaries.append(
                SessionRoomSummary(
                    session_type=session_type,
                    display_name=session_type.display_name,
                    scheduled_start=scheduled.starts_at,
                    actual_start=(
                        existing.actual_start
                        if existing is not None
                        else self._session_start(provider_session)
                    ),
                    status=public_status,
                    room_slug=(
                        existing.slug if existing is not None and decision.can_open else None
                    ),
                    room_eligible=existing is not None and decision.can_open,
                    eligibility=decision.status,
                    data_availability=(
                        existing.source_availability if existing is not None else availability
                    ),
                    replay_available=replay_available and decision.can_replay,
                    results_available=results_available,
                )
            )
        meeting_key = next(
            (
                str(row["meeting_key"])
                for row in matched_sessions
                if row.get("meeting_key") is not None
            ),
            None,
        )
        return EventWeekend(
            event_id=meeting.id,
            event_slug=self._event_slug(meeting),
            meeting_key=meeting_key,
            season=meeting.season_year,
            round=meeting.round_number,
            event_name=meeting.race_name,
            circuit_name=meeting.circuit_name,
            country=meeting.country,
            weekend_start=weekend_start,
            weekend_end=weekend_end,
            weekend_status=weekend_status,
            is_sprint_weekend=any(
                item.session_type in {SessionType.SPRINT_QUALIFYING, SessionType.SPRINT}
                for item in summaries
            ),
            sessions=summaries,
        )

    def _events_from_rooms(self, rooms: list[RaceRoom], now: datetime) -> list[EventWeekend]:
        grouped: dict[tuple[int, int], list[RaceRoom]] = {}
        for room in rooms:
            if room.is_development or room.round_number is None:
                continue
            grouped.setdefault((room.season, room.round_number), []).append(room)
        events: list[EventWeekend] = []
        for (_, round_number), event_rooms in grouped.items():
            event_rooms.sort(key=lambda item: item.scheduled_start)
            first = event_rooms[0]
            weekend_start = first.weekend_start or first.scheduled_start
            weekend_end = event_rooms[-1].weekend_end or (
                event_rooms[-1].scheduled_start + SESSION_DURATION[event_rooms[-1].session_type]
            )
            weekend_status = (
                WeekendStatus.UPCOMING
                if now < weekend_start
                else WeekendStatus.COMPLETED
                if now >= weekend_end
                else WeekendStatus.LIVE
            )
            events.append(
                EventWeekend(
                    event_id=first.id,
                    event_slug=first.event_slug or self._slug(first.race_name),
                    meeting_key=first.meeting_key,
                    season=first.season,
                    round=round_number,
                    event_name=first.race_name,
                    circuit_name=first.circuit_name,
                    country=first.country,
                    weekend_start=weekend_start,
                    weekend_end=weekend_end,
                    weekend_status=weekend_status,
                    is_sprint_weekend=any(item.is_sprint_weekend for item in event_rooms),
                    sessions=[
                        SessionRoomSummary(
                            session_type=room.session_type,
                            display_name=room.session_type.display_name,
                            scheduled_start=room.scheduled_start,
                            actual_start=room.actual_start,
                            status=(
                                PublicSessionStatus.UPCOMING
                                if now < room.scheduled_start
                                else PublicSessionStatus.COMPLETED
                                if weekend_status is WeekendStatus.COMPLETED
                                else PublicSessionStatus.LIVE
                            ),
                            room_slug=room.slug,
                            room_eligible=True,
                            eligibility=RoomEligibilityStatus.ALREADY_EXISTS,
                            data_availability=room.source_availability,
                            replay_available=room.replay_available,
                            results_available=room.results_available,
                        )
                        for room in event_rooms
                    ],
                )
            )
        return events

    async def _retire_legacy_fixture(self) -> None:
        cleanup = getattr(self.repository, "delete_empty_development_room", None)
        if cleanup is not None:
            await cleanup("development-day2-validation")

    async def _existing_room(self, slug: str) -> RaceRoom | None:
        getter = getattr(self.repository, "get_room", None)
        if getter is not None:
            return await getter(slug)
        rooms = getattr(self.repository, "rooms", None)
        return rooms.get(slug) if isinstance(rooms, dict) else None

    @staticmethod
    def _slug(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized[:150]

    def _event_slug(self, meeting: RaceMeeting) -> str:
        return f"{meeting.season_year}-{self._slug(meeting.race_name)}"

    @staticmethod
    def _room_slug(event_slug: str, session_type: SessionType) -> str:
        suffix = {
            SessionType.SPRINT_QUALIFYING: "sprint-qualifying",
            SessionType.SPRINT: "sprint",
            SessionType.QUALIFYING: "qualifying",
            SessionType.RACE: "race",
        }[session_type]
        return f"{event_slug}-{suffix}"

    def _from_session(
        self,
        *,
        meeting: RaceMeeting,
        session_type: SessionType,
        scheduled: datetime,
        provider_session: dict[str, Any] | None,
        public_status: PublicSessionStatus,
        availability: SourceAvailability,
        eligibility: RoomEligibilityStatus,
        weekend_start: datetime,
        weekend_end: datetime,
        is_sprint_weekend: bool,
        replay_available: bool,
        results_available: bool,
    ) -> RaceRoom:
        completed = public_status is PublicSessionStatus.COMPLETED
        live = public_status is PublicSessionStatus.LIVE
        event_slug = self._event_slug(meeting)
        return RaceRoom(
            slug=self._room_slug(event_slug, session_type),
            event_slug=event_slug,
            meeting_key=(
                str(provider_session["meeting_key"])
                if provider_session and provider_session.get("meeting_key") is not None
                else None
            ),
            session_key=(
                str(provider_session["session_key"])
                if provider_session and provider_session.get("session_key") is not None
                else None
            ),
            season=meeting.season_year,
            round_number=meeting.round_number,
            race_name=(
                meeting.race_name
                if session_type is SessionType.RACE
                else f"{meeting.race_name} — {session_type.display_name}"
            ),
            official_name=meeting.race_name,
            circuit_name=meeting.circuit_name,
            country=meeting.country,
            country_code=(
                str(provider_session["country_code"])
                if provider_session and provider_session.get("country_code")
                else None
            ),
            session_type=session_type,
            scheduled_start=scheduled,
            actual_start=self._session_start(provider_session),
            weekend_start=weekend_start,
            weekend_end=weekend_end,
            is_sprint_weekend=is_sprint_weekend,
            status=(
                RoomStatus.LIVE
                if live and availability is not SourceAvailability.UNAVAILABLE
                else RoomStatus.READY
                if completed and availability is not SourceAvailability.UNAVAILABLE
                else RoomStatus.PENDING
            ),
            mode=(
                RoomMode.LIVE
                if live and availability is not SourceAvailability.UNAVAILABLE
                else RoomMode.ARCHIVED
                if completed and availability is not SourceAvailability.UNAVAILABLE
                else RoomMode.REPLAY
            ),
            eligibility_status=eligibility,
            ingestion_status=IngestionStatus.PENDING,
            source_availability=availability,
            replay_available=replay_available,
            results_available=results_available,
            telemetry_quality=(
                "openf1_session_discovered" if provider_session is not None else "metadata_only"
            ),
            agent_count=5,
            is_featured=meeting.is_target and session_type is SessionType.RACE,
        )

    async def _historical_sessions(self) -> list[dict[str, Any]]:
        if self.openf1 is None:
            return []
        try:
            return await self.openf1.sessions(year=self.season_year)
        except Exception as exc:
            logger.warning("OpenF1 session discovery unavailable error=%s", type(exc).__name__)
            return []

    def _match_session(
        self,
        meeting: RaceMeeting,
        session_type: SessionType,
        scheduled_start: datetime,
        sessions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        match = self.session_matcher.match_session(
            meeting,
            sessions,
            session_type,
            scheduled_start=self._aware(scheduled_start),
        )
        return match.session if match.resolved else None

    @staticmethod
    def _session_start(session: dict[str, Any] | None) -> datetime | None:
        if session is None or not session.get("date_start"):
            return None
        try:
            value = datetime.fromisoformat(str(session["date_start"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _session_end(session: dict[str, Any] | None) -> datetime | None:
        if session is None or not session.get("date_end"):
            return None
        try:
            value = datetime.fromisoformat(str(session["date_end"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def _competitive_sessions(
        self, meeting: RaceMeeting
    ) -> list[tuple[SessionType, RaceWeekendSession]]:
        by_type: dict[SessionType, RaceWeekendSession] = {}
        for session in meeting.sessions:
            session_type = SessionType.from_provider_name(session.name)
            if session_type is not None:
                by_type[session_type] = session
        by_type.setdefault(
            SessionType.RACE,
            RaceWeekendSession(name="Race", starts_at=meeting.race_start),
        )
        return sorted(by_type.items(), key=lambda item: item[1].starts_at)

    def _session_status(
        self,
        meeting: RaceMeeting,
        session_type: SessionType,
        scheduled_start: datetime,
        provider_session: dict[str, Any] | None,
        now: datetime,
    ) -> PublicSessionStatus:
        start = self._session_start(provider_session) or self._aware(scheduled_start)
        provider_end = self._session_end(provider_session)
        end = provider_end or start + SESSION_DURATION[session_type]
        if meeting.status is MeetingLifecycleStatus.COMPLETED or now >= end:
            return PublicSessionStatus.COMPLETED
        if now < start:
            return PublicSessionStatus.UPCOMING
        return PublicSessionStatus.LIVE

    @staticmethod
    def _availability(
        session_type: SessionType,
        status: PublicSessionStatus,
        provider_session: dict[str, Any] | None,
    ) -> SourceAvailability:
        # Session discovery proves identity only. The historical ingestion
        # workflow promotes availability after normalized datasets persist.
        return SourceAvailability.UNAVAILABLE

    def _provider_weekend_bounds(
        self,
        sessions: Iterable[tuple[SessionType, RaceWeekendSession]],
        matches: dict[SessionType, dict[str, Any] | None],
    ) -> tuple[datetime, datetime]:
        session_list = list(sessions)
        starts = [
            self._session_start(matches.get(session_type)) or scheduled.starts_at
            for session_type, scheduled in session_list
        ]
        ends = [
            self._session_end(matches.get(session_type))
            or (self._session_start(matches.get(session_type)) or scheduled.starts_at)
            + SESSION_DURATION[session_type]
            for session_type, scheduled in session_list
        ]
        return min(starts), max(ends)

    @staticmethod
    def _weekend_status(
        meeting: RaceMeeting,
        weekend_start: datetime,
        weekend_end: datetime,
        now: datetime,
    ) -> WeekendStatus:
        if meeting.status is MeetingLifecycleStatus.COMPLETED or now >= weekend_end:
            return WeekendStatus.COMPLETED
        if meeting.status is MeetingLifecycleStatus.LIVE or (
            weekend_start - timedelta(hours=12) <= now < weekend_end
        ):
            return WeekendStatus.LIVE
        return WeekendStatus.UPCOMING

    @staticmethod
    def _event_sort_key(event: EventWeekend) -> tuple[int, datetime]:
        rank = {
            WeekendStatus.LIVE: 0,
            WeekendStatus.COMPLETED: 1,
            WeekendStatus.UPCOMING: 2,
        }[event.weekend_status]
        return rank, event.weekend_start

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def _development_room(self) -> RaceRoom:
        return RaceRoom(
            slug="day3-validation-room",
            event_slug="day3-validation",
            session_key=DAY3_FIXTURE_SESSION_KEY,
            season=self.season_year,
            race_name="Day 3 Validation Room",
            official_name="Apex Arena Day 3 Development Validation",
            circuit_name="Synthetic validation circuit",
            country="Development fixture",
            session_type=SessionType.RACE,
            scheduled_start=datetime(2026, 7, 16, 12, tzinfo=UTC),
            weekend_start=datetime(2026, 7, 16, 12, tzinfo=UTC),
            weekend_end=datetime(2026, 7, 16, 16, tzinfo=UTC),
            status=RoomStatus.READY,
            mode=RoomMode.DEVELOPMENT,
            eligibility_status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
            ingestion_status=IngestionStatus.READY,
            source_availability=SourceAvailability.LIMITED,
            replay_available=True,
            results_available=True,
            telemetry_quality="deterministic_fixture",
            total_laps=12,
            agent_count=5,
            is_featured=False,
            is_development=True,
        )
