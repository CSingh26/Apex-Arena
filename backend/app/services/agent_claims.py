# SPDX-License-Identifier: AGPL-3.0-only
"""Cursor-bounded recall and revision of recorded agent positions.

The store is deliberately small. Its job is to let an agent notice that it has
already taken a position on something and revise it out loud when the facts
move, not to accumulate a transcript. Whole-conversation context is never fed
back into generation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from app.domain.claims import (
    MAX_RETAINED_CLAIMS,
    AgentClaim,
    ClaimKind,
    ClaimOutcome,
    ClaimStatus,
)
from app.storage.database import Database
from app.storage.models import AgentClaimRecord

logger = logging.getLogger(__name__)


class AgentClaimMemory:
    """Bounded claim recall scoped to one room and discussion generation."""

    def __init__(self, database: Database, *, retained: int = MAX_RETAINED_CLAIMS) -> None:
        self.database = database
        self.retained = retained

    @staticmethod
    def _claim(record: AgentClaimRecord) -> AgentClaim:
        return AgentClaim(
            claim_id=record.id,
            room_id=record.room_id,
            discussion_generation=record.discussion_generation,
            agent_id=record.agent_id,
            message_id=record.message_id,
            kind=ClaimKind(record.kind),
            status=ClaimStatus(record.status),
            outcome=ClaimOutcome(record.outcome),
            source_sequence=record.source_sequence,
            lap_number=record.lap_number,
            subjects=list(record.subjects or []),
            summary=record.summary,
            evidence_keys=list(record.evidence_keys or []),
            superseded_by=record.superseded_by,
            observed_at=record.observed_at,
        )

    async def record(self, claim: AgentClaim) -> AgentClaim:
        """Persist one asserted position and evict beyond the recall window."""
        stored = claim.model_copy(update={"claim_id": claim.claim_id or uuid4()})
        async with self.database.session_factory() as session:
            session.add(
                AgentClaimRecord(
                    id=stored.claim_id,
                    room_id=stored.room_id,
                    discussion_generation=stored.discussion_generation,
                    agent_id=stored.agent_id,
                    message_id=stored.message_id,
                    kind=stored.kind.value,
                    status=stored.status.value,
                    outcome=stored.outcome.value,
                    source_sequence=stored.source_sequence,
                    lap_number=stored.lap_number,
                    subjects=list(stored.subjects),
                    summary=stored.summary,
                    evidence_keys=list(stored.evidence_keys),
                    observed_at=stored.observed_at,
                )
            )
            await self._evict_beyond_window(session, stored.room_id, stored.discussion_generation)
            await session.commit()
        return stored

    async def _evict_beyond_window(
        self, session, room_id: UUID, discussion_generation: int
    ) -> None:
        """Keep the newest claims only; older positions fall out of recall."""
        keep = await session.scalars(
            select(AgentClaimRecord.id)
            .where(
                AgentClaimRecord.room_id == room_id,
                AgentClaimRecord.discussion_generation == discussion_generation,
            )
            .order_by(AgentClaimRecord.source_sequence.desc(), AgentClaimRecord.id.desc())
            .limit(self.retained)
        )
        retained = list(keep)
        if len(retained) < self.retained:
            return
        await session.execute(
            delete(AgentClaimRecord).where(
                AgentClaimRecord.room_id == room_id,
                AgentClaimRecord.discussion_generation == discussion_generation,
                AgentClaimRecord.id.notin_(retained),
            )
        )

    async def recall(
        self,
        room_id: UUID,
        *,
        discussion_generation: int,
        cursor: int,
        agent_id: str | None = None,
        kinds: Iterable[ClaimKind] | None = None,
        subjects: Iterable[int] | None = None,
        limit: int = 8,
    ) -> list[AgentClaim]:
        """Return open claims the room has actually reached.

        ``cursor`` is the consumed source sequence. A claim recorded at a later
        sequence is invisible, so a replay seek backwards cannot surface a
        position the viewer has not seen yet.
        """
        if cursor < 0:
            return []
        query = (
            select(AgentClaimRecord)
            .where(
                AgentClaimRecord.room_id == room_id,
                AgentClaimRecord.discussion_generation == discussion_generation,
                AgentClaimRecord.source_sequence <= cursor,
                AgentClaimRecord.status == ClaimStatus.STANDING.value,
                AgentClaimRecord.outcome == ClaimOutcome.UNDECIDED.value,
            )
            .order_by(AgentClaimRecord.source_sequence.desc(), AgentClaimRecord.id.desc())
            .limit(max(1, min(limit, self.retained)))
        )
        if agent_id is not None:
            query = query.where(AgentClaimRecord.agent_id == agent_id)
        if kinds is not None:
            values = [kind.value for kind in kinds]
            if not values:
                return []
            query = query.where(AgentClaimRecord.kind.in_(values))
        async with self.database.session_factory() as session:
            records = list(await session.scalars(query))
        claims = [self._claim(record) for record in records]
        if subjects is None:
            return claims
        wanted = set(subjects)
        if not wanted:
            return []
        # A claim about nobody in particular still applies to the session.
        return [claim for claim in claims if not claim.subjects or wanted & set(claim.subjects)]

    async def revise(
        self,
        claim_id: UUID,
        *,
        outcome: ClaimOutcome,
        superseded_by: UUID | None = None,
    ) -> None:
        """Close a claim the facts have decided, linking any replacement."""
        status = ClaimStatus.REVISED if superseded_by else ClaimStatus.WITHDRAWN
        async with self.database.session_factory() as session:
            await session.execute(
                update(AgentClaimRecord)
                .where(AgentClaimRecord.id == claim_id)
                .values(
                    status=status.value,
                    outcome=outcome.value,
                    superseded_by=superseded_by,
                )
            )
            await session.commit()

    async def reset(self, room_id: UUID, *, discussion_generation: int) -> int:
        """Drop a generation's memory when its discussion is reset."""
        async with self.database.session_factory() as session:
            # Replacement claims reference their predecessor, so clear the link
            # before deleting or the self-referencing key blocks the delete.
            await session.execute(
                update(AgentClaimRecord)
                .where(
                    AgentClaimRecord.room_id == room_id,
                    AgentClaimRecord.discussion_generation == discussion_generation,
                )
                .values(superseded_by=None)
            )
            result = await session.execute(
                delete(AgentClaimRecord).where(
                    AgentClaimRecord.room_id == room_id,
                    AgentClaimRecord.discussion_generation == discussion_generation,
                )
            )
            await session.commit()
        return int(result.rowcount or 0)
