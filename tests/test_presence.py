"""USE CASES: field presence is faithfully modeled — None means "unset on the
wire", never conflated with a zero default — and oneofs are mutually exclusive.
"""

import pytest
from pydantic import ValidationError


@pytest.fixture(scope="module")
def mod(generate):
    return generate("presence.proto")


def test_optional_unset_is_none(mod):
    """Unset optional fields come back as None, not zero-values."""
    restored = mod.Carrier.from_proto_bytes(mod.Carrier().to_proto_bytes())
    assert restored.opt_int is None
    assert restored.opt_str is None
    assert restored.opt_bool is None
    assert restored.opt_bytes is None
    assert restored.inner is None


def test_optional_zero_is_distinct_from_unset(mod):
    """Explicitly-set zero values keep their presence bit across the wire."""
    carrier = mod.Carrier(opt_int=0, opt_str="", opt_bool=False, opt_bytes=b"")
    msg = carrier.to_proto()
    assert msg.HasField("opt_int")
    assert msg.HasField("opt_str")
    restored = mod.Carrier.from_proto_bytes(carrier.to_proto_bytes())
    assert restored.opt_int == 0
    assert restored.opt_str == ""
    assert restored.opt_bool is False
    assert restored.opt_bytes == b""


def test_singular_message_presence(mod):
    """A set-but-empty submessage is distinguishable from an unset one."""
    with_empty = mod.Carrier(inner=mod.Inner())
    restored = mod.Carrier.from_proto_bytes(with_empty.to_proto_bytes())
    assert restored.inner == mod.Inner()
    assert restored.inner is not None


@pytest.mark.parametrize(
    "field,value",
    [("text", "hi"), ("number", 42)],
)
def test_oneof_scalar_members_roundtrip(mod, field, value):
    """Each oneof member round-trips and reports via WhichOneof."""
    carrier = mod.Carrier(**{field: value})
    assert carrier.to_proto().WhichOneof("payload") == field
    restored = mod.Carrier.from_proto_bytes(carrier.to_proto_bytes())
    assert getattr(restored, field) == value


def test_oneof_message_member_roundtrip(mod):
    """Message-typed oneof members work too."""
    carrier = mod.Carrier(boxed=mod.Inner(n=7))
    restored = mod.Carrier.from_proto_bytes(carrier.to_proto_bytes())
    assert restored.boxed == mod.Inner(n=7)
    assert restored.text is None
    assert restored.number is None


def test_oneof_none_set(mod):
    """A oneof with no member set round-trips as all-None."""
    restored = mod.Carrier.from_proto_bytes(mod.Carrier().to_proto_bytes())
    assert restored.text is None and restored.boxed is None and restored.number is None


def test_oneof_mutual_exclusion_validated(mod):
    """Setting two members of the same oneof is a validation error — the model
    enforces proto semantics instead of silently letting one value win."""
    with pytest.raises(ValidationError):
        mod.Carrier(text="hi", number=42)
