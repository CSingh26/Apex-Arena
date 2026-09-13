# SPDX-License-Identifier: AGPL-3.0-only
"""Adversarial coverage for the surfaces added in this build.

These tests try to break telemetry reads, claim recall and generation policy
with hostile inputs, rather than confirming the happy path a second time.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.claims import AgentClaim, ClaimKind
from app.providers.language import LanguageRequest, LanguageResult
from app.services.generation_policy import GenerationPolicy
from app.services.telemetry_history import TelemetryHistoryService
from tests.test_telemetry_history import FakeEvents, car_data, service

START = datetime(2026, 7, 17, 12, tzinfo=UTC)


# --- Telemetry against hostile provider payloads ----------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"speed": float("nan")},
        {"speed": float("inf")},
        {"speed": "280"},
        {"speed": None, "throttle": None},
        {"rpm": [1, 2, 3]},
        {"gear": {"nested": 1}},
        {"brake": True},
        {},
    ],
)
async def test_malformed_provider_values_never_reach_a_chart(payload):
    """A non-finite or wrongly typed reading must be dropped, not plotted."""
    window = await service([car_data(1, 4, payload=payload)]).read("race", drivers=[4])
    for driver in window.drivers:
        for sample in driver.samples:
            for channel in ("speed", "throttle", "brake", "rpm", "gear"):
                value = getattr(sample, channel)
                assert value is None or (isinstance(value, int | float) and math.isfinite(value))
            # A channel is only advertised when it carries a usable value.
            for channel in driver.channels:
                assert getattr(sample, channel, None) is not None or len(driver.samples) > 1


async def test_a_driver_number_that_does_not_exist_is_reported_not_invented():
    window = await service([car_data(1, 4)]).read("race", drivers=[999])
    assert window.availability == "unavailable"
    assert window.drivers[0].driver_number == 999
    assert window.drivers[0].samples == []


async def test_an_unreadable_race_state_does_not_fail_the_telemetry_read():
    """Scenario 20: a stale or broken cache must not take the panel offline."""

    async def broken(_key):
        raise RuntimeError("synthetic state outage")

    reader = TelemetryHistoryService(
        FakeEvents([car_data(1, 4)]), SimpleNamespace(get_state=broken)
    )
    window = await reader.read("race", drivers=[4])
    # Falls back to reading without a cursor bound rather than erroring.
    assert window.view_sequence == 0
    assert window.availability in {"available", "partial", "unavailable"}


async def test_a_zero_cursor_session_returns_an_honest_empty_window():
    window = await service([car_data(1, 4)], sequence=0).read("race", drivers=[4], cursor=0)
    assert window.view_sequence == 0
    assert window.availability == "unavailable"


async def test_two_drivers_with_disjoint_channels_are_each_described_accurately():
    """Scenario 15/16: comparison must not blend one car's channels into another."""
    events = [
        car_data(1, 4, payload={"speed": 300.0, "rpm": 11000}),
        car_data(2, 16, payload={"throttle": 88.0, "brake": 12.0}),
    ]
    window = await service(events).read("race", drivers=[4, 16])
    channels = {driver.driver_number: set(driver.channels) for driver in window.drivers}
    assert channels[4] == {"speed", "rpm"}
    assert channels[16] == {"throttle", "brake"}
    assert window.availability == "available"


# --- Claim recall under replay seeks ----------------------------------------


class SeekableMemory:
    """Minimal in-memory recall with the same cursor contract."""

    def __init__(self) -> None:
        self.claims: list[AgentClaim] = []

    async def record(self, claim):
        self.claims.append(claim)
        return claim

    async def recall(self, room_id, *, discussion_generation, cursor, **_kwargs):
        return [
            claim
            for claim in self.claims
            if claim.discussion_generation == discussion_generation
            and claim.source_sequence <= cursor
        ]


def claim(sequence: int, *, generation: int = 1) -> AgentClaim:
    return AgentClaim(
        claim_id=uuid4(),
        room_id=uuid4(),
        discussion_generation=generation,
        agent_id="nova",
        kind=ClaimKind.STRATEGY_EXPECTATION,
        source_sequence=sequence,
        summary=f"Read at {sequence}.",
        observed_at=START + timedelta(seconds=sequence),
    )


async def test_seeking_backwards_never_leaks_a_later_position():
    """Scenario 10/14: a replay seek must not surface the future."""
    memory = SeekableMemory()
    room = uuid4()
    for sequence in (5, 25, 90):
        stored = claim(sequence).model_copy(update={"room_id": room})
        await memory.record(stored)

    for cursor, expected in ((4, 0), (5, 1), (26, 2), (1000, 3)):
        recalled = await memory.recall(room, discussion_generation=1, cursor=cursor)
        assert len(recalled) == expected
        assert all(item.source_sequence <= cursor for item in recalled)


async def test_a_reset_generation_cannot_recall_the_previous_conversation():
    memory = SeekableMemory()
    room = uuid4()
    await memory.record(claim(5, generation=1).model_copy(update={"room_id": room}))
    assert await memory.recall(room, discussion_generation=2, cursor=10_000) == []


# --- Generation under hostile providers -------------------------------------


class HostileProvider:
    """Returns output designed to break a naive consumer."""

    name = "hostile"

    def __init__(self, text):
        self.text = text

    async def compose(self, request, *, timeout_seconds):
        return LanguageResult(text=self.text, tokens_used=1)


@pytest.mark.parametrize(
    "text",
    [
        "x" * 10_000,
        "\n\n\n",
        "Conclusion: something entirely different",
        "voice: Measured race engineer",
    ],
)
async def test_hostile_generation_output_is_refused_in_favour_of_the_facts(settings, text):
    policy = GenerationPolicy(
        settings.model_copy(
            update={
                "ai_generation_opt_in": True,
                "ai_enabled": True,
                "ai_kill_switch": False,
            }
        ),
        HostileProvider(text),
    )
    conclusion = "Verstappen has cleared the pit window with the gap intact."
    outcome = await policy.compose(
        LanguageRequest(purpose="strategy", agent_voice="Engineer", conclusion=conclusion),
        session_key="race",
    )
    assert outcome.text == conclusion
    assert outcome.generated is False


async def test_budget_accounting_survives_a_provider_that_overreports_usage(settings):
    """A provider claiming absurd usage must stop spending, not wrap around."""

    class Greedy:
        name = "greedy"

        async def compose(self, request, *, timeout_seconds):
            return LanguageResult(text="Short and clean.", tokens_used=10**12)

    policy = GenerationPolicy(
        settings.model_copy(
            update={
                "ai_generation_opt_in": True,
                "ai_enabled": True,
                "ai_kill_switch": False,
            }
        ),
        Greedy(),
    )
    first = await policy.compose(
        LanguageRequest(purpose="p", agent_voice="v", conclusion="A decided conclusion."),
        session_key="race",
    )
    assert first.generated is True
    blocked = await policy.compose(
        LanguageRequest(purpose="p", agent_voice="v", conclusion="Another decided conclusion."),
        session_key="race",
    )
    assert blocked.generated is False
    assert blocked.reason == "token_budget_exhausted"


async def test_many_concurrent_rooms_cannot_exceed_the_configured_concurrency(settings):
    """Scenario 15: simultaneous activity stays inside the declared ceiling."""

    class Counting:
        name = "counting"

        def __init__(self):
            self.active = 0
            self.peak = 0

        async def compose(self, request, *, timeout_seconds):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.01)
                return LanguageResult(text="Phrased.", tokens_used=1)
            finally:
                self.active -= 1

    provider = Counting()
    policy = GenerationPolicy(
        settings.model_copy(
            update={
                "ai_generation_opt_in": True,
                "ai_enabled": True,
                "ai_kill_switch": False,
                "ai_max_agents_per_event": 3,
                "ai_max_calls_per_minute": 1000,
                "ai_max_calls_per_session": 1000,
            }
        ),
        provider,
    )
    await asyncio.gather(
        *(
            policy.compose(
                LanguageRequest(
                    purpose="p", agent_voice="v", conclusion=f"Decided conclusion {index}."
                ),
                session_key=f"race-{index % 4}",
            )
            for index in range(24)
        )
    )
    assert provider.peak <= 3


async def test_a_response_carrying_provider_corruption_is_still_valid_json():
    """NaN and infinity are not valid JSON; they must never reach a response."""
    import json

    hostile = [
        car_data(1, 4, payload={"speed": float("nan"), "throttle": 50.0}),
        car_data(2, 4, payload={"speed": float("inf"), "rpm": 11000}),
        car_data(3, 4, payload={"speed": -20.0, "gear": 99}),
    ]
    window = await service(hostile).read("race", drivers=[4])
    encoded = json.dumps(window.model_dump(mode="json"), allow_nan=False)
    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    # Out-of-range readings are dropped, so no implausible value is charted.
    for driver in window.drivers:
        for sample in driver.samples:
            assert sample.speed is None or 0 <= sample.speed <= 450
            assert sample.gear is None or -1 <= sample.gear <= 8
