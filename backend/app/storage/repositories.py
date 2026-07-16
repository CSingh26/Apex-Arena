# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.services.raw_events import (
    RawEventCreate,
    RawEventRepositoryResult,
)
from app.storage.database import Database
from app.storage.models import RawProviderEventRecord


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
