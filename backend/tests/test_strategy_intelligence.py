# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta

import pytest

from app.domain.models import RaceEventType as E
from app.domain.strategy_situations import encoded
from app.services.race_state import RaceStateEngine
from app.services.strategy_intelligence import StrategyIntelligence
from tests.test_history_integration import START
from tests.test_race_history import fact
from tests.test_race_state import SnapshotRepository


class Run:
    def __init__(self):
        self.engine = RaceStateEngine(SnapshotRepository())
        self.detector = StrategyIntelligence(semantic_identity="synthetic:strategy")
        self.sequence = 0
        self.rows = []

    async def add(self, kind, *, seconds=None, driver=4, lap=None, **payload):
        self.sequence += 1
        source = fact(self.sequence, kind, driver=driver, lap=lap, **payload)
        source.event_time = START + timedelta(
            seconds=seconds if seconds is not None else self.sequence
        )
        self.rows.append(source)
        state = await self.engine.apply(source, persist_snapshot=False)
        self.last = await self.engine.evaluate_owned_facts(
            source, state, lambda ctx: self.detector.advance(source, state, ctx)
        )
        return self.last

    async def setup(self):
        await self.add(
            E.SESSION_START,
            seconds=0,
            normalized_session_type="RACE",
            control={"lifecycle": "running", "neutralization": "green"},
        )
        for driver, position, compound in [(4, 1, "MEDIUM"), (81, 2, "HARD")]:
            await self.add(
                E.DRIVER_UPDATE, driver=driver, team_name="Synthetic", full_name=f"Driver {driver}"
            )
            await self.add(E.POSITION_SAMPLE, driver=driver, position=position)
            await self.add(
                E.STINT_UPDATE,
                driver=driver,
                stint_number=1,
                compound=compound,
                lap_start=1,
                tyre_age_at_start=0,
            )


def by_kind(delta, kind):
    return next((s for s in delta.frame.situations if s.kind.value == kind), None)


@pytest.mark.asyncio
async def test_source_divergence_keeps_unknown_age_and_repeated_ticks_do_not_emit():
    run = Run()
    await run.setup()
    item = by_kind(run.last, "stint_divergence")
    assert item is not None and item.payload.age_offset_laps is None
    assert item.payload.same_reported_team is True and item.payload.plan == "unknown"
    identity = item.situation_id
    for index in range(20):
        value = await run.add(E.CAR_DATA_SAMPLE, speed=200, seconds=10 + index)
        assert not value.transitions
        assert by_kind(value, "stint_divergence").situation_id == identity
    assert (
        value.frame.capabilities["extra_stop_consequence"].reason
        == "missing_authoritative_remaining_distance"
    )


@pytest.mark.asyncio
async def test_source_clean_pace_has_signed_range_and_deletion_withdraws_immediately():
    run = Run()
    await run.setup()
    for lap in range(1, 4):
        for driver, duration in [(4, 90 + (lap - 1) * 0.2), (81, 91 + (lap - 1) * 0.2)]:
            await run.add(
                E.LAP_COMPLETED,
                driver=driver,
                lap=lap,
                seconds=lap * 100 + driver / 100,
                lap_duration=duration,
                date_start=(START + timedelta(seconds=lap * 100 - 95)).isoformat(),
            )
    item = by_kind(run.last, "relative_pace")
    assert item.payload.pace.difference_seconds == pytest.approx(-1)
    assert item.payload.pace.range_seconds == pytest.approx((-1.4, -0.6))
    value = await run.add(E.LAP_DELETED, driver=81, lap=3, seconds=302)
    assert (
        not by_kind(value, "relative_pace") or by_kind(value, "relative_pace").status == "withdrawn"
    )
    assert any(
        t.situation.transition == "withdrawn" and t.situation.kind == "relative_pace"
        for t in value.transitions
    )


@pytest.mark.asyncio
async def test_neutralized_source_context_is_qualitative_and_red_ends_it():
    run = Run()
    await run.setup()
    value = await run.add(E.RACE_CONTROL, control={"neutralization": "safety_car_ending"})
    item = by_kind(value, "neutralized_pit_context")
    assert item.payload.neutralization == "safety_car_ending"
    assert item.payload.numeric_saving is None
    value = await run.add(
        E.RACE_CONTROL, control={"neutralization": "red", "lifecycle": "suspended"}
    )
    assert by_kind(value, "neutralized_pit_context") is None


@pytest.mark.asyncio
async def test_weather_source_transition_and_staleness_do_not_infer_wetness():
    run = Run()
    await run.add(E.WEATHER_UPDATE, seconds=0, rainfall=0, track_temperature=34)
    value = await run.add(E.WEATHER_UPDATE, seconds=60, rainfall=1, track_temperature=31)
    item = by_kind(value, "weather_change")
    assert item.payload.track_temperature_change == -3
    assert item.payload.rainfall_now is True
    assert len(item.evidence_keys) == 2
    assert len(encoded(value.frame)) <= 65536
    value = await run.add(E.LOCATION_SAMPLE, seconds=361, x=1)
    assert by_kind(value, "weather_change") is None
    assert not value.transitions


async def supported_loss(run, driver=81):
    for lap, duration in [(1, 89.5), (2, 90), (3, 90.5), (4, 100), (5, 100)]:
        if lap == 4:
            await run.add(E.PIT_STOP, driver=driver, lap=4, seconds=395, lane_duration=25)
        await run.add(
            E.LAP_COMPLETED,
            driver=driver,
            lap=lap,
            seconds=lap * 100,
            lap_duration=duration,
            is_pit_out_lap=lap == 5,
            date_start=(START + timedelta(seconds=lap * 100 - duration)).isoformat(),
        )
    await run.add(
        E.STINT_UPDATE,
        driver=driver,
        seconds=501,
        stint_number=2,
        compound="HARD",
        lap_start=5,
        tyre_age_at_start=0,
    )


@pytest.mark.asyncio
async def test_source_pit_window_and_undercut_are_conditional_partial_not_rank():
    run = Run()
    await run.setup()
    await supported_loss(run)
    for lap in (6, 7, 8):
        for driver, duration in [(4, 90.8), (81, 91.2)]:
            await run.add(
                E.LAP_COMPLETED,
                driver=driver,
                lap=lap,
                seconds=lap * 100,
                lap_duration=duration,
                date_start=(START + timedelta(seconds=lap * 100 - duration)).isoformat(),
            )
    for driver, position, gap in [(4, 1, 0), (81, 2, 1.2)]:
        await run.add(E.POSITION_SAMPLE, driver=driver, seconds=801, position=position)
        await run.add(
            E.INTERVAL_SAMPLE, driver=driver, lap=9, seconds=801, gap_to_leader=gap, interval=gap
        )
    item = by_kind(run.last, "pit_window")
    assert item.payload.pit_window.loss_seconds == 20
    assert item.payload.pit_window.projected_gap_seconds == pytest.approx((19.2, 23.2))
    assert item.payload.pit_window.rank_range is None
    undercut = by_kind(run.last, "undercut_condition")
    assert undercut.payload.required_gain_seconds == 1.2
    assert undercut.payload.new_tyre_pace == "unknown"
    value = await run.add(E.CAR_DATA_SAMPLE, seconds=832, speed=200)
    assert by_kind(value, "pit_window") is None
    assert by_kind(value, "undercut_condition") is None


@pytest.mark.asyncio
async def test_actual_pit_anchor_preserves_rival_pre_stop_pace_for_stay_out_update():
    run = Run()
    await run.setup()
    for lap in (1, 2, 3):
        for driver, duration in [(4, 90.1), (81, 91.1)]:
            await run.add(
                E.LAP_COMPLETED,
                driver=driver,
                lap=lap,
                seconds=lap * 100,
                lap_duration=duration,
                date_start=(START + timedelta(seconds=lap * 100 - duration)).isoformat(),
            )
    for driver, position, gap in [(4, 1, 0), (81, 2, 1.2)]:
        await run.add(E.POSITION_SAMPLE, driver=driver, seconds=301, position=position)
        await run.add(
            E.INTERVAL_SAMPLE, driver=driver, lap=4, seconds=301, gap_to_leader=gap, interval=gap
        )
    await run.add(E.PIT_STOP, driver=81, lap=4, seconds=302, lane_duration=25)
    pit = run.rows[-1].id
    await run.add(
        E.STINT_UPDATE,
        driver=81,
        seconds=303,
        stint_number=2,
        compound="MEDIUM",
        lap_start=4,
        tyre_age_at_start=0,
    )
    value = await run.add(
        E.LAP_COMPLETED,
        driver=4,
        lap=4,
        seconds=400,
        lap_duration=90,
        date_start=(START + timedelta(seconds=310)).isoformat(),
    )
    item = by_kind(value, "overcut_condition")
    assert item.payload.pit_anchor == pit
    assert item.payload.clean_laps_since_pit == 1
    assert item.payload.pace.second.median_seconds == 91.1
    assert item.payload.outcome == "unknown"
    value = await run.add(E.PIT_STOP, driver=4, lap=5, seconds=410, lane_duration=25)
    assert by_kind(value, "overcut_condition") is None


def test_prominence_literal_components_and_neutralization():
    from app.services.strategy_battles import prominence

    score = prominence(
        gap=0.8,
        duration=40,
        closing=True,
        position=1,
        train_size=3,
        remaining=None,
        same_team=True,
        strategy=True,
        neutralization="green",
        running=True,
    )
    assert score.score == 80
    assert score.remaining_distance == 0
    score = prominence(
        gap=0.8,
        duration=40,
        closing=True,
        position=1,
        train_size=3,
        remaining=None,
        same_team=None,
        strategy=True,
        neutralization="green",
        running=True,
    )
    assert score.score == 75
    score = prominence(
        gap=0.8,
        duration=40,
        closing=True,
        position=1,
        train_size=3,
        remaining=None,
        same_team=True,
        strategy=True,
        neutralization="safety_car",
        running=True,
    )
    assert score.score == 0 and score.basis == "not_racing"


@pytest.mark.asyncio
async def test_reconstruction_at_source_cursor_matches_ids_and_frames():
    first = Run()
    await first.setup()
    await first.add(E.WEATHER_UPDATE, seconds=60, rainfall=0)
    await first.add(E.WEATHER_UPDATE, seconds=120, rainfall=1)
    replay = Run()
    for source in first.rows:
        state = await replay.engine.apply(source, persist_snapshot=False)
        delta = await replay.engine.evaluate_owned_facts(
            source,
            state,
            lambda ctx, source=source, state=state: replay.detector.advance(source, state, ctx),
        )
    assert delta.model_dump() == first.last.model_dump()


@pytest.mark.asyncio
async def test_coordinator_installs_on_explicit_owner_and_real_trigger_accepts_strategy():
    from app.services.agent_grounding import AgentEligibility
    from app.services.discussion_triggers import DiscussionTriggerEvaluator
    from app.services.race_intelligence import RaceIntelligenceCoordinator

    run = Run()
    await run.setup()
    destination = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(destination)
    scratch = RaceStateEngine(SnapshotRepository())
    emitted = []
    for row in run.rows:
        state = await scratch.apply(row, persist_snapshot=False)
        effects = await coordinator.advance_applied_source(row, state, factual_owner=scratch)
        emitted.extend(effects.derived)
    state = await scratch.get_state("history-test")
    assert by_kind(type("Delta", (), {"frame": state.strategy_frame}), "stint_divergence")
    assert (await destination.get_state("history-test")).strategy_frame is None
    event = next(e for e in emitted if e.event_type.value == "STRATEGY_SITUATION")
    assert AgentEligibility().evaluate(event)
    assert DiscussionTriggerEvaluator().evaluate(event) is not None
    weak = event.model_copy(update={"payload": {"schema_version": "strategy-v1"}})
    assert not AgentEligibility().evaluate(weak)


@pytest.mark.asyncio
async def test_room_replay_has_independent_effects_off_source_projection_and_gate():
    from app.services.room_replay import RoomReplayCoordinator

    run = Run()
    await run.setup()
    replay = RoomReplayCoordinator(None, None, None, RaceStateEngine(SnapshotRepository()), None)
    for row in run.rows:
        await replay._apply_recorded(row.model_copy(update={"is_replay": True}))
    state = await replay.race_state.get_state("history-test")
    assert state.strategy_frame.situations
    assert replay.intelligence.drain_derived("history-test") == []

    async def denied(session):
        return False

    blocked = RoomReplayCoordinator(
        None, None, None, RaceStateEngine(SnapshotRepository()), None, reasoning_allowed=denied
    )
    for row in run.rows:
        await blocked._apply_recorded(row)
    assert (await blocked.race_state.get_state("history-test")).strategy_frame is None
