# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: E501
from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from app.domain.circuits import CircuitIntelligence, CircuitRecord, SessionWeather

logger = logging.getLogger(__name__)


class WeatherProvider(Protocol):
    async def weather(self, **filters: Any) -> list[dict[str, Any]]: ...


def _record(label: str, value: str, detail: str | None = None) -> CircuitRecord:
    return CircuitRecord(label=label, value=value, detail=detail)


def _profile(
    circuit_name: str,
    page_slug: str,
    *,
    length: str,
    first_gp: str,
    lap_record: str,
    record_holder: str,
    facts: list[str],
) -> CircuitIntelligence:
    return CircuitIntelligence(
        circuit_name=circuit_name,
        records=[
            _record("Circuit length", length),
            _record("First Grand Prix", first_gp),
            _record("Race lap record", lap_record, record_holder),
        ],
        facts=facts,
        source_url=f"https://www.formula1.com/en/racing/2026/{page_slug}",
    )


_PROFILES = [
    _profile(
        "Albert Park Grand Prix Circuit",
        "australia",
        length="5.278 km",
        first_gp="1996",
        lap_record="1:19.813",
        record_holder="Charles Leclerc · 2024",
        facts=[
            "Albert Park combines permanent track sections with roads that normally serve the surrounding park.",
            "Because the public-road surface begins the weekend relatively green, grip evolves quickly as rubber goes down.",
        ],
    ),
    _profile(
        "Shanghai International Circuit",
        "china",
        length="5.451 km",
        first_gp="2004",
        lap_record="1:32.238",
        record_holder="Michael Schumacher · 2004",
        facts=[
            "The layout was inspired by the Chinese character shang, the first character in Shanghai.",
            "Its tightening opening complex and long back straight make tyre management and braking stability decisive.",
        ],
    ),
    _profile(
        "Suzuka Circuit",
        "japan",
        length="5.807 km",
        first_gp="1987",
        lap_record="1:30.965",
        record_holder="Kimi Antonelli · 2025",
        facts=[
            "Suzuka is the only figure-eight circuit on the current Formula 1 calendar.",
            "Honda originally commissioned the track as a test facility; the Esses, Spoon and 130R still reward precision and commitment.",
        ],
    ),
    _profile(
        "Miami International Autodrome",
        "miami",
        length="5.412 km",
        first_gp="2022",
        lap_record="1:29.708",
        record_holder="Max Verstappen · 2023",
        facts=[
            "The temporary circuit wraps around Hard Rock Stadium, home of the Miami Dolphins.",
            "Designers simulated 36 layout concepts before settling on the 19-corner configuration.",
        ],
    ),
    _profile(
        "Circuit Gilles Villeneuve",
        "canada",
        length="4.361 km",
        first_gp="1978",
        lap_record="1:13.078",
        record_holder="Valtteri Bottas · 2019",
        facts=[
            "The circuit uses roads on the man-made Île Notre-Dame, created for Montreal's Expo 67.",
            "The final chicane's Wall of Champions earned its nickname after three world champions hit it during the 1999 weekend.",
        ],
    ),
    _profile(
        "Circuit de Monaco",
        "monaco",
        length="3.337 km",
        first_gp="1950",
        lap_record="1:12.909",
        record_holder="Lewis Hamilton · 2021",
        facts=[
            "Monaco is the shortest circuit on the 2026 calendar and threads through streets used by everyday traffic.",
            "Ayrton Senna remains its benchmark with six Grand Prix victories around the principality.",
        ],
    ),
    _profile(
        "Circuit de Barcelona-Catalunya",
        "barcelona-catalunya",
        length="4.657 km",
        first_gp="1991",
        lap_record="1:15.743",
        record_holder="Oscar Piastri · 2025",
        facts=[
            "The circuit's blend of long corners, braking zones and a lengthy straight has made it a traditional all-round car test.",
            "The 2023 removal of the final chicane restored the fast, sweeping final-corner configuration.",
        ],
    ),
    _profile(
        "Red Bull Ring",
        "austria",
        length="4.326 km",
        first_gp="1970",
        lap_record="1:07.924",
        record_holder="Oscar Piastri · 2025",
        facts=[
            "The compact Styrian circuit has only ten corners and one of the shortest lap times of the season.",
            "Large elevation changes and three consecutive uphill acceleration zones make traction and braking especially visible in the data.",
        ],
    ),
    _profile(
        "Silverstone Circuit",
        "great-britain",
        length="5.891 km",
        first_gp="1950",
        lap_record="1:27.097",
        record_holder="Max Verstappen · 2020",
        facts=[
            "Silverstone hosted the first round of the Formula 1 World Championship on 13 May 1950.",
            "The circuit grew from the perimeter roads of a Second World War airfield and remains famous for Maggotts and Becketts.",
        ],
    ),
    _profile(
        "Circuit de Spa-Francorchamps",
        "belgium",
        length="7.004 km",
        first_gp="1950",
        lap_record="1:44.701",
        record_holder="Sergio Perez · 2024",
        facts=[
            "Spa is the longest circuit on the current calendar and rises and falls through the Ardennes forest.",
            "Its scale means rain can affect one part of the lap while another remains dry, magnifying strategy calls.",
        ],
    ),
    _profile(
        "Hungaroring",
        "hungary",
        length="4.381 km",
        first_gp="1986",
        lap_record="1:16.627",
        record_holder="Lewis Hamilton · 2020",
        facts=[
            "The corner-heavy layout is often compared with a karting circuit and rewards rhythm more than top speed.",
            "Construction began in 1985 and the purpose-built track was ready for racing just nine months later.",
        ],
    ),
    _profile(
        "Circuit Park Zandvoort",
        "netherlands",
        length="4.259 km",
        first_gp="1952",
        lap_record="1:11.097",
        record_holder="Lewis Hamilton · 2021",
        facts=[
            "Zandvoort flows through coastal sand dunes, giving the lap its undulating, rollercoaster character.",
            "Its modernised banking reaches 18 degrees at key corners—steeper than the banking at Indianapolis.",
        ],
    ),
    _profile(
        "Autodromo Nazionale di Monza",
        "italy",
        length="5.793 km",
        first_gp="1950",
        lap_record="1:20.901",
        record_holder="Lando Norris · 2025",
        facts=[
            "Built in only 110 days in 1922, Monza was the world's third purpose-built motor racing circuit.",
            "Cars spend roughly 80% of the lap at full throttle, earning the circuit its Temple of Speed reputation.",
        ],
    ),
    _profile(
        "Madring",
        "spain",
        length="5.416 km",
        first_gp="2026",
        lap_record="Awaiting first race",
        record_holder="New for the 2026 season",
        facts=[
            "Madrid's 22-turn circuit combines public roads with purpose-built sections around the IFEMA exhibition district.",
            "Turn 12, La Monumental, is a half-kilometre banked corner designed with a gradient of up to 24 percent.",
        ],
    ),
    _profile(
        "Baku City Circuit",
        "azerbaijan",
        length="6.003 km",
        first_gp="2016",
        lap_record="1:43.009",
        record_holder="Charles Leclerc · 2019",
        facts=[
            "Baku pairs a very long shoreline straight with an exceptionally narrow passage beside the medieval city walls.",
            "Teams must balance low drag for the straight against downforce for the tight old-town section.",
        ],
    ),
    _profile(
        "Marina Bay Street Circuit",
        "singapore",
        length="4.927 km",
        first_gp="2008",
        lap_record="1:33.808",
        record_holder="Lewis Hamilton · 2025",
        facts=[
            "Singapore hosted Formula 1's first night race in 2008.",
            "Heat, humidity and a bumpy street surface make it one of the most physically demanding races of the year.",
        ],
    ),
    _profile(
        "Circuit of the Americas",
        "united-states",
        length="5.513 km",
        first_gp="2012",
        lap_record="1:36.169",
        record_holder="Charles Leclerc · 2019",
        facts=[
            "The steep climb to a wide Turn 1 is one of the most distinctive first corners in Formula 1.",
            "Its fast opening sequence takes inspiration from Silverstone and Suzuka, while the stadium section echoes Hockenheim.",
        ],
    ),
    _profile(
        "Autódromo Hermanos Rodríguez",
        "mexico",
        length="4.304 km",
        first_gp="1963",
        lap_record="1:17.774",
        record_holder="Valtteri Bottas · 2021",
        facts=[
            "At more than two kilometres above sea level, thin air changes cooling, downforce and drag calculations.",
            "The modern lap runs through a former baseball stadium, creating one of the calendar's loudest amphitheatres.",
        ],
    ),
    _profile(
        "Autódromo José Carlos Pace",
        "brazil",
        length="4.309 km",
        first_gp="1973",
        lap_record="1:10.540",
        record_holder="Valtteri Bottas · 2018",
        facts=[
            "Interlagos runs anti-clockwise and compresses major elevation changes into a short lap.",
            "Its banked final turn launches cars uphill onto the start-finish straight, often setting up late-race attacks.",
        ],
    ),
    _profile(
        "Las Vegas Strip Street Circuit",
        "las-vegas",
        length="6.201 km",
        first_gp="2023",
        lap_record="1:33.365",
        record_holder="Max Verstappen · 2025",
        facts=[
            "The 17-turn street circuit includes a high-speed run along the Las Vegas Strip.",
            "Cold desert-night conditions and Monza-like average speeds create an unusual tyre warm-up challenge.",
        ],
    ),
    _profile(
        "Losail International Circuit",
        "qatar",
        length="5.419 km",
        first_gp="2021",
        lap_record="1:22.384",
        record_holder="Lando Norris · 2024",
        facts=[
            "Losail was built primarily for motorcycle racing and features a flowing sequence of medium- and high-speed corners.",
            "Its main straight is more than one kilometre long before the heavy braking zone at Turn 1.",
        ],
    ),
    _profile(
        "Yas Marina Circuit",
        "united-arab-emirates",
        length="5.281 km",
        first_gp="2009",
        lap_record="1:26.103",
        record_holder="Max Verstappen · 2021",
        facts=[
            "The Abu Dhabi race traditionally begins in daylight and finishes beneath the circuit's floodlights.",
            "A 2021 redesign opened several corners and shortened the lap to encourage closer racing.",
        ],
    ),
]


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return " ".join(
        "".join(char for char in decomposed if char.isalnum() or char.isspace()).lower().split()
    )


_PROFILE_BY_NAME = {_normalise(profile.circuit_name): profile for profile in _PROFILES}
_ALIASES = {
    "circuit gilles villeneuve": "circuit gilles villeneuve",
    "circuit de barcelona catalunya": "circuit de barcelona catalunya",
    "autodromo nazionale di monza": "autodromo nazionale di monza",
    "autodromo jose carlos pace interlagos": "autodromo jose carlos pace",
    "yas marina": "yas marina circuit",
}


class CircuitIntelligenceService:
    def for_circuit(self, circuit_name: str) -> CircuitIntelligence:
        key = _ALIASES.get(_normalise(circuit_name), _normalise(circuit_name))
        profile = _PROFILE_BY_NAME.get(key)
        if profile is not None:
            return profile.model_copy(deep=True)
        return CircuitIntelligence(
            circuit_name=circuit_name,
            facts=[
                "Verified circuit records and facts will appear when this venue is added to the Apex Arena archive."
            ],
        )

    @property
    def supported_circuits(self) -> frozenset[str]:
        return frozenset(profile.circuit_name for profile in _PROFILES)


class CircuitWeatherService:
    def __init__(self, provider: WeatherProvider, *, timeout_seconds: float = 4.0) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    async def for_session(self, session_key: str | None) -> SessionWeather:
        if not session_key:
            return SessionWeather(
                notice="Weather will appear when OpenF1 publishes this session.",
            )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                rows = await self.provider.weather(session_key=session_key)
        except Exception as exc:
            logger.warning(
                "OpenF1 weather lookup failed session_key=%s error=%s",
                session_key,
                type(exc).__name__,
            )
            return SessionWeather(
                notice="OpenF1 weather is temporarily unavailable. Race-room data remains online.",
            )
        samples = [row for row in rows if isinstance(row, Mapping)]
        if not samples:
            return SessionWeather(
                notice="OpenF1 has not published weather samples for this session yet.",
            )
        latest = max(samples, key=lambda row: str(row.get("date") or ""))
        weather = SessionWeather(
            available=True,
            sampled_at=_datetime(latest.get("date")),
            air_temperature_c=_number(latest.get("air_temperature")),
            track_temperature_c=_number(latest.get("track_temperature")),
            rainfall=_rainfall(latest.get("rainfall")),
            humidity_percent=_number(latest.get("humidity")),
            pressure_mbar=_number(latest.get("pressure")),
            wind_speed_mps=_number(latest.get("wind_speed")),
            wind_direction_degrees=_number(latest.get("wind_direction")),
            notice="Latest weather sample published by OpenF1 for this session.",
        )
        if not any(
            value is not None
            for value in (
                weather.air_temperature_c,
                weather.track_temperature_c,
                weather.rainfall,
                weather.humidity_percent,
                weather.pressure_mbar,
                weather.wind_speed_mps,
                weather.wind_direction_degrees,
            )
        ):
            return SessionWeather(
                notice="OpenF1 returned a weather sample without usable measurements.",
            )
        return weather


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _rainfall(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    number = _number(value)
    return number > 0 if number is not None else None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
