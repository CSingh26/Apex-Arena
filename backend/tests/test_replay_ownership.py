# SPDX-License-Identifier: AGPL-3.0-only
"""Optional real PostgreSQL coverage; every test owns an isolated temporary schema."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.rooms import RoomMode, RoomStatus
from app.storage.database import Base, Database
from app.storage.models import RaceRoomRecord, RoomPlaybackStateRecord
from app.storage.room_repository import SqlRaceRoomRepository
from tests.test_room_replay import replay_room


@pytest.fixture
async def sql_replay():
    url = os.environ.get("TEST_REPLAY_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_REPLAY_POSTGRES_URL to an isolated local PostgreSQL database")
    from sqlalchemy.engine import make_url

    assert make_url(url).host in {"localhost", "127.0.0.1"}
    database = Database(url)
    schema = "replay_test_" + uuid4().hex
    async with database.engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    room = replay_room().model_copy(
        update={
            "status": RoomStatus.REPLAYING,
            "current_lap": 4,
            "last_event_at": datetime(2026, 7, 17, 12, 4, tzinfo=UTC),
        }
    )
    values = room.model_dump(exclude={"created_at", "updated_at"})
    values["event_slug"] = "belgian-grand-prix"
    async with database.session_factory() as session:
        session.add(RaceRoomRecord(**values))
        await session.commit()
        session.add(
            RoomPlaybackStateRecord(
                room_id=room.id,
                current_event_sequence=17,
                current_message_sequence=9,
                current_lap=4,
                playback_speed=2,
                is_paused=False,
                started_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
            )
        )
        await session.commit()
    try:
        yield SqlRaceRoomRepository(database), room
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await database.close()


async def records(repository, room):
    async with repository.database.session_factory() as session:
        playback = (
            await session.execute(
                select(RoomPlaybackStateRecord).where(RoomPlaybackStateRecord.room_id == room.id)
            )
        ).scalar_one_or_none()
        stored_room = await session.get(RaceRoomRecord, room.id)
        return playback, stored_room


async def test_recovery_preserves_cursors_and_is_idempotent(sql_replay):
    repository, room = sql_replay
    before, before_room = await records(repository, room)
    assert await repository.pause_orphaned_running_rows() == 1
    after, after_room = await records(repository, room)
    assert after.is_paused is True
    assert after_room.status == "paused"
    for field in (
        "current_event_sequence",
        "current_message_sequence",
        "current_lap",
        "playback_speed",
        "started_at",
    ):
        assert getattr(after, field) == getattr(before, field)
    assert after_room.current_lap == before_room.current_lap
    assert after_room.last_event_at == before_room.last_event_at
    assert await repository.pause_orphaned_running_rows() == 0
    again, _ = await records(repository, room)
    assert again.updated_at == after.updated_at


@pytest.mark.parametrize(
    "mode,status,paused,missing,expected",
    [
        (RoomMode.REPLAY, RoomStatus.REPLAYING, False, False, 1),
        (RoomMode.ARCHIVED, RoomStatus.REPLAYING, False, False, 1),
        (RoomMode.LIVE, RoomStatus.REPLAYING, False, False, 0),
        (RoomMode.REPLAY, RoomStatus.PAUSED, False, False, 0),
        (RoomMode.REPLAY, RoomStatus.COMPLETED, False, False, 0),
        (RoomMode.REPLAY, RoomStatus.FAILED, False, False, 0),
        (RoomMode.REPLAY, RoomStatus.READY, False, False, 0),
        (RoomMode.REPLAY, RoomStatus.REPLAYING, True, False, 0),
        (RoomMode.REPLAY, RoomStatus.REPLAYING, False, True, 0),
    ],
)
async def test_recovery_eligibility(sql_replay, mode, status, paused, missing, expected):
    repository, room = sql_replay
    async with repository.database.session_factory() as session:
        stored_room = await session.get(RaceRoomRecord, room.id)
        stored_room.mode, stored_room.status = mode.value, status.value
        playback = await session.get(RoomPlaybackStateRecord, room.id)
        if missing:
            await session.delete(playback)
        else:
            playback.is_paused = paused
        await session.commit()
    assert await repository.pause_orphaned_running_rows() == expected
    playback, stored_room = await records(repository, room)
    assert stored_room.status == ("paused" if expected else status.value)
    if missing:
        assert playback is None


async def test_healthy_peer_and_expired_token_fencing(sql_replay):
    repository, room = sql_replay
    first, second = uuid4(), uuid4()
    assert await repository.claim_replay(room.id, first, lease_seconds=30)
    assert not await repository.claim_replay(room.id, second, lease_seconds=30)
    assert await repository.pause_orphaned_running_rows() == 0
    assert await repository.renew_replay(room.id, first, lease_seconds=30)
    assert not await repository.release_replay(room.id, second)
    async with repository.database.session_factory() as session:
        playback = await session.get(RoomPlaybackStateRecord, room.id)
        playback.replay_owner_expires_at = datetime(2000, 1, 1, tzinfo=UTC)
        await session.commit()
    assert not await repository.renew_replay(room.id, first, lease_seconds=30)
    assert await repository.claim_replay(room.id, second, lease_seconds=30)
    assert not await repository.release_replay(room.id, first)
    from app.storage.room_repository import ReplayOwnershipLostError

    with pytest.raises(ReplayOwnershipLostError):
        await repository.update_playback(room.id, current_event_sequence=99, owner_token=first)
    with pytest.raises(ReplayOwnershipLostError):
        await repository.update_room_status(room.id, RoomStatus.FAILED, owner_token=first)
    with pytest.raises(ReplayOwnershipLostError):
        await repository.reset_discussion(room.id, owner_token=first)
    playback, stored_room = await records(repository, room)
    assert playback.current_event_sequence == 17
    assert stored_room.status == "replaying"
    assert playback.replay_owner_token == second
    assert await repository.release_replay(room.id, second)
    assert await repository.pause_orphaned_running_rows() == 1


@pytest.mark.parametrize("contender", ["claim", "renew", "reconcile", "release", "write"])
async def test_lease_operations_wait_on_same_playback_row(sql_replay, contender):
    """Observe PostgreSQL blocking, then recheck expiry after the lock is released."""
    repository, room = sql_replay
    token = uuid4()
    assert await repository.claim_replay(room.id, token, lease_seconds=0.5)
    async with repository.database.session_factory() as holder:
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        playback = (
            await holder.execute(
                select(RoomPlaybackStateRecord)
                .where(RoomPlaybackStateRecord.room_id == room.id)
                .with_for_update()
            )
        ).scalar_one()
        operations = {
            "claim": lambda: repository.claim_replay(room.id, uuid4(), lease_seconds=30),
            "renew": lambda: repository.renew_replay(room.id, token, lease_seconds=30),
            "reconcile": repository.pause_orphaned_running_rows,
            "release": lambda: repository.release_replay(room.id, token),
            "write": lambda: repository.update_playback(
                room.id,
                current_event_sequence=99,
                owner_token=token,
                room_status=RoomStatus.COMPLETED,
                is_paused=True,
            ),
        }
        competing = asyncio.create_task(operations[contender]())
        try:
            async with asyncio.timeout(3):
                while True:
                    async with repository.database.session_factory() as observer:
                        blocked_since = await observer.scalar(
                            text(
                                "SELECT min(xact_start) FROM pg_stat_activity "
                                "WHERE :holder = ANY(pg_blocking_pids(pid))"
                            ),
                            {"holder": holder_pid},
                        )
                    if blocked_since is not None:
                        break
                    await asyncio.sleep(0.01)
            assert not competing.done()
            assert blocked_since < playback.replay_owner_expires_at
            # Let the original lease really expire while the SQL transaction
            # waits. PostgreSQL now() would still use its pre-expiry start time.
            await asyncio.sleep(0.55)
            await holder.commit()
            if contender == "write":
                from app.storage.room_repository import ReplayOwnershipLostError

                with pytest.raises(ReplayOwnershipLostError):
                    await asyncio.wait_for(competing, 3)
            else:
                result = await asyncio.wait_for(competing, 3)
                assert (
                    result
                    == {"claim": True, "renew": False, "reconcile": 1, "release": False}[contender]
                )
        finally:
            competing.cancel()
            await asyncio.gather(competing, return_exceptions=True)
    playback, stored_room = await records(repository, room)
    assert playback.current_event_sequence == 17
    if contender == "reconcile":
        assert stored_room.status == "paused"
        assert playback.replay_owner_token is None
    else:
        assert stored_room.status == "replaying"


async def test_atomic_playback_and_room_transition_rolls_back_together(sql_replay):
    repository, room = sql_replay
    token = uuid4()
    assert await repository.claim_replay(room.id, token, lease_seconds=30)
    async with repository.database.session_factory() as session:
        # A real SQL constraint rejects the room half AFTER the playback update.
        await session.execute(
            text(
                'ALTER TABLE "'
                + session.bind.get_execution_options()["schema_translate_map"][None]
                + "\".race_rooms ADD CONSTRAINT reject_completed CHECK (status <> 'completed')"
            )
        )
        await session.commit()
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await repository.update_playback(
            room.id,
            is_paused=True,
            current_event_sequence=99,
            room_status=RoomStatus.COMPLETED,
            owner_token=token,
        )
    playback, stored_room = await records(repository, room)
    assert playback.current_event_sequence == 17
    assert playback.is_paused is False
    assert stored_room.status == "replaying"


async def test_public_playback_does_not_expose_ownership(sql_replay):
    repository, room = sql_replay
    assert await repository.claim_replay(room.id, uuid4(), lease_seconds=30)
    public = (await repository.get_playback(room.id)).model_dump()
    assert "replay_owner_token" not in public
    assert "replay_owner_expires_at" not in public


async def test_lifespan_deferred_sweep_recovers_recently_dead_owner(
    sql_replay, settings, monkeypatch
):
    from app.main import create_app
    from app.services.container import AppServices

    repository, room = sql_replay
    assert await repository.claim_replay(room.id, uuid4(), lease_seconds=0.15)
    original = AppServices.__init__

    def initialize(self, configured):
        original(self, configured)
        self.room_replay.rooms = repository
        self.room_replay.lease_seconds = 0.15

    monkeypatch.setattr(AppServices, "__init__", initialize)
    application = create_app(settings.model_copy(update={"app_process_role": "api"}))
    async with application.router.lifespan_context(application):
        playback, stored_room = await records(repository, room)
        assert playback.is_paused is False
        assert stored_room.status == "replaying"
        await asyncio.wait_for(application.state.services._replay_reconciliation_task, 2)
        playback, stored_room = await records(repository, room)
        assert playback.is_paused is True
        assert stored_room.status == "paused"
        assert playback.current_event_sequence == 17
        assert playback.current_message_sequence == 9
