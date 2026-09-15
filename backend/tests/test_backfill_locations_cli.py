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
