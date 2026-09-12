# SPDX-License-Identifier: AGPL-3.0-only
"""Fail-closed security contracts for the public proxy boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.proxy import ProxyContextMiddleware
from app.core import settings as settings_module
from app.core.settings import Settings
from app.main import create_app

PROXY_TOKEN = "staging-proxy-token"


def _deployed(
    settings: Settings,
    *,
    app_env: str = "staging",
    app_process_role: str = "api",
    apex_arena_proxy_token: object = PROXY_TOKEN,
    proxy_enforcement_enabled: bool = True,
) -> Settings:
    values = settings.model_dump()
    values.update(
        app_env=app_env,
        app_process_role=app_process_role,
        apex_arena_proxy_token=apex_arena_proxy_token,
        proxy_enforcement_enabled=proxy_enforcement_enabled,
        database_url="postgresql://u:p@pooler.example/apex?ssl=require",
        database_migration_url=(
            "postgresql://u:p@direct.example/apex?ssl=require"
            if app_process_role in {"ingestor", "combined", "all"}
            else None
        ),
        redis_url="rediss://default:p@redis.example:6379",
        postgres_password=None,
        openf1_live_auto_connect=False,
        debug_ingestion_enabled=False,
        room_diagnostics_enabled=False,
    )
    return Settings.model_validate(values)


def _proxy_app(settings: Settings) -> FastAPI:
    application = FastAPI()
    application.add_middleware(ProxyContextMiddleware, settings=settings)

    @application.get("/{path:path}")
    async def inspect_context(request: Request, path: str) -> dict[str, object]:
        return {
            "path": path,
            "public_host": request.state.public_host,
            "original_path": request.state.original_path,
        }

    return application


@contextmanager
def _client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(_proxy_app(settings)) as client:
        yield client


@pytest.mark.parametrize(
    ("app_env", "app_process_role"),
    [
        ("staging", "api"),
        ("staging", "combined"),
        ("staging", "all"),
        ("production", "api"),
        ("production", "combined"),
    ],
)
def test_api_serving_deployments_require_a_proxy_token(
    settings: Settings,
    app_env: str,
    app_process_role: str,
) -> None:
    with pytest.raises(ValidationError, match="APEX_ARENA_PROXY_TOKEN"):
        _deployed(
            settings,
            app_env=app_env,
            app_process_role=app_process_role,
            apex_arena_proxy_token=None,
        )


@pytest.mark.parametrize("invalid_token", ["", "   ", " token-with-padding "])
def test_api_serving_deployments_reject_invalid_proxy_secrets(
    settings: Settings,
    invalid_token: str,
) -> None:
    with pytest.raises(ValidationError, match="APEX_ARENA_PROXY_TOKEN"):
        _deployed(settings, apex_arena_proxy_token=invalid_token)


def test_proxy_validation_errors_do_not_render_secret_input(settings: Settings) -> None:
    canary = "proxy-canary-do-not-leak"

    with pytest.raises(ValidationError) as captured:
        _deployed(settings, apex_arena_proxy_token=f" {canary} ")

    assert canary not in str(captured.value)


@pytest.mark.parametrize("app_env", ["local", "test"])
def test_local_and_test_api_roles_allow_no_proxy_token(
    settings: Settings,
    app_env: str,
) -> None:
    values = settings.model_dump()
    values.update(
        app_env=app_env,
        app_process_role="api",
        apex_arena_proxy_token=None,
    )

    assert Settings.model_validate(values).apex_arena_proxy_token is None


def test_explicitly_disabled_enforcement_allows_no_proxy_token(settings: Settings) -> None:
    configured = _deployed(
        settings,
        apex_arena_proxy_token=None,
        proxy_enforcement_enabled=False,
    )

    assert configured.apex_arena_proxy_token is None


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_dedicated_ingestor_remains_tokenless(
    settings: Settings,
    app_env: str,
) -> None:
    configured = _deployed(
        settings,
        app_env=app_env,
        app_process_role="ingestor",
        apex_arena_proxy_token=None,
    )

    assert configured.apex_arena_proxy_token is None


def test_production_migration_settings_remain_tokenless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_PROCESS_ROLE", "api")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:p@pooler.example/apex?ssl=require",
    )
    monkeypatch.setenv("DATABASE_MIGRATION_URL", "")
    monkeypatch.setenv("REDIS_URL", "rediss://default:p@redis.example:6379")
    monkeypatch.setenv("APEX_ARENA_PROXY_TOKEN", "")
    monkeypatch.setenv("OPENF1_LIVE_AUTO_CONNECT", "false")
    monkeypatch.setenv("DEBUG_INGESTION_ENABLED", "false")
    monkeypatch.setenv("ROOM_DIAGNOSTICS_ENABLED", "false")

    factory = getattr(settings_module, "get_migration_settings", None)
    assert factory is not None
    configured = factory()

    assert configured.app_env == "production"
    assert configured.proxy_enforcement_enabled is False
    assert configured.async_migration_database_url.startswith("postgresql+asyncpg://")


def test_middleware_stays_fail_closed_after_settings_are_mutated(settings: Settings) -> None:
    configured = _deployed(settings)

    with _client(configured) as client:
        assert (
            client.get(
                "/protected",
                headers={"X-Apex-Proxy-Token": PROXY_TOKEN},
            ).status_code
            == 200
        )

        configured.app_env = "local"
        configured.proxy_enforcement_enabled = False
        configured.apex_arena_proxy_token = None

        assert client.get("/protected").status_code == 403


def test_duplicate_proxy_token_headers_are_rejected(settings: Settings) -> None:
    with _client(_deployed(settings)) as client:
        response = client.get(
            "/protected",
            headers=[
                ("X-Apex-Proxy-Token", PROXY_TOKEN),
                ("X-Apex-Proxy-Token", PROXY_TOKEN),
            ],
        )

    assert response.status_code == 403


@pytest.mark.parametrize("supplied", ["", "   ", f" {PROXY_TOKEN}", f"{PROXY_TOKEN} "])
def test_blank_or_padded_proxy_token_headers_are_rejected(
    settings: Settings,
    supplied: str,
) -> None:
    with _client(_deployed(settings)) as client:
        response = client.get(
            "/protected",
            headers={"X-Apex-Proxy-Token": supplied},
        )

    assert response.status_code == 403


def test_proxy_header_name_is_case_insensitive_but_value_is_exact(settings: Settings) -> None:
    with _client(_deployed(settings)) as client:
        accepted = client.get(
            "/protected",
            headers={"x-apex-proxy-token": PROXY_TOKEN},
        )
        rejected = client.get(
            "/protected",
            headers={"x-apex-proxy-token": PROXY_TOKEN.upper()},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 403


def test_exact_liveness_path_is_the_only_tokenless_exemption(settings: Settings) -> None:
    with _client(_deployed(settings)) as client:
        liveness = client.get("/health/live")
        trailing_slash = client.get("/health/live/")
        sibling = client.get("/health/liveness")

    assert liveness.status_code == 200
    assert trailing_slash.status_code == 403
    assert sibling.status_code == 403


def test_rejected_requests_do_not_process_forwarded_metadata(settings: Settings) -> None:
    with (
        patch.object(
            ProxyContextMiddleware,
            "_public_host",
            side_effect=AssertionError("forwarded host was processed"),
        ),
        _client(_deployed(settings)) as client,
    ):
        response = client.get(
            "/protected",
            headers={
                "X-Apex-Public-Host": "attacker.example",
                "X-Apex-Original-Path": "/attacker-controlled",
            },
        )

    assert response.status_code == 403


def test_proxy_token_comparison_uses_constant_time_primitive(settings: Settings) -> None:
    with (
        patch("app.api.proxy.hmac.compare_digest", return_value=False) as compare,
        _client(_deployed(settings)) as client,
    ):
        response = client.get(
            "/protected",
            headers={"X-Apex-Proxy-Token": PROXY_TOKEN},
        )

    assert response.status_code == 403
    compare.assert_called_once_with(PROXY_TOKEN, PROXY_TOKEN)


def test_production_api_refuses_public_replay_controls_without_operator_password(
    settings: Settings,
) -> None:
    configured = _deployed(settings, app_env="production")

    with pytest.raises(RuntimeError, match="ADMIN_DASHBOARD_PASSWORD"):
        create_app(configured)


def test_production_api_allows_disabled_public_replay_controls_without_operator_password(
    settings: Settings,
) -> None:
    configured = _deployed(settings, app_env="production").model_copy(
        update={"enable_public_replays": False}
    )

    create_app(configured)


def test_local_api_and_production_ingestor_do_not_require_operator_password(
    settings: Settings,
) -> None:
    create_app(settings)
    configured = _deployed(
        settings,
        app_env="production",
        app_process_role="ingestor",
    )

    create_app(configured)
