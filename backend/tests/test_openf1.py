# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import httpx
import pytest

from app.core.settings import Settings
from app.providers.openf1 import (
    OpenF1AuthService,
    OpenF1AuthUnavailable,
    OpenF1RestClient,
)


@pytest.mark.asyncio
async def test_missing_live_credentials_degrades_without_request(settings: Settings) -> None:
    auth = OpenF1AuthService(settings)

    with pytest.raises(OpenF1AuthUnavailable, match="historical REST remains available"):
        await auth.get_access_token()

    assert auth.credentials_present is False
    await auth.close()


@pytest.mark.asyncio
async def test_access_token_is_requested_and_cached(settings: Settings) -> None:
    live_settings = Settings.model_validate(
        {
            **settings.model_dump(),
            "openf1_username": "fan@example.test",
            "openf1_password": "live-password",
        }
    )
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.method == "POST"
        assert b"username=fan%40example.test" in request.content
        assert b"password=live-password" in request.content
        return httpx.Response(
            200,
            json={"access_token": "sensitive-token", "expires_in": "3600", "token_type": "bearer"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = OpenF1AuthService(live_settings, http_client)

    first = await auth.get_access_token()
    second = await auth.get_access_token()

    assert first == second == "sensitive-token"
    assert requests == 1
    assert auth.token_available is True
    await http_client.aclose()


@pytest.mark.asyncio
async def test_historical_query_helper_accepts_operators(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["session_key"] == "9839"
        assert request.url.params["lap_number<="] == "3"
        return httpx.Response(200, json=[{"lap_number": 1}])

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.openf1.test/v1/"
    )
    client = OpenF1RestClient(settings, http_client)

    data = await client.laps(**{"session_key": 9839, "lap_number<=": 3})

    assert data == [{"lap_number": 1}]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_historical_query_helper_rejects_unsafe_keys(settings: Settings) -> None:
    http_client = httpx.AsyncClient(base_url="https://api.openf1.test/v1/")
    client = OpenF1RestClient(settings, http_client)

    with pytest.raises(ValueError, match="Unsafe OpenF1 filter"):
        await client.sessions(**{"token?": "do-not-send"})

    await http_client.aclose()
