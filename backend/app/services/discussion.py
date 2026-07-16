# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageType,
    RoomMessage,
)
from app.services.discussion_triggers import (
    DiscussionTrigger,
    DiscussionTriggerEvaluator,
)
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)

RoomPublisher = Callable[[RoomMessage], Awaitable[None]]


class GroundingValidator:
    FORBIDDEN_UNGROUNDED_PHRASES = ("team radio says", "radio message", "confirmed tyre")

    def validate(self, message: RoomMessage, evidence: list[MessageEvidence]) -> bool:
        lowered = message.content.lower()
        if any(phrase in lowered for phrase in self.FORBIDDEN_UNGROUNDED_PHRASES):
            return False
        if message.evidence_status == EvidenceStatus.GROUNDED and not evidence:
            return False
        return bool(message.content.strip())


class DeterministicRoomGenerator:
    """Factual templates that only reference fields present in normalized events."""

    def generate(
        self,
        event: NormalizedRaceEvent,
        trigger: DiscussionTrigger,
        agent_id: str,
        *,
        reply_to: RoomMessage | None = None,
        host_summary: bool = False,
    ) -> tuple[str, EvidenceStatus, Confidence]:
        payload = event.payload
        driver = event.driver_numbers[0] if event.driver_numbers else None
        lap = event.lap_number
        if host_summary:
            return (
                "The room agrees on the confirmed event. Further strategic impact remains "
                "uncertain until the next timing and position samples arrive.",
                EvidenceStatus.PARTIAL,
                Confidence.MEDIUM,
            )
        if reply_to is not None:
            return (
                "Agreed on the observed event. I would keep the interpretation narrow because "
                "the available sample does not establish the wider trend yet.",
                EvidenceStatus.PARTIAL,
                Confidence.MEDIUM,
            )
        if event.event_type == RaceEventType.PIT_STOP:
            duration = payload.get("pit_duration") or payload.get("duration")
            fact = (
                f"Driver {driver} made a recorded pit stop"
                if driver
                else "A pit stop was recorded"
            )
            if lap is not None:
                fact += f" on lap {lap}"
            if duration is not None:
                fact += f" with a recorded duration of {duration} seconds"
            compound = payload.get("compound")
            suffix = (
                f" The supplied stint data identifies {compound} tyres."
                if compound
                else " Tyre compound data is unavailable, so strategy impact remains uncertain."
            )
            return fact + "." + suffix, EvidenceStatus.GROUNDED, Confidence.HIGH
        if event.event_type in {
            RaceEventType.SAFETY_CAR,
            RaceEventType.VIRTUAL_SAFETY_CAR,
            RaceEventType.RED_FLAG,
            RaceEventType.YELLOW_FLAG,
        }:
            message = payload.get("message")
            fact = event.event_type.value.replace("_", " ").title()
            if message:
                fact += f": {message}"
            return (
                f"{fact}. The neutralisation is confirmed; its duration and pit-window effect "
                "are not known yet.",
                EvidenceStatus.GROUNDED,
                Confidence.HIGH,
            )
        if event.event_type in {RaceEventType.OVERTAKE, RaceEventType.POSITION_CHANGE}:
            position = payload.get("position")
            fact = (
                f"Driver {driver} has a confirmed position update"
                if driver
                else "Position changed"
            )
            if position is not None:
                fact += f" to P{position}"
            return (
                f"{fact}. The event confirms track position, but not whether the change came "
                "from an on-track pass, pit cycle or another cause.",
                EvidenceStatus.PARTIAL,
                Confidence.MEDIUM,
            )
        if event.event_type == RaceEventType.SESSION_FINISH:
            return (
                "The session is recorded as finished. Classification context is not included in "
                "this event, so the room will not infer a result.",
                EvidenceStatus.PARTIAL,
                Confidence.HIGH,
            )
        return (
            f"A {event.event_type.value.replace('_', ' ').lower()} event was recorded. The "
            "available data is insufficient for a stronger conclusion.",
            EvidenceStatus.PARTIAL,
            Confidence.LOW,
        )


class RaceRoomDiscussionEngine:
    def __init__(
        self,
        repository: SqlRaceRoomRepository,
        evaluator: DiscussionTriggerEvaluator,
        publisher: RoomPublisher | None = None,
    ) -> None:
        self.repository = repository
        self.evaluator = evaluator
        self.publisher = publisher
        self.generator = DeterministicRoomGenerator()
        self.validator = GroundingValidator()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._recent_content: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=30))

    async def consume(self, event: NormalizedRaceEvent) -> None:
        trigger = self.evaluator.evaluate(event)
        if trigger is None:
            return
        room = await self.repository.get_room_by_session(event.session_key)
        if room is None:
            return
        async with self._locks[room.slug]:
            await self._generate_chain(room.id, event, trigger)

    async def _generate_chain(
        self, room_id: Any, event: NormalizedRaceEvent, trigger: DiscussionTrigger
    ) -> None:
        primary = await self._build_message(room_id, event, trigger, trigger.agent_candidates[0])
        if primary is None:
            return
        await self._store(primary, event)
        if trigger.needs_reply and len(trigger.agent_candidates) > 1:
            reply = await self._build_message(
                room_id,
                event,
                trigger,
                trigger.agent_candidates[1],
                reply_to=primary,
            )
            if reply is not None:
                await self._store(reply, event)
        if trigger.needs_host_summary and primary.agent_id != "nova":
            summary = await self._build_message(
                room_id,
                event,
                trigger,
                "nova",
                reply_to=primary,
                host_summary=True,
            )
            if summary is not None:
                await self._store(summary, event)

    async def _build_message(
        self,
        room_id: Any,
        event: NormalizedRaceEvent,
        trigger: DiscussionTrigger,
        agent_id: str,
        *,
        reply_to: RoomMessage | None = None,
        host_summary: bool = False,
    ) -> RoomMessage | None:
        content, evidence_status, confidence = self.generator.generate(
            event,
            trigger,
            agent_id,
            reply_to=reply_to,
            host_summary=host_summary,
        )
        fingerprint = " ".join(content.lower().split())
        if fingerprint in self._recent_content[str(room_id)]:
            return None
        sequence = await self.repository.next_message_sequence(room_id)
        message = RoomMessage(
            room_id=room_id,
            agent_id=agent_id,
            sequence=sequence,
            lap_number=event.lap_number,
            wall_time=event.event_time,
            topic=trigger.topic,
            message_type=(
                MessageType.SUMMARY
                if host_summary
                else MessageType.AGREEMENT
                if reply_to
                else MessageType.ANALYSIS
            ),
            content=content,
            confidence=confidence,
            evidence_status=evidence_status,
            reply_to_message_id=reply_to.id if reply_to else None,
            trigger_event_id=event.id,
        )
        evidence = self._evidence(event, message)
        if not self.validator.validate(message, evidence):
            logger.warning("Rejected ungrounded room message agent=%s", agent_id)
            return None
        self._recent_content[str(room_id)].append(fingerprint)
        return message

    async def _store(self, message: RoomMessage, event: NormalizedRaceEvent) -> None:
        stored, inserted = await self.repository.insert_message(
            message, self._evidence(event, message)
        )
        if inserted and self.publisher is not None:
            await self.publisher(stored)

    @staticmethod
    def _evidence(event: NormalizedRaceEvent, message: RoomMessage) -> list[MessageEvidence]:
        return [
            MessageEvidence(
                message_id=message.id,
                evidence_type="normalized_event",
                source_provider=event.source,
                source_reference=str(event.id),
                metric_name="event_type",
                metric_value=event.event_type.value,
                context={
                    "sequence_number": event.sequence_number,
                    "lap_number": event.lap_number,
                    "driver_numbers": event.driver_numbers,
                    "available_fields": sorted(event.payload.keys()),
                },
            )
        ]
