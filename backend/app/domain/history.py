# SPDX-License-Identifier: AGPL-3.0-only
"""Compact references are not detector checkpoints or proofs of current availability."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

HISTORY_SCHEMA_VERSION = 1
COMPACT_SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024


class HistoryReference(BaseModel):
    schema_version: int = HISTORY_SCHEMA_VERSION
    algorithm_version: str = Field(min_length=1, max_length=160)
    base_event_id: UUID
    base_sequence: int = Field(ge=1)
    relevant_event_id: UUID
    relevant_sequence: int = Field(ge=1)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def ordered(self):
        if self.base_sequence > self.relevant_sequence:
            raise ValueError("History reference is not ordered")
        if (self.base_sequence == self.relevant_sequence) != (
            self.base_event_id == self.relevant_event_id
        ):
            raise ValueError("History source identity mismatch")
        return self


@dataclass(frozen=True)
class PreparedHistoryCheckpoint:
    session_key: str
    algorithm_version: str
    source_id: UUID
    source_sequence: int
    checksum: str
    encoded: str
    schema_version: int = HISTORY_SCHEMA_VERSION
