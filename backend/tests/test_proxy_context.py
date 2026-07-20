# SPDX-License-Identifier: AGPL-3.0-only
"""Origin protection and public-URL derivation for proxied requests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


def _staging(settings: Settings, **overrides: object) -> Settings:
    values = settings.model_dump()
    values.update(
        app_env="staging",
        app_base_path="/apex-arena",
        apex_arena_proxy_token="staging-proxy-token",
        debug_ingestion_enabled=False,
        **overrides,
    )
    return Settings.model_validate(values)


def test_direct_origin_access_is_rejected_without_the_proxy_token(
    settings: Settings,
) -> None:
    app = create_app(_staging(settings))
    with TestClient(app) as client:
        response = client.get("/api/v1/debug/config")

    assert response.status_code == 403
    # The configured token must never appear in the rejection body.
    assert "staging-proxy-token" not in response.text
    assert response.headers.get("X-Request-ID")


def test_a_wrong_proxy_token_is_rejected(settings: Settings) -> None:
    app = create_app(_staging(settings))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/debug/config",
            headers={"X-Apex-Proxy-Token": "not-the-token"},
        )

    assert response.status_code == 403


def test_proxied_requests_are_accepted(settings: Settings) -> None:
    app = create_app(_staging(settings))
    with TestClient(app) as client:
        response = client.get(
            "/health/ready",
            headers={"X-Apex-Proxy-Token": "staging-proxy-token"},
        )

    assert response.status_code in {200, 503}


def test_liveness_stays_reachable_for_platform_health_checks(
    settings: Settings,
) -> None:
    """Railway probes the container directly, bypassing the public proxy."""
    app = create_app(_staging(settings))
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200


def test_local_development_does_not_enforce_the_proxy(settings: Settings) -> None:
    values = settings.model_dump()
    values.update(app_env="local", apex_arena_proxy_token="unused-locally")
    with TestClient(create_app(Settings.model_validate(values))) as client:
        response = client.get("/api/v1/debug/config")

    assert response.status_code == 200


def test_enforcement_can_be_explicitly_disabled(settings: Settings) -> None:
    app = create_app(_staging(settings, proxy_enforcement_enabled=False))
    with TestClient(app) as client:
        response = client.get("/api/v1/debug/config")

    # Reaches the handler: staging is not production, so the env gate allows it.
    assert response.status_code == 200


def test_untrusted_forwarded_host_cannot_poison_the_public_origin(
    settings: Settings,
) -> None:
    """A spoofed host header must not become the canonical public origin."""
    app = create_app(_staging(settings, trusted_proxy_hosts="chaitanyasingh.org"))

    with TestClient(app) as client:
        app.state.services.database.health_check = AsyncMock(return_value=(True, "ok"))
        app.state.services.redis.health_check = AsyncMock(return_value=(True, "ok"))
        response = client.get(
            "/health/ready",
            headers={
                "X-Apex-Proxy-Token": "staging-proxy-token",
                "X-Apex-Public-Host": "attacker.example",
            },
        )

    # The request still succeeds, but the untrusted host is not echoed anywhere.
    assert response.status_code in {200, 503}
    assert "attacker.example" not in response.text


def test_request_id_is_propagated_when_supplied(settings: Settings) -> None:
    app = create_app(_staging(settings))
    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"X-Request-ID": "correlation-123"},
        )

    assert response.headers["X-Request-ID"] == "correlation-123"
