# SPDX-License-Identifier: AGPL-3.0-only
"""Optional real PostgreSQL coverage; every test owns an isolated temporary schema."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RoomMessage,
    RoomMode,
    RoomStatus,
)
from app.storage.database import Base, Database
from app.storage.models import (
    AgentProfileRecord,
    MessageEvidenceRecord,
    RaceRoomRecord,
    RoomMessageRecord,
    RoomPlaybackStateRecord,
)
from app.storage.room_repository import (
    DiscussionGenerationChangedError,
    SqlRaceRoomRepository,
)
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
        session.add(
            AgentProfileRecord(
                id="nova",
                display_name="Nova",
                role="Host",
                short_description="Fixture host",
                avatar_key="N",
                specialties=[],
                personality_rules=[],
                speaking_style="Concise",
                supported_topics=["session"],
                active=True,
                sort_order=1,
                ui_accent_key="gold",
            )
        )
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


async def test_playback_projection_remains_coherent_when_restart_overlaps_read(sql_replay):
    repository, room = sql_replay
    token = uuid4()
    assert await repository.claim_replay(room.id, token, lease_seconds=30)
    original_factory = repository.database.session_factory

    class ReadBoundary:
        def __init__(self, session):
            self.session = session
            self.reset = False

        async def after_read(self):
            if not self.reset:
                self.reset = True
                await repository.begin_discussion_restart(
                    room.id, owner_token=token, started_at=datetime.now(UTC)
                )

        async def get(self, model, key):
            record = await self.session.get(model, key)
            await self.after_read()
            return record

        async def execute(self, statement):
            result = await self.session.execute(statement)
            await self.after_read()
            return result

        async def scalar(self, statement):
            return await self.session.scalar(statement)

    @asynccontextmanager
    async def read_factory():
        async with original_factory() as session:
            yield ReadBoundary(session)

    reader = SqlRaceRoomRepository(SimpleNamespace(session_factory=read_factory))
    observed = await reader.get_playback(room.id)
    assert (
        observed.discussion_generation,
        observed.current_event_sequence,
        observed.current_message_sequence,
    ) in {(1, 17, 9), (2, 0, 0)}
    current = await repository.get_playback(room.id)
    assert current.discussion_generation == 2
    assert current.current_event_sequence == 0


def discussion_message(room_id, *, generation: int) -> RoomMessage:
    return RoomMessage(
        room_id=room_id,
        agent_id="nova",
        sequence=0,
        discussion_generation=generation,
        topic=MessageTopic.SESSION,
        message_type=MessageType.OBSERVATION,
        content="The session state is grounded in fixture timing.",
        confidence=Confidence.HIGH,
        evidence_status=EvidenceStatus.GROUNDED,
        generated_by="deterministic",
        generation_key="restart-contract",
        generation_version="fixture-v1",
    )


def discussion_evidence() -> MessageEvidence:
    return MessageEvidence(
        message_id=uuid4(),
        evidence_key="fixture-state",
        evidence_type="normalized_event",
        source_provider="fixture",
        source_reference="event-1",
    )


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
        await repository.begin_discussion_restart(
            room.id, owner_token=first, started_at=datetime.now(UTC)
        )
    playback, stored_room = await records(repository, room)
    assert playback.current_event_sequence == 17
    assert stored_room.status == "replaying"
    assert stored_room.discussion_generation == 1
    assert playback.replay_owner_token == second
    assert await repository.release_replay(room.id, second)
    assert await repository.pause_orphaned_running_rows() == 1


async def test_restart_atomically_advances_generation_and_fences_stale_writers(sql_replay):
    repository, room = sql_replay
    token = uuid4()
    stored, inserted = await repository.insert_message(
        discussion_message(room.id, generation=1),
        [discussion_evidence()],
        expected_generation=1,
    )
    assert inserted and stored.sequence == 1
    assert await repository.claim_replay(room.id, token, lease_seconds=30)

    restarted = await repository.begin_discussion_restart(
        room.id,
        owner_token=token,
        started_at=datetime(2026, 7, 17, 13, tzinfo=UTC),
    )

    assert restarted.discussion_generation == 2
    assert restarted.current_event_sequence == 0
    assert restarted.current_message_sequence == 0
    assert restarted.current_lap == 0
    assert restarted.playback_speed == 1
    assert restarted.is_paused is True
    playback, stored_room = await records(repository, room)
    assert stored_room.discussion_generation == 2
    assert stored_room.status == "paused"
    assert stored_room.message_count == 0
    assert stored_room.generated_message_count == 0
    assert stored_room.last_generated_sequence == 0
    assert stored_room.current_lap is None
    assert stored_room.last_event_at is None
    assert playback.current_event_sequence == 0
    async with repository.database.session_factory() as session:
        assert await session.scalar(select(text("count(*)")).select_from(RoomMessageRecord)) == 0
        evidence_count = await session.scalar(
            select(text("count(*)")).select_from(MessageEvidenceRecord)
        )
        assert evidence_count == 0

    with pytest.raises(DiscussionGenerationChangedError):
        await repository.insert_message(
            discussion_message(room.id, generation=1),
            [discussion_evidence()],
            expected_generation=1,
        )
    current, inserted = await repository.insert_message(
        discussion_message(room.id, generation=2),
        [discussion_evidence()],
        expected_generation=2,
    )
    assert inserted and current.sequence == 1
    assert current.discussion_generation == 2

    page = await repository.list_message_page(
        room.id,
        expected_generation=1,
        after_sequence=99,
        limit=10,
    )
    assert page.discussion_generation == 2
    assert page.reset_required is True
    assert [item.sequence for item in page.messages] == [1]


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

    async def reconcile_ingestion(_self):
        return 0

    monkeypatch.setattr(AppServices, "__init__", initialize)
    monkeypatch.setattr(AppServices, "reconcile_interrupted_ingestion_runs", reconcile_ingestion)
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


@pytest.mark.parametrize("operation", ["claim", "reconcile"])
@pytest.mark.parametrize("healthy", [True, False])
async def test_room_contention_does_not_hold_playback_or_starve_healthy_owner(
    sql_replay, operation, healthy
):
    repository, room = sql_replay
    token = uuid4()
    if healthy:
        assert await repository.claim_replay(room.id, token, lease_seconds=0.5)
    async with repository.database.session_factory() as holder:
        await holder.execute(
            select(RaceRoomRecord).where(RaceRoomRecord.id == room.id).with_for_update()
        )
        action = (
            repository.claim_replay(room.id, uuid4(), lease_seconds=30)
            if operation == "claim"
            else repository.pause_orphaned_running_rows()
        )
        result = await asyncio.wait_for(action, 0.2)
        assert result == 0
        if healthy:
            assert await asyncio.wait_for(
                repository.renew_replay(room.id, token, lease_seconds=0.5), 0.2
            )
        await holder.rollback()
    playback, stored_room = await records(repository, room)
    assert playback.current_event_sequence == 17
    assert playback.is_paused is False
    assert stored_room.status == "replaying"
    assert playback.replay_owner_token == (token if healthy else None)


async def replay_mutation(repository, room, token, operation):
    if operation == "paired":
        return await repository.update_playback(
            room.id,
            current_event_sequence=99,
            current_message_sequence=99,
            current_lap=99,
            is_paused=True,
            room_status=RoomStatus.COMPLETED,
            owner_token=token,
        )
    if operation == "status":
        return await repository.update_room_status(
            room.id,
            RoomStatus.FAILED,
            current_lap=99,
            owner_token=token,
        )
    return await repository.begin_discussion_restart(
        room.id, owner_token=token, started_at=datetime.now(UTC)
    )


async def assert_original_replay(repository, room):
    playback, stored_room = await records(repository, room)
    assert playback.current_event_sequence == 17
    assert playback.current_message_sequence == 9
    assert playback.current_lap == 4
    assert playback.is_paused is False
    assert stored_room.status == "replaying"
    assert stored_room.discussion_generation == 1
    assert stored_room.current_lap == 4
    assert stored_room.last_event_at == datetime(2026, 7, 17, 12, 4, tzinfo=UTC)


@pytest.mark.parametrize("operation", ["paired", "status", "reset"])
async def test_replay_mutations_decline_locked_room_without_starving_renewal(sql_replay, operation):
    from app.storage.room_repository import ReplayWriteBusyError

    repository, room = sql_replay
    token = uuid4()
    assert await repository.claim_replay(room.id, token, lease_seconds=0.5)
    async with repository.database.session_factory() as holder:
        await holder.execute(
            select(RaceRoomRecord).where(RaceRoomRecord.id == room.id).with_for_update()
        )
        with pytest.raises(ReplayWriteBusyError, match="retry"):
            await asyncio.wait_for(replay_mutation(repository, room, token, operation), 0.2)
        assert await repository.renew_replay(room.id, token, lease_seconds=0.5)
        await holder.rollback()
    await assert_original_replay(repository, room)
    # A valid retry remains available after contention clears.
    await replay_mutation(repository, room, token, operation)


@pytest.mark.parametrize("operation", ["paired", "status", "reset"])
async def test_expiry_during_later_mutation_rolls_back_entire_transaction(sql_replay, operation):
    from app.storage.room_repository import ReplayOwnershipLostError

    repository, room = sql_replay
    token = uuid4()
    assert await repository.claim_replay(room.id, token, lease_seconds=0.5)
    async with repository.database.session_factory() as session:
        schema = session.bind.get_execution_options()["schema_translate_map"][None]
        await session.execute(
            text(f'''
            CREATE FUNCTION "{schema}".delay_room_write() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_sleep(0.55); RETURN NEW; END;
            $$
        ''')
        )
        await session.execute(
            text(f'''
            CREATE TRIGGER delay_room_write BEFORE UPDATE ON "{schema}".race_rooms
            FOR EACH ROW EXECUTE FUNCTION "{schema}".delay_room_write()
        ''')
        )
        await session.commit()
    with pytest.raises(ReplayOwnershipLostError, match="retry"):
        await replay_mutation(repository, room, token, operation)
    assert not await repository.renew_replay(room.id, token, lease_seconds=30)
    await assert_original_replay(repository, room)


async def test_reset_later_message_lock_is_bounded_and_rolls_back(sql_replay):
    from app.storage.room_repository import ReplayWriteBusyError

    repository, room = sql_replay
    token = uuid4()
    assert await repository.claim_replay(room.id, token, lease_seconds=0.5)
    async with repository.database.session_factory() as holder:
        schema = holder.bind.get_execution_options()["schema_translate_map"][None]
        await holder.execute(text(f'LOCK TABLE "{schema}".room_messages IN ACCESS EXCLUSIVE MODE'))
        with pytest.raises(ReplayWriteBusyError, match="retry"):
            await asyncio.wait_for(
                repository.begin_discussion_restart(
                    room.id, owner_token=token, started_at=datetime.now(UTC)
                ),
                0.3,
            )
        assert await repository.renew_replay(room.id, token, lease_seconds=0.5)
        await holder.rollback()
    await assert_original_replay(repository, room)


async def test_worker_retries_busy_sql_without_reconsuming_event_while_heartbeat_renews(sql_replay):
    from tests.test_room_replay import coordinator, replay_event

    repository, room = sql_replay
    service, _, _, discussion, _, _ = coordinator(room, [replay_event(18, 5)])
    service.rooms = repository
    service.lease_seconds = 0.5
    discussion.block_on_sequence = 18
    try:
        await service.resume(room)
        await asyncio.wait_for(discussion.consume_started.wait(), 1)
        async with repository.database.session_factory() as holder:
            await holder.execute(
                select(RaceRoomRecord).where(RaceRoomRecord.id == room.id).with_for_update()
            )
            discussion.consume_release.set()
            await asyncio.sleep(0.6)
            assert not service._tasks[room.id].done()
            playback, stored_room = await records(repository, room)
            assert playback.current_event_sequence == 17
            assert stored_room.status == "replaying"
            assert discussion.consumed == [18]
            assert not await repository.claim_replay(room.id, uuid4(), lease_seconds=30)
            await holder.rollback()
        await asyncio.wait_for(service._tasks[room.id], 2)
        playback, stored_room = await records(repository, room)
        assert playback.current_event_sequence == 18
        assert stored_room.status == "completed"
        assert discussion.consumed == [18]
    finally:
        await service.close()


@pytest.mark.parametrize("operation", ["claim", "reconcile"])
async def test_room_table_contention_is_also_bounded(sql_replay, operation):
    repository, room = sql_replay
    async with repository.database.session_factory() as holder:
        schema = holder.bind.get_execution_options()["schema_translate_map"][None]
        await holder.execute(text(f'LOCK TABLE "{schema}".race_rooms IN ACCESS EXCLUSIVE MODE'))
        if operation == "claim":
            action = repository.claim_replay(room.id, uuid4(), lease_seconds=30)
        else:
            action = repository.pause_orphaned_running_rows()
        assert await asyncio.wait_for(action, 0.3) == 0
        await holder.rollback()


async def test_missing_playback_claim_does_not_wait_indefinitely_on_room_fk(sql_replay):
    repository, room = sql_replay
    async with repository.database.session_factory() as session:
        await session.delete(await session.get(RoomPlaybackStateRecord, room.id))
        await session.commit()
    async with repository.database.session_factory() as holder:
        await holder.execute(
            select(RaceRoomRecord).where(RaceRoomRecord.id == room.id).with_for_update()
        )
        assert not await asyncio.wait_for(
            repository.claim_replay(room.id, uuid4(), lease_seconds=30), 0.3
        )
        await holder.rollback()
    playback, _ = await records(repository, room)
    assert playback is None
