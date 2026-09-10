# SPDX-License-Identifier: AGPL-3.0-only
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import anyio.lowlevel
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.database import Database


@pytest.mark.asyncio
async def test_old_schema_is_rejected_before_ingestion_writes():
    db = Database("postgresql+asyncpg://test:test@localhost/test")
    context = AsyncMock()
    context.__aenter__.return_value.execute.return_value = SimpleNamespace(all=lambda: [])
    db.session_factory = MagicMock(return_value=context)
    try:
        with pytest.raises(RuntimeError, match="schema"):
            await db.require_ingestion_schema()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_request_cancellation_allows_database_session_cleanup(monkeypatch):
    cleaned = []

    async def close_session(self, *args):
        await anyio.lowlevel.checkpoint()
        cleaned.append(True)

    monkeypatch.setattr(AsyncSession, "__aexit__", close_session)
    db = Database("postgresql+asyncpg://test:test@localhost/test")
    try:
        with anyio.CancelScope() as scope:
            async with db.session_factory():
                scope.cancel()
        assert cleaned == [True]
    finally:
        await db.close()
