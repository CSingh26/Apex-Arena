# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
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
        await self.engine.dispose()
