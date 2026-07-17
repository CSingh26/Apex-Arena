# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections import defaultdict, deque
from enum import IntEnum
from uuid import UUID

from pydantic import BaseModel

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.domain.rooms import MessageTopic


class TriggerPriority(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30
    CRITICAL = 40


class DiscussionTrigger(BaseModel):
    event_id: UUID
    dedup_key: str
    session_key: str
    event_type: RaceEventType
    topic: MessageTopic
    priority: TriggerPriority
    lap_number: int | None
    agent_candidates: list[str]
    needs_reply: bool = False
    needs_host_summary: bool = False


TRIGGER_RULES: dict[RaceEventType, tuple[MessageTopic, TriggerPriority, list[str]]] = {
    RaceEventType.SESSION_START: (
        MessageTopic.SESSION,
        TriggerPriority.HIGH,
        ["nova", "arjun-reyes"],
    ),
    RaceEventType.RACE_START: (MessageTopic.SESSION, TriggerPriority.HIGH, ["nova", "lena-cross"]),
    RaceEventType.LAP_COMPLETED: (
        MessageTopic.PACE,
        TriggerPriority.LOW,
        ["theo-voss", "mira-vale"],
    ),
    RaceEventType.POSITION_CHANGE: (
        MessageTopic.RACECRAFT,
        TriggerPriority.HIGH,
        ["lena-cross", "theo-voss"],
    ),
    RaceEventType.OVERTAKE: (
        MessageTopic.RACECRAFT,
        TriggerPriority.HIGH,
        ["lena-cross", "theo-voss"],
    ),
    RaceEventType.PIT_STOP: (
        MessageTopic.PIT_STOP,
        TriggerPriority.HIGH,
        ["mira-vale", "theo-voss"],
    ),
    RaceEventType.TYRE_CHANGE: (
        MessageTopic.TYRES,
        TriggerPriority.MEDIUM,
        ["mira-vale", "theo-voss"],
    ),
    RaceEventType.FASTEST_LAP: (
        MessageTopic.PACE,
        TriggerPriority.MEDIUM,
        ["theo-voss", "lena-cross"],
    ),
    RaceEventType.SAFETY_CAR: (
        MessageTopic.INCIDENT,
        TriggerPriority.CRITICAL,
        ["mira-vale", "lena-cross"],
    ),
    RaceEventType.VIRTUAL_SAFETY_CAR: (
        MessageTopic.INCIDENT,
        TriggerPriority.CRITICAL,
        ["mira-vale", "lena-cross"],
    ),
    RaceEventType.RED_FLAG: (
        MessageTopic.RACE_CONTROL,
        TriggerPriority.CRITICAL,
        ["nova", "mira-vale"],
    ),
    RaceEventType.YELLOW_FLAG: (
        MessageTopic.RACE_CONTROL,
        TriggerPriority.HIGH,
        ["lena-cross", "nova"],
    ),
    RaceEventType.PENALTY: (
        MessageTopic.RACE_CONTROL,
        TriggerPriority.HIGH,
        ["lena-cross", "nova"],
    ),
    RaceEventType.RACE_CONTROL: (
        MessageTopic.RACE_CONTROL,
        TriggerPriority.MEDIUM,
        ["nova", "lena-cross"],
    ),
    RaceEventType.WEATHER_CHANGE: (
        MessageTopic.WEATHER,
        TriggerPriority.HIGH,
        ["mira-vale", "theo-voss"],
    ),
    RaceEventType.WEATHER_UPDATE: (
        MessageTopic.WEATHER,
        TriggerPriority.MEDIUM,
        ["theo-voss", "mira-vale"],
    ),
    RaceEventType.RETIREMENT: (MessageTopic.INCIDENT, TriggerPriority.HIGH, ["lena-cross", "nova"]),
    RaceEventType.SESSION_FINISH: (
        MessageTopic.SUMMARY,
        TriggerPriority.CRITICAL,
        ["nova", "arjun-reyes"],
    ),
}


class DiscussionTriggerEvaluator:
    """Event-time trigger selection with bounded dedup, cooldowns, and room throttling."""

    def __init__(
        self,
        topic_cooldown_seconds: int = 20,
        agent_cooldown_seconds: int = 10,
        room_max_triggers_per_minute: int = 12,
        dedup_capacity: int = 5000,
    ) -> None:
        self.topic_cooldown_seconds = topic_cooldown_seconds
        self.agent_cooldown_seconds = agent_cooldown_seconds
        self.room_max_triggers_per_minute = room_max_triggers_per_minute
        self.dedup_capacity = dedup_capacity
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._topic_last_at: dict[tuple[str, MessageTopic], float] = {}
        self._agent_last_at: dict[tuple[str, str], float] = {}
        self._room_triggers: dict[str, deque[float]] = defaultdict(deque)

    def evaluate(self, event: NormalizedRaceEvent) -> DiscussionTrigger | None:
        rule = TRIGGER_RULES.get(event.event_type)
        if rule is None or event.dedup_key in self._seen or not self._is_meaningful(event):
            return None
        topic, priority, configured_candidates = rule
        if (
            event.event_type == RaceEventType.LAP_COMPLETED
            and "pace_trend_seconds" in event.payload
        ):
            priority = TriggerPriority.HIGH
        event_at = event.event_time.timestamp()
        room_window = self._room_triggers[event.session_key]
        while room_window and event_at - room_window[0] >= 60:
            room_window.popleft()
        if (
            priority < TriggerPriority.CRITICAL
            and len(room_window) >= self.room_max_triggers_per_minute
        ):
            return None
        last_topic_at = self._topic_last_at.get((event.session_key, topic), float("-inf"))
        if (
            priority < TriggerPriority.CRITICAL
            and event_at - last_topic_at < self.topic_cooldown_seconds
        ):
            return None
        candidates = [
            agent
            for agent in configured_candidates
            if priority >= TriggerPriority.CRITICAL
            or event_at - self._agent_last_at.get((event.session_key, agent), float("-inf"))
            >= self.agent_cooldown_seconds
        ]
        if not candidates:
            return None
        self._remember(event.dedup_key)
        self._topic_last_at[(event.session_key, topic)] = event_at
        room_window.append(event_at)
        for agent in candidates[:2]:
            self._agent_last_at[(event.session_key, agent)] = event_at
        return DiscussionTrigger(
            event_id=event.id,
            dedup_key=event.dedup_key,
            session_key=event.session_key,
            event_type=event.event_type,
            topic=topic,
            priority=priority,
            lap_number=event.lap_number,
            agent_candidates=candidates,
            needs_reply=priority >= TriggerPriority.HIGH and len(candidates) > 1,
            needs_host_summary=priority >= TriggerPriority.CRITICAL,
        )

    def reset_session(self, session_key: str) -> None:
        self._seen.clear()
        self._seen_order.clear()
        self._topic_last_at = {
            key: value for key, value in self._topic_last_at.items() if key[0] != session_key
        }
        self._agent_last_at = {
            key: value for key, value in self._agent_last_at.items() if key[0] != session_key
        }
        self._room_triggers.pop(session_key, None)

    @staticmethod
    def _is_meaningful(event: NormalizedRaceEvent) -> bool:
        if event.event_type != RaceEventType.LAP_COMPLETED:
            return True
        lap = event.lap_number or 0
        return lap == 1 or lap % 10 == 0 or "pace_trend_seconds" in event.payload

    def _remember(self, dedup_key: str) -> None:
        self._seen.add(dedup_key)
        self._seen_order.append(dedup_key)
        while len(self._seen_order) > self.dedup_capacity:
            self._seen.discard(self._seen_order.popleft())
