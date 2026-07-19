# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.storage.database import Database
from app.storage.models import OpenF1BackfillJobRecord


class SqlOpenF1BackfillJobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, season: int, session_key: str) -> OpenF1BackfillJobRecord | None:
        statement = select(OpenF1BackfillJobRecord).where(
            OpenF1BackfillJobRecord.season == season,
            OpenF1BackfillJobRecord.session_key == session_key,
        )
        async with self.database.session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none()

    async def latest(self) -> OpenF1BackfillJobRecord | None:
        statement = (
            select(OpenF1BackfillJobRecord)
            .order_by(OpenF1BackfillJobRecord.updated_at.desc())
            .limit(1)
        )
        async with self.database.session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none()

    async def prepare(
        self,
        *,
        season: int,
        meeting_key: str | None,
        session_key: str,
        room_slug: str | None,
        endpoints: list[str],
        metadata: dict[str, Any],
        force_retry_failed: bool = False,
    ) -> OpenF1BackfillJobRecord:
        now = datetime.now(UTC)
        statement = (
            insert(OpenF1BackfillJobRecord)
            .values(
                season=season,
                meeting_key=meeting_key,
                session_key=session_key,
                room_slug=room_slug,
                status="pending",
                requested_endpoints=endpoints,
                completed_endpoints=[],
                cursor_state={},
                job_metadata=metadata,
            )
            .on_conflict_do_nothing(constraint="uq_openf1_backfill_season_session")
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            record = (
                await session.execute(
                    select(OpenF1BackfillJobRecord)
                    .where(
                        OpenF1BackfillJobRecord.season == season,
                        OpenF1BackfillJobRecord.session_key == session_key,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if record.status == "failed" and not force_retry_failed:
                await session.rollback()
                raise RuntimeError("Backfill job previously failed; use --force-retry-failed")
            requested = list(dict.fromkeys([*record.requested_endpoints, *endpoints]))
            await session.execute(
                update(OpenF1BackfillJobRecord)
                .where(OpenF1BackfillJobRecord.id == record.id)
                .values(
                    meeting_key=meeting_key or record.meeting_key,
                    room_slug=room_slug or record.room_slug,
                    requested_endpoints=requested,
                    status="running",
                    started_at=record.started_at or now,
                    failed_endpoint=None,
                    last_error_code=None,
                    last_error_message=None,
                    updated_at=now,
                    job_metadata={**record.job_metadata, **metadata},
                )
            )
            await session.commit()
        refreshed = await self.get(season, session_key)
        assert refreshed is not None
        return refreshed

    async def complete_endpoint(
        self,
        season: int,
        session_key: str,
        endpoint: str,
        *,
        fetched: int,
        processed: int,
        inserted: int,
        deduplicated: int,
    ) -> OpenF1BackfillJobRecord:
        async with self.database.session_factory() as session:
            record = (
                await session.execute(
                    select(OpenF1BackfillJobRecord)
                    .where(
                        OpenF1BackfillJobRecord.season == season,
                        OpenF1BackfillJobRecord.session_key == session_key,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            completed = list(dict.fromkeys([*record.completed_endpoints, endpoint]))
            cursor = dict(record.cursor_state)
            cursor[endpoint] = {
                "completed_at": datetime.now(UTC).isoformat(),
                "rows_fetched": fetched,
            }
            cursor["current_endpoint"] = None
            await session.execute(
                update(OpenF1BackfillJobRecord)
                .where(OpenF1BackfillJobRecord.id == record.id)
                .values(
                    completed_endpoints=completed,
                    rows_fetched=record.rows_fetched + fetched,
                    rows_processed=record.rows_processed + processed,
                    rows_inserted=record.rows_inserted + inserted,
                    rows_deduplicated=record.rows_deduplicated + deduplicated,
                    cursor_state=cursor,
                    status="running",
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
        refreshed = await self.get(season, session_key)
        assert refreshed is not None
        return refreshed

    async def start_endpoint(self, season: int, session_key: str, endpoint: str) -> None:
        async with self.database.session_factory() as session:
            record = (
                await session.execute(
                    select(OpenF1BackfillJobRecord)
                    .where(
                        OpenF1BackfillJobRecord.season == season,
                        OpenF1BackfillJobRecord.session_key == session_key,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            cursor = dict(record.cursor_state)
            cursor["current_endpoint"] = endpoint
            await session.execute(
                update(OpenF1BackfillJobRecord)
                .where(OpenF1BackfillJobRecord.id == record.id)
                .values(cursor_state=cursor, updated_at=datetime.now(UTC))
            )
            await session.commit()

    async def finish(self, season: int, session_key: str, *, partial: bool) -> None:
        now = datetime.now(UTC)
        async with self.database.session_factory() as session:
            await session.execute(
                update(OpenF1BackfillJobRecord)
                .where(
                    OpenF1BackfillJobRecord.season == season,
                    OpenF1BackfillJobRecord.session_key == session_key,
                )
                .values(
                    status="partial" if partial else "completed",
                    completed_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    async def fail(self, season: int, session_key: str, endpoint: str, exc: Exception) -> None:
        async with self.database.session_factory() as session:
            record = (
                await session.execute(
                    select(OpenF1BackfillJobRecord).where(
                        OpenF1BackfillJobRecord.season == season,
                        OpenF1BackfillJobRecord.session_key == session_key,
                    )
                )
            ).scalar_one()
            cursor = dict(record.cursor_state)
            cursor["current_endpoint"] = endpoint
            await session.execute(
                update(OpenF1BackfillJobRecord)
                .where(
                    OpenF1BackfillJobRecord.season == season,
                    OpenF1BackfillJobRecord.session_key == session_key,
                )
                .values(
                    status="failed",
                    failed_endpoint=endpoint,
                    rows_failed=OpenF1BackfillJobRecord.rows_failed + 1,
                    last_error_code=type(exc).__name__[:80],
                    last_error_message="Provider endpoint failed; safe to resume",
                    cursor_state=cursor,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
