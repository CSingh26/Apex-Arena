# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded agent claim memory.

An agent that states a position at lap 15 and ignores it at lap 27 does not
sound like an analyst. These records let the deterministic reasoner recall what
was actually asserted, and revise it when the facts move, without replaying the
whole conversation into a language model.

Recall is cursor-bounded: a claim is only visible once the consumed source
cursor has reached the fact that produced it. Replay seeks and restarts
therefore cannot leak a position the room has not yet reached.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ClaimText = Annotated[str, Field(min_length=1, max_length=400)]
Code = Annotated[str, Field(min_length=1, max_length=80)]

# One room generation keeps a bounded working memory. This is a recall window,
# not an archive; the durable message history remains the record of what was
# said.
MAX_RETAINED_CLAIMS = 64
MAX_EVIDENCE_KEYS = 24


class ClaimKind(StrEnum):
    """What kind of position the agent took."""

    STRATEGY_EXPECTATION = "strategy_expectation"
    PACE_ASSESSMENT = "pace_assessment"
    BATTLE_EXPECTATION = "battle_expectation"
    WEATHER_EXPECTATION = "weather_expectation"
    INCIDENT_READING = "incident_reading"


class ClaimStatus(StrEnum):
    """Whether the claim still stands at the current cursor."""

    STANDING = "standing"
    # The agent revised its own position after the facts moved.
    REVISED = "revised"
    # The factual premise was retracted, so the claim cannot be assessed.
    WITHDRAWN = "withdrawn"


class ClaimOutcome(StrEnum):
    """Whether observed facts have borne the claim out.

    Deliberately conservative: most claims never reach a decidable outcome, and
    an undecided claim must not be reported as correct.
    """

    UNDECIDED = "undecided"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"


class AgentClaim(BaseModel):
    """One recorded agent position, addressable by the evidence behind it."""

    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    room_id: UUID
    discussion_generation: int = Field(ge=1)
    agent_id: Code
    message_id: UUID | None = None
    kind: ClaimKind
    status: ClaimStatus = ClaimStatus.STANDING
    outcome: ClaimOutcome = ClaimOutcome.UNDECIDED
    # The claim is only recalled once the room's consumed cursor reaches this
    # sequence, which is what keeps replay seeks honest.
    source_sequence: int = Field(ge=0)
    lap_number: int | None = Field(default=None, ge=0)
    subjects: list[int] = Field(default_factory=list, max_length=4)
    summary: ClaimText
    evidence_keys: list[Code] = Field(default_factory=list, max_length=MAX_EVIDENCE_KEYS)
    superseded_by: UUID | None = None
    observed_at: AwareDatetime

    @property
    def is_open(self) -> bool:
        """A standing, undecided claim is the only kind worth revisiting."""
        return self.status is ClaimStatus.STANDING and self.outcome is ClaimOutcome.UNDECIDED
