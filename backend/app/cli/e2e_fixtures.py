# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic CI inputs, consumed only by explicitly guarded E2E commands.

These are invented test timings, not observations from a real race/provider.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from app.domain.rooms import (
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SourceAvailability,
)
from app.services.raw_events import RawEventInput

FIXTURE_VERSION = "apex-e2e-normal-race-v1"
SESSION_KEY = "e2e-normal-race-v1"
RACE_START = datetime(2026, 1, 4, 12, tzinfo=UTC)
RACE_NAME = "Synthetic CI Normal Grand Prix"


def fixture_room(slug: str) -> RaceRoom:
    return RaceRoom(
        id=uuid5(NAMESPACE_URL, FIXTURE_VERSION),
        slug=slug,
        event_slug="2026-synthetic-ci-normal-grand-prix",
        meeting_key="e2e-meeting-1",
        session_key=SESSION_KEY,
        season=2026,
        round_number=1,
        race_name=RACE_NAME,
        official_name=RACE_NAME,
        circuit_name="Circuit de Spa-Francorchamps",
        country="Belgium",
        country_code="BEL",
        scheduled_start=RACE_START,
        actual_start=RACE_START,
        weekend_start=RACE_START - timedelta(days=2),
        weekend_end=RACE_START + timedelta(hours=4),
        status=RoomStatus.INGESTING,
        mode=RoomMode.REPLAY,
        ingestion_status=IngestionStatus.NORMALIZING,
        eligibility_status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
        source_availability=SourceAvailability.TIMING_ONLY,
        total_laps=10,
        generation_version=FIXTURE_VERSION,
    )


def normal_race_inputs() -> list[RawEventInput]:
    events: list[RawEventInput] = []

    def add(endpoint: str, second: float, **payload):
        timestamp = RACE_START + timedelta(seconds=second)
        events.append(
            RawEventInput(
                provider="apex_e2e_synthetic",
                provider_endpoint=endpoint,
                session_key=SESSION_KEY,
                provider_event_id=f"{FIXTURE_VERSION}:{len(events)}",
                event_time=timestamp,
                received_at=timestamp,
                is_replay=True,
                raw_payload={
                    "session_key": SESSION_KEY,
                    "date": timestamp.isoformat(),
                    "normalized_session_type": "RACE",
                    "synthetic_fixture": FIXTURE_VERSION,
                    **payload,
                },
            )
        )

    add("sessions", 0, session_name="Race", meeting_name=RACE_NAME)
    for position, driver in enumerate((16, 4, 63), 1):
        add(
            "drivers",
            position,
            driver_number=driver,
            full_name=f"Synthetic Driver {driver}",
            name_acronym=f"C{driver}",
            team_name="Synthetic CI Team",
        )
        add("position", 5 + position, driver_number=driver, position=position)
        add(
            "stints",
            10 + position,
            driver_number=driver,
            stint_number=1,
            compound="MEDIUM",
            tyre_age_at_start=0,
        )
    for lap in range(1, 11):
        for offset, driver in enumerate((16, 4, 63)):
            add(
                "laps",
                lap * 90 + offset,
                driver_number=driver,
                lap_number=lap,
                lap_duration=90 + offset * 0.2,
            )
    add("pit", 200, driver_number=63, lap_number=2, pit_duration=23.1, lane_duration=23.1)
    add(
        "race_control",
        280,
        lap_number=3,
        category="Flag",
        flag="YELLOW",
        message="SYNTHETIC CI YELLOW FLAG AT TURN 1",
    )
    for sample, gap in enumerate((1.6, 1.4, 1.2, 0.9, 0.8)):
        add(
            "intervals",
            370 + sample * 10,
            driver_number=4,
            lap_number=4,
            interval=gap,
            gap_to_leader=gap,
        )
    add(
        "race_control",
        470,
        lap_number=5,
        category="Flag",
        flag="GREEN",
        message="SYNTHETIC CI TRACK CLEAR",
    )
    add(
        "race_control",
        990,
        lap_number=10,
        category="SessionStatus",
        message="SYNTHETIC CI SESSION FINISHED",
    )
    return sorted(events, key=lambda item: item.event_time)


def calendar_payload(now: datetime) -> dict:
    def race(round_number, name, start, sprint=False):
        def when(hours):
            at = start + timedelta(hours=hours)
            return {"date": at.date().isoformat(), "time": at.strftime("%H:%M:%SZ")}

        payload = {
            "season": "2026",
            "round": str(round_number),
            "raceName": name,
            **when(0),
            "Circuit": {
                "circuitId": "spa",
                "circuitName": "Circuit de Spa-Francorchamps",
                "Location": {"locality": "Synthetic CI venue", "country": "Belgium"},
            },
            "FirstPractice": when(-48),
            "Qualifying": when(-20),
        }
        if sprint:
            payload.update(SprintQualifying=when(-44), Sprint=when(-24))
        else:
            payload.update(SecondPractice=when(-44), ThirdPractice=when(-24))
        return payload

    return {
        "MRData": {
            "RaceTable": {
                "Races": [
                    race(1, RACE_NAME, RACE_START),
                    race(2, "Synthetic CI Sprint Grand Prix", RACE_START + timedelta(days=7), True),
                    race(99, "Synthetic CI Upcoming Grand Prix", now + timedelta(days=30)),
                ]
            }
        }
    }


def provider_sessions() -> list[dict]:
    return [
        {
            "session_key": SESSION_KEY,
            "meeting_key": "e2e-meeting-1",
            "year": 2026,
            "session_name": "Race",
            "session_type": "Race",
            "meeting_name": RACE_NAME,
            "country_name": "Belgium",
            "country_code": "BEL",
            "circuit_short_name": "Circuit de Spa-Francorchamps",
            "date_start": RACE_START.isoformat(),
            "date_end": (RACE_START + timedelta(hours=4)).isoformat(),
        }
    ]
