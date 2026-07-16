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
