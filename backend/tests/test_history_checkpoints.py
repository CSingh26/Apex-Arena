# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util

import pytest
from sqlalchemy import func, select

from tests.test_ingestion_recovery import processor, raw
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


@pytest.mark.asyncio
async def test_relevant_checkpoint_is_atomic_and_unchanged_telemetry_reuses_blob(intelligence_sql):
    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    state = await public.get_state("race")
    assert state.history_reference is not None
    assert state.history_reference.algorithm_version == "test-v1"
    assert state.history_reference.base_sequence == state.history_reference.relevant_sequence == 1
    assert importlib.util.find_spec("app.storage.history_checkpoints") is not None
    from app.storage.models import SessionHistoryCheckpointRecord

    for index in range(2, 22):
        await pipeline.ingest(raw(index, "car_data", speed=200 + index))
    async with intelligence_sql.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(SessionHistoryCheckpointRecord))
            == 1
        )
    current = await public.get_state("race")
    assert current.history_reference == state.history_reference
    assert current.sequence_number == 21
    progress = await projection.repository.load("race")
    assert progress.history_reference == state.history_reference
    snapshots = await public.snapshots.latest("race")
    assert snapshots.state["history_reference"] == state.history_reference.model_dump(mode="json")


@pytest.mark.asyncio
async def test_checkpoint_failure_keeps_source_pending_and_no_candidate_history_leaks(
    intelligence_sql, monkeypatch
):
    pipeline, projection, public, _ = processor(intelligence_sql)
    assert hasattr(projection.repository, "_persist_detail"), "atomic detail insertion seam missing"
    persist = projection.repository._persist_detail

    async def fail(*args, **kwargs):
        await persist(*args, **kwargs)
        raise RuntimeError("synthetic checkpoint failure")

    monkeypatch.setattr(projection.repository, "_persist_detail", fail)
    with pytest.raises(RuntimeError, match="synthetic checkpoint"):
        await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    assert (await projection.repository.load("race")).pending_source_id is not None
    assert (await public.get_state("race")).sequence_number == 0
    from app.storage.models import SessionHistoryCheckpointRecord

    async with intelligence_sql.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(SessionHistoryCheckpointRecord))
            == 0
        )
    monkeypatch.setattr(projection.repository, "_persist_detail", persist)
    await pipeline.recover_session("race")
    assert (await public.get_state("race")).history_reference.relevant_sequence == 1
    assert (await projection.repository.load("race")).pending_source_id is None


@pytest.mark.asyncio
async def test_actual_bundle_keeps_all_event_ids_but_publishes_one_final_state(intelligence_sql):
    from app.storage.redis import RaceEventRedisPublisher

    pipeline, _, public, _ = processor(intelligence_sql)

    class Bus:
        def __init__(self):
            self.events, self.states = [], []

        async def publish_event(self, event):
            self.events.append(event)

        async def publish_state(self, state):
            self.states.append(state)

    bus = Bus()
    pipeline.consumers.append(RaceEventRedisPublisher(bus, public))
    await pipeline.ingest(raw(1, "position", driver_number=16, position=4))
    await pipeline.ingest(raw(2, "position", position=5))
    for index in range(3, 6):
        await pipeline.ingest(raw(index, interval=2 - index / 10))
    rows = await pipeline.normalized_repository.list_for_session("race", limit=100)
    assert len(rows) > 5, "actual detector must produce source+derived bundle"
    assert [event.id for event in bus.events] == [event.id for event in rows]
    assert len(bus.states) == 5
    assert bus.states[-1].sequence_number == rows[-1].sequence_number


@pytest.mark.asyncio
async def test_snapshot_conflict_is_not_silent_idempotence(intelligence_sql):
    from uuid import uuid4

    from app.domain.models import RaceStateSnapshot
    from app.storage.repositories import SqlRaceStateSnapshotRepository
    from tests.test_race_history import fact

    repository = SqlRaceStateSnapshotRepository(intelligence_sql)
    event = fact(1)
    snapshot = RaceStateSnapshot(
        session_key="history-test",
        snapshot_time=event.event_time,
        sequence_number=1,
        state={"session_key": "history-test", "sequence_number": 1},
        created_at=event.event_time,
    )
    assert (await repository.insert(snapshot)).is_new
    assert not (await repository.insert(snapshot.model_copy(update={"id": uuid4()}))).is_new
    with pytest.raises(RuntimeError, match="[Ss]napshot.*conflict"):
        await repository.insert(
            snapshot.model_copy(
                update={
                    "id": uuid4(),
                    "state": {
                        "session_key": "history-test",
                        "sequence_number": 1,
                        "status": "finished",
                    },
                }
            )
        )


@pytest.mark.asyncio
async def test_snapshot_resolver_never_uses_future_or_incompatible_base(intelligence_sql):
    import time

    pipeline, _, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    for index in range(2, 21):
        await pipeline.ingest(raw(index, "car_data", speed=200 + index))
    repository = public.snapshots
    assert hasattr(repository, "at_or_before"), "bounded compatible resolver missing"

    async def resolve(cursor, algorithm="test-v1"):
        return await repository.at_or_before(
            "race",
            cursor,
            algorithm_identity=algorithm,
            snapshot_schema_version=1,
            lower_sequence=max(0, cursor - 2047),
            statement_deadline=time.monotonic() + 2,
        )

    assert await resolve(9) is None
    assert (await resolve(19)).sequence_number == 10
    assert (await resolve(20)).sequence_number == 20
    assert await resolve(20, "other-algorithm") is None
    snapshot = await resolve(19)
    snapshot.state["history_reference"]["base_sequence"] = 999
    assert (await resolve(19)).state["history_reference"]["base_sequence"] == 1


@pytest.mark.asyncio
async def test_reference_relevant_source_identity_is_checked_before_ack(
    intelligence_sql, monkeypatch
):
    from uuid import uuid4

    pipeline, projection, public, _ = processor(intelligence_sql)
    commit = projection.repository.commit_projection

    async def corrupted(*args, **kwargs):
        kwargs["history_reference"] = kwargs["history_reference"].model_copy(
            update={"relevant_event_id": uuid4()}
        )
        return await commit(*args, **kwargs)

    monkeypatch.setattr(projection.repository, "commit_projection", corrupted)
    with pytest.raises(RuntimeError, match="source"):
        await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    assert (await public.get_state("race")).sequence_number == 0
    assert (await projection.repository.load("race")).pending_source_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", ["snapshot", "progress"])
async def test_late_transaction_failure_rolls_back_detail_snapshot_and_ack(
    intelligence_sql, monkeypatch, seam
):
    from sqlalchemy import text

    from app.storage.models import RaceStateSnapshotRecord, SessionHistoryCheckpointRecord
    from app.storage.repositories import SqlRaceStateSnapshotRepository

    pipeline, projection, public, _ = processor(intelligence_sql)
    public.snapshot_every_n_events = 1
    insert_snapshot = SqlRaceStateSnapshotRepository.insert_in_transaction
    if seam == "snapshot":

        async def fail(*args):
            await insert_snapshot(*args)
            raise RuntimeError("synthetic snapshot failure")

        monkeypatch.setattr(SqlRaceStateSnapshotRepository, "insert_in_transaction", fail)
    else:
        # Owned UUID schema only: the trigger is removed by fixture cleanup.
        async with intelligence_sql.session_factory() as session:
            schema = session.bind.get_execution_options()["schema_translate_map"][None]
            await session.execute(
                text(
                    f'''CREATE FUNCTION "{schema}".fail_history_ack() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                    IF OLD.pending_source_id IS NOT NULL AND NEW.pending_source_id IS NULL
                    THEN RAISE EXCEPTION 'synthetic progress failure'; END IF;
                    RETURN NEW; END $$'''
                )
            )
            await session.execute(
                text(
                    f'''CREATE TRIGGER fail_history_ack BEFORE UPDATE ON
                    "{schema}".session_intelligence_progress FOR EACH ROW
                    EXECUTE FUNCTION "{schema}".fail_history_ack()'''
                )
            )
            await session.commit()
    with pytest.raises(Exception, match="synthetic"):
        await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    async with intelligence_sql.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(SessionHistoryCheckpointRecord))
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(RaceStateSnapshotRecord)) == 0
        if seam == "progress":
            await session.execute(
                text(f'DROP TRIGGER fail_history_ack ON "{schema}".session_intelligence_progress')
            )
            await session.commit()
    monkeypatch.setattr(SqlRaceStateSnapshotRepository, "insert_in_transaction", insert_snapshot)
    assert (await projection.repository.load("race")).pending_source_id is not None
    assert (await public.get_state("race")).sequence_number == 0
    await pipeline.recover_session("race")
    assert (await public.get_state("race")).history_reference.relevant_sequence == 1


@pytest.mark.asyncio
async def test_ambiguous_commit_recovery_reuses_same_checkpoint_without_notification(
    intelligence_sql, monkeypatch
):
    from app.storage.models import SessionHistoryCheckpointRecord

    pipeline, projection, public, notified = processor(intelligence_sql)
    commit = projection.repository.commit_projection

    async def lost_response(*args, **kwargs):
        await commit(*args, **kwargs)
        raise RuntimeError("synthetic response lost")

    monkeypatch.setattr(projection.repository, "commit_projection", lost_response)
    with pytest.raises(RuntimeError, match="response lost"):
        await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    anchor = await projection.repository.load("race")
    assert anchor.pending_source_id is None
    monkeypatch.setattr(projection.repository, "commit_projection", commit)
    await pipeline.recover_session("race")
    assert (await public.get_state("race")).history_reference == anchor.history_reference
    assert not notified.events
    async with intelligence_sql.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(SessionHistoryCheckpointRecord))
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_base", [False, True])
async def test_oversize_history_acknowledges_facts_and_recovers_at_bounded_cadence(
    intelligence_sql, monkeypatch, existing_base
):
    from app.services import history_codec
    from app.services.history_details import HistoryDetailReader

    pipeline, projection, public, _ = processor(intelligence_sql)
    public.snapshot_every_n_events = 1
    if existing_base:
        await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    # Exercise the actual encoded-byte branch/SQL transaction with a reduced
    # synthetic cap. The full-size legal overflow is measured separately.
    monkeypatch.setattr(history_codec, "MAX_CHECKPOINT_BYTES", 1)
    first = 2 if existing_base else 1
    # Explicit terminal is a dirty immediate cadence boundary with an old B.
    if existing_base:
        await pipeline.ingest(
            raw(
                first,
                "race_control",
                category="SessionStatus",
                message="SESSION FINISHED",
                status="Finished",
            )
        )
    else:
        await pipeline.ingest(raw(first, "laps", lap_number=1, lap_duration=90))
    state = await public.get_state("race")
    assert state.history_detail_status == "checkpoint_bytes"
    assert (state.history_reference is not None) == existing_base
    assert (await projection.repository.load("race")).history_detail_status == "checkpoint_bytes"
    assert (await projection.repository.load("race")).pending_source_id is None
    assert (await public.snapshots.latest("race")).state[
        "history_detail_status"
    ] == "checkpoint_bytes"
    result = await HistoryDetailReader(intelligence_sql, algorithm_version="test-v1").read(
        "race", drivers=[4], families=["laps"], view=state
    )
    assert result["reason"] == "checkpoint_bytes"
    assert "data" not in result
    await pipeline.ingest(raw(first + 1, "car_data", speed=210))
    new_pipeline, new_projection, new_public, _ = processor(intelligence_sql)
    await new_pipeline.recover_session("race")
    recovered = await new_public.get_state("race")
    assert recovered.history_detail_status == "checkpoint_bytes"
    assert recovered.history_sequence == state.history_sequence
    # A healthy encoder must not be invoked by the next63 relevant facts, even
    # when there was no successful B. On the64th it recovers availability.
    monkeypatch.setattr(history_codec, "MAX_CHECKPOINT_BYTES", 8 * 1024 * 1024)
    import app.services.history_checkpointing as checkpointing

    encode = checkpointing.encode_context
    calls = 0

    def counted(*args):
        nonlocal calls
        calls += 1
        return encode(*args)

    monkeypatch.setattr(checkpointing, "encode_context", counted)
    for index in range(1, 65):
        await new_pipeline.ingest(
            raw(first + 1 + index, "laps", lap_number=1, lap_duration=90 + index / 100)
        )
        if index < 64:
            assert calls == 0
    assert calls == 1
    assert (await new_public.get_state("race")).history_detail_status == "available"
    assert (await new_projection.repository.load("race")).history_detail_status == "available"
