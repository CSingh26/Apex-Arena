# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain import strategy_situations as domain
from app.domain.models import RaceEventType
from app.services.race_state import RaceStateEngine
from tests.test_race_history import fact
from tests.test_race_state import SnapshotRepository


def frame(**updates):
    return domain.StrategyFrame(
        session_key="history-test",
        sequence=1,
        history_sequence=0,
        analysis_time=datetime(2026, 9, 12, tzinfo=UTC),
        semantic_identity="synthetic:test",
        **updates,
    )


def test_empty_frame_has_eight_honest_capabilities_and_bounded_header():
    value = frame()
    assert len(value.capabilities) == 8
    assert (
        value.capabilities["extra_stop_consequence"].reason
        == "missing_authoritative_remaining_distance"
    )
    assert len(domain.encoded(value)) <= 8192
    assert value.projection_status == "acknowledged_at_cursor"


@pytest.mark.parametrize(
    "updates",
    [
        {"history_sequence": 2},
        {"analysis_time": datetime(2026, 9, 12)},
        {"session_key": "x" * 129},
        {"semantic_identity": "x" * 257},
    ],
)
def test_frame_rejects_unbound_identity_clock_or_future_history(updates):
    data = frame().model_dump()
    data.update(updates)
    with pytest.raises(ValidationError):
        domain.StrategyFrame.model_validate(data)


def test_frame_rejects_orphan_or_future_evidence():
    ref = domain.StrategyEvidence(
        event_id=UUID(int=1),
        sequence=1,
        observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        source="synthetic",
        session_key="history-test",
        role="weather",
        family="WEATHER_UPDATE",
    )
    with pytest.raises(ValidationError):
        frame(evidence={ref.key: ref})


@pytest.mark.asyncio
async def test_owned_evaluation_is_exact_detached_and_never_exports_history(monkeypatch):
    engine = RaceStateEngine(SnapshotRepository(), algorithm_version="synthetic:test")
    source = fact(1, RaceEventType.CAR_DATA_SAMPLE, speed=200)
    state = await engine.apply(source, persist_snapshot=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("full history export")

    monkeypatch.setattr(engine, "export_factual_context", forbidden)
    original = domain.StrategyDelta(frame=frame())

    def evaluate(context):
        assert context.session_key == source.session_key
        assert context.relevant_sequence == 0  # An irrelevant source legitimately has H < C.
        assert context.analysis_time == source.event_time
        return original

    result = await engine.evaluate_owned_facts(source, state, evaluate)
    result.frame.limitations.append("caller_mutation")
    assert original.frame.limitations == []
    wrong = source.model_copy(update={"sequence_number": 2})
    with pytest.raises(ValueError, match="cursor"):
        await engine.evaluate_owned_facts(wrong, state, evaluate)
    with pytest.raises(ValueError, match="cursor"):
        await RaceStateEngine(SnapshotRepository()).evaluate_owned_facts(source, state, evaluate)
    with pytest.raises((ValueError, TypeError, ValidationError)):
        await engine.evaluate_owned_facts(source, state, lambda context: context)
