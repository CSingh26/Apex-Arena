# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cli import build_race_rooms
from app.cli.safe_errors import format_safe_cli_error, sanitize_exception_message
from app.domain.rooms import (
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionType,
    SourceAvailability,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FakeSettings:
    room_topic_cooldown_seconds: int = 20


class FakeRoomRepository:
    def __init__(self) -> None:
        self.list_calls = 0

    async def list_rooms(self, **_: object) -> tuple[list[RaceRoom], int]:
        self.list_calls += 1
        return (
            [
                RaceRoom(
                    slug="2026-australian-grand-prix-race",
                    event_slug="2026-australian-grand-prix",
                    season=2026,
                    round_number=1,
                    race_name="Australian Grand Prix",
                    official_name="Australian Grand Prix",
                    circuit_name="Albert Park",
                    country="Australia",
                    session_type=SessionType.RACE,
                    scheduled_start=datetime(2026, 3, 8, tzinfo=UTC),
                    status=RoomStatus.READY,
                    mode=RoomMode.ARCHIVED,
                    eligibility_status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
                    ingestion_status=IngestionStatus.READY,
                    source_availability=SourceAvailability.LIMITED,
                    replay_available=True,
                )
            ],
            1,
        )


class FakeRooms:
    def __init__(self) -> None:
        self.invalidated = False
        self.force_sync_calls = 0

    def invalidate_catalog(self) -> None:
        self.invalidated = True

    async def force_sync(self) -> int:
        self.force_sync_calls += 1
        return 1


class FakeServices:
    instances: list[FakeServices] = []

    def __init__(self, _: object) -> None:
        self.room_repository = FakeRoomRepository()
        self.rooms = FakeRooms()
        self.processor = argparse.Namespace(consumers=["live"])
        self.closed = False
        FakeServices.instances.append(self)

    async def close(self) -> None:
        self.closed = True


def run_build_cli(monkeypatch: pytest.MonkeyPatch, args: argparse.Namespace) -> FakeServices:
    FakeServices.instances = []
    monkeypatch.setattr(build_race_rooms, "Settings", lambda **_: FakeSettings())
    monkeypatch.setattr(build_race_rooms, "configure_logging", lambda _: None)
    monkeypatch.setattr(build_race_rooms, "AppServices", FakeServices)
    assert asyncio.run(build_race_rooms.run(args)) == 0
    return FakeServices.instances[0]


def build_args(**overrides: object) -> argparse.Namespace:
    values = {
        "season": 2026,
        "completed_only": True,
        "room_slug": None,
        "dry_run": False,
        "json_summary": True,
        "force_refresh": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_race_rooms_normal_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    services = run_build_cli(monkeypatch, build_args())

    assert services.rooms.force_sync_calls == 1
    assert services.rooms.invalidated is False
    assert services.closed is True


def test_build_race_rooms_force_refresh_invalidates_without_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = run_build_cli(monkeypatch, build_args(force_refresh=True))

    assert services.rooms.invalidated is True
    assert services.rooms.force_sync_calls == 1


def test_build_race_rooms_dry_run_does_not_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    services = run_build_cli(monkeypatch, build_args(dry_run=True, force_refresh=True))

    assert services.rooms.invalidated is False
    assert services.rooms.force_sync_calls == 0


def test_safe_error_formatting_redacts_secrets() -> None:
    exc = RuntimeError(
        "failed postgresql://user:secret@db.example/apex?sslmode=require&token=abc "
        "redis://default:redispass@cache:6379 password=hunter2 api_key=sk-testsecret000000"
    )

    message = format_safe_cli_error("Build race rooms failed safely", exc)

    assert "RuntimeError" in message
    assert "secret" not in message
    assert "redispass" not in message
    assert "hunter2" not in message
    assert "sk-testsecret" not in message
    assert "<redacted>" in message


def test_sanitize_empty_error_message() -> None:
    assert sanitize_exception_message(RuntimeError()) == ""


def test_service_specific_railway_toml_files_parse() -> None:
    for relative in (
        "backend/deploy/railway/api.toml",
        "backend/deploy/railway/chat-build.toml",
        "railway.toml",
    ):
        data = tomllib.loads((REPO_ROOT / relative).read_text())
        assert data["build"]["builder"] == "dockerfile"
        assert data["build"]["dockerfilePath"].endswith("backend/Dockerfile")


def test_historical_config_is_guarded_and_finite() -> None:
    data = tomllib.loads((REPO_ROOT / "backend/deploy/railway/chat-build.toml").read_text())
    command = data["deploy"]["startCommand"]

    assert "RUN_ROOM_CHAT_BUILD" in command
    assert "scripts/build_2026_rooms_and_chats.sh" in command
    assert "python -m app.runtime" not in command
    assert "healthcheckPath" not in data["deploy"]
    assert data["deploy"]["restartPolicyType"] == "NEVER"


def test_api_config_does_not_invoke_historical_script() -> None:
    data = tomllib.loads((REPO_ROOT / "backend/deploy/railway/api.toml").read_text())
    command = data["deploy"]["startCommand"]

    assert "alembic upgrade head" in command
    assert "python -m app.runtime" in command
    assert "build_2026_rooms_and_chats" not in command
    assert data["deploy"]["healthcheckPath"] == "/health/live"


def test_deploy_script_argument_and_missing_variable_behavior(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    railway = fake_bin / "railway"
    railway.write_text('#!/usr/bin/env bash\necho railway "$@"\n')
    railway.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    missing = subprocess.run(
        ["bash", "scripts/deploy_railway.sh", "api"],
        cwd=REPO_ROOT,
        env={key: value for key, value in env.items() if key != "RAILWAY_TOKEN"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "RAILWAY_TOKEN is required" in missing.stderr

    invalid = subprocess.run(
        ["bash", "scripts/deploy_railway.sh", "bogus"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid.returncode == 2
    assert "unknown target" in invalid.stderr


def test_dockerfile_copies_backend_scripts() -> None:
    dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text()

    assert "COPY backend/scripts ./scripts" in dockerfile


def test_historical_workflow_is_manual_only() -> None:
    text = (REPO_ROOT / ".github/workflows/run-historical-chat-build.yml").read_text()

    assert "workflow_dispatch:" in text
    assert 'name: "observant-freedom / production"' in text
    assert "push:" not in text
    assert "scripts/deploy_railway.sh historical" in text


def test_api_workflow_deploys_only_api_service() -> None:
    text = (REPO_ROOT / ".github/workflows/deploy-railway.yml").read_text()

    assert "push:" in text
    assert 'name: "observant-freedom / production"' in text
    assert "scripts/deploy_railway.sh api" in text
    assert "deploy_railway.sh historical" not in text
    assert "apex-arena-historical-chat" not in text
    assert "Skipping GitHub Actions Railway CLI deploy" in text


def test_historical_workflow_skips_without_railway_token() -> None:
    text = (REPO_ROOT / ".github/workflows/run-historical-chat-build.yml").read_text()

    assert "Skipping GitHub Actions Railway CLI deploy" in text
    assert "Use Railway's GitHub-connected service" in text
