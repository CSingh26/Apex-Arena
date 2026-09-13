# SPDX-License-Identifier: AGPL-3.0-only
import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.domain.history import MAX_CHECKPOINT_BYTES, PreparedHistoryCheckpoint
from app.storage.models import SessionHistoryCheckpointRecord


class HistoryCheckpointConflictError(RuntimeError):
    """Immutable history identity/content differs; never overwrite or adopt it."""


async def insert_history_checkpoint(session, candidate: PreparedHistoryCheckpoint) -> None:
    encoded = candidate.encoded.encode("utf-8")
    if (
        len(encoded) > MAX_CHECKPOINT_BYTES
        or hashlib.sha256(encoded).hexdigest() != candidate.checksum
    ):
        raise HistoryCheckpointConflictError("Invalid history checkpoint size/checksum")
    values = {
        "session_key": candidate.session_key,
        "algorithm_version": candidate.algorithm_version,
        "source_id": candidate.source_id,
        "source_sequence": candidate.source_sequence,
        "schema_version": candidate.schema_version,
        "checksum": candidate.checksum,
        "encoded": candidate.encoded,
        "encoded_bytes": len(encoded),
    }
    inserted = await session.scalar(
        insert(SessionHistoryCheckpointRecord)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_history_checkpoint_revision")
        .returning(SessionHistoryCheckpointRecord.id)
    )
    if inserted is not None:
        return
    existing = await session.scalar(
        select(SessionHistoryCheckpointRecord).where(
            SessionHistoryCheckpointRecord.session_key == candidate.session_key,
            SessionHistoryCheckpointRecord.algorithm_version == candidate.algorithm_version,
            SessionHistoryCheckpointRecord.schema_version == candidate.schema_version,
            SessionHistoryCheckpointRecord.source_sequence == candidate.source_sequence,
        )
    )
    if existing is None or any(getattr(existing, key) != value for key, value in values.items()):
        raise HistoryCheckpointConflictError("Immutable history checkpoint conflict")
