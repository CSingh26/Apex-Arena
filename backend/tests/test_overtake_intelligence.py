# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.intelligence import (
    OvertakeContext,
    PositionChange,
    PositionChangeCause,
    RaceIntelligenceConfig,
)
from app.domain.models import EventConfidence, EventOrigin, RaceEventType
from app.services.overtake_intelligence import OvertakeDetector

START = datetime(2026, 7, 19, 13, 42, tzinfo=UTC)


def gain(
    *,
    driver: int = 4,
    target: int = 16,
    cause: PositionChangeCause = PositionChangeCause.ON_TRACK_CANDIDATE,
    second: int = 0,
    sequence: int = 10,
) -> PositionChange:
    return PositionChange(
        session_key="race",
        driver_number=driver,
        related_driver_numbers=[target],
        position_before=5,
        position_after=4,
        cause=cause,
        observed_at=START + timedelta(seconds=second),
        source_sequence=sequence,
        batch_key=f"race:{second}:{driver}-{target}",
    )


def context(
    *,
    second: int,
    interval: float | None = 0.72,
    pit_data_available: bool = True,
    location_available: bool = True,
    both_running: bool = True,
) -> OvertakeContext:
    return OvertakeContext(
        session_type="RACE",
        observed_at=START + timedelta(seconds=second),
        interval_before=interval,
        pit_data_available=pit_data_available,
        location_available=location_available,
        both_running=both_running,
    )


def detector() -> OvertakeDetector:
    return OvertakeDetector(
        RaceIntelligenceConfig(overtake_confirmation_seconds=2, overtake_confirmation_samples=2)
    )


def test_persistent_adjacent_reversal_confirms_high_confidence_overtake() -> None:
    service = detector()

    assert service.apply(gain(), context(second=0)) is None
    event = service.apply(gain(second=2, sequence=11), context(second=2))

    assert event is not None
    assert event.event_type is RaceEventType.OVERTAKE
    assert event.event_origin is EventOrigin.DERIVED
    assert event.primary_driver_number == 4
    assert event.secondary_driver_number == 16
    assert event.confidence_level is EventConfidence.HIGH
    assert event.interval_seconds == 0.72
    assert event.derivation is not None
    assert event.derivation.algorithm == "overtake_detector_v1"


def test_brief_order_reversal_that_changes_direction_never_confirms() -> None:
    service = detector()

    assert service.apply(gain(), context(second=0)) is None
    assert service.apply(gain(driver=16, target=4, second=1), context(second=1)) is None
    assert service.pending_count == 1


def test_pit_retirement_penalty_and_lapped_changes_are_rejected() -> None:
    service = detector()

    for cause in (
        PositionChangeCause.PIT_CYCLE,
        PositionChangeCause.RETIREMENT_INHERITANCE,
        PositionChangeCause.PENALTY_OR_CLASSIFICATION,
        PositionChangeCause.LAPPED_ORDERING,
        PositionChangeCause.TIMING_CORRECTION,
    ):
        assert service.apply(gain(cause=cause), context(second=0)) is None

    assert service.pending_count == 0


def test_missing_location_does_not_block_timing_supported_pass() -> None:
    service = detector()

    service.apply(gain(), context(second=0, location_available=False))
    event = service.apply(
        gain(second=2, sequence=11), context(second=2, location_available=False)
    )

    assert event is not None
    assert event.confidence_level is EventConfidence.HIGH


def test_missing_pit_context_lowers_confidence_but_keeps_strong_pass() -> None:
    service = detector()

    service.apply(gain(), context(second=0, pit_data_available=False))
    event = service.apply(
        gain(second=2, sequence=11), context(second=2, pit_data_available=False)
    )

    assert event is not None
    assert event.confidence_level is EventConfidence.MEDIUM


def test_weak_or_invalid_context_suppresses_overtake() -> None:
    service = detector()

    assert service.apply(gain(), context(second=0, interval=4.2)) is None
    assert service.apply(gain(second=2), context(second=2, interval=4.0)) is None
    assert service.apply(gain(second=4), context(second=4, both_running=False)) is None
    assert service.pending_count == 0
