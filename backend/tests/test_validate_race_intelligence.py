# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.cli.validate_race_intelligence import render_json
from app.domain.intelligence import BattleState, RaceIntelligenceConfig
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.services.intelligence_rebuild import IntelligenceRebuildService

START = datetime(2026, 7, 19, 13, tzinfo=UTC)


def source(
    session_key: str,
    event_type: RaceEventType,
    driver: int,
    sequence: int,
    **payload: object,
) -> NormalizedRaceEvent:
    observed_at = START + timedelta(seconds=sequence)
    return NormalizedRaceEvent(
        id=uuid4(),
        session_key=session_key,
        source="openf1",
        event_origin=EventOrigin.SOURCE_FACT,
        event_time=observed_at,
        received_at=observed_at,
        sequence_number=sequence,
        event_type=event_type,
        driver_numbers=[driver],
        interval_seconds=(
            float(payload["interval"]) if payload.get("interval") is not None else None
        ),
        payload={"driver_number": driver, **payload},
        dedup_key=f"source:{session_key}:{sequence}",
    )


def race_stream() -> list[NormalizedRaceEvent]:
    rows = [
        source(
            "race", RaceEventType.POSITION_SAMPLE, 16, 1, position=4, normalized_session_type="RACE"
        ),
        source(
            "race", RaceEventType.POSITION_SAMPLE, 4, 2, position=5, normalized_session_type="RACE"
        ),
        source(
            "race", RaceEventType.POSITION_SAMPLE, 63, 3, position=6, normalized_session_type="RACE"
        ),
        source(
            "race", RaceEventType.POSITION_SAMPLE, 81, 4, position=7, normalized_session_type="RACE"
        ),
        source(
            "race",
            RaceEventType.INTERVAL_SAMPLE,
            4,
            5,
            interval=1.8,
            normalized_session_type="RACE",
        ),
        source(
            "race",
            RaceEventType.INTERVAL_SAMPLE,
            4,
            6,
            interval=1.7,
            normalized_session_type="RACE",
        ),
        source(
            "race",
            RaceEventType.INTERVAL_SAMPLE,
            4,
            7,
            interval=1.6,
            normalized_session_type="RACE",
        ),
        source(
            "race", RaceEventType.POSITION_SAMPLE, 4, 8, position=4, normalized_session_type="RACE"
        ),
        source(
            "race", RaceEventType.POSITION_SAMPLE, 16, 9, position=5, normalized_session_type="RACE"
        ),
        source(
            "race",
            RaceEventType.POSITION_SAMPLE,
            4,
            11,
            position=4,
            normalized_session_type="RACE",
        ),
        source(
            "race",
            RaceEventType.POSITION_SAMPLE,
            81,
            12,
            position=6,
            normalized_session_type="RACE",
        ),
        source(
            "race",
            RaceEventType.POSITION_SAMPLE,
            63,
            13,
            position=7,
            in_pit=True,
            normalized_session_type="RACE",
        ),
    ]
    return rows


def qualifying_stream() -> list[NormalizedRaceEvent]:
    return [
        *[
            source(
                "qualifying",
                RaceEventType.POSITION_SAMPLE,
                driver,
                driver,
                position=driver,
                normalized_session_type="QUALIFYING",
                session_phase="Q1",
            )
            for driver in range(1, 21)
        ],
        source(
            "qualifying",
            RaceEventType.POSITION_SAMPLE,
            16,
            21,
            position=15,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
            time_remaining_seconds=90,
        ),
        source(
            "qualifying",
            RaceEventType.POSITION_SAMPLE,
            15,
            22,
            position=16,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
            time_remaining_seconds=89,
        ),
    ]


class Events:
    def __init__(self, rows: list[NormalizedRaceEvent]) -> None:
        self.rows = rows

    async def list_for_session(
        self,
        session_key: str,
        after_sequence: int = 0,
        limit: int = 100,
        **_: object,
    ) -> list[NormalizedRaceEvent]:
        return [
            row
            for row in self.rows
            if row.session_key == session_key and row.sequence_number > after_sequence
        ][:limit]

    async def replace_derived_for_session(
        self, session_key: str, events: list[NormalizedRaceEvent]
    ) -> list[NormalizedRaceEvent]:
        raise AssertionError("validation is read-only")


class Battles:
    async def replace_for_session(self, session_key: str, battles: list[BattleState]) -> None:
        raise AssertionError("validation is read-only")


def timer() -> object:
    values = iter((10.0, 10.25))
    return lambda: next(values)


async def validate(rows: list[NormalizedRaceEvent], session_key: str) -> str:
    summary = await IntelligenceRebuildService(
        Events(rows),  # type: ignore[arg-type]
        Battles(),
        config=RaceIntelligenceConfig(
            battle_start_samples=3,
            overtake_confirmation_seconds=2,
            overtake_confirmation_samples=2,
        ),
        timer=timer(),  # type: ignore[arg-type]
    ).run(session_key, dry_run=True, replace_derived=False)
    return render_json(summary)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "session_key"),
    [(race_stream(), "race"), (qualifying_stream(), "qualifying")],
)
async def test_validation_json_is_stable_for_deterministic_streams(
    rows: list[NormalizedRaceEvent], session_key: str
) -> None:
    first = await validate(rows, session_key)
    second = await validate(rows, session_key)

    assert first == second
    assert '"source_event_count"' in first
    assert '"bounded_state_maxima"' in first


@pytest.mark.asyncio
async def test_validation_reports_overtake_and_exclusion_metrics() -> None:
    payload = await validate(race_stream(), "race")

    assert '"overtake_confirmations":1' in payload
    assert '"pit_exclusions":1' in payload
    assert '"PIT_CYCLE":2' in payload
