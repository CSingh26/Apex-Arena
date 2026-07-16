# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.models import NormalizedRaceEvent
from app.services.event_pipeline import NormalizedPersistResult
from app.services.raw_events import (
    RawEventCreate,
    RawEventRepositoryResult,
)
from app.storage.database import Database
from app.storage.models import NormalizedRaceEventRecord, RawProviderEventRecord


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
        values = event.model_dump()
        values["event_type"] = event.event_type.value
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

    async def max_sequence(self, session_key: str) -> int:
        statement = select(func.max(NormalizedRaceEventRecord.sequence_number)).where(
            NormalizedRaceEventRecord.session_key == session_key
        )
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one_or_none() or 0)

    async def count(self, session_key: str | None = None) -> int:
        statement = select(func.count(NormalizedRaceEventRecord.id))
        if session_key is not None:
            statement = statement.where(NormalizedRaceEventRecord.session_key == session_key)
        async with self.database.session_factory() as session:
            return int((await session.execute(statement)).scalar_one())

    async def list_for_session(
        self, session_key: str, after_sequence: int = 0, limit: int = 100
    ) -> list[NormalizedRaceEvent]:
        statement = (
            select(NormalizedRaceEventRecord)
            .where(
                NormalizedRaceEventRecord.session_key == session_key,
                NormalizedRaceEventRecord.sequence_number > after_sequence,
            )
            .order_by(NormalizedRaceEventRecord.sequence_number)
            .limit(limit)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
            return [
                NormalizedRaceEvent.model_validate(record, from_attributes=True)
                for record in records
            ]
