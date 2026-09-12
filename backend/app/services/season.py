# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.core.settings import Settings
from app.domain.models import MeetingLifecycleStatus, RaceMeeting, RaceWeekendSession
from app.providers.jolpica import JolpicaClient, JolpicaPayloadError


@dataclass
class CalendarCacheEntry:
    fetched_at: float
    retry_at: float
    checked_at: datetime
    races: list[dict]


class SeasonService:
    SESSION_FIELDS = (
        ("FirstPractice", "Practice 1"),
        ("SecondPractice", "Practice 2"),
        ("ThirdPractice", "Practice 3"),
        ("SprintQualifying", "Sprint Qualifying"),
        ("SprintShootout", "Sprint Qualifying"),
        ("Sprint", "Sprint"),
        ("Qualifying", "Qualifying"),
    )

    def __init__(self, settings: Settings, jolpica: JolpicaClient) -> None:
        self.settings = settings
        self.jolpica = jolpica
        self._calendar_lock = asyncio.Lock()
        self._calendar_cache: OrderedDict[int, CalendarCacheEntry] = OrderedDict()
        # Keep only deadlines, never exceptions with provider payloads/tracebacks.
        self._calendar_failures: OrderedDict[int, float] = OrderedDict()

    async def calendar(self, year: int, now: datetime | None = None) -> list[RaceMeeting]:
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        async with self._calendar_lock:
            cached = self._calendar_cache.get(year)
            if time.monotonic() < self._calendar_failures.get(year, 0):
                raise JolpicaPayloadError("Calendar provider temporarily unavailable")
            self._calendar_failures.pop(year, None)
            if (
                cached is not None
                and time.monotonic() < cached.retry_at
                and time.monotonic() - cached.fetched_at <= 86400
            ):
                entry = cached
                self._calendar_cache.move_to_end(year)
            else:
                try:
                    async with asyncio.timeout(20):
                        races = await self.jolpica.fetch_calendar(year)
                    if (
                        not isinstance(races, list)
                        or len(races) > 100
                        or not all(isinstance(race, dict) for race in races)
                        or len(json.dumps(races).encode("utf-8")) > 2_000_000
                    ):
                        raise JolpicaPayloadError("Calendar exceeds the supported cache budget")
                    # Validate before retaining provider data. A malformed refresh
                    # cannot poison later requests or bypass normal schema checks.
                    for race in races:
                        self._normalize_race(race, observed_at)
                    fetched_at = time.monotonic()
                    entry = CalendarCacheEntry(
                        fetched_at, fetched_at + 600, datetime.now(UTC), deepcopy(races)
                    )
                except (
                    httpx.HTTPError,
                    JolpicaPayloadError,
                    KeyError,
                    TypeError,
                    ValueError,
                    TimeoutError,
                ):
                    if cached is None or time.monotonic() - cached.fetched_at > 86400:
                        self._calendar_failures[year] = time.monotonic() + 30
                        while len(self._calendar_failures) > 8:
                            self._calendar_failures.popitem(last=False)
                        raise
                    entry = cached
                    entry.retry_at = time.monotonic() + 30
                self._calendar_cache[year] = entry
                self._calendar_cache.move_to_end(year)
                while len(self._calendar_cache) > 8:
                    self._calendar_cache.popitem(last=False)
            age = max(0.0, time.monotonic() - entry.fetched_at)
            return [
                self._normalize_race(race, observed_at).model_copy(
                    update={
                        "source_checked_at": entry.checked_at,
                        "source_age_seconds": age,
                        "source_stale": age >= 600,
                    }
                )
                for race in entry.races
            ]

    def _normalize_race(self, race: dict[str, object], now: datetime) -> RaceMeeting:
        circuit = race.get("Circuit")
        if not isinstance(circuit, dict):
            raise ValueError("Jolpica race is missing circuit metadata")
        location = circuit.get("Location")
        if not isinstance(location, dict):
            raise ValueError("Jolpica race is missing circuit location")

        season_year = int(str(race["season"]))
        round_number = int(str(race["round"]))
        race_name = str(race["raceName"])
        circuit_name = str(circuit["circuitName"])
        race_date = datetime.fromisoformat(str(race["date"])).date()
        race_time = str(race.get("time") or "00:00:00Z")
        race_start = datetime.fromisoformat(
            f"{race_date.isoformat()}T{race_time}".replace("Z", "+00:00")
        )

        sessions = self._sessions(race, race_start)
        weekend_start = min(session.starts_at for session in sessions)
        weekend_end = max(
            session.ends_at or session.starts_at + self._session_duration(session.name)
            for session in sessions
        )
        if weekend_start - timedelta(hours=12) <= now < weekend_end:
            status = MeetingLifecycleStatus.LIVE
        elif now >= weekend_end:
            status = MeetingLifecycleStatus.COMPLETED
        else:
            status = MeetingLifecycleStatus.UPCOMING

        target_name = self._slug(self.settings.target_grand_prix)
        target_circuit = self._slug(self.settings.target_circuit)
        is_target = target_name in self._slug(race_name) or target_circuit in self._slug(
            circuit_name
        )

        return RaceMeeting(
            id=uuid5(NAMESPACE_URL, f"apex-arena:{season_year}:{round_number}"),
            season_year=season_year,
            round_number=round_number,
            race_name=race_name,
            circuit_id=str(circuit["circuitId"]),
            circuit_name=circuit_name,
            locality=str(location["locality"]),
            country=str(location["country"]),
            race_date=race_date,
            race_start=race_start,
            status=status,
            is_target=is_target,
            source_url=str(race["url"]) if race.get("url") else None,
            sessions=sessions,
        )

    def _sessions(self, race: dict[str, object], race_start: datetime) -> list[RaceWeekendSession]:
        sessions: list[RaceWeekendSession] = []
        seen: set[tuple[str, datetime]] = set()
        for field, name in self.SESSION_FIELDS:
            value = race.get(field)
            if not isinstance(value, dict) or not value.get("date"):
                continue
            starts_at = datetime.fromisoformat(
                f"{value['date']}T{value.get('time') or '00:00:00Z'}".replace("Z", "+00:00")
            )
            key = (name, starts_at)
            if key not in seen:
                sessions.append(
                    RaceWeekendSession(
                        name=name,
                        starts_at=starts_at,
                        ends_at=starts_at + self._session_duration(name),
                    )
                )
                seen.add(key)
        sessions.append(
            RaceWeekendSession(
                name="Race",
                starts_at=race_start,
                ends_at=race_start + self._session_duration("Race"),
            )
        )
        return sorted(sessions, key=lambda session: session.starts_at)

    @staticmethod
    def _session_duration(name: str) -> timedelta:
        normalized = name.casefold()
        if "race" in normalized and "sprint" not in normalized:
            return timedelta(hours=4)
        if "sprint" in normalized and "qual" not in normalized and "shootout" not in normalized:
            return timedelta(hours=2)
        return timedelta(hours=2)

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())
