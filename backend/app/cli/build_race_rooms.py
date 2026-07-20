# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from app.cli.safe_errors import format_safe_cli_error
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.services.container import AppServices


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Synchronize Apex Arena race-room catalog from provider calendars."
    )
    command.add_argument("--season", type=int, default=2026)
    command.add_argument("--completed-only", action="store_true")
    command.add_argument("--room-slug")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--json-summary", action="store_true")
    command.add_argument("--force-refresh", action="store_true")
    return command


async def run(args: argparse.Namespace) -> int:
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    services = AppServices(settings)
    services.processor.consumers = []
    try:
        before_total = 0
        _, before_total = await services.room_repository.list_rooms(
            season=args.season,
            limit=1,
            include_unavailable=True,
            include_development=True,
        )
        if not args.dry_run:
            if args.force_refresh:
                services.rooms.invalidate_catalog()
            await services.rooms.force_sync()
        rooms, total = await services.room_repository.list_rooms(
            season=args.season,
            limit=500,
            include_unavailable=True,
            include_development=True,
        )
        if args.room_slug:
            rooms = [room for room in rooms if room.slug == args.room_slug]
        if args.completed_only:
            rooms = [room for room in rooms if room.scheduled_start <= datetime.now(UTC)]
        payload = {
            "season": args.season,
            "dry_run": args.dry_run,
            "rooms_before": before_total,
            "rooms_after": total,
            "rooms_returned": len(rooms),
            "room_slugs": [room.slug for room in rooms],
        }
        if args.json_summary:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                f"Race room catalog synced season={args.season} rooms={payload['rooms_after']} "
                f"returned={payload['rooms_returned']} dry_run={args.dry_run}"
            )
        return 0
    finally:
        await services.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except Exception as exc:
        print(format_safe_cli_error("Build race rooms failed safely", exc), file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
