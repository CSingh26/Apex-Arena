# SPDX-License-Identifier: AGPL-3.0-only
"""Read bounded historical car telemetry for one or two drivers.

Live rooms keep only the latest sample per driver, which is enough for a gauge
and useless for a trace. This reads the retained CAR_DATA_SAMPLE facts instead,
under explicit ceilings, and reports honestly when a session or a driver simply
has no telemetry rather than implying an empty chart means a quiet car.
"""

from __future__ import annotations

import logging
import math

from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.domain.telemetry import (
    CHANNEL_BOUNDS,
    CHANNEL_UNITS,
    MAX_COMPARISON_DRIVERS,
    MAX_SAMPLES_PER_DRIVER,
    MAX_SCANNED_EVENTS,
    DriverTelemetry,
    TelemetrySample,
    TelemetryWindow,
)
from app.services.race_state import RaceStateEngine

logger = logging.getLogger(__name__)


class TelemetrySelectionError(ValueError):
    """The requested driver or lap selection is outside supported bounds."""


class TelemetryHistoryService:
    """Bounded reads of retained car telemetry."""

    def __init__(self, events, states: RaceStateEngine | None = None) -> None:
        self.events = events
        self.states = states

    @staticmethod
    def _sample(event: NormalizedRaceEvent) -> TelemetrySample | None:
        """Project one normalized car-data fact, keeping unknowns unknown."""
        payload = event.payload if isinstance(event.payload, dict) else {}
        values = {
            name: payload.get(name) for name in ("speed", "throttle", "brake", "rpm", "gear", "drs")
        }
        # A sample with no usable channel is noise, not a data point.
        if all(value is None for value in values.values()):
            return None
        readings = {name: _reading(name, values[name]) for name in CHANNEL_BOUNDS}
        drs = values["drs"] if isinstance(values["drs"], bool) else None
        if drs is None and all(value is None for value in readings.values()):
            return None
        try:
            return TelemetrySample(
                sequence=event.sequence_number,
                observed_at=event.event_time,
                lap_number=event.lap_number,
                speed=readings["speed"],
                throttle=readings["throttle"],
                brake=readings["brake"],
                rpm=None if readings["rpm"] is None else int(readings["rpm"]),
                gear=None if readings["gear"] is None else int(readings["gear"]),
                drs=drs,
            )
        except ValueError:
            return None

    async def read(
        self,
        session_key: str,
        *,
        drivers: list[int],
        lap_number: int | None = None,
        cursor: int | None = None,
    ) -> TelemetryWindow:
        """Return aligned bounded series for the selected drivers."""
        selected = list(dict.fromkeys(drivers))
        if not selected or len(selected) > MAX_COMPARISON_DRIVERS:
            raise TelemetrySelectionError(
                f"Select between 1 and {MAX_COMPARISON_DRIVERS} distinct drivers"
            )
        if any(number < 1 for number in selected):
            raise TelemetrySelectionError("Driver numbers must be positive")
        if lap_number is not None and lap_number < 0:
            raise TelemetrySelectionError("Lap number must not be negative")

        view_sequence, bound = await self._view_sequence(session_key, cursor)
        window = TelemetryWindow(
            session_key=session_key, lap_number=lap_number, view_sequence=view_sequence
        )
        populated = 0
        for number in selected:
            driver, scan_limited = await self._driver_window(session_key, number, lap_number, bound)
            window.drivers.append(driver)
            window.scan_limited = window.scan_limited or scan_limited
            populated += bool(driver.samples)

        if populated == 0:
            window.availability = "unavailable"
            window.reason = (
                "no_telemetry_for_lap" if lap_number is not None else "no_telemetry_retained"
            )
        elif populated < len(selected):
            window.availability = "partial"
            window.reason = "telemetry_missing_for_some_drivers"
        else:
            window.availability = "available"
            if window.scan_limited:
                # Coverage is real but incomplete; say so rather than implying a
                # full trace was returned.
                window.availability = "partial"
                window.reason = "scan_limit_reached"
        return window

    async def _view_sequence(self, session_key: str, cursor: int | None) -> tuple[int, int | None]:
        """Resolve the view cursor and the bound reads must not pass.

        The bound is ``None`` only when no cursor is known at all. A cursor of
        zero is a real bound meaning nothing has been consumed yet, and must not
        be confused with "read everything".
        """
        acknowledged: int | None = None
        if self.states is not None:
            try:
                state = await self.states.get_state(session_key)
                acknowledged = int(state.sequence_number or 0)
            except Exception as exc:
                logger.warning(
                    "Telemetry view sequence unavailable session=%s error=%s",
                    session_key,
                    type(exc).__name__,
                )
        if cursor is None:
            return (acknowledged or 0), acknowledged
        bound = min(cursor, acknowledged) if acknowledged is not None else cursor
        return bound, bound

    async def _driver_window(
        self,
        session_key: str,
        driver_number: int,
        lap_number: int | None,
        bound: int | None,
    ) -> tuple[DriverTelemetry, bool]:
        events = await self.events.list_for_session(
            session_key,
            limit=MAX_SCANNED_EVENTS,
            before_sequence=bound,
            event_types=[RaceEventType.CAR_DATA_SAMPLE],
            driver_number=driver_number,
            lap_number=lap_number,
            event_origin=EventOrigin.SOURCE_FACT,
        )
        scan_limited = len(events) >= MAX_SCANNED_EVENTS
        samples: list[TelemetrySample] = []
        for event in events:
            sample = self._sample(event)
            if sample is not None:
                samples.append(sample)

        truncated = len(samples) > MAX_SAMPLES_PER_DRIVER
        if truncated:
            # Keep the most recent window; a trace's tail is what a comparison
            # is usually asking about.
            samples = samples[-MAX_SAMPLES_PER_DRIVER:]

        channels = [
            name
            for name in CHANNEL_UNITS
            if any(getattr(sample, name, None) is not None for sample in samples)
        ]
        return (
            DriverTelemetry(
                driver_number=driver_number,
                samples=samples,
                channels=channels,
                samples_truncated=truncated,
            ),
            scan_limited,
        )


def _reading(channel: str, value: object) -> float | None:
    """Accept a finite, in-range numeric reading; drop provider corruption.

    NaN and infinity are not merely unusable in a chart, they are not valid
    JSON, so they must never reach a response.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    minimum, maximum = CHANNEL_BOUNDS[channel]
    return number if minimum <= number <= maximum else None
