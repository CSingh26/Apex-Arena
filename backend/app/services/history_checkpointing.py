# SPDX-License-Identifier: AGPL-3.0-only
"""One immutable encoding per cadence revision; no raw-tick history traversal."""

import hashlib
from dataclasses import dataclass

from app.domain.history import (
    HistoryReference,
    PreparedHistoryCheckpoint,
)
from app.services.history_codec import HistoryCheckpointSizeError, encode_context
from app.services.intelligence_context import SessionFactualContext


@dataclass
class HistoryCheckpointTracker:
    checkpoint: PreparedHistoryCheckpoint | None = None
    base_count: int = 0
    last_attempt_count: int = 0
    last_attempt_sequence: int = 0
    status: str = "legacy_history_unverified"

    def advance(
        self, context: SessionFactualContext, algorithm_version: str, *, terminal: bool
    ) -> HistoryReference | None:
        if context.relevant_event_id is None:
            raise ValueError("Cannot checkpoint an empty history")
        base = self.checkpoint
        if (
            self.last_attempt_sequence == 0
            or context.relevant_count - self.last_attempt_count >= 64
            or context.relevant_sequence - self.last_attempt_sequence >= 2048
            or terminal
            and context.relevant_sequence != self.last_attempt_sequence
        ):
            self.last_attempt_count = context.relevant_count
            self.last_attempt_sequence = context.relevant_sequence
            try:
                encoded = encode_context(context, algorithm_version)
            except HistoryCheckpointSizeError:
                # Factual acknowledgement continues; this is not permission to
                # serve an old B as current usable detail or hide corrupt data.
                self.status = "checkpoint_bytes"
            else:
                base = PreparedHistoryCheckpoint(
                    session_key=context.session_key,
                    algorithm_version=algorithm_version,
                    source_id=context.relevant_event_id,
                    source_sequence=context.relevant_sequence,
                    checksum=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    encoded=encoded,
                )
                self.checkpoint = base
                self.base_count = context.relevant_count
                self.status = "available"
        if base is None:
            return None
        return HistoryReference(
            algorithm_version=algorithm_version,
            base_event_id=base.source_id,
            base_sequence=base.source_sequence,
            relevant_event_id=context.relevant_event_id,
            relevant_sequence=context.relevant_sequence,
            checksum=base.checksum,
        )
