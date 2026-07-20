# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from app.core.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
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
