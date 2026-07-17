# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageType,
    RoomMessage,
)
from app.services.discussion_triggers import DiscussionTrigger, DiscussionTriggerEvaluator
from app.services.race_state import RaceState
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)

RoomPublisher = Callable[[RoomMessage], Awaitable[object]]
StateReader = Callable[[str], Awaitable[RaceState]]


class GroundedClaim(BaseModel):
    claim: str
    evidence_keys: list[str] = Field(min_length=1)


class GeneratedRoomMessage(BaseModel):
    message_type: MessageType
    content: str
    confidence: Confidence
    evidence_status: EvidenceStatus
    claims: list[GroundedClaim]


class GroundingContext(BaseModel):
    evidence: dict[str, Any]
    data_quality: str


class DiscussionMetrics(BaseModel):
    trigger_count: int = 0
    generated_message_count: int = 0
    rejected_message_count: int = 0
    deterministic_fallback_count: int = 0


class GroundingContextBuilder:
    def build(self, event: NormalizedRaceEvent, state: RaceState | None) -> GroundingContext:
        evidence: dict[str, Any] = {
            "event_type": event.event_type.value,
            "event_sequence": event.sequence_number,
            "lap_number": event.lap_number,
            "driver_numbers": event.driver_numbers,
        }
        evidence.update(event.payload)
        if state is not None:
            evidence["race_status"] = state.status
            evidence["race_current_lap"] = state.current_lap
            relevant = {
                driver: driver_state.model_dump(mode="json")
                for driver, driver_state in state.drivers.items()
                if int(driver) in event.driver_numbers
            }
            if relevant:
                evidence["relevant_driver_state"] = relevant
        quality = str(event.payload.get("data_quality") or "partial")
        return GroundingContext(evidence=evidence, data_quality=quality)


class GroundingValidator:
    FORBIDDEN_UNGROUNDED_PHRASES = ("team radio says", "radio message", "confirmed tyre")

    def validate(
        self,
        message: GeneratedRoomMessage | RoomMessage,
        context: GroundingContext | list[MessageEvidence],
    ) -> bool:
        lowered = message.content.lower()
        if not message.content.strip() or any(
            phrase in lowered for phrase in self.FORBIDDEN_UNGROUNDED_PHRASES
        ):
            return False
        if isinstance(message, RoomMessage):
            return not (message.evidence_status == EvidenceStatus.GROUNDED and not context)
        assert isinstance(context, GroundingContext)
        available = set(context.evidence)
        if any(not set(claim.evidence_keys) <= available for claim in message.claims):
            return False
        supplied_drivers = {str(driver) for driver in context.evidence.get("driver_numbers", [])}
        mentioned_drivers = set(re.findall(r"\bDriver (\d+)\b", message.content))
        if not mentioned_drivers <= supplied_drivers:
            return False
        if context.data_quality == "incomplete" and message.confidence == Confidence.HIGH:
            return False
        return bool(message.claims)


class DeterministicRoomGenerator:
    """Specialist templates that can only state values present in grounded context."""

    def generate(
        self,
        event: NormalizedRaceEvent,
        trigger: DiscussionTrigger,
        agent_id: str,
        context: GroundingContext | None = None,
        *,
        reply_to: RoomMessage | None = None,
        host_summary: bool = False,
    ) -> GeneratedRoomMessage:
        context = context or GroundingContextBuilder().build(event, None)
        evidence = context.evidence
        driver = event.driver_numbers[0] if event.driver_numbers else None
        lap = event.lap_number
        base_keys = ["event_type", "event_sequence"]
        if host_summary:
            return self._message(
                MessageType.SUMMARY,
                "The confirmed race-control event changes the current phase. The room agrees "
                "on the event itself; its duration and strategic effect remain uncertain.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "A major race phase changed.",
                base_keys,
            )
        if reply_to is not None:
            return self._reply(event, agent_id, context)
        if event.event_type in {RaceEventType.SESSION_START, RaceEventType.RACE_START}:
            return self._message(
                MessageType.OBSERVATION,
                "The session start is confirmed. The room will wait for timing samples before "
                "drawing conclusions about pace or strategy.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "The session started.",
                base_keys,
            )
        if event.event_type == RaceEventType.LAP_COMPLETED:
            trend = evidence.get("pace_trend_seconds")
            if trend is not None:
                return self._message(
                    MessageType.ANALYSIS,
                    f"Driver {driver}'s supplied representative-lap sample improves by "
                    f"{abs(float(trend)):.2f} seconds. That is a measured pace trend, but its "
                    "strategic value still depends on gaps and traffic.",
                    Confidence.HIGH,
                    EvidenceStatus.GROUNDED,
                    "The representative-lap sample contains a pace trend.",
                    ["driver_numbers", "pace_trend_seconds", "representative_laps"],
                )
            duration = evidence.get("lap_duration")
            duration_text = f" in {duration} seconds" if duration is not None else ""
            return self._message(
                MessageType.OBSERVATION,
                f"Driver {driver} completed lap {lap}{duration_text}. No broader pace trend is "
                "established by one lap.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "A lap was completed.",
                ["driver_numbers", "lap_number", "event_type"],
            )
        if event.event_type == RaceEventType.PIT_STOP:
            duration = evidence.get("pit_duration") or evidence.get("duration")
            fact = f"Driver {driver} made a recorded pit stop"
            if lap is not None:
                fact += f" on lap {lap}"
            keys = ["driver_numbers", "event_type"]
            if duration is not None:
                fact += f" with a recorded duration of {duration} seconds"
                keys.append("pit_duration")
            return self._message(
                MessageType.ANALYSIS,
                fact + ". Tyre life and undercut outcome remain uncertain until the next position "
                "sample.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A pit stop was recorded.",
                keys,
            )
        if event.event_type == RaceEventType.TYRE_CHANGE:
            compound = evidence.get("compound")
            text = (
                f"Driver {driver}'s supplied stint update records {compound} tyres. "
                "The compound is confirmed, but future stint length is not."
                if compound
                else f"Driver {driver} has a tyre-change event without compound data. "
                "Strategy impact cannot yet be assessed."
            )
            keys = ["driver_numbers", "event_type"] + (["compound"] if compound else [])
            return self._message(
                MessageType.ANALYSIS if compound else MessageType.UNCERTAINTY,
                text,
                Confidence.HIGH if compound else Confidence.LOW,
                EvidenceStatus.GROUNDED if compound else EvidenceStatus.PARTIAL,
                "A tyre update was supplied.",
                keys,
            )
        if event.event_type == RaceEventType.FASTEST_LAP:
            duration = evidence.get("lap_duration")
            detail = f" at {duration} seconds" if duration is not None else ""
            keys = ["driver_numbers", "event_type"] + (["lap_duration"] if duration else [])
            return self._message(
                MessageType.ANALYSIS,
                f"Driver {driver} set the supplied fastest-lap marker{detail}. One peak lap does "
                "not establish sustainable race pace.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A fastest-lap event was supplied.",
                keys,
            )
        if event.event_type in {RaceEventType.OVERTAKE, RaceEventType.POSITION_CHANGE}:
            position = evidence.get("position")
            update = f" to P{position}" if position is not None else ""
            keys = ["driver_numbers", "event_type"] + (["position"] if position else [])
            return self._message(
                MessageType.ANALYSIS,
                f"Driver {driver} has a confirmed position update{update}. The record confirms "
                "track position; only an explicit overtake event establishes an on-track pass.",
                Confidence.HIGH
                if event.event_type == RaceEventType.OVERTAKE
                else Confidence.MEDIUM,
                EvidenceStatus.GROUNDED,
                "Track position changed.",
                keys,
            )
        if event.event_type in {
            RaceEventType.SAFETY_CAR,
            RaceEventType.VIRTUAL_SAFETY_CAR,
            RaceEventType.RED_FLAG,
            RaceEventType.YELLOW_FLAG,
        }:
            label = event.event_type.value.replace("_", " ").title()
            return self._message(
                MessageType.OBSERVATION,
                f"{label} is confirmed. Its duration and pit-window effect are not known yet.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                f"{label} was recorded.",
                base_keys,
            )
        if event.event_type in {RaceEventType.WEATHER_UPDATE, RaceEventType.WEATHER_CHANGE}:
            return self._message(
                MessageType.UNCERTAINTY,
                "A weather sample arrived, but rainfall data is incomplete. The room cannot "
                "infer changing grip from this sample.",
                Confidence.LOW,
                EvidenceStatus.PARTIAL,
                "The weather sample is incomplete.",
                ["event_type", "data_quality"],
            )
        if event.event_type == RaceEventType.RETIREMENT:
            return self._message(
                MessageType.OBSERVATION,
                f"Driver {driver} is recorded as retired. The supplied event does not establish "
                "a cause.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A retirement was recorded.",
                ["driver_numbers", "event_type"],
            )
        if event.event_type == RaceEventType.SESSION_FINISH:
            return self._message(
                MessageType.SUMMARY,
                "The session is recorded as finished. The room will use only the supplied "
                "classification and will not infer championship consequences.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "The session finished.",
                base_keys,
            )
        return self._message(
            MessageType.UNCERTAINTY,
            f"A {event.event_type.value.replace('_', ' ').lower()} event was recorded. The "
            "available data is insufficient for a stronger conclusion.",
            Confidence.LOW,
            EvidenceStatus.PARTIAL,
            "An event was recorded.",
            base_keys,
        )

    def _reply(
        self, event: NormalizedRaceEvent, agent_id: str, context: GroundingContext
    ) -> GeneratedRoomMessage:
        if agent_id == "theo-voss" and event.event_type == RaceEventType.POSITION_CHANGE:
            return self._message(
                MessageType.DISAGREEMENT,
                "I agree that track position changed, but not that the data proves an on-track "
                "pass. A pit cycle or timing correction remains possible.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "The event is a position update rather than an explicit overtake.",
                ["event_type"],
            )
        if agent_id == "mira-vale" and "pace_trend_seconds" in context.evidence:
            return self._message(
                MessageType.REPLY,
                "Theo's measured trend is supported by the representative laps. Its strategic "
                "relevance remains uncertain without a usable traffic gap or pit-loss estimate.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "Representative laps support the trend but not a strategy call.",
                ["pace_trend_seconds", "representative_laps"],
            )
        if agent_id == "arjun-reyes" and "season_context" in context.evidence:
            return self._message(
                MessageType.REPLY,
                "The supplied context explicitly marks this as a synthetic validation race, so "
                "no championship comparison or points implication is valid.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "No championship points apply to the fixture.",
                ["season_context"],
            )
        return self._message(
            MessageType.REPLY,
            "The observed event is supported. I would keep the interpretation narrow because "
            "the current evidence does not establish the wider trend.",
            Confidence.MEDIUM,
            EvidenceStatus.PARTIAL,
            "The triggering event is supported.",
            ["event_type"],
        )

    @staticmethod
    def _message(
        message_type: MessageType,
        content: str,
        confidence: Confidence,
        evidence_status: EvidenceStatus,
        claim: str,
        evidence_keys: list[str],
    ) -> GeneratedRoomMessage:
        return GeneratedRoomMessage(
            message_type=message_type,
            content=content,
            confidence=confidence,
            evidence_status=evidence_status,
            claims=[GroundedClaim(claim=claim, evidence_keys=evidence_keys)],
        )


class RaceRoomDiscussionEngine:
    def __init__(
        self,
        repository: SqlRaceRoomRepository,
        evaluator: DiscussionTriggerEvaluator,
        publisher: RoomPublisher | None = None,
        state_reader: StateReader | None = None,
    ) -> None:
        self.repository = repository
        self.evaluator = evaluator
        self.publisher = publisher
        self.state_reader = state_reader
        self.generator = DeterministicRoomGenerator()
        self.validator = GroundingValidator()
        self.context_builder = GroundingContextBuilder()
        self.metrics = DiscussionMetrics()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._recent_content: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=50))

    async def consume(self, event: NormalizedRaceEvent) -> None:
        room = await self.repository.get_room_by_session(event.session_key)
        if room is None:
            return
        trigger = self.evaluator.evaluate(event)
        if trigger is None:
            return
        self.metrics.trigger_count += 1
        state = await self.state_reader(event.session_key) if self.state_reader else None
        context = self.context_builder.build(event, state)
        async with self._locks[room.slug]:
            await self._generate_chain(room.id, event, trigger, context)

    def reset_session(self, session_key: str, room_id: str) -> None:
        self.evaluator.reset_session(session_key)
        self._recent_content.pop(room_id, None)

    async def _generate_chain(
        self,
        room_id: Any,
        event: NormalizedRaceEvent,
        trigger: DiscussionTrigger,
        context: GroundingContext,
    ) -> None:
        primary = await self._build_message(
            room_id, event, trigger, trigger.agent_candidates[0], context
        )
        if primary is None:
            return
        primary = await self._store(primary, event, context)
        if primary is None:
            return
        if trigger.needs_reply and len(trigger.agent_candidates) > 1:
            reply = await self._build_message(
                room_id,
                event,
                trigger,
                trigger.agent_candidates[1],
                context,
                reply_to=primary,
            )
            if reply is not None:
                await self._store(reply, event, context)
        if trigger.needs_host_summary and primary.agent_id != "nova":
            summary = await self._build_message(
                room_id,
                event,
                trigger,
                "nova",
                context,
                reply_to=primary,
                host_summary=True,
            )
            if summary is not None:
                await self._store(summary, event, context)

    async def _build_message(
        self,
        room_id: Any,
        event: NormalizedRaceEvent,
        trigger: DiscussionTrigger,
        agent_id: str,
        context: GroundingContext,
        *,
        reply_to: RoomMessage | None = None,
        host_summary: bool = False,
    ) -> RoomMessage | None:
        generated = self.generator.generate(
            event,
            trigger,
            agent_id,
            context,
            reply_to=reply_to,
            host_summary=host_summary,
        )
        fingerprint = " ".join(generated.content.lower().split())
        if fingerprint in self._recent_content[str(room_id)] or not self.validator.validate(
            generated, context
        ):
            self.metrics.rejected_message_count += 1
            return None
        message = RoomMessage(
            room_id=room_id,
            agent_id=agent_id,
            sequence=0,
            lap_number=event.lap_number,
            wall_time=event.event_time,
            topic=trigger.topic,
            message_type=generated.message_type,
            content=generated.content,
            confidence=generated.confidence,
            evidence_status=generated.evidence_status,
            reply_to_message_id=reply_to.id if reply_to else None,
            trigger_event_id=event.id,
            generated_by="deterministic",
            prompt_version="rooms-v2",
        )
        self._recent_content[str(room_id)].append(fingerprint)
        return message

    async def _store(
        self,
        message: RoomMessage,
        event: NormalizedRaceEvent,
        context: GroundingContext,
    ) -> RoomMessage | None:
        stored, inserted = await self.repository.insert_message(
            message, self._evidence(event, message, context)
        )
        if not inserted:
            return None
        self.metrics.generated_message_count += 1
        self.metrics.deterministic_fallback_count += 1
        if self.publisher is not None:
            try:
                await self.publisher(stored)
            except Exception as exc:
                logger.error("Room message publication failed error=%s", type(exc).__name__)
        return stored

    @staticmethod
    def _evidence(
        event: NormalizedRaceEvent,
        message: RoomMessage,
        context: GroundingContext,
    ) -> list[MessageEvidence]:
        entries: list[MessageEvidence] = []
        for key, value in context.evidence.items():
            if value is None:
                continue
            entries.append(
                MessageEvidence(
                    message_id=message.id,
                    evidence_key=key,
                    evidence_type="normalized_event",
                    source_provider=event.source,
                    source_reference=str(event.id),
                    metric_name=key,
                    metric_value=value if isinstance(value, (str, float, int)) else None,
                    context={
                        "event_sequence": event.sequence_number,
                        "lap_number": event.lap_number,
                        "data_quality": context.data_quality,
                        "value": value,
                    },
                )
            )
        return entries
