# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from sqlalchemy import update

from app.services.race_state import RaceStateEngine
from app.storage.models import SessionIntelligenceProgressRecord
from app.storage.room_repository import SqlRaceRoomRepository
from tests.test_ingestion_recovery import processor, raw
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql
from tests.test_room_replay import replay_room


async def test_room_reschedule_preserves_capture_anchor_across_database_roundtrip(
    intelligence_sql, settings
):
    from datetime import timedelta

    from app.services.session_capture import capture_policy

    repo = SqlRaceRoomRepository(intelligence_sql)

    async def schema_ready():
        pass  # UUID fixture creates current metadata, not a deployed migration database.

    intelligence_sql.require_ingestion_schema = schema_ready
    original = replay_room()
    await repo.upsert_room(original, [])
    changed = original.model_copy(
        update={"scheduled_start": original.scheduled_start + timedelta(hours=6)}
    )
    await repo.upsert_room(changed, [])
    stored = await repo.get_room(original.slug)
    assert stored.scheduled_start == changed.scheduled_start
    assert stored.capture_anchor_start == original.scheduled_start
    assert not capture_policy(
        stored.capture_anchor_start, original.scheduled_start + timedelta(hours=12)
    ).eligible
    from tests.test_live_ingestion import runtime

    restarted = await runtime(settings)
    restarted.service.repository = repo
    restarted.service._catalog_next_at = original.scheduled_start + timedelta(days=1)
    await restarted.service.run_once(now=original.scheduled_start + timedelta(hours=12))
    assert not restarted.service.sessions


async def test_catalog_cancellation_is_durable_and_false_cannot_reinstate(
    intelligence_sql, settings
):
    from datetime import timedelta

    from app.services.rooms import RaceRoomService
    from tests.test_live_ingestion import runtime
    from tests.test_live_room_repair import START, monza, provider
    from tests.test_race_rooms_service import FakeSeason

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    r = await runtime(settings)
    repo = SqlRaceRoomRepository(intelligence_sql)
    r.rooms.repository = repo
    await r.rooms.sync_meetings([monza()], [{**provider(), "is_cancelled": True}], now=START)
    await r.rooms.sync_meetings(
        [monza()], [{**provider(), "is_cancelled": False}], now=START + timedelta(seconds=1)
    )
    fresh = RaceRoomService(repo, FakeSeason([]), 2026)
    fresh._catalog_ready = True
    events, _ = await fresh.grouped_events(now=START + timedelta(seconds=2))
    assert events[0].sessions[0].status.value == "cancelled"
    r.service.repository = repo
    r.service.rooms = fresh
    r.service._catalog_next_at = START + timedelta(hours=1)
    await r.service.run_once(now=START + timedelta(seconds=3))
    assert not r.service.sessions

    # A different provider identity is not an automatic reversal of this one.
    room = (await repo.list_rooms(include_unavailable=True))[0][0]
    replacement = room.model_copy(
        update={"session_key": "replacement-synthetic", "provider_cancelled": False}
    )
    await repo.upsert_room(replacement, [])
    assert not (await repo.get_room(room.slug)).provider_cancelled


@pytest.mark.parametrize("observation_seconds", [1, -3600])
async def test_pending_unavailable_cancellation_persists_without_opening_room(
    intelligence_sql, settings, observation_seconds
):
    from datetime import timedelta

    from app.domain.rooms import RoomStatus, SourceAvailability
    from app.services.rooms import RaceRoomService
    from app.storage.models import RaceRoomRecord
    from tests.test_live_ingestion import runtime
    from tests.test_live_room_repair import START, monza, provider
    from tests.test_race_rooms_service import FakeSeason

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    repo = SqlRaceRoomRepository(intelligence_sql)
    r = await runtime(settings)
    r.rooms.repository = repo
    await r.rooms.sync_meetings([monza()], [provider()], now=START)
    initial = (await repo.list_rooms(include_unavailable=True))[0][0]
    async with intelligence_sql.session_factory() as session:
        await session.execute(
            update(RaceRoomRecord)
            .where(RaceRoomRecord.id == initial.id)
            .values(status="pending", source_availability="unavailable")
        )
        await session.commit()
    await r.rooms.sync_meetings(
        [monza()],
        [{**provider(), "is_cancelled": True}],
        now=START + timedelta(seconds=observation_seconds),
    )
    stored = await repo.get_room(initial.slug)
    assert stored.provider_cancelled
    assert stored.status is RoomStatus.PENDING
    assert stored.source_availability is SourceAvailability.UNAVAILABLE
    assert stored.mode == initial.mode
    assert not stored.replay_available
    assert not stored.results_available
    fresh = RaceRoomService(repo, FakeSeason([]), 2026)
    fresh._catalog_ready = True
    events, _ = await fresh.grouped_events(now=START + timedelta(seconds=2))
    summary = events[0].sessions[0]
    assert summary.capture_state == "cancelled"
    assert summary.status.value == "cancelled"
    assert not summary.room_eligible
    assert summary.room_slug is None
    eligibility = fresh.eligibility.evaluate(
        scheduled_start=stored.scheduled_start,
        actual_status="cancelled",
        provider_session_available=False,
        existing_room=stored,
        now=START + timedelta(seconds=2),
    )
    assert not eligibility.can_create and not eligibility.can_open and not eligibility.can_replay
    r.service.repository = repo
    r.service.rooms = fresh
    r.service._catalog_next_at = START + timedelta(hours=1)
    r.client.reads.clear()
    await r.service.run_once(now=START + timedelta(seconds=3))
    assert not r.service.sessions
    assert not r.client.reads

    # Stale catalog observation cannot cancel a separately identified replacement.
    replacement = stored.model_copy(
        update={"session_key": "replacement-synthetic", "provider_cancelled": False}
    )
    await repo.upsert_room(replacement, [])
    assert not await repo.observe_catalog_cancellation(stored.id, stored.session_key)
    assert not (await repo.get_room(stored.slug)).provider_cancelled


@pytest.mark.parametrize("invalid", [None, "pending", "legacy", "algorithm"])
async def test_acknowledged_cancellation_sql_authority(intelligence_sql, invalid):
    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "sessions", is_cancelled=True))
    await public.reset_session("race", is_replay=True)
    if invalid:
        values = (
            {"pending_source_id": __import__("uuid").uuid4()}
            if invalid == "pending"
            else {"historical_effects_unverified": True}
            if invalid == "legacy"
            else {"algorithm_version": "old"}
        )
        async with intelligence_sql.session_factory() as session:
            await session.execute(
                update(SessionIntelligenceProgressRecord)
                .where(SessionIntelligenceProgressRecord.session_key == "race")
                .values(**values)
            )
            await session.commit()
    repo = SqlRaceRoomRepository(intelligence_sql)
    observed = await repo.confirmed_cancelled_sessions(
        ["race"], algorithm_version=projection.repository.algorithm_version
    )
    assert observed == ({"race"} if invalid is None else set())


@pytest.mark.parametrize("value", ["true", "cancelled", None])
async def test_cancellation_sql_does_not_coerce_nonboolean_metadata(intelligence_sql, value):
    pipeline, projection, _, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "sessions", is_cancelled=value))
    assert not await SqlRaceRoomRepository(intelligence_sql).confirmed_cancelled_sessions(
        ["race"], algorithm_version=projection.repository.algorithm_version
    )


async def test_committed_cancellation_restart_catalog_and_rest_stay_closed(
    intelligence_sql, settings
):
    from datetime import timedelta

    from app.services.rooms import RaceRoomService
    from tests.test_live_ingestion import runtime
    from tests.test_live_room_repair import START
    from tests.test_race_rooms_service import FakeSeason

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    pipeline, projection, _, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "sessions", is_cancelled=True))
    await pipeline.ingest(raw(2, "sessions", is_cancelled=False))
    repo = SqlRaceRoomRepository(intelligence_sql)
    room = replay_room(session_key="race").model_copy(update={"scheduled_start": START})
    await repo.upsert_room(room, [])
    catalog = RaceRoomService(repo, FakeSeason([]), 2026)
    catalog.intelligence_algorithm_version = projection.repository.algorithm_version
    catalog._catalog_ready = True
    events, _ = await catalog.grouped_events(now=START + timedelta(seconds=3))
    assert events[0].sessions[0].capture_state == "cancelled"
    assert events[0].sessions[0].status.value == "cancelled"
    r = await runtime(settings)
    r.service.processor, _, r.service.race_state, _ = processor(intelligence_sql)
    r.service.repository = repo
    r.service.rooms = catalog
    r.service._catalog_next_at = START + timedelta(hours=1)
    await r.service.run_once(now=START + timedelta(seconds=4))
    assert not r.service.sessions
    assert not r.client.reads


async def test_provider_cancellation_migration_defaults_unknown_legacy_to_not_observed(
    intelligence_sql,
):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration = importlib.import_module("migrations.versions.20260913_0021_provider_cancellation")

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    room = replay_room()
    await SqlRaceRoomRepository(intelligence_sql).upsert_room(room, [])
    engine = intelligence_sql.session_factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"][None]
    async with engine.begin() as connection:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("ALTER TABLE race_rooms DROP COLUMN provider_cancelled"))

        def run(sync_connection, direction):
            original = migration.op
            migration.op = Operations(MigrationContext.configure(sync_connection))
            try:
                getattr(migration, direction)()
            finally:
                migration.op = original

        await connection.run_sync(run, "upgrade")
        assert await connection.scalar(text("SELECT provider_cancelled FROM race_rooms")) is False
        assert (
            await connection.scalar(text("SELECT scheduled_start FROM race_rooms"))
            == room.scheduled_start
        )
        await connection.run_sync(run, "downgrade")
        await connection.run_sync(run, "upgrade")
        assert await connection.scalar(text("SELECT count(*) FROM race_rooms")) == 1


async def test_capture_anchor_additive_migration_preserves_legacy_schedule(intelligence_sql):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration = importlib.import_module("migrations.versions.20260913_0020_capture_anchor")

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    original = replay_room()
    await SqlRaceRoomRepository(intelligence_sql).upsert_room(original, [])
    engine = intelligence_sql.session_factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"][None]
    async with engine.begin() as connection:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("ALTER TABLE race_rooms DROP COLUMN capture_anchor_start"))

        def run(sync_connection, direction):
            original = migration.op
            migration.op = Operations(MigrationContext.configure(sync_connection))
            try:
                getattr(migration, direction)()
            finally:
                migration.op = original

        await connection.run_sync(run, "upgrade")
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM race_rooms WHERE capture_anchor_start IS NOT NULL")
            )
            == 0
        )
        await connection.run_sync(run, "downgrade")
        await connection.run_sync(run, "upgrade")
        assert (
            await connection.scalar(text("SELECT scheduled_start FROM race_rooms"))
            == original.scheduled_start
        )


@pytest.mark.parametrize("invalid", [None, "pending", "legacy", "algorithm"])
async def test_catalog_terminal_authority_is_committed_verified_source_not_replay(
    intelligence_sql, invalid
):
    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(
        raw(1, "race_control", category="SessionStatus", message="SESSION FINISHED")
    )
    repo = SqlRaceRoomRepository(intelligence_sql)
    # User-selected replay views cannot alter committed catalog authority.
    await public.reset_session("race", is_replay=True)
    if invalid:
        values = (
            {"pending_source_id": __import__("uuid").uuid4()}
            if invalid == "pending"
            else {"historical_effects_unverified": True}
            if invalid == "legacy"
            else {"algorithm_version": "old"}
        )
        async with intelligence_sql.session_factory() as session:
            await session.execute(
                update(SessionIntelligenceProgressRecord)
                .where(SessionIntelligenceProgressRecord.session_key == "race")
                .values(**values)
            )
            await session.commit()
    terminal = await repo.confirmed_terminal_sessions(
        ["race"], algorithm_version=projection.repository.algorithm_version
    )
    assert terminal == ({"race"} if invalid is None else set())


async def test_control_source_recorded_replay_restart_cursor_parity(intelligence_sql):
    pipeline, projection, public, notified = processor(intelligence_sql)
    facts = [
        raw(1, "race_control", category="SessionStatus", message="SESSION STARTED"),
        raw(3, "race_control", message="SAFETY CAR DEPLOYED"),
        raw(4, "race_control", category="SessionStatus", message="SESSION RESUMED"),
        raw(2, "race_control", flag="RED"),
        raw(5, "race_control", category="SessionStatus", message="Q1 FINISHED"),
        raw(6, "race_control", category="SessionStatus", message="SESSION FINISHED"),
    ]
    replay = RaceStateEngine(public.snapshots)
    await replay.reset_session("race", is_replay=True)
    cursor = 0
    for fact in facts:
        await pipeline.ingest(fact)
        expected = await public.get_state("race")
        rows = await pipeline.normalized_repository.list_for_session(
            "race", after_sequence=cursor, limit=100
        )
        for row in rows:
            await replay.apply(row.model_copy(update={"is_replay": True}), persist_snapshot=False)
        cursor = expected.sequence_number
        assert (await replay.get_state("race")).control == expected.control
        restarted, restored, current, effects = processor(intelligence_sql)
        await restarted.initialize_session("race")
        assert (await current.get_state("race")).control == expected.control
        assert not effects.events
    assert expected.control.lifecycle.value == "finished"
    assert expected.control.neutralization.value == "safety_car"


async def test_truncated_late_finish_sql_restart_replay_and_capture_agree(
    intelligence_sql, settings
):
    from datetime import UTC, datetime, timedelta

    from app.services.control_state import racing_inference_blocked
    from app.services.live_ingestion import LiveSessionProgress
    from tests.test_live_ingestion import runtime
    from tests.test_race_intelligence import START
    from tests.test_race_state import SnapshotRepository

    pipeline, projection, public, _ = processor(intelligence_sql)
    r = await runtime(settings)
    r.service.processor = pipeline
    r.service.repository = SqlRaceRoomRepository(intelligence_sql)
    r.service.race_state = public
    now = datetime.now(UTC)
    room = replay_room(session_key="race").model_copy(update={"scheduled_start": now})
    r.service.sessions["race"] = LiveSessionProgress(room=room, admission_ready=True)
    pipeline.consumers.append(r.service)
    for index in range(1, 123):
        payload = (
            {"category": "SessionStatus", "message": "SESSION RESUMED"}
            if index == 1
            else {"flag": "GREEN"}
        )
        await pipeline.ingest(
            raw(
                index,
                "race_control",
                date=(START + timedelta(seconds=100 + index)).isoformat(),
                **payload,
            )
        )
    await pipeline.ingest(
        raw(
            123,
            "race_control",
            category="SessionStatus",
            message="SESSION FINISHED",
            date=(START + timedelta(seconds=5)).isoformat(),
        )
    )
    await pipeline.ingest(
        raw(
            124,
            "race_control",
            category="SessionStatus",
            message="SESSION RESUMED",
            date=(START + timedelta(hours=1)).isoformat(),
        )
    )
    expected = await public.get_state("race")
    assert expected.control.history_truncated
    assert expected.status == "finished"
    assert racing_inference_blocked(expected.control)
    assert await r.service.repository.confirmed_terminal_sessions(
        ["race"], algorithm_version=projection.repository.algorithm_version
    ) == {"race"}
    assert await r.service.admit_mqtt("v1/position", {"session_key": "race"}, now) == "terminal"
    replay = RaceStateEngine(SnapshotRepository())
    rows = await pipeline.normalized_repository.list_for_session("race", limit=250)
    for row in rows:
        await replay.apply(row.model_copy(update={"is_replay": True}), persist_snapshot=False)
    assert (await replay.get_state("race")).control == expected.control
    restarted, _, current, effects = processor(intelligence_sql)
    await restarted.initialize_session("race")
    assert (await current.get_state("race")).control == expected.control
    assert not effects.events


@pytest.mark.parametrize("cancelled", [False, True])
async def test_staged_terminal_failure_cannot_close_mqtt_admission(
    intelligence_sql, settings, cancelled
):
    from datetime import UTC, datetime

    from app.services.live_ingestion import LiveSessionProgress
    from tests.test_live_ingestion import runtime

    pipeline, projection, public, _ = processor(intelligence_sql)
    r = await runtime(settings)
    r.service.processor = pipeline
    r.service.repository = SqlRaceRoomRepository(intelligence_sql)
    r.service.race_state = public
    now = datetime.now(UTC)
    room = replay_room(session_key="race").model_copy(update={"scheduled_start": now})
    r.service.sessions["race"] = LiveSessionProgress(room=room, admission_ready=True)
    pipeline.consumers.append(r.service)
    projection.state_publisher = r.service.publish_recovered_state
    commit = projection.repository.commit_projection

    async def fail(*args, **kwargs):
        assert (
            cancelled
            or (await projection.working_state.get_state("race")).control.lifecycle.value
            == "finished"
        )
        raise RuntimeError("synthetic commit failure")

    projection.repository.commit_projection = fail
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await pipeline.ingest(
            raw(1, "sessions", is_cancelled=True)
            if cancelled
            else raw(1, "race_control", category="SessionStatus", message="SESSION FINISHED")
        )
    assert await r.service.admit_mqtt("v1/position", {"session_key": "race"}, now) == "accepted"
    assert not r.service.sessions["race"].terminal_confirmed
    projection.repository.commit_projection = commit
    await pipeline.recover_session("race")
    # A recovered committed terminal closes admission through the state-only
    # refresh, without replaying historical notifications or a catalog poll.
    assert await r.service.admit_mqtt("v1/position", {"session_key": "race"}, now) == (
        "cancelled" if cancelled else "terminal"
    )
