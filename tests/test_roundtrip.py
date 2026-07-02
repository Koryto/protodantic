"""End-to-end tests: compile demo.proto, generate models, round-trip data."""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from protodantic import compile_fdset, generate_source

PROTO_DIR = Path(__file__).parent / "protos"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    fdset = compile_fdset([str(PROTO_DIR / "demo.proto")])
    source = generate_source(fdset)
    module_path = tmp_path_factory.mktemp("generated") / "demo_pd.py"
    module_path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("demo_pd", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_pd"] = module
    spec.loader.exec_module(module)
    return module


def full_user(demo):
    return demo.User(
        id=7,
        name="kory",
        active=True,
        score=99.5,
        avatar=b"\x00\x01",
        color=demo.Color.GREEN,
        address=demo.Address(street="Main St 1", city="Warsaw"),
        tags=["a", "b"],
        addresses=[demo.Address(street="Second St 2", city="Krakow")],
        counts={"clicks": 3, "views": 12},
        places={"home": demo.Address(street="Third St 3", city="Gdansk")},
        nickname="kk",
        created_at=datetime(2026, 7, 2, 12, 30, tzinfo=timezone.utc),
        email="k@example.com",
        ttl=timedelta(seconds=90),
        settings=demo.User_Settings(dark_mode=True),
    )


def test_pydantic_to_proto(demo):
    msg = full_user(demo).to_proto()
    assert msg.id == 7
    assert msg.name == "kory"
    assert msg.color == 2
    assert list(msg.tags) == ["a", "b"]
    assert msg.address.city == "Warsaw"
    assert msg.counts["views"] == 12
    assert msg.places["home"].street == "Third St 3"
    assert msg.WhichOneof("contact") == "email"
    assert msg.created_at.seconds > 0
    assert msg.ttl.seconds == 90
    assert msg.settings.dark_mode is True


def test_wire_roundtrip(demo):
    user = full_user(demo)
    restored = demo.User.from_proto_bytes(user.to_proto_bytes())
    assert restored == user


def test_proto_to_pydantic(demo):
    msg = demo.User.proto_class()()
    msg.id = 42
    msg.name = "ada"
    msg.color = 1
    msg.tags.extend(["x"])
    msg.phone = "555"
    user = demo.User.from_proto(msg)
    assert user.id == 42
    assert user.color == demo.Color.RED
    assert user.tags == ["x"]
    assert user.phone == "555"
    assert user.email is None  # other oneof member stays unset


def test_unset_fields_roundtrip(demo):
    user = demo.User()
    restored = demo.User.from_proto_bytes(user.to_proto_bytes())
    assert restored == user
    assert restored.address is None
    assert restored.nickname is None
    assert restored.created_at is None
    assert restored.tags == []
    assert restored.counts == {}
    assert restored.avatar == b""


def test_json_roundtrip(demo):
    user = full_user(demo)
    restored = demo.User.from_proto_json(user.to_proto_json())
    assert restored == user


def test_validation_still_applies(demo):
    with pytest.raises(Exception):
        demo.User(id="not-an-int-at-all")
