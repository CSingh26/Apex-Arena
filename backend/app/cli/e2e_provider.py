# SPDX-License-Identifier: AGPL-3.0-only
"""Opt-in, offline synthetic metadata server for the isolated browser-test stack."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, HTTPException

from app.cli.e2e_fixtures import SESSION_KEY, calendar_payload, provider_sessions


def create_fixture_app(*, environment: str, enabled: bool, now: datetime | None = None) -> FastAPI:
    if environment not in {"local", "test"} or not enabled:
        raise RuntimeError("Synthetic provider requires explicit opt-in in local/test environments")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    calendar = calendar_payload(now or datetime.now(UTC))

    @app.get("/health")
    def health():
        return {"fixture": "synthetic-e2e"}

    @app.get("/jolpica/2026.json")
    def races():
        return calendar

    @app.get("/openf1/sessions")
    def sessions(year: int):
        if year != 2026:
            raise HTTPException(404, "No synthetic fixture for this year")
        return provider_sessions()

    @app.get("/openf1/weather")
    def weather(session_key: str):
        if session_key != SESSION_KEY:
            raise HTTPException(404, "No synthetic fixture for this session")
        return []

    # Next navigation prefetches standings even while browsing race rooms.
    # Supply only that consumer's exact latest-snapshot contract, not a
    # catch-all OpenF1 fallback or a normal ingestion endpoint.
    def require_latest(session_key: str) -> None:
        if session_key != "latest":
            raise HTTPException(404, "Only the synthetic latest snapshot is available")

    @app.get("/openf1/championship_drivers")
    def championship_drivers(session_key: str):
        require_latest(session_key)
        return [
            {"driver_number": number, "position_current": position, "points_current": points}
            for position, (number, points) in enumerate(((16, 25), (4, 18), (63, 15)), 1)
        ]

    @app.get("/openf1/championship_teams")
    def championship_teams(session_key: str):
        require_latest(session_key)
        return [{"team_name": "Synthetic CI Team", "position_current": 1, "points_current": 58}]

    @app.get("/openf1/drivers")
    def drivers(session_key: str):
        require_latest(session_key)
        return [
            {
                "driver_number": number,
                "full_name": f"Synthetic Driver {number}",
                "name_acronym": f"C{number}",
                "team_name": "Synthetic CI Team",
            }
            for number in (16, 4, 63)
        ]

    return app


def main() -> None:
    app = create_fixture_app(
        environment=os.environ.get("APP_ENV", ""),
        enabled=os.environ.get("APEX_E2E_FIXTURES") == "1",
    )
    uvicorn.run(app, host="0.0.0.0", port=9000)


if __name__ == "__main__":
    main()
