# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.intelligence import BattleState, RaceIntelligenceConfig
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.services.intelligence_rebuild import IntelligenceRebuildService
from app.storage.repositories import canonical_replay_sequence_numbers

START = datetime(2026, 7, 19, 13, tzinfo=UTC)


def source(
    event_type: RaceEventType,
    driver: int,
    sequence: int,
    *,
    position: int | None = None,
    interval: float | None = None,
    second: int | None = None,
) -> NormalizedRaceEvent:
    observed = START + timedelta(seconds=sequence if second is None else second)
    payload: dict[str, object] = {
        "driver_number": driver,
        "normalized_session_type": "RACE",
    }
    if position is not None:
        payload["position"] = position
    if interval is not None:
        payload["interval"] = interval
    return NormalizedRaceEvent(
        session_key="11334",
        source="openf1",
        event_time=observed,
        received_at=observed,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[driver],
        interval_seconds=interval,
        payload=payload,
        dedup_key=f"source:{sequence}",
    )


def stream() -> list[NormalizedRaceEvent]:
    return [
        source(RaceEventType.POSITION_SAMPLE, 16, 1, position=4),
        source(RaceEventType.POSITION_SAMPLE, 4, 2, position=5),
        source(RaceEventType.INTERVAL_SAMPLE, 4, 3, interval=1.8),
        source(RaceEventType.INTERVAL_SAMPLE, 4, 4, interval=1.7),
        source(RaceEventType.INTERVAL_SAMPLE, 4, 5, interval=1.6),
        source(RaceEventType.PIT_ENTRY, 4, 6),
    ]


class Events:
    def __init__(self) -> None:
        stale = source(RaceEventType.OVERTAKE, 4, 90).model_copy(
            update={
                "id": uuid4(),
                "event_origin": EventOrigin.DERIVED,
                "dedup_key": "stale-derived",
            }
        )
        self.rows = [*stream(), stale]
        self.replaced: list[NormalizedRaceEvent] | None = None
        self.source_sequences: dict[object, int] | None = None

    async def list_for_session(
        self,
        session_key: str,
        after_sequence: int = 0,
        limit: int = 100,
        **_: object,
    ) -> list[NormalizedRaceEvent]:
        return [
            event
            for event in self.rows
            if event.session_key == session_key and event.sequence_number > after_sequence
        ][:limit]

    async def replace_derived_for_session(
        self,
        session_key: str,
        events: list[NormalizedRaceEvent],
        *,
        source_events: list[NormalizedRaceEvent],
    ) -> list[NormalizedRaceEvent]:
        self.source_sequences, self.replaced = canonical_replay_sequence_numbers(
            source_events,
            events,
        )
        return self.replaced

    async def insert(self, event: NormalizedRaceEvent):
        raise AssertionError("rebuild must use atomic replacement")

    async def max_sequence(self, session_key: str) -> int:
        return max(event.sequence_number for event in self.rows)

    async def latest_session_key(self) -> str | None:
        return "11334"

    async def count(self, session_key: str | None = None) -> int:
        return len(self.rows)


class Battles:
    def __init__(self) -> None:
        self.replaced: list[BattleState] | None = None

    async def replace_for_session(
        self,
        session_key: str,
        battles: list[BattleState],
    ) -> None:
        self.replaced = battles


class Snapshots:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_for_session(self, session_key: str) -> None:
        self.deleted.append(session_key)


def service(events: Events, battles: Battles, snapshots: Snapshots) -> IntelligenceRebuildService:
    return IntelligenceRebuildService(
        events,  # type: ignore[arg-type]
        battles,
        snapshots=snapshots,
        config=RaceIntelligenceConfig(battle_start_samples=3),
    )


@pytest.mark.asyncio
async def test_rebuild_dry_run_is_source_only_deterministic_and_side_effect_free() -> None:
    first_events, first_battles, first_snapshots = Events(), Battles(), Snapshots()
    second_events, second_battles, second_snapshots = Events(), Battles(), Snapshots()

    first = await service(first_events, first_battles, first_snapshots).run(
        "11334", dry_run=True, replace_derived=False
    )
    second = await service(second_events, second_battles, second_snapshots).run(
        "11334", dry_run=True, replace_derived=False
    )

    assert first.source_event_count == 6
    assert first.derived_event_count >= 2
    assert first.derived_by_type == second.derived_by_type
    assert first.derived_dedup_keys == second.derived_dedup_keys
    assert "stale-derived" not in first.derived_dedup_keys
    assert first_events.replaced is None
    assert first_battles.replaced is None
    assert first_snapshots.deleted == []


@pytest.mark.asyncio
async def test_rebuild_replaces_derived_summaries_and_snapshots_safely() -> None:
    events, battles, snapshots = Events(), Battles(), Snapshots()

    summary = await service(events, battles, snapshots).run(
        "11334", dry_run=False, replace_derived=True
    )

    assert summary.replaced_derived is True
    assert events.replaced is not None
    assert events.replaced[0].sequence_number == 6
    assert events.source_sequences is not None
    assert sorted(events.source_sequences.values()) == [1, 2, 3, 4, 5, 7]
    assert all(event.event_origin is EventOrigin.DERIVED for event in events.replaced)
    assert battles.replaced is not None
    assert len(battles.replaced) == 1
    assert snapshots.deleted == ["11334"]


def test_canonical_replay_sequences_interleave_derivations_after_source_facts() -> None:
    first = source(RaceEventType.POSITION_SAMPLE, 16, 20, position=4, second=1)
    second = source(RaceEventType.POSITION_SAMPLE, 4, 30, position=5, second=2)
    first_derived = first.model_copy(
        update={
            "id": uuid4(),
            "source": "apexarena",
            "event_origin": EventOrigin.DERIVED,
            "event_type": RaceEventType.POSITION_CHANGE,
            "sequence_number": 1,
            "dedup_key": "derived:first",
        }
    )
    second_derived = second.model_copy(
        update={
            "id": uuid4(),
            "source": "apexarena",
            "event_origin": EventOrigin.DERIVED,
            "event_type": RaceEventType.BATTLE_STARTED,
            "sequence_number": 2,
            "dedup_key": "derived:second",
        }
    )

    source_sequences, derived = canonical_replay_sequence_numbers(
        [
            first.model_copy(update={"sequence_number": 1}),
            second.model_copy(update={"sequence_number": 2}),
        ],
        [first_derived, second_derived],
    )

    assert source_sequences == {first.id: 1, second.id: 3}
    assert [event.sequence_number for event in derived] == [2, 4]


@pytest.mark.asyncio
async def test_rebuild_refuses_an_implicit_destructive_write() -> None:
    events, battles, snapshots = Events(), Battles(), Snapshots()

    with pytest.raises(ValueError, match="--replace-derived"):
        await service(events, battles, snapshots).run("11334", dry_run=False, replace_derived=False)


@pytest.mark.asyncio
async def test_rebuild_reorders_endpoint_grouped_facts_by_event_time() -> None:
    rows = [
        source(RaceEventType.POSITION_SAMPLE, 16, 1, position=4, second=0),
        source(RaceEventType.POSITION_SAMPLE, 4, 2, position=5, second=0),
        source(RaceEventType.POSITION_SAMPLE, 4, 3, position=4, second=10),
        source(RaceEventType.POSITION_SAMPLE, 16, 4, position=5, second=10),
        source(RaceEventType.INTERVAL_SAMPLE, 4, 5, interval=0.8, second=5),
        source(RaceEventType.POSITION_SAMPLE, 4, 6, position=4, second=12),
        source(RaceEventType.SESSION_FINISH, 4, 7, second=20),
    ]
    events, battles, snapshots = Events(), Battles(), Snapshots()
    events.rows = rows

    summary = await IntelligenceRebuildService(
        events,  # type: ignore[arg-type]
        battles,
        snapshots=snapshots,
        config=RaceIntelligenceConfig(
            overtake_confirmation_seconds=2,
            overtake_confirmation_samples=2,
        ),
    ).run("11334", dry_run=True, replace_derived=False)

    assert summary.overtake_confirmations == 1
    assert summary.derived_by_type[RaceEventType.OVERTAKE.value] == 1
    assert summary.bounded_state_maxima["pending_overtakes"] == 1
    assert summary.remaining_pending_overtakes == 0
    assert summary.remaining_current_battles == 0
