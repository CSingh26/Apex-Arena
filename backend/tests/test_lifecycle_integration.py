# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.models import EventOrigin, RaceEventType
from app.services.normalization import OpenF1EventNormalizer
from app.services.race_state import RaceState, RaceStateEngine
from app.services.raw_events import RawEventInput
from tests.test_race_state import SnapshotRepository

START = datetime(2026, 9, 6, 13, tzinfo=UTC)


def normalized(endpoint, payload, sequence=1, received=START):
    return (
        OpenF1EventNormalizer()
        .normalize(
            RawEventInput(
                provider_endpoint=endpoint,
                session_key="synthetic-control",
                raw_payload=payload,
                received_at=received,
            ),
            uuid4(),
        )
        .model_copy(update={"sequence_number": sequence})
    )


@pytest.mark.asyncio
async def test_metadata_start_is_not_observed_sporting_start():
    engine = RaceStateEngine(SnapshotRepository())
    event = normalized("sessions", {"date_start": START.isoformat(), "session_name": "Race"})
    state = await engine.apply(event)
    assert state.status == "unknown"
    assert state.control.lifecycle.value == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("transitions", [2, 122])
@pytest.mark.parametrize("replay", [False, True])
async def test_late_finish_is_absorbing_across_control_retention(transitions, replay):
    from app.services.control_state import racing_inference_blocked

    engine = RaceStateEngine(SnapshotRepository())
    for sequence in range(1, transitions + 1):
        payload = (
            {"category": "SessionStatus", "message": "SESSION RESUMED"}
            if sequence == 1
            else {"flag": "GREEN"}
        )
        event = normalized(
            "race_control",
            {**payload, "date": (START + timedelta(seconds=100 + sequence)).isoformat()},
            sequence,
        ).model_copy(update={"is_replay": replay})
        await engine.apply(event)
    finish = normalized(
        "race_control",
        {
            "category": "SessionStatus",
            "message": "SESSION FINISHED",
            "date": (START + timedelta(seconds=5)).isoformat(),
        },
        transitions + 1,
    ).model_copy(update={"is_replay": replay})
    state = await engine.apply(finish)
    assert state.status == "finished"
    assert state.control.lifecycle.evidence.event_id == finish.id
    resume = normalized(
        "race_control",
        {
            "category": "SessionStatus",
            "message": "SESSION RESUMED",
            "date": (START + timedelta(hours=1)).isoformat(),
        },
        transitions + 2,
    ).model_copy(update={"is_replay": replay})
    state = await engine.apply(resume)
    assert state.control.lifecycle.value == "finished"
    assert racing_inference_blocked(state.control)
    context = await engine.export_factual_context(state.session_key)
    assert len(context.control.history) <= 120
    assert "history" not in state.model_dump()["control"]


def test_status_observation_uses_received_time_without_poll_identity_churn():
    payload = {"date_start": START.isoformat(), "status": "suspended"}
    first = normalized("sessions", payload, received=START + timedelta(hours=1))
    second = normalized("sessions", payload, received=START + timedelta(hours=2))
    assert first.event_time == START + timedelta(hours=1)
    assert first.payload["timestamp_authority"] == "received_observation"
    assert first.payload["control"]["lifecycle"] == "suspended"
    assert first.dedup_key == second.dedup_key
    assert normalized("sessions", {**payload, "status": "resumed"}).dedup_key != first.dedup_key


@pytest.mark.parametrize(
    ("payload", "kind", "control"),
    [
        ({"message": "PENALTY FOR OVERTAKING UNDER SAFETY CAR"}, RaceEventType.PENALTY, {}),
        (
            {"category": "SessionStatus", "message": "Q1 FINISHED"},
            RaceEventType.QUALIFYING_PHASE,
            {},
        ),
        (
            {"category": "SessionStatus", "message": "SESSION FINISHED"},
            RaceEventType.SESSION_FINISH,
            {"lifecycle": "finished"},
        ),
        (
            {"message": "SAFETY CAR ENDING"},
            RaceEventType.RACE_CONTROL,
            {"neutralization": "safety_car_ending"},
        ),
    ],
)
def test_actual_control_normalizer_uses_transition_not_keyword_mentions(payload, kind, control):
    event = normalized("race_control", payload)
    assert event.event_type == kind
    assert event.payload["control"] == control
    if kind is RaceEventType.QUALIFYING_PHASE:
        assert event.payload["session_phase"] == "Q1"


@pytest.mark.asyncio
async def test_control_is_source_only_relevant_and_roundtrip_safe():
    engine = RaceStateEngine(SnapshotRepository())
    start = normalized("race_control", {"category": "SessionStatus", "message": "SESSION STARTED"})
    state = await engine.apply(start)
    assert state.status == "running"
    assert state.control.sequence == 1
    await engine.apply(normalized("car_data", {"driver_number": 4, "speed": 200}, 2))
    state = await engine.apply(
        normalized("race_control", {"flag": "RED"}, 3).model_copy(
            update={"event_origin": EventOrigin.DERIVED}
        )
    )
    assert state.control.sequence == 1
    assert state.status == "running"
    assert len((await engine.export_factual_context(state.session_key)).control.history) == 1
    assert "history" not in state.model_dump()["control"]
    assert RaceState.model_validate(state.model_dump()).control == state.control


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "observed"),
    [(0, False), (1, False), (8, None), (10, True), (12, True), (14, True), (7, None)],
)
async def test_drs_eligibility_is_not_observed_opening(code, observed):
    state = await RaceStateEngine(SnapshotRepository()).apply(
        normalized("car_data", {"driver_number": 4, "drs": code})
    )
    assert state.drivers["4"].telemetry.get("drs") is observed
    assert state.drivers["4"].telemetry["drs_code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message", ["SAFETY CAR DEPLOYED", "VSC DEPLOYED", "RED FLAG", "SESSION SUSPENDED"]
)
async def test_neutralization_cancels_pending_pass_without_losing_position_facts(message):
    from app.services.race_intelligence import RaceIntelligenceCoordinator

    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)
    rows = [
        ("sessions", {"session_name": "Race"}),
        ("position", {"driver_number": 16, "position": 4}),
        ("position", {"driver_number": 4, "position": 5}),
        ("intervals", {"driver_number": 4, "interval": 0.7}),
        ("position", {"driver_number": 4, "position": 4}),
        ("position", {"driver_number": 16, "position": 5}),
    ]
    for index, (endpoint, payload) in enumerate(rows, 1):
        event = normalized(endpoint, payload, index, START + timedelta(seconds=index))
        await engine.apply(event)
        await coordinator.consume(event)
        coordinator.drain_derived(event.session_key)
    assert coordinator.diagnostics_for_session("synthetic-control").pending_overtakes == 1
    control = normalized(
        "race_control",
        {"message": message, "category": "SessionStatus"},
        7,
        START + timedelta(seconds=7),
    )
    await engine.apply(control)
    await coordinator.consume(control)
    assert coordinator.diagnostics_for_session("synthetic-control").pending_overtakes == 0
    assert not (await engine.get_state("synthetic-control")).current_battles
    for index, (endpoint, payload) in enumerate(
        [
            (
                "race_control",
                {"flag": "GREEN", "category": "SessionStatus", "message": "SESSION RESUMED"},
            ),
            ("position", {"driver_number": 4, "position": 4}),
        ],
        8,
    ):
        event = normalized(endpoint, payload, index, START + timedelta(seconds=index))
        await engine.apply(event)
        await coordinator.consume(event)
    assert not any(
        event.event_type is RaceEventType.OVERTAKE
        for event in coordinator.drain_derived("synthetic-control")
    )
    assert (await engine.get_state("synthetic-control")).drivers["4"].position == 4


@pytest.mark.asyncio
async def test_neutralized_close_intervals_cannot_start_battle():
    from app.services.race_intelligence import RaceIntelligenceCoordinator

    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)
    rows = [
        ("sessions", {"session_name": "Race"}),
        ("race_control", {"message": "SAFETY CAR DEPLOYED"}),
        ("position", {"driver_number": 16, "position": 1}),
        ("position", {"driver_number": 4, "position": 2}),
    ]
    rows += [("intervals", {"driver_number": 4, "interval": 0.7})] * 4
    for index, (endpoint, payload) in enumerate(rows, 1):
        event = normalized(endpoint, payload, index, START + timedelta(seconds=index))
        await engine.apply(event)
        await coordinator.consume(event)
    assert not (await engine.get_state("synthetic-control")).current_battles
    assert not any(
        item.event_type is RaceEventType.BATTLE_STARTED
        for item in coordinator.drain_derived("synthetic-control")
    )


@pytest.mark.asyncio
async def test_neutralized_timing_and_intelligence_expose_control_not_stale_attack():
    from app.api.schemas import IntelligenceProjectionStatus, SessionIntelligenceResponse
    from app.domain.intelligence import BattleState
    from app.services.session_realtime import timing_state

    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(normalized("position", {"driver_number": 16, "position": 1}, 1))
    await engine.apply(normalized("position", {"driver_number": 4, "position": 2}, 2))
    state = await engine.apply(normalized("race_control", {"flag": "RED"}, 3))
    state.current_battles = [
        BattleState(
            id="stale",
            session_key=state.session_key,
            lead_driver_number=16,
            chasing_driver_number=4,
            lead_position=1,
            chasing_position=2,
            interval_seconds=0.5,
            closest_interval_seconds=0.5,
            started_at=START,
            last_updated_at=START,
        )
    ]
    assert all(row.battle_context.status == "UNAVAILABLE" for row in timing_state(state).drivers)
    projection = IntelligenceProjectionStatus(status="current")
    assert SessionIntelligenceResponse.from_state(state, projection).control == state.control


@pytest.mark.asyncio
async def test_resume_without_green_does_not_claim_green_track():
    from app.services.session_realtime import timing_state

    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(normalized("race_control", {"message": "RED FLAG"}, 1))
    state = await engine.apply(
        normalized("race_control", {"category": "SessionStatus", "message": "SESSION RESUMED"}, 2)
    )
    assert timing_state(state).track_status == "UNKNOWN"
    assert timing_state(state).control == state.control


@pytest.mark.asyncio
async def test_unknown_control_and_drs_survive_public_snapshot_json_roundtrip():
    snapshots = SnapshotRepository()
    state = await RaceStateEngine(snapshots, snapshot_every_n_events=1).apply(
        normalized("car_data", {"driver_number": 4, "drs": 8})
    )
    assert "drs" not in state.drivers["4"].telemetry
    assert RaceState.model_validate_json(state.model_dump_json()) == state
    restored = RaceState.model_validate(snapshots.snapshots[-1].state)
    assert restored.control.lifecycle.value == "unknown"
    assert restored.drivers["4"].telemetry == {"drs_code": 8}
