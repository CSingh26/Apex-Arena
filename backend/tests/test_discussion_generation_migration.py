# SPDX-License-Identifier: AGPL-3.0-only
"""Optional isolated-PostgreSQL coverage for the discussion epoch migration."""

from __future__ import annotations

import importlib
import os
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.storage.database import Database


@pytest.mark.asyncio
async def test_discussion_generation_migration_preserves_rooms_and_messages() -> None:
    url = os.environ.get("TEST_REPLAY_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_REPLAY_POSTGRES_URL to an isolated local PostgreSQL database")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    database = Database(url)
    schema = "discussion_migration_test_" + uuid4().hex
    migration = importlib.import_module("migrations.versions.20260912_0018_discussion_generation")
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(
                text("CREATE TABLE race_rooms (id integer PRIMARY KEY, slug text NOT NULL)")
            )
            await connection.execute(
                text(
                    "CREATE TABLE room_messages ("
                    "id integer PRIMARY KEY, room_id integer NOT NULL, sequence integer NOT NULL)"
                )
            )
            await connection.execute(
                text("INSERT INTO race_rooms (id, slug) VALUES (1, 'with-message'), (2, 'empty')")
            )
            await connection.execute(
                text("INSERT INTO room_messages (id, room_id, sequence) VALUES (10, 1, 7)")
            )

            def upgrade(sync_connection) -> None:
                original_op = migration.op
                migration.op = Operations(MigrationContext.configure(sync_connection))
                try:
                    migration.upgrade()
                finally:
                    migration.op = original_op

            await connection.run_sync(upgrade)
            legacy = (
                await connection.execute(
                    text("SELECT id, discussion_generation FROM race_rooms ORDER BY id")
                )
            ).all()
            messages = (
                await connection.execute(
                    text("SELECT id, room_id, sequence FROM room_messages ORDER BY id")
                )
            ).all()
            await connection.execute(
                text("INSERT INTO race_rooms (id, slug) VALUES (3, 'new-room')")
            )
            new_generation = await connection.scalar(
                text("SELECT discussion_generation FROM race_rooms WHERE id = 3")
            )

        assert legacy == [(1, 1), (2, 1)]
        assert messages == [(10, 1, 7)]
        assert new_generation == 1
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await database.close()
