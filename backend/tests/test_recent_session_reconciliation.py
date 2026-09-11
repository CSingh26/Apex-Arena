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
        self.force_sync_calls: list[dict[str, object]] = []
        self.invalidations = 0

    async def force_sync(self, **kwargs) -> int:
        self.force_syncs += 1
        self.force_sync_calls.append(kwargs)
        return 1

    def invalidate_catalog(self) -> None:
        self.invalidations += 1


class FakeRoomRepository:
    def __init__(self, rooms: list[RaceRoom]) -> None:
        self.rooms = rooms
        self.binds: list[tuple[str, str | None, str]] = []
        self.candidate_queries: list[dict[str, object]] = []
        self.candidate_count_queries: list[dict[str, object]] = []

    async def list_recent_reconciliation_candidates(self, **kwargs: object) -> list[RaceRoom]:
        self.candidate_queries.append(kwargs)
        candidates = self._candidates(**kwargs)
        offset = int(kwargs.get("offset", 0))
        return candidates[offset : offset + int(kwargs["limit"])]

    async def count_recent_reconciliation_candidates(self, **kwargs: object) -> int:
        self.candidate_count_queries.append(kwargs)
        return len(self._candidates(**kwargs))

    def _candidates(self, **kwargs: object) -> list[RaceRoom]:
        now = kwargs["now"]
        assert isinstance(now, datetime)
        lookback = timedelta(days=int(kwargs["lookback_days"]))
        grace = timedelta(minutes=int(kwargs["grace_minutes"]))
        candidates = [
            room
            for room in self.rooms
            if room.session_type
            in {
                SessionType.PRACTICE_1,
                SessionType.PRACTICE_2,
                SessionType.PRACTICE_3,
                SessionType.QUALIFYING,
                SessionType.SPRINT_QUALIFYING,
                SessionType.SPRINT,
                SessionType.RACE,
            }
            and now - lookback <= room.scheduled_start <= now - grace
            and not room.replay_available
        ]
        candidates.sort(key=lambda room: room.slug)
        candidates.sort(key=lambda room: room.scheduled_start, reverse=True)
        return candidates

    async def bind_provider_session(
        self, slug: str, *, meeting_key: str | None, session_key: str
    ) -> RaceRoom:
        self.binds.append((slug, meeting_key, session_key))
        room = next(item for item in self.rooms if item.slug == slug)
        return room.model_copy(update={"meeting_key": meeting_key, "session_key": session_key})


class FakeBackfill:
    def __init__(
        self,
        *,
        fails_resolution: bool = False,
        replay_available: bool = True,
        status: BackfillStatus = BackfillStatus.PARTIAL,
    ) -> None:
        self.fails_resolution = fails_resolution
        self.replay_available = replay_available
        self.status = status
        self.resolutions: list[str] = []
        self.runs: list[dict[str, Any]] = []

    async def resolve(self, *, season: int, room_slug: str, **_: object) -> SessionResolution:
        self.resolutions.append(room_slug)
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
            status=self.status,
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


def service(  # type: ignore[no-untyped-def]
    settings,
    *,
    rooms,
    endpoint_rows,
    auto_backfill=True,
    max_sessions=None,
    backfill=None,
):
    updates = {
        "app_process_role": "combined",
        "recent_session_reconciliation_enabled": True,
        "recent_session_auto_backfill_enabled": auto_backfill,
    }
    if max_sessions is not None:
        updates["recent_session_auto_backfill_max_sessions"] = max_sessions
    configured = settings.model_copy(update=updates)
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
    assert backfill.runs[0]["resume"] is True
    assert backfill.runs[0]["force_retry_failed"] is True


@pytest.mark.asyncio
async def test_reconciliation_uses_observed_time_for_bounded_catalog_sync(settings) -> None:  # type: ignore[no-untyped-def]
    observed_at = datetime(2026, 7, 20, 12, tzinfo=UTC)
    reconciler, rooms_service, repository, _ = service(
        settings,
        rooms=[],
        endpoint_rows={},
    )

    await reconciler.run_once(now=observed_at)

    assert rooms_service.force_sync_calls == [
        {
            "now": observed_at,
            "live_window_only": True,
            "lookback_days": 14,
        }
    ]
    assert repository.candidate_count_queries == [
        {
            "now": observed_at,
            "lookback_days": 14,
            "grace_minutes": 15,
        }
    ]
    assert repository.candidate_queries == []


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
async def test_recent_practice_session_is_backfilled(settings) -> None:  # type: ignore[no-untyped-def]
    reconciler, _, _, backfill = service(
        settings,
        rooms=[
            spa_room(slug="2026-belgian-grand-prix-practice-1", session_type=SessionType.PRACTICE_1)
        ],
        endpoint_rows={"drivers": [{}], "laps": [{}]},
    )

    summary = await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))

    assert summary.sessions_examined == 1
    assert summary.sessions_queued_for_backfill == 1
    assert backfill.runs[0]["room_slug"] == "2026-belgian-grand-prix-practice-1"


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


@pytest.mark.asyncio
async def test_candidate_budget_is_deterministic_and_rotates_without_starvation(settings) -> None:  # type: ignore[no-untyped-def]
    targets = [
        spa_room(
            slug="older-practice",
            session_type=SessionType.PRACTICE_1,
            scheduled_start=datetime(2026, 7, 18, 10, tzinfo=UTC),
        ),
        spa_room(
            slug="newer-b-qualifying",
            scheduled_start=datetime(2026, 7, 19, 10, tzinfo=UTC),
        ),
        spa_room(
            slug="newer-a-qualifying",
            scheduled_start=datetime(2026, 7, 19, 10, tzinfo=UTC),
        ),
    ]
    reconciler, _, _, backfill = service(
        settings,
        rooms=targets,
        endpoint_rows={},
        auto_backfill=False,
        max_sessions=2,
    )

    first = await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))
    second = await reconciler.run_once(now=datetime(2026, 7, 20, 12, 15, tzinfo=UTC))

    assert first.sessions_examined == 2
    assert second.sessions_examined == 2
    assert backfill.resolutions == [
        "newer-a-qualifying",
        "newer-b-qualifying",
        "older-practice",
        "newer-a-qualifying",
    ]


@pytest.mark.asyncio
async def test_locked_backfill_job_consumes_budget_then_rotates(settings) -> None:  # type: ignore[no-untyped-def]
    targets = [spa_room(slug="new-qualifying"), spa_room(slug="older-qualifying")]
    backfill = FakeBackfill(
        replay_available=False,
        status=BackfillStatus.LOCKED,
    )
    reconciler, _, _, _ = service(
        settings,
        rooms=targets,
        endpoint_rows={"drivers": [{}], "laps": [{}]},
        backfill=backfill,
    )

    first = await reconciler.run_once(now=datetime(2026, 7, 20, 12, tzinfo=UTC))
    second = await reconciler.run_once(now=datetime(2026, 7, 20, 12, 15, tzinfo=UTC))

    assert first.sessions_examined == 1
    assert first.sessions_queued_for_backfill == 1
    assert first.sessions_finalized == 0
    assert second.sessions_examined == 1
    assert second.sessions_queued_for_backfill == 1
    assert second.sessions_finalized == 0
    assert first.current_room_slug == "new-qualifying"
    assert second.current_room_slug == "older-qualifying"
    assert [run["room_slug"] for run in backfill.runs] == [
        "new-qualifying",
        "older-qualifying",
    ]


@pytest.mark.asyncio
async def test_restart_stable_pagination_reaches_candidate_beyond_first_hundred(settings) -> None:  # type: ignore[no-untyped-def]
    targets = [
        spa_room(
            slug=f"candidate-{index:03}",
            scheduled_start=datetime(2026, 7, 18, 10, tzinfo=UTC),
        )
        for index in range(101)
    ]
    first_pass = datetime(2026, 7, 20, 12, tzinfo=UTC)
    attempted: list[str] = []

    for pass_index in range(101):
        reconciler, _, _, backfill = service(
            settings,
            rooms=targets,
            endpoint_rows={},
            auto_backfill=False,
        )
        await reconciler.run_once(
            now=first_pass
            + timedelta(
                seconds=pass_index * settings.recent_session_reconciliation_interval_seconds
            )
        )
        attempted.extend(backfill.resolutions)

    assert len(attempted) == 101
    assert set(attempted) == {f"candidate-{index:03}" for index in range(101)}
    assert "candidate-100" in attempted
