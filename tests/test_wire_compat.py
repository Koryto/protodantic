"""USE CASES: schema evolution — models built from an older schema revision
interoperate with bytes produced by a newer one, and vice versa.
"""

import pytest


@pytest.fixture(scope="module")
def mod(generate):
    return generate("wire.proto")


def test_forward_compat_newer_bytes_parse(mod):
    """Bytes from a newer schema (extra fields) parse into the older model."""
    v2 = mod.EventV2(id="e-1", source="sensor", priority=5)
    v1 = mod.EventV1.from_proto_bytes(v2.to_proto_bytes())
    assert v1.id == "e-1"


def test_unknown_fields_dropped_on_reserialize(mod):
    """Documented limitation: fields unknown to the model are dropped when the
    model is serialized again (the model is the source of truth)."""
    v2_bytes = mod.EventV2(id="e-2", source="s", priority=9).to_proto_bytes()
    v1 = mod.EventV1.from_proto_bytes(v2_bytes)
    reparsed = mod.EventV2.from_proto_bytes(v1.to_proto_bytes())
    assert reparsed.id == "e-2"
    assert reparsed.source == ""  # unknown field was not preserved
    assert reparsed.priority == 0


def test_backward_compat_older_bytes_parse(mod):
    """Bytes from an older schema parse into the newer model with defaults."""
    v1_bytes = mod.EventV1(id="e-3").to_proto_bytes()
    v2 = mod.EventV2.from_proto_bytes(v1_bytes)
    assert v2.id == "e-3"
    assert v2.source == ""
    assert v2.priority == 0
