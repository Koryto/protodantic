"""USE CASES: edge values of every proto3 scalar type survive protodantic
unchanged, judged against the protobuf runtime itself — a message built with
raw protobuf, decoded into a model and re-encoded, must come back as the same
wire data. One row per (field, value); a failure names both.
"""

import struct

import pytest
from pydantic import ValidationError

_INT_RANGES = {
    "f_int32": (-(2**31), 2**31 - 1),
    "f_sint32": (-(2**31), 2**31 - 1),
    "f_sfixed32": (-(2**31), 2**31 - 1),
    "f_uint32": (0, 2**32 - 1),
    "f_fixed32": (0, 2**32 - 1),
    "f_int64": (-(2**63), 2**63 - 1),
    "f_sint64": (-(2**63), 2**63 - 1),
    "f_sfixed64": (-(2**63), 2**63 - 1),
    "f_uint64": (0, 2**64 - 1),
    "f_fixed64": (0, 2**64 - 1),
}

_FLOAT32_MAX = 3.4028234663852886e38
_FLOAT32_MIN_SUBNORMAL = 1.401298464324817e-45
_FLOAT_SPECIALS = (0.0, -0.0, float("inf"), float("-inf"), float("nan"))

_EDGES = {
    **{field: sorted({low, 0, 1, high}) for field, (low, high) in _INT_RANGES.items()},
    "f_double": (*_FLOAT_SPECIALS, 5e-324, 1.7976931348623157e308, -1.7976931348623157e308),
    # float32 rows hold only values a 32-bit float represents exactly
    "f_float": (*_FLOAT_SPECIALS, _FLOAT32_MIN_SUBNORMAL, _FLOAT32_MAX, -_FLOAT32_MAX),
    "f_bool": (False, True),
    "f_string": ("", "\x00", "ż", "𝄞", "á", "x" * 5000),
    "f_bytes": (b"", b"\x00", b"\xff\xfe", bytes(range(256))),
    "r_int64": ([-(2**63), 0, 2**63 - 1], [0, 0]),
    "r_double": ([-0.0, float("nan"), float("inf")], [5e-324]),
    "r_bytes": ([b"", b"\xff", b""],),
    "m_int64": ({-(2**63): "", 2**63 - 1: "𝄞", 0: "zero"},),
    "m_bool": ({True: 2**31 - 1, False: -(2**31)},),
}

_CASES = [
    pytest.param(field, value, id=f"{field}={value!r:.40}")
    for field, values in _EDGES.items()
    for value in values
]

_BYTES_IDS = [f"{value!r:.40}" for value in _EDGES["f_bytes"]]

_REJECTED = [
    pytest.param(field, value, id=f"{field}={value}")
    for field, (low, high) in _INT_RANGES.items()
    for value in (low - 1, high + 1)
]


@pytest.fixture(scope="module")
def mod(generate):
    return generate("scalars.proto")


def _raw_message(mod, field, value):
    msg = mod.ScalarSink.proto_class()()
    if isinstance(value, list):
        getattr(msg, field).extend(value)
    elif isinstance(value, dict):
        getattr(msg, field).update(value)
    else:
        setattr(msg, field, value)
    return msg


@pytest.mark.parametrize(("field", "value"), _CASES)
def test_edge_value_survives_decode_and_encode(mod, field, value):
    """USE CASE: wire data holding a boundary value passes through a model
    bit-for-bit — including -0.0, NaN, subnormals, and non-UTF-8 bytes."""
    original = _raw_message(mod, field, value).SerializeToString(deterministic=True)
    reencoded = mod.ScalarSink.from_proto_bytes(original).to_proto_bytes()
    canonical = mod.ScalarSink.proto_class().FromString(reencoded)
    assert canonical.SerializeToString(deterministic=True) == original


@pytest.mark.parametrize(("field", "value"), _CASES)
def test_edge_value_survives_model_construction(mod, field, value):
    """USE CASE: a model built in python with a boundary value encodes to
    exactly what the protobuf runtime produces for the same value."""
    expected = _raw_message(mod, field, value).SerializeToString(deterministic=True)
    encoded = mod.ScalarSink(**{field: value}).to_proto_bytes()
    canonical = mod.ScalarSink.proto_class().FromString(encoded)
    assert canonical.SerializeToString(deterministic=True) == expected


@pytest.mark.parametrize(("field", "value"), _REJECTED)
def test_one_past_the_integer_range_is_rejected(mod, field, value):
    """USE CASE: every proto integer type rejects the first value outside its
    range at construction, on both ends."""
    with pytest.raises(ValidationError):
        mod.ScalarSink(**{field: value})


@pytest.mark.parametrize("value", _EDGES["f_bytes"], ids=_BYTES_IDS)
def test_bytes_survive_pydantic_json(mod, value):
    """USE CASE: any bytes value, not only valid UTF-8, survives a pydantic
    JSON dump and reload — bytes are base64 in JSON."""
    sink = mod.ScalarSink(f_bytes=value, r_bytes=[value])
    assert mod.ScalarSink.model_validate_json(sink.model_dump_json()) == sink


@pytest.mark.parametrize("value", _EDGES["f_bytes"], ids=_BYTES_IDS)
def test_proto_json_bytes_load_through_pydantic(mod, value):
    """USE CASE: bytes written by proto JSON (base64) are read back as those
    bytes by model_validate_json, never stored as the base64 text."""
    sink = mod.ScalarSink(f_bytes=value)
    proto_json = sink.to_proto_json(preserving_proto_field_name=True)
    assert mod.ScalarSink.model_validate_json(proto_json) == sink


def test_float_fields_narrow_to_32_bits(mod):
    """POLICY: a proto `float` narrows to 32 bits on the wire exactly as the
    protobuf runtime does — precision is float32, and a finite value beyond
    the float32 range becomes infinity. `double` fields are unaffected."""
    sink = mod.ScalarSink(f_float=0.1, f_double=0.1)
    restored = mod.ScalarSink.from_proto_bytes(sink.to_proto_bytes())
    assert restored.f_float == struct.unpack("f", struct.pack("f", 0.1))[0]
    assert restored.f_float != 0.1
    assert restored.f_double == 0.1

    overflowed = mod.ScalarSink.from_proto_bytes(mod.ScalarSink(f_float=1e40).to_proto_bytes())
    assert overflowed.f_float == float("inf")
