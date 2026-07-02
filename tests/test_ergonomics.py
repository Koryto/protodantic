"""USE CASES: generated models are first-class pydantic citizens — dump, copy,
JSON schema, and proto-JSON all work.
"""

from datetime import datetime, timezone

import pytest


@pytest.fixture(scope="module")
def mod(generate):
    return generate("demo.proto")


def full_user(mod):
    return mod.User(
        id=1,
        name="kory",
        color=mod.Color.RED,
        address=mod.Address(street="Main", city="Warsaw"),
        tags=["x"],
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        email="k@example.com",
    )


def test_model_dump(mod):
    dumped = full_user(mod).model_dump()
    assert dumped["name"] == "kory"
    assert dumped["address"]["city"] == "Warsaw"


def test_model_dump_json(mod):
    assert '"kory"' in full_user(mod).model_dump_json()


def test_model_copy_update(mod):
    user = full_user(mod)
    clone = user.model_copy(update={"name": "ada"})
    assert clone.name == "ada"
    restored = mod.User.from_proto_bytes(clone.to_proto_bytes())
    assert restored.name == "ada"


def test_json_schema_generation(mod):
    """Generated models can produce a JSON schema (OpenAPI/FastAPI use case)."""
    schema = mod.User.model_json_schema()
    assert "name" in schema["properties"]


def test_proto_json_uses_canonical_names(mod):
    """to_proto_json emits proto3 canonical JSON (camelCase field names)."""
    text = full_user(mod).to_proto_json()
    assert "createdAt" in text
    restored = mod.User.from_proto_json(text)
    assert restored == full_user(mod)


def test_mutation_then_serialize(mod):
    user = full_user(mod)
    user.tags.append("y")
    user.counts["k"] = 3
    restored = mod.User.from_proto_bytes(user.to_proto_bytes())
    assert restored.tags == ["x", "y"]
    assert restored.counts == {"k": 3}
