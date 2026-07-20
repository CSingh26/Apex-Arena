# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from app.core.logging import configure_logging
from app.core.settings import Settings
from app.services.container import AppServices


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Explain completed-room historical backfill readiness and exclusions."
    )
    command.add_argument("--season", type=int, default=2026)
    command.add_argument("--room-slug")
    command.add_argument("--json-summary", action="store_true")
    return command


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def run(args: argparse.Namespace) -> int:
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    services = AppServices(settings)
    try:
        rows = await services.room_repository.list_completed_backfill_status(
            season=args.season,
            room_slug=args.room_slug,
        )
        payload: dict[str, Any] = {
            "season": args.season,
            "rooms_seen": len(rows),
            "backfill_candidates": sum(1 for row in rows if row["backfill_candidate"]),
            "ready_for_chat_generation": sum(
                1 for row in rows if not row["backfill_candidate"]
            ),
            "results": rows,
        }
        if args.json_summary:
            print(json.dumps(payload, default=_json_default, sort_keys=True))
        else:
            for row in rows:
                marker = "needs_backfill" if row["backfill_candidate"] else "ready"
                reasons = ",".join(row["repair_reasons"]) or row["exclusion_reason"]
                print(f"{row['slug']} {marker} {reasons}")
        return 0
    finally:
        await services.close()


def main() -> None:
    raise SystemExit(asyncio.run(run(parser().parse_args())))


if __name__ == "__main__":
    main()
