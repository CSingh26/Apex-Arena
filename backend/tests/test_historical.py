# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.event_pipeline import PipelineResult
from app.services.historical import HistoricalOpenF1Adapter, IngestionRunSummary
from app.services.raw_events import RawEventInput


class FakeOpenF1Client:
    def __init__(self, payloads: dict[str, list[dict[str, Any]]]) -> None:
        self.payloads = payloads
        self.queries: list[tuple[str, str]] = []

    def __getattr__(self, endpoint: str) -> Any:
        async def fetch(*, session_key: str) -> list[dict[str, Any]]:
            self.queries.append((endpoint, session_key))
            return self.payloads.get(endpoint, [])

        return fetch


class FakeProcessor:
    def __init__(self) -> None:
        self.events: list[RawEventInput] = []

    async def ingest_batch(self, events: list[RawEventInput]) -> PipelineResult:
        self.events.extend(events)
        return PipelineResult(
            raw_inserted=len(events),
            raw_duplicates=1,
            normalized_inserted=len(events),
        )


class FakeRuns:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.finished: dict[str, Any] | None = None

    async def start(self, *, provider: str, session_key: str, metadata: dict[str, Any]) -> UUID:
        assert provider == "openf1"
        assert metadata["adapter"] == "historical_rest"
        return self.run_id

    async def finish(self, run_id: UUID, **values: Any) -> None:
        assert run_id == self.run_id
        self.finished = values

    async def latest(self) -> IngestionRunSummary | None:
        return None


class FakeSnapshots:
    def __init__(self) -> None:
        self.calls = 0

    async def count(self, session_key: str | None = None) -> int:
        self.calls += 1
        return 2 if self.calls == 1 else 4


@pytest.mark.asyncio
async def test_historical_records_use_unified_pipeline_in_event_time_order() -> None:
    client = FakeOpenF1Client(
        {
            "laps": [
                {
                    "_id": 2,
                    "session_key": 9839,
                    "driver_number": 4,
                    "date_start": "2026-07-19T13:02:00Z",
                }
            ],
            "position": [
                {
                    "_id": 1,
                    "session_key": 9839,
                    "driver_number": 4,
                    "date": "2026-07-19T13:01:00Z",
                }
            ],
        }
    )
    processor = FakeProcessor()
    runs = FakeRuns()
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=processor,  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
    )

    result = await adapter.ingest_session("9839", ["laps", "position"])

    assert [event.provider_endpoint for event in processor.events] == ["position", "laps"]
    assert all(event.is_replay for event in processor.events)
    assert result.raw_inserted == 2
    assert result.duplicates == 1
    assert result.normalized_inserted == 2
    assert result.snapshots == 2
    assert runs.finished and runs.finished["status"] == "completed"


@pytest.mark.asyncio
async def test_historical_ingestion_caps_each_endpoint() -> None:
    records = [{"_id": number, "session_key": 9839} for number in range(5)]
    processor = FakeProcessor()
    adapter = HistoricalOpenF1Adapter(
        client=FakeOpenF1Client({"weather": records}),  # type: ignore[arg-type]
        processor=processor,  # type: ignore[arg-type]
        runs=FakeRuns(),
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=2,
    )

    result = await adapter.ingest_session("9839", ["weather"])

    assert result.fetched_records == 2
    assert len(processor.events) == 2


def test_historical_ingestion_rejects_unsupported_endpoints() -> None:
    with pytest.raises(ValueError, match="Unsupported historical endpoints"):
        HistoricalOpenF1Adapter._validate_endpoints(["laps", "secrets"])


def test_historical_payload_time_is_timezone_aware() -> None:
    parsed = HistoricalOpenF1Adapter._payload_time({"date": "2026-07-19T13:00:00"})

    assert parsed == datetime(2026, 7, 19, 13, tzinfo=UTC)
