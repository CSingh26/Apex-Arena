# SPDX-License-Identifier: AGPL-3.0-only
import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


async def test_history_migration_preserves_existing_progress_and_round_trips(intelligence_sql):
    migration = importlib.import_module("migrations.versions.20260913_0022_history_checkpoints")
    assert migration.down_revision == "20260913_0021"
    engine = intelligence_sql.session_factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"][None]
    async with engine.begin() as connection:
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

        def run(sync, direction):
            original = migration.op
            migration.op = Operations(MigrationContext.configure(sync))
            try:
                getattr(migration, direction)()
            finally:
                migration.op = original

        await connection.run_sync(run, "downgrade")
        await connection.execute(
            text(
                "INSERT INTO session_intelligence_progress(session_key,algorithm_version,"
                "completed_through_sequence,completed_source_sequence,"
                "historical_effects_unverified) VALUES ('synthetic-migration','test-v1',7,0,true)"
            )
        )
        await connection.run_sync(run, "upgrade")
        result = (
            await connection.execute(
                text(
                    "SELECT completed_through_sequence,history_reference,history_detail_status "
                    "FROM session_intelligence_progress WHERE session_key='synthetic-migration'"
                )
            )
        ).one()
        assert result == (7, None, "legacy_history_unverified")
        await connection.run_sync(run, "downgrade")
        assert (
            await connection.scalar(
                text(
                    "SELECT completed_through_sequence FROM session_intelligence_progress "
                    "WHERE session_key='synthetic-migration'"
                )
            )
            == 7
        )
        await connection.run_sync(run, "upgrade")
