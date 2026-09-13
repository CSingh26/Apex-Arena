# SPDX-License-Identifier: AGPL-3.0-only
"""Critical state is staged privately, acknowledged durably, then exposed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from app.domain.models import NormalizedRaceEvent, RaceEventType, RaceStateSnapshot
from app.services.event_pipeline import NormalizedEventRepository
from app.services.history_ownership import HistoryContextPool
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceState, RaceStateEngine
from app.storage.intelligence_progress import IntelligenceAnchor, SqlIntelligenceProgressRepository


class IntelligenceProjection:
    def __init__(
        self,
        repository: SqlIntelligenceProgressRepository,
        events: NormalizedEventRepository,
        coordinator: RaceIntelligenceCoordinator,
        public_state: RaceStateEngine,
        *,
        state_publisher: Callable[[RaceState], Awaitable[None]] | None = None,
    ) -> None:
        self.repository = repository
        self.events = events
        self.coordinator = coordinator
        self.public_state = public_state
        self.state_publisher = state_publisher
        self.working_state = coordinator.race_state
        self.working_state.algorithm_version = repository.algorithm_version
        if self.working_state is public_state or self.working_state.live_state_reader is not None:
            raise ValueError(
                "Critical projection requires a private state engine without shared readers"
            )
        self._ready: dict[str, IntelligenceAnchor] = {}
        self.context_pool = HistoryContextPool(4, self._evict_private_session)

    async def _evict_private_session(self, session_key: str) -> None:
        self._ready.pop(session_key, None)
        self.coordinator.reset_session(session_key)
        await self.working_state.discard_factual_context(session_key, discard_compact=True)

    async def initialize_session(self, session_key: str) -> None:
        progress = await self.repository.initialize(session_key)
        restored = self._ready.get(session_key) != progress.anchor
        if restored:
            await self.coordinator.restore_session(
                session_key,
                self.events,
                through_sequence=progress.completed_through_sequence,
            )
            await self.public_state.install_state(await self.working_state.get_state(session_key))
            self._ready[session_key] = progress.anchor
        source = await self.repository.pending_source(progress)
        if source is not None:
            await self._project(source, progress.anchor)
        if (restored or source is not None) and self.state_publisher is not None:
            state = await self.working_state.get_state(session_key)
            if state.sequence_number:
                # State-only refresh, never replay of historical events/chat.
                # A failed cache write leaves API views explicitly stale.
                with suppress(Exception):
                    async with asyncio.timeout(1):
                        await self.state_publisher(state)

    async def append(self, event: NormalizedRaceEvent) -> tuple[list[NormalizedRaceEvent], bool]:
        predecessor = self._ready.get(event.session_key)
        if predecessor is None:
            raise RuntimeError("Initialize the private predecessor before source admission")
        source, new = await self.repository.append_source(event, expected_anchor=predecessor)
        if not new:
            return [], False
        derived = await self._project(source, predecessor)
        return [source, *derived], True

    async def _project(
        self, source: NormalizedRaceEvent, predecessor: IntelligenceAnchor
    ) -> list[NormalizedRaceEvent]:
        key = source.session_key
        try:
            state = await self.working_state.apply(source, persist_snapshot=False)
            effects = await self.coordinator.advance_source(source, state)
            for offset, candidate in enumerate(effects.derived, 1):
                await self.working_state.apply(
                    candidate.model_copy(
                        update={
                            "sequence_number": source.sequence_number + offset,
                        }
                    ),
                    persist_snapshot=False,
                )
            state = await self.working_state.get_state(key)
            snapshot = None
            interval = self.public_state.snapshot_every_n_events
            if (
                (source.sequence_number - 1) // interval != state.sequence_number // interval
                or source.event_type is RaceEventType.SESSION_FINISH
            ):
                snapshot = RaceStateSnapshot(
                    session_key=key,
                    meeting_id=source.meeting_id,
                    session_id=source.session_id,
                    snapshot_time=source.event_time,
                    sequence_number=state.sequence_number,
                    current_lap=state.current_lap,
                    session_status=state.status,
                    state=state.model_dump(mode="json"),
                )
            persisted = await self.repository.commit_projection(
                source,
                effects.derived,
                effects.resolved_battles,
                snapshot,
                expected_anchor=predecessor,
                history_reference=state.history_reference,
                history_detail_status=state.history_detail_status,
                detail_checkpoint=await self.working_state.prepared_history_checkpoint(key),
            )
            await self.public_state.install_state(state)
            self._ready[key] = (source.id, source.sequence_number, state.sequence_number)
            return persisted
        except BaseException as exc:
            # A partially mutated private detector is never reused. Reload the
            # acknowledged prefix even when a failed commit response was ambiguous.
            self._ready.pop(key, None)
            if not isinstance(exc, asyncio.CancelledError):
                with suppress(Exception):
                    async with asyncio.timeout(0.25):
                        await self.repository.record_failure(key, source.id, type(exc).__name__)
            raise
