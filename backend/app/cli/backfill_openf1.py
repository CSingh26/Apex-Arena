# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.logging import configure_logging
from app.core.settings import Settings
from app.services.container import AppServices
from app.services.openf1_backfill import OpenF1HistoricalBackfillService, OpenF1RoomFinalizer


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Backfill one completed OpenF1 session through the production pipeline."
    )
    command.add_argument("--season", type=int, default=2026)
    command.add_argument("--session-key")
    command.add_argument("--meeting-key")
    command.add_argument("--room-slug")
    command.add_argument("--from-round", type=int)
    command.add_argument("--to-round", type=int)
    command.add_argument("--endpoints")
    command.add_argument("--include-high-frequency", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--resume", action="store_true")
    command.add_argument("--max-sessions", type=int, default=1)
    command.add_argument("--force-retry-failed", action="store_true")
    command.add_argument("--json-summary", action="store_true")
    return command


def validate_args(args: argparse.Namespace) -> None:
    selectors = sum(bool(value) for value in (args.session_key, args.room_slug))
    if selectors != 1:
        raise ValueError("Specify exactly one of --session-key or --room-slug")
    if args.max_sessions != 1:
        raise ValueError("This command processes exactly one session; --max-sessions must be 1")
    if args.from_round is not None or args.to_round is not None:
        raise ValueError("Round ranges require a future reviewed batch command; select one room")
    if args.season < 1950 or args.season > 2100:
        raise ValueError("--season is outside the supported range")


async def run(args: argparse.Namespace) -> int:
    validate_args(args)
    # The CLI is an explicit ingestion context and always uses the direct DSN.
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    services = AppServices(settings)
    # Historical rows are replayed from PostgreSQL after finalization. Do not
    # synchronously fan thousands of archived events into the live Redis stream
    # or generate chat while an operator is rebuilding the durable sequence.
    services.processor.consumers = []
    backfill = OpenF1HistoricalBackfillService(
        settings=settings,
        client=services.openf1,
        adapter=services.historical,
        jobs=services.backfill_jobs,
        rooms=services.room_repository,
        database=services.database,
        finalizer=OpenF1RoomFinalizer(services.database),
        cli_safe=True,
    )
    try:
        summary = await backfill.run(
            season=args.season,
            room_slug=args.room_slug,
            session_key=args.session_key,
            meeting_key=args.meeting_key,
            endpoints=(
                [item.strip() for item in args.endpoints.split(",") if item.strip()]
                if args.endpoints
                else None
            ),
            include_high_frequency=args.include_high_frequency,
            dry_run=args.dry_run,
            resume=args.resume,
            force_retry_failed=args.force_retry_failed,
        )
        payload = summary.model_dump(mode="json")
        if args.json_summary:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                "OpenF1 backfill "
                f"status={payload['status']} session={payload['session_key']} "
                f"fetched={payload['rows_fetched']} inserted={payload['rows_inserted']} "
                f"deduplicated={payload['rows_deduplicated']}"
            )
        return 0 if summary.status not in {"locked", "partial"} else 2
    finally:
        await services.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (RuntimeError, ValueError) as exc:
        print(f"Backfill refused: {exc}", file=sys.stderr)
        code = 2
    except Exception as exc:
        print(f"Backfill failed safely: {type(exc).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
