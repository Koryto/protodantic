"""USE CASES: every proto3 scalar type maps to python and round-trips losslessly,
including boundary values, and proto integer range constraints are enforced by
pydantic validation at model construction time.
"""

import pytest
from pydantic import ValidationError


@pytest.fixture(scope="module")
def mod(generate):
    return generate("scalars.proto")


def test_all_scalar_types_roundtrip(mod):
    """Every proto3 scalar type survives a wire round-trip unchanged."""
    sink = mod.ScalarSink(
        f_double=3.141592653589793,
        f_float=1.25,  # exactly representable in float32
        f_int32=-42,
        f_int64=-(2**40),
        f_uint32=4_000_000_000,
        f_uint64=2**60,
        f_sint32=-123,
        f_sint64=-(2**35),
        f_fixed32=7,
        f_fixed64=8,
        f_sfixed32=-9,
        f_sfixed64=-10,
        f_bool=True,
        f_string="héllo wörld 🎉",
        f_bytes=b"\xff\x00\xfe",
        r_int64=[1, -2, 3],
        r_double=[0.5, -1.5],
        r_bytes=[b"a", b"\x00"],
        m_int64={-5: "neg", 5: "pos"},
        m_bool={True: 1, False: 0},
    )
    restored = mod.ScalarSink.from_proto_bytes(sink.to_proto_bytes())
    assert restored == sink


def test_integer_boundary_values(mod):
    """int64 min/max and uint64 max survive the round-trip."""
    sink = mod.ScalarSink(
        f_int64=2**63 - 1,
        f_sint64=-(2**63),
        f_uint64=2**64 - 1,
    )
    restored = mod.ScalarSink.from_proto_bytes(sink.to_proto_bytes())
    assert restored.f_int64 == 2**63 - 1
    assert restored.f_sint64 == -(2**63)
    assert restored.f_uint64 == 2**64 - 1


def test_default_values_produce_empty_wire(mod):
    """proto3 implicit-presence defaults are not serialized (canonical behavior)."""
    assert mod.ScalarSink().to_proto_bytes() == b""


def test_int32_range_enforced_at_construction(mod):
    """A proto int32 field rejects out-of-range values when the model is built,
    not later at serialization time."""
    with pytest.raises(ValidationError):
        mod.ScalarSink(f_int32=2**40)


def test_uint_rejects_negative_at_construction(mod):
    """Unsigned proto fields reject negative values at model construction."""
    with pytest.raises(ValidationError):
        mod.ScalarSink(f_uint32=-1)


def test_uint64_range_enforced_at_construction(mod):
    """uint64 rejects values >= 2**64 at model construction."""
    with pytest.raises(ValidationError):
        mod.ScalarSink(f_uint64=2**64)


def test_string_field_rejects_wrong_type(mod):
    """Pydantic type validation applies to generated models."""
    with pytest.raises(ValidationError):
        mod.ScalarSink(f_string=12345)
