# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Any


def standard_weekend_sessions() -> list[dict[str, Any]]:
    common = {
        "meeting_key": 1264,
        "meeting_name": "Belgian Grand Prix",
        "year": 2026,
        "country_name": "Belgium",
        "country_code": "BEL",
        "circuit_short_name": "Spa-Francorchamps",
    }
    return [
        {
            **common,
            "session_key": 9838,
            "session_name": "Qualifying",
            "date_start": "2026-07-18T14:00:00Z",
        },
        {
            **common,
            "session_key": 9839,
            "session_name": "Race",
            "date_start": "2026-07-19T13:00:00Z",
        },
    ]


def sprint_weekend_sessions() -> list[dict[str, Any]]:
    common = {
        "meeting_key": 1265,
        "meeting_name": "United States Grand Prix",
        "year": 2026,
        "country_name": "United States",
        "country_code": "USA",
        "circuit_short_name": "Austin",
    }
    return [
        {
            **common,
            "session_key": 9901,
            "session_name": "Sprint Shootout",
            "date_start": "2026-10-23T21:30:00Z",
        },
        {
            **common,
            "session_key": 9902,
            "session_name": "Sprint Race",
            "date_start": "2026-10-24T18:00:00Z",
        },
        {
            **common,
            "session_key": 9903,
            "session_name": "Qualifying",
            "date_start": "2026-10-24T22:00:00Z",
        },
        {
            **common,
            "session_key": 9904,
            "session_name": "Race",
            "date_start": "2026-10-25T20:00:00Z",
        },
    ]


def qualifying_historical_payloads() -> dict[str, list[dict[str, Any]]]:
    return {
        "sessions": [
            {
                "meeting_key": 1264,
                "session_key": 9838,
                "session_name": "Qualifying",
                "date_start": "2026-07-18T14:00:00Z",
            }
        ],
        "drivers": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "full_name": "Oscar Piastri",
                "broadcast_name": "O PIASTRI",
                "name_acronym": "PIA",
                "team_name": "McLaren",
            }
        ],
        "laps": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "lap_number": 7,
                "lap_duration": 101.245,
                "date_start": "2026-07-18T14:22:00Z",
                "qualifying_phase": 1,
            }
        ],
        "position": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "position": 1,
                "date": "2026-07-18T14:22:05Z",
            }
        ],
        "intervals": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "gap_to_leader": 0,
                "date": "2026-07-18T14:22:05Z",
            }
        ],
        "stints": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "stint_number": 1,
                "compound": "SOFT",
            }
        ],
        "pit": [],
        "race_control": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "qualifying_phase": 1,
                "category": "SessionStatus",
                "message": "Q1 STARTED",
                "date": "2026-07-18T14:00:00Z",
            }
        ],
        "weather": [
            {
                "session_key": 9838,
                "track_temperature": 31.2,
                "rainfall": 0,
                "date": "2026-07-18T14:10:00Z",
            }
        ],
        "session_result": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "position": 1,
                "duration": [102.1, 101.7, 101.245],
                "gap_to_leader": [0, 0, 0],
            }
        ],
        "starting_grid": [
            {
                "session_key": 9838,
                "driver_number": 81,
                "position": 1,
            }
        ],
    }
