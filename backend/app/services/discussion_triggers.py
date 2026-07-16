# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import time
from collections import deque
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


TRIGGER_RULES: dict[
    RaceEventType, tuple[MessageTopic, TriggerPriority, list[str]]
] = {
    RaceEventType.SESSION_START: (
        MessageTopic.SESSION,
        TriggerPriority.HIGH,
        ["nova", "arjun-reyes"],
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
        MessageTopic.INCIDENT,
        TriggerPriority.CRITICAL,
        ["nova", "mira-vale"],
    ),
    RaceEventType.YELLOW_FLAG: (
        MessageTopic.INCIDENT,
        TriggerPriority.HIGH,
        ["lena-cross", "nova"],
    ),
    RaceEventType.PENALTY: (
        MessageTopic.INCIDENT,
        TriggerPriority.HIGH,
        ["lena-cross", "nova"],
    ),
    RaceEventType.RACE_CONTROL: (
        MessageTopic.INCIDENT,
        TriggerPriority.MEDIUM,
        ["nova", "lena-cross"],
    ),
    RaceEventType.WEATHER_CHANGE: (
        MessageTopic.STRATEGY,
        TriggerPriority.HIGH,
        ["mira-vale", "theo-voss"],
    ),
    RaceEventType.SESSION_FINISH: (
        MessageTopic.SUMMARY,
        TriggerPriority.CRITICAL,
        ["nova", "arjun-reyes"],
    ),
}


class DiscussionTriggerEvaluator:
    """Select meaningful events while enforcing deduplication and topic cooldowns."""

    def __init__(self, topic_cooldown_seconds: int = 20, dedup_capacity: int = 5000) -> None:
        self.topic_cooldown_seconds = topic_cooldown_seconds
        self.dedup_capacity = dedup_capacity
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._topic_last_at: dict[tuple[str, MessageTopic], float] = {}

    def evaluate(self, event: NormalizedRaceEvent) -> DiscussionTrigger | None:
        rule = TRIGGER_RULES.get(event.event_type)
        if rule is None or event.dedup_key in self._seen:
            return None
        topic, priority, candidates = rule
        now = time.monotonic()
        cooldown_key = (event.session_key, topic)
        last_at = self._topic_last_at.get(cooldown_key, 0)
        if priority < TriggerPriority.CRITICAL and now - last_at < self.topic_cooldown_seconds:
            return None
        self._remember(event.dedup_key)
        self._topic_last_at[cooldown_key] = now
        return DiscussionTrigger(
            event_id=event.id,
            dedup_key=event.dedup_key,
            session_key=event.session_key,
            event_type=event.event_type,
            topic=topic,
            priority=priority,
            lap_number=event.lap_number,
            agent_candidates=candidates,
            needs_reply=priority >= TriggerPriority.HIGH,
            needs_host_summary=priority >= TriggerPriority.CRITICAL,
        )

    def _remember(self, dedup_key: str) -> None:
        self._seen.add(dedup_key)
        self._seen_order.append(dedup_key)
        while len(self._seen_order) > self.dedup_capacity:
            self._seen.discard(self._seen_order.popleft())
