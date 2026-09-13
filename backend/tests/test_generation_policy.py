# SPDX-License-Identifier: AGPL-3.0-only
"""Optional generation: opt-in safety, budgets, failure isolation and fallback.

Every test here uses a fake provider. Nothing in this file may perform, or be
able to perform, a real paid call.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from app.providers.language import (
    LanguageRequest,
    LanguageResult,
    LanguageUnavailable,
    NullLanguageProvider,
)
from app.services.generation_policy import (
    GenerationPolicy,
    build_language_provider,
)

CONCLUSION = "Hamilton is losing time in the final sector, though traffic is not ruled out."


def request(conclusion: str = CONCLUSION) -> LanguageRequest:
    return LanguageRequest(
        purpose="pace_assessment",
        agent_voice="Measured race engineer",
        conclusion=conclusion,
        facts=("Sector 3 delta +0.4s over two laps",),
    )


class FakeProvider:
    """Records calls so budget and concurrency behaviour can be asserted."""

    name = "fake"

    def __init__(self, text: str = "He's bleeding time in the last sector.", tokens: int = 50):
        self.text = text
        self.tokens = tokens
        self.calls = 0
        self.concurrent = 0
        self.peak_concurrent = 0

    async def compose(self, request, *, timeout_seconds):
        self.calls += 1
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            await asyncio.sleep(0)
            return LanguageResult(text=self.text, tokens_used=self.tokens)
        finally:
            self.concurrent -= 1


class FailingProvider:
    name = "failing"

    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    async def compose(self, request, *, timeout_seconds):
        self.calls += 1
        raise self.error


class HangingProvider:
    name = "hanging"

    async def compose(self, request, *, timeout_seconds):
        await asyncio.Event().wait()


def opted_in(settings, **overrides):
    values = {"ai_generation_opt_in": True, "ai_enabled": True, "ai_kill_switch": False}
    values.update(overrides)
    return settings.model_copy(update=values)


# --- The opt-in gate --------------------------------------------------------


async def test_existing_ai_settings_and_api_key_cannot_activate_paid_calls(settings):
    """An upgrade must not turn a pre-existing configuration into live spend."""
    legacy = settings.model_copy(
        update={
            "ai_enabled": True,
            "ai_kill_switch": False,
            "openai_api_key": SecretStr("synthetic-not-a-real-key"),
        }
    )
    assert legacy.ai_generation_opt_in is False
    assert isinstance(build_language_provider(legacy), NullLanguageProvider)

    provider = FakeProvider()
    policy = GenerationPolicy(legacy, provider)
    assert policy.enabled is False

    outcome = await policy.compose(request(), session_key="race")
    assert outcome.text == CONCLUSION
    assert outcome.generated is False
    assert outcome.reason == "not_opted_in"
    assert outcome.mode == "deterministic"
    assert provider.calls == 0


async def test_kill_switch_stops_generation_even_when_opted_in(settings):
    provider = FakeProvider()
    policy = GenerationPolicy(opted_in(settings, ai_kill_switch=True), provider)

    outcome = await policy.compose(request(), session_key="race")
    assert outcome.generated is False
    assert outcome.reason == "kill_switch"
    assert provider.calls == 0
    assert policy.status()["status"] == "disabled"


async def test_opt_in_without_a_key_still_yields_the_null_provider(settings):
    configured = opted_in(settings, openai_api_key=None)
    assert isinstance(build_language_provider(configured), NullLanguageProvider)
    policy = GenerationPolicy(configured, build_language_provider(configured))
    outcome = await policy.compose(request(), session_key="race")
    assert outcome.generated is False
    assert outcome.reason == "no_provider"


async def test_generation_is_used_when_explicitly_opted_in(settings):
    provider = FakeProvider()
    policy = GenerationPolicy(opted_in(settings), provider)
    assert policy.enabled is True

    outcome = await policy.compose(request(), session_key="race")
    assert outcome.generated is True
    assert outcome.text == provider.text
    assert outcome.mode == "language_model"
    assert provider.calls == 1


# --- Failure isolation ------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LanguageUnavailable("provider down"), "provider_unavailable"),
        (RuntimeError("unexpected"), "provider_error"),
    ],
)
async def test_provider_failure_falls_back_without_raising(settings, error, reason):
    provider = FailingProvider(error)
    policy = GenerationPolicy(opted_in(settings), provider)

    outcome = await policy.compose(request(), session_key="race")
    assert outcome.text == CONCLUSION
    assert outcome.generated is False
    assert outcome.reason == reason


async def test_a_slow_provider_cannot_hold_the_room(settings):
    policy = GenerationPolicy(opted_in(settings, ai_request_timeout_ms=100), HangingProvider())
    outcome = await asyncio.wait_for(policy.compose(request(), session_key="race"), 5)
    assert outcome.generated is False
    assert outcome.reason == "timeout"
    assert outcome.text == CONCLUSION


async def test_cancellation_is_not_swallowed(settings):
    """Shutdown and viewer disconnect must stay promptly cancellable."""
    policy = GenerationPolicy(opted_in(settings, ai_request_timeout_ms=60_000), HangingProvider())
    task = asyncio.create_task(policy.compose(request(), session_key="race"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("text", ["", "   ", "Voice: Measured race engineer", "x" * 500])
async def test_unusable_output_is_rejected_in_favour_of_the_decided_wording(settings, text):
    policy = GenerationPolicy(opted_in(settings), FakeProvider(text=text))
    outcome = await policy.compose(request(), session_key="race")
    assert outcome.text == CONCLUSION
    assert outcome.generated is False
    assert outcome.reason == "rejected_output"


# --- Spend control ----------------------------------------------------------


async def test_repeated_identical_conclusions_are_not_paid_for_twice(settings):
    provider = FakeProvider()
    policy = GenerationPolicy(opted_in(settings), provider)

    first = await policy.compose(request(), session_key="race")
    second = await policy.compose(request(), session_key="race")
    assert first.text == second.text
    assert second.generated is True
    assert provider.calls == 1


async def test_per_session_call_budget_is_enforced(settings):
    provider = FakeProvider()
    policy = GenerationPolicy(opted_in(settings, ai_max_calls_per_session=2), provider)

    for index in range(2):
        outcome = await policy.compose(request(f"{CONCLUSION} {index}"), session_key="race")
        assert outcome.generated is True

    blocked = await policy.compose(request(f"{CONCLUSION} overflow"), session_key="race")
    assert blocked.generated is False
    assert blocked.reason == "session_budget_exhausted"
    assert provider.calls == 2
    # A different session keeps its own allowance.
    other = await policy.compose(request(f"{CONCLUSION} other"), session_key="quali")
    assert other.generated is True


async def test_daily_token_budget_stops_spending(settings):
    provider = FakeProvider(tokens=10_000)
    policy = GenerationPolicy(opted_in(settings, ai_daily_token_budget=400), provider)

    first = await policy.compose(request(), session_key="race")
    assert first.generated is True
    # Reported usage supersedes the estimate and exhausts the day.
    assert policy.budget.tokens_spent == 10_000

    blocked = await policy.compose(request(f"{CONCLUSION} again"), session_key="race")
    assert blocked.generated is False
    assert blocked.reason == "token_budget_exhausted"


async def test_per_minute_rate_limit_is_enforced(settings):
    provider = FakeProvider()
    policy = GenerationPolicy(opted_in(settings, ai_max_calls_per_minute=1), provider)

    assert (await policy.compose(request("first"), session_key="race")).generated is True
    blocked = await policy.compose(request("second"), session_key="race")
    assert blocked.generated is False
    assert blocked.reason == "rate_limited"


async def test_concurrent_generation_is_bounded(settings):
    provider = FakeProvider()
    policy = GenerationPolicy(opted_in(settings, ai_max_agents_per_event=2), provider)

    await asyncio.gather(
        *(policy.compose(request(f"claim {index}"), session_key="race") for index in range(6))
    )
    assert provider.peak_concurrent <= 2


# --- Contract guards --------------------------------------------------------


def test_generation_cannot_originate_a_claim():
    with pytest.raises(ValueError):
        LanguageRequest(purpose="pace", agent_voice="Engineer", conclusion="   ")


def test_request_bounds_reject_oversized_context():
    with pytest.raises(ValueError):
        LanguageRequest(
            purpose="pace",
            agent_voice="Engineer",
            conclusion=CONCLUSION,
            facts=tuple(f"fact {index}" for index in range(50)),
        )
    with pytest.raises(ValueError):
        LanguageRequest(
            purpose="pace",
            agent_voice="Engineer",
            conclusion=CONCLUSION,
            facts=("x" * 500,),
        )


async def test_null_provider_always_refuses(settings):
    with pytest.raises(LanguageUnavailable):
        await NullLanguageProvider().compose(request(), timeout_seconds=1)
