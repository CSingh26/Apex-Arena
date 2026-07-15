# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Any

import httpx


class JolpicaPayloadError(RuntimeError):
    pass


class JolpicaClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json", "User-Agent": "Apex-Arena/0.1"},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch_calendar(self, year: int) -> list[dict[str, Any]]:
        response = await self.client.get(f"{year}.json")
        response.raise_for_status()
        return self._extract_races(response.json())

    async def fetch_race_results(self, year: int, round_number: int) -> list[dict[str, Any]]:
        response = await self.client.get(f"{year}/{round_number}/results.json")
        response.raise_for_status()
        races = self._extract_races(response.json())
        if not races:
            return []
        results = races[0].get("Results", [])
        if not isinstance(results, list):
            raise JolpicaPayloadError("Jolpica returned an unexpected results shape")
        return results

    @staticmethod
    def _extract_races(payload: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            races = payload["MRData"]["RaceTable"]["Races"]
        except (KeyError, TypeError) as exc:
            raise JolpicaPayloadError("Jolpica returned an unexpected calendar shape") from exc
        if not isinstance(races, list):
            raise JolpicaPayloadError("Jolpica returned an unexpected calendar shape")
        return races
