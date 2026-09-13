# SPDX-License-Identifier: AGPL-3.0-only
"""Pin the contract native clients are told to depend on.

docs/native-client-contracts.md makes promises to a future Swift or Kotlin
client that cannot see this codebase. These tests fail when the code stops
honouring them, so the document cannot quietly become fiction.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.proxy import PROXY_TOKEN_HEADER, REPLAY_OPERATOR_HEADER, UNPROTECTED_PATHS
from app.api.room_streaming import discussion_event_id, parse_discussion_event_id
from app.core.settings import Settings
from app.main import create_app

DOC = Path(__file__).resolve().parents[2] / "docs" / "native-client-contracts.md"

# Read endpoints the document tells clients to call. A rename here is a
# breaking change for a shipped native client.
DOCUMENTED_PATHS = {
    "/api/v1/season/{year}",
    "/api/v1/season/{season}/weekends",
    "/api/v1/weekends/{event_slug}",
    "/api/v1/weekends/{event_slug}/sessions",
    "/api/v1/sessions/{session_id}",
    "/api/v1/sessions/{session_id}/capabilities",
    "/api/v1/sessions/{session_key}/state",
    "/api/v1/sessions/{session_key}/timing",
    "/api/v1/sessions/{session_key}/events",
    "/api/v1/sessions/{session_key}/track",
    "/api/v1/sessions/{session_key}/locations",
    "/api/v1/sessions/{session_key}/locations/samples",
    "/api/v1/sessions/{session_key}/drivers/{driver_number}/telemetry",
    "/api/v1/sessions/{session_key}/telemetry-history",
    "/api/v1/sessions/{session_key}/intelligence-detail",
    "/api/v1/race-rooms",
    "/api/v1/race-rooms/{room_slug}",
    "/api/v1/race-rooms/{room_slug}/intelligence-detail",
    "/api/v1/championship/drivers",
    "/api/v1/championship/constructors",
    "/api/v1/championship/summary",
    "/api/v1/stream/sessions/{session_key}",
}


@pytest.fixture
def schema(settings: Settings) -> dict:
    return create_app(settings).openapi()


def test_every_documented_endpoint_actually_exists(schema):
    missing = sorted(path for path in DOCUMENTED_PATHS if path not in schema["paths"])
    assert not missing, f"documented but not served: {missing}"


def test_the_public_surface_is_entirely_versioned(schema):
    unversioned = sorted(
        path
        for path in schema["paths"]
        if not path.startswith("/api/v1/") and not path.startswith("/health") and path != "/"
    )
    assert not unversioned, f"public paths outside /api/v1: {unversioned}"


def test_operation_ids_are_unique_so_generated_clients_do_not_collide(schema):
    ids = [
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert not duplicates, f"duplicate operationId: {duplicates}"


def test_documented_headers_match_the_implementation():
    assert PROXY_TOKEN_HEADER == "X-Apex-Proxy-Token"
    assert REPLAY_OPERATOR_HEADER == "X-Apex-Replay-Password"
    text = DOC.read_text()
    assert PROXY_TOKEN_HEADER in text
    assert REPLAY_OPERATOR_HEADER in text


def test_only_liveness_is_reachable_without_the_proxy_hop():
    # The document tells clients every other path requires the hop. Widening
    # this set silently would make that guidance wrong.
    assert UNPROTECTED_PATHS == frozenset({"/health/live"})
    assert "/health/live" in DOC.read_text()


@pytest.mark.parametrize(("generation", "sequence"), [(1, 0), (1, 42), (7, 1234)])
def test_room_event_ids_round_trip_generation_and_sequence(generation, sequence):
    # A native client resumes on this id. Both halves have to survive, or a
    # reset conversation resumes into the previous generation's messages.
    encoded = discussion_event_id(generation, sequence)
    assert encoded == f"{generation}:{sequence}"
    assert parse_discussion_event_id(encoded) == (generation, sequence)


@pytest.mark.parametrize("value", [None, "", "12", "abc:1", "1:x", "0:5", "-1:5"])
def test_malformed_room_event_ids_are_refused_rather_than_guessed(value):
    assert parse_discussion_event_id(value) is None


def test_documented_sse_event_names_are_the_ones_actually_emitted():
    """Every event name the document promises must be emitted somewhere."""
    session_stream = Path("app/api/streaming.py").read_text()
    room_stream = Path("app/api/room_streaming.py").read_text()
    publisher = Path("app/storage/redis.py").read_text()
    emitted = set(re.findall(r'format_sse\(\s*"(\w+)"', session_stream))
    emitted |= set(re.findall(r'_sse\(\s*"(\w+)"', room_stream))
    emitted |= set(re.findall(r'"kind":\s*"(\w+)"', publisher))

    documented = {
        "state",
        "event",
        "stream_status",
        "connection_status",
        "room_message",
        "playback_state",
        "room_status",
        "discussion_generation",
    }
    assert documented <= emitted, f"documented but never emitted: {sorted(documented - emitted)}"


def test_retry_after_is_always_expressed_in_seconds():
    # The document tells clients to read Retry-After as seconds.
    for module in ("app/api/history_routes.py", "app/api/rate_limits.py"):
        for value in re.findall(r'"Retry-After":\s*"([^"]+)"', Path(module).read_text()):
            assert value.isdigit(), f"{module} sends a non-numeric Retry-After: {value!r}"


def test_open_string_sets_are_documented_as_open():
    text = DOC.read_text()
    assert "ignore unknown fields" in text
    assert "open sets" in text


def test_the_schema_is_served_for_client_generation(schema):
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"]
