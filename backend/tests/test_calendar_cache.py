# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.providers.jolpica import JolpicaPayloadError
from app.services.season import SeasonService
from tests.test_jolpica import calendar_payload


async def test_calendar_coalesces_requests_but_recomputes_weekend_status(settings):
    class Provider:
        calls = 0

        async def fetch_calendar(self, year):
            self.calls += 1
            await asyncio.sleep(0)
            return calendar_payload()["MRData"]["RaceTable"]["Races"]

    provider = Provider()
    service = SeasonService(settings, provider)
    before, during = await asyncio.gather(
        service.calendar(2026, now=datetime(2026, 7, 15, tzinfo=UTC)),
        service.calendar(2026, now=datetime(2026, 7, 17, 12, tzinfo=UTC)),
    )
    assert provider.calls == 1
    assert before[1].status.value == "upcoming"
    assert during[1].status.value == "live"
    before[1].race_name = "caller mutation"
    assert (await service.calendar(2026))[1].race_name == "Belgian Grand Prix"


async def test_calendar_stale_fallback_is_labelled_bounded_and_retry_coalesced(
    settings, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr("app.services.season.time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Provider:
        calls = 0

        async def fetch_calendar(self, year):
            self.calls += 1
            if self.calls > 1:
                raise httpx.ConnectError("synthetic outage")
            return calendar_payload()["MRData"]["RaceTable"]["Races"]

    provider = Provider()
    service = SeasonService(settings, provider)
    first = await service.calendar(2026)
    clock[0] += 601
    stale = await service.calendar(2026)
    assert stale[0].source_stale is True
    assert stale[0].source_age_seconds == 601
    assert stale[0].source_checked_at == first[0].source_checked_at
    await service.calendar(2026)
    assert provider.calls == 2
    clock[0] = 1000 + 86399
    await service.calendar(2026)
    clock[0] += 2
    with pytest.raises(httpx.ConnectError):
        await service.calendar(2026)


async def test_calendar_evicts_old_years_and_rejects_oversized_refresh(settings, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.services.season.time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Provider:
        calls = 0
        oversized = False

        async def fetch_calendar(self, year):
            self.calls += 1
            races = calendar_payload()["MRData"]["RaceTable"]["Races"]
            return races * 1000 if self.oversized else races

    provider = Provider()
    service = SeasonService(settings, provider)
    for year in range(2020, 2030):
        await service.calendar(year)
    await service.calendar(2020)
    assert provider.calls == 11
    provider.oversized = True
    clock[0] += 601
    result = await service.calendar(2020)
    assert len(result) == 2
    assert result[0].source_stale is True


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("failure", [httpx.ConnectError, TimeoutError])
async def test_calendar_unavailable_requests_share_bounded_retry(
    settings, monkeypatch, expired, failure
):
    clock = [1000.0]
    monkeypatch.setattr("app.services.season.time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Provider:
        calls = 0
        unavailable = False

        async def fetch_calendar(self, year):
            self.calls += 1
            await asyncio.sleep(0)
            if self.unavailable:
                raise failure("synthetic outage")
            return calendar_payload()["MRData"]["RaceTable"]["Races"]

    provider = Provider()
    service = SeasonService(settings, provider)
    if expired:
        await service.calendar(2026)
        clock[0] += 86401
    provider.unavailable = True
    calls = provider.calls
    results = await asyncio.gather(
        service.calendar(2026), service.calendar(2026), return_exceptions=True
    )
    assert all(isinstance(result, (failure, JolpicaPayloadError)) for result in results)
    assert provider.calls == calls + 1
    clock[0] += 31
    provider.unavailable = False
    assert len(await service.calendar(2026)) == 2
    assert provider.calls == calls + 2
