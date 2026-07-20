# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.rooms import (
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionType,
    SourceAvailability,
)
from app.services.openf1_backfill import BackfillStatus, BackfillSummary, SessionResolution
from app.services.recent_sessions import (
    ProviderPublicationState,
    RecentSessionReconciliationService,
    ReconciliationPassSummary,
)


def spa_room(
    *,
    slug: str = "2026-belgian-grand-prix-qualifying",
    session_type: SessionType = SessionType.QUALIFYING,
    session_key: str | None = None,
    scheduled_start: datetime = datetime(2026, 7, 18, 14, tzinfo=UTC),
    replay_available: bool = False,
) -> RaceRoom:
    return RaceRoom(
        slug=slug,
        event_slug="2026-belgian-grand-prix",
        meeting_key="1290",
        session_key=session_key,
        season=2026,
        round_number=13,
        race_name="Belgian Grand Prix - Qualifying",
        official_name="Belgian Grand Prix",
        circuit_name="Circuit de Spa-Francorchamps",
        country="Belgium",
        session_type=session_type,
        scheduled_start=scheduled_start,
        status=RoomStatus.PENDING,
        mode=RoomMode.REPLAY,
        eligibility_status=RoomEligibilityStatus.PROVIDER_PENDING,
        ingestion_status=IngestionStatus.PENDING,
        source_availability=SourceAvailability.UNAVAILABLE,
        replay_available=replay_available,
    )


class FakeDatabase:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired

    @asynccontextmanager
    async def reconciliation_lease(self):  # type: ignore[no-untyped-def]
        yield self.acquired


class FakeRoomsService:
    def __init__(self) -> None:
        self.force_syncs = 0
        self.invalidations = 0

    async def force_sync(self) -> int:
        self.force_syncs += 1
        return 1

    def invalidate_catalog(self) -> None:
        self.invalidations += 1


class FakeRoomRepository:
    def __init__(self, rooms: list[RaceRoom]) -> None:
        self.rooms = rooms
        self.binds: list[tuple[str, str | None, str]] = []

    async def list_recent_reconciliation_candidates(self, **kwargs: object) -> list[RaceRoom]:
        now = kwargs["now"]
        assert isinstance(now, datetime)
        lookback = timedelta(days=int(kwargs["lookback_days"]))
        grace = timedelta(minutes=int(kwargs["grace_minutes"]))
        return [
            room
            for room in self.rooms
            if not room.is_development
            and room.session_type
            in {
                SessionType.QUALIFYING,
                SessionType.SPRINT_QUALIFYING,
                SessionType.SPRINT,
                SessionType.RACE,
            }
            and now - lookback <= room.scheduled_start <= now - grace
            and not room.replay_available
        ][: int(kwargs["limit"])]

    async def bind_provider_session(
        self, slug: str, *, meeting_key: str | None, session_key: str
    ) -> RaceRoom:
        self.binds.append((slug, meeting_key, session_key))
        room = next(item for item in self.rooms if item.slug == slug)
        return room.model_copy(update={"meeting_key": meeting_key, "session_key": session_key})


class FakeBackfill:
    def __init__(self, *, fails_resolution: bool = False, replay_available: bool = True) -> None:
        self.fails_resolution = fails_resolution
        self.replay_available = replay_available
        self.runs: list[dict[str, Any]] = []

    async def resolve(self, *, season: int, room_slug: str, **_: object) -> SessionResolution:
        if self.fails_resolution:
            raise ValueError("No confident OpenF1 session match was found")
        return SessionResolution(
            session_key="11330" if room_slug.endswith("qualifying") else "11334",
            meeting_key="1290",
            room_slug=room_slug,
            match_method="metadata_date_type",
            confidence="high",
            candidate_count=1,
            normalized_provider_session_name="QUALIFYING",
            provider_session={
                "session_key": "11330",
                "meeting_key": "1290",
                "session_name": "Qualifying",
                "date_end": "2026-07-18T15:00:00+00:00",
            },
        )

    async def run(self, **kwargs: Any) -> BackfillSummary:
        self.runs.append(kwargs)
        return BackfillSummary(
            status=BackfillStatus.PARTIAL,
            season=kwargs["season"],
            session_key="11330",
            meeting_key="1290",
            room_slug=kwargs["room_slug"],
            match_method="metadata_date_type",
            confidence="high",
            candidate_count=1,
            endpoints=kwargs["endpoints"],
            rows_fetched=10,
            rows_processed=10,
            rows_inserted=8,
            normalized_event_count=8,
            source_availability=SourceAvailability.LIMITED,
            replay_available=self.replay_available,
            results_available=True,
        )


class FakeClient:
    def __init__(self, endpoint_rows: dict[str, list[dict[str, object]]]) -> None:
        self.endpoint_rows = endpoint_rows

    def __getattr__(self, endpoint: str):  # type: ignore[no-untyped-def]
        async def fetch(**_: object) -> list[dict[str, object]]:
            if endpoint == "intervals" and endpoint not in self.endpoint_rows:
                raise RuntimeError("provider endpoint missing")
            return self.endpoint_rows.get(endpoint, [])

        return fetch


def service(settings, *, rooms, endpoint_rows, auto_backfill=True, backfill=None):  # type: ignore[no-untyped-def]
    configured = settings.model_copy(
        update={
            "app_process_role": "combined",
            "recent_session_reconciliation_enabled": True,
            "recent_session_auto_backfill_enabled": auto_backfill,
        }
    )
    rooms_service = FakeRoomsService()
    repository = FakeRoomRepository(rooms)
    backfill_service = backfill or FakeBackfill()
    return (
        RecentSessionReconciliationService(
            settings=configured,
            database=FakeDatabase(),  # type: ignore[arg-type]
            rooms=rooms_service,  # type: ignore[arg-type]
            room_repository=repository,  # type: ignore[arg-type]
            client=FakeClient(endpoint_rows),  # type: ignore[arg-type]
            backfill=backfill_service,  # type: ignore[arg-type]
        ),
        rooms_service,
        repository,
        backfill_service,
    )


@pytest.mark.asyncio
async def test_recent_spa_qualifying_is_backfilled_when_core_data_exists(settings) -> None:  # type: ignore[no-untyped-def]
    rows = {
        "drivers": [{"driver_number": 1}],
        "laps": [{"date_start": "2026-07-18T14:02:00+00:00"}],
        "position": [{"date": "2026-07-18T14:03:00+00:00"}],
        "race_control": [],
        "weather": [],
        "session_result": [{"position": 1}],
        "starting_grid": [{"position": 1}],
    }
    reconciler, rooms_service, repository, backfill = service(
        settings,
        rooms=[spa_room()],
        endpoint_rows=rows,
    )

    summary = await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))

    assert summary.sessions_examined == 1
    assert summary.sessions_matched == 1
    assert summary.sessions_queued_for_backfill == 1
    assert summary.sessions_finalized == 1
    assert rooms_service.invalidations == 1
    assert repository.binds == [("2026-belgian-grand-prix-qualifying", "1290", "11330")]
    assert backfill.runs[0]["endpoints"] == [
        "drivers",
        "laps",
        "position",
        "race_control",
        "weather",
        "session_result",
        "starting_grid",
    ]


@pytest.mark.asyncio
async def test_recent_spa_race_uses_race_endpoint_allowlist(settings) -> None:  # type: ignore[no-untyped-def]
    rows = {
        "drivers": [{}],
        "laps": [{"date_start": "2026-07-19T13:01:00+00:00"}],
        "position": [{}],
        "intervals": [{}],
        "pit": [{}],
        "stints": [{}],
        "race_control": [{}],
        "weather": [{}],
        "session_result": [{}],
        "starting_grid": [{}],
    }
    reconciler, _, _, backfill = service(
        settings,
        rooms=[
            spa_room(
                slug="2026-belgian-grand-prix-race",
                session_type=SessionType.RACE,
                scheduled_start=datetime(2026, 7, 19, 13, tzinfo=UTC),
            )
        ],
        endpoint_rows=rows,
    )

    await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))

    assert backfill.runs[0]["endpoints"] == [
        "drivers",
        "laps",
        "position",
        "intervals",
        "pit",
        "stints",
        "race_control",
        "weather",
        "session_result",
        "starting_grid",
    ]


@pytest.mark.asyncio
async def test_provider_metadata_missing_remains_pending(settings) -> None:  # type: ignore[no-untyped-def]
    reconciler, rooms_service, repository, backfill = service(
        settings,
        rooms=[spa_room()],
        endpoint_rows={},
        backfill=FakeBackfill(fails_resolution=True),
    )

    summary = ReconciliationPassSummary(started_at=datetime(2026, 7, 20, 12, tzinfo=UTC))
    state = await reconciler._reconcile_room(spa_room(), summary=summary)  # noqa: SLF001

    assert state is ProviderPublicationState.AWAITING_SESSION_METADATA
    assert rooms_service.invalidations == 0
    assert repository.binds == []
    assert backfill.runs == []


@pytest.mark.asyncio
async def test_core_data_missing_binds_metadata_without_auto_success(settings) -> None:  # type: ignore[no-untyped-def]
    reconciler, rooms_service, repository, backfill = service(
        settings,
        rooms=[spa_room()],
        endpoint_rows={"drivers": [{}], "weather": [{}]},
    )

    summary = await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))

    assert summary.sessions_awaiting_provider == 1
    assert repository.binds == [("2026-belgian-grand-prix-qualifying", "1290", "11330")]
    assert rooms_service.invalidations == 1
    assert backfill.runs == []


@pytest.mark.asyncio
async def test_future_sessions_are_excluded(settings) -> None:  # type: ignore[no-untyped-def]
    reconciler, _, _, backfill = service(
        settings,
        rooms=[spa_room(scheduled_start=datetime(2026, 7, 21, 14, tzinfo=UTC))],
        endpoint_rows={"drivers": [{}], "laps": [{}]},
    )

    summary = await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))

    assert summary.sessions_examined == 0
    assert backfill.runs == []
