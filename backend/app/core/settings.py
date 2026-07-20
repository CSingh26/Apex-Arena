# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse, urlunparse

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_POSTGRES_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    """Typed environment configuration. Secret fields stay masked in repr/log output."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Apex Arena"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_process_role: Literal["api", "ingestor", "combined", "all"] = "api"
    node_env: str = "development"

    season_year: int = 2026
    season_only_mode: bool = True
    target_grand_prix: str = "Belgian Grand Prix"
    target_circuit: str = "Spa-Francorchamps"

    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    next_public_app_name: str = "Apex Arena"
    next_public_app_url: str = "http://localhost:3000"
    next_public_app_base_path: str = ""

    database_url: SecretStr
    # Managed providers issue authoritative DSNs. POSTGRES_* belongs to local
    # Compose and is cross-checked only when a URL points to a local host.
    database_migration_url: SecretStr | None = None
    postgres_db: str = "apex_arena"
    postgres_user: str = "apex"
    postgres_password: SecretStr | None = None
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Conservative pool sizing keeps a small managed database within its
    # connection ceiling when several Railway replicas connect at once.
    db_pool_size: int = Field(default=3, ge=1, le=20)
    db_max_overflow: int = Field(default=2, ge=0, le=20)
    db_pool_timeout_seconds: int = Field(default=15, ge=1, le=120)
    db_pool_recycle_seconds: int = Field(default=300, ge=30, le=3600)

    redis_url: SecretStr
    redis_port: int = 6379
    redis_socket_timeout_seconds: int = Field(default=5, ge=1, le=60)
    redis_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    redis_health_check_interval_seconds: int = Field(default=30, ge=0, le=300)

    openf1_rest_base_url: str = "https://api.openf1.org/v1"
    openf1_username: str | None = None
    openf1_password: SecretStr | None = None
    openf1_auth_url: str = "https://api.openf1.org/token"
    openf1_mqtt_host: str = "mqtt.openf1.org"
    openf1_mqtt_port: int = 8883
    openf1_mqtt_ws_url: str = "wss://mqtt.openf1.org:8084/mqtt"
    openf1_token_refresh_buffer_seconds: int = 300
    openf1_reconnect_max_attempts: int = 20
    openf1_reconnect_base_delay_ms: int = 1000
    openf1_reconnect_max_delay_ms: int = 30000
    openf1_live_auto_connect: bool = False
    openf1_ingestion_mode: Literal["mqtt", "rest", "auto"] = "auto"
    openf1_rest_backfill_enabled: bool = False
    openf1_rest_backfill_season: int = Field(default=2026, ge=1950, le=2100)
    openf1_rest_backfill_max_sessions: int = Field(default=1, ge=1, le=10)
    openf1_rest_max_concurrent_requests: int = Field(default=2, ge=1, le=8)
    openf1_rest_cursor_overlap_seconds: int = Field(default=2, ge=0, le=60)
    openf1_rest_include_high_frequency: bool = False
    openf1_mqtt_connect_timeout_seconds: int = Field(default=10, ge=1, le=120)
    openf1_live_catalog_sync_seconds: int = Field(default=60, ge=15, le=900)
    recent_session_reconciliation_enabled: bool = False
    recent_session_auto_backfill_enabled: bool = False
    recent_session_reconciliation_lookback_days: int = Field(default=14, ge=1, le=60)
    recent_session_provider_grace_minutes: int = Field(default=15, ge=0, le=360)
    recent_session_reconciliation_interval_seconds: int = Field(default=900, ge=60, le=21600)
    recent_session_auto_backfill_max_sessions: int = Field(default=1, ge=1, le=5)
    recent_session_auto_backfill_max_concurrent: int = Field(default=1, ge=1, le=3)
    openf1_live_topics: str = (
        "v1/sessions,v1/drivers,v1/position,v1/intervals,v1/laps,v1/pit,"
        "v1/stints,v1/race_control,v1/weather"
    )

    jolpica_base_url: str = "https://api.jolpi.ca/ergast/f1"

    openai_api_key: SecretStr | None = None
    ai_enabled: bool = True
    openai_reaction_model: str = "gpt-4.1-mini"
    openai_summary_model: str = "gpt-4.1-mini"
    ai_max_calls_per_minute: int = 20
    ai_max_calls_per_session: int = 500
    ai_max_agents_per_event: int = 4
    ai_request_timeout_ms: int = 20000
    ai_daily_token_budget: int = 1000000
    ai_kill_switch: bool = False

    live_mode_enabled: bool = True
    live_stale_after_seconds: int = 15
    live_degraded_after_seconds: int = 45
    event_dedup_ttl_seconds: int = 3600
    event_ordering_buffer_ms: int = 1500
    event_importance_min_for_ai: float = Field(default=0.55, ge=0, le=1)
    reaction_queue_enabled: bool = True
    reaction_queue_max_size: int = 100
    reaction_stale_after_seconds: int = 30

    stream_backend: Literal["sse"] = "sse"
    sse_heartbeat_seconds: int = Field(default=15, ge=1, le=120)
    race_state_snapshot_every_n_events: int = Field(default=10, ge=1, le=1000)
    engine_recent_events_limit: int = Field(default=100, ge=1, le=1000)
    room_topic_cooldown_seconds: int = Field(default=20, ge=0, le=600)
    room_stream_backlog_limit: int = Field(default=250, ge=1, le=1000)
    room_replay_interval_seconds: float = Field(default=0.6, ge=0.05, le=10)
    room_diagnostics_enabled: bool = False
    development_fixture_enabled: bool = False
    historical_ingestion_enabled: bool = True
    historical_ingestion_max_records_per_endpoint: int = Field(default=5000, ge=1, le=50000)
    historical_provider_retry_attempts: int = Field(default=3, ge=1, le=6)
    historical_provider_retry_base_delay_ms: int = Field(default=100, ge=0, le=5000)
    historical_provider_min_interval_ms: int = Field(default=25, ge=0, le=5000)
    historical_provider_cache_ttl_seconds: int = Field(default=900, ge=0, le=86400)
    debug_ingestion_enabled: bool = True

    enable_live_rooms: bool = True
    enable_historical_replay: bool = True
    enable_auto_room_creation: bool = True
    enable_agent_memory: bool = True
    enable_agent_predictions: bool = True
    enable_public_replays: bool = True
    enable_user_chat: bool = False
    enable_user_created_agents: bool = False
    enable_vector_memory: bool = False
    enable_monte_carlo: bool = False

    jwt_secret: SecretStr | None = None
    session_secret: SecretStr | None = None
    internal_api_key: SecretStr | None = None
    admin_dashboard_password: SecretStr | None = None
    cors_allowed_origins: str = "http://localhost:3000"

    # Public mount point. Requests arrive through the portfolio proxy, so the
    # backend must be able to rebuild public URLs without trusting raw Host.
    app_base_path: str = ""
    apex_arena_proxy_token: SecretStr | None = None
    public_proxy_host: str = ""
    trusted_proxy_hosts: str = ""
    proxy_enforcement_enabled: bool = True

    # RESERVED: these record the intended retention policy but nothing prunes yet.
    # No pruning job exists in the application, so setting them has no runtime
    # effect today. They are declared so deployment configuration and the cost
    # documentation can be written against stable names. Defaults are inert.
    raw_event_retention_days: int = Field(default=0, ge=0, le=3650)
    normalized_event_retention_days: int = Field(default=0, ge=0, le=3650)
    provider_payload_retention_days: int = Field(default=0, ge=0, le=3650)
    replay_archive_enabled: bool = False

    log_level: str = "info"
    log_format: Literal["pretty", "json"] = "pretty"
    sentry_dsn: SecretStr | None = None
    next_public_sentry_dsn: str | None = None

    production_frontend_url: str = "https://chaitanyasingh.org/apex-arena"
    production_backend_url: str = "https://chaitanyasingh.org/apex-arena/api"
    public_base_url: str = "https://chaitanyasingh.org/apex-arena"

    @field_validator(
        "openf1_username",
        "next_public_sentry_dsn",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "openf1_password",
        "openai_api_key",
        "jwt_secret",
        "session_secret",
        "internal_api_key",
        "admin_dashboard_password",
        "sentry_dsn",
        mode="before",
    )
    @classmethod
    def empty_secret_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url", "database_migration_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: object, info: ValidationInfo) -> str | None:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        variable_name = info.field_name.upper()
        if info.field_name == "database_migration_url" and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            return None
        if not isinstance(value, str):
            raise ValueError(f"{variable_name} must be a string")
        if not value.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise ValueError(f"{variable_name} must use PostgreSQL")
        return value.rstrip("/")

    @field_validator("redis_url", mode="before")
    @classmethod
    def validate_redis_url(cls, value: object) -> str:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            raise ValueError("REDIS_URL must be a string")
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use Redis")
        return value

    @field_validator(
        "frontend_url",
        "backend_url",
        "next_public_app_url",
        "openf1_rest_base_url",
        "openf1_auth_url",
        "jolpica_base_url",
        "production_frontend_url",
        "production_backend_url",
        "public_base_url",
    )
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> Settings:
        if self.season_only_mode and self.season_year != 2026:
            raise ValueError("SEASON_ONLY_MODE requires SEASON_YEAR=2026 for Apex Arena v0.1")

        # POSTGRES_* belongs to the repository's local Compose database. A developer
        # may also keep managed DSNs in the same untracked .env, so never compare
        # credentials for an external host merely because POSTGRES_PASSWORD exists.
        if self.postgres_password is not None:
            configured_password = self.postgres_password.get_secret_value()
            database_urls = [("DATABASE_URL", self.database_url)]
            if self.database_migration_url is not None:
                database_urls.append(("DATABASE_MIGRATION_URL", self.database_migration_url))
            for variable_name, secret_url in database_urls:
                parsed = urlparse(secret_url.get_secret_value())
                host = (parsed.hostname or "").lower().rstrip(".")
                if host not in LOCAL_POSTGRES_HOSTS:
                    continue
                if parsed.password and unquote(parsed.password) != configured_password:
                    raise ValueError(
                        f"{variable_name} password must match POSTGRES_PASSWORD "
                        "for local PostgreSQL"
                    )

        if self.openf1_reconnect_base_delay_ms > self.openf1_reconnect_max_delay_ms:
            raise ValueError("OpenF1 reconnect base delay cannot exceed maximum delay")
        worker_role = self.app_process_role in {"ingestor", "combined", "all"}
        if self.openf1_rest_backfill_enabled and self.app_process_role == "api":
            raise ValueError("API processes cannot enable OpenF1 historical backfill")
        if self.recent_session_reconciliation_enabled and not worker_role:
            raise ValueError("Recent-session reconciliation requires ingestor or combined role")
        if (
            self.recent_session_auto_backfill_enabled
            and not self.recent_session_reconciliation_enabled
        ):
            raise ValueError("Recent-session auto backfill requires reconciliation to be enabled")
        if self.app_env == "production" and self.app_process_role == "all":
            raise ValueError("APP_PROCESS_ROLE=all is not allowed in production")
        # The singleton lease is a session-scoped advisory lock. Through a
        # transaction pooler it cannot be relied upon, so an ingesting role in a
        # deployed environment must be given the direct endpoint explicitly.
        if (
            self.app_env in {"staging", "production"}
            and worker_role
            and self.database_migration_url is None
        ):
            raise ValueError(
                "Ingesting roles require DATABASE_MIGRATION_URL (the direct, "
                "non-pooled endpoint) so the singleton lease is reliable"
            )
        if (
            self.app_env == "production"
            and self.app_process_role == "api"
            and self.openf1_live_auto_connect
        ):
            raise ValueError(
                "API processes cannot auto-connect OpenF1 live ingestion in production"
            )
        if self.app_env == "production":
            database_query = parse_qs(urlparse(self.database_url.get_secret_value()).query)
            database_tls = (database_query.get("ssl") or database_query.get("sslmode") or [""])[0]
            if database_tls not in {"require", "verify-ca", "verify-full", "true"}:
                raise ValueError("Production DATABASE_URL must require TLS")
            if not self.redis_url.get_secret_value().startswith("rediss://"):
                raise ValueError("Production REDIS_URL must use rediss://")
            if self.debug_ingestion_enabled:
                raise ValueError("DEBUG_INGESTION_ENABLED must be false in production")
            if self.development_fixture_enabled:
                raise ValueError("DEVELOPMENT_FIXTURE_ENABLED must be false in production")
            if self.room_diagnostics_enabled:
                raise ValueError("ROOM_DIAGNOSTICS_ENABLED must be false in production")
        return self

    @staticmethod
    def _asyncpg_dsn(database_url: str) -> str:
        """Normalize a managed PostgreSQL DSN for the asyncpg driver.

        Neon's copy button emits libpq parameters (``sslmode``,
        ``channel_binding``) that asyncpg rejects at connect time. Translate
        ``sslmode`` to asyncpg's ``ssl`` and drop parameters it cannot consume,
        so an operator can paste the provider string verbatim.
        """
        if not database_url.startswith("postgresql+asyncpg://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        parsed = urlparse(database_url)
        if not parsed.query:
            return database_url
        preserved: list[tuple[str, str]] = []
        ssl_mode: str | None = None
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered in {"sslmode", "ssl"}:
                ssl_mode = ssl_mode or value
                continue
            if lowered == "channel_binding":
                continue
            preserved.append((key, value))
        if ssl_mode:
            # asyncpg understands ssl=require/verify-ca/verify-full; libpq's
            # "true" is normalized to the equivalent require.
            preserved.append(("ssl", "require" if ssl_mode == "true" else ssl_mode))
        return urlunparse(parsed._replace(query=urlencode(preserved)))

    @property
    def async_database_url(self) -> str:
        return self._asyncpg_dsn(self.database_url.get_secret_value())

    @property
    def effective_redis_socket_timeout(self) -> int:
        """Socket timeout that cannot abort a blocking stream read.

        SSE consumers call ``XREAD`` with ``BLOCK`` for up to ten seconds. A
        socket timeout below that window would abort every idle heartbeat and
        report a healthy stream as degraded, so keep a margin above it.
        """
        blocking_window_seconds = min(10, self.sse_heartbeat_seconds)
        return max(self.redis_socket_timeout_seconds, blocking_window_seconds + 5)

    @property
    def async_migration_database_url(self) -> str:
        """Migrations and the ingestor lease need a direct (non-pooled) endpoint.

        Managed poolers in transaction mode break session-scoped advisory locks
        and prepared statements, so fall back to the runtime DSN only when no
        dedicated migration URL is configured.
        """
        if self.database_migration_url is None:
            return self.async_database_url
        return self._asyncpg_dsn(self.database_migration_url.get_secret_value())

    @property
    def async_process_database_url(self) -> str:
        """Choose the DSN that preserves the process role's connection semantics."""
        needs_session_lease = self.app_process_role == "ingestor" or (
            self.app_process_role in {"combined", "all"}
            and (self.openf1_live_auto_connect or self.recent_session_reconciliation_enabled)
        )
        if needs_session_lease:
            return self.async_migration_database_url
        return self.async_database_url

    @property
    def normalized_base_path(self) -> str:
        value = (self.app_base_path or self.next_public_app_base_path or "").strip()
        if not value or value == "/":
            return ""
        return "/" + value.strip("/")

    @property
    def trusted_proxy_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_proxy_hosts.split(",") if host.strip()]

    @property
    def redis_dsn(self) -> str:
        return self.redis_url.get_secret_value()

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def openf1_credentials_present(self) -> bool:
        return bool(
            self.openf1_username
            and self.openf1_password
            and self.openf1_password.get_secret_value()
        )

    @property
    def openf1_topics(self) -> list[str]:
        return [topic.strip() for topic in self.openf1_live_topics.split(",") if topic.strip()]

    @property
    def safe_runtime_metadata(self) -> dict[str, object]:
        database = urlparse(self.database_url.get_secret_value())
        redis = urlparse(self.redis_url.get_secret_value())
        return {
            "environment": self.app_env,
            "process_role": self.app_process_role,
            "season": self.season_year,
            "database_host": database.hostname,
            "database_port": database.port,
            "redis_host": redis.hostname,
            "redis_port": redis.port,
            "live_mode_enabled": self.live_mode_enabled,
            "openf1_credentials_present": self.openf1_credentials_present,
            "ai_enabled": self.ai_enabled and not self.ai_kill_switch,
            "room_topic_cooldown_seconds": self.room_topic_cooldown_seconds,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
