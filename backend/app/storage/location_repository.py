# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.domain.locations import (
    DriverLocationSample,
    SessionTrackGeometry,
    TrackBounds,
)
from app.storage.database import Database
from app.storage.models import SessionLocationSampleRecord, SessionTrackGeometryRecord


class SqlSessionLocationRepository:
    """Time-indexed access to persisted driver track positions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def bulk_insert(
        self,
        session_key: str,
        samples: list[DriverLocationSample],
        *,
        source: str = "historical",
        chunk_size: int = 1000,
    ) -> int:
        """Insert samples idempotently; re-running a backfill must not duplicate."""

        if not samples:
            return 0
        inserted = 0
        async with self.database.session_factory() as session:
            for start in range(0, len(samples), chunk_size):
                rows = [
                    {
                        "id": uuid4(),
                        "session_key": session_key,
                        "driver_number": sample.driver_number,
                        "sample_time": sample.sample_time,
                        "x": sample.x,
                        "y": sample.y,
                        "z": sample.z,
                        "source": source,
                    }
                    for sample in samples[start : start + chunk_size]
                ]
                result = await session.execute(
                    insert(SessionLocationSampleRecord)
                    .values(rows)
                    .on_conflict_do_nothing(constraint="uq_location_sample_session_driver_time")
                    .returning(SessionLocationSampleRecord.id)
                )
                inserted += len(result.all())
            await session.commit()
        return inserted

    async def count(self, session_key: str) -> int:
        async with self.database.session_factory() as session:
            return int(
                (
                    await session.execute(
                        select(func.count(SessionLocationSampleRecord.id)).where(
                            SessionLocationSampleRecord.session_key == session_key
                        )
                    )
                ).scalar_one()
            )

    async def counts_for_sessions(self, session_keys: list[str]) -> dict[str, int]:
        if not session_keys:
            return {}
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        SessionLocationSampleRecord.session_key,
                        func.count(SessionLocationSampleRecord.id),
                    )
                    .where(SessionLocationSampleRecord.session_key.in_(session_keys))
                    .group_by(SessionLocationSampleRecord.session_key)
                )
            ).all()
        return {str(key): int(count) for key, count in rows}

    async def time_range(self, session_key: str) -> tuple[datetime | None, datetime | None]:
        async with self.database.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        func.min(SessionLocationSampleRecord.sample_time),
                        func.max(SessionLocationSampleRecord.sample_time),
                    ).where(SessionLocationSampleRecord.session_key == session_key)
                )
            ).one()
        return row[0], row[1]

    async def driver_numbers(self, session_key: str) -> list[int]:
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(SessionLocationSampleRecord.driver_number)
                    .where(SessionLocationSampleRecord.session_key == session_key)
                    .distinct()
                    .order_by(SessionLocationSampleRecord.driver_number)
                )
            ).all()
        return [int(row[0]) for row in rows]

    async def latest_per_driver(
        self,
        session_key: str,
        *,
        at: datetime | None = None,
    ) -> list[DriverLocationSample]:
        """Newest sample at or before ``at`` for every driver.

        Providers never emit all cars on one shared timestamp, so an exact
        timestamp match would return nothing. This is a per-driver "latest
        known position", which is exactly what the map needs.
        """

        latest_time = func.max(SessionLocationSampleRecord.sample_time).label("sample_time")
        newest = select(
            SessionLocationSampleRecord.driver_number,
            latest_time,
        ).where(SessionLocationSampleRecord.session_key == session_key)
        if at is not None:
            newest = newest.where(SessionLocationSampleRecord.sample_time <= at)
        newest = newest.group_by(SessionLocationSampleRecord.driver_number).subquery()

        statement = (
            select(SessionLocationSampleRecord)
            .join(
                newest,
                (SessionLocationSampleRecord.driver_number == newest.c.driver_number)
                & (SessionLocationSampleRecord.sample_time == newest.c.sample_time),
            )
            .where(SessionLocationSampleRecord.session_key == session_key)
            .order_by(SessionLocationSampleRecord.driver_number)
        )
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
        return [_to_sample(record) for record in records]

    async def window(
        self,
        session_key: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        driver_number: int | None = None,
        limit: int = 20_000,
    ) -> list[DriverLocationSample]:
        statement = select(SessionLocationSampleRecord).where(
            SessionLocationSampleRecord.session_key == session_key
        )
        if since is not None:
            statement = statement.where(SessionLocationSampleRecord.sample_time >= since)
        if until is not None:
            statement = statement.where(SessionLocationSampleRecord.sample_time <= until)
        if driver_number is not None:
            statement = statement.where(SessionLocationSampleRecord.driver_number == driver_number)
        statement = statement.order_by(
            SessionLocationSampleRecord.sample_time,
            SessionLocationSampleRecord.driver_number,
        ).limit(limit)
        async with self.database.session_factory() as session:
            records = (await session.execute(statement)).scalars().all()
        return [_to_sample(record) for record in records]

    async def sample_points(
        self,
        session_key: str,
        limit: int = 40_000,
    ) -> list[tuple[float, float]]:
        statement = (
            select(SessionLocationSampleRecord.x, SessionLocationSampleRecord.y)
            .where(SessionLocationSampleRecord.session_key == session_key)
            .order_by(SessionLocationSampleRecord.sample_time)
            .limit(limit)
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [(float(row[0]), float(row[1])) for row in rows]

    async def delete_for_session(self, session_key: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(
                delete(SessionLocationSampleRecord).where(
                    SessionLocationSampleRecord.session_key == session_key
                )
            )
            await session.execute(
                delete(SessionTrackGeometryRecord).where(
                    SessionTrackGeometryRecord.session_key == session_key
                )
            )
            await session.commit()

    async def save_geometry(self, geometry: SessionTrackGeometry) -> None:
        values = {
            "session_key": geometry.session_key,
            "min_x": geometry.bounds.min_x,
            "max_x": geometry.bounds.max_x,
            "min_y": geometry.bounds.min_y,
            "max_y": geometry.bounds.max_y,
            "path": [[point[0], point[1]] for point in geometry.path],
            "source_driver_number": geometry.source_driver_number,
            "sample_count": geometry.sample_count,
        }
        async with self.database.session_factory() as session:
            await session.execute(
                insert(SessionTrackGeometryRecord)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[SessionTrackGeometryRecord.session_key],
                    set_={key: value for key, value in values.items() if key != "session_key"},
                )
            )
            await session.commit()

    async def get_geometry(self, session_key: str) -> SessionTrackGeometry | None:
        async with self.database.session_factory() as session:
            record = (
                await session.execute(
                    select(SessionTrackGeometryRecord).where(
                        SessionTrackGeometryRecord.session_key == session_key
                    )
                )
            ).scalar_one_or_none()
        if record is None:
            return None
        return SessionTrackGeometry(
            session_key=record.session_key,
            bounds=TrackBounds(
                min_x=record.min_x,
                max_x=record.max_x,
                min_y=record.min_y,
                max_y=record.max_y,
            ),
            path=[(float(point[0]), float(point[1])) for point in record.path or []],
            source_driver_number=record.source_driver_number,
            sample_count=record.sample_count,
        )


def _to_sample(record: SessionLocationSampleRecord) -> DriverLocationSample:
    return DriverLocationSample(
        driver_number=record.driver_number,
        x=record.x,
        y=record.y,
        z=record.z,
        sample_time=record.sample_time,
    )
