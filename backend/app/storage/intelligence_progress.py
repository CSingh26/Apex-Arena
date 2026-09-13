# SPDX-License-Identifier: AGPL-3.0-only
"""Append-only critical projection commits. Notifications are not an outbox."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.history import HistoryReference, PreparedHistoryCheckpoint
from app.domain.intelligence import BattleState
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceStateSnapshot
from app.storage.database import INGESTOR_HANDOFF_LOCK_ID, Database
from app.storage.history_checkpoints import (
    HistoryCheckpointConflictError,
    insert_history_checkpoint,
)
from app.storage.intelligence_repository import SqlBattleSummaryRepository
from app.storage.models import (
    BattleSummaryRecord,
    NormalizedRaceEventRecord,
    SessionHistoryCheckpointRecord,
    SessionIntelligenceProgressRecord,
)
from app.storage.repositories import SqlNormalizedEventRepository, SqlRaceStateSnapshotRepository

INGESTOR_LOCK_ID = 1_095_782_232
type IntelligenceAnchor = tuple[UUID | None, int, int]


class IntelligenceWriterConflictError(RuntimeError):
    """Another writer or changed durable prefix requires a safe retry."""


class IntelligenceProgress(BaseModel):
    session_key: str
    algorithm_version: str
    completed_source_id: UUID | None = None
    completed_source_sequence: int = 0
    completed_through_sequence: int = 0
    pending_source_id: UUID | None = None
    pending_source_sequence: int | None = None
    historical_effects_unverified: bool = False
    failure_code: str | None = None
    history_reference: HistoryReference | None = None
    history_detail_status: str = "legacy_history_unverified"

    @property
    def anchor(self) -> IntelligenceAnchor:
        return (
            self.completed_source_id,
            self.completed_source_sequence,
            self.completed_through_sequence,
        )

    @property
    def status(self) -> str:
        if self.pending_source_id is not None:
            return "pending"
        return "historical_effects_unverified" if self.historical_effects_unverified else "current"


class SqlIntelligenceProgressRepository:
    def __init__(self, database: Database, *, algorithm_version: str) -> None:
        self.database = database
        self.algorithm_version = algorithm_version

    @asynccontextmanager
    async def _write(self, session_key: str):
        async with self.database.session_factory() as session:
            async with session.begin():
                # A takeover must drain every admitted transaction before it
                # becomes active. This shared barrier survives lease-socket loss
                # and spans the actual commit, unlike another Python-side check.
                if not await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
                    {"key": INGESTOR_HANDOFF_LOCK_ID},
                ):
                    raise IntelligenceWriterConflictError("Ingestor handoff is busy; retry later")
                # Maintenance writers must not overlap the live singleton.
                # The owner's connection is checked, not merely its Python flag.
                if self.database.ingestor_lease_owned:
                    await self.database.verify_ingestor_lease()
                elif not await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": INGESTOR_LOCK_ID}
                ):
                    raise IntelligenceWriterConflictError(
                        "Another ingestor owns writes; retry later"
                    )
                key = int.from_bytes(
                    hashlib.sha256(f"intelligence:{session_key}".encode()).digest()[:8],
                    "big",
                    signed=True,
                )
                if not await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
                ):
                    raise IntelligenceWriterConflictError("Session projection is busy; retry later")
                yield session

    async def _record(self, session, session_key: str):
        record = await session.get(
            SessionIntelligenceProgressRecord, session_key, with_for_update=True
        )
        if record is None:
            maximum = int(
                await session.scalar(
                    select(func.max(NormalizedRaceEventRecord.sequence_number)).where(
                        NormalizedRaceEventRecord.session_key == session_key
                    )
                )
                or 0
            )
            source = await session.scalar(
                select(NormalizedRaceEventRecord)
                .where(
                    NormalizedRaceEventRecord.session_key == session_key,
                    NormalizedRaceEventRecord.event_origin == EventOrigin.SOURCE_FACT.value,
                )
                .order_by(NormalizedRaceEventRecord.sequence_number.desc())
                .limit(1)
            )
            record = SessionIntelligenceProgressRecord(
                session_key=session_key,
                algorithm_version=self.algorithm_version,
                completed_through_sequence=maximum,
                completed_source_id=source.id if source else None,
                completed_source_sequence=source.sequence_number if source else 0,
                historical_effects_unverified=maximum > 0,
            )
            session.add(record)
            await session.flush()
        if record.algorithm_version != self.algorithm_version:
            raise IntelligenceWriterConflictError(
                "Intelligence algorithm changed; explicit baseline review required"
            )
        if record.completed_source_id is not None:
            anchor = await session.get(NormalizedRaceEventRecord, record.completed_source_id)
            if (
                anchor is None
                or anchor.session_key != session_key
                or anchor.sequence_number != record.completed_source_sequence
            ):
                raise IntelligenceWriterConflictError("Completed intelligence prefix changed")
        return record

    async def initialize(self, session_key: str) -> IntelligenceProgress:
        async with self._write(session_key) as session:
            return IntelligenceProgress.model_validate(
                await self._record(session, session_key), from_attributes=True
            )

    async def check_writer_access(self, session_key: str) -> None:
        """Cheap preflight before maintenance provider work; writes recheck in-transaction."""
        async with self._write(session_key):
            pass

    async def load(self, session_key: str) -> IntelligenceProgress | None:
        async with self.database.session_factory() as session:
            record = await session.get(SessionIntelligenceProgressRecord, session_key)
            return (
                IntelligenceProgress.model_validate(record, from_attributes=True)
                if record
                else None
            )

    async def pending_sessions(self, *, after_session: str = "", limit: int = 50) -> list[str]:
        async with self.database.session_factory() as session:
            return list(
                await session.scalars(
                    select(SessionIntelligenceProgressRecord.session_key)
                    .where(
                        SessionIntelligenceProgressRecord.pending_source_id.is_not(None),
                        SessionIntelligenceProgressRecord.session_key > after_session,
                    )
                    .order_by(SessionIntelligenceProgressRecord.session_key)
                    .limit(min(100, max(1, limit)))
                )
            )

    async def pending_source(self, progress: IntelligenceProgress) -> NormalizedRaceEvent | None:
        if progress.pending_source_id is None:
            return None
        async with self.database.session_factory() as session:
            row = await session.get(NormalizedRaceEventRecord, progress.pending_source_id)
            if (
                row is None
                or row.session_key != progress.session_key
                or row.sequence_number != progress.pending_source_sequence
            ):
                raise IntelligenceWriterConflictError("Pending intelligence source is inconsistent")
            return NormalizedRaceEvent.model_validate(row, from_attributes=True)

    async def append_source(
        self, event: NormalizedRaceEvent, *, expected_anchor: IntelligenceAnchor
    ) -> tuple[NormalizedRaceEvent, bool]:
        if event.event_origin is not EventOrigin.SOURCE_FACT:
            raise ValueError("Critical intake accepts source facts only")
        async with self._write(event.session_key) as session:
            progress = await self._record(session, event.session_key)
            self._require_anchor(progress, expected_anchor)
            if progress.pending_source_id is not None:
                raise IntelligenceWriterConflictError(
                    "Recover pending intelligence before admitting later facts"
                )
            existing = await session.scalar(
                select(NormalizedRaceEventRecord).where(
                    NormalizedRaceEventRecord.dedup_key == event.dedup_key
                )
            )
            if existing is not None:
                return NormalizedRaceEvent.model_validate(existing, from_attributes=True), False
            maximum = int(
                await session.scalar(
                    select(func.max(NormalizedRaceEventRecord.sequence_number)).where(
                        NormalizedRaceEventRecord.session_key == event.session_key
                    )
                )
                or 0
            )
            if maximum != progress.completed_through_sequence:
                raise IntelligenceWriterConflictError(
                    "Uncoordinated source writer changed the durable prefix"
                )
            stored = event.model_copy(update={"sequence_number": maximum + 1})
            await session.execute(
                insert(NormalizedRaceEventRecord).values(
                    **SqlNormalizedEventRepository._event_values(stored)
                )
            )
            progress.pending_source_id = stored.id
            progress.pending_source_sequence = stored.sequence_number
            return stored, True

    async def commit_projection(
        self,
        source: NormalizedRaceEvent,
        derived: list[NormalizedRaceEvent],
        summaries: list[BattleState],
        snapshot: RaceStateSnapshot | None = None,
        *,
        expected_anchor: IntelligenceAnchor,
        history_reference: HistoryReference | None = None,
        detail_checkpoint: PreparedHistoryCheckpoint | None = None,
        history_detail_status: str = "legacy_history_unverified",
    ) -> list[NormalizedRaceEvent]:
        async with self._write(source.session_key) as session:
            progress = await self._record(session, source.session_key)
            self._require_anchor(progress, expected_anchor)
            if (
                progress.pending_source_id != source.id
                or progress.pending_source_sequence != source.sequence_number
            ):
                raise IntelligenceWriterConflictError(
                    "Projection progress changed; reload before retry"
                )
            maximum = int(
                await session.scalar(
                    select(func.max(NormalizedRaceEventRecord.sequence_number)).where(
                        NormalizedRaceEventRecord.session_key == source.session_key
                    )
                )
                or 0
            )
            if maximum != source.sequence_number:
                raise IntelligenceWriterConflictError("Durable pending prefix changed")
            persisted = []
            for offset, candidate in enumerate(derived, 1):
                if (
                    candidate.session_key != source.session_key
                    or candidate.event_origin is not EventOrigin.DERIVED
                ):
                    raise ValueError("Projection output must be derived in the same session")
                sequenced = candidate.model_copy(
                    update={"sequence_number": source.sequence_number + offset}
                )
                await session.execute(
                    insert(NormalizedRaceEventRecord).values(
                        **SqlNormalizedEventRepository._event_values(sequenced)
                    )
                )
                persisted.append(sequenced)
            for summary in summaries:
                if summary.session_key != source.session_key:
                    raise ValueError("Summary belongs to another session")
                values = SqlBattleSummaryRepository._values(summary)
                await session.execute(
                    insert(BattleSummaryRecord)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[BattleSummaryRecord.battle_key],
                        set_={key: value for key, value in values.items() if key != "battle_key"},
                    )
                )
            through = source.sequence_number + len(persisted)
            encoded_reference = (
                history_reference.model_dump(mode="json") if history_reference else None
            )
            if encoded_reference != progress.history_reference:
                await self._persist_detail(session, source, history_reference, detail_checkpoint)
            if snapshot is not None:
                if (
                    snapshot.session_key != source.session_key
                    or snapshot.sequence_number != through
                ):
                    raise ValueError("Snapshot must match the completed projection")
                await SqlRaceStateSnapshotRepository.insert_in_transaction(session, snapshot)
            progress.completed_source_id = source.id
            progress.completed_source_sequence = source.sequence_number
            progress.completed_through_sequence = through
            progress.pending_source_id = None
            progress.pending_source_sequence = None
            progress.failure_code = None
            progress.history_reference = encoded_reference
            progress.history_detail_status = history_detail_status
            await session.flush()
            return persisted

    async def _persist_detail(self, session, source, reference, candidate) -> None:
        if reference is None:
            return
        if (
            reference.algorithm_version != self.algorithm_version
            or reference.relevant_sequence > source.sequence_number
        ):
            raise HistoryCheckpointConflictError("History reference does not bind projection")
        relevant = await session.scalar(
            select(NormalizedRaceEventRecord.id).where(
                NormalizedRaceEventRecord.session_key == source.session_key,
                NormalizedRaceEventRecord.sequence_number == reference.relevant_sequence,
                NormalizedRaceEventRecord.id == reference.relevant_event_id,
                NormalizedRaceEventRecord.event_origin == EventOrigin.SOURCE_FACT.value,
            )
        )
        if relevant is None:
            raise HistoryCheckpointConflictError("History reference source identity mismatch")
        if candidate is not None and candidate.source_sequence == source.sequence_number:
            if (
                candidate.session_key != source.session_key
                or candidate.source_id != source.id
                or candidate.algorithm_version != self.algorithm_version
            ):
                raise HistoryCheckpointConflictError("History checkpoint does not bind source")
            await insert_history_checkpoint(session, candidate)
        # Metadata only: an unchanged 8MiB blob must never be loaded per tick.
        row = (
            await session.execute(
                select(
                    SessionHistoryCheckpointRecord.source_id,
                    SessionHistoryCheckpointRecord.checksum,
                ).where(
                    SessionHistoryCheckpointRecord.session_key == source.session_key,
                    SessionHistoryCheckpointRecord.algorithm_version == reference.algorithm_version,
                    SessionHistoryCheckpointRecord.schema_version == reference.schema_version,
                    SessionHistoryCheckpointRecord.source_sequence == reference.base_sequence,
                )
            )
        ).one_or_none()
        if (
            row is None
            or row.source_id != reference.base_event_id
            or row.checksum != reference.checksum
        ):
            raise HistoryCheckpointConflictError("History reference has no matching immutable base")

    @staticmethod
    def _require_anchor(progress, expected: IntelligenceAnchor) -> None:
        if expected != (
            progress.completed_source_id,
            progress.completed_source_sequence,
            progress.completed_through_sequence,
        ):
            raise IntelligenceWriterConflictError(
                "Completed predecessor changed; reload before retry"
            )

    async def record_failure(self, session_key: str, source_id: UUID, code: str) -> None:
        async with self._write(session_key) as session:
            await session.execute(
                update(SessionIntelligenceProgressRecord)
                .where(
                    SessionIntelligenceProgressRecord.session_key == session_key,
                    SessionIntelligenceProgressRecord.pending_source_id == source_id,
                )
                .values(failure_code=code[:80])
            )
