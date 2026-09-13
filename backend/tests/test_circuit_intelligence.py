# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from types import SimpleNamespace
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


async def test_weather_cache_coalesces_and_labels_bounded_stale_data(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(
        "app.services.circuit_intelligence.time",
        SimpleNamespace(monotonic=lambda: clock[0]),
        raising=False,
    )
    provider = AsyncMock()
    provider.weather.return_value = [{"date": "2026-07-18T13:00:00Z", "air_temperature": 21.2}]
    service = CircuitWeatherService(provider)
    first, second = await asyncio.gather(service.for_session("9876"), service.for_session("9876"))
    assert provider.weather.await_count == 1
    first.air_temperature_c = 99
    assert second.air_temperature_c == 21.2
    clock[0] += 31
    provider.weather.side_effect = RuntimeError("synthetic unavailable")
    stale = await service.for_session("9876")
    assert stale.available and stale.source_stale
    assert stale.source_age_seconds == 31
    assert stale.air_temperature_c == 21.2
    await service.for_session("9876")
    assert provider.weather.await_count == 2
    clock[0] += 300
    assert not (await service.for_session("9876")).available


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), "Infinity", "NaN", 10**400])
async def test_weather_rejects_nonfinite_measurements(value):
    provider = AsyncMock()
    provider.weather.return_value = [{"date": "2026-07-18T13:00:00Z", "air_temperature": value}]
    assert not (await CircuitWeatherService(provider).for_session("9876")).available


async def test_weather_cache_bounds_keys_and_coalesces_negative_results():
    provider = AsyncMock()
    provider.weather.return_value = []
    service = CircuitWeatherService(provider)
    results = await asyncio.gather(service.for_session("empty"), service.for_session("empty"))
    assert all(not result.available for result in results)
    assert provider.weather.await_count == 1
    for number in range(65):
        await service.for_session(str(number))
    await service.for_session("empty")
    assert provider.weather.await_count == 67


async def test_weather_cancellation_releases_fetch_lock_without_caching():
    provider = AsyncMock()
    entered = asyncio.Event()

    async def blocked(**filters):
        entered.set()
        await asyncio.Event().wait()

    provider.weather.side_effect = blocked
    service = CircuitWeatherService(provider)
    task = asyncio.create_task(service.for_session("9876"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    provider.weather.side_effect = None
    provider.weather.return_value = []
    await asyncio.wait_for(service.for_session("9876"), timeout=1)
    assert provider.weather.await_count == 2


async def test_weather_cache_hit_does_not_wait_for_another_session_fetch():
    provider = AsyncMock()
    provider.weather.return_value = [{"air_temperature": 21.2}]
    service = CircuitWeatherService(provider, timeout_seconds=1)
    await service.for_session("cached")
    entered = asyncio.Event()

    async def blocked(**filters):
        entered.set()
        await asyncio.Event().wait()

    provider.weather.side_effect = blocked
    task = asyncio.create_task(service.for_session("cold"))
    await entered.wait()
    try:
        result = await asyncio.wait_for(service.for_session("cached"), timeout=0.05)
        assert result.available
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_weather_deadline_includes_waiting_for_cache_lock():
    provider = AsyncMock()
    service = CircuitWeatherService(provider, timeout_seconds=0.005)
    async with service._lock:
        result = await asyncio.wait_for(service.for_session("queued"), timeout=0.1)
    assert not result.available
    assert "temporarily unavailable" in result.notice
    provider.weather.assert_not_awaited()


@pytest.mark.parametrize("warm", [False, True])
async def test_weather_total_deadline_retains_retry_cooldown(monkeypatch, warm):
    clock = [1000.0]
    monkeypatch.setattr(
        "app.services.circuit_intelligence.time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    provider = AsyncMock()
    provider.weather.return_value = [{"air_temperature": 21.2}]
    service = CircuitWeatherService(provider, timeout_seconds=0.005)
    if warm:
        await service.for_session("9876")
        clock[0] += 31

    async def blocked(**filters):
        await asyncio.Event().wait()

    provider.weather.side_effect = blocked
    attempts = provider.weather.await_count
    first = await service.for_session("9876")
    second = await service.for_session("9876")
    assert first.available is warm and second.available is warm
    assert provider.weather.await_count == attempts + 1
