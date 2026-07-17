# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import paho.mqtt.client as mqtt

from app.core.settings import Settings
from app.services.event_pipeline import RaceEventProcessor
from app.services.raw_events import RawEventInput
from app.storage.redis import EventBus

logger = logging.getLogger(__name__)

OPENF1_ENDPOINTS = frozenset(
    {
        "sessions",
        "drivers",
        "position",
        "intervals",
        "laps",
        "pit",
        "stints",
        "race_control",
        "weather",
    }
)
FILTER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:<=|>=|<|>)?$")


class ProviderPayloadError(RuntimeError):
    pass


class OpenF1RestClient:
    """Historical OpenF1 REST client. Historical endpoints do not require auth."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.base_url = settings.openf1_rest_base_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(10.0),
            headers={"Accept": "application/json", "User-Agent": "Apex-Arena/0.1"},
        )
        self.token_provider = token_provider

    @property
    def status(self) -> dict[str, Any]:
        parsed = urlparse(self.base_url)
        return {
            "rest_configured": bool(parsed.scheme and parsed.netloc),
            "rest_host": parsed.hostname,
            "historical_auth_required": False,
            "historical_auth_mode": (
                "oauth_retry" if self.token_provider is not None else "public_only"
            ),
            "supported_endpoints": sorted(OPENF1_ENDPOINTS),
        }

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, endpoint: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        if endpoint not in OPENF1_ENDPOINTS:
            raise ValueError(f"Unsupported OpenF1 endpoint: {endpoint}")
        if len(filters) > 20:
            raise ValueError("OpenF1 queries are limited to 20 filters")

        params: dict[str, str | int | float | bool] = {}
        for key, value in filters.items():
            if not FILTER_PATTERN.fullmatch(key):
                raise ValueError(f"Unsafe OpenF1 filter: {key}")
            if value is not None:
                params[key] = value

        response = await self.client.get(endpoint, params=params)
        if response.status_code == 401 and self.token_provider is not None:
            token = await self.token_provider()
            response = await self.client.get(
                endpoint,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ProviderPayloadError("OpenF1 returned an unexpected response shape")
        return data

    async def sessions(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("sessions", filters)

    async def drivers(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("drivers", filters)

    async def position(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("position", filters)

    async def intervals(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("intervals", filters)

    async def laps(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("laps", filters)

    async def pit(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("pit", filters)

    async def stints(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("stints", filters)

    async def race_control(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("race_control", filters)

    async def weather(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._get("weather", filters)


class OpenF1AuthUnavailable(RuntimeError):
    pass


class OpenF1AuthService:
    """Backend-only OAuth token cache for authenticated OpenF1 live access."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._access_token: str | None = None
        self._expires_at_monotonic: float | None = None

    @property
    def credentials_present(self) -> bool:
        return self.settings.openf1_credentials_present

    @property
    def token_available(self) -> bool:
        return bool(self._access_token and self.expires_in_seconds > 0)

    @property
    def expires_in_seconds(self) -> int:
        if self._expires_at_monotonic is None:
            return 0
        return max(0, int(self._expires_at_monotonic - time.monotonic()))

    async def get_access_token(self, force_refresh: bool = False) -> str:
        refresh_buffer = self.settings.openf1_token_refresh_buffer_seconds
        if (
            not force_refresh
            and self._access_token
            and self.expires_in_seconds > refresh_buffer
        ):
            return self._access_token

        if not self.credentials_present:
            raise OpenF1AuthUnavailable(
                "OpenF1 live credentials are missing; historical REST remains available"
            )

        password = self.settings.openf1_password
        assert password is not None  # Narrowed by credentials_present.
        response = await self.client.post(
            self.settings.openf1_auth_url,
            data={
                "username": self.settings.openf1_username,
                "password": password.get_secret_value(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code >= 400:
            raise OpenF1AuthUnavailable(
                f"OpenF1 authentication failed with HTTP {response.status_code}"
            )

        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise OpenF1AuthUnavailable("OpenF1 authentication returned no access token")

        try:
            expires_in = int(payload.get("expires_in", 3600))
        except (TypeError, ValueError) as exc:
            raise OpenF1AuthUnavailable("OpenF1 authentication returned an invalid expiry") from exc

        self._access_token = token
        self._expires_at_monotonic = time.monotonic() + max(1, expires_in)
        return token

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class LiveConnectionState(StrEnum):
    DISABLED = "DISABLED"
    MISSING_CREDENTIALS = "MISSING_CREDENTIALS"
    AUTHENTICATING = "AUTHENTICATING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class LiveProcessor(Protocol):
    async def ingest(self, raw: RawEventInput) -> object: ...

    async def flush_session(self, session_key: str) -> object: ...


class OpenF1LiveClient:
    """TLS MQTT client that feeds the same processor used by historical replay."""

    def __init__(
        self,
        settings: Settings,
        auth: OpenF1AuthService,
        processor: RaceEventProcessor | LiveProcessor | None = None,
        event_bus: EventBus | None = None,
        client_factory: Callable[[], mqtt.Client] | None = None,
    ) -> None:
        self.settings = settings
        self.auth = auth
        self.processor = processor
        self.event_bus = event_bus
        self.client_factory = client_factory or self._default_client
        self.connection_state = LiveConnectionState.DISCONNECTED
        self.last_event_at: datetime | None = None
        self.reconnect_attempts = 0
        self.current_session_key: str | None = None
        self.degraded_reason: str | None = None
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutting_down = False

    async def connect(self) -> None:
        if not self.settings.live_mode_enabled:
            await self._set_state(LiveConnectionState.DISABLED)
            return
        if not self.auth.credentials_present:
            await self._set_state(
                LiveConnectionState.MISSING_CREDENTIALS,
                "OpenF1 live credentials are not configured",
            )
            return

        self._loop = asyncio.get_running_loop()
        self._shutting_down = False
        await self._set_state(LiveConnectionState.AUTHENTICATING)
        try:
            token = await self.auth.get_access_token()
        except OpenF1AuthUnavailable:
            await self._set_state(LiveConnectionState.DEGRADED, "OpenF1 authentication failed")
            return

        client = self.client_factory()
        self._client = client
        client.username_pw_set(self.settings.openf1_username or "apex-arena", token)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        client.reconnect_delay_set(
            min_delay=max(1, math.ceil(self.settings.openf1_reconnect_base_delay_ms / 1000)),
            max_delay=max(1, math.ceil(self.settings.openf1_reconnect_max_delay_ms / 1000)),
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        await self._set_state(LiveConnectionState.CONNECTING)
        result = client.connect_async(
            self.settings.openf1_mqtt_host,
            self.settings.openf1_mqtt_port,
            keepalive=60,
        )
        if result != mqtt.MQTT_ERR_SUCCESS:
            await self._set_state(
                LiveConnectionState.ERROR,
                f"MQTT connect setup failed with code {result}",
            )
            return
        client.loop_start()

    async def disconnect(self) -> None:
        self._shutting_down = True
        if self._client is not None:
            self._client.disconnect()
            self._client.loop_stop()
            self._client = None
        await self._set_state(LiveConnectionState.DISCONNECTED)

    def status(self) -> dict[str, Any]:
        return {
            "live_mode_enabled": self.settings.live_mode_enabled,
            "credentials_present": self.auth.credentials_present,
            "auth_available": self.auth.credentials_present,
            "token_available": self.auth.token_available,
            "token_expires_in_seconds": (
                self.auth.expires_in_seconds if self.auth.token_available else None
            ),
            "connection_state": self.connection_state.value,
            "last_event_at": self.last_event_at,
            "reconnect_attempts": self.reconnect_attempts,
            "current_session_key": self.current_session_key,
            "degraded_reason": self.degraded_reason,
        }

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: object,
        reason_code: object,
        properties: object = None,
    ) -> None:
        code = int(getattr(reason_code, "value", reason_code))
        if code != 0:
            self._submit_state(LiveConnectionState.ERROR, f"MQTT rejected connection ({code})")
            return
        self.reconnect_attempts = 0
        for topic in self.settings.openf1_topics:
            client.subscribe(topic)
        self._submit_state(LiveConnectionState.CONNECTED)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        disconnect_flags: object,
        reason_code: object,
        properties: object = None,
    ) -> None:
        if self._shutting_down:
            self._submit_state(LiveConnectionState.DISCONNECTED)
            return
        self.reconnect_attempts += 1
        if self.reconnect_attempts > self.settings.openf1_reconnect_max_attempts:
            self._submit_state(
                LiveConnectionState.ERROR,
                "Maximum MQTT reconnect attempts exceeded",
            )
            return
        self._submit_state(LiveConnectionState.RECONNECTING)

    def _on_message(self, client: mqtt.Client, userdata: object, message: object) -> None:
        if self._loop is None:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))  # type: ignore[attr-defined]
            topic = str(message.topic)  # type: ignore[attr-defined]
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            self._submit_state(LiveConnectionState.DEGRADED, "Invalid MQTT message received")
            return
        if not isinstance(payload, dict):
            self._submit_state(LiveConnectionState.DEGRADED, "Unexpected MQTT payload shape")
            return
        asyncio.run_coroutine_threadsafe(self._handle_message(topic, payload), self._loop)

    async def _handle_message(self, topic: str, payload: dict[str, Any]) -> None:
        session_key = str(payload.get("session_key") or "unknown")
        self.current_session_key = session_key
        self.last_event_at = datetime.now(UTC)
        if self.processor is None:
            await self._set_state(LiveConnectionState.DEGRADED, "Race event processor unavailable")
            return
        try:
            await self.processor.ingest(
                RawEventInput(
                    provider="openf1",
                    provider_endpoint=topic.removeprefix("v1/"),
                    provider_event_id=str(payload["_id"]) if "_id" in payload else None,
                    session_key=session_key,
                    raw_payload=payload,
                    received_at=self.last_event_at,
                )
            )
            await asyncio.sleep(self.settings.event_ordering_buffer_ms / 1000)
            await self.processor.flush_session(session_key)
            await self._set_state(LiveConnectionState.CONNECTED)
        except Exception as exc:
            logger.error("OpenF1 message processing failed error=%s", type(exc).__name__)
            await self._set_state(LiveConnectionState.DEGRADED, "Race event processing failed")

    async def _set_state(
        self, state: LiveConnectionState, degraded_reason: str | None = None
    ) -> None:
        self.connection_state = state
        self.degraded_reason = degraded_reason
        logger.info("OpenF1 live connection state=%s", state.value)
        if self.event_bus is not None:
            try:
                await self.event_bus.publish_connection_status(self.status())
            except Exception as exc:
                logger.error("Live status publish failed error=%s", type(exc).__name__)

    def _submit_state(
        self, state: LiveConnectionState, degraded_reason: str | None = None
    ) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._set_state(state, degraded_reason))
        )

    @staticmethod
    def _default_client() -> mqtt.Client:
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv5,
            transport="tcp",
        )
