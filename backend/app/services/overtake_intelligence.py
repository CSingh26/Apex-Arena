# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging

from app.domain.intelligence import (
    OvertakeCandidate,
    OvertakeContext,
    PositionChange,
    PositionChangeCause,
    RaceIntelligenceConfig,
)
from app.domain.models import (
    DerivationEvidence,
    EventConfidence,
    EventDerivation,
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)

logger = logging.getLogger(__name__)
RACE_LIKE_SESSIONS = {"RACE", "SPRINT"}


class OvertakeDetector:
    """Confirm classification passes only after persistence and exclusion checks."""

    def __init__(self, config: RaceIntelligenceConfig | None = None) -> None:
        self.config = config or RaceIntelligenceConfig()
        self._candidates: dict[tuple[str, int, int], OvertakeCandidate] = {}

    @property
    def pending_count(self) -> int:
        return len(self._candidates)

    def pending_for_session(self, session_key: str) -> list[OvertakeCandidate]:
        return [
            candidate.model_copy(deep=True)
            for candidate in self._candidates.values()
            if candidate.session_key == session_key
        ]

    def reset_session(self, session_key: str) -> None:
        for key in [key for key in self._candidates if key[0] == session_key]:
            self._candidates.pop(key, None)

    def apply(
        self,
        change: PositionChange,
        context: OvertakeContext,
    ) -> NormalizedRaceEvent | None:
        pair = self._pair_key(change)
        target = change.related_driver_numbers[0] if change.related_driver_numbers else None
        if (
            target is None
            or change.position_delta <= 0
            or change.cause is not PositionChangeCause.ON_TRACK_CANDIDATE
            or context.session_type.upper() not in RACE_LIKE_SESSIONS
            or not context.both_running
            or context.interval_before is None
            or context.interval_before > self.config.overtake_max_interval_seconds
        ):
            self._candidates.pop(pair, None)
            logger.debug(
                "overtake_candidate result=rejected driver=%s target=%s cause=%s",
                change.driver_number,
                target,
                change.cause.value,
            )
            return None

        candidate = self._candidates.get(pair)
        if candidate is None or candidate.driver_number != change.driver_number:
            self._candidates[pair] = OvertakeCandidate(
                session_key=change.session_key,
                driver_number=change.driver_number,
                target_driver_number=target,
                position_before=change.position_before,
                position_after=change.position_after,
                first_observed_at=context.observed_at,
                last_observed_at=context.observed_at,
                first_sequence=change.source_sequence,
                last_sequence=change.source_sequence,
                interval_before=context.interval_before,
            )
            logger.debug(
                "overtake_candidate result=pending session=%s driver=%s target=%s "
                "position=%s interval=%s",
                change.session_key,
                change.driver_number,
                target,
                change.position_after,
                context.interval_before,
            )
            return None
        if change.source_sequence <= candidate.last_sequence:
            return None

        candidate.samples += 1
        candidate.last_observed_at = context.observed_at
        candidate.last_sequence = change.source_sequence
        elapsed = (candidate.last_observed_at - candidate.first_observed_at).total_seconds()
        if (
            candidate.samples < self.config.overtake_confirmation_samples
            or elapsed < self.config.overtake_confirmation_seconds
        ):
            return None

        self._candidates.pop(pair, None)
        confidence_level = (
            EventConfidence.HIGH if context.pit_data_available else EventConfidence.MEDIUM
        )
        confidence = 0.92 if confidence_level is EventConfidence.HIGH else 0.72
        logger.info(
            "overtake_candidate result=confirmed driver=%s target=%s position=%s evidence=%s",
            change.driver_number,
            target,
            change.position_after,
            "timing,persistence,pit_exclusion",
        )
        return NormalizedRaceEvent(
            session_key=change.session_key,
            source="apexarena",
            event_origin=EventOrigin.DERIVED,
            event_time=context.observed_at,
            received_at=context.observed_at,
            event_type=RaceEventType.OVERTAKE,
            primary_driver_number=change.driver_number,
            secondary_driver_number=target,
            position_before=change.position_before,
            position_after=change.position_after,
            interval_seconds=candidate.interval_before,
            importance=0.82,
            importance_level=EventImportance.IMPORTANT,
            confidence=confidence,
            confidence_level=confidence_level,
            derivation=EventDerivation(
                algorithm="overtake_detector_v1",
                version=1,
                evidence=[
                    DerivationEvidence(
                        kind="confirmed_order_reversal",
                        observed_at=candidate.first_observed_at,
                        value=f"{change.driver_number}>{target}",
                    ),
                    DerivationEvidence(
                        kind="ordering_persistence_seconds",
                        observed_at=context.observed_at,
                        value=elapsed,
                    ),
                    DerivationEvidence(
                        kind="interval_before_seconds",
                        observed_at=candidate.first_observed_at,
                        value=candidate.interval_before,
                    ),
                ],
                exclusions_checked=[
                    "pit_transition",
                    "retirement",
                    "penalty",
                    "timing_correction",
                    "lapped_ordering",
                ],
            ),
            payload={
                "confirmation_samples": candidate.samples,
                "confirmation_seconds": elapsed,
                "pit_context_available": context.pit_data_available,
                "location_context_available": context.location_available,
            },
            dedup_key=(
                f"overtake:{change.session_key}:{change.driver_number}:"
                f"{target}:{candidate.first_sequence}"
            ),
        )

    @staticmethod
    def _pair_key(change: PositionChange) -> tuple[str, int, int]:
        target = change.related_driver_numbers[0] if change.related_driver_numbers else 0
        low, high = sorted((change.driver_number, target))
        return change.session_key, low, high
