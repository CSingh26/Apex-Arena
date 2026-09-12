# SPDX-License-Identifier: AGPL-3.0-only
"""Restart the entire state/intelligence pair, not only a public state reader."""

import asyncio

import pytest

from app.domain.intelligence import RaceIntelligenceConfig
from app.domain.models import RaceEventType
from app.services.event_pipeline import (
    EventDeduplicator,
    EventOrderingBuffer,
    RaceEventProcessor,
    SequenceNumberService,
)
from app.services.normalization import OpenF1EventNormalizer
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceState, RaceStateEngine
from app.services.raw_events import RawEventInput, RawProviderEventService
from tests.test_event_pipeline import Consumer, NormalizedRepository, RawRepository
from tests.test_race_intelligence import BattleSummaries, Snapshots, consume, source_event


def race_prefix():
    return [
        source_event(RaceEventType.POSITION_SAMPLE, driver=16, position=4, second=0, sequence=1),
        source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=5, second=0, sequence=2),
        *[
            source_event(
                RaceEventType.INTERVAL_SAMPLE, driver=4, interval=gap, second=index, sequence=index
            )
            for index, gap in enumerate((1.8, 1.7, 1.6), 3)
        ],
    ]


def qualifying_prefix():
    return [
        source_event(
            RaceEventType.POSITION_SAMPLE,
            driver=driver,
            position=driver,
            second=driver,
            sequence=driver,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
        )
        for driver in range(1, 23)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["battle", "pending_overtake", "resolved_battle", "qualifying"]
)
async def test_restarted_processor_restores_intelligence_without_republishing_history(scenario):
    config = RaceIntelligenceConfig(
        battle_start_samples=3,
        battle_trend_window=5,
        overtake_confirmation_samples=2,
        overtake_confirmation_seconds=2,
    )
    prefix = qualifying_prefix() if scenario == "qualifying" else race_prefix()
    if scenario in {"pending_overtake", "resolved_battle"}:
        prefix += [
            source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=4, second=6, sequence=6),
            source_event(
                RaceEventType.POSITION_SAMPLE, driver=16, position=5, second=6, sequence=7
            ),
            source_event(
                RaceEventType.INTERVAL_SAMPLE, driver=4, interval=0.7, second=8, sequence=8
            ),
        ]
        if scenario == "resolved_battle":
            prefix.append(
                source_event(
                    RaceEventType.POSITION_SAMPLE, driver=4, position=4, second=9, sequence=9
                )
            )
    repository = NormalizedRepository()
    uninterrupted = RaceStateEngine(Snapshots(), snapshot_every_n_events=100)
    original = RaceIntelligenceCoordinator(uninterrupted, config=config)
    cursor = 0
    # Persist the real interleaved source/derived sequence produced by the coordinator.
    for source in prefix:
        cursor += 1
        source = source.model_copy(update={"sequence_number": cursor})
        await repository.insert(source)
        for derived in await consume(uninterrupted, original, source):
            cursor += 1
            derived = derived.model_copy(update={"sequence_number": cursor})
            await repository.insert(derived)
            await uninterrupted.apply(derived)
    before = await uninterrupted.get_state("race")
    if scenario == "battle":
        assert len(before.current_battles) == 1
    if scenario == "pending_overtake":
        assert original.diagnostics_for_session("race").pending_overtakes == 1
    if scenario == "qualifying":
        assert before.qualifying_intelligence.field_size == 22
        assert before.qualifying_intelligence.cutoff_position == 16

    class NoSnapshotWrites(Snapshots):
        async def insert(self, snapshot):
            raise AssertionError("restart reconstruction must not write snapshots")

    restored = RaceStateEngine(NoSnapshotWrites(), snapshot_every_n_events=1)
    summaries = BattleSummaries()
    restarted = RaceIntelligenceCoordinator(restored, config=config, battle_summaries=summaries)
    published = Consumer()
    processor = RaceEventProcessor(
        raw_events=RawProviderEventService(RawRepository()),
        normalizer=OpenF1EventNormalizer(),
        normalized_repository=repository,
        deduplicator=EventDeduplicator(),
        ordering_buffer=EventOrderingBuffer(0),
        sequence_numbers=SequenceNumberService(repository),
        consumers=[restored, restarted, published],
    )
    await processor.flush_session("race")
    recovered = await restored.get_state("race")
    assert recovered.model_dump() == before.model_dump()
    assert restarted.diagnostics_for_session("race") == original.diagnostics_for_session("race")
    assert published.events == []
    assert summaries.saved == []
    assert restarted.drain_derived("race") == []
    assert await repository.count("race") == cursor

    if scenario in {"battle", "resolved_battle"}:
        next_source = source_event(
            RaceEventType.INTERVAL_SAMPLE, driver=4, interval=1.5, second=12, sequence=cursor + 1
        )
    elif scenario == "pending_overtake":
        next_source = source_event(
            RaceEventType.POSITION_SAMPLE, driver=4, position=4, second=9, sequence=cursor + 1
        )
    else:
        next_source = source_event(
            RaceEventType.POSITION_SAMPLE,
            driver=17,
            position=15,
            second=30,
            sequence=cursor + 1,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
        )
    restored.snapshots = Snapshots()
    continued = await consume(uninterrupted, original, next_source)
    after_restart = await consume(restored, restarted, next_source)
    assert [event.model_dump() for event in after_restart] == [
        event.model_dump() for event in continued
    ]
    assert (await restored.get_state("race")).model_dump() == (
        await uninterrupted.get_state("race")
    ).model_dump()
    expected = {
        "pending_overtake": RaceEventType.OVERTAKE,
        "qualifying": RaceEventType.QUALIFYING_CUTOFF_CHANGE,
    }
    if scenario in expected:
        assert expected[scenario] in {event.event_type for event in after_restart}
    else:
        assert not any(event.event_type is RaceEventType.BATTLE_STARTED for event in after_restart)

    if scenario == "qualifying":
        phase = source_event(
            RaceEventType.QUALIFYING_PHASE,
            driver=17,
            second=31,
            sequence=cursor + 2,
            normalized_session_type="QUALIFYING",
            session_phase="Q2",
        )
        expected_phase = await consume(uninterrupted, original, phase)
        restored_phase = await consume(restored, restarted, phase)
        assert [event.model_dump() for event in restored_phase] == [
            event.model_dump() for event in expected_phase
        ]
        assert any(
            event.event_type is RaceEventType.SESSION_PHASE_CHANGE for event in restored_phase
        )
        assert (await restored.get_state("race")).qualifying_intelligence.cutoff_position == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_partial_prefix_reconstruction_retries_without_publishing_or_replacing_state(failure):
    class InterruptedRepository(NormalizedRepository):
        failed = False

        async def list_for_session(self, session_key, after_sequence=0, limit=100):
            if after_sequence >= 1000 and not self.failed:
                self.failed = True
                raise failure("interrupted prefix read")
            return await super().list_for_session(session_key, after_sequence, limit)

    repository = InterruptedRepository()
    for sequence in range(1, 1002):
        await repository.insert(
            source_event(
                RaceEventType.WEATHER_UPDATE,
                driver=4,
                second=sequence,
                sequence=sequence,
                rainfall=0,
            )
        )
    state = RaceStateEngine(Snapshots(), snapshot_every_n_events=100)
    await state.install_state(RaceState(session_key="race", current_lap=77))
    coordinator = RaceIntelligenceCoordinator(state)
    published = Consumer()
    pipeline = RaceEventProcessor(
        raw_events=RawProviderEventService(RawRepository()),
        normalizer=OpenF1EventNormalizer(),
        normalized_repository=repository,
        deduplicator=EventDeduplicator(),
        ordering_buffer=EventOrderingBuffer(0),
        sequence_numbers=SequenceNumberService(repository),
        consumers=[state, coordinator, published],
    )
    with pytest.raises(failure, match="interrupted prefix"):
        await pipeline.initialize_session("race")
    assert (await state.get_state("race")).current_lap == 77
    assert published.events == []
    assert coordinator.drain_derived("race") == []
    assert await repository.count("race") == 1001

    # Intake must retry full initialization before assigning the next sequence.
    await pipeline.ingest(
        RawEventInput(
            provider_endpoint="position",
            session_key="race",
            raw_payload={"driver_number": 4, "position": 1},
        )
    )
    current = await state.get_state("race")
    assert current.drivers["4"].position == 1
    assert current.weather["rainfall"] == 0
    assert current.current_lap is None
    assert current.sequence_number == 1002
    assert [event.sequence_number for event in published.events] == [1002]


@pytest.mark.asyncio
async def test_reconstruction_rejects_a_replaced_prefix_instead_of_polling_forever():
    class ReplacedPrefix(NormalizedRepository):
        reads = 0

        async def max_sequence(self, session_key):
            return 2

        async def list_for_session(self, session_key, after_sequence=0, limit=100):
            self.reads += 1
            if self.reads > 1:
                raise RuntimeError("polled replaced prefix again")
            return [
                source_event(
                    RaceEventType.POSITION_SAMPLE, driver=4, position=1, second=3, sequence=3
                )
            ]

    state = RaceStateEngine(Snapshots())
    coordinator = RaceIntelligenceCoordinator(state)
    with pytest.raises(RuntimeError, match="prefix is incomplete"):
        await coordinator.restore_session("race", ReplacedPrefix())
    assert (await state.get_state("race")).drivers == {}
