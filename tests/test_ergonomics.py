"""USE CASES: generated models are first-class pydantic citizens — dump, copy,
JSON schema, and proto-JSON all work.
"""

import json
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
    """Generated models support standard nested python-mode dumps."""
    dumped = full_user(mod).model_dump()
    assert dumped["name"] == "kory"
    assert dumped["address"]["city"] == "Warsaw"


def test_model_dump_json(mod):
    """Generated models support standard pydantic JSON dumps."""
    assert '"kory"' in full_user(mod).model_dump_json()


def test_model_copy_update(mod):
    """A standard model_copy update remains protobuf-serializable."""
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


def test_from_proto_json_kwargs_passthrough(mod):
    """json_format options are exposed both ways — e.g. lenient ingestion of
    JSON containing fields from a newer schema revision."""
    payload = json.loads(full_user(mod).to_proto_json())
    payload["fieldFromTheFuture"] = 123
    restored = mod.User.from_proto_json(json.dumps(payload), ignore_unknown_fields=True)
    assert restored == full_user(mod)


def test_json_name_option_respected(mod):
    """A [json_name = ...] override is honored in proto JSON, both directions."""
    user = mod.User(id=1, legacy_field="v")
    text = user.to_proto_json()
    assert "legacyName" in text
    assert mod.User.from_proto_json(text).legacy_field == "v"


def test_mutation_then_serialize(mod):
    """In-place list and map mutations are reflected on the wire."""
    user = full_user(mod)
    user.tags.append("y")
    user.counts["k"] = 3
    restored = mod.User.from_proto_bytes(user.to_proto_bytes())
    assert restored.tags == ["x", "y"]
    assert restored.counts == {"k": 3}
