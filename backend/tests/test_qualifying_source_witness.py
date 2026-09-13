# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta

import pytest

from app.domain.models import EventOrigin, RaceEventType
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceStateEngine
from tests.test_history_integration import START
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql
from tests.test_race_history import fact
from tests.test_race_state import SnapshotRepository

BEST_TYPES = {RaceEventType.PERSONAL_BEST, RaceEventType.FASTEST_LAP}


def facts(scenario):
    if scenario == "deleted_tie":
        rows = [
            fact(1, lap=1, lap_duration=88),
            fact(2, RaceEventType.LAP_DELETED, lap=2),
            fact(
                3, lap=2, lap_duration=88, normalized_session_type="QUALIFYING", session_phase="Q1"
            ),
        ]
    else:
        rows = [
            fact(
                1, lap=1, lap_duration=90, normalized_session_type="QUALIFYING", session_phase="Q1"
            ),
            fact(2, lap=2, lap_duration=89, session_phase="Q2"),
            fact(
                3,
                lap=1,
                lap_duration=88,
                **({"session_phase": "Q1"} if scenario == "late_phase" else {}),
            ),
        ]
    for row in rows:
        row.event_time = START + timedelta(seconds=row.sequence_number * 60)
    return rows


def assert_effects(scenario, source, derived, state):
    from app.services.agent_grounding import AgentEventEnvelope

    best = [row for row in derived if row.event_type in BEST_TYPES]
    if scenario == "deleted_tie":
        assert best == [], "equal duration does not establish valid addressed lap identity"
        return
    assert {row.event_type for row in best} == BEST_TYPES
    for row in best:
        assert row.lap_number == 1
        assert row.payload["lap_duration"] == 88
        assert row.payload.get("session_phase") == ("Q1" if scenario == "late_phase" else None)
        assert row.derivation.evidence[0].event_id == source.id
        assert row.derivation.evidence[0].observed_at == source.event_time
        assert (
            AgentEventEnvelope.from_event(row, state).session_phase == row.payload["session_phase"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["deleted_tie", "late_phase", "unknown_phase"])
async def test_coordinator_requires_source_lap_witness_and_uses_its_phase(scenario):
    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)
    rows = facts(scenario)
    original = [row.model_dump() for row in rows]
    for row in rows:
        effects = await coordinator.advance_source(
            row, await engine.apply(row, persist_snapshot=False)
        )
    state = await engine.get_state("history-test")
    assert state.qualifying_intelligence.best_laps == {4: 88}
    assert state.current_phase == ("Q1" if scenario == "deleted_tie" else "Q2")
    assert_effects(scenario, rows[-1], effects.derived, state)
    assert [row.model_dump() for row in rows] == original


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["deleted_tie", "late_phase", "unknown_phase"])
async def test_critical_bundle_witness_preserves_prior_rows_and_effects_off_parity(
    intelligence_sql, scenario
):
    from tests.test_ingestion_recovery import processor, raw

    pipeline, projection, public, _ = processor(intelligence_sql)
    rows = facts(scenario)

    async def ingest(row):
        payload = {
            **row.payload,
            "normalized_session_type": row.payload.get(
                "normalized_session_type", "QUALIFYING" if scenario != "deleted_tie" else None
            ),
            "lap_number": row.lap_number,
        }
        if row.event_type is RaceEventType.LAP_DELETED:
            payload["message"] = "LAP TIME DELETED"
        await pipeline.ingest(
            raw(
                row.sequence_number * 60,
                "race_control" if row.event_type is RaceEventType.LAP_DELETED else "laps",
                **payload,
            )
        )

    for row in rows[:-1]:
        await ingest(row)
    before = await pipeline.normalized_repository.list_for_session("race", limit=100)
    immutable = [row.model_dump() for row in before]
    await ingest(rows[-1])
    after = await pipeline.normalized_repository.list_for_session("race", limit=100)
    assert [row.model_dump() for row in after[: len(before)]] == immutable
    new = after[len(before) :]
    source = next(row for row in new if row.event_origin is EventOrigin.SOURCE_FACT)
    current = await public.get_state("race")
    assert_effects(
        scenario, source, [row for row in new if row.event_origin is EventOrigin.DERIVED], current
    )
    assert current.current_phase == ("Q1" if scenario == "deleted_tie" else "Q2")
    restored = RaceStateEngine(SnapshotRepository(), algorithm_version="test-v1")
    coordinator = RaceIntelligenceCoordinator(restored)
    await coordinator.restore_session("race", pipeline.normalized_repository)
    assert (
        await restored.get_state("race")
    ).qualifying_intelligence == current.qualifying_intelligence
    assert coordinator.drain_derived("race") == []
    assert [
        row.model_dump()
        for row in await pipeline.normalized_repository.list_for_session("race", limit=100)
    ] == [row.model_dump() for row in after]
    await pipeline.ingest(
        raw(
            300,
            "laps",
            lap_number=4,
            lap_duration=87,
            normalized_session_type="QUALIFYING",
            session_phase="Q1" if scenario == "deleted_tie" else "Q2",
        )
    )
    continued = await pipeline.normalized_repository.list_for_session("race", limit=100)
    continuation = continued[len(after) :]
    next_source = next(row for row in continuation if row.event_origin is EventOrigin.SOURCE_FACT)
    replayed = await coordinator.advance_source(
        next_source, await restored.apply(next_source, persist_snapshot=False)
    )

    def signature(row):
        return row.event_type, row.payload, row.derivation.model_dump(), row.dedup_key

    assert [signature(row) for row in replayed.derived] == [
        signature(row) for row in continuation if row.event_origin is EventOrigin.DERIVED
    ]
    assert [row.model_dump() for row in continued[: len(after)]] == [
        row.model_dump() for row in after
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch", ["absent", "deleted", "id", "sequence", "time", "source", "lap", "duration"]
)
async def test_best_event_rejects_missing_or_mismatched_source_witness(mismatch):
    from uuid import uuid4

    from app.services.qualifying_intelligence import QualifyingEngine

    engine = RaceStateEngine(SnapshotRepository())
    source = facts("late_phase")[0]
    state = await engine.apply(source, persist_snapshot=False)
    witness = await engine.source_lap_witness(source)
    assert witness is not None
    if mismatch == "absent":
        witness = None
    elif mismatch == "deleted":
        witness.deleted = True
    elif mismatch == "id":
        witness.evidence.event_id = uuid4()
    elif mismatch == "sequence":
        witness.evidence.sequence += 1
    elif mismatch == "time":
        witness.evidence.observed_at += timedelta(seconds=1)
    elif mismatch == "source":
        witness.evidence.source = "other"
    elif mismatch == "lap":
        witness.lap_number += 1
    elif mismatch == "duration":
        witness.duration_seconds = 91
    assert QualifyingEngine().apply(source, state, lap_witness=witness) == []
    retained = await engine.source_lap_witness(source)
    assert not retained.deleted and retained.evidence.event_id == source.id
    assert retained.lap_number == source.lap_number and retained.duration_seconds == 90
