"""USE CASE: deeply nested message declarations (3+ levels) flatten to
predictable class names (Outer_Middle_Inner) and round-trip intact.
"""


def test_three_level_nesting(generate):
    """Three nested message levels flatten predictably and round-trip."""
    mod = generate("nesting.proto")
    outer = mod.Outer(middle=mod.Outer_Middle(tag="m", inner=mod.Outer_Middle_Inner(leaf="deep")))
    restored = mod.Outer.from_proto_bytes(outer.to_proto_bytes())
    assert restored == outer
    assert restored.middle.inner.leaf == "deep"
