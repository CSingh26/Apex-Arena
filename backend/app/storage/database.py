# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class RequestSafeAsyncSession(AsyncSession):
    async def __aexit__(self, *args):
        # Starlette uses level cancellation: a disconnected SSE request must
        # still return its SQL connection even while its cancel scope is active.
        with anyio.CancelScope(shield=True):
            return await super().__aexit__(*args)


class Database:
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 3,
        max_overflow: int = 2,
        pool_timeout: int = 15,
        pool_recycle: int = 300,
    ) -> None:
        # Conservative pooling: managed free-tier databases cap total connections,
        # and pre-ping recovers sockets dropped during provider autosuspend.
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=RequestSafeAsyncSession,
            expire_on_commit=False,
        )
        self._ingestor_lease_connection: AsyncConnection | None = None
        self._ingestion_schema_verified = False

    async def require_ingestion_schema(self) -> None:
        """Refuse room writes from a newer worker against an older deployment schema."""
        if self._ingestion_schema_verified:
            return
        required = {
            ("normalized_race_events", "event_origin"),
            ("normalized_race_events", "primary_driver_number"),
            ("normalized_race_events", "importance_level"),
            ("session_location_samples", "sample_time"),
            ("session_track_geometry", "path"),
        }
        async with self.session_factory() as session:
            available = set(
                (
                    await session.execute(
                        text(
                            "SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema='public' AND table_name IN "
                            "('normalized_race_events','session_location_samples','session_track_geometry')"
                        )
                    )
                ).all()
            )
        if not required <= available:
            raise RuntimeError(
                "Database schema is behind this ingestor; "
                "coordinate application and migration rollout before writes"
            )
        self._ingestion_schema_verified = True

    @property
    def ingestor_lease_owned(self) -> bool:
        return self._ingestor_lease_connection is not None

    async def acquire_ingestor_lease(self) -> bool:
        """Hold a PostgreSQL advisory lock for the lifetime of the ingestion process."""
        if self._ingestor_lease_connection is not None:
            return True
        connection = await self.engine.connect()
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": 1_095_782_232},  # ASCII-ish stable identifier for APEX.
            )
        )
        if not acquired:
            await connection.close()
            return False
        self._ingestor_lease_connection = connection
        return True

    async def release_ingestor_lease(self) -> None:
        if self._ingestor_lease_connection is None:
            return
        connection = self._ingestor_lease_connection
        self._ingestor_lease_connection = None
        try:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": 1_095_782_232},
            )
        finally:
            await connection.close()

    @asynccontextmanager
    async def backfill_lease(self, season: int, session_key: str) -> AsyncIterator[bool]:
        """Hold a session-scoped advisory lock on one direct database connection."""
        digest = hashlib.sha256(f"openf1:{season}:{session_key}".encode()).digest()
        lock_id = int.from_bytes(digest[:8], "big", signed=True)
        connection = await self.engine.connect()
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
            )
        )
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                    )
                except Exception:
                    # A dropped session has already released its PostgreSQL lock.
                    pass
            await connection.close()

    @asynccontextmanager
    async def reconciliation_lease(self) -> AsyncIterator[bool]:
        """Serialize recent-session reconciliation across API/worker processes."""

        connection = await self.engine.connect()
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": 1_095_782_233},
            )
        )
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": 1_095_782_233},
                    )
                except Exception:
                    pass
            await connection.close()

    async def health_check(self, timeout_seconds: float = 2.0) -> tuple[bool, str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            return True, "connected"
        except Exception as exc:
            # Only the exception class is surfaced; connection strings and credentials never are.
            return False, f"unavailable ({type(exc).__name__})"

    async def close(self) -> None:
        await self.release_ingestor_lease()
        await self.engine.dispose()
