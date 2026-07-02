"""USE CASES: proto field names that collide with python keywords or pydantic
reserved names generate working models — the python attribute gets a trailing
underscore and the original name stays usable as an alias and on the wire.
"""

import pytest


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
