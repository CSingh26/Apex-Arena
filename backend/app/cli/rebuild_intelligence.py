# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.logging import configure_logging
from app.core.settings import Settings
from app.domain.intelligence import RaceIntelligenceConfig
from app.services.intelligence_rebuild import IntelligenceRebuildService
from app.storage.database import Database
from app.storage.intelligence_repository import SqlBattleSummaryRepository
from app.storage.repositories import (
    SqlNormalizedEventRepository,
    SqlRaceStateSnapshotRepository,
)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Deterministically rebuild derived race intelligence for one session."
    )
    command.add_argument("--session-key", required=True)
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--replace-derived", action="store_true")
    command.add_argument("--json", action="store_true", dest="json_output")
    return command


def _config(settings: Settings) -> RaceIntelligenceConfig:
    return RaceIntelligenceConfig(
        overtake_confirmation_seconds=settings.overtake_confirmation_seconds,
        overtake_confirmation_samples=settings.overtake_confirmation_samples,
        overtake_max_interval_seconds=settings.overtake_max_interval_seconds,
        battle_start_interval_seconds=settings.battle_start_interval_seconds,
        battle_start_samples=settings.battle_start_samples,
        battle_intense_interval_seconds=settings.battle_intense_interval_seconds,
        battle_end_interval_seconds=settings.battle_end_interval_seconds,
        battle_end_samples=settings.battle_end_samples,
        battle_trend_window=settings.battle_trend_window,
        battle_trend_minimum_change=settings.battle_trend_minimum_change,
        proximity_exit_seconds=settings.proximity_exit_seconds,
        event_cooldown_seconds=settings.intelligence_event_cooldown_seconds,
    )


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
    rebuild = IntelligenceRebuildService(
        SqlNormalizedEventRepository(database),
        SqlBattleSummaryRepository(database),
        config=_config(settings),
        snapshots=SqlRaceStateSnapshotRepository(database),
    )
    try:
        summary = await rebuild.run(
            args.session_key,
            dry_run=args.dry_run,
            replace_derived=args.replace_derived,
        )
        payload = summary.model_dump(mode="json")
        if args.json_output:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                "Race intelligence rebuild "
                f"session={summary.session_key} source={summary.source_event_count} "
                f"derived={summary.derived_event_count} battles={summary.resolved_battle_count} "
                f"rate={summary.events_per_second:.2f}/s dry_run={summary.dry_run}"
            )
        return 0
    finally:
        await database.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except ValueError as exc:
        print(f"Rebuild refused: {exc}", file=sys.stderr)
        code = 2
    except Exception as exc:
        print(f"Rebuild failed safely: {type(exc).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
