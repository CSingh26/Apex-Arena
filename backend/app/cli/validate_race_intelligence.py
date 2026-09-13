# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.cli.rebuild_intelligence import _config
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.services.intelligence_rebuild import (
    IntelligenceRebuildService,
    IntelligenceRebuildSummary,
)
from app.storage.database import Database
from app.storage.intelligence_repository import SqlBattleSummaryRepository
from app.storage.repositories import (
    SqlNormalizedEventRepository,
    SqlRaceStateSnapshotRepository,
)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Validate deterministic race intelligence without changing stored data."
    )
    command.add_argument("--session-key", required=True)
    command.add_argument("--json", action="store_true", dest="json_output")
    return command


def render_json(summary: IntelligenceRebuildSummary) -> str:
    return json.dumps(summary.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


async def run(args: argparse.Namespace) -> int:
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    database = Database(
        settings.async_migration_database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
    )
    validation = IntelligenceRebuildService(
        SqlNormalizedEventRepository(database),
        SqlBattleSummaryRepository(database),
        config=_config(settings),
        snapshots=SqlRaceStateSnapshotRepository(database),
    )
    try:
        summary = await validation.run(
            args.session_key,
            dry_run=True,
            replace_derived=False,
        )
        if args.json_output:
            print(render_json(summary))
        else:
            print(
                "Race intelligence validation "
                f"session={summary.session_key} source={summary.source_event_count} "
                f"derived={summary.derived_event_count} battles={summary.resolved_battle_count} "
                f"overtakes={summary.overtake_confirmations} "
                f"rejections={summary.overtake_rejections} "
                f"pit_exclusions={summary.pit_exclusions} "
                f"rate={summary.events_per_second:.2f}/s"
            )
        return 0
    finally:
        await database.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except Exception as exc:
        print(f"Validation failed safely: {type(exc).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
