# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def test_settings_exposes_only_safe_runtime_metadata(settings: Settings) -> None:
    metadata = settings.safe_runtime_metadata

    assert metadata["database_host"] == "localhost"
    assert metadata["openf1_credentials_present"] is False
    assert "test-password" not in repr(settings)
    assert "database_url" not in metadata
    assert settings.stream_backend == "sse"
    assert settings.race_state_snapshot_every_n_events == 10
    assert "v1/laps" in settings.openf1_topics


def test_database_passwords_must_match() -> None:
    with pytest.raises(ValidationError, match="must match POSTGRES_PASSWORD"):
        Settings(
            app_env="test",
            database_url="postgresql://apex:first@localhost:5432/apex_arena",
            postgres_password="second",
            redis_url="redis://localhost:6379/15",
        )


def test_2026_only_mode_rejects_another_season() -> None:
    with pytest.raises(ValidationError, match="SEASON_YEAR=2026"):
        Settings(
            app_env="test",
            season_year=2027,
            database_url="postgresql://apex:test@localhost:5432/apex_arena",
            postgres_password="test",
            redis_url="redis://localhost:6379/15",
        )


def test_production_rejects_combined_role_and_plaintext_datastores(settings: Settings) -> None:
    values = settings.model_dump()
    values.update(
        app_env="production",
        app_process_role="all",
        debug_ingestion_enabled=False,
        openf1_live_auto_connect=False,
    )
    with pytest.raises(ValidationError, match="APP_PROCESS_ROLE=all"):
        Settings.model_validate(values)

    values["app_process_role"] = "api"
    with pytest.raises(ValidationError, match="DATABASE_URL must require TLS"):
        Settings.model_validate(values)


def test_production_rejects_api_auto_ingestion(settings: Settings) -> None:
    values = settings.model_dump()
    values.update(
        app_env="production",
        app_process_role="api",
        openf1_live_auto_connect=True,
        debug_ingestion_enabled=False,
        database_url="postgresql://apex:test-password@localhost:5432/apex_arena?ssl=require",
        redis_url="rediss://localhost:6379/15",
    )

    with pytest.raises(ValidationError, match="cannot auto-connect"):
        Settings.model_validate(values)
