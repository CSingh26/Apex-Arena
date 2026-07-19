# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio

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


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self._ingestor_lease_connection: AsyncConnection | None = None

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
