# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import time
from collections import Counter
from typing import Protocol

from pydantic import BaseModel, Field

from app.domain.intelligence import BattleState, RaceIntelligenceConfig
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceStateSnapshot
from app.services.event_pipeline import NormalizedEventRepository
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceStateEngine, SnapshotPersistResult


class ReplaceableEventRepository(NormalizedEventRepository, Protocol):
    async def replace_derived_for_session(
        self,
        session_key: str,
        events: list[NormalizedRaceEvent],
    ) -> list[NormalizedRaceEvent]: ...


class ReplaceableBattleRepository(Protocol):
    async def replace_for_session(
        self,
        session_key: str,
        battles: list[BattleState],
    ) -> None: ...


class ResettableSnapshotRepository(Protocol):
    async def delete_for_session(self, session_key: str) -> None: ...


class IntelligenceRebuildSummary(BaseModel):
    session_key: str
    dry_run: bool
    replaced_derived: bool
    source_event_count: int
    derived_event_count: int
    derived_by_type: dict[str, int] = Field(default_factory=dict)
    resolved_battle_count: int = 0
    peak_current_battles: int = 0
    peak_pending_overtakes: int = 0
    elapsed_seconds: float = 0
    events_per_second: float = 0
    derived_dedup_keys: list[str] = Field(default_factory=list)


class _MemorySnapshots:
    async def insert(self, snapshot: RaceStateSnapshot) -> SnapshotPersistResult:
        return SnapshotPersistResult(record_id=snapshot.id, is_new=True)

    async def latest(self, session_key: str) -> None:
        return None


class _BattleCollector:
    def __init__(self) -> None:
        self.resolved: dict[str, BattleState] = {}

    async def upsert_resolved(self, battle: BattleState) -> None:
        self.resolved[battle.id] = battle.model_copy(deep=True)


class IntelligenceRebuildService:
    """Reconstruct Sprint 3 intelligence from immutable source facts only."""

    def __init__(
        self,
        events: ReplaceableEventRepository,
        battles: ReplaceableBattleRepository,
        *,
        config: RaceIntelligenceConfig | None = None,
        snapshots: ResettableSnapshotRepository | None = None,
    ) -> None:
        self.events = events
        self.battles = battles
        self.config = config or RaceIntelligenceConfig()
        self.snapshots = snapshots

    async def run(
        self,
        session_key: str,
        *,
        dry_run: bool,
        replace_derived: bool,
    ) -> IntelligenceRebuildSummary:
        if not dry_run and not replace_derived:
            raise ValueError("Writing a rebuild requires --replace-derived")

        source_events = await self._source_events(session_key)
        race_state = RaceStateEngine(_MemorySnapshots(), snapshot_every_n_events=1_000_000)
        collector = _BattleCollector()
        coordinator = RaceIntelligenceCoordinator(
            race_state,
            config=self.config,
            battle_summaries=collector,
        )
        derived: list[NormalizedRaceEvent] = []
        peak_battles = 0
        peak_pending = 0
        started = time.perf_counter()
        for source in source_events:
            await race_state.consume(source)
            await coordinator.consume(source)
            derived.extend(coordinator.drain_derived(session_key))
            peak_battles = max(
                peak_battles,
                len(coordinator.battles.current_for_session(session_key)),
            )
            peak_pending = max(
                peak_pending,
                len(coordinator.overtakes.pending_for_session(session_key)),
            )
        elapsed = time.perf_counter() - started

        persisted = derived
        if not dry_run:
            persisted = await self.events.replace_derived_for_session(session_key, derived)
            await self.battles.replace_for_session(
                session_key,
                list(collector.resolved.values()),
            )
            if self.snapshots is not None:
                await self.snapshots.delete_for_session(session_key)

        by_type = Counter(event.event_type.value for event in persisted)
        return IntelligenceRebuildSummary(
            session_key=session_key,
            dry_run=dry_run,
            replaced_derived=not dry_run and replace_derived,
            source_event_count=len(source_events),
            derived_event_count=len(persisted),
            derived_by_type=dict(sorted(by_type.items())),
            resolved_battle_count=len(collector.resolved),
            peak_current_battles=peak_battles,
            peak_pending_overtakes=peak_pending,
            elapsed_seconds=round(elapsed, 6),
            events_per_second=round(len(source_events) / elapsed, 2) if elapsed else 0,
            derived_dedup_keys=[event.dedup_key for event in persisted],
        )

    async def _source_events(self, session_key: str) -> list[NormalizedRaceEvent]:
        source: list[NormalizedRaceEvent] = []
        after_sequence = 0
        while True:
            page = await self.events.list_for_session(
                session_key,
                after_sequence=after_sequence,
                limit=1000,
                event_origin=EventOrigin.SOURCE_FACT,
            )
            page = [event for event in page if event.event_origin is EventOrigin.SOURCE_FACT]
            if not page:
                break
            source.extend(page)
            after_sequence = max(event.sequence_number for event in page)
            if len(page) < 1000:
                break
        return sorted(
            source,
            key=lambda event: (event.sequence_number, event.event_time, str(event.id)),
        )
