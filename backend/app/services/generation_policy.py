# SPDX-License-Identifier: AGPL-3.0-only
"""Spend, safety and fallback policy around optional language generation.

Every path through this module has a deterministic answer. If generation is
switched off, unconfigured, over budget, too slow, saturated or simply wrong
about the facts, the caller receives the sentence the deterministic reasoner
already produced. Race timing, telemetry, strategy and factual events never
depend on any of this succeeding.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from app.core.settings import Settings
from app.providers.language import (
    MAX_OUTPUT_CHARS,
    LanguageProvider,
    LanguageRequest,
    LanguageUnavailable,
    NullLanguageProvider,
)

logger = logging.getLogger(__name__)

CACHE_ENTRIES = 256
CACHE_TTL_SECONDS = 900


@dataclass(frozen=True)
class GenerationOutcome:
    """What the room should publish, and how it was produced."""

    text: str
    generated: bool
    # Why generation did not happen. Always populated when generated is False,
    # so a degraded room can say what is actually wrong instead of going quiet.
    reason: str | None = None

    @property
    def mode(self) -> str:
        return "language_model" if self.generated else "deterministic"


class GenerationBudget:
    """Bounded call and token accounting with a rolling one-minute window."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._minute_calls: list[float] = []
        self._session_calls: dict[str, int] = {}
        self._tokens_spent = 0
        self._token_day: int | None = None

    def _day(self, now: float) -> int:
        return int(now // 86_400)

    def check(self, session_key: str, estimated_tokens: int, *, now: float) -> str | None:
        """Return a refusal reason, or None when the call is affordable."""
        cutoff = now - 60
        self._minute_calls = [stamp for stamp in self._minute_calls if stamp > cutoff]
        if len(self._minute_calls) >= self.settings.ai_max_calls_per_minute:
            return "rate_limited"
        if self._session_calls.get(session_key, 0) >= self.settings.ai_max_calls_per_session:
            return "session_budget_exhausted"
        day = self._day(now)
        if self._token_day != day:
            self._token_day = day
            self._tokens_spent = 0
        if self._tokens_spent + estimated_tokens > self.settings.ai_daily_token_budget:
            return "token_budget_exhausted"
        return None

    def reserve(self, session_key: str, *, now: float) -> None:
        self._minute_calls.append(now)
        self._session_calls[session_key] = self._session_calls.get(session_key, 0) + 1

    def record_usage(self, tokens: int, *, now: float) -> None:
        """Charge actual reported usage, which supersedes the estimate."""
        day = self._day(now)
        if self._token_day != day:
            self._token_day = day
            self._tokens_spent = 0
        self._tokens_spent += max(0, tokens)

    @property
    def tokens_spent(self) -> int:
        return self._tokens_spent


class GenerationPolicy:
    """Decide whether to spend a language call, and never fail a room if not."""

    def __init__(
        self,
        settings: Settings,
        provider: LanguageProvider | None = None,
        *,
        budget: GenerationBudget | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or NullLanguageProvider()
        self.budget = budget or GenerationBudget(settings)
        self._semaphore = asyncio.Semaphore(max(1, settings.ai_max_agents_per_event))
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        """Generation runs only on an explicit, separate opt-in.

        ``ai_enabled`` and a stored API key predate any working generation path,
        so neither may switch on paid traffic by itself after an upgrade.
        """
        return (
            self.settings.ai_generation_opt_in
            and self.settings.ai_enabled
            and not self.settings.ai_kill_switch
            and not isinstance(self.provider, NullLanguageProvider)
        )

    def status(self) -> dict[str, object]:
        """Component health that reflects the real state, not the configuration."""
        if self.settings.ai_kill_switch:
            detail = "Kill switch engaged; rooms use deterministic wording"
        elif not self.settings.ai_generation_opt_in:
            detail = "Generation is not opted in; rooms use deterministic wording"
        elif isinstance(self.provider, NullLanguageProvider):
            detail = "No language provider is configured; rooms use deterministic wording"
        elif not self.settings.ai_enabled:
            detail = "AI is disabled; rooms use deterministic wording"
        else:
            detail = f"Generation active via {self.provider.name}"
        return {
            "status": "enabled" if self.enabled else "disabled",
            "detail": detail,
            "provider": self.provider.name,
            "tokens_spent_today": self.budget.tokens_spent,
        }

    def _cached(self, key: str, *, now: float) -> str | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, text = entry
        if now - stored_at > CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return text

    def _store(self, key: str, text: str, *, now: float) -> None:
        self._cache[key] = (now, text)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)

    @staticmethod
    def _acceptable(text: str, request: LanguageRequest) -> bool:
        """Reject output that is empty, oversized or obviously not prose.

        This is a shape check, not a truth check. Grounding is enforced upstream
        by only ever asking for a rephrasing of a decided conclusion.
        """
        stripped = text.strip()
        if not stripped or len(stripped) > MAX_OUTPUT_CHARS:
            return False
        # A provider echoing the prompt scaffolding back is not usable speech.
        lowered = stripped.lower()
        return not lowered.startswith(("voice:", "conclusion:", "supporting observations"))

    async def compose(self, request: LanguageRequest, *, session_key: str) -> GenerationOutcome:
        """Phrase the conclusion, falling back to it verbatim on any problem."""
        fallback = request.conclusion
        if not self.enabled:
            reason = (
                "kill_switch"
                if self.settings.ai_kill_switch
                else "not_opted_in"
                if not self.settings.ai_generation_opt_in
                else "no_provider"
            )
            return GenerationOutcome(text=fallback, generated=False, reason=reason)

        now = time.monotonic()
        key = request.cache_key()
        cached = self._cached(key, now=now)
        if cached is not None:
            return GenerationOutcome(text=cached, generated=True, reason=None)

        refusal = self.budget.check(session_key, request.estimated_tokens(), now=time.time())
        if refusal is not None:
            return GenerationOutcome(text=fallback, generated=False, reason=refusal)

        timeout_seconds = max(0.1, self.settings.ai_request_timeout_ms / 1000)
        try:
            async with self._semaphore:
                self.budget.reserve(session_key, now=time.time())
                async with asyncio.timeout(timeout_seconds):
                    result = await self.provider.compose(request, timeout_seconds=timeout_seconds)
        except asyncio.CancelledError:
            # Shutdown and viewer disconnect must stay promptly cancellable.
            raise
        except TimeoutError:
            logger.warning("Language generation timed out purpose=%s", request.purpose)
            return GenerationOutcome(text=fallback, generated=False, reason="timeout")
        except LanguageUnavailable as exc:
            logger.warning("Language generation unavailable reason=%s", exc)
            return GenerationOutcome(text=fallback, generated=False, reason="provider_unavailable")
        except Exception as exc:
            logger.warning("Language generation failed error=%s", type(exc).__name__)
            return GenerationOutcome(text=fallback, generated=False, reason="provider_error")

        self.budget.record_usage(result.tokens_used, now=time.time())
        if not self._acceptable(result.text, request):
            logger.warning("Language generation rejected purpose=%s", request.purpose)
            return GenerationOutcome(text=fallback, generated=False, reason="rejected_output")
        self._store(key, result.text, now=now)
        return GenerationOutcome(text=result.text, generated=True, reason=None)


def build_language_provider(settings: Settings) -> LanguageProvider:
    """Construct a paid provider only when generation is explicitly opted into."""
    if not settings.ai_generation_opt_in or settings.ai_kill_switch:
        return NullLanguageProvider()
    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not api_key:
        return NullLanguageProvider()
    from app.providers.language import OpenAILanguageProvider

    return OpenAILanguageProvider(api_key, settings.openai_reaction_model)
