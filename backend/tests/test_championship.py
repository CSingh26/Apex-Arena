# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.schemas import DriverStandingsResponse
from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.services.championship import ChampionshipService


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls = 0

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **_: object) -> bool:
        self.values[key] = value
        self.set_calls += 1
        return True

    async def delete(self, *keys: str) -> int:
        removed = sum(key in self.values for key in keys)
        for key in keys:
            self.values.pop(key, None)
        return removed


def driver(number: str, driver_id: str, code: str, given: str, family: str) -> dict:
    return {
        "permanentNumber": number,
        "driverId": driver_id,
        "code": code,
        "givenName": given,
        "familyName": family,
    }


def result(
    number: str,
    driver_id: str,
    code: str,
    given: str,
    family: str,
    position: int,
    points: float,
    *,
    grid: int,
    team_id: str = "orange",
    team_name: str = "Orange Racing",
    status: str = "Finished",
    position_text: str | None = None,
    round_number: int = 1,
    fastest: bool = False,
) -> dict:
    row = {
        "Driver": driver(number, driver_id, code, given, family),
        "Constructor": {"constructorId": team_id, "name": team_name},
        "position": str(position),
        "positionText": position_text or str(position),
        "points": str(points),
        "grid": str(grid),
        "status": status,
        "_round": str(round_number),
        "_race_name": "Test Grand Prix",
    }
    if fastest:
        row["FastestLap"] = {"rank": "1"}
    return row


def meeting(status: MeetingLifecycleStatus) -> RaceMeeting:
    return RaceMeeting(
        season_year=2026,
        round_number=1,
        race_name="Test Grand Prix",
        circuit_id="test",
        circuit_name="Test Circuit",
        locality="Test City",
        country="Testland",
        race_date=date(2026, 3, 8),
        race_start=datetime(2026, 3, 8, 12, tzinfo=UTC),
        status=status,
    )


def service_fixture(*, openf1_available: bool = True, live: bool = False):
    open_driver_rows = [
        {
            "driver_number": 4,
            "position_current": 1,
            "position_start": 2,
            "points_current": 25,
            "points_start": 0,
        },
        {
            "driver_number": 81,
            "position_current": 2,
            "position_start": 1,
            "points_current": 18,
            "points_start": 0,
        },
    ]
    open_team_rows = [
        {
            "team_name": "Orange Racing",
            "position_current": 1,
            "position_start": 2,
            "points_current": 43,
            "points_start": 0,
        }
    ]
    metadata = [
        {
            "driver_number": 4,
            "first_name": "Alex",
            "last_name": "North",
            "full_name": "Alex North",
            "name_acronym": "NOR",
            "team_name": "Orange Racing",
            "team_colour": "ff8700",
            "headshot_url": "https://example.test/nor.png",
        },
        {
            "driver_number": 81,
            "first_name": "Pat",
            "last_name": "East",
            "full_name": "Pat East",
            "name_acronym": "EAS",
            "team_name": "Orange Racing",
            "team_colour": "ff8700",
        },
    ]
    jolpica_drivers = [
        {
            "position": "2",
            "points": "18",
            "wins": "0",
            "Driver": driver("81", "east", "EAS", "Pat", "East"),
            "Constructors": [{"constructorId": "orange", "name": "Orange Racing"}],
        },
        {
            "position": "1",
            "points": "25",
            "wins": "1",
            "Driver": driver("4", "north", "NOR", "Alex", "North"),
            "Constructors": [{"constructorId": "orange", "name": "Orange Racing"}],
        },
    ]
    jolpica_teams = [
        {
            "position": "1",
            "points": "43",
            "wins": "1",
            "Constructor": {"constructorId": "orange", "name": "Orange Racing"},
        }
    ]
    race_rows = [
        result("4", "north", "NOR", "Alex", "North", 1, 25, grid=3, fastest=True),
        result("81", "east", "EAS", "Pat", "East", 2, 18, grid=1),
    ]
    qualifying = [
        result("4", "north", "NOR", "Alex", "North", 2, 0, grid=2),
        result("81", "east", "EAS", "Pat", "East", 1, 0, grid=1),
    ]
    sprint = [result("4", "north", "NOR", "Alex", "North", 1, 8, grid=1)]
    unavailable = RuntimeError("unavailable")
    openf1 = SimpleNamespace(
        championship_drivers=AsyncMock(
            return_value=open_driver_rows if openf1_available else unavailable
        ),
        championship_teams=AsyncMock(
            return_value=open_team_rows if openf1_available else unavailable
        ),
        drivers=AsyncMock(return_value=metadata if openf1_available else unavailable),
    )
    if not openf1_available:
        openf1.championship_drivers.side_effect = unavailable
        openf1.championship_teams.side_effect = unavailable
        openf1.drivers.side_effect = unavailable
    jolpica = SimpleNamespace(
        fetch_driver_standings=AsyncMock(return_value=jolpica_drivers),
        fetch_constructor_standings=AsyncMock(return_value=jolpica_teams),
        fetch_race_results=AsyncMock(return_value=race_rows),
        fetch_qualifying_results=AsyncMock(return_value=qualifying),
        fetch_sprint_results=AsyncMock(return_value=sprint),
    )
    season = SimpleNamespace(
        calendar=AsyncMock(
            return_value=[
                meeting(MeetingLifecycleStatus.LIVE if live else MeetingLifecycleStatus.COMPLETED)
            ]
        )
    )
    redis = MemoryRedis()
    service = ChampionshipService(
        season=2026,
        openf1=openf1,
        jolpica=jolpica,
        season_service=season,
        redis=redis,
    )
    return service, openf1, jolpica, redis


@pytest.mark.asyncio
async def test_normalizes_and_aggregates_driver_standings() -> None:
    service, _, _, _ = service_fixture()

    response = await service.drivers()

    assert [row.driver_id for row in response.standings] == ["north", "east"]
    leader = response.standings[0]
    assert leader.full_name == "Alex North"
    assert leader.team_colour == "#ff8700"
    assert leader.wins == 1
    assert leader.podiums == 1
    assert leader.fastest_laps == 1
    assert leader.poles == 0
    assert leader.sprint_wins == 1
    assert leader.sprint_points == 8
    assert leader.average_finish == 1
    assert leader.average_qualifying_position == 2
    assert leader.positions_gained_lost == 2
    assert leader.championship_position_change == 1
    assert leader.points_change_from_previous_race == 25


@pytest.mark.asyncio
async def test_constructor_stats_do_not_double_count_events() -> None:
    service, _, _, _ = service_fixture()

    response = await service.constructors()

    team = response.standings[0]
    assert team.constructor_id == "orange"
    assert team.logo_url is None
    assert team.wins == 1
    assert team.podiums == 2
    assert team.double_podiums == 1
    assert team.poles == 1
    assert team.fastest_laps == 1
    assert team.sprint_wins == 1
    assert team.race_starts == 1
    assert [item.driver_id for item in team.drivers] == ["north", "east"]


@pytest.mark.asyncio
async def test_dnf_and_dsq_are_separate_from_classified_finishes() -> None:
    service, _, jolpica, _ = service_fixture()
    jolpica.fetch_race_results.return_value = [
        result(
            "4",
            "north",
            "NOR",
            "Alex",
            "North",
            20,
            0,
            grid=4,
            status="Engine",
            position_text="R",
        ),
        result(
            "81",
            "east",
            "EAS",
            "Pat",
            "East",
            20,
            0,
            grid=2,
            status="Disqualified",
            position_text="D",
        ),
    ]

    response = await service.drivers()

    assert response.standings[0].dnfs == 1
    assert response.standings[0].dsqs == 0
    assert response.standings[1].dnfs == 0
    assert response.standings[1].dsqs == 1


@pytest.mark.asyncio
async def test_uses_jolpica_fallback_and_sorts_by_position() -> None:
    service, _, _, _ = service_fixture(openf1_available=False)

    response = await service.drivers()

    assert response.metadata.source == "Jolpica"
    assert [row.position for row in response.standings] == [1, 2]
    assert response.standings[0].driver_id == "north"
    assert response.standings[0].championship_position_change is None


@pytest.mark.asyncio
async def test_jolpica_fallback_uses_previous_round_for_movement_and_points() -> None:
    service, _, jolpica, _ = service_fixture(openf1_available=False)
    current_drivers = jolpica.fetch_driver_standings.return_value
    current_teams = jolpica.fetch_constructor_standings.return_value
    previous_drivers = [
        {**row, "position": "2" if row["Driver"]["driverId"] == "north" else "1", "points": "0"}
        for row in current_drivers
    ]
    previous_teams = [{**current_teams[0], "position": "2", "points": "0"}]

    async def driver_standings(_: int, round_number: int | None = None):
        return previous_drivers if round_number == 1 else current_drivers

    async def constructor_standings(_: int, round_number: int | None = None):
        return previous_teams if round_number == 1 else current_teams

    jolpica.fetch_driver_standings.side_effect = driver_standings
    jolpica.fetch_constructor_standings.side_effect = constructor_standings
    jolpica.fetch_race_results.return_value = [
        {**row, "_round": "2"} for row in jolpica.fetch_race_results.return_value
    ]

    response = await service.drivers()

    assert response.standings[0].championship_position_change == 1
    assert response.standings[0].points_change_from_previous_race == 25


@pytest.mark.asyncio
async def test_partial_statistics_failure_keeps_championship_available() -> None:
    service, _, jolpica, _ = service_fixture()
    jolpica.fetch_qualifying_results.side_effect = RuntimeError("qualifying unavailable")
    jolpica.fetch_sprint_results.side_effect = RuntimeError("sprint unavailable")

    response = await service.drivers()

    assert response.standings[0].points == 25
    assert response.standings[0].wins == 1
    assert response.standings[0].poles is None
    assert response.standings[0].sprint_wins is None


@pytest.mark.asyncio
async def test_cache_prevents_duplicate_provider_requests() -> None:
    service, openf1, _, redis = service_fixture()

    first = await service.drivers()
    second = await service.drivers()

    assert isinstance(first, DriverStandingsResponse)
    assert second.metadata.cached is True
    assert redis.set_calls == 6
    openf1.championship_drivers.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_openf1_snapshot_is_provisional() -> None:
    service, _, jolpica, _ = service_fixture(live=True)
    jolpica.fetch_race_results.return_value = []

    response = await service.summary()

    assert response.metadata.live is True
    assert response.metadata.provisional is True
    assert response.driver_leader is not None
    assert response.driver_leader.advantage == 7


@pytest.mark.asyncio
async def test_live_window_returns_to_official_when_event_results_exist() -> None:
    service, _, _, _ = service_fixture(live=True)

    response = await service.summary()

    assert response.metadata.live is True
    assert response.metadata.provisional is False


@pytest.mark.asyncio
async def test_dns_is_not_counted_as_a_start_or_dnf() -> None:
    service, _, jolpica, _ = service_fixture()
    jolpica.fetch_race_results.return_value = [
        result(
            "4",
            "north",
            "NOR",
            "Alex",
            "North",
            20,
            0,
            grid=0,
            status="Did not start",
            position_text="W",
        ),
        result("81", "east", "EAS", "Pat", "East", 1, 25, grid=1),
    ]

    response = await service.drivers()

    north = next(row for row in response.standings if row.driver_id == "north")
    assert north.race_starts is None
    assert north.dnfs == 0


def test_current_racing_bulls_names_normalize_to_one_constructor() -> None:
    assert ChampionshipService._team_key({"team_name": "Racing Bulls"}) == "racingbulls"
    assert (
        ChampionshipService._team_key(
            {"Constructor": {"constructorId": "rb", "name": "RB F1 Team"}}
        )
        == "racingbulls"
    )


def test_current_constructor_logo_uses_official_f1_asset() -> None:
    service, _, _, _ = service_fixture()

    assert service._team_logo_url("mercedes") == (
        "https://media.formula1.com/image/upload/c_lfill,w_128/q_auto/"
        "v1740000001/common/f1/2026/mercedes/2026mercedeslogo.webp"
    )
    assert service._team_logo_url("unknown-constructor") is None


@pytest.mark.asyncio
async def test_missing_constructor_cache_key_triggers_complete_refresh() -> None:
    service, openf1, _, redis = service_fixture()
    await service.drivers()
    redis.values.pop("apexarena:2026:standings:constructors")

    response = await service.constructors()

    assert response.standings[0].team_name == "Orange Racing"
    assert openf1.championship_drivers.await_count == 2


@pytest.mark.asyncio
async def test_cache_invalidation_keeps_last_available_snapshot() -> None:
    service, _, _, redis = service_fixture()
    await service.drivers()

    await service.invalidate()

    assert "apexarena:2026:standings:drivers" not in redis.values
    assert "apexarena:2026:standings:constructors" not in redis.values
    assert "apexarena:2026:standings:summary" not in redis.values
    assert "apexarena:2026:standings:drivers:last_available" in redis.values


@pytest.mark.asyncio
async def test_last_available_cache_survives_total_provider_failure() -> None:
    service, openf1, jolpica, redis = service_fixture()
    await service.drivers()
    for name in ("drivers", "constructors", "summary"):
        redis.values.pop(f"apexarena:2026:standings:{name}")
    failure = RuntimeError("all providers unavailable")
    openf1.championship_drivers.side_effect = failure
    openf1.championship_teams.side_effect = failure
    openf1.drivers.side_effect = failure
    jolpica.fetch_driver_standings.side_effect = failure
    jolpica.fetch_constructor_standings.side_effect = failure
    jolpica.fetch_race_results.side_effect = failure
    jolpica.fetch_qualifying_results.side_effect = failure
    jolpica.fetch_sprint_results.side_effect = failure
    service.season_service.calendar.side_effect = failure

    response = await service.drivers()

    assert response.metadata.stale is True
    assert response.metadata.cached is True
    assert response.standings[0].driver_id == "north"
