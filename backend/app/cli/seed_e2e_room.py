# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit synthetic E2E seed; never part of normal application startup."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.cli.e2e_fixtures import FIXTURE_VERSION, SESSION_KEY, fixture_room, normal_race_inputs
from app.cli.safe_errors import format_safe_cli_error
from app.core.settings import Settings
from app.domain.rooms import ChatGenerationStatus, IngestionStatus, RoomStatus
from app.services.container import AppServices
from app.services.room_agents import active_agent_profiles
from app.services.room_chat_generation import HistoricalRoomChatGenerator
from app.storage.models import (
    AgentProfileRecord,
    NormalizedRaceEventRecord,
    RaceRoomAgentRecord,
    RaceRoomRecord,
    RoomMessageRecord,
    RoomPlaybackStateRecord,
)


def require_e2e_database(settings: Settings) -> None:
    if settings.app_env not in {"local", "test"}:
        raise RuntimeError("Synthetic seeds are allowed only in local/test environments")
    database = urlparse(settings.database_url.get_secret_value())
    if (
        database.hostname not in {"localhost", "127.0.0.1", "postgres"}
        or not database.path.removeprefix("/").startswith("apex_e2e")
        # asyncpg accepts query overrides such as host/database. The isolated
        # seed supports only an unambiguous, query-free local connection URL.
        or database.query
        or database.fragment
    ):
        raise RuntimeError("Synthetic seeds require an isolated local E2E database named apex_e2e*")


async def seed(*, settings: Settings, scenario: str = "normal-race", slug: str = "e2e-normal-race"):
    require_e2e_database(settings)
    if scenario != "normal-race" or not re.fullmatch(r"e2e-[a-z0-9-]{1,100}", slug):
        raise RuntimeError("Use the normal-race scenario and an e2e- prefixed slug")
    services = AppServices(settings.model_copy(update={"app_process_role": "api"}))
    room = fixture_room(slug)
    try:
        await services.database.require_ingestion_schema()
        async with services.database.session_factory() as session:
            conflicts = (
                await session.scalars(
                    select(RaceRoomRecord).where(
                        or_(
                            RaceRoomRecord.slug == slug,
                            RaceRoomRecord.session_key == SESSION_KEY,
                            and_(
                                RaceRoomRecord.season == 2026,
                                RaceRoomRecord.round_number == 1,
                                RaceRoomRecord.session_type == "RACE",
                            ),
                        )
                    )
                )
            ).all()
            if any(
                row.id != room.id
                or row.slug != slug
                or row.session_key != SESSION_KEY
                or row.generation_version != FIXTURE_VERSION
                for row in conflicts
            ):
                raise RuntimeError("Refusing to overwrite unrelated E2E target data")
            existing = next(iter(conflicts), None)
            if existing is None:
                if await session.scalar(
                    select(func.count())
                    .select_from(NormalizedRaceEventRecord)
                    .where(NormalizedRaceEventRecord.session_key == SESSION_KEY)
                ):
                    raise RuntimeError("Refusing to attach unrelated existing session events")
                # Insert-only: a concurrent conflicting identity fails and rolls
                # back; never use room/agent upserts to overwrite user fixtures.
                session.add(RaceRoomRecord(**room.model_dump()))
                await session.flush()
                for order, agent in enumerate(active_agent_profiles(), 1):
                    await session.execute(
                        insert(AgentProfileRecord)
                        .values(**agent.model_dump())
                        .on_conflict_do_nothing(index_elements=["id"])
                    )
                    session.add(
                        RaceRoomAgentRecord(
                            room_id=room.id,
                            agent_id=agent.id,
                            sort_order=order * 10,
                            is_active=True,
                        )
                    )
                session.add(RoomPlaybackStateRecord(room_id=room.id))
                await session.commit()
            elif existing.ingestion_status == IngestionStatus.READY.value:
                return await _summary(services, slug)
        # These consumers are the real reducers. Exclude provider/network side
        # effects (championship/live locations/Redis) from synthetic ingestion.
        services.race_state.live_state_reader = None
        services.processor.consumers = [services.race_state, services.race_intelligence]
        await services.processor.ingest_batch(normal_race_inputs())
        generation = await HistoricalRoomChatGenerator(
            rooms=services.room_repository,
            events=services.normalized_event_repository,
            topic_cooldown_seconds=settings.room_topic_cooldown_seconds,
        ).run(
            season=2026,
            # The explicit owned slug is still INGESTING until all durable
            # discussion writes succeed; do not expose a half-ready fixture.
            completed_only=False,
            room_slug=slug,
            dry_run=False,
            force_regenerate=False,
            max_rooms=1,
            max_messages_per_room=None,
            generation_version=FIXTURE_VERSION,
        )
        if generation.rooms_completed != 1 or (await _summary(services, slug))["messages"] < 1:
            raise RuntimeError("Synthetic E2E discussion generation did not complete")
        async with services.database.session_factory() as session:
            await session.execute(
                update(RaceRoomRecord)
                .where(RaceRoomRecord.id == room.id)
                .values(
                    status=RoomStatus.COMPLETED.value,
                    ingestion_status=IngestionStatus.READY.value,
                    replay_available=True,
                    chat_generation_status=ChatGenerationStatus.COMPLETED.value,
                )
            )
            await session.commit()
        return await _summary(services, slug)
    finally:
        await services.close()


async def _summary(services: AppServices, slug: str) -> dict:
    async with services.database.session_factory() as session:
        room_id = await session.scalar(select(RaceRoomRecord.id).where(RaceRoomRecord.slug == slug))
        count = await session.scalar(
            select(func.count())
            .select_from(NormalizedRaceEventRecord)
            .where(NormalizedRaceEventRecord.session_key == SESSION_KEY)
        )
        messages = await session.scalar(
            select(func.count())
            .select_from(RoomMessageRecord)
            .where(RoomMessageRecord.room_id == room_id)
        )
    return {
        "slug": slug,
        "fixture": FIXTURE_VERSION,
        "normalized_events": count,
        "messages": messages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed synthetic data in an isolated local E2E database"
    )
    parser.add_argument("--scenario", choices=["normal-race"], default="normal-race")
    parser.add_argument("--slug", default="e2e-normal-race")
    args = parser.parse_args()
    try:
        result = asyncio.run(
            seed(settings=Settings(_env_file=None), scenario=args.scenario, slug=args.slug)
        )
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(format_safe_cli_error("E2E seed refused or failed", exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
