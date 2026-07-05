"""USE CASES: proto field names that collide with python keywords or pydantic
reserved names generate working models — the python attribute gets a trailing
underscore and the original name stays usable as an alias and on the wire.
"""

import pytest

from protodantic import model_for


@pytest.fixture(scope="module")
def mod(generate):
    return generate("naming.proto")


def test_keyword_fields_get_underscore_suffix(mod):
    """`class` -> `class_`, `from` -> `from_`, etc."""
    hazard = mod.Hazard(class_="warrior", from_=3, import_=True, global_=["x"])
    assert hazard.class_ == "warrior"
    assert hazard.from_ == 3


def test_keyword_fields_constructible_by_proto_name(mod):
    """The original proto name works as a populate alias."""
    hazard = mod.Hazard(**{"class": "mage", "from": 1})
    assert hazard.class_ == "mage"


def test_keyword_fields_roundtrip(mod):
    """Aliased fields serialize under their true proto names."""
    hazard = mod.Hazard(class_="rogue", from_=2, import_=True, global_=["a", "b"])
    msg = hazard.to_proto()
    assert getattr(msg, "class") == "rogue"
    assert getattr(msg, "from") == 2
    restored = mod.Hazard.from_proto_bytes(hazard.to_proto_bytes())
    assert restored == hazard


def test_pydantic_reserved_name_field(mod):
    """A proto field named `model_config` must not clash with pydantic."""
    hazard = mod.Hazard(model_config_="value")
    restored = mod.Hazard.from_proto_bytes(hazard.to_proto_bytes())
    assert restored.model_config_ == "value"


def test_keyword_and_builtin_type_names(generate):
    """Type names that are python keywords or generated-code builtins get the
    trailing-underscore treatment (`message list` -> `list_`) and the module
    still works — builtin annotations and factories stay unshadowed."""
    mod = generate("hostile_names.proto")
    holder = mod.Holder(
        c=mod.class_(x="a"),
        l=mod.list_(item="b"),
        d=mod.dict_(k="c"),
        mode=mod.global_.def_,
        tags=["t1", "t2"],
        counts={"k": 1},
    )
    restored = mod.Holder.from_proto_bytes(holder.to_proto_bytes())
    assert restored == holder
    assert restored.tags == ["t1", "t2"]
    assert restored.counts == {"k": 1}


def test_hostile_type_names_keep_proto_truth(generate):
    """Renamed classes still resolve by their true proto full names."""
    mod = generate("hostile_names.proto")
    assert model_for("test.hostile.class") is mod.class_
    assert model_for("test.hostile.list") is mod.list_


def test_keyword_enum_members(generate):
    """Enum members that are python keywords get the same underscore rule."""
    mod = generate("hostile_names.proto")
    assert mod.global_.def_ == 1
    assert mod.global_.return_ == 2


def test_message_named_any_does_not_shadow_typing(generate):
    """A user message named `Any` must not hijack the typing.Any annotation of
    real google.protobuf.Any fields — generated imports are shadow-proof."""
    mod = generate("shadowing.proto")
    env = mod.Env(payload=mod.Note(text="not an Any instance"))
    restored = mod.Env.from_proto_bytes(env.to_proto_bytes())
    assert isinstance(restored.payload, mod.Note)


def test_shadowing_message_still_usable(generate):
    """The user's own `Any` message works as a normal model too."""
    mod = generate("shadowing.proto")
    env = mod.Env(payload=mod.Any(x="v"))
    restored = mod.Env.from_proto_bytes(env.to_proto_bytes())
    assert isinstance(restored.payload, mod.Any)
    assert restored.payload.x == "v"
