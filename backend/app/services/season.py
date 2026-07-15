# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from app.core.settings import Settings
from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.providers.jolpica import JolpicaClient


class SeasonService:
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

        if race_start <= now < race_start + timedelta(hours=4):
            status = MeetingLifecycleStatus.LIVE
        elif now >= race_start + timedelta(hours=4):
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
        )

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())
