# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
import json

import pytest

from app.services.intelligence_context import SessionFactualContext
from tests.test_race_history import fact


def codec():
    assert importlib.util.find_spec("app.services.history_codec") is not None, (
        "bounded checkpoint codec missing"
    )
    from app.services import history_codec

    return history_codec


def test_interned_checkpoint_is_exact_deterministic_and_detached():
    module = codec()
    context = SessionFactualContext(session_key="history-test")
    context.advance_owned(fact(1, lap=1, lap_duration=90, date_start="2026-09-12T00:00:00Z"))
    context.history.drivers["4"].laps[0].evidence.source = '🛞"\\\n' * 5
    encoded = module.encode_context(context, "test-v1")
    assert encoded == module.encode_context(context, "test-v1")
    restored = module.decode_context(encoded, "test-v1")
    assert restored == context
    restored.history.drivers["4"].laps[0].evidence.source = "changed"
    assert restored != context
    context.advance_owned(fact(2, lap=2, lap_duration=91))
    again = module.decode_context(encoded, "test-v1")
    again.advance_owned(fact(2, lap=2, lap_duration=91))
    assert again == context


@pytest.mark.parametrize("index", [True, -1, 1, 0.0, "0", {"$fact": 0}])
def test_invalid_fact_index_is_rejected(index):
    module = codec()
    context = SessionFactualContext(session_key="history-test")
    context.advance_owned(fact(1, lap=1, lap_duration=90))
    envelope = json.loads(module.encode_context(context, "test-v1"))
    envelope["context"]["history"]["drivers"]["4"]["laps"][0]["evidence"] = {"$fact": index}
    with pytest.raises(ValueError):
        module.decode_context(json.dumps(envelope), "test-v1")


def test_duplicate_id_with_different_reference_value_is_not_merged():
    module = codec()
    context = SessionFactualContext(session_key="history-test")
    context.advance_owned(fact(1, lap=1, lap_duration=90, date_start="2026-09-12T00:00:00Z"))
    lap = context.history.drivers["4"].laps[0]
    lap.interval_evidence = [lap.evidence.model_copy(update={"source": "other-source"})]
    envelope = json.loads(module.encode_context(context, "test-v1"))
    assert len(envelope["facts"]) == 2
    assert module.decode_context(json.dumps(envelope), "test-v1") == context


@pytest.mark.parametrize("mutation", ["cycle", "tag", "table", "version", "nodes"])
def test_malformed_checkpoint_envelope_is_rejected(mutation):
    module = codec()
    context = SessionFactualContext(session_key="history-test")
    context.advance_owned(fact(1, lap=1, lap_duration=90))
    envelope = json.loads(module.encode_context(context, "test-v1"))
    if mutation == "cycle":
        envelope["facts"][0] = [{"$fact": 0}, 1, "2026-09-12T00:00:00Z", "source"]
    elif mutation == "tag":
        envelope["context"]["unknown"] = {"$fact": 0, "extra": True}
    elif mutation == "table":
        envelope["facts"] *= module.MAX_FACT_REFERENCES + 1
    elif mutation == "version":
        envelope["encoding"] = "unknown"
    else:
        envelope["context"]["unknown"] = [None] * (module.MAX_CONTEXT_NODES + 1)
    with pytest.raises(ValueError):
        module.decode_context(json.dumps(envelope), "test-v1")
