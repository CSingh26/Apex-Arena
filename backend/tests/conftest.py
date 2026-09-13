# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.core.settings import Settings

# Required fields have no code default, so a hermetic run still has to supply
# them. These point at nothing real; suites that need live infrastructure gate
# themselves behind explicit opt-in environment flags instead.
SYNTHETIC_REQUIRED_ENVIRONMENT = {
    "DATABASE_URL": "postgresql+asyncpg://synthetic:synthetic@localhost:5432/apex_arena_test",
    "REDIS_URL": "redis://localhost:6379/15",
}


@pytest.fixture(scope="session", autouse=True)
def hermetic_settings_environment() -> Iterator[None]:
    """Assert against code defaults, never a developer's real .env or shell.

    ``Settings`` reads ``.env`` and the ambient environment, so without this a
    local deployment value silently becomes the expected value and the suite
    passes or fails depending on whose machine it runs on.
    """
    saved = dict(os.environ)
    configured_env_file = Settings.model_config.get("env_file")
    for field in Settings.model_fields:
        os.environ.pop(field.upper(), None)
        os.environ.pop(field, None)
    os.environ.update(SYNTHETIC_REQUIRED_ENVIRONMENT)
    Settings.model_config["env_file"] = None
    try:
        yield
    finally:
        Settings.model_config["env_file"] = configured_env_file
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def no_replay_startup_io(monkeypatch):
    """Route-only tests isolate SQL; lifecycle/recovery tests exercise real startup."""
    from app.storage.intelligence_progress import SqlIntelligenceProgressRepository
    from app.storage.repositories import SqlIngestionRunRepository
    from app.storage.room_repository import SqlRaceRoomRepository

    async def recover(_repository):
        return 0

    async def recover_ingestion(_repository, _cutoff, *, reason):
        return 0

    async def no_projection_metadata(_repository, _session_key):
        return None

    monkeypatch.setattr(SqlRaceRoomRepository, "pause_orphaned_running_rows", recover)
    monkeypatch.setattr(SqlIntelligenceProgressRepository, "load", no_projection_metadata)
    monkeypatch.setattr(
        SqlIngestionRunRepository,
        "fail_running_before",
        recover_ingestion,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        rate_limit_enabled=False,
        database_url="postgresql://apex:test-password@localhost:5432/apex_arena",
        database_migration_url=None,
        postgres_password="test-password",
        redis_url="redis://localhost:6379/15",
        openf1_username=None,
        openf1_password=None,
        openai_api_key=None,
        jwt_secret=None,
        session_secret=None,
        internal_api_key=None,
        admin_dashboard_password=None,
        sentry_dsn=None,
    )
