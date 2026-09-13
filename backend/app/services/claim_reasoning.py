# SPDX-License-Identifier: AGPL-3.0-only
"""Turn bounded strategy facts into positions an agent can later take back.

An analyst who states a read at lap 15 and never revisits it sounds like a
ticker. The deterministic reasoner here records what position a message
committed to, and when the underlying observation is revised or withdrawn it
produces the line a person would actually say next.

Nothing in this module invents racing conclusions. It phrases transitions that
the strategy projection already established from source evidence.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from app.domain.claims import AgentClaim, ClaimKind
from app.domain.strategy_situations import StrategySituation, StrategySituationKind

# What kind of position each observed situation commits an agent to.
CLAIM_KINDS: dict[StrategySituationKind, ClaimKind] = {
    StrategySituationKind.STINT_DIVERGENCE: ClaimKind.STRATEGY_EXPECTATION,
    StrategySituationKind.RELATIVE_PACE: ClaimKind.PACE_ASSESSMENT,
    StrategySituationKind.PIT_WINDOW: ClaimKind.STRATEGY_EXPECTATION,
    StrategySituationKind.UNDERCUT_CONDITION: ClaimKind.STRATEGY_EXPECTATION,
    StrategySituationKind.OVERCUT_CONDITION: ClaimKind.STRATEGY_EXPECTATION,
    StrategySituationKind.NEUTRALIZED_PIT_CONTEXT: ClaimKind.STRATEGY_EXPECTATION,
    StrategySituationKind.EXTRA_STOP_CONSEQUENCE: ClaimKind.STRATEGY_EXPECTATION,
    StrategySituationKind.WEATHER_CHANGE: ClaimKind.WEATHER_EXPECTATION,
}

# What the agent is on the record as having read into the observation. These
# stay hedged because the underlying situations are themselves qualified.
CLAIM_SUMMARIES: dict[StrategySituationKind, str] = {
    StrategySituationKind.STINT_DIVERGENCE: (
        "These two are on genuinely different tyre plans, so their stops should diverge."
    ),
    StrategySituationKind.RELATIVE_PACE: (
        "The recent clean laps point to a real pace difference between them."
    ),
    StrategySituationKind.PIT_WINDOW: (
        "There is a usable pit window here if the gaps behind hold."
    ),
    StrategySituationKind.UNDERCUT_CONDITION: (
        "An undercut is on if the fresher tyres deliver the required gain."
    ),
    StrategySituationKind.OVERCUT_CONDITION: (
        "Staying out looks defensible against the rival's pre-stop pace."
    ),
    StrategySituationKind.NEUTRALIZED_PIT_CONTEXT: (
        "The neutralisation changes the value of stopping right now."
    ),
    StrategySituationKind.EXTRA_STOP_CONSEQUENCE: (
        "An extra stop only pays if they can find the average gain needed."
    ),
    StrategySituationKind.WEATHER_CHANGE: (
        "The conditions moved, so compound choice is back in question."
    ),
}

# Said when the premise is retracted outright: the read cannot be assessed.
WITHDRAWAL_LINES: tuple[str, ...] = (
    "Scratch that — the evidence I leaned on has been retracted, so my read goes with it.",
    "I have to pull that one back. The observation behind it no longer stands.",
    "That call is void. The facts underneath it were withdrawn, not disproved.",
)

# Said when the observation was revised rather than retracted: the agent was
# reading real evidence that has since moved.
REVISION_LINES: tuple[str, ...] = (
    "Well, that kills my earlier theory. The evidence moved, and I have to move with it.",
    "I was wrong on that one. The updated observation does not support what I said.",
    "That changes things — my earlier read was built on numbers that have since shifted.",
)


def _variant(variants: tuple[str, ...], seed: str) -> str:
    """Pick a stable phrasing per claim so replays read identically."""
    return variants[int(sha256(seed.encode("utf-8")).hexdigest(), 16) % len(variants)]


def claim_for_situation(
    situation: StrategySituation,
    *,
    room_id,
    discussion_generation: int,
    agent_id: str,
    message_id=None,
    lap_number: int | None = None,
) -> AgentClaim | None:
    """Record the position an active situation commits this agent to.

    A withdrawn or unavailable situation asserts nothing, so it creates no
    claim; it can only close one that already exists.
    """
    if situation.status != "active" or situation.availability == "unavailable":
        return None
    kind = CLAIM_KINDS.get(situation.kind)
    if kind is None:
        return None
    return AgentClaim(
        claim_id=uuid4(),
        room_id=room_id,
        discussion_generation=discussion_generation,
        agent_id=agent_id,
        message_id=message_id,
        kind=kind,
        source_sequence=situation.sequence,
        lap_number=lap_number,
        subjects=list(situation.participants),
        summary=CLAIM_SUMMARIES[situation.kind],
        # The situation identity is what lets a later revision find this claim.
        evidence_keys=[f"situation:{situation.situation_id}", *situation.evidence_keys][:24],
        observed_at=situation.analysis_time,
    )


def situation_key(situation: StrategySituation) -> str:
    """The recall key tying a claim to the observation that produced it."""
    return f"situation:{situation.situation_id}"


def closes(claim: AgentClaim, situation: StrategySituation) -> bool:
    """Does this situation decide a standing claim?"""
    if situation_key(situation) not in claim.evidence_keys:
        return False
    # A revision carries new evidence for the same identity; a withdrawal
    # retracts the premise. Either one ends the claim as stated.
    return situation.status == "withdrawn" or situation.transition == "revised"


def revision_text(claim: AgentClaim, situation: StrategySituation) -> str:
    """Phrase the agent taking its own earlier position back."""
    lines = WITHDRAWAL_LINES if situation.status == "withdrawn" else REVISION_LINES
    return _variant(lines, f"{claim.claim_id}:{situation.revision_id}")
