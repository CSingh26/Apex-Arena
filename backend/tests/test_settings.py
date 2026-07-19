# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

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


def test_local_compose_password_match_passes() -> None:
    configured = Settings(
        app_env="test",
        database_url="postgresql://apex:local-password@postgres:5432/apex_arena",
        postgres_password="local-password",
        redis_url="redis://localhost:6379/15",
    )

    assert configured.safe_runtime_metadata["database_host"] == "postgres"


def test_local_compose_password_mismatch_fails_without_disclosing_values() -> None:
    with pytest.raises(ValidationError, match="must match POSTGRES_PASSWORD"):
        Settings(
            app_env="test",
            database_url="postgresql://apex:local-url-secret@postgres:5432/apex_arena",
            postgres_password="local-variable-secret",
            redis_url="redis://localhost:6379/15",
        )

    try:
        Settings(
            app_env="test",
            database_url="postgresql://apex:local-url-secret@postgres:5432/apex_arena",
            postgres_password="local-variable-secret",
            redis_url="redis://localhost:6379/15",
        )
    except ValidationError as exc:
        message = str(exc)
        assert "local-url-secret" not in message
        assert "local-variable-secret" not in message
        assert "postgresql://" not in message


def test_localhost_password_mismatch_still_fails() -> None:
    with pytest.raises(ValidationError, match="for local PostgreSQL"):
        Settings(
            app_env="test",
            database_url="postgresql://apex:first@127.0.0.1:5432/apex_arena",
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


def test_managed_dsn_ignores_unrelated_local_postgres_password() -> None:
    """Ambient local Compose credentials do not invalidate an external DSN."""
    managed = Settings(
        app_env="staging",
        database_url="postgresql://neon_user:neon_pw@ep-example.neon.tech/apex?ssl=require",
        redis_url="rediss://default:token@example.upstash.io:6379",
        postgres_password="unrelated-local-password",
    )

    assert managed.postgres_password is not None
    assert managed.async_database_url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in managed.async_database_url


def test_managed_direct_migration_dsn_ignores_local_postgres_password() -> None:
    managed = Settings(
        app_env="staging",
        database_url="postgresql://u:pooled@pooler.example.net/apex?ssl=require",
        database_migration_url="postgresql://u:direct@direct.example.net/apex?ssl=require",
        redis_url="rediss://default:token@example.upstash.io:6379",
        postgres_password="local-compose-password",
    )

    assert "direct.example.net" in managed.async_migration_database_url


def test_local_percent_encoded_password_is_compared_after_decoding() -> None:
    configured = Settings(
        app_env="test",
        database_url="postgresql://apex:p%40ss%25word@localhost:5432/apex_arena",
        postgres_password="p@ss%word",
        redis_url="redis://localhost:6379/15",
    )

    assert "p%40ss%25word" in configured.async_database_url


def test_migration_url_is_validated_independently() -> None:
    with pytest.raises(ValidationError, match="DATABASE_MIGRATION_URL must use PostgreSQL"):
        Settings(
            app_env="test",
            database_url="postgresql://apex:test@localhost:5432/apex_arena",
            database_migration_url="mysql://apex:test@localhost/apex_arena",
            postgres_password="test",
            redis_url="redis://localhost:6379/15",
        )


def test_empty_migration_url_falls_back_to_runtime_dsn(settings: Settings) -> None:
    values = settings.model_dump()
    values["database_migration_url"] = ""
    configured = Settings.model_validate(values)

    assert configured.database_migration_url is None
    assert configured.async_migration_database_url == configured.async_database_url


def test_shell_environment_overrides_dotenv_file(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql://apex:file@localhost:5432/from_file\n"
        "POSTGRES_PASSWORD=file\n"
        "REDIS_URL=redis://localhost:6379/15\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://managed:shell@external.example.net/from_shell?ssl=require",
    )
    monkeypatch.setenv(
        "DATABASE_MIGRATION_URL",
        "postgresql://managed:direct@direct.example.net/from_shell?ssl=require",
    )
    monkeypatch.setenv("POSTGRES_PASSWORD", "unrelated-local-password")
    monkeypatch.setenv("REDIS_URL", "rediss://default:token@example.upstash.io:6379")

    configured = Settings(_env_file=env_file)

    assert configured.safe_runtime_metadata["database_host"] == "external.example.net"
    assert "direct.example.net" in configured.async_migration_database_url


def test_neon_libpq_parameters_are_translated_for_asyncpg() -> None:
    """Neon's copy button emits sslmode/channel_binding, which asyncpg rejects."""
    neon = Settings(
        app_env="staging",
        database_url=(
            "postgresql://u:p@ep-example.neon.tech/apex?sslmode=require&channel_binding=require"
        ),
        redis_url="rediss://default:token@example.upstash.io:6379",
        postgres_password=None,
    )

    dsn = neon.async_database_url
    assert dsn.startswith("postgresql+asyncpg://")
    assert "ssl=require" in dsn
    assert "sslmode" not in dsn
    assert "channel_binding" not in dsn


def test_redis_socket_timeout_cannot_abort_a_blocking_stream_read(
    settings: Settings,
) -> None:
    """A socket timeout under the XREAD BLOCK window would flap SSE to degraded."""
    values = settings.model_dump()
    values.update(redis_socket_timeout_seconds=5, sse_heartbeat_seconds=15)
    configured = Settings.model_validate(values)

    # The room stream blocks for at most 10s, so the socket must outlast it.
    assert configured.effective_redis_socket_timeout > 10
    assert configured.effective_redis_socket_timeout >= 15


def test_generous_redis_socket_timeout_is_respected(settings: Settings) -> None:
    values = settings.model_dump()
    values.update(redis_socket_timeout_seconds=40, sse_heartbeat_seconds=15)
    assert Settings.model_validate(values).effective_redis_socket_timeout == 40


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
    assert "direct.neon.tech" not in configured.async_process_database_url


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


def test_deployed_ingestor_requires_the_direct_endpoint(settings: Settings) -> None:
    """A pooled DSN makes the session-scoped singleton lease unreliable."""
    values = settings.model_dump()
    values.update(
        app_env="staging",
        app_process_role="ingestor",
        database_url="postgresql://u:p@pooler.neon.tech/apex?ssl=require",
        redis_url="rediss://default:t@x.upstash.io:6379",
        postgres_password=None,
        database_migration_url=None,
    )

    with pytest.raises(ValidationError, match="DATABASE_MIGRATION_URL"):
        Settings.model_validate(values)

    values["database_migration_url"] = "postgresql://u:p@direct.neon.tech/apex?ssl=require"
    configured = Settings.model_validate(values)
    assert "direct.neon.tech" in configured.async_migration_database_url
    assert "direct.neon.tech" in configured.async_process_database_url


def test_combined_role_also_requires_the_direct_endpoint(settings: Settings) -> None:
    """Combined mode ingests too, so it takes the same lease as the ingestor."""
    values = settings.model_dump()
    values.update(
        app_env="staging",
        app_process_role="all",
        database_url="postgresql://u:p@pooler.neon.tech/apex?ssl=require",
        redis_url="rediss://default:t@x.upstash.io:6379",
        postgres_password=None,
        database_migration_url=None,
    )

    with pytest.raises(ValidationError, match="DATABASE_MIGRATION_URL"):
        Settings.model_validate(values)

    values["database_migration_url"] = "postgresql://u:p@direct.neon.tech/apex?ssl=require"
    configured = Settings.model_validate(values)
    assert "direct.neon.tech" in configured.async_process_database_url


def test_local_ingestor_does_not_require_a_direct_endpoint(settings: Settings) -> None:
    """Local development runs against one plain PostgreSQL with no pooler."""
    values = settings.model_dump()
    values.update(app_env="local", app_process_role="all", database_migration_url=None)

    assert Settings.model_validate(values).app_process_role == "all"


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
