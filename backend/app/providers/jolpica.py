# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class JolpicaPayloadError(RuntimeError):
    pass


class JolpicaClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        min_request_interval_seconds: float = 0.1,
        retry_attempts: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json", "User-Agent": "Apex-Arena/0.1"},
        )
        self.min_request_interval_seconds = max(0.0, min_request_interval_seconds)
        self.retry_attempts = max(1, retry_attempts)
        self._request_lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch_calendar(self, year: int) -> list[dict[str, Any]]:
        return self._extract_races(await self._get_json(f"{year}.json"))

    async def fetch_race_results(
        self, year: int, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._fetch_results(year, round_number, "results", "Results")

    async def fetch_qualifying_results(
        self, year: int, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._fetch_results(
            year,
            round_number,
            "qualifying",
            "QualifyingResults",
        )

    async def fetch_sprint_results(
        self, year: int, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._fetch_results(year, round_number, "sprint", "SprintResults")

    async def fetch_driver_standings(
        self, year: int, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._fetch_standings(year, round_number, "driver", "DriverStandings")

    async def fetch_constructor_standings(
        self, year: int, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._fetch_standings(
            year,
            round_number,
            "constructor",
            "ConstructorStandings",
        )

    async def _fetch_results(
        self,
        year: int,
        round_number: int | None,
        resource: str,
        result_key: str,
    ) -> list[dict[str, Any]]:
        prefix = f"{year}/{round_number}" if round_number is not None else str(year)
        path = f"{prefix}/{resource}.json"
        payloads = await self._paginated_payloads(path)
        flattened: list[dict[str, Any]] = []
        for payload in payloads:
            for race in self._extract_races(payload):
                results = race.get(result_key, [])
                if not isinstance(results, list) or not all(
                    isinstance(result, dict) for result in results
                ):
                    raise JolpicaPayloadError("Jolpica returned an unexpected results shape")
                if round_number is not None:
                    flattened.extend(dict(result) for result in results)
                    continue
                for result in results:
                    contextualized = dict(result)
                    contextualized.update(
                        {
                            "_season": race.get("season"),
                            "_round": race.get("round"),
                            "_race_name": race.get("raceName"),
                            "_date": race.get("date"),
                        }
                    )
                    flattened.append(contextualized)
        return flattened

    async def _fetch_standings(
        self,
        year: int,
        round_number: int | None,
        resource: str,
        standing_key: str,
    ) -> list[dict[str, Any]]:
        prefix = f"{year}/{round_number}" if round_number is not None else str(year)
        payload = await self._get_json(
            f"{prefix}/{resource}standings.json",
            params={"limit": 200},
        )
        try:
            lists = payload["MRData"]["StandingsTable"]["StandingsLists"]
        except (KeyError, TypeError) as exc:
            raise JolpicaPayloadError("Jolpica returned an unexpected standings shape") from exc
        if not isinstance(lists, list):
            raise JolpicaPayloadError("Jolpica returned an unexpected standings shape")
        if not lists:
            return []
        standings = lists[-1].get(standing_key) if isinstance(lists[-1], dict) else None
        if not isinstance(standings, list) or not all(
            isinstance(standing, dict) for standing in standings
        ):
            raise JolpicaPayloadError("Jolpica returned an unexpected standings shape")
        return [dict(standing) for standing in standings]

    async def _paginated_payloads(self, path: str) -> list[dict[str, Any]]:
        requested_limit = 100
        first = await self._get_json(path, params={"limit": requested_limit, "offset": 0})
        try:
            metadata = first["MRData"]
            total = int(metadata.get("total", 0))
            page_limit = max(1, int(metadata.get("limit", requested_limit)))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise JolpicaPayloadError("Jolpica returned invalid pagination metadata") from exc
        payloads = [first]
        for offset in range(page_limit, total, page_limit):
            payloads.append(
                await self._get_json(
                    path,
                    params={"limit": requested_limit, "offset": offset},
                )
            )
        return payloads

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        async with self._request_lock:
            response: httpx.Response | None = None
            for attempt in range(self.retry_attempts):
                delay = self._next_request_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self._next_request_at = time.monotonic() + self.min_request_interval_seconds
                response = await self.client.get(path, params=params)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
                if attempt + 1 < self.retry_attempts:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        server_delay = float(retry_after) if retry_after is not None else 0.0
                    except ValueError:
                        server_delay = 0.0
                    await asyncio.sleep(max(server_delay, 0.25 * (2**attempt)))
            assert response is not None
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise JolpicaPayloadError("Jolpica returned an unexpected response shape")
            return payload

    @staticmethod
    def _extract_races(payload: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            races = payload["MRData"]["RaceTable"]["Races"]
        except (KeyError, TypeError) as exc:
            raise JolpicaPayloadError("Jolpica returned an unexpected calendar shape") from exc
        if not isinstance(races, list):
            raise JolpicaPayloadError("Jolpica returned an unexpected calendar shape")
        return races
