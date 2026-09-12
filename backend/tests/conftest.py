# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from app.core.settings import Settings


@pytest.fixture
def no_replay_startup_io(monkeypatch):
    """Route-only tests isolate SQL; lifecycle/recovery tests exercise real startup."""
    from app.storage.repositories import SqlIngestionRunRepository
    from app.storage.room_repository import SqlRaceRoomRepository

    async def recover(_repository):
        return 0

    async def recover_ingestion(_repository, _cutoff, *, reason):
        return 0

    monkeypatch.setattr(SqlRaceRoomRepository, "pause_orphaned_running_rows", recover)
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
