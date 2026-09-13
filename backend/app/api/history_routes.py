# SPDX-License-Identifier: AGPL-3.0-only
"""Finite, read-only factual detail; compact bootstrap/SSE remain unchanged."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.services.history_details import (
    DetailBusyError,
    DetailNotFoundError,
    DetailSelectionError,
    DetailViewChangedError,
)
from app.services.telemetry_history import TelemetrySelectionError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Factual history"])


def get_services(request: Request):
    return request.app.state.services


async def _request_operation(request, operation):
    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    task = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            return None
        return await task
    finally:
        for pending in (task, watcher):
            if not pending.done() and not pending.cancelling():
                pending.cancel()
        await asyncio.wait({task, watcher}, timeout=0.25)

        def observed(done):
            if not done.cancelled():
                done.exception()

        for pending in (task, watcher):
            pending.add_done_callback(observed)


async def _respond(request, operation):
    try:
        result = await _request_operation(request, operation)
        return Response(status_code=204) if result is None else JSONResponse(result)
    except DetailSelectionError as exc:
        raise HTTPException(
            422, "Select one or two distinct drivers and supported history families"
        ) from exc
    except DetailNotFoundError as exc:
        raise HTTPException(404, "Session or room history not found") from exc
    except DetailViewChangedError as exc:
        raise HTTPException(409, "Room view changed; refresh and retry detail") from exc
    except TimeoutError:
        return JSONResponse({"availability": "unavailable", "reason": "deadline"})
    except Exception as exc:
        if not isinstance(exc, DetailBusyError):
            logger.warning("History detail unavailable error=%s", type(exc).__name__)
        raise HTTPException(
            503,
            "History detail is temporarily unavailable; retry shortly",
            headers={"Retry-After": "1"},
        ) from exc


@router.get("/api/v1/sessions/{session_key}/intelligence-detail")
async def session_history_detail(
    session_key: str,
    request: Request,
    services: Annotated[object, Depends(get_services)],
    driver: Annotated[list[int], Query(min_length=1, max_length=2)],
    family: Annotated[list[str], Query(min_length=1, max_length=5)],
    cursor: Annotated[int | None, Query(ge=0)] = None,
):
    return await _respond(
        request,
        services.history_details.read_session(
            session_key,
            drivers=driver,
            families=family,
            cursor=cursor,
            states=services.race_state,
            progress=services.intelligence_progress,
        ),
    )


@router.get("/api/v1/race-rooms/{room_slug}/intelligence-detail")
async def room_history_detail(
    room_slug: str,
    request: Request,
    services: Annotated[object, Depends(get_services)],
    driver: Annotated[list[int], Query(min_length=1, max_length=2)],
    family: Annotated[list[str], Query(min_length=1, max_length=5)],
):
    return await _respond(
        request,
        services.history_details.read_room(
            room_slug,
            drivers=driver,
            families=family,
            rooms=services.room_repository,
            states=services.race_state,
            progress=services.intelligence_progress,
        ),
    )


@router.get("/api/v1/sessions/{session_key}/telemetry-history")
async def session_telemetry_history(
    session_key: str,
    request: Request,
    services: Annotated[object, Depends(get_services)],
    driver: Annotated[list[int], Query(min_length=1, max_length=2)],
    lap: Annotated[int | None, Query(ge=0)] = None,
    cursor: Annotated[int | None, Query(ge=0)] = None,
):
    """Bounded retained car telemetry for one or two drivers."""
    try:
        window = await _request_operation(
            request,
            services.telemetry_history.read(
                session_key, drivers=driver, lap_number=lap, cursor=cursor
            ),
        )
    except TelemetrySelectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.warning("Telemetry read unavailable error=%s", type(exc).__name__)
        raise HTTPException(
            503,
            "Telemetry is temporarily unavailable; retry shortly",
            headers={"Retry-After": "1"},
        ) from exc
    if window is None:
        return Response(status_code=204)
    return JSONResponse(window.model_dump(mode="json"))
