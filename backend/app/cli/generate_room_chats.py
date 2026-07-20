# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.logging import configure_logging
from app.core.settings import Settings
from app.services.container import AppServices
from app.services.room_chat_generation import HistoricalRoomChatGenerator


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Generate persisted race-room chats from already-normalized OpenF1 events."
    )
    command.add_argument("--season", type=int, default=2026)
    command.add_argument("--room-slug")
    command.add_argument("--completed-only", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--json-summary", action="store_true")
    command.add_argument("--force-regenerate", action="store_true")
    command.add_argument("--max-rooms", type=int)
    command.add_argument("--max-messages-per-room", type=int)
    command.add_argument("--generation-version", default="rooms-v4-stat-debate")
    return command


async def run(args: argparse.Namespace) -> int:
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    services = AppServices(settings)
    services.processor.consumers = []
    generator = HistoricalRoomChatGenerator(
        rooms=services.room_repository,
        events=services.normalized_event_repository,
        topic_cooldown_seconds=settings.room_topic_cooldown_seconds,
    )
    try:
        summary = await generator.run(
            season=args.season,
            completed_only=args.completed_only,
            room_slug=args.room_slug,
            dry_run=args.dry_run,
            force_regenerate=args.force_regenerate,
            max_rooms=args.max_rooms,
            max_messages_per_room=args.max_messages_per_room,
            generation_version=args.generation_version,
        )
        payload = {
            "season": summary.season,
            "generation_version": summary.generation_version,
            "dry_run": summary.dry_run,
            "rooms_seen": summary.rooms_seen,
            "rooms_completed": summary.rooms_completed,
            "rooms_partial": summary.rooms_partial,
            "rooms_failed": summary.rooms_failed,
            "rooms_skipped": summary.rooms_skipped,
            "messages_inserted": summary.messages_inserted,
            "archived_messages": summary.archived_messages,
            "results": [item.__dict__ for item in summary.results],
        }
        if args.json_summary:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                "Room chat generation "
                f"season={summary.season} rooms={summary.rooms_seen} "
                f"messages={summary.messages_inserted} failed={summary.rooms_failed} "
                f"dry_run={summary.dry_run}"
            )
        return 0 if summary.rooms_failed == 0 else 2
    finally:
        await services.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except Exception as exc:
        print(f"Generate room chats failed safely: {type(exc).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
