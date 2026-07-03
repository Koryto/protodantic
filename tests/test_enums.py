"""USE CASES: proto enums become IntEnums, work in every container position,
support aliases, and — because proto3 enums are open — unknown values received
off the wire must be preserved, not rejected.
"""

import pytest


@pytest.fixture(scope="module")
def mod(generate):
    return generate("enums.proto")


def test_enum_roundtrip_all_positions(mod):
    """Enums round-trip as singular fields, repeated elements, and map values."""
    palette = mod.Palette(
        mood=mod.Mood.GRUMPY,
        history=[mod.Mood.HAPPY, mod.Mood.MOOD_UNSPECIFIED],
        by_name={"morning": mod.Mood.GRUMPY},
    )
    restored = mod.Palette.from_proto_bytes(palette.to_proto_bytes())
    assert restored == palette
    assert isinstance(restored.mood, mod.Mood)


def test_enum_accepts_raw_int(mod):
    """Plain ints matching a member coerce to the enum (ergonomic construction)."""
    assert mod.Palette(mood=2).mood is mod.Mood.GRUMPY


def test_enum_aliases(mod):
    """allow_alias enums generate python enum aliases pointing at one member."""
    assert mod.Mood.JOYFUL is mod.Mood.HAPPY
    assert mod.Mood.JOYFUL == 1


def test_unknown_enum_value_preserved(mod):
    """proto3 enums are OPEN: a value not in the schema (e.g. from a newer peer)
    must survive parse and re-serialization instead of raising."""
    raw = mod.Palette.proto_class()()
    raw.mood = 99  # not a Mood member
    data = raw.SerializeToString()

    restored = mod.Palette.from_proto_bytes(data)
    assert restored.mood == 99
    assert restored.to_proto_bytes() == data
