# SPDX-License-Identifier: AGPL-3.0-only
"""Optional language-generation boundary.

Generation is an enhancement layer over facts that already exist. Nothing here
may mutate a fact, block factual ingestion, or become the model itself: the
deterministic reasoner decides what is true, and a provider is only ever asked
to phrase a conclusion that has already been reached.

The default provider composes nothing, so an install with no explicit opt-in
behaves exactly as it did before this module existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

# Bounds on what may be handed to a provider. These exist so a malformed or
# unusually large context cannot turn into an unbounded paid request.
MAX_PROMPT_FACTS = 12
MAX_FACT_CHARS = 240
MAX_OUTPUT_CHARS = 400


class LanguageUnavailable(Exception):
    """Raised when generation cannot be performed and the caller must fall back."""


@dataclass(frozen=True)
class LanguageRequest:
    """A bounded request to phrase an already-decided conclusion."""

    purpose: str
    agent_voice: str
    # The deterministic sentence. A provider rephrases this; it never replaces
    # its meaning, and it is what the caller falls back to.
    conclusion: str
    facts: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.conclusion.strip():
            raise ValueError("A conclusion is required; generation never originates claims")
        if len(self.facts) > MAX_PROMPT_FACTS:
            raise ValueError(f"At most {MAX_PROMPT_FACTS} supporting facts may be supplied")
        if any(len(fact) > MAX_FACT_CHARS for fact in self.facts):
            raise ValueError(f"Supporting facts are limited to {MAX_FACT_CHARS} characters")

    def cache_key(self) -> str:
        """Identical conclusions and facts must not be paid for twice."""
        return "|".join((self.purpose, self.agent_voice, self.conclusion, *self.facts))

    def estimated_tokens(self) -> int:
        """A deliberately coarse pre-flight estimate for budget accounting.

        Roughly four characters per token. This gates spend before a call; the
        provider's reported usage is authoritative afterwards.
        """
        characters = len(self.conclusion) + sum(len(fact) for fact in self.facts)
        return max(1, characters // 4) + MAX_OUTPUT_CHARS // 4


@dataclass(frozen=True)
class LanguageResult:
    """Provider output plus whatever the provider actually reported spending."""

    text: str
    tokens_used: int


@runtime_checkable
class LanguageProvider(Protocol):
    """Phrase a decided conclusion, or raise LanguageUnavailable."""

    name: str

    async def compose(
        self, request: LanguageRequest, *, timeout_seconds: float
    ) -> LanguageResult: ...


class NullLanguageProvider:
    """The default: never composes, so callers always use their own wording."""

    name = "null"

    async def compose(self, request: LanguageRequest, *, timeout_seconds: float) -> LanguageResult:
        raise LanguageUnavailable("No language provider is configured")


class OpenAILanguageProvider:
    """Chat-completions adapter.

    Constructed only when generation has been explicitly opted into; holding an
    API key is deliberately not sufficient to reach this class.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str, *, base_url: str = "https://api.openai.com/v1"):
        if not api_key:
            raise ValueError("An API key is required to construct a paid language provider")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def _messages(self, request: LanguageRequest) -> list[dict[str, str]]:
        supporting = "\n".join(f"- {fact}" for fact in request.facts)
        return [
            {
                "role": "system",
                "content": (
                    "You rephrase a motorsport analyst's already-decided conclusion in their"
                    " voice. Never introduce a fact, number, driver or outcome that is not"
                    " supplied. Never strengthen a hedge into a certainty. Reply with one or"
                    " two sentences of plain speech and nothing else."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Voice: {request.agent_voice}\n"
                    f"Conclusion: {request.conclusion}\n"
                    f"Supporting observations:\n{supporting or '- none supplied'}"
                ),
            },
        ]

    async def compose(self, request: LanguageRequest, *, timeout_seconds: float) -> LanguageResult:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": self._messages(request),
                        "max_tokens": MAX_OUTPUT_CHARS // 3,
                        "temperature": 0.7,
                    },
                )
        except httpx.HTTPError as exc:
            # The provider's own error text can carry request context; keep the
            # type only so nothing sensitive reaches the log.
            raise LanguageUnavailable(
                f"Language provider transport failed: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise LanguageUnavailable(f"Language provider returned HTTP {response.status_code}")
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
            tokens = int(usage.get("total_tokens") or request.estimated_tokens())
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LanguageUnavailable(
                f"Language provider returned an unusable payload: {type(exc).__name__}"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise LanguageUnavailable("Language provider returned empty text")
        return LanguageResult(text=text.strip(), tokens_used=max(1, tokens))
