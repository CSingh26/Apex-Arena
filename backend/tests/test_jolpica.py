# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.core.settings import Settings
from app.domain.models import MeetingLifecycleStatus
from app.providers.jolpica import JolpicaClient
from app.services.season import SeasonService


def calendar_payload() -> dict[str, object]:
    return {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "1",
                        "raceName": "Australian Grand Prix",
                        "url": "https://example.test/australia",
                        "Circuit": {
                            "circuitId": "albert_park",
                            "circuitName": "Albert Park Grand Prix Circuit",
                            "Location": {"locality": "Melbourne", "country": "Australia"},
                        },
                        "date": "2026-03-08",
                        "time": "04:00:00Z",
                    },
                    {
                        "season": "2026",
                        "round": "13",
                        "raceName": "Belgian Grand Prix",
                        "url": "https://example.test/belgium",
                        "Circuit": {
                            "circuitId": "spa",
                            "circuitName": "Circuit de Spa-Francorchamps",
                            "Location": {"locality": "Spa", "country": "Belgium"},
                        },
                        "date": "2026-07-19",
                        "time": "13:00:00Z",
                        "FirstPractice": {"date": "2026-07-17", "time": "11:30:00Z"},
                        "SprintQualifying": {"date": "2026-07-17", "time": "15:30:00Z"},
                        "Sprint": {"date": "2026-07-18", "time": "10:00:00Z"},
                        "Qualifying": {"date": "2026-07-18", "time": "14:00:00Z"},
                    },
                ]
            }
        }
    }


@pytest.mark.asyncio
async def test_calendar_normalization_highlights_spa(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/2026.json")
        return httpx.Response(200, json=calendar_payload())

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient("https://api.example.test", http_client)
    service = SeasonService(settings, client)

    races = await service.calendar(2026, now=datetime(2026, 7, 15, tzinfo=UTC))

    assert races[0].status == MeetingLifecycleStatus.COMPLETED
    assert races[1].status == MeetingLifecycleStatus.UPCOMING
    assert races[1].is_target is True
    assert races[1].circuit_name == "Circuit de Spa-Francorchamps"
    assert [session.name for session in races[1].sessions] == [
        "Practice 1",
        "Sprint Qualifying",
        "Sprint",
        "Qualifying",
        "Race",
    ]
    assert races[1].sessions[-1].starts_at == races[1].race_start
    await http_client.aclose()


@pytest.mark.asyncio
async def test_fetch_race_results_when_available() -> None:
    payload = calendar_payload()
    payload["MRData"]["RaceTable"]["Races"][0]["Results"] = [{"position": "1"}]  # type: ignore[index]

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient("https://api.example.test", http_client)

    results = await client.fetch_race_results(2026, 1)

    assert results == [{"position": "1"}]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_fetch_season_results_flattens_races_with_event_context() -> None:
    payload = calendar_payload()
    races = payload["MRData"]["RaceTable"]["Races"]  # type: ignore[index]
    races[0]["Results"] = [{"position": "1", "Driver": {"driverId": "driver-a"}}]
    races[1]["Results"] = [{"position": "2", "Driver": {"driverId": "driver-b"}}]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/2026/results.json")
        assert request.url.params["limit"] == "100"
        assert request.url.params["offset"] == "0"
        return httpx.Response(200, json=payload)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient("https://api.example.test", http_client)

    results = await client.fetch_race_results(2026)

    assert results[0]["Driver"]["driverId"] == "driver-a"
    assert results[0]["_round"] == "1"
    assert results[0]["_race_name"] == "Australian Grand Prix"
    assert results[1]["_round"] == "13"
    assert results[1]["_date"] == "2026-07-19"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_fetch_season_results_follows_jolpica_pagination() -> None:
    offsets: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        rows = [
            {
                "position": str(index + 1),
                "Driver": {"driverId": f"driver-{offset + index}"},
            }
            for index in range(2 if offset < 4 else 1)
        ]
        return httpx.Response(
            200,
            json={
                "MRData": {
                    "limit": "2",
                    "offset": str(offset),
                    "total": "5",
                    "RaceTable": {
                        "Races": [
                            {
                                "season": "2026",
                                "round": str(offset // 2 + 1),
                                "raceName": "Test Grand Prix",
                                "date": "2026-03-08",
                                "Results": rows,
                            }
                        ]
                    },
                }
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient(
        "https://api.example.test",
        http_client,
        min_request_interval_seconds=0,
    )

    results = await client.fetch_race_results(2026)

    assert offsets == [0, 2, 4]
    assert len(results) == 5
    assert results[-1]["Driver"]["driverId"] == "driver-4"
    assert results[-1]["_round"] == "3"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_fetch_qualifying_and_sprint_results_use_bulk_resources() -> None:
    payload = calendar_payload()
    races = payload["MRData"]["RaceTable"]["Races"]  # type: ignore[index]
    races[0]["QualifyingResults"] = [{"position": "1"}]
    races[1]["SprintResults"] = [{"position": "1"}]
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=payload)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient("https://api.example.test", http_client)

    qualifying = await client.fetch_qualifying_results(2026)
    sprint = await client.fetch_sprint_results(2026)

    assert qualifying == [
        {
            "position": "1",
            "_season": "2026",
            "_round": "1",
            "_race_name": "Australian Grand Prix",
            "_date": "2026-03-08",
        }
    ]
    assert sprint[0]["_round"] == "13"
    assert paths == ["/2026/qualifying.json", "/2026/sprint.json"]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_fetch_current_driver_and_constructor_standings() -> None:
    driver_payload = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [{"DriverStandings": [{"position": "1", "points": "42"}]}]
            }
        }
    }
    constructor_payload = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [{"ConstructorStandings": [{"position": "1", "points": "70"}]}]
            }
        }
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/driverstandings.json"):
            return httpx.Response(200, json=driver_payload)
        return httpx.Response(200, json=constructor_payload)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient("https://api.example.test", http_client)

    drivers = await client.fetch_driver_standings(2026)
    constructors = await client.fetch_constructor_standings(2026, 3)

    assert drivers == [{"position": "1", "points": "42"}]
    assert constructors == [{"position": "1", "points": "70"}]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_invalid_standings_shape_raises_safe_payload_error() -> None:
    from app.providers.jolpica import JolpicaPayloadError

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"MRData": {}})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.test/"
    )
    client = JolpicaClient("https://api.example.test", http_client)

    with pytest.raises(JolpicaPayloadError, match="unexpected standings shape"):
        await client.fetch_driver_standings(2026)

    await http_client.aclose()
