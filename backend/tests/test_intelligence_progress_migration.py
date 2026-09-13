# SPDX-License-Identifier: AGPL-3.0-only
"""Run only the additive recovery migration in an isolated task-owned schema."""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


async def test_progress_migration_preserves_source_facts_and_round_trips(intelligence_sql):
    migration = importlib.import_module("migrations.versions.20260912_0019_intelligence_progress")
    assert migration.down_revision == "20260912_0018"
    engine = intelligence_sql.session_factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"][None]
    async with engine.begin() as connection:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("DROP TABLE session_intelligence_progress"))
        await connection.execute(
            text("CREATE TABLE synthetic_migration_fact (id integer PRIMARY KEY)")
        )
        await connection.execute(text("INSERT INTO synthetic_migration_fact VALUES (7)"))

        def run(sync_connection, direction):
            original = migration.op
            migration.op = Operations(MigrationContext.configure(sync_connection))
            try:
                getattr(migration, direction)()
            finally:
                migration.op = original

        await connection.run_sync(run, "upgrade")
        assert (
            await connection.scalar(text("SELECT count(*) FROM session_intelligence_progress")) == 0
        )
        assert await connection.scalar(text("SELECT id FROM synthetic_migration_fact")) == 7
        await connection.run_sync(run, "downgrade")
        assert (
            await connection.scalar(text("SELECT to_regclass('session_intelligence_progress')"))
            is None
        )
        assert await connection.scalar(text("SELECT id FROM synthetic_migration_fact")) == 7
        await connection.run_sync(run, "upgrade")
