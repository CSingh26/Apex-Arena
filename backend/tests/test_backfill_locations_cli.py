# SPDX-License-Identifier: AGPL-3.0-only
"""The whole-environment geometry rebuild must not be race-specific or fragile."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.cli.backfill_locations import parser, rebuild_all_geometry, validate_args


def args(*argv: str):
    return parser().parse_args(list(argv))


def test_all_sessions_needs_no_session_key():
    validate_args(args("--all-sessions", "--rebuild-geometry-only"))


@pytest.mark.parametrize(
    "argv",
    [
        # One selector or the other, never both.
        ("--all-sessions", "--rebuild-geometry-only", "--session-key", "11361"),
        ("--all-sessions", "--rebuild-geometry-only", "--room-slug", "italian-gp-race"),
        # Refetching every session's full series is a different, far heavier job.
        ("--all-sessions",),
        # A startup job must never drop stored samples.
        ("--all-sessions", "--rebuild-geometry-only", "--reset"),
    ],
)
def test_unsafe_or_ambiguous_all_sessions_requests_are_refused(argv):
    with pytest.raises(ValueError):
        validate_args(args(*argv))


def test_single_session_mode_is_unchanged():
    validate_args(args("--session-key", "11361", "--rebuild-geometry-only"))
    with pytest.raises(ValueError):
        validate_args(args("--rebuild-geometry-only"))


class FakeIngestion:
    def __init__(self, failing: set[str]):
        self.failing = failing
        self.rebuilt: list[str] = []

    async def resolve_window(self, session_key, start, end):
        return None, None

    async def rebuild_geometry(self, session_key, start, end):
        if session_key in self.failing:
            raise RuntimeError("synthetic provider outage")
        self.rebuilt.append(session_key)
        if session_key == "sparse":
            return None
        return SimpleNamespace(path=[(0.0, 0.0)] * 900)


def services_for(keys, failing=frozenset()):
    async def session_keys():
        return list(keys)

    ingestion = FakeIngestion(set(failing))
    return (
        SimpleNamespace(
            location_ingestion=ingestion,
            location_repository=SimpleNamespace(session_keys=session_keys),
        ),
        ingestion,
    )


async def test_one_failing_session_does_not_leave_the_rest_stale(capsys):
    services, ingestion = services_for(["11354", "broken", "11361"], failing={"broken"})

    assert await rebuild_all_geometry(services, json_summary=False) == 0

    assert ingestion.rebuilt == ["11354", "11361"]
    output = capsys.readouterr().out
    assert "session=broken RuntimeError" in output
    assert "session=11361 track_points=900" in output


async def test_a_session_that_cannot_support_an_outline_is_reported_not_fatal(capsys):
    services, _ = services_for(["sparse"])
    assert await rebuild_all_geometry(services, json_summary=True) == 0
    assert '"track_points": 0' in capsys.readouterr().out


async def test_an_empty_database_is_a_quiet_success(capsys):
    services, ingestion = services_for([])
    assert await rebuild_all_geometry(services, json_summary=False) == 0
    assert ingestion.rebuilt == []


# --- Every started room gets its GPS series --------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from app.cli.backfill_locations import backfill_all_rooms, room_session_keys  # noqa: E402
from app.services.locations import LocationUnavailableError  # noqa: E402

NOW = datetime.now(UTC)


def test_all_rooms_needs_no_session_key():
    validate_args(args("--all-rooms"))


@pytest.mark.parametrize(
    "argv",
    [
        ("--all-rooms", "--session-key", "11361"),
        ("--all-rooms", "--all-sessions"),
        ("--all-rooms", "--rebuild-geometry-only"),
        ("--all-rooms", "--reset"),
        # A partial series would look loaded and never be completed later.
        ("--all-rooms", "--max-minutes", "5"),
    ],
)
def test_unsafe_or_ambiguous_all_rooms_requests_are_refused(argv):
    with pytest.raises(ValueError):
        validate_args(args(*argv))


def room(key, *, started=True):
    return SimpleNamespace(
        session_key=key,
        scheduled_start=NOW - timedelta(days=1) if started else NOW + timedelta(days=3),
    )


class RoomRepository:
    def __init__(self, rooms, page_size_seen):
        self.rooms = rooms
        self.page_size_seen = page_size_seen

    async def list_rooms(self, *, sort, limit, offset):
        self.page_size_seen.append(limit)
        return self.rooms[offset : offset + limit], len(self.rooms)


class LoadingIngestion:
    def __init__(self, unavailable=(), failing=()):
        self.unavailable = set(unavailable)
        self.failing = set(failing)
        self.loaded = []

    async def ingest_session(self, session_key):
        if session_key in self.unavailable:
            raise LocationUnavailableError("no data")
        if session_key in self.failing:
            raise RuntimeError("synthetic outage")
        self.loaded.append(session_key)
        return SimpleNamespace(total_samples=50_000, track_points=940)


def room_services(rooms, *, loaded=(), unavailable=(), failing=()):
    ingestion = LoadingIngestion(unavailable, failing)
    stored = set(loaded)

    async def count(key):
        return 10 if key in stored else 0

    services = SimpleNamespace(
        room_repository=RoomRepository(rooms, []),
        location_repository=SimpleNamespace(count=count),
        location_ingestion=ingestion,
    )
    return services, ingestion


async def test_every_page_of_rooms_is_visited_not_just_the_first():
    rooms = [room(str(index)) for index in range(250)]
    services, _ = room_services(rooms)
    keys = await room_session_keys(services, now=NOW)
    assert len(keys) == 250


async def test_rooms_that_have_not_started_are_not_requested():
    services, _ = room_services([room("past"), room("future", started=False)])
    assert await room_session_keys(services, now=NOW) == ["past"]


async def test_rooms_sharing_a_session_are_loaded_once():
    services, ingestion = room_services([room("11361"), room("11361")])
    await backfill_all_rooms(services, json_summary=False)
    assert ingestion.loaded == ["11361"]


async def test_only_rooms_missing_gps_are_loaded_so_reruns_resume(capsys):
    services, ingestion = room_services(
        [room("done"), room("missing"), room("empty"), room("broken")],
        loaded={"done"},
        unavailable={"empty"},
        failing={"broken"},
    )
    assert await backfill_all_rooms(services, json_summary=False) == 0

    assert ingestion.loaded == ["missing"]
    output = capsys.readouterr().out
    assert "session=done status=already_loaded" in output
    assert "session=missing status=loaded samples=50000 track_points=940" in output
    assert "session=empty status=no_provider_data" in output
    assert "session=broken status=failed error=RuntimeError" in output
