# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import math

import httpx
import pytest

from app.providers.jolpica import JolpicaClient, JolpicaPayloadError
from app.providers.openf1 import OpenF1RestClient


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["jolpica", "openf1"])
@pytest.mark.parametrize(
    "header", ["inf", "nan", "9999999999", "-1", "invalid", "2", "Wed, 21 Oct 2015 07:28:00 GMT"]
)
async def test_provider_retry_after_is_finite_and_capped(settings, monkeypatch, provider, header):
    sleeps = []
    attempts = 0

    async def sleep(delay):
        assert math.isfinite(delay) and 0 <= delay <= 5
        sleeps.append(delay)

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": header})
        return httpx.Response(
            200, json=[] if provider == "openf1" else {"MRData": {"RaceTable": {"Races": []}}}
        )

    monkeypatch.setattr("asyncio.sleep", sleep)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/"
    ) as http:
        if provider == "openf1":
            client = OpenF1RestClient(settings, http, min_request_interval_seconds=0)
            result = await client.meetings(year=2026)
        else:
            client = JolpicaClient("https://example.test", http, min_request_interval_seconds=0)
            result = await client.fetch_calendar(2026)
    assert result == []
    assert attempts == 2
    assert len(sleeps) == 1
    if header == "2":
        assert sleeps == [2]


@pytest.mark.asyncio
async def test_jolpica_network_exhaustion_preserves_last_error(monkeypatch):
    attempts = 0
    sleeps = []
    error = httpx.ConnectError("synthetic exhausted")

    async def sleep(delay):
        sleeps.append(delay)

    async def handler(request):
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr("asyncio.sleep", sleep)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/"
    ) as http:
        client = JolpicaClient("https://example.test", http, min_request_interval_seconds=0)
        with pytest.raises(httpx.ConnectError) as caught:
            await client.fetch_calendar(2026)
    assert caught.value is error
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_jolpica_retry_cancellation_releases_request_lock(monkeypatch):
    attempts = 0

    async def sleep(delay):
        raise asyncio.CancelledError

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"MRData": {"RaceTable": {"Races": []}}})

    monkeypatch.setattr("asyncio.sleep", sleep)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/"
    ) as http:
        client = JolpicaClient("https://example.test", http, min_request_interval_seconds=0)
        with pytest.raises(asyncio.CancelledError):
            await client.fetch_calendar(2026)
        assert attempts == 1
        assert await asyncio.wait_for(client.fetch_calendar(2026), timeout=1) == []
    assert attempts == 2


@pytest.mark.asyncio
async def test_jolpica_retries_transient_network_failure_without_losing_success():
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("synthetic connection loss", request=request)
        return httpx.Response(200, json={"MRData": {"RaceTable": {"Races": []}}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/"
    ) as http:
        client = JolpicaClient("https://example.test", http, min_request_interval_seconds=0)
        assert await client.fetch_calendar(2026) == []
    assert attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"total": "1000000000", "limit": "1"},
        {"total": "-1", "limit": "100"},
        {"total": "2", "limit": "0"},
    ],
)
async def test_jolpica_rejects_unbounded_or_invalid_pagination_before_fanout(metadata):
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        assert attempts == 1, "invalid pagination must not launch another provider request"
        return httpx.Response(200, json={"MRData": {**metadata, "RaceTable": {"Races": []}}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/"
    ) as http:
        client = JolpicaClient("https://example.test", http, min_request_interval_seconds=0)
        with pytest.raises(JolpicaPayloadError, match="pagination"):
            await client.fetch_race_results(2026)
    assert attempts == 1


@pytest.mark.parametrize("field", ["total", "limit"])
async def test_jolpica_classifies_numeric_overflow_metadata(field):
    async def handler(request):
        return httpx.Response(200, content=('{"MRData":{"' + field + '":1e400}}').encode())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://example.test/"
    ) as http:
        client = JolpicaClient("https://example.test", http, min_request_interval_seconds=0)
        with pytest.raises(JolpicaPayloadError, match="pagination"):
            await client.fetch_race_results(2026)
