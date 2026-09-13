# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest


@pytest.mark.parametrize("environment", ["production", "staging"])
async def test_seed_refuses_unsafe_environment_before_services(settings, monkeypatch, environment):
    module = importlib.import_module("app.cli.seed_e2e_room")
    factory = Mock(side_effect=AssertionError("services must not be constructed"))
    monkeypatch.setattr(module, "AppServices", factory)
    with pytest.raises(RuntimeError, match="test environments"):
        await module.seed(settings=settings.model_copy(update={"app_env": environment}))
    factory.assert_not_called()


@pytest.fixture
async def seed_database(settings, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.container import AppServices
    from app.storage.database import Base, Database
    from tests.test_room_replay import FakeEventBus

    url = os.environ.get("TEST_E2E_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_E2E_DATABASE_URL to an isolated local apex_e2e* database")
    configured = settings.__class__(
        _env_file=None,
        **{
            **settings.model_dump(),
            "database_url": url,
            "postgres_password": "task24-synthetic",
            "app_process_role": "api",
            "live_mode_enabled": False,
            "recent_session_reconciliation_enabled": False,
        },
    )
    module = importlib.import_module("app.cli.seed_e2e_room")
    module.require_e2e_database(configured)
    database = Database(configured.async_database_url)
    schema = "seed_test_" + uuid4().hex
    async with database.engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    instances = []

    def factory(options):
        services = AppServices(options)
        services.database.session_factory = database.session_factory
        services.database._ingestion_schema_verified = True
        services.race_state.live_state_reader = None
        services.room_discussion.publisher = None
        services.room_replay.event_bus = FakeEventBus()
        instances.append(services)
        return services

    monkeypatch.setattr(module, "AppServices", factory)
    try:
        yield module, configured, database, factory, instances
    finally:
        for services in instances:
            await services.close()
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await database.close()


async def test_seed_is_repeatable_and_replays_lap_six_battles_with_pit_history(seed_database):
    from sqlalchemy import func, select

    from app.domain.models import RaceEventType
    from app.storage.models import NormalizedRaceEventRecord, RaceRoomRecord, RoomMessageRecord

    module, configured, database, factory, _ = seed_database
    first = await module.seed(settings=configured)
    second = await module.seed(settings=configured)
    assert first["slug"] == second["slug"] == "e2e-normal-race"
    assert first["normalized_events"] == second["normalized_events"] > 20
    async with database.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(RaceRoomRecord)) == 1
        message_count = await session.scalar(select(func.count()).select_from(RoomMessageRecord))
        assert message_count == first["messages"] == second["messages"] > 0
        assert (
            await session.scalar(select(func.count()).select_from(NormalizedRaceEventRecord))
            == first["normalized_events"]
        )
    services = factory(configured)
    room = await services.room_repository.get_room("e2e-normal-race")
    assert room.ingestion_status.value == "ready"
    assert "Synthetic" in room.race_name
    playback = await services.room_replay.seek_to_lap(room, 6)
    state = await services.race_state.get_state(room.session_key)
    assert state.current_battles
    events = await services.normalized_event_repository.list_for_session(
        room.session_key,
        limit=200,
        before_sequence=playback.current_event_sequence,
        event_types=[RaceEventType.PIT_STOP],
    )
    assert events
    before = await services.room_repository.get_playback(room.id)
    assert await module.seed(settings=configured) == first
    after = await services.room_repository.get_playback(room.id)
    assert after == before


async def test_seed_refuses_existing_unrelated_target_and_closes_after_failure(seed_database):
    from sqlalchemy import func, insert, select

    from app.domain.rooms import RaceRoom, RoomMode, RoomStatus, SourceAvailability
    from app.storage.models import AgentProfileRecord, RaceRoomRecord

    module, configured, database, _, instances = seed_database
    room = RaceRoom(
        slug="e2e-normal-race",
        event_slug="user-room",
        season=2026,
        round_number=77,
        race_name="User data",
        official_name="User data",
        circuit_name="User",
        country="User",
        scheduled_start=datetime(2026, 1, 1, tzinfo=UTC),
        status=RoomStatus.READY,
        mode=RoomMode.REPLAY,
        source_availability=SourceAvailability.LIMITED,
    )
    async with database.session_factory() as session:
        await session.execute(insert(RaceRoomRecord).values(**room.model_dump()))
        await session.commit()
    with pytest.raises(RuntimeError, match="unrelated"):
        await module.seed(settings=configured)
    async with database.session_factory() as session:
        assert (await session.get(RaceRoomRecord, room.id)).race_name == "User data"
        assert await session.scalar(select(func.count()).select_from(AgentProfileRecord)) == 0
    assert instances[-1].room_replay._closed


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://apex:synthetic@example.com/apex_e2e",
        "postgresql://apex:synthetic@localhost/ordinary_user_database",
        "postgresql://apex:synthetic@localhost/apex_e2e?host=remote.example",
    ],
)
async def test_seed_refuses_unscoped_database_before_services(settings, monkeypatch, url):
    module = importlib.import_module("app.cli.seed_e2e_room")
    factory = Mock(side_effect=AssertionError("services must not be constructed"))
    monkeypatch.setattr(module, "AppServices", factory)
    configured = settings.__class__(
        _env_file=None,
        **{
            **settings.model_dump(),
            "database_url": url,
            "postgres_password": "synthetic",
        },
    )
    with pytest.raises(RuntimeError, match="isolated local E2E database"):
        await module.seed(settings=configured)
    factory.assert_not_called()


async def test_seed_closes_services_when_schema_validation_fails(settings, monkeypatch):
    module = importlib.import_module("app.cli.seed_e2e_room")
    configured = settings.model_copy(
        update={
            "database_url": settings.database_url.__class__(
                "postgresql://apex:test-password@localhost/apex_e2e"
            ),
        }
    )
    services = Mock()
    services.database.require_ingestion_schema = AsyncMock(
        side_effect=RuntimeError("schema failed")
    )
    services.close = AsyncMock()
    monkeypatch.setattr(module, "AppServices", Mock(return_value=services))
    with pytest.raises(RuntimeError, match="schema failed"):
        await module.seed(settings=configured)
    services.close.assert_awaited_once()
