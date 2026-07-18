# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.settings import Settings
from app.domain.models import MeetingLifecycleStatus, RaceEventType, RaceMeeting, RaceWeekendSession
from app.domain.rooms import IngestionStatus, SessionType, SourceAvailability
from app.providers.openf1 import OpenF1RestClient
from app.services.discussion import DeterministicRoomGenerator, GroundingContext
from app.services.discussion_triggers import DiscussionTriggerEvaluator
from app.services.driver_identity import DriverIdentityResolver
from app.services.event_pipeline import PipelineResult
from app.services.historical import (
    DEFAULT_HISTORICAL_ENDPOINTS,
    HistoricalDataAvailability,
    HistoricalIngestionError,
    HistoricalOpenF1Adapter,
    IngestionRunSummary,
)
from app.services.normalization import OpenF1EventNormalizer
from app.services.provider_matching import MatchConfidence, OpenF1SessionMatcher
from app.services.race_state import RaceStateEngine, SnapshotPersistResult
from app.services.raw_events import RawEventInput
from app.services.room_replay import RoomReplayCoordinator
from app.services.session_semantics import (
    normalize_qualifying_phase,
    normalize_session_type,
    phase_result_rows,
)
from tests.fixtures.openf1_day4 import (
    qualifying_historical_payloads,
    sprint_weekend_sessions,
    standard_weekend_sessions,
)


def meeting(
    *,
    race_name: str = "Belgian GP",
    country: str = "Belgium",
    circuit_name: str = "Circuit de Spa-Francorchamps",
    race_day: date = date(2026, 7, 19),
    sessions: list[RaceWeekendSession] | None = None,
) -> RaceMeeting:
    race_start = datetime.combine(race_day, datetime.min.time(), UTC).replace(hour=13)
    return RaceMeeting(
        season_year=2026,
        round_number=13,
        race_name=race_name,
        circuit_id="spa",
        circuit_name=circuit_name,
        locality="Spa",
        country=country,
        race_date=race_day,
        race_start=race_start,
        status=MeetingLifecycleStatus.COMPLETED,
        sessions=sessions or [],
    )


@pytest.mark.parametrize(
    ("provider_name", "expected"),
    [
        ("Qualifying", SessionType.QUALIFYING),
        ("Sprint Qualifying", SessionType.SPRINT_QUALIFYING),
        ("Sprint Shootout", SessionType.SPRINT_QUALIFYING),
        ("Sprint Race", SessionType.SPRINT),
        ("Race", SessionType.RACE),
        ("Practice 2", None),
    ],
)
def test_competitive_session_names_have_one_canonical_identity(
    provider_name: str, expected: SessionType | None
) -> None:
    assert normalize_session_type(provider_name) is expected
    assert SessionType.from_provider_name(provider_name) is expected


def test_qualifying_phases_only_derive_from_explicit_provider_values() -> None:
    assert normalize_qualifying_phase(2, SessionType.QUALIFYING) == "Q2"
    assert normalize_qualifying_phase("Q3", SessionType.SPRINT_QUALIFYING) == "SQ3"
    assert normalize_qualifying_phase(None, SessionType.QUALIFYING) is None
    assert normalize_qualifying_phase(1, SessionType.RACE) is None
    assert phase_result_rows(
        {"duration": [92.1, 91.8], "gap_to_leader": [0.2, 0]},
        SessionType.SPRINT_QUALIFYING,
    ) == [
        {"phase": "SQ1", "best_lap": 92.1, "gap_to_leader": 0.2},
        {"phase": "SQ2", "best_lap": 91.8, "gap_to_leader": 0},
    ]


def test_provider_matching_uses_date_circuit_country_and_name_not_exact_strings() -> None:
    race = meeting(
        sessions=[
            RaceWeekendSession(name="Qualifying", starts_at=datetime(2026, 7, 18, 14, tzinfo=UTC)),
            RaceWeekendSession(name="Race", starts_at=datetime(2026, 7, 19, 13, tzinfo=UTC)),
        ]
    )
    matcher = OpenF1SessionMatcher()

    event_match = matcher.match_meeting(race, standard_weekend_sessions())
    session_match = matcher.match_session(
        race,
        standard_weekend_sessions(),
        SessionType.QUALIFYING,
        scheduled_start=datetime(2026, 7, 18, 14, tzinfo=UTC),
    )

    assert event_match.resolved is True
    assert event_match.meeting_key == "1264"
    assert event_match.confidence in {MatchConfidence.HIGH, MatchConfidence.MEDIUM}
    assert session_match.resolved is True
    assert session_match.session_key == "9838"


def test_provider_matching_supports_sprint_shootout_and_missing_sprint_data() -> None:
    usa = meeting(
        race_name="United States Grand Prix",
        country="USA",
        circuit_name="Circuit of the Americas",
        race_day=date(2026, 10, 25),
        sessions=[
            RaceWeekendSession(
                name="Sprint Qualifying",
                starts_at=datetime(2026, 10, 23, 21, 30, tzinfo=UTC),
            )
        ],
    )
    matcher = OpenF1SessionMatcher()

    sprint = matcher.match_session(
        usa,
        sprint_weekend_sessions(),
        SessionType.SPRINT_QUALIFYING,
        scheduled_start=datetime(2026, 10, 23, 21, 30, tzinfo=UTC),
    )
    absent = matcher.match_session(
        usa,
        standard_weekend_sessions(),
        SessionType.SPRINT_QUALIFYING,
    )

    assert sprint.session_key == "9901"
    assert absent.resolved is False


def test_ambiguous_meeting_match_is_safely_unresolved() -> None:
    rows = standard_weekend_sessions()
    duplicate = [
        {**row, "meeting_key": 9999, "session_key": row["session_key"] + 100} for row in rows
    ]

    matched = OpenF1SessionMatcher().match_meeting(meeting(), rows + duplicate)

    assert matched.confidence is MatchConfidence.AMBIGUOUS
    assert matched.resolved is False


@pytest.mark.asyncio
async def test_openf1_retries_throttles_and_caches_historical_queries(settings: Settings) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[{"session_key": 9838}])

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.openf1.test/v1/"
    )
    client = OpenF1RestClient(
        settings,
        http_client,
        retry_attempts=2,
        retry_base_delay_seconds=0,
        min_request_interval_seconds=0,
        cache_ttl_seconds=60,
    )

    first = await client.sessions(session_key=9838)
    second = await client.sessions(session_key=9838)

    assert first == second == [{"session_key": 9838}]
    assert calls == 2
    await http_client.aclose()


@pytest.mark.asyncio
async def test_openf1_does_not_retry_non_retryable_responses(settings: Settings) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"detail": "not found"})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.openf1.test/v1/"
    )
    client = OpenF1RestClient(
        settings,
        http_client,
        retry_attempts=3,
        min_request_interval_seconds=0,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.laps(session_key=9838)
    assert calls == 1
    await http_client.aclose()


class FakeOpenF1:
    def __init__(
        self,
        payloads: dict[str, list[dict[str, Any]]],
        failures: set[str] | None = None,
    ) -> None:
        self.payloads = payloads
        self.failures = failures or set()
        self.queries: list[tuple[str, str]] = []

    def __getattr__(self, endpoint: str) -> Any:
        async def fetch(*, session_key: str) -> list[dict[str, Any]]:
            self.queries.append((endpoint, session_key))
            if endpoint in self.failures:
                raise httpx.ReadTimeout("fixture timeout")
            return [dict(row) for row in self.payloads.get(endpoint, [])]

        return fetch


class DedupProcessor:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.events: list[RawEventInput] = []

    async def ingest_batch(self, events: list[RawEventInput]) -> PipelineResult:
        inserted = 0
        duplicates = 0
        for event in events:
            identity = json.dumps(
                [event.provider_endpoint, event.raw_payload], sort_keys=True, default=str
            )
            if identity in self.seen:
                duplicates += 1
                continue
            self.seen.add(identity)
            self.events.append(event)
            inserted += 1
        return PipelineResult(
            raw_inserted=inserted,
            raw_duplicates=duplicates,
            normalized_inserted=inserted,
        )


class FakeRuns:
    def __init__(self) -> None:
        self.finishes: list[dict[str, Any]] = []

    async def start(self, **_: Any) -> UUID:
        return uuid4()

    async def finish(self, _: UUID, **values: Any) -> None:
        self.finishes.append(values)

    async def latest(self) -> IngestionRunSummary | None:
        return None


class FakeSnapshots:
    async def count(self, session_key: str | None = None) -> int:
        return 0


class FakeRoomAvailability:
    def __init__(self) -> None:
        self.values: list[dict[str, Any]] = []

    async def update_ingestion_availability(self, **values: Any) -> bool:
        self.values.append(values)
        return True


def historical_adapter(
    client: FakeOpenF1,
    processor: DedupProcessor | None = None,
    availability: FakeRoomAvailability | None = None,
) -> tuple[HistoricalOpenF1Adapter, DedupProcessor, FakeRuns, FakeRoomAvailability]:
    resolved_processor = processor or DedupProcessor()
    runs = FakeRuns()
    resolved_availability = availability or FakeRoomAvailability()
    return (
        HistoricalOpenF1Adapter(
            client=client,  # type: ignore[arg-type]
            processor=resolved_processor,  # type: ignore[arg-type]
            runs=runs,
            snapshots=FakeSnapshots(),
            max_records_per_endpoint=500,
            room_availability=resolved_availability,
        ),
        resolved_processor,
        runs,
        resolved_availability,
    )


@pytest.mark.asyncio
async def test_staged_historical_ingestion_is_session_scoped_replay_ready_and_idempotent() -> None:
    client = FakeOpenF1(qualifying_historical_payloads())
    adapter, processor, _, availability = historical_adapter(client)

    first = await adapter.ingest_session("9838")
    second = await adapter.ingest_session("9838")

    assert [endpoint for endpoint, key in client.queries if key == "9838"] == [
        *DEFAULT_HISTORICAL_ENDPOINTS,
        *DEFAULT_HISTORICAL_ENDPOINTS,
    ]
    assert "car_data" not in first.endpoints and "location" not in first.endpoints
    assert first.data_availability is HistoricalDataAvailability.REPLAY_READY
    assert first.status is IngestionStatus.READY
    assert first.normalized_inserted > 0
    assert second.normalized_inserted == 0
    assert second.duplicates == first.fetched_records
    assert [stage.name for stage in first.stages] == [
        "metadata",
        "timing",
        "strategy",
        "context",
        "classification",
    ]
    assert availability.values[-1]["source_availability"] is SourceAvailability.LIMITED
    assert availability.values[-1]["replay_available"] is True
    result_event = next(
        event for event in processor.events if event.provider_endpoint == "session_result"
    )
    assert result_event.raw_payload["resolved_driver_name"] == "Oscar Piastri"
    assert [row["phase"] for row in result_event.raw_payload["phase_results"]] == [
        "Q1",
        "Q2",
        "Q3",
    ]


@pytest.mark.asyncio
async def test_historical_ingestion_isolates_partial_empty_and_failed_datasets() -> None:
    partial_client = FakeOpenF1(qualifying_historical_payloads(), failures={"weather"})
    partial, _, runs, availability = historical_adapter(partial_client)

    result = await partial.ingest_session("9838")

    assert result.status is IngestionStatus.PARTIAL
    assert result.failed_endpoints == ["weather"]
    assert runs.finishes[-1]["status"] == "partial"
    assert availability.values[-1]["replay_available"] is True

    partial_client.failures.clear()
    retried = await partial.retry_failed_session(result)
    assert retried.failed_endpoints == []
    assert retried.status is IngestionStatus.READY
    assert retried.endpoint_counts["laps"] == 1
    assert retried.endpoint_counts["weather"] == 1
    assert retried.data_availability is HistoricalDataAvailability.REPLAY_READY

    empty, _, _, empty_availability = historical_adapter(FakeOpenF1({}))
    empty_result = await empty.ingest_session("9838", ["sessions", "session_result"])
    assert empty_result.status is IngestionStatus.UNAVAILABLE
    assert empty_result.data_availability is HistoricalDataAvailability.UNAVAILABLE
    assert empty_availability.values[-1]["source_availability"] is SourceAvailability.UNAVAILABLE

    failed, _, failed_runs, failed_availability = historical_adapter(
        FakeOpenF1({}, failures={"laps"})
    )
    with pytest.raises(HistoricalIngestionError):
        await failed.ingest_session("9838", ["laps"])
    assert failed_runs.finishes[-1]["status"] == "failed"
    assert failed_availability.values[-1]["ingestion_status"] is IngestionStatus.FAILED


@pytest.mark.parametrize(
    ("endpoint", "payload", "event_type"),
    [
        ("laps", {"driver_number": 81, "lap_number": 7}, RaceEventType.LAP_COMPLETED),
        ("position", {"driver_number": 81, "position": 1}, RaceEventType.POSITION_SAMPLE),
        ("intervals", {"driver_number": 81, "interval": 0}, RaceEventType.INTERVAL_SAMPLE),
        ("stints", {"driver_number": 81, "compound": "SOFT"}, RaceEventType.STINT_UPDATE),
        ("pit", {"driver_number": 81, "lap_number": 8}, RaceEventType.PIT_STOP),
        ("weather", {"rainfall": 0}, RaceEventType.WEATHER_UPDATE),
        ("session_result", {"driver_number": 81, "position": 1}, RaceEventType.SESSION_RESULT),
        ("starting_grid", {"driver_number": 81, "position": 1}, RaceEventType.STARTING_GRID),
    ],
)
def test_historical_datasets_normalize_to_replay_events(
    endpoint: str, payload: dict[str, Any], event_type: RaceEventType
) -> None:
    event = OpenF1EventNormalizer().normalize(
        RawEventInput(provider_endpoint=endpoint, session_key="9838", raw_payload=payload),
        uuid4(),
    )
    assert event.event_type is event_type


def test_qualifying_phase_annotation_does_not_hide_a_red_flag() -> None:
    event = OpenF1EventNormalizer().normalize(
        RawEventInput(
            provider_endpoint="race_control",
            session_key="9838",
            raw_payload={
                "qualifying_phase": 2,
                "flag": "RED",
                "message": "RED FLAG",
                "normalized_session_type": "QUALIFYING",
            },
        ),
        uuid4(),
    )

    assert event.event_type is RaceEventType.RED_FLAG
    assert event.payload["session_phase"] == "Q2"


def test_driver_identity_resolution_enriches_public_evidence_without_guessing_team() -> None:
    resolver = DriverIdentityResolver()
    registry = resolver.build_registry(qualifying_historical_payloads()["drivers"])

    enriched = resolver.enrich({"driver_number": 81, "lap_number": 7}, registry)

    assert enriched["resolved_driver_name"] == "Oscar Piastri"
    assert enriched["resolved_team_name"] == "McLaren"
    assert resolver.public_label(enriched, 81) == "Oscar Piastri"
    assert resolver.public_label({"driver_number": 99}, 99) == "The driver in car 99"
    assert (
        resolver.public_label(
            {"relevant_driver_state": {"81": {"full_name": "Oscar Piastri"}}},
            81,
        )
        == "Oscar Piastri"
    )


class PhaseEventRepository:
    def __init__(self) -> None:
        start = datetime(2026, 7, 18, 14, tzinfo=UTC)
        self.events = [
            OpenF1EventNormalizer()
            .normalize(
                RawEventInput(
                    provider_endpoint="race_control",
                    session_key="9838",
                    event_time=start,
                    raw_payload={
                        "qualifying_phase": phase,
                        "category": "SessionStatus",
                        "message": f"Q{phase} STARTED",
                        "normalized_session_type": "QUALIFYING",
                    },
                ),
                uuid4(),
            )
            .model_copy(
                update={
                    "sequence_number": phase,
                    "event_time": start.replace(minute=phase * 15),
                }
            )
            for phase in (1, 2, 3)
        ]

    async def list_for_session(
        self, _: str, *, after_sequence: int = 0, limit: int = 100
    ) -> list[Any]:
        return [event for event in self.events if event.sequence_number > after_sequence][:limit]


@pytest.mark.asyncio
async def test_replay_locates_provider_confirmed_phase_and_session_time_boundaries() -> None:
    replay = RoomReplayCoordinator(
        None,  # type: ignore[arg-type]
        PhaseEventRepository(),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert await replay._sequence_for_phase("9838", "Q2") == 2
    assert await replay._sequence_for_session_time("9838", 1800) == 3


class MemorySnapshots:
    def __init__(self) -> None:
        self.latest_snapshot = None

    async def insert(self, snapshot: Any) -> SnapshotPersistResult:
        self.latest_snapshot = snapshot
        return SnapshotPersistResult(record_id=snapshot.id, is_new=True)

    async def latest(self, _: str) -> Any:
        return self.latest_snapshot

    async def delete_for_session(self, _: str) -> None:
        self.latest_snapshot = None


@pytest.mark.asyncio
async def test_qualifying_state_tracks_phase_identity_best_lap_grid_and_result() -> None:
    engine = RaceStateEngine(MemorySnapshots(), snapshot_every_n_events=1)  # type: ignore[arg-type]
    normalizer = OpenF1EventNormalizer()
    raw_events = [
        ("drivers", qualifying_historical_payloads()["drivers"][0]),
        (
            "race_control",
            {
                **qualifying_historical_payloads()["race_control"][0],
                "normalized_session_type": "QUALIFYING",
            },
        ),
        (
            "laps",
            {
                **qualifying_historical_payloads()["laps"][0],
                "normalized_session_type": "QUALIFYING",
            },
        ),
        (
            "starting_grid",
            qualifying_historical_payloads()["starting_grid"][0],
        ),
        (
            "session_result",
            {
                **qualifying_historical_payloads()["session_result"][0],
                "normalized_session_type": "QUALIFYING",
            },
        ),
    ]
    for sequence, (endpoint, payload) in enumerate(raw_events, start=1):
        event = normalizer.normalize(
            RawEventInput(provider_endpoint=endpoint, session_key="9838", raw_payload=payload),
            uuid4(),
        ).model_copy(update={"sequence_number": sequence})
        await engine.apply(event)

    state = await engine.get_state("9838")
    driver = state.drivers["81"]
    assert state.session_type == "QUALIFYING"
    assert state.current_phase == "Q1"
    assert driver.full_name == "Oscar Piastri"
    assert driver.best_laps_by_phase == {"Q1": 101.245}
    assert driver.grid_position == 1 and driver.final_position == 1
    assert [row["phase"] for row in driver.phase_results] == ["Q1", "Q2", "Q3"]


def test_qualifying_commentary_uses_driver_name_plain_language_and_no_race_strategy() -> None:
    event = (
        OpenF1EventNormalizer()
        .normalize(
            RawEventInput(
                provider_endpoint="laps",
                session_key="9838",
                raw_payload={
                    "driver_number": 81,
                    "lap_number": 7,
                    "lap_duration": 101.245,
                    "normalized_session_type": "QUALIFYING",
                    "session_phase": "Q1",
                    "resolved_driver_name": "Oscar Piastri",
                },
            ),
            uuid4(),
        )
        .model_copy(update={"sequence_number": 7})
    )
    trigger = DiscussionTriggerEvaluator(
        topic_cooldown_seconds=0, agent_cooldown_seconds=0
    ).evaluate(event)
    assert trigger is not None

    generated = DeterministicRoomGenerator().generate(
        event,
        trigger,
        trigger.agent_candidates[0],
        GroundingContext(
            evidence={"event_type": "LAP_COMPLETED", **event.payload},
            data_quality="partial",
        ),
    )

    assert "Oscar Piastri" in generated.content
    assert "Driver 81" not in generated.content
    assert "pit window" not in generated.content.casefold()
    assert "elimination" in generated.content.casefold()
    assert len(generated.content) <= 420

    qualifying_pit = event.model_copy(
        update={"event_type": RaceEventType.PIT_STOP, "dedup_key": "qualifying-pit"}
    )
    assert DiscussionTriggerEvaluator().evaluate(qualifying_pit) is None
