# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.domain.models import NormalizedRaceEvent
from app.domain.rooms import ChatGenerationStatus, RaceRoom
from app.services.discussion import RaceRoomDiscussionEngine
from app.services.discussion_triggers import DiscussionTriggerEvaluator
from app.storage.repositories import SqlNormalizedEventRepository
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)


@dataclass
class RoomChatGenerationResult:
    room_slug: str
    session_key: str | None
    status: str
    events_evaluated: int = 0
    triggers_selected: int = 0
    messages_inserted: int = 0
    messages_skipped: int = 0
    archived_messages: int = 0
    error: str | None = None


@dataclass
class ChatGenerationSummary:
    season: int
    generation_version: str
    dry_run: bool
    rooms_seen: int = 0
    rooms_completed: int = 0
    rooms_partial: int = 0
    rooms_failed: int = 0
    rooms_skipped: int = 0
    messages_inserted: int = 0
    archived_messages: int = 0
    results: list[RoomChatGenerationResult] = field(default_factory=list)


class HistoricalRoomChatGenerator:
    """Build durable historical race-room conversations from persisted OpenF1 events.

    This service intentionally does not subscribe to Redis or call the frontend.
    It reads normalized events from PostgreSQL and writes room_messages/evidence
    in small commits through the same repository path used by live discussions.
    """

    def __init__(
        self,
        *,
        rooms: SqlRaceRoomRepository,
        events: SqlNormalizedEventRepository,
        topic_cooldown_seconds: int,
    ) -> None:
        self.rooms = rooms
        self.events = events
        self.topic_cooldown_seconds = topic_cooldown_seconds

    async def run(
        self,
        *,
        season: int,
        completed_only: bool,
        room_slug: str | None,
        dry_run: bool,
        force_regenerate: bool,
        max_rooms: int | None,
        max_messages_per_room: int | None,
        generation_version: str,
    ) -> ChatGenerationSummary:
        candidates = await self.rooms.list_chat_generation_candidates(
            season=season,
            completed_only=completed_only,
            room_slug=room_slug,
            limit=max_rooms,
        )
        summary = ChatGenerationSummary(
            season=season, generation_version=generation_version, dry_run=dry_run
        )
        summary.rooms_seen = len(candidates)
        for room in candidates:
            result = await self._generate_room(
                room,
                dry_run=dry_run,
                force_regenerate=force_regenerate,
                max_messages=max_messages_per_room,
                generation_version=generation_version,
            )
            summary.results.append(result)
            summary.messages_inserted += result.messages_inserted
            summary.archived_messages += result.archived_messages
            if result.status == ChatGenerationStatus.COMPLETED.value:
                summary.rooms_completed += 1
            elif result.status == ChatGenerationStatus.PARTIAL.value:
                summary.rooms_partial += 1
            elif result.status == ChatGenerationStatus.SKIPPED.value:
                summary.rooms_skipped += 1
            else:
                summary.rooms_failed += 1
        return summary

    async def _generate_room(
        self,
        room: RaceRoom,
        *,
        dry_run: bool,
        force_regenerate: bool,
        max_messages: int | None,
        generation_version: str,
    ) -> RoomChatGenerationResult:
        result = RoomChatGenerationResult(
            room_slug=room.slug,
            session_key=room.session_key,
            status=ChatGenerationStatus.RUNNING.value,
        )
        if not room.session_key:
            result.status = ChatGenerationStatus.SKIPPED.value
            result.error = "missing provider session_key"
            return result
        normalized = await self._all_events(room.session_key)
        if not normalized:
            result.status = ChatGenerationStatus.SKIPPED.value
            result.error = "no normalized events available"
            if not dry_run:
                await self.rooms.mark_generation_status(
                    room.id,
                    ChatGenerationStatus.SKIPPED,
                    generation_version=generation_version,
                    error=result.error,
                )
            return result
        if dry_run:
            selected = self._count_triggers(normalized)
            result.events_evaluated = len(normalized)
            result.triggers_selected = selected
            result.messages_inserted = selected
            result.status = ChatGenerationStatus.COMPLETED.value
            return result

        await self.rooms.mark_generation_status(
            room.id, ChatGenerationStatus.RUNNING, generation_version=generation_version
        )
        if force_regenerate:
            result.archived_messages = await self.rooms.archive_generated_messages(
                room.id, generation_version
            )
        evaluator = DiscussionTriggerEvaluator(topic_cooldown_seconds=self.topic_cooldown_seconds)
        engine = RaceRoomDiscussionEngine(
            self.rooms, evaluator, generation_version=generation_version
        )
        engine.reset_session(room.session_key, str(room.id))
        try:
            for event in normalized:
                if max_messages is not None and result.messages_inserted >= max_messages:
                    result.status = ChatGenerationStatus.PARTIAL.value
                    result.error = "max messages per room reached"
                    break
                trigger = evaluator.evaluate(event)
                result.events_evaluated += 1
                if trigger is None:
                    continue
                result.triggers_selected += 1
                context = engine.context_builder.build(event, None)
                chain = await engine._generate_chain(room.id, event, trigger, context)
                result.messages_inserted += chain.inserted_count
                result.messages_skipped += chain.skipped_count
            if result.status == ChatGenerationStatus.RUNNING.value:
                result.status = ChatGenerationStatus.COMPLETED.value
            await self.rooms.mark_generation_status(
                room.id,
                ChatGenerationStatus(result.status),
                generation_version=generation_version,
                error=result.error,
            )
        except Exception as exc:
            result.status = ChatGenerationStatus.FAILED.value
            result.error = type(exc).__name__
            logger.exception("Historical room chat generation failed room_slug=%s", room.slug)
            await self.rooms.mark_generation_status(
                room.id,
                ChatGenerationStatus.FAILED,
                generation_version=generation_version,
                error=result.error,
            )
        return result

    async def _all_events(self, session_key: str) -> list[NormalizedRaceEvent]:
        events: list[NormalizedRaceEvent] = []
        after = 0
        while True:
            batch = await self.events.list_for_session(session_key, after_sequence=after, limit=500)
            if not batch:
                return events
            events.extend(batch)
            after = batch[-1].sequence_number

    def _count_triggers(self, events: list[NormalizedRaceEvent]) -> int:
        evaluator = DiscussionTriggerEvaluator(topic_cooldown_seconds=self.topic_cooldown_seconds)
        return sum(1 for event in events if evaluator.evaluate(event) is not None)
