# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    node_env: str = "development"

    season_year: int = 2026
    season_only_mode: bool = True
    target_grand_prix: str = "Belgian Grand Prix"
    target_circuit: str = "Spa-Francorchamps"

    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    next_public_app_name: str = "Apex Arena"
    next_public_app_url: str = "http://localhost:3000"
    next_public_api_url: str = "http://localhost:8000"

    database_url: SecretStr
    postgres_db: str = "apex_arena"
    postgres_user: str = "apex"
    postgres_password: SecretStr
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    redis_url: SecretStr
    redis_port: int = 6379

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
    openf1_live_catalog_sync_seconds: int = Field(default=60, ge=15, le=900)
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

    log_level: str = "info"
    log_format: Literal["pretty", "json"] = "pretty"
    sentry_dsn: SecretStr | None = None
    next_public_sentry_dsn: str | None = None

    production_frontend_url: str = "https://apex.chaitanyasingh.org"
    production_backend_url: str = "https://api.apex.chaitanyasingh.org"
    public_base_url: str = "https://apex.chaitanyasingh.org"

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

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: object) -> str:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str):
            raise ValueError("DATABASE_URL must be a string")
        if not value.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
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
        "next_public_api_url",
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

        parsed_password = urlparse(self.database_url.get_secret_value()).password
        configured_password = self.postgres_password.get_secret_value()
        if parsed_password and unquote(parsed_password) != configured_password:
            raise ValueError("DATABASE_URL password must match POSTGRES_PASSWORD")

        if self.openf1_reconnect_base_delay_ms > self.openf1_reconnect_max_delay_ms:
            raise ValueError("OpenF1 reconnect base delay cannot exceed maximum delay")
        return self

    @property
    def async_database_url(self) -> str:
        database_url = self.database_url.get_secret_value()
        if database_url.startswith("postgresql+asyncpg://"):
            return database_url
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

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
