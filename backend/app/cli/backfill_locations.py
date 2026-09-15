# SPDX-License-Identifier: AGPL-3.0-only
"""Backfill the driver track position series for one completed session."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app.cli.safe_errors import format_safe_cli_error
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.services.container import AppServices
from app.services.locations import LocationUnavailableError

logger = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Fetch OpenF1 /location for a session and store the replay-ready series."
    )
    command.add_argument("--session-key")
    command.add_argument("--room-slug")
    command.add_argument(
        "--max-minutes",
        type=int,
        help="Bound the fetch to the first N minutes of the session window.",
    )
    command.add_argument(
        "--sample-interval-ms",
        type=int,
        help="Override the per-driver downsample interval (default from settings).",
    )
    command.add_argument("--rebuild-geometry-only", action="store_true")
    command.add_argument(
        "--all-sessions",
        action="store_true",
        help="Rebuild geometry for every session with stored samples.",
    )
    command.add_argument("--reset", action="store_true", help="Drop stored samples first.")
    command.add_argument("--json-summary", action="store_true")
    return command


def validate_args(args: argparse.Namespace) -> None:
    if args.all_sessions:
        if args.session_key or args.room_slug:
            raise ValueError("--all-sessions cannot be combined with a single session selector")
        if not args.rebuild_geometry_only:
            # Refetching every session's whole series is a very different and
            # far more expensive operation than re-deriving stored geometry.
            raise ValueError("--all-sessions is only supported with --rebuild-geometry-only")
        if args.reset:
            raise ValueError("--all-sessions refuses --reset; it never drops stored samples")
    elif sum(bool(value) for value in (args.session_key, args.room_slug)) != 1:
        raise ValueError("Specify exactly one of --session-key or --room-slug")
    if args.max_minutes is not None and args.max_minutes <= 0:
        raise ValueError("--max-minutes must be positive")
    if args.sample_interval_ms is not None and args.sample_interval_ms < 0:
        raise ValueError("--sample-interval-ms cannot be negative")


async def resolve_session_key(services: AppServices, args: argparse.Namespace) -> str:
    if args.session_key:
        return str(args.session_key)
    room = await services.room_repository.get_room(args.room_slug)
    if room is None or room.session_key is None:
        raise ValueError(f"No provider session is bound to room {args.room_slug}")
    return str(room.session_key)


async def rebuild_all_geometry(services, *, json_summary: bool) -> int:
    """Re-derive every stored session's outline, reporting each result.

    One session failing must not abandon the rest: a circuit whose samples
    cannot support an outline is a fact about that session, not a reason to
    leave every other one stale.
    """
    ingestion = services.location_ingestion
    keys = await services.location_repository.session_keys()
    results: list[dict[str, object]] = []
    for session_key in keys:
        try:
            start, end = await ingestion.resolve_window(session_key, None, None)
            geometry = await ingestion.rebuild_geometry(session_key, start, end)
            points = len(geometry.path) if geometry else 0
            results.append({"session_key": session_key, "track_points": points})
        except Exception as exc:
            logger.warning(
                "location_geometry_rebuild_failed session_key=%s error=%s",
                session_key,
                type(exc).__name__,
            )
            results.append({"session_key": session_key, "error": type(exc).__name__})
    if json_summary:
        print(json.dumps({"sessions": results}, sort_keys=True, default=str))
    else:
        for row in results:
            detail = row.get("error") or f"track_points={row['track_points']}"
            print(f"Location geometry session={row['session_key']} {detail}")
    return 0


async def run(args: argparse.Namespace) -> int:
    validate_args(args)
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    services = AppServices(settings)
    # Location never enters the replay event sequence, so the live consumers
    # have nothing to do here and must not fan archived rows into Redis.
    services.processor.consumers = []
    try:
        if args.all_sessions:
            return await rebuild_all_geometry(services, json_summary=args.json_summary)
        session_key = await resolve_session_key(services, args)
        ingestion = services.location_ingestion
        if args.sample_interval_ms is not None:
            ingestion.sample_interval_ms = args.sample_interval_ms
        if args.reset:
            await services.location_repository.delete_for_session(session_key)

        if args.rebuild_geometry_only:
            start, end = await ingestion.resolve_window(session_key, None, None)
            geometry = await ingestion.rebuild_geometry(session_key, start, end)
            payload = {
                "session_key": session_key,
                "track_points": len(geometry.path) if geometry else 0,
                "bounds": geometry.bounds.model_dump() if geometry else None,
            }
        else:
            summary = await ingestion.ingest_session(session_key, max_minutes=args.max_minutes)
            payload = summary.model_dump(mode="json")

        if args.json_summary:
            print(json.dumps(payload, sort_keys=True, default=str))
        else:
            print(
                "Location backfill "
                f"session={payload['session_key']} "
                f"samples={payload.get('total_samples', 'n/a')} "
                f"drivers={len(payload.get('drivers', []))} "
                f"track_points={payload.get('track_points', 0)}"
            )
        return 0
    finally:
        await services.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (LocationUnavailableError, ValueError) as exc:
        print(f"Location backfill refused: {exc}", file=sys.stderr)
        code = 2
    except Exception as exc:
        print(format_safe_cli_error("Location backfill failed", exc), file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
