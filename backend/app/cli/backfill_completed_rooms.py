# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import configure_logging
from app.core.settings import Settings
from app.domain.rooms import RaceRoom
from app.services.container import AppServices
from app.services.openf1_backfill import BackfillStatus, OpenF1HistoricalBackfillService


class CompletedRoomBackfillResult(BaseModel):
    room_slug: str
    session_type: str
    scheduled_start: datetime
    resolved_session_key: str | None = None
    resolved_meeting_key: str | None = None
    match_method: str | None = None
    candidate_count: int = 0
    status: str
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_deduplicated: int = 0
    normalized_event_count: int = 0
    source_availability: str
    replay_available: bool
    error: str | None = None


class CompletedRoomBackfillSummary(BaseModel):
    season: int
    rooms_seen: int
    rooms_completed: int = 0
    rooms_partial: int = 0
    rooms_failed: int = 0
    rooms_skipped: int = 0
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_deduplicated: int = 0
    replay_ready: int = 0
    results: list[CompletedRoomBackfillResult] = Field(default_factory=list)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Backfill all completed 2026 competitive rooms before chat generation."
    )
    command.add_argument("--season", type=int, default=2026)
    command.add_argument("--room-slug")
    command.add_argument("--max-rooms", type=int)
    command.add_argument("--resume", action="store_true")
    command.add_argument("--force-retry-failed", action="store_true")
    command.add_argument("--include-high-frequency", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--json-summary", action="store_true")
    failure_mode = command.add_mutually_exclusive_group()
    failure_mode.add_argument("--continue-on-error", action="store_true", default=True)
    failure_mode.add_argument("--fail-fast", action="store_true")
    return command


def _print(payload: dict[str, Any], *, json_summary: bool) -> None:
    if json_summary:
        print(json.dumps(payload, sort_keys=True))
        return
    print(
        "Completed room backfill "
        f"season={payload['season']} seen={payload['rooms_seen']} "
        f"completed={payload['rooms_completed']} partial={payload['rooms_partial']} "
        f"failed={payload['rooms_failed']} replay_ready={payload['replay_ready']}"
    )


def _empty_result(
    room: RaceRoom, *, status: str, error: str | None = None
) -> CompletedRoomBackfillResult:
    return CompletedRoomBackfillResult(
        room_slug=room.slug,
        session_type=room.session_type.value,
        scheduled_start=room.scheduled_start,
        resolved_session_key=room.session_key,
        resolved_meeting_key=room.meeting_key,
        status=status,
        source_availability=room.source_availability.value,
        replay_available=room.replay_available,
        error=error,
    )


async def _room_result(
    services: AppServices,
    backfill: OpenF1HistoricalBackfillService,
    room: RaceRoom,
    args: argparse.Namespace,
) -> CompletedRoomBackfillResult:
    before_count = (
        await services.normalized_event_repository.count(room.session_key)
        if room.session_key
        else 0
    )
    summary = await backfill.run(
        season=args.season,
        room_slug=room.slug,
        include_high_frequency=args.include_high_frequency,
        dry_run=args.dry_run,
        resume=args.resume,
        force_retry_failed=args.force_retry_failed,
    )
    refreshed = await services.room_repository.get_room(room.slug)
    event_count = (
        await services.normalized_event_repository.count(summary.session_key)
        if summary.session_key
        else before_count
    )
    availability = (
        refreshed.source_availability.value if refreshed else summary.source_availability.value
    )
    replay_available = refreshed.replay_available if refreshed else summary.replay_available
    return CompletedRoomBackfillResult(
        room_slug=room.slug,
        session_type=room.session_type.value,
        scheduled_start=room.scheduled_start,
        resolved_session_key=summary.session_key,
        resolved_meeting_key=summary.meeting_key,
        match_method=summary.match_method,
        candidate_count=summary.candidate_count,
        status=summary.status.value,
        rows_fetched=summary.rows_fetched,
        rows_inserted=summary.rows_inserted,
        rows_deduplicated=summary.rows_deduplicated,
        normalized_event_count=event_count,
        source_availability=availability,
        replay_available=replay_available,
    )


async def run(args: argparse.Namespace) -> int:
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    services = AppServices(settings)
    services.processor.consumers = []
    backfill = OpenF1HistoricalBackfillService(
        settings=settings,
        client=services.openf1,
        adapter=services.historical,
        jobs=services.backfill_jobs,
        rooms=services.room_repository,
        database=services.database,
        finalizer=services.room_finalizer,
        cli_safe=True,
    )
    try:
        rooms = await services.room_repository.list_completed_backfill_candidates(
            season=args.season,
            room_slug=args.room_slug,
            limit=args.max_rooms,
        )
        summary = CompletedRoomBackfillSummary(season=args.season, rooms_seen=len(rooms))
        for room in rooms:
            try:
                result = await _room_result(services, backfill, room, args)
            except Exception as exc:
                result = _empty_result(room, status="failed", error=str(exc))
                summary.rooms_failed += 1
                summary.results.append(result)
                if args.fail_fast:
                    break
                continue
            summary.results.append(result)
            summary.rows_fetched += result.rows_fetched
            summary.rows_inserted += result.rows_inserted
            summary.rows_deduplicated += result.rows_deduplicated
            if result.replay_available:
                summary.replay_ready += 1
            if result.status == BackfillStatus.COMPLETED.value:
                summary.rooms_completed += 1
            elif result.status in {BackfillStatus.PARTIAL.value, BackfillStatus.LOCKED.value}:
                summary.rooms_partial += 1
            elif result.status == BackfillStatus.DRY_RUN.value:
                summary.rooms_skipped += 1
            else:
                summary.rooms_failed += 1
        payload = summary.model_dump(mode="json")
        _print(payload, json_summary=args.json_summary)
        return 2 if summary.rooms_failed or summary.rooms_partial else 0
    finally:
        await services.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except (RuntimeError, ValueError) as exc:
        print(f"Completed room backfill refused: {exc}", file=sys.stderr)
        code = 2
    except Exception as exc:
        print(f"Completed room backfill failed safely: {type(exc).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
