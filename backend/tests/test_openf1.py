# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from app.core.settings import Settings
from app.providers.openf1 import (
    LiveConnectionState,
    OpenF1AuthService,
    OpenF1AuthUnavailable,
    OpenF1LiveClient,
    OpenF1RestClient,
)
from app.services.raw_events import RawEventInput


class FakeMqttClient:
    def __init__(self) -> None:
        self.username: str | None = None
        self.password: str | None = None
        self.subscriptions: list[str] = []
        self.started = False
        self.on_connect: Any = None
        self.on_disconnect: Any = None
        self.on_message: Any = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def tls_set(self, **_: Any) -> None:
        return None

    def reconnect_delay_set(self, **_: Any) -> None:
        return None

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        return None

    def loop_start(self) -> None:
        self.started = True

    def loop_stop(self) -> None:
        self.started = False

    def disconnect(self) -> None:
        return None

    def subscribe(self, topic: str) -> None:
        self.subscriptions.append(topic)


class FakeProcessor:
    def __init__(self) -> None:
        self.events: list[RawEventInput] = []
        self.flushed: list[str] = []

    async def ingest(self, raw: RawEventInput) -> None:
        self.events.append(raw)

    async def flush_session(self, session_key: str) -> None:
        self.flushed.append(session_key)


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


@pytest.mark.asyncio
async def test_historical_query_retries_one_401_with_bearer_token_without_logging_secret(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []
    token_requests = 0

    async def token_provider() -> str:
        nonlocal token_requests
        token_requests += 1
        return "never-log-oauth-token"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, json={"detail": "authentication required"})
        assert request.headers["Authorization"] == "Bearer never-log-oauth-token"
        return httpx.Response(200, json=[{"session_key": 9839}])

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openf1.test/v1/",
    )
    client = OpenF1RestClient(settings, http_client, token_provider=token_provider)
    caplog.set_level(logging.DEBUG)

    rows = await client.sessions(year=2026, session_name="Race")

    assert rows == [{"session_key": 9839}]
    assert len(requests) == 2
    assert "Authorization" not in requests[0].headers
    assert token_requests == 1
    assert client.status["historical_auth_mode"] == "oauth_retry"
    assert "never-log-oauth-token" not in caplog.text
    await http_client.aclose()


@pytest.mark.asyncio
async def test_live_client_reports_missing_credentials_without_crashing(settings: Settings) -> None:
    auth = OpenF1AuthService(settings)
    live = OpenF1LiveClient(settings, auth)

    await live.connect()

    assert live.connection_state == LiveConnectionState.MISSING_CREDENTIALS
    assert live.status()["degraded_reason"] == "OpenF1 live credentials are not configured"
    await auth.close()


@pytest.mark.asyncio
async def test_live_client_configures_tls_mqtt_without_logging_token(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    live_settings = Settings.model_validate(
        {
            **settings.model_dump(),
            "openf1_username": "fan@example.test",
            "openf1_password": "live-password",
        }
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "never-log-token", "expires_in": 3600})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = OpenF1AuthService(live_settings, http_client)
    mqtt_client = FakeMqttClient()
    live = OpenF1LiveClient(
        live_settings,
        auth,
        client_factory=lambda: mqtt_client,  # type: ignore[arg-type]
    )
    caplog.set_level(logging.INFO)

    await live.connect()
    mqtt_client.on_connect(mqtt_client, None, None, 0, None)
    await asyncio.sleep(0)  # Schedule the callback's coroutine.
    await asyncio.sleep(0)  # Let the coroutine publish its state.

    assert live.connection_state == LiveConnectionState.CONNECTED
    assert mqtt_client.started is True
    assert "v1/laps" in mqtt_client.subscriptions
    assert "never-log-token" not in caplog.text
    await live.disconnect()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_live_message_uses_unified_processor(settings: Settings) -> None:
    live_settings = Settings.model_validate(
        {**settings.model_dump(), "event_ordering_buffer_ms": 0}
    )
    auth = OpenF1AuthService(live_settings)
    processor = FakeProcessor()
    live = OpenF1LiveClient(live_settings, auth, processor=processor)

    await live._handle_message(
        "v1/position",
        {"_id": 12, "session_key": 9999, "driver_number": 4, "position": 1},
    )

    assert processor.events[0].provider_endpoint == "position"
    assert processor.events[0].provider_event_id == "12"
    assert processor.flushed == ["9999"]
    assert live.last_event_at is not None
    await auth.close()
