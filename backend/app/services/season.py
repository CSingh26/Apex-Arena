# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from app.core.settings import Settings
from app.domain.models import MeetingLifecycleStatus, RaceMeeting, RaceWeekendSession
from app.providers.jolpica import JolpicaClient


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

    async def calendar(self, year: int, now: datetime | None = None) -> list[RaceMeeting]:
        races = await self.jolpica.fetch_calendar(year)
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        return [self._normalize_race(race, observed_at) for race in races]

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
