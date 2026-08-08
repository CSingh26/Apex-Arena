# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.api.schemas import (
    ChampionshipDriverRef,
    ChampionshipLeader,
    ChampionshipMetadata,
    ChampionshipSummaryResponse,
    ConstructorStanding,
    ConstructorStandingsResponse,
    DriverStanding,
    DriverStandingsResponse,
)
from app.domain.models import MeetingLifecycleStatus, NormalizedRaceEvent, RaceEventType
from app.providers.jolpica import JolpicaClient
from app.providers.openf1 import OpenF1RestClient

logger = logging.getLogger(__name__)


class ChampionshipUnavailableError(RuntimeError):
    pass


class ChampionshipService:
    """Build one provider-neutral season snapshot with bounded upstream requests."""

    CACHE_PREFIX = "apexarena:{season}:standings"

    def __init__(
        self,
        *,
        season: int,
        openf1: OpenF1RestClient,
        jolpica: JolpicaClient,
        season_service: Any,
        redis: Any,
        normal_ttl_seconds: int = 600,
        live_ttl_seconds: int = 30,
    ) -> None:
        self.season = season
        self.openf1 = openf1
        self.jolpica = jolpica
        self.season_service = season_service
        self.redis = redis
        self.normal_ttl_seconds = normal_ttl_seconds
        self.live_ttl_seconds = live_ttl_seconds
        self._refresh_lock = asyncio.Lock()

    async def drivers(self) -> DriverStandingsResponse:
        value = await self._read_cached("drivers", DriverStandingsResponse)
        if value is not None:
            return value
        refreshed = await self._refresh()
        if refreshed is not None:
            return refreshed[0]
        value = await self._read_cached("drivers", DriverStandingsResponse)
        if value is None:
            raise ChampionshipUnavailableError("Driver championship standings are unavailable")
        return value

    async def constructors(self) -> ConstructorStandingsResponse:
        value = await self._read_cached("constructors", ConstructorStandingsResponse)
        if value is not None:
            return value
        refreshed = await self._refresh()
        if refreshed is not None:
            return refreshed[1]
        value = await self._read_cached("constructors", ConstructorStandingsResponse)
        if value is None:
            raise ChampionshipUnavailableError("Constructor championship standings are unavailable")
        return value

    async def summary(self) -> ChampionshipSummaryResponse:
        value = await self._read_cached("summary", ChampionshipSummaryResponse)
        if value is not None:
            return value
        refreshed = await self._refresh()
        if refreshed is not None:
            return refreshed[2]
        value = await self._read_cached("summary", ChampionshipSummaryResponse)
        if value is None:
            raise ChampionshipUnavailableError("Championship summary is unavailable")
        return value

    async def _refresh(
        self,
    ) -> (
        tuple[
            DriverStandingsResponse,
            ConstructorStandingsResponse,
            ChampionshipSummaryResponse,
        ]
        | None
    ):
        async with self._refresh_lock:
            cached_values = await asyncio.gather(
                self._read_cached("drivers", DriverStandingsResponse),
                self._read_cached("constructors", ConstructorStandingsResponse),
                self._read_cached("summary", ChampionshipSummaryResponse),
            )
            if all(value is not None for value in cached_values):
                return None
            try:
                drivers, constructors, summary = await self._build_snapshot()
            except Exception as exc:
                logger.warning("Championship refresh failed error=%s", type(exc).__name__)
                restored = await self._restore_stale()
                if not restored:
                    raise ChampionshipUnavailableError(
                        "Championship providers are temporarily unavailable"
                    ) from exc
                return None
            ttl = self.live_ttl_seconds if drivers.metadata.live else self.normal_ttl_seconds
            await asyncio.gather(
                self._write_cached("drivers", drivers, ttl),
                self._write_cached("constructors", constructors, ttl),
                self._write_cached("summary", summary, ttl),
            )
            return drivers, constructors, summary

    async def _build_snapshot(
        self,
    ) -> tuple[DriverStandingsResponse, ConstructorStandingsResponse, ChampionshipSummaryResponse]:
        calls = (
            self.openf1.championship_drivers(session_key="latest"),
            self.openf1.championship_teams(session_key="latest"),
            self.openf1.drivers(session_key="latest"),
            self.jolpica.fetch_driver_standings(self.season),
            self.jolpica.fetch_constructor_standings(self.season),
            self.jolpica.fetch_race_results(self.season),
            self.jolpica.fetch_qualifying_results(self.season),
            self.jolpica.fetch_sprint_results(self.season),
            self.season_service.calendar(self.season),
        )
        results = await asyncio.gather(*calls, return_exceptions=True)
        (
            open_drivers,
            open_teams,
            driver_metadata,
            jolpica_drivers,
            jolpica_teams,
            races,
            qualifying,
            sprints,
            calendar,
        ) = [self._rows_or_empty(result) for result in results]

        if not open_drivers and not jolpica_drivers:
            raise ChampionshipUnavailableError("No driver standings provider returned data")
        if not open_teams and not jolpica_teams:
            raise ChampionshipUnavailableError("No constructor standings provider returned data")

        if (open_drivers or open_teams) and (jolpica_drivers or jolpica_teams or races):
            source = "OpenF1 + Jolpica"
        elif open_drivers or open_teams:
            source = "OpenF1"
        else:
            source = "Jolpica"
        completed_rounds = {self._int(row.get("_round")) for row in races}
        completed_rounds.discard(None)
        races_completed = len(completed_rounds) or sum(
            getattr(item, "status", None) == MeetingLifecycleStatus.COMPLETED for item in calendar
        )
        latest_completed_round = max(completed_rounds, default=0)
        if not open_drivers and latest_completed_round > 1:
            previous_driver_rows, previous_team_rows = await asyncio.gather(
                self._safe_previous_standings("drivers", latest_completed_round - 1),
                self._safe_previous_standings("constructors", latest_completed_round - 1),
            )
            self._add_previous_snapshot(jolpica_drivers, previous_driver_rows, driver=True)
            self._add_previous_snapshot(jolpica_teams, previous_team_rows, driver=False)
        total_races = len(calendar) or None
        races_remaining = max(total_races - races_completed, 0) if total_races is not None else None
        live = any(
            getattr(item, "status", None) == MeetingLifecycleStatus.LIVE for item in calendar
        )
        live_rounds = {
            getattr(item, "round_number", None)
            for item in calendar
            if getattr(item, "status", None) == MeetingLifecycleStatus.LIVE
        }
        official_live_result = bool(live_rounds.intersection(completed_rounds))
        latest_race = self._latest_race_name(races)
        next_race = next(
            (
                item.race_name
                for item in calendar
                if getattr(item, "status", None) == MeetingLifecycleStatus.UPCOMING
            ),
            None,
        )
        metadata = ChampionshipMetadata(
            season=self.season,
            generated_at=datetime.now(UTC),
            latest_completed_event=latest_race,
            races_completed=races_completed,
            races_remaining=races_remaining,
            source=source,
            live=live,
            provisional=live and bool(open_drivers) and not official_live_result,
        )
        driver_rows = self._driver_standings(
            open_drivers,
            jolpica_drivers,
            driver_metadata,
            races,
            qualifying,
            sprints,
            races_completed,
            qualifying_available=isinstance(results[6], list),
            sprint_available=isinstance(results[7], list),
        )
        constructor_rows = self._constructor_standings(
            open_teams,
            jolpica_teams,
            driver_rows,
            races,
            qualifying,
            sprints,
            races_completed,
            qualifying_available=isinstance(results[6], list),
            sprint_available=isinstance(results[7], list),
        )
        driver_response = DriverStandingsResponse(standings=driver_rows, metadata=metadata)
        constructor_response = ConstructorStandingsResponse(
            standings=constructor_rows, metadata=metadata
        )
        summary = ChampionshipSummaryResponse(
            driver_leader=self._driver_leader(driver_rows),
            constructor_leader=self._constructor_leader(constructor_rows),
            closest_title_battle=self._closest_battle(driver_rows),
            races_completed=races_completed,
            races_remaining=races_remaining,
            latest_race=latest_race,
            next_race=next_race,
            metadata=metadata,
        )
        return driver_response, constructor_response, summary

    def _driver_standings(
        self,
        open_rows: list[dict[str, Any]],
        jolpica_rows: list[dict[str, Any]],
        metadata_rows: list[dict[str, Any]],
        races: list[dict[str, Any]],
        qualifying: list[dict[str, Any]],
        sprints: list[dict[str, Any]],
        races_completed: int,
        *,
        qualifying_available: bool,
        sprint_available: bool,
    ) -> list[DriverStanding]:
        metadata = {
            self._driver_key(row): row for row in metadata_rows if self._driver_key(row) is not None
        }
        jolpica = {
            self._driver_key(row): row for row in jolpica_rows if self._driver_key(row) is not None
        }
        stats = self._driver_stats(
            races,
            qualifying,
            sprints,
            qualifying_available=qualifying_available,
            sprint_available=sprint_available,
        )
        source_rows = open_rows or jolpica_rows
        standings: list[DriverStanding] = []
        for row in source_rows:
            key = self._driver_key(row)
            if key is None:
                continue
            open_metadata = metadata.get(key, {})
            fallback = jolpica.get(key, row if not open_rows else {})
            driver = fallback.get("Driver") if isinstance(fallback.get("Driver"), dict) else {}
            constructors = fallback.get("Constructors")
            fallback_team = (
                constructors[-1] if isinstance(constructors, list) and constructors else {}
            )
            values = stats.get(key, {})
            full_name = str(
                open_metadata.get("full_name")
                or " ".join(
                    part
                    for part in (
                        open_metadata.get("first_name"),
                        open_metadata.get("last_name"),
                    )
                    if part
                )
                or " ".join(
                    part for part in (driver.get("givenName"), driver.get("familyName")) if part
                )
                or open_metadata.get("broadcast_name")
                or driver.get("driverId")
                or f"Driver {key.split(':')[-1]}"
            )
            position = self._int(row.get("position_current") or row.get("position"))
            if position is None:
                continue
            points = self._float(row.get("points_current") or row.get("points")) or 0.0
            position_start = self._int(row.get("position_start"))
            points_start = self._float(row.get("points_start"))
            race_starts = values.get("race_starts")
            team_name = open_metadata.get("team_name") or fallback_team.get("name")
            team_id = fallback_team.get("constructorId") or (
                self._slug(str(team_name)) if team_name else None
            )
            standings.append(
                DriverStanding(
                    position=position,
                    driver_id=str(driver.get("driverId") or self._driver_id(key, full_name)),
                    driver_number=self._int(
                        row.get("driver_number")
                        or open_metadata.get("driver_number")
                        or driver.get("permanentNumber")
                    ),
                    first_name=self._text(
                        open_metadata.get("first_name") or driver.get("givenName")
                    ),
                    last_name=self._text(
                        open_metadata.get("last_name") or driver.get("familyName")
                    ),
                    full_name=full_name,
                    acronym=self._text(open_metadata.get("name_acronym") or driver.get("code")),
                    country_code=self._text(driver.get("nationality")),
                    headshot_url=self._text(open_metadata.get("headshot_url")),
                    team_id=self._text(team_id),
                    team_name=self._text(team_name),
                    team_colour=self._colour(open_metadata.get("team_colour")),
                    points=points,
                    wins=values.get("wins", self._int(fallback.get("wins"))),
                    podiums=values.get("podiums"),
                    top_5_finishes=values.get("top_5_finishes"),
                    top_10_finishes=values.get("top_10_finishes"),
                    poles=values.get("poles"),
                    fastest_laps=values.get("fastest_laps"),
                    race_starts=race_starts,
                    classified_finishes=values.get("classified_finishes"),
                    dnfs=values.get("dnfs"),
                    dsqs=values.get("dsqs"),
                    sprint_starts=values.get("sprint_starts"),
                    sprint_wins=values.get("sprint_wins"),
                    sprint_podiums=values.get("sprint_podiums"),
                    sprint_points=values.get("sprint_points"),
                    best_sprint_finish=values.get("best_sprint_finish"),
                    average_finish=values.get("average_finish"),
                    best_finish=values.get("best_finish"),
                    worst_classified_finish=values.get("worst_classified_finish"),
                    average_grid_position=values.get("average_grid_position"),
                    average_qualifying_position=values.get("average_qualifying_position"),
                    best_qualifying_result=values.get("best_qualifying_result"),
                    q3_appearances=values.get("q3_appearances"),
                    positions_gained_lost=values.get("positions_gained_lost"),
                    championship_position_change=(
                        position_start - position if position_start is not None else None
                    ),
                    points_change_from_previous_race=(
                        round(points - points_start, 3) if points_start is not None else None
                    ),
                    latest_race_finish=values.get("latest_race_finish"),
                    latest_race_points=values.get("latest_race_points"),
                    races_completed=races_completed,
                    points_per_race=(round(points / race_starts, 2) if race_starts else None),
                    podium_percentage=self._percentage(values.get("podiums"), race_starts),
                    points_finishing_percentage=self._percentage(
                        values.get("points_finishes"), race_starts
                    ),
                )
            )
        return sorted(standings, key=lambda item: (item.position, -item.points))

    def _driver_stats(
        self,
        races: list[dict[str, Any]],
        qualifying: list[dict[str, Any]],
        sprints: list[dict[str, Any]],
        *,
        qualifying_available: bool,
        sprint_available: bool,
    ) -> dict[str, dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: defaultdict(int)  # type: ignore[arg-type,return-value]
        )
        finishes: dict[str, list[int]] = defaultdict(list)
        grids: dict[str, list[int]] = defaultdict(list)
        qualifying_positions: dict[str, list[int]] = defaultdict(list)
        sprint_positions: dict[str, list[int]] = defaultdict(list)
        latest_round = max((self._int(row.get("_round")) or 0 for row in races), default=0)
        for row in races:
            key = self._driver_key(row)
            if key is None:
                continue
            bucket = buckets[key]
            position = self._position(row)
            grid = self._int(row.get("grid"))
            points = self._float(row.get("points")) or 0.0
            status = str(row.get("status") or "")
            position_text = str(row.get("positionText") or "")
            is_latest = (self._int(row.get("_round")) or 0) == latest_round
            if self._did_not_start(status, position_text):
                if is_latest:
                    bucket["latest_race_finish"] = None
                    bucket["latest_race_points"] = points
                continue
            bucket["race_starts"] += 1
            if position is not None and position_text.isdigit():
                finishes[key].append(position)
                bucket["classified_finishes"] += 1
                bucket["wins"] += position == 1
                bucket["podiums"] += position <= 3
                bucket["top_5_finishes"] += position <= 5
                bucket["top_10_finishes"] += position <= 10
                bucket["points_finishes"] += points > 0
            elif position_text.upper() == "D" or "disqual" in status.lower():
                bucket["dsqs"] += 1
            elif not self._is_classified_status(status):
                bucket["dnfs"] += 1
            if grid and grid > 0:
                grids[key].append(grid)
                if position is not None:
                    bucket["positions_gained_lost"] += grid - position
            fastest = row.get("FastestLap")
            if isinstance(fastest, dict) and self._int(fastest.get("rank")) == 1:
                bucket["fastest_laps"] += 1
            if is_latest:
                bucket["latest_race_finish"] = position
                bucket["latest_race_points"] = points
        for row in qualifying:
            key = self._driver_key(row)
            position = self._position(row)
            if key is None or position is None:
                continue
            qualifying_positions[key].append(position)
            buckets[key]["poles"] += position == 1
            buckets[key]["q3_appearances"] += position <= 10
        for row in sprints:
            key = self._driver_key(row)
            if key is None:
                continue
            position = self._position(row)
            bucket = buckets[key]
            if self._did_not_start(
                str(row.get("status") or ""), str(row.get("positionText") or "")
            ):
                continue
            bucket["sprint_starts"] += 1
            bucket["sprint_points"] += self._float(row.get("points")) or 0.0
            if position is not None:
                sprint_positions[key].append(position)
                bucket["sprint_wins"] += position == 1
                bucket["sprint_podiums"] += position <= 3
        for key, bucket in buckets.items():
            for field in (
                "wins",
                "podiums",
                "top_5_finishes",
                "top_10_finishes",
                "classified_finishes",
                "dnfs",
                "dsqs",
                "fastest_laps",
                "positions_gained_lost",
                "points_finishes",
            ):
                bucket.setdefault(field, 0)
            if qualifying_available:
                bucket.setdefault("poles", 0)
                bucket.setdefault("q3_appearances", 0)
            if sprint_available:
                for field in (
                    "sprint_starts",
                    "sprint_wins",
                    "sprint_podiums",
                    "sprint_points",
                ):
                    bucket.setdefault(field, 0)
            values = finishes[key]
            bucket["average_finish"] = self._average(values)
            bucket["best_finish"] = min(values) if values else None
            bucket["worst_classified_finish"] = max(values) if values else None
            bucket["average_grid_position"] = self._average(grids[key])
            positions = qualifying_positions[key]
            bucket["average_qualifying_position"] = self._average(positions)
            bucket["best_qualifying_result"] = min(positions) if positions else None
            bucket["best_sprint_finish"] = (
                min(sprint_positions[key]) if sprint_positions[key] else None
            )
        return {key: dict(value) for key, value in buckets.items()}

    def _constructor_standings(
        self,
        open_rows: list[dict[str, Any]],
        jolpica_rows: list[dict[str, Any]],
        drivers: list[DriverStanding],
        races: list[dict[str, Any]],
        qualifying: list[dict[str, Any]],
        sprints: list[dict[str, Any]],
        races_completed: int,
        *,
        qualifying_available: bool,
        sprint_available: bool,
    ) -> list[ConstructorStanding]:
        fallback = {self._team_key(row): row for row in jolpica_rows}
        stats = self._constructor_stats(
            races,
            qualifying,
            sprints,
            qualifying_available=qualifying_available,
            sprint_available=sprint_available,
        )
        by_team: dict[str, list[DriverStanding]] = defaultdict(list)
        for driver in drivers:
            by_team[self._team_key({"team_name": driver.team_name})].append(driver)
        contributions: dict[tuple[str, str], float] = defaultdict(float)
        for result in (*races, *sprints):
            driver_key = self._driver_key(result)
            if driver_key is not None:
                contributions[(self._team_key(result), driver_key)] += (
                    self._float(result.get("points")) or 0.0
                )
        standings: list[ConstructorStanding] = []
        for row in open_rows or jolpica_rows:
            key = self._team_key(row)
            secondary = fallback.get(key, row if not open_rows else {})
            constructor = secondary.get("Constructor")
            if not isinstance(constructor, dict):
                constructor = {}
            name = str(row.get("team_name") or constructor.get("name") or key)
            position = self._int(row.get("position_current") or row.get("position"))
            if position is None:
                continue
            values = stats.get(key, {})
            points = self._float(row.get("points_current") or row.get("points")) or 0.0
            points_start = self._float(row.get("points_start"))
            position_start = self._int(row.get("position_start"))
            pair = sorted(by_team.get(key, []), key=lambda item: (-item.points, item.position))
            standings.append(
                ConstructorStanding(
                    position=position,
                    constructor_id=str(constructor.get("constructorId") or self._slug(name)),
                    team_name=name,
                    team_colour=self._colour(row.get("team_colour"))
                    or next((driver.team_colour for driver in pair if driver.team_colour), None),
                    logo_url=self._team_logo_url(key),
                    points=points,
                    wins=values.get("wins", self._int(secondary.get("wins"))),
                    podiums=values.get("podiums"),
                    poles=values.get("poles"),
                    fastest_laps=values.get("fastest_laps"),
                    race_starts=races_completed,
                    double_podiums=values.get("double_podiums"),
                    dnfs=values.get("dnfs"),
                    sprint_wins=values.get("sprint_wins"),
                    sprint_podiums=values.get("sprint_podiums"),
                    average_finish=values.get("average_finish"),
                    average_points_per_event=(
                        round(points / races_completed, 2) if races_completed else None
                    ),
                    championship_position_change=(
                        position_start - position if position_start is not None else None
                    ),
                    points_change_from_previous_race=(
                        round(points - points_start, 3) if points_start is not None else None
                    ),
                    drivers=[
                        ChampionshipDriverRef(
                            driver_id=driver.driver_id,
                            driver_number=driver.driver_number,
                            full_name=driver.full_name,
                            acronym=driver.acronym,
                            headshot_url=driver.headshot_url,
                            points=contributions.get(
                                (
                                    key,
                                    f"number:{driver.driver_number}",
                                )
                            ),
                        )
                        for driver in pair
                    ],
                    races_completed=races_completed,
                )
            )
        return sorted(standings, key=lambda item: (item.position, -item.points))

    def _constructor_stats(
        self,
        races: list[dict[str, Any]],
        qualifying: list[dict[str, Any]],
        sprints: list[dict[str, Any]],
        *,
        qualifying_available: bool,
        sprint_available: bool,
    ) -> dict[str, dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: defaultdict(int)  # type: ignore[arg-type,return-value]
        )
        finishes: dict[str, list[int]] = defaultdict(list)
        podiums_by_event: dict[tuple[str, int], int] = defaultdict(int)
        for row in races:
            key = self._team_key(row)
            bucket = buckets[key]
            position = self._position(row)
            position_text = str(row.get("positionText") or "")
            status = str(row.get("status") or "")
            if self._did_not_start(status, position_text):
                continue
            if position is not None and position_text.isdigit():
                finishes[key].append(position)
                bucket["wins"] += position == 1
                bucket["podiums"] += position <= 3
                if position <= 3:
                    podiums_by_event[(key, self._int(row.get("_round")) or 0)] += 1
            elif position_text.upper() == "D" or "disqual" in status.lower():
                bucket["dnfs"] += 1
            elif not self._is_classified_status(status):
                bucket["dnfs"] += 1
            fastest = row.get("FastestLap")
            if isinstance(fastest, dict) and self._int(fastest.get("rank")) == 1:
                bucket["fastest_laps"] += 1
        for (key, _), count in podiums_by_event.items():
            buckets[key]["double_podiums"] += count >= 2
        for row in qualifying:
            if self._position(row) == 1:
                buckets[self._team_key(row)]["poles"] += 1
        for row in sprints:
            position = self._position(row)
            if position is None:
                continue
            key = self._team_key(row)
            buckets[key]["sprint_wins"] += position == 1
            buckets[key]["sprint_podiums"] += position <= 3
        for key, bucket in buckets.items():
            for field in (
                "wins",
                "podiums",
                "double_podiums",
                "dnfs",
                "fastest_laps",
            ):
                bucket.setdefault(field, 0)
            if qualifying_available:
                bucket.setdefault("poles", 0)
            if sprint_available:
                bucket.setdefault("sprint_wins", 0)
                bucket.setdefault("sprint_podiums", 0)
            bucket["average_finish"] = self._average(finishes[key])
        return {key: dict(value) for key, value in buckets.items()}

    async def _read_cached(self, name: str, model: type[BaseModel]) -> Any | None:
        try:
            raw = await self.redis.get(self._cache_key(name))
        except Exception:
            return None
        if not raw:
            return None
        try:
            value = model.model_validate_json(raw)
        except Exception:
            return None
        self._mark_cached(value)
        return value

    async def _write_cached(self, name: str, value: BaseModel, ttl: int) -> None:
        raw = value.model_dump_json()
        try:
            await self.redis.set(self._cache_key(name), raw, ex=ttl)
            await self.redis.set(self._cache_key(name, stale=True), raw, ex=604800)
        except Exception as exc:
            logger.warning("Championship cache write failed error=%s", type(exc).__name__)

    async def _restore_stale(self) -> bool:
        restored = False
        for name, model in (
            ("drivers", DriverStandingsResponse),
            ("constructors", ConstructorStandingsResponse),
            ("summary", ChampionshipSummaryResponse),
        ):
            try:
                raw = await self.redis.get(self._cache_key(name, stale=True))
                if not raw:
                    continue
                value = model.model_validate_json(raw)
                value.metadata.stale = True
                await self.redis.set(self._cache_key(name), value.model_dump_json(), ex=60)
                restored = True
            except Exception:
                continue
        return restored

    async def invalidate(self) -> None:
        """Expire current snapshots while retaining the last-known-good copies."""
        try:
            await self.redis.delete(
                self._cache_key("drivers"),
                self._cache_key("constructors"),
                self._cache_key("summary"),
            )
        except Exception as exc:
            logger.warning("Championship cache invalidation failed error=%s", type(exc).__name__)

    async def consume(self, event: NormalizedRaceEvent) -> None:
        if event.event_type in {RaceEventType.SESSION_FINISH, RaceEventType.SESSION_RESULT}:
            await self.invalidate()

    def _mark_cached(self, value: Any) -> None:
        value.metadata.cached = True
        value.metadata.cache_age_seconds = max(
            0, int((datetime.now(UTC) - value.metadata.generated_at).total_seconds())
        )

    def _cache_key(self, name: str, *, stale: bool = False) -> str:
        key = f"{self.CACHE_PREFIX.format(season=self.season)}:{name}"
        return f"{key}:last_available" if stale else key

    @staticmethod
    def _rows_or_empty(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @classmethod
    def _driver_key(cls, row: dict[str, Any]) -> str | None:
        driver = row.get("Driver") if isinstance(row.get("Driver"), dict) else row
        number = cls._int(driver.get("permanentNumber") or driver.get("driver_number"))
        if number is not None:
            return f"number:{number}"
        code = cls._text(driver.get("code") or driver.get("name_acronym"))
        if code:
            return f"code:{code.upper()}"
        identifier = cls._text(driver.get("driverId"))
        return f"id:{identifier.lower()}" if identifier else None

    @classmethod
    def _team_key(cls, row: dict[str, Any]) -> str:
        constructor = row.get("Constructor")
        if not isinstance(constructor, dict):
            constructor = row.get("constructor") if isinstance(row.get("constructor"), dict) else {}
        value = str(
            row.get("team_name")
            or constructor.get("name")
            or constructor.get("constructorId")
            or "unknown"
        ).lower()
        value = re.sub(r"\b(formula one|f1|racing|team)\b", "", value)
        key = cls._slug(value)
        return "racingbulls" if key in {"rb", "bulls", "racingbulls"} else key

    def _team_logo_url(self, team_key: str) -> str | None:
        asset_slug = {
            "haas": "haasf1team",
            "redbull": "redbullracing",
        }.get(team_key, team_key)
        supported = {
            "alpine",
            "astonmartin",
            "audi",
            "cadillac",
            "ferrari",
            "haasf1team",
            "mclaren",
            "mercedes",
            "racingbulls",
            "redbullracing",
            "williams",
        }
        if asset_slug not in supported:
            return None
        return (
            "https://media.formula1.com/image/upload/"
            "c_lfill,w_128/q_auto/v1740000001/common/f1/"
            f"{self.season}/{asset_slug}/{self.season}{asset_slug}logo.webp"
        )

    async def _safe_previous_standings(self, kind: str, round_number: int) -> list[dict[str, Any]]:
        try:
            if kind == "drivers":
                return await self.jolpica.fetch_driver_standings(self.season, round_number)
            return await self.jolpica.fetch_constructor_standings(self.season, round_number)
        except Exception:
            return []

    def _add_previous_snapshot(
        self,
        current: list[dict[str, Any]],
        previous: list[dict[str, Any]],
        *,
        driver: bool,
    ) -> None:
        key_for = self._driver_key if driver else self._team_key
        prior = {key_for(row): row for row in previous}
        for row in current:
            older = prior.get(key_for(row))
            if older is None:
                continue
            row["position_start"] = older.get("position")
            row["points_start"] = older.get("points")

    @staticmethod
    def _driver_id(key: str, full_name: str) -> str:
        if key.startswith("id:"):
            return key[3:]
        return ChampionshipService._slug(full_name) or key.replace(":", "-")

    @staticmethod
    def _position(row: dict[str, Any]) -> int | None:
        return ChampionshipService._int(row.get("position"))

    @staticmethod
    def _latest_race_name(rows: list[dict[str, Any]]) -> str | None:
        if not rows:
            return None
        latest = max(rows, key=lambda row: ChampionshipService._int(row.get("_round")) or 0)
        return ChampionshipService._text(latest.get("_race_name"))

    @staticmethod
    def _driver_leader(rows: list[DriverStanding]) -> ChampionshipLeader | None:
        if not rows:
            return None
        leader = rows[0]
        advantage = leader.points - rows[1].points if len(rows) > 1 else None
        return ChampionshipLeader(
            id=leader.driver_id,
            name=leader.full_name,
            points=leader.points,
            advantage=advantage,
            headshot_url=leader.headshot_url,
            team_colour=leader.team_colour,
        )

    @staticmethod
    def _constructor_leader(rows: list[ConstructorStanding]) -> ChampionshipLeader | None:
        if not rows:
            return None
        leader = rows[0]
        advantage = leader.points - rows[1].points if len(rows) > 1 else None
        return ChampionshipLeader(
            id=leader.constructor_id,
            name=leader.team_name,
            points=leader.points,
            advantage=advantage,
            team_colour=leader.team_colour,
        )

    @staticmethod
    def _closest_battle(rows: list[DriverStanding]) -> dict[str, Any] | None:
        if len(rows) < 2:
            return None
        first, second = rows[:2]
        return {
            "leader": first.full_name,
            "challenger": second.full_name,
            "points_gap": round(first.points - second.points, 3),
            "top_three_gap": round(first.points - rows[2].points, 3) if len(rows) > 2 else None,
        }

    @staticmethod
    def _is_classified_status(status: str) -> bool:
        return status == "Finished" or bool(re.fullmatch(r"\+\d+ Laps?", status))

    @staticmethod
    def _did_not_start(status: str, position_text: str) -> bool:
        return "did not start" in status.lower() or position_text.upper() in {"DNS", "W"}

    @staticmethod
    def _average(values: list[int]) -> float | None:
        return round(sum(values) / len(values), 2) if values else None

    @staticmethod
    def _percentage(value: Any, total: Any) -> float | None:
        return round(float(value) * 100 / float(total), 1) if total else None

    @staticmethod
    def _colour(value: Any) -> str | None:
        text = ChampionshipService._text(value)
        if not text:
            return None
        clean = text.lstrip("#")
        return f"#{clean}" if re.fullmatch(r"[0-9a-fA-F]{6}", clean) else None

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    @staticmethod
    def _text(value: Any) -> str | None:
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None
