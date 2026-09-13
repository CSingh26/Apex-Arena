# SPDX-License-Identifier: AGPL-3.0-only
"""Run only the additive claim-memory migration in an isolated task-owned schema."""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


async def test_claim_migration_round_trips_without_touching_existing_facts(intelligence_sql):
    migration = importlib.import_module("migrations.versions.20260913_0023_agent_claims")
    assert migration.down_revision == "20260913_0022"
    engine = intelligence_sql.session_factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"][None]
    async with engine.begin() as connection:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("DROP TABLE agent_claims"))
        await connection.execute(text("CREATE TABLE synthetic_claim_fact (id integer PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO synthetic_claim_fact VALUES (11)"))

        def run(sync_connection, direction):
            original = migration.op
            migration.op = Operations(MigrationContext.configure(sync_connection))
            try:
                getattr(migration, direction)()
            finally:
                migration.op = original

        await connection.run_sync(run, "upgrade")
        assert await connection.scalar(text("SELECT count(*) FROM agent_claims")) == 0
        assert await connection.scalar(text("SELECT id FROM synthetic_claim_fact")) == 11

        await connection.run_sync(run, "downgrade")
        assert await connection.scalar(text("SELECT to_regclass('agent_claims')")) is None
        # A memory table is recall, not a record of fact; dropping it must leave
        # the factual tables completely untouched.
        assert await connection.scalar(text("SELECT id FROM synthetic_claim_fact")) == 11

        await connection.run_sync(run, "upgrade")
        assert await connection.scalar(text("SELECT count(*) FROM agent_claims")) == 0
