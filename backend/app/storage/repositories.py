# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.models import (
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
    RaceStateSnapshot,
)
from app.services.event_pipeline import NormalizedPersistResult, PipelineResult
from app.services.historical import IngestionRunSummary
from app.services.race_state import SnapshotPersistResult
from app.services.raw_events import (
    RawEventCreate,
    RawEventRepositoryResult,
)
from app.storage.database import Database
from app.storage.models import (
    IngestionRunRecord,
    NormalizedRaceEventRecord,
    RaceStateSnapshotRecord,
    RawProviderEventRecord,
)


class SqlIngestionRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def start(self, *, provider: str, session_key: str, metadata: dict[str, Any]) -> UUID:
        run_id = uuid4()
        async with self.database.session_factory() as session:
            session.add(
                IngestionRunRecord(
                    id=run_id,
                    provider=provider,
                    session_key=session_key,
                    status="running",
                    run_metadata=metadata,
                )
            )
            await session.commit()
        return run_id

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str,
        result: PipelineResult,
        last_event_at: datetime | None,
        last_error: str | None = None,
    ) -> None:
        async with self.database.session_factory() as session:
            await session.execute(
                update(IngestionRunRecord)
                .where(IngestionRunRecord.id == run_id)
                .values(
                    status=status,
                    ended_at=datetime.now(UTC),
                    last_event_at=last_event_at,
                    last_error=last_error,
                    raw_inserted=result.raw_inserted,
                    duplicates=result.raw_duplicates + result.normalized_duplicates,
                    normalized_inserted=result.normalized_inserted,
                )
            )
            await session.commit()

    async def latest(self) -> IngestionRunSummary | None:
        statement = (
            select(IngestionRunRecord).order_by(IngestionRunRecord.started_at.desc()).limit(1)
        )
        async with self.database.session_factory() as session:
            record = (await session.execute(statement)).scalar_one_or_none()
            if record is None:
                return None
            return IngestionRunSummary(
                id=record.id,
                provider=record.provider,
                session_key=record.session_key,
                status=record.status,
                started_at=record.started_at,
                ended_at=record.ended_at,
                last_event_at=record.last_event_at,
                last_error=record.last_error,
                metadata=record.run_metadata,
                raw_inserted=record.raw_inserted,
                duplicates=record.duplicates,
                normalized_inserted=record.normalized_inserted,
            )


class SqlRawEventRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def insert(self, event: RawEventCreate) -> RawEventRepositoryResult:
        statement = (
            insert(RawProviderEventRecord)
            .values(**event.model_dump())
            .on_conflict_do_nothing(constraint="uq_raw_provider_event_hash")
            .returning(RawProviderEventRecord.id)
        )
        async with self.database.session_factory() as session:
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            if inserted_id is not None:
                return RawEventRepositoryResult(record_id=inserted_id, is_new=True)

            existing_id = (
                await session.execute(
                    select(RawProviderEventRecord.id).where(
                        RawProviderEventRecord.deterministic_hash == event.deterministic_hash
                    )
                )
            ).scalar_one()
            return RawEventRepositoryResult(record_id=existing_id, is_new=False)

    async def count(self, session_key: str | None = None) -> int:
        statement = select(func.count(RawProviderEventRecord.id))
        if session_key is not None:
            statement = statement.where(RawProviderEventRecord.session_key == session_key)
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one())

    async def mark_status(self, record_id: UUID, status: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(
                update(RawProviderEventRecord)
                .where(RawProviderEventRecord.id == record_id)
                .values(processing_status=status)
            )
            await session.commit()


class SqlNormalizedEventRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def insert(self, event: NormalizedRaceEvent) -> NormalizedPersistResult:
        values = self._event_values(event)
        statement = (
            insert(NormalizedRaceEventRecord)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_normalized_event_dedup_key")
            .returning(NormalizedRaceEventRecord.id)
        )
        async with self.database.session_factory() as session:
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            if inserted_id is not None:
                return NormalizedPersistResult(record_id=inserted_id, is_new=True)
            existing_id = (
                await session.execute(
                    select(NormalizedRaceEventRecord.id).where(
                        NormalizedRaceEventRecord.dedup_key == event.dedup_key
                    )
                )
            ).scalar_one()
            return NormalizedPersistResult(record_id=existing_id, is_new=False)

    @staticmethod
    def _event_values(event: NormalizedRaceEvent) -> dict[str, Any]:
        values = event.model_dump(
            exclude={
                "event_type",
                "event_origin",
                "importance_level",
                "confidence_level",
                "derivation",
            }
        )
        values["event_type"] = event.event_type.value
        values["event_origin"] = event.event_origin.value
        values["importance_level"] = event.importance_level.value
        values["confidence_level"] = event.confidence_level.value
        values["derivation"] = (
            event.derivation.model_dump(mode="json") if event.derivation is not None else None
        )
        return values

    async def replace_derived_for_session(
        self,
        session_key: str,
        events: list[NormalizedRaceEvent],
    ) -> list[NormalizedRaceEvent]:
        """Atomically replace derived rows while preserving source sequence identities."""

        async with self.database.session_factory() as session:
            await session.execute(
                delete(NormalizedRaceEventRecord).where(
                    NormalizedRaceEventRecord.session_key == session_key,
                    NormalizedRaceEventRecord.event_origin == EventOrigin.DERIVED.value,
                )
            )
            source_max = int(
                (
                    await session.execute(
                        select(func.max(NormalizedRaceEventRecord.sequence_number)).where(
                            NormalizedRaceEventRecord.session_key == session_key,
                            NormalizedRaceEventRecord.event_origin
                            == EventOrigin.SOURCE_FACT.value,
                        )
                    )
                ).scalar_one_or_none()
                or 0
            )
            persisted = [
                event.model_copy(update={"sequence_number": source_max + index})
                for index, event in enumerate(events, start=1)
            ]
            session.add_all(
                [NormalizedRaceEventRecord(**self._event_values(event)) for event in persisted]
            )
            await session.commit()
            return persisted

    async def max_sequence(self, session_key: str) -> int:
        statement = select(func.max(NormalizedRaceEventRecord.sequence_number)).where(
            NormalizedRaceEventRecord.session_key == session_key
        )
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one_or_none() or 0)

    async def latest_session_key(self) -> str | None:
        statement = (
            select(NormalizedRaceEventRecord.session_key)
            .order_by(NormalizedRaceEventRecord.processed_at.desc())
            .limit(1)
        )
        async with self.database.session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none()

    async def count(self, session_key: str | None = None) -> int:
        statement = select(func.count(NormalizedRaceEventRecord.id))
        if session_key is not None:
            statement = statement.where(NormalizedRaceEventRecord.session_key == session_key)
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one())

    async def list_for_session(
        self,
        session_key: str,
        after_sequence: int = 0,
        limit: int = 100,
        *,
        event_types: list[RaceEventType] | None = None,
        driver_number: int | None = None,
        lap_number: int | None = None,
        minimum_importance: EventImportance | None = None,
        event_origin: EventOrigin | None = None,
        before_time: datetime | None = None,
    ) -> list[NormalizedRaceEvent]:
        predicates = [
            NormalizedRaceEventRecord.session_key == session_key,
            NormalizedRaceEventRecord.sequence_number > after_sequence,
        ]
        if event_types:
            predicates.append(
                NormalizedRaceEventRecord.event_type.in_([value.value for value in event_types])
            )
        if driver_number is not None:
            predicates.append(
                or_(
                    NormalizedRaceEventRecord.primary_driver_number == driver_number,
                    NormalizedRaceEventRecord.secondary_driver_number == driver_number,
                    NormalizedRaceEventRecord.driver_numbers.contains([driver_number]),
                )
            )
        if lap_number is not None:
            predicates.append(NormalizedRaceEventRecord.lap_number == lap_number)
        if minimum_importance is not None:
            levels = list(EventImportance)
            predicates.append(
                NormalizedRaceEventRecord.importance_level.in_(
                    [level.value for level in levels[levels.index(minimum_importance) :]]
                )
            )
        if event_origin is not None:
            predicates.append(NormalizedRaceEventRecord.event_origin == event_origin.value)
        if before_time is not None:
            predicates.append(NormalizedRaceEventRecord.event_time < before_time)
        statement = (
            select(NormalizedRaceEventRecord)
            .where(*predicates)
            .order_by(NormalizedRaceEventRecord.sequence_number)
            .limit(limit)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            return [
                NormalizedRaceEvent.model_validate(record, from_attributes=True)
                for record in records
            ]

    async def list_driver_profiles(self, session_key: str) -> list[NormalizedRaceEvent]:
        statement = (
            select(NormalizedRaceEventRecord)
            .where(
                NormalizedRaceEventRecord.session_key == session_key,
                NormalizedRaceEventRecord.event_type == RaceEventType.DRIVER_UPDATE.value,
            )
            .order_by(NormalizedRaceEventRecord.sequence_number)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            return [
                NormalizedRaceEvent.model_validate(record, from_attributes=True)
                for record in records
            ]

    async def sequence_for_lap(self, session_key: str, lap_number: int) -> int | None:
        statement = select(func.min(NormalizedRaceEventRecord.sequence_number)).where(
            NormalizedRaceEventRecord.session_key == session_key,
            NormalizedRaceEventRecord.lap_number >= lap_number,
        )
        async with self.database.session_factory() as session:
            sequence = (await session.execute(statement)).scalar_one_or_none()
            return int(sequence) if sequence is not None else None


class SqlRaceStateSnapshotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def insert(self, snapshot: RaceStateSnapshot) -> SnapshotPersistResult:
        statement = (
            insert(RaceStateSnapshotRecord)
            .values(**snapshot.model_dump())
            .on_conflict_do_nothing(constraint="uq_snapshot_session_sequence")
            .returning(RaceStateSnapshotRecord.id)
        )
        async with self.database.session_factory() as session:
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            if inserted_id is not None:
                return SnapshotPersistResult(record_id=inserted_id, is_new=True)
            existing_id = (
                await session.execute(
                    select(RaceStateSnapshotRecord.id).where(
                        RaceStateSnapshotRecord.session_key == snapshot.session_key,
                        RaceStateSnapshotRecord.sequence_number == snapshot.sequence_number,
                    )
                )
            ).scalar_one()
            return SnapshotPersistResult(record_id=existing_id, is_new=False)

    async def latest(self, session_key: str) -> RaceStateSnapshot | None:
        statement = (
            select(RaceStateSnapshotRecord)
            .where(RaceStateSnapshotRecord.session_key == session_key)
            .order_by(RaceStateSnapshotRecord.sequence_number.desc())
            .limit(1)
        )
        async with self.database.session_factory() as session:
            record = (await session.execute(statement)).scalar_one_or_none()
            if record is None:
                return None
            return RaceStateSnapshot.model_validate(record, from_attributes=True)

    async def count(self, session_key: str | None = None) -> int:
        statement = select(func.count(RaceStateSnapshotRecord.id))
        if session_key is not None:
            statement = statement.where(RaceStateSnapshotRecord.session_key == session_key)
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one())

    async def delete_for_session(self, session_key: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(
                delete(RaceStateSnapshotRecord).where(
                    RaceStateSnapshotRecord.session_key == session_key
                )
            )
            await session.commit()
