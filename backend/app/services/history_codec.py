# SPDX-License-Identifier: AGPL-3.0-only
"""Checkpoint-only exact evidence interning; never a general payload serializer."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.domain.history import HISTORY_SCHEMA_VERSION, MAX_CHECKPOINT_BYTES
from app.services.intelligence_context import SessionFactualContext

ENCODING = "interned_facts_v1"
# 64*120*(lap+deletion+2interval+3control) + 64*(24stint+24pit)
# + 60weather + 104current-control + 120control-transitions.
MAX_FACT_REFERENCES = 57_116
# The bounded model has <400k context scalar/container nodes even when every
# optional field is populated; the flat maximum fact table adds <286k nodes.
MAX_CONTEXT_NODES = 750_000
FACT_FIELDS = ("event_id", "sequence", "observed_at", "source")


class HistoryCheckpointSizeError(ValueError):
    """Valid factual context cannot fit the declared checkpoint byte ceiling."""


class _Budget:
    def __init__(self):
        self.nodes = 0

    def visit(self, depth):
        self.nodes += 1
        if self.nodes > MAX_CONTEXT_NODES or depth > 32:
            raise ValueError("Checkpoint node/depth limit")


def encode_context(context: SessionFactualContext, algorithm_version: str) -> str:
    facts, indices = [], {}
    budget = _Budget()

    def pack(value, depth=0):
        budget.visit(depth)
        if isinstance(value, dict):
            keys = set(value)
            if keys in (set(FACT_FIELDS), {*FACT_FIELDS, "semantics"}):
                reference = tuple(value[key] for key in FACT_FIELDS)
                index = indices.get(reference)
                if index is None:
                    if len(facts) >= MAX_FACT_REFERENCES:
                        raise ValueError("Checkpoint reference limit")
                    index = len(facts)
                    indices[reference] = index
                    facts.append(reference)
                return {
                    "$fact": index,
                    **(
                        {"semantics": pack(value["semantics"], depth + 1)}
                        if "semantics" in value
                        else {}
                    ),
                }
            return {key: pack(child, depth + 1) for key, child in value.items()}
        if isinstance(value, list):
            return [pack(child, depth + 1) for child in value]
        return value

    packed = pack(context.model_dump(mode="json", exclude_defaults=True))
    encoded = json.dumps(
        {
            "kind": "factual_history_control_only",
            "schema_version": HISTORY_SCHEMA_VERSION,
            "algorithm_version": algorithm_version,
            "encoding": ENCODING,
            "facts": facts,
            "context": packed,
        },
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        raise HistoryCheckpointSizeError("Factual checkpoint byte limit exceeded")
    return encoded


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate checkpoint key")
        result[key] = value
    return result


def decode_context(encoded: str, algorithm_version: str) -> SessionFactualContext:
    if len(encoded.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        raise ValueError("Checkpoint byte limit")
    try:
        envelope = json.loads(encoded, object_pairs_hook=_unique_object)
        if (
            not isinstance(envelope, dict)
            or set(envelope)
            != {"kind", "schema_version", "algorithm_version", "encoding", "facts", "context"}
            or envelope["kind"] != "factual_history_control_only"
            or type(envelope["schema_version"]) is not int
            or envelope["schema_version"] != HISTORY_SCHEMA_VERSION
            or envelope["algorithm_version"] != algorithm_version
            or envelope["encoding"] != ENCODING
        ):
            raise ValueError("Incompatible checkpoint envelope")
        facts = envelope["facts"]
        if not isinstance(facts, list) or len(facts) > MAX_FACT_REFERENCES:
            raise ValueError("Checkpoint fact table limit")
        budget = _Budget()
        budget.nodes = len(facts) * 5
        for row in facts:
            if (
                not isinstance(row, list)
                or len(row) != 4
                or not isinstance(row[0], str)
                or type(row[1]) is not int
                or row[1] < 1
                or not isinstance(row[2], str)
                or not isinstance(row[3], str)
                or len(row[3]) > 30
            ):
                raise ValueError("Invalid checkpoint fact")
            UUID(row[0])
            observed = datetime.fromisoformat(row[2].replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError("Unzoned checkpoint fact")

        def unpack(value, depth=0):
            budget.visit(depth)
            if isinstance(value, dict):
                if "$fact" in value:
                    if set(value) not in ({"$fact"}, {"$fact", "semantics"}):
                        raise ValueError("Invalid checkpoint reference tag")
                    index = value["$fact"]
                    if type(index) is not int or not 0 <= index < len(facts):
                        raise ValueError("Invalid checkpoint reference index")
                    result = dict(zip(FACT_FIELDS, facts[index], strict=True))
                    if "semantics" in value:
                        result["semantics"] = unpack(value["semantics"], depth + 1)
                    return result
                if any(key.startswith("$") for key in value):
                    raise ValueError("Unknown checkpoint reference tag")
                return {key: unpack(child, depth + 1) for key, child in value.items()}
            if isinstance(value, list):
                return [unpack(child, depth + 1) for child in value]
            return value

        return SessionFactualContext.model_validate(unpack(envelope["context"]))
    except (KeyError, TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("Invalid checkpoint representation") from exc
