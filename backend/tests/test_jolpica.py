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
