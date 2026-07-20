# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.logging import configure_logging
from app.core.settings import Settings
from app.storage.database import Database


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Print secret-safe database readiness metadata.")
    command.add_argument("--json-summary", action="store_true")
    return command


def migration_head() -> str | None:
    versions = Path(__file__).parents[2] / "migrations" / "versions"
    heads = sorted("_".join(path.stem.split("_")[:2]) for path in versions.glob("*.py"))
    return heads[-1] if heads else None


async def run(args: argparse.Namespace) -> int:
    settings = Settings(app_process_role="ingestor")  # type: ignore[call-arg]
    configure_logging(settings)
    database = Database(
        settings.async_migration_database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
    )
    try:
        healthy, health_detail = await database.health_check()
        async with database.session_factory() as session:
            version = (
                await session.execute(text("select version_num from alembic_version limit 1"))
            ).scalar_one_or_none()
            room_count = (
                await session.execute(text("select count(*) from race_rooms"))
            ).scalar_one()
            message_count = (
                await session.execute(
                    text("select count(*) from room_messages where archived_at is null")
                )
            ).scalar_one()
        payload = {
            "database_host": settings.safe_runtime_metadata["database_host"],
            "environment": settings.app_env,
            "process_role": settings.app_process_role,
            "healthy": healthy,
            "health_detail": health_detail,
            "alembic_current": version,
            "alembic_expected_head": migration_head(),
            "race_rooms": int(room_count),
            "active_room_messages": int(message_count),
        }
        if args.json_summary:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(
                f"DB healthy={payload['healthy']} alembic={version} "
                f"rooms={payload['race_rooms']} messages={payload['active_room_messages']}"
            )
        return 0 if healthy and version == payload["alembic_expected_head"] else 2
    finally:
        await database.close()


def main() -> None:
    try:
        code = asyncio.run(run(parser().parse_args()))
    except Exception as exc:
        print(f"Database status failed safely: {type(exc).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
