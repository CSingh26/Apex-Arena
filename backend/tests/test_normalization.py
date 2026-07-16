# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.models import RaceEventType
from app.services.normalization import OpenF1EventNormalizer
from app.services.raw_events import RawEventInput


@pytest.mark.parametrize(
    ("endpoint", "payload", "expected"),
    [
        ("sessions", {"date_start": "2026-07-19T13:00:00Z"}, RaceEventType.SESSION_START),
        ("drivers", {"driver_number": 4}, RaceEventType.DRIVER_UPDATE),
        ("position", {"driver_number": 4, "position": 2}, RaceEventType.POSITION_SAMPLE),
        ("intervals", {"driver_number": 4, "interval": 1.2}, RaceEventType.INTERVAL_SAMPLE),
        ("laps", {"driver_number": 4, "lap_number": 12}, RaceEventType.LAP_COMPLETED),
        ("pit", {"driver_number": 4, "lap_number": 13}, RaceEventType.PIT_STOP),
        ("stints", {"driver_number": 4, "compound": "MEDIUM"}, RaceEventType.STINT_UPDATE),
        ("race_control", {"message": "SAFETY CAR DEPLOYED"}, RaceEventType.SAFETY_CAR),
        ("weather", {"rainfall": 0}, RaceEventType.WEATHER_UPDATE),
    ],
)
def test_supported_openf1_categories_are_normalized(
    endpoint: str,
    payload: dict[str, object],
    expected: RaceEventType,
) -> None:
    normalizer = OpenF1EventNormalizer()

    event = normalizer.normalize(
        RawEventInput(
            provider_endpoint=endpoint,
            session_key="9999",
            raw_payload=payload,
            received_at=datetime(2026, 7, 19, 13, tzinfo=UTC),
        ),
        uuid4(),
    )

    assert event.event_type == expected
    assert event.session_key == "9999"
    assert event.sequence_number == 0


def test_normalizer_preserves_driver_lap_and_replay_source() -> None:
    raw_id = uuid4()
    normalizer = OpenF1EventNormalizer()
    raw = RawEventInput(
        provider_endpoint="v1/laps",
        raw_payload={
            "session_key": 9999,
            "driver_number": 81,
            "lap_number": 24,
            "date_start": "2026-07-19T14:02:03Z",
        },
        is_replay=True,
    )

    event = normalizer.normalize(raw, raw_id)

    assert event.raw_event_id == raw_id
    assert event.driver_numbers == [81]
    assert event.lap_number == 24
    assert event.source == "openf1_historical"
    assert event.is_replay is True
    assert event.event_time == datetime(2026, 7, 19, 14, 2, 3, tzinfo=UTC)


def test_normalized_dedup_key_is_deterministic() -> None:
    normalizer = OpenF1EventNormalizer()
    raw = RawEventInput(
        provider_endpoint="weather",
        session_key="9999",
        raw_payload={"track_temperature": 31.2},
        event_time=datetime(2026, 7, 19, 13, tzinfo=UTC),
    )

    first = normalizer.normalize(raw, uuid4())
    second = normalizer.normalize(raw, uuid4())

    assert first.dedup_key == second.dedup_key


def test_race_control_flags_are_specialized() -> None:
    normalizer = OpenF1EventNormalizer()
    red = normalizer.normalize(
        RawEventInput(provider_endpoint="race_control", raw_payload={"flag": "RED"}), uuid4()
    )
    investigation = normalizer.normalize(
        RawEventInput(
            provider_endpoint="race_control",
            raw_payload={"message": "CAR 4 UNDER INVESTIGATION"},
        ),
        uuid4(),
    )

    assert red.event_type == RaceEventType.RED_FLAG
    assert investigation.event_type == RaceEventType.INVESTIGATION
