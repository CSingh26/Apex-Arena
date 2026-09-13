# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.models import (
    DerivationEvidence,
    EventConfidence,
    EventDerivation,
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.storage.models import NormalizedRaceEventRecord


def derived_overtake(**overrides: object) -> NormalizedRaceEvent:
    now = datetime(2026, 7, 19, 13, 42, tzinfo=UTC)
    values: dict[str, object] = {
        "session_key": "11334",
        "source": "apexarena",
        "event_origin": EventOrigin.DERIVED,
        "event_time": now,
        "received_at": now,
        "event_type": RaceEventType.OVERTAKE,
        "primary_driver_number": 4,
        "secondary_driver_number": 16,
        "position_before": 5,
        "position_after": 4,
        "interval_seconds": 0.72,
        "importance": 0.8,
        "importance_level": EventImportance.IMPORTANT,
        "confidence": 0.9,
        "confidence_level": EventConfidence.HIGH,
        "derivation": EventDerivation(
            algorithm="overtake_detector_v1",
            version=1,
            evidence=[
                DerivationEvidence(
                    kind="confirmed_order_reversal",
                    observed_at=now,
                    value="4>16",
                )
            ],
            exclusions_checked=["pit", "retirement", "penalty"],
        ),
        "dedup_key": "overtake:11334:4:16:38",
    }
    values.update(overrides)
    return NormalizedRaceEvent(**values)  # type: ignore[arg-type]


def test_derived_event_exposes_controlled_intelligence_metadata() -> None:
    event = derived_overtake()

    assert event.driver_numbers == [4, 16]
    assert event.event_origin is EventOrigin.DERIVED
    assert event.importance_level is EventImportance.IMPORTANT
    assert event.confidence_level is EventConfidence.HIGH
    assert event.derivation is not None
    assert event.derivation.algorithm == "overtake_detector_v1"
    assert event.derivation.evidence[0].kind == "confirmed_order_reversal"


def test_source_fact_defaults_to_source_origin_and_has_no_derivation() -> None:
    now = datetime(2026, 7, 19, 13, tzinfo=UTC)
    event = NormalizedRaceEvent(
        session_key="11334",
        source="openf1",
        event_time=now,
        received_at=now,
        event_type=RaceEventType.POSITION_SAMPLE,
        driver_numbers=[4],
        payload={"position": 5},
        dedup_key="position:11334:4:5",
    )

    assert event.event_origin is EventOrigin.SOURCE_FACT
    assert event.derivation is None


def test_storage_record_round_trips_intelligence_metadata() -> None:
    event = derived_overtake()
    values = event.model_dump()
    values["event_type"] = event.event_type.value
    values["event_origin"] = event.event_origin.value
    values["importance_level"] = event.importance_level.value
    values["confidence_level"] = event.confidence_level.value
    values["derivation"] = event.derivation.model_dump(mode="json") if event.derivation else None

    record = NormalizedRaceEventRecord(**values)
    restored = NormalizedRaceEvent.model_validate(record, from_attributes=True)

    assert restored.primary_driver_number == 4
    assert restored.secondary_driver_number == 16
    assert restored.derivation is not None
    assert restored.derivation.exclusions_checked == ["pit", "retirement", "penalty"]


@pytest.mark.parametrize("field", ["importance", "confidence"])
def test_event_scores_remain_normalized(field: str) -> None:
    with pytest.raises(ValidationError):
        derived_overtake(**{field: 1.01})
