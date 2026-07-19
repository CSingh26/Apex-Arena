# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.cli.backfill_openf1 import parser, validate_args
from app.domain.rooms import (
    IngestionStatus,
    RaceRoom,
    RoomEligibilityStatus,
    RoomMode,
    RoomStatus,
    SessionType,
    SourceAvailability,
)
from app.services.historical import HistoricalDataAvailability, HistoricalIngestionResult
from app.services.openf1_backfill import (
    CORE_BACKFILL_ENDPOINTS,
    BackfillStatus,
    OpenF1HistoricalBackfillService,
    OpenF1RoomFinalizer,
    RoomFinalizationResult,
)


def room(*, session_key: str | None = None, meeting_key: str | None = "55") -> RaceRoom:
    return RaceRoom(
        slug="2026-australian-grand-prix-qualifying",
        event_slug="2026-australian-grand-prix",
        meeting_key=meeting_key,
        session_key=session_key,
        season=2026,
        round_number=1,
        race_name="Australian Grand Prix",
        official_name="Australian Grand Prix Qualifying",
        circuit_name="Albert Park Grand Prix Circuit",
        country="Australia",
        session_type=SessionType.QUALIFYING,
        scheduled_start=datetime(2026, 3, 7, 5, tzinfo=UTC),
        status=RoomStatus.PENDING,
        mode=RoomMode.REPLAY,
        eligibility_status=RoomEligibilityStatus.PROVIDER_PENDING,
        ingestion_status=IngestionStatus.PENDING,
        source_availability=SourceAvailability.UNAVAILABLE,
    )


def provider_session(
    *, key: str = "1001", meeting: str = "55", hour_delta: int = 0
) -> dict[str, object]:
    start = datetime(2026, 3, 7, 5, tzinfo=UTC) + timedelta(hours=hour_delta)
    return {
        "session_key": key,
        "meeting_key": meeting,
        "session_name": "Qualifying",
        "meeting_name": "Australian Grand Prix",
        "circuit_short_name": "Albert Park",
        "country_name": "Australia",
        "date_start": start.isoformat(),
        "date_end": (start + timedelta(hours=1)).isoformat(),
    }


class FakeClient:
    def __init__(self, sessions: list[dict[str, object]]) -> None:
        self.rows = sessions
        self.queries: list[dict[str, object]] = []

    async def sessions(self, **filters: object) -> list[dict[str, object]]:
        self.queries.append(filters)
        if "session_key" in filters:
            return [row for row in self.rows if row["session_key"] == filters["session_key"]]
        return self.rows


class FakeRooms:
    def __init__(self, value: RaceRoom | None) -> None:
        self.value = value
        self.binds: list[tuple[str, str | None, str]] = []

    async def get_room(self, slug: str) -> RaceRoom | None:
        return self.value if self.value and self.value.slug == slug else None

    async def get_room_by_session(self, session_key: str) -> RaceRoom | None:
        return self.value if self.value and self.value.session_key == session_key else None

    async def bind_provider_session(
        self, slug: str, *, meeting_key: str | None, session_key: str
    ) -> RaceRoom:
        self.binds.append((slug, meeting_key, session_key))
        assert self.value is not None
        self.value = self.value.model_copy(
            update={"meeting_key": meeting_key, "session_key": session_key}
        )
        return self.value


class FakeDatabase:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.leases: list[tuple[int, str]] = []

    @asynccontextmanager
    async def backfill_lease(self, season: int, session_key: str):  # type: ignore[no-untyped-def]
        self.leases.append((season, session_key))
        yield self.acquired


class FakeJobs:
    def __init__(self, completed: list[str] | None = None) -> None:
        self.completed = completed or []
        self.prepares = 0
        self.endpoint_updates: list[str] = []
        self.record = SimpleNamespace(
            completed_endpoints=self.completed,
            failed_endpoint=None,
            rows_fetched=0,
            rows_processed=0,
            rows_inserted=0,
            rows_deduplicated=0,
        )

    async def prepare(self, **kwargs):  # type: ignore[no-untyped-def]
        self.prepares += 1
        return self.record

    async def complete_endpoint(self, season, session_key, endpoint, **counts):  # type: ignore[no-untyped-def]
        self.endpoint_updates.append(endpoint)
        self.record.completed_endpoints.append(endpoint)
        self.record.rows_fetched += counts["fetched"]
        self.record.rows_processed += counts["processed"]
        self.record.rows_inserted += counts["inserted"]
        self.record.rows_deduplicated += counts["deduplicated"]
        return self.record

    async def start_endpoint(self, season: int, session_key: str, endpoint: str) -> None:
        return None

    async def get(self, season: int, session_key: str):  # type: ignore[no-untyped-def]
        return self.record

    async def finish(self, season: int, session_key: str, *, partial: bool) -> None:
        return None

    async def fail(self, season: int, session_key: str, endpoint: str, exc: Exception) -> None:
        self.record.failed_endpoint = endpoint


class FakeAdapter:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.endpoints: list[str] = []
        self.failures = failures or set()

    async def ingest_session(
        self, session_key: str, endpoints: list[str], *, update_room: bool
    ) -> HistoricalIngestionResult:
        assert update_room is False
        self.endpoints.extend(endpoints)
        if endpoints[0] in self.failures:
            raise RuntimeError("provider unavailable")
        return HistoricalIngestionResult(
            run_id=uuid4(),
            session_key=session_key,
            endpoints=endpoints,
            fetched_records=2,
            raw_inserted=1,
            duplicates=1,
            normalized_inserted=1,
            normalized_duplicates=0,
            snapshots=0,
            data_availability=HistoricalDataAvailability.PARTIAL,
        )


class FakeFinalizer:
    async def finalize(self, session_key: str) -> RoomFinalizationResult:
        return RoomFinalizationResult(
            room_slug="2026-australian-grand-prix-qualifying",
            normalized_event_count=10,
            endpoint_counts={"sessions": 1, "drivers": 20, "laps": 10},
            source_availability=SourceAvailability.LIMITED,
            replay_available=True,
            results_available=True,
        )


def service(
    settings,
    *,
    provider_rows=None,
    existing_room=None,
    acquired=True,
    completed=None,
    failures=None,
):  # type: ignore[no-untyped-def]
    client = FakeClient(provider_rows or [provider_session()])
    adapter = FakeAdapter(failures)
    jobs = FakeJobs(completed)
    rooms = FakeRooms(existing_room or room())
    database = FakeDatabase(acquired)
    backfill = OpenF1HistoricalBackfillService(
        settings=settings,
        client=client,  # type: ignore[arg-type]
        adapter=adapter,  # type: ignore[arg-type]
        jobs=jobs,  # type: ignore[arg-type]
        rooms=rooms,  # type: ignore[arg-type]
        database=database,  # type: ignore[arg-type]
        finalizer=FakeFinalizer(),  # type: ignore[arg-type]
        cli_safe=True,
    )
    return backfill, client, adapter, jobs, rooms, database


@pytest.mark.asyncio
async def test_resolution_prefers_existing_session_key(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, client, *_ = service(settings, existing_room=room(session_key="1001"))
    result = await backfill.resolve(season=2026, room_slug="2026-australian-grand-prix-qualifying")
    assert result.match_method == "existing_session_key"
    assert client.queries == [{"session_key": "1001"}]


@pytest.mark.asyncio
async def test_session_key_selector_finds_bound_room(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, _, _, _, rooms, _ = service(settings, existing_room=room(session_key="1001"))
    result = await backfill.run(season=2026, session_key="1001", dry_run=True)
    assert result.room_slug == rooms.value.slug  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_resolution_uses_meeting_key_and_session_type(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, *_ = service(settings)
    result = await backfill.resolve(season=2026, room_slug="2026-australian-grand-prix-qualifying")
    assert result.session_key == "1001"
    assert result.match_method == "meeting_key_and_session_type"


@pytest.mark.asyncio
async def test_resolution_rejects_ambiguous_metadata_match(settings) -> None:  # type: ignore[no-untyped-def]
    rows = [provider_session(key="1", meeting="1"), provider_session(key="2", meeting="2")]
    backfill, *_ = service(settings, provider_rows=rows, existing_room=room(meeting_key=None))
    with pytest.raises(ValueError, match="ambiguous"):
        await backfill.resolve(season=2026, room_slug="2026-australian-grand-prix-qualifying")


@pytest.mark.asyncio
async def test_dry_run_resolves_without_writes(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, _, adapter, jobs, rooms, database = service(settings)
    result = await backfill.run(
        season=2026,
        room_slug="2026-australian-grand-prix-qualifying",
        dry_run=True,
    )
    assert result.status is BackfillStatus.DRY_RUN
    assert result.endpoints == list(CORE_BACKFILL_ENDPOINTS)
    assert not adapter.endpoints and not jobs.prepares and not rooms.binds and not database.leases


@pytest.mark.asyncio
async def test_default_run_is_one_session_and_checkpoints_each_endpoint(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, _, adapter, jobs, rooms, database = service(settings)
    result = await backfill.run(season=2026, room_slug="2026-australian-grand-prix-qualifying")
    assert result.status is BackfillStatus.COMPLETED
    assert adapter.endpoints == ["sessions", *CORE_BACKFILL_ENDPOINTS]
    assert jobs.endpoint_updates == adapter.endpoints
    assert rooms.binds == [(rooms.value.slug, "55", "1001")]  # type: ignore[union-attr]
    assert database.leases == [(2026, "1001")]
    assert result.replay_available is True


@pytest.mark.asyncio
async def test_completed_endpoint_is_resumed_without_refetch(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, _, adapter, _, *_ = service(settings, completed=["sessions", "drivers"])
    result = await backfill.run(
        season=2026,
        room_slug="2026-australian-grand-prix-qualifying",
        resume=True,
    )
    assert adapter.endpoints[0] == "laps"
    assert result.skipped_completed_endpoints == ["sessions", "drivers"]


@pytest.mark.asyncio
async def test_optional_endpoint_failure_finishes_partial_without_losing_checkpoints(
    settings,
) -> None:  # type: ignore[no-untyped-def]
    backfill, _, adapter, jobs, *_ = service(settings, failures={"intervals"})
    result = await backfill.run(
        season=2026,
        room_slug="2026-australian-grand-prix-qualifying",
    )
    assert result.status is BackfillStatus.PARTIAL
    assert "intervals" in adapter.endpoints
    assert jobs.record.failed_endpoint == "intervals"
    assert "starting_grid" in jobs.endpoint_updates


@pytest.mark.asyncio
async def test_session_lock_prevents_second_worker(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, _, adapter, jobs, rooms, *_ = service(settings, acquired=False)
    result = await backfill.run(season=2026, room_slug="2026-australian-grand-prix-qualifying")
    assert result.status is BackfillStatus.LOCKED
    assert not adapter.endpoints and not jobs.prepares and not rooms.binds


@pytest.mark.asyncio
async def test_api_process_cannot_run_without_cli_context(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, *_ = service(settings)
    backfill.cli_safe = False
    with pytest.raises(RuntimeError, match="ingestor role"):
        await backfill.run(season=2026, room_slug="2026-australian-grand-prix-qualifying")


@pytest.mark.asyncio
async def test_high_frequency_endpoints_are_opt_in(settings) -> None:  # type: ignore[no-untyped-def]
    backfill, *_ = service(settings)
    with pytest.raises(ValueError, match="High-frequency"):
        await backfill.run(
            season=2026,
            room_slug="2026-australian-grand-prix-qualifying",
            endpoints=["car_data"],
            dry_run=True,
        )
    allowed = await backfill.run(
        season=2026,
        room_slug="2026-australian-grand-prix-qualifying",
        endpoints=["drivers"],
        include_high_frequency=True,
        dry_run=True,
    )
    assert allowed.endpoints == ["drivers", "car_data", "location"]


def test_cli_enforces_one_session_default() -> None:
    args = parser().parse_args(["--room-slug", "room", "--max-sessions", "2"])
    with pytest.raises(ValueError, match="exactly one session"):
        validate_args(args)


def test_cli_rejects_ambiguous_selector_combination() -> None:
    args = parser().parse_args(["--room-slug", "room", "--session-key", "1001"])
    with pytest.raises(ValueError, match="exactly one"):
        validate_args(args)


@pytest.mark.parametrize(
    ("counts", "normalized", "expected"),
    [
        ({}, 0, SourceAvailability.UNAVAILABLE),
        ({"session_result": 20}, 20, SourceAvailability.RESULTS_ONLY),
        ({"laps": 10}, 10, SourceAvailability.TIMING_ONLY),
        (
            {"sessions": 1, "drivers": 20, "laps": 10},
            31,
            SourceAvailability.LIMITED,
        ),
        (
            {"sessions": 1, "drivers": 20, "laps": 10, "car_data": 100},
            131,
            SourceAvailability.TELEMETRY,
        ),
    ],
)
def test_room_finalization_thresholds(
    counts: dict[str, int], normalized: int, expected: SourceAvailability
) -> None:
    assert OpenF1RoomFinalizer.classify(counts, normalized) is expected


def test_room_finalization_never_downgrades_better_data() -> None:
    assert (
        OpenF1RoomFinalizer.classify(
            {"session_result": 20},
            20,
            current=SourceAvailability.TELEMETRY,
        )
        is SourceAvailability.TELEMETRY
    )
