# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RoomMessage,
)
from app.services.discussion_triggers import DiscussionTrigger, DiscussionTriggerEvaluator
from app.services.driver_identity import DriverIdentityResolver
from app.services.race_state import RaceState
from app.services.session_semantics import is_qualifying_session
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
    generation_failure_count: int = 0


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
            if state.session_type is not None:
                evidence["session_type"] = state.session_type
            if state.current_phase is not None:
                evidence["session_phase"] = state.current_phase
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
    TYRE_COMPOUNDS = ("soft", "medium", "hard", "intermediate", "wet")
    INCIDENT_TERMS = ("crash", "collision", "contact", "spun", "spin")
    HISTORICAL_TERMS = ("last season", "last year", "previous race", "championship points")

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
        if context.evidence.get("resolved_driver_name") and mentioned_drivers:
            return False
        compound = str(context.evidence.get("compound") or "").casefold()
        mentioned_compounds = {
            tyre for tyre in self.TYRE_COMPOUNDS if re.search(rf"\b{tyre}\b", lowered)
        }
        if mentioned_compounds and (
            not compound or any(tyre != compound for tyre in mentioned_compounds)
        ):
            return False
        event_type = str(context.evidence.get("event_type") or "")
        if any(term in lowered for term in self.INCIDENT_TERMS) and event_type not in {
            RaceEventType.OVERTAKE.value,
            RaceEventType.RACE_CONTROL.value,
            RaceEventType.RETIREMENT.value,
            RaceEventType.SAFETY_CAR.value,
            RaceEventType.VIRTUAL_SAFETY_CAR.value,
            RaceEventType.RED_FLAG.value,
            RaceEventType.YELLOW_FLAG.value,
        }:
            return False
        if any(term in lowered for term in self.HISTORICAL_TERMS) and not context.evidence.get(
            "season_context"
        ):
            return False
        stated_seconds = [
            float(value) for value in re.findall(r"\b(\d+(?:\.\d+)?) seconds\b", lowered)
        ]
        supplied_numbers = self._numeric_evidence(context.evidence)
        if any(
            not any(abs(stated - abs(supplied)) <= 0.011 for supplied in supplied_numbers)
            for stated in stated_seconds
        ):
            return False
        if context.data_quality == "incomplete" and message.confidence == Confidence.HIGH:
            return False
        return bool(message.claims)

    @classmethod
    def _numeric_evidence(cls, value: Any) -> list[float]:
        if isinstance(value, bool) or value is None:
            return []
        if isinstance(value, (int, float)):
            return [float(value)]
        if isinstance(value, dict):
            return [number for item in value.values() for number in cls._numeric_evidence(item)]
        if isinstance(value, list):
            return [number for item in value for number in cls._numeric_evidence(item)]
        return []


class PublicMessageShaper:
    """Small deterministic guardrail for concise, audience-friendly copy."""

    max_characters = 420

    def shape(self, content: str) -> str:
        compact = " ".join(content.split())
        if len(compact) <= self.max_characters:
            return compact
        shortened = compact[: self.max_characters - 1].rsplit(" ", 1)[0]
        return shortened.rstrip(".,;:") + "…"


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
        driver_number = event.driver_numbers[0] if event.driver_numbers else None
        driver = DriverIdentityResolver.public_label(evidence, driver_number)
        lap = event.lap_number
        qualifying = is_qualifying_session(
            evidence.get("normalized_session_type") or evidence.get("session_type")
        )
        phase = evidence.get("session_phase")
        base_keys = ["event_type", "event_sequence"]
        if host_summary:
            return self._message(
                MessageType.SUMMARY,
                "Room verdict: the event changed the session, but nobody gets to declare a "
                "winner yet. The next timing sample has to prove which argument survives.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "A major race phase changed.",
                base_keys,
            )
        if reply_to is not None:
            return self._reply(event, agent_id, context)
        if event.event_type in {RaceEventType.SESSION_START, RaceEventType.RACE_START}:
            start_detail = (
                "My call: the first valid laps will separate genuine speed from warm-up noise."
                if qualifying
                else "My call: ignore the launch hype until the first gaps and lap times land."
            )
            return self._message(
                MessageType.OBSERVATION,
                f"Lights out. {start_detail}",
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
                    f"{driver} has found {abs(float(trend)):.2f} seconds across the recent "
                    "representative laps. I'm buying this pace shift: it is a real trend until "
                    "traffic or the next sample proves otherwise.",
                    Confidence.HIGH,
                    EvidenceStatus.GROUNDED,
                    "The representative-lap sample contains a pace trend.",
                    ["driver_numbers", "pace_trend_seconds", "representative_laps"],
                )
            duration = evidence.get("lap_duration")
            duration_text = (
                f" in {self._format_seconds(duration)} seconds" if duration is not None else ""
            )
            if qualifying:
                phase_text = f" in {phase}" if phase else ""
                position = evidence.get("position")
                position_text = f" and is P{position}" if position is not None else ""
                return self._message(
                    MessageType.OBSERVATION,
                    f"{driver} clocks{duration_text}{phase_text}{position_text}. My stand: that is "
                    "a serious lap, but calling it safe before the elimination cutoff settles "
                    "is asking for trouble.",
                    Confidence.MEDIUM,
                    EvidenceStatus.PARTIAL,
                    "A qualifying lap was completed.",
                    ["driver_numbers", "event_type"]
                    + (["session_phase"] if phase else [])
                    + (["lap_duration"] if duration is not None else [])
                    + (["position"] if position is not None else []),
                )
            return self._message(
                MessageType.OBSERVATION,
                f"{driver}: lap {lap}{duration_text}. I am not calling that a trend yet—one lap "
                "is a headline, the next representative laps are the argument.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "A lap was completed.",
                ["driver_numbers", "lap_number", "event_type"],
            )
        if event.event_type == RaceEventType.PIT_STOP:
            duration = evidence.get("pit_duration") or evidence.get("duration")
            fact = f"{driver} made a recorded pit stop"
            if lap is not None:
                fact += f" on lap {lap}"
            keys = ["driver_numbers", "event_type"]
            if duration is not None:
                fact += f" with a recorded duration of {self._format_seconds(duration)} seconds"
                keys.append(
                    "pit_duration" if evidence.get("pit_duration") is not None else "duration"
                )
            return self._message(
                MessageType.ANALYSIS,
                fact + ". My call: do not celebrate the strategy yet. The next position update "
                "decides whether that time bought an advantage or just burned track position.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A pit stop was recorded.",
                keys,
            )
        if event.event_type == RaceEventType.TYRE_CHANGE:
            compound = evidence.get("compound")
            text = (
                f"{driver} is on {compound} tyres. I like the intent, but the compound only wins "
                "the argument if the lap times hold through the stint."
                if compound
                else f"{driver} changed tyres, but the compound is missing. No compound, no bold "
                "strategy verdict—I am sitting this prediction out."
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
            detail = f" at {self._format_seconds(duration)} seconds" if duration is not None else ""
            keys = ["driver_numbers", "event_type"] + (["lap_duration"] if duration else [])
            consequence = (
                f"That throws the pressure straight at the elimination line in {phase}."
                if qualifying and phase
                else "I love the lap, but I refuse to confuse one peak with sustainable race pace."
            )
            return self._message(
                MessageType.ANALYSIS,
                f"{driver} set the quickest recorded lap{detail}. {consequence}",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A fastest-lap event was supplied.",
                keys,
            )
        if event.event_type in {RaceEventType.OVERTAKE, RaceEventType.POSITION_CHANGE}:
            position = evidence.get("position")
            previous = evidence.get("previous_position")
            update = (
                f" from P{previous} to P{position}"
                if position is not None and previous is not None
                else f" to P{position}"
                if position is not None
                else ""
            )
            keys = (
                ["driver_numbers", "event_type"]
                + (["position"] if position is not None else [])
                + (["previous_position"] if previous is not None else [])
            )
            if qualifying:
                return self._message(
                    MessageType.ANALYSIS,
                    f"{driver} jumps{update} in the qualifying order. That is a direct hit on the "
                    "cutoff fight—fast on the sheet, even though this is not an on-track pass.",
                    Confidence.MEDIUM,
                    EvidenceStatus.GROUNDED,
                    "The qualifying order changed.",
                    keys,
                )
            racecraft = (
                "That is a clean track-position win, and I am giving the driver credit."
                if event.event_type == RaceEventType.OVERTAKE
                else "The gain is real; I will not call it an overtake without an explicit "
                "pass event."
            )
            return self._message(
                MessageType.ANALYSIS,
                f"{driver} moves{update}. {racecraft}",
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
                f"{label}. This is the strategy reset everyone wanted to claim in advance. My "
                "stand: the pit window is now the argument, but its winner is not known yet.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                f"{label} was recorded.",
                base_keys,
            )
        if event.event_type in {RaceEventType.WEATHER_UPDATE, RaceEventType.WEATHER_CHANGE}:
            return self._message(
                MessageType.UNCERTAINTY,
                "No complete rainfall number, no wet-track prophecy. I am calling out the hype: "
                "this weather sample does not prove the grip has changed.",
                Confidence.LOW,
                EvidenceStatus.PARTIAL,
                "The weather sample is incomplete.",
                ["event_type", "data_quality"],
            )
        if event.event_type == RaceEventType.RETIREMENT:
            return self._message(
                MessageType.OBSERVATION,
                f"{driver} is recorded as retired. The supplied event does not establish a cause.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A retirement was recorded.",
                ["driver_numbers", "event_type"],
            )
        if event.event_type == RaceEventType.QUALIFYING_PHASE:
            phase_text = str(phase or "the next qualifying phase")
            return self._message(
                MessageType.SUMMARY,
                f"{phase_text} is now under way. Drivers need a valid lap before the phase ends; "
                "the slowest will not progress.",
                Confidence.HIGH if phase else Confidence.MEDIUM,
                EvidenceStatus.GROUNDED if phase else EvidenceStatus.PARTIAL,
                "The qualifying phase changed.",
                base_keys + (["session_phase"] if phase else []),
            )
        if event.event_type == RaceEventType.LAP_DELETED:
            return self._message(
                MessageType.OBSERVATION,
                f"{driver}'s lap time has been deleted. That lap no longer helps the driver's "
                "qualifying position, so another valid attempt may be needed.",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A lap time was deleted.",
                ["driver_numbers", "event_type"],
            )
        if event.event_type == RaceEventType.SESSION_RESULT:
            position = evidence.get("position")
            result_text = f" finished P{position}" if position is not None else " has a result"
            consequence = (
                "This sets the starting order for the next competitive session."
                if qualifying
                else "This is the recorded final classification."
            )
            return self._message(
                MessageType.SUMMARY,
                f"{driver}{result_text}. {consequence}",
                Confidence.HIGH,
                EvidenceStatus.GROUNDED,
                "A final session result was supplied.",
                ["driver_numbers", "event_type"] + (["position"] if position is not None else []),
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
            "The feed moved, but the stat line did not give us enough to take an honest side. "
            "No empty hot take—bring the next timing sample.",
            Confidence.LOW,
            EvidenceStatus.PARTIAL,
            "An event was recorded.",
            base_keys,
        )

    def _reply(
        self, event: NormalizedRaceEvent, agent_id: str, context: GroundingContext
    ) -> GeneratedRoomMessage:
        evidence = context.evidence
        driver_number = event.driver_numbers[0] if event.driver_numbers else None
        driver = DriverIdentityResolver.public_label(evidence, driver_number)
        qualifying = is_qualifying_session(
            evidence.get("normalized_session_type") or evidence.get("session_type")
        )
        if qualifying:
            lap_duration = evidence.get("lap_duration")
            lap_stat = (
                f"{driver}'s {self._format_seconds(lap_duration)}-second lap"
                if lap_duration is not None
                else f"{driver}'s timing update"
            )
            if agent_id == "lena-cross":
                return self._message(
                    MessageType.DISAGREEMENT,
                    f"{lap_stat}. Quick lap, yes. Safe? Not yet. The order can still move, and "
                    "only the "
                    "remaining valid runs will settle the elimination fight.",
                    Confidence.MEDIUM,
                    EvidenceStatus.PARTIAL,
                    "The timing update does not settle the qualifying order.",
                    ["event_type"] + (["lap_duration"] if lap_duration is not None else []),
                )
            if agent_id == "theo-voss":
                return self._message(
                    MessageType.QUESTION,
                    f"{lap_stat} is real; the verdict is not. Does the next valid lap confirm "
                    "this pace, or expose it as a single-run peak?",
                    Confidence.MEDIUM,
                    EvidenceStatus.PARTIAL,
                    "More valid timing is required to confirm the pace.",
                    ["event_type"] + (["lap_duration"] if lap_duration is not None else []),
                )
            return self._message(
                MessageType.REPLY,
                "That lap changes the argument, not the final answer. The remaining runs decide "
                "whether this is breathing room or the start of real elimination pressure.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "Qualifying timing supports a narrow conclusion.",
                ["event_type"],
            )
        if agent_id == "theo-voss" and event.event_type == RaceEventType.POSITION_CHANGE:
            previous = evidence.get("previous_position")
            position = evidence.get("position")
            move = (
                f"P{previous} to P{position}"
                if previous is not None and position is not None
                else "the position change"
            )
            return self._message(
                MessageType.DISAGREEMENT,
                f"{move} is in the data, but I reject the on-track-pass claim. "
                "A pit cycle or timing correction remains possible.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "The event is a position update rather than an explicit overtake.",
                ["event_type"]
                + (["previous_position"] if previous is not None else [])
                + (["position"] if position is not None else []),
            )
        if agent_id == "mira-vale" and "pace_trend_seconds" in evidence:
            trend = abs(float(evidence["pace_trend_seconds"]))
            return self._message(
                MessageType.DISAGREEMENT,
                f"I see the {trend:.2f}-second pace gain, but calling it a strategy advantage is "
                "premature. "
                "Without a usable traffic gap or pit-loss estimate, speed can still become a trap.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "Representative laps support the trend but not a strategy call.",
                ["pace_trend_seconds", "representative_laps"],
            )
        if agent_id == "lena-cross" and event.event_type in {
            RaceEventType.SAFETY_CAR,
            RaceEventType.VIRTUAL_SAFETY_CAR,
        }:
            return self._message(
                MessageType.DISAGREEMENT,
                "Do not call this a free stop for everyone. The neutralisation is confirmed; "
                "who actually benefits still depends on track position and the pit cycle.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "Neutralisation alone does not establish a strategic winner.",
                ["event_type"],
            )
        if agent_id == "theo-voss" and event.event_type == RaceEventType.PIT_STOP:
            duration = evidence.get("pit_duration") or evidence.get("duration")
            duration_text = (
                f"The recorded {self._format_seconds(duration)} seconds"
                if duration is not None
                else "The recorded stop"
            )
            return self._message(
                MessageType.CORRECTION,
                f"{duration_text} is not a strategy verdict. The timing record confirms "
                "the service; the next position sample decides whether the call worked.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "Pit service timing does not establish the strategic outcome.",
                ["event_type"]
                + (["pit_duration"] if evidence.get("pit_duration") is not None else [])
                + (
                    ["duration"]
                    if evidence.get("pit_duration") is None and duration is not None
                    else []
                ),
            )
        if agent_id == "lena-cross" and event.event_type == RaceEventType.FASTEST_LAP:
            return self._message(
                MessageType.DISAGREEMENT,
                "Fastest on one lap is a headline, not a race verdict. Show me that pace again "
                "under traffic or tyre stress before we crown the stronger car.",
                Confidence.MEDIUM,
                EvidenceStatus.PARTIAL,
                "One fastest lap does not prove sustainable race pace.",
                ["event_type"],
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
            MessageType.QUESTION,
            "The event is real, but the easy conclusion is still on trial. What does the next "
            "timing sample confirm—and what does it overturn?",
            Confidence.MEDIUM,
            EvidenceStatus.PARTIAL,
            "The triggering event is supported.",
            ["event_type"],
        )

    @staticmethod
    def _format_seconds(value: object) -> str:
        try:
            formatted = f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
        return formatted.rstrip("0").rstrip(".")

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
        generation_version: str = "rooms-v4-stat-debate",
    ) -> None:
        self.repository = repository
        self.evaluator = evaluator
        self.publisher = publisher
        self.state_reader = state_reader
        self.generator = DeterministicRoomGenerator()
        self.validator = GroundingValidator()
        self.context_builder = GroundingContextBuilder()
        self.message_shaper = PublicMessageShaper()
        self.metrics = DiscussionMetrics()
        self.generation_version = generation_version
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._recent_content: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=50))

    async def consume(self, event: NormalizedRaceEvent) -> None:
        try:
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.metrics.generation_failure_count += 1
            logger.error(
                "Room discussion generation failed event_type=%s error=%s",
                event.event_type.value,
                type(exc).__name__,
            )

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
        generated = generated.model_copy(
            update={"content": self.message_shaper.shape(generated.content)}
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
            topic=(
                MessageTopic.STRATEGY
                if agent_id == "mira-vale"
                and event.event_type == RaceEventType.LAP_COMPLETED
                and "pace_trend_seconds" in context.evidence
                and not is_qualifying_session(
                    context.evidence.get("normalized_session_type")
                    or context.evidence.get("session_type")
                )
                else trigger.topic
            ),
            message_type=generated.message_type,
            content=generated.content,
            confidence=generated.confidence,
            evidence_status=generated.evidence_status,
            reply_to_message_id=reply_to.id if reply_to else None,
            trigger_event_id=event.id,
            generated_by="deterministic",
            prompt_version=self.generation_version,
            generation_key=self._generation_key(
                room_id=room_id,
                event=event,
                agent_id=agent_id,
                message_type=generated.message_type.value,
                role="host" if host_summary else ("reply" if reply_to else "primary"),
                generation_version=self.generation_version,
            ),
            generation_version=self.generation_version,
            source_provider=event.source,
            source_reference=str(event.id),
            generation_metadata={
                "event_sequence": event.sequence_number,
                "event_type": event.event_type.value,
                "dedup_key": event.dedup_key,
                "trigger_priority": trigger.priority.value,
                "role": "host" if host_summary else ("reply" if reply_to else "primary"),
            },
        )
        self._recent_content[str(room_id)].append(fingerprint)
        return message

    @staticmethod
    def _generation_key(
        *,
        room_id: Any,
        event: NormalizedRaceEvent,
        agent_id: str,
        message_type: str,
        role: str,
        generation_version: str,
    ) -> str:
        material = "|".join(
            [
                str(room_id),
                str(event.id),
                event.dedup_key,
                agent_id,
                message_type,
                role,
                generation_version,
            ]
        )
        return sha256(material.encode("utf-8")).hexdigest()

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
