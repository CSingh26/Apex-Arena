# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.settings import Settings

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

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = settings.openf1_rest_base_url
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(10.0),
            headers={"Accept": "application/json", "User-Agent": "Apex-Arena/0.1"},
        )

    @property
    def status(self) -> dict[str, Any]:
        parsed = urlparse(self.base_url)
        return {
            "rest_configured": bool(parsed.scheme and parsed.netloc),
            "rest_host": parsed.hostname,
            "historical_auth_required": False,
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


class OpenF1LiveClient:
    """MQTT lifecycle boundary. Transport connection is intentionally deferred to Day 2."""

    def __init__(self, settings: Settings, auth: OpenF1AuthService) -> None:
        self.settings = settings
        self.auth = auth
        self.connection_state = "disconnected"

    async def connect(self) -> None:
        if not self.settings.live_mode_enabled:
            self.connection_state = "disabled"
            return
        if not self.auth.credentials_present:
            self.connection_state = "degraded_missing_credentials"
            return
        await self.auth.get_access_token()
        self.connection_state = "auth_ready_transport_deferred"

    async def disconnect(self) -> None:
        self.connection_state = "disconnected"

    def status(self) -> dict[str, Any]:
        if not self.settings.live_mode_enabled:
            state = "disabled"
        elif not self.auth.credentials_present:
            state = "degraded_missing_credentials"
        else:
            state = self.connection_state
        return {
            "live_mode_enabled": self.settings.live_mode_enabled,
            "credentials_present": self.auth.credentials_present,
            "token_available": self.auth.token_available,
            "token_expires_in_seconds": (
                self.auth.expires_in_seconds if self.auth.token_available else None
            ),
            "connection_state": state,
        }
