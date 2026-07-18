# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.domain.rooms import (
    RaceRoom,
    RoomEligibilityStatus,
    RoomStatus,
    SourceAvailability,
)


class RoomEligibilityResult(BaseModel):
    status: RoomEligibilityStatus
    can_create: bool
    can_open: bool
    can_replay: bool
    reason: str


class RoomActionUnavailableError(RuntimeError):
    def __init__(self, result: RoomEligibilityResult) -> None:
        super().__init__(result.reason)
        self.result = result


class RoomEligibilityService:
    """The single policy boundary for room creation, opening, and generation."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        *,
        scheduled_start: datetime | None,
        actual_status: str | RoomStatus | None,
        provider_session_available: bool,
        data_availability: SourceAvailability = SourceAvailability.UNAVAILABLE,
        replay_available: bool = False,
        results_available: bool = False,
        existing_room: RaceRoom | None = None,
        test_fixture_mode: bool = False,
        now: datetime | None = None,
    ) -> RoomEligibilityResult:
        observed_at = self._aware(now or self._clock())
        starts_at = self._aware(scheduled_start) if scheduled_start is not None else None
        status = (
            actual_status.value
            if isinstance(actual_status, RoomStatus)
            else str(actual_status or "")
        ).casefold()

        if test_fixture_mode:
            return RoomEligibilityResult(
                status=(
                    RoomEligibilityStatus.ALREADY_EXISTS
                    if existing_room is not None
                    else RoomEligibilityStatus.ELIGIBLE_HISTORICAL
                ),
                can_create=existing_room is None,
                can_open=True,
                can_replay=True,
                reason="Internal deterministic fixture mode is enabled.",
            )

        # A stale row from an older catalog must never make a future session active.
        if (
            starts_at is not None
            and starts_at > observed_at
            and status
            not in {
                RoomStatus.LIVE.value,
                RoomStatus.COMPLETED.value,
            }
        ):
            return RoomEligibilityResult(
                status=RoomEligibilityStatus.FUTURE_READ_ONLY,
                can_create=False,
                can_open=False,
                can_replay=False,
                reason="This session has not started. Room opens when session data is available.",
            )

        if existing_room is not None:
            if (
                existing_room.status in {RoomStatus.PENDING, RoomStatus.INGESTING}
                and existing_room.source_availability is SourceAvailability.UNAVAILABLE
            ):
                return self._provider_pending()
            replay_ready = bool(
                existing_room.replay_available
                or (
                    existing_room.status is not RoomStatus.LIVE
                    and existing_room.source_availability
                    in {
                        SourceAvailability.TELEMETRY,
                        SourceAvailability.LIMITED,
                        SourceAvailability.TIMING_ONLY,
                    }
                )
            )
            return RoomEligibilityResult(
                status=RoomEligibilityStatus.ALREADY_EXISTS,
                can_create=False,
                can_open=existing_room.status not in {RoomStatus.PENDING, RoomStatus.UNAVAILABLE},
                can_replay=replay_ready,
                reason="A room already exists for this session.",
            )

        if status in {RoomStatus.LIVE.value, "live"}:
            if (
                provider_session_available
                or data_availability is not SourceAvailability.UNAVAILABLE
            ):
                return RoomEligibilityResult(
                    status=RoomEligibilityStatus.ELIGIBLE_LIVE,
                    can_create=True,
                    can_open=True,
                    can_replay=False,
                    reason="The session is live and provider data is available.",
                )
            return self._provider_pending()

        if status in {RoomStatus.COMPLETED.value, RoomStatus.READY.value, "completed", "finished"}:
            if (
                provider_session_available
                or data_availability is not SourceAvailability.UNAVAILABLE
                or replay_available
                or results_available
            ):
                can_replay = bool(
                    replay_available
                    or data_availability
                    in {
                        SourceAvailability.TELEMETRY,
                        SourceAvailability.LIMITED,
                        SourceAvailability.TIMING_ONLY,
                    }
                )
                return RoomEligibilityResult(
                    status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
                    can_create=True,
                    can_open=True,
                    can_replay=can_replay,
                    reason="Historical session data is available.",
                )
            return self._provider_pending()

        if starts_at is None:
            return RoomEligibilityResult(
                status=RoomEligibilityStatus.UNAVAILABLE,
                can_create=False,
                can_open=False,
                can_replay=False,
                reason="No authoritative session start is available.",
            )

        # A provider can mark a just-started session later than the calendar. Keep
        # this narrow so stale schedules do not look indefinitely live.
        if starts_at <= observed_at < starts_at + timedelta(hours=6):
            if provider_session_available:
                return RoomEligibilityResult(
                    status=RoomEligibilityStatus.ELIGIBLE_LIVE,
                    can_create=True,
                    can_open=True,
                    can_replay=False,
                    reason="The scheduled session has started and provider data is available.",
                )
            return self._provider_pending()

        if data_availability is not SourceAvailability.UNAVAILABLE or results_available:
            return RoomEligibilityResult(
                status=RoomEligibilityStatus.ELIGIBLE_HISTORICAL,
                can_create=True,
                can_open=True,
                can_replay=replay_available,
                reason="Historical session data is available.",
            )
        return self._provider_pending()

    def evaluate_room(
        self, room: RaceRoom, *, now: datetime | None = None
    ) -> RoomEligibilityResult:
        return self.evaluate(
            scheduled_start=room.scheduled_start,
            actual_status=room.status,
            provider_session_available=room.session_key is not None,
            data_availability=room.source_availability,
            replay_available=room.replay_available,
            results_available=room.results_available,
            existing_room=room,
            test_fixture_mode=room.is_development,
            now=now,
        )

    def require_room_action(
        self,
        room: RaceRoom,
        *,
        action: str,
        now: datetime | None = None,
    ) -> RoomEligibilityResult:
        result = self.evaluate_room(room, now=now)
        allowed = result.can_open if action in {"open", "generate"} else result.can_replay
        if not allowed:
            raise RoomActionUnavailableError(result)
        return result

    @staticmethod
    def _provider_pending() -> RoomEligibilityResult:
        return RoomEligibilityResult(
            status=RoomEligibilityStatus.PROVIDER_PENDING,
            can_create=False,
            can_open=False,
            can_replay=False,
            reason="The provider has not published usable session data yet.",
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
