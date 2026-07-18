# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.circuit_intelligence import (
    CircuitIntelligenceService,
    CircuitWeatherService,
)

CURRENT_2026_CIRCUITS = {
    "Albert Park Grand Prix Circuit",
    "Shanghai International Circuit",
    "Suzuka Circuit",
    "Miami International Autodrome",
    "Circuit Gilles Villeneuve",
    "Circuit de Monaco",
    "Circuit de Barcelona-Catalunya",
    "Red Bull Ring",
    "Silverstone Circuit",
    "Circuit de Spa-Francorchamps",
    "Hungaroring",
    "Circuit Park Zandvoort",
    "Autodromo Nazionale di Monza",
    "Madring",
    "Baku City Circuit",
    "Marina Bay Street Circuit",
    "Circuit of the Americas",
    "Autódromo Hermanos Rodríguez",
    "Autódromo José Carlos Pace",
    "Las Vegas Strip Street Circuit",
    "Losail International Circuit",
    "Yas Marina Circuit",
}


def test_all_2026_circuits_have_complete_verified_profiles() -> None:
    service = CircuitIntelligenceService()

    assert service.supported_circuits == CURRENT_2026_CIRCUITS
    for circuit_name in CURRENT_2026_CIRCUITS:
        profile = service.for_circuit(circuit_name)
        assert profile.circuit_name == circuit_name
        assert len(profile.records) == 3
        assert len(profile.facts) >= 2
        assert profile.source_url and profile.source_url.startswith("https://www.formula1.com/")


@pytest.mark.asyncio
async def test_weather_uses_latest_openf1_sample_and_normalises_values() -> None:
    provider = AsyncMock()
    provider.weather.return_value = [
        {"date": "2026-07-18T13:00:00Z", "air_temperature": 21.2},
        {
            "date": "2026-07-18T13:02:00Z",
            "air_temperature": "22.5",
            "track_temperature": 34.1,
            "rainfall": 1,
            "humidity": 71,
            "pressure": 1008.2,
            "wind_speed": 3.4,
            "wind_direction": 247,
        },
    ]

    weather = await CircuitWeatherService(provider).for_session("9876")

    assert weather.available is True
    assert weather.air_temperature_c == 22.5
    assert weather.track_temperature_c == 34.1
    assert weather.rainfall is True
    assert weather.humidity_percent == 71
    assert weather.pressure_mbar == 1008.2
    assert weather.wind_speed_mps == 3.4
    assert weather.wind_direction_degrees == 247
    assert weather.sampled_at is not None
    provider.weather.assert_awaited_once_with(session_key="9876")


@pytest.mark.asyncio
async def test_weather_gracefully_handles_future_and_provider_failure() -> None:
    provider = AsyncMock()
    service = CircuitWeatherService(provider)

    future = await service.for_session(None)
    assert future.available is False
    assert "publishes" in future.notice
    provider.weather.assert_not_awaited()

    provider.weather.side_effect = RuntimeError("provider unavailable")
    unavailable = await service.for_session("123")
    assert unavailable.available is False
    assert "temporarily unavailable" in unavailable.notice


@pytest.mark.asyncio
async def test_weather_times_out_without_blocking_room_data() -> None:
    async def slow_weather(**filters: object) -> list[dict[str, object]]:
        del filters
        await asyncio.sleep(0.05)
        return []

    provider = AsyncMock()
    provider.weather.side_effect = slow_weather

    weather = await CircuitWeatherService(provider, timeout_seconds=0.001).for_session("slow")

    assert weather.available is False
    assert "temporarily unavailable" in weather.notice
