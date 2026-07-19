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


def test_managed_dsn_does_not_require_discrete_postgres_password() -> None:
    """Neon issues a single DSN; the discrete POSTGRES_* parts stay optional."""
    managed = Settings(
        app_env="staging",
        database_url="postgresql://neon_user:neon_pw@ep-example.neon.tech/apex?ssl=require",
        redis_url="rediss://default:token@example.upstash.io:6379",
        postgres_password=None,
    )

    assert managed.postgres_password is None
    assert managed.async_database_url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in managed.async_database_url


def test_migration_url_falls_back_to_runtime_dsn(settings: Settings) -> None:
    assert settings.database_migration_url is None
    assert settings.async_migration_database_url == settings.async_database_url


def test_direct_migration_url_is_preferred_when_configured(settings: Settings) -> None:
    """Pooled endpoints break session advisory locks, so the direct DSN wins."""
    values = settings.model_dump()
    values["database_migration_url"] = (
        "postgresql://apex:test-password@direct.neon.tech:5432/apex_arena?ssl=require"
    )
    configured = Settings.model_validate(values)

    assert "direct.neon.tech" in configured.async_migration_database_url
    assert configured.async_migration_database_url.startswith("postgresql+asyncpg://")


def test_upstash_tls_url_is_accepted_and_masked() -> None:
    upstash = Settings(
        app_env="staging",
        database_url="postgresql://u:p@ep-example.neon.tech/apex?ssl=require",
        redis_url="rediss://default:secret-token@example.upstash.io:6379",
        postgres_password=None,
    )

    assert upstash.redis_dsn.startswith("rediss://")
    assert "secret-token" not in repr(upstash)
    assert upstash.safe_runtime_metadata["redis_host"] == "example.upstash.io"


def test_conservative_pool_defaults_suit_a_small_managed_database(settings: Settings) -> None:
    assert settings.db_pool_size == 3
    assert settings.db_max_overflow == 2
    assert settings.db_pool_timeout_seconds == 15
    assert settings.db_pool_recycle_seconds == 300
    assert settings.redis_socket_timeout_seconds == 5
    assert settings.redis_health_check_interval_seconds == 30


def test_base_path_normalizes_to_a_single_leading_slash() -> None:
    for raw in ("apex-arena", "/apex-arena", "/apex-arena/"):
        configured = Settings(
            app_env="test",
            app_base_path=raw,
            database_url="postgresql://apex:t@localhost:5432/apex_arena",
            redis_url="redis://localhost:6379/15",
            postgres_password=None,
        )
        assert configured.normalized_base_path == "/apex-arena"


def test_empty_base_path_means_root_mount() -> None:
    configured = Settings(
        app_env="test",
        app_base_path="/",
        database_url="postgresql://apex:t@localhost:5432/apex_arena",
        redis_url="redis://localhost:6379/15",
        postgres_password=None,
    )
    assert configured.normalized_base_path == ""


def test_retention_is_disabled_by_default(settings: Settings) -> None:
    """Nothing is pruned implicitly; retention must be opted into."""
    assert settings.raw_event_retention_days == 0
    assert settings.normalized_event_retention_days == 0
    assert settings.provider_payload_retention_days == 0
    assert settings.replay_archive_enabled is False


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
