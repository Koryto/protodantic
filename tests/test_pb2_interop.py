"""USE CASES: interop with classic protoc-generated _pb2 modules. Brownfield
orgs consume centralized proto packages as _pb2 code; protodantic models built
from the same schema must accept those instances directly and produce bytes
those classes parse.
"""

import importlib.resources
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from google.protobuf import message_factory
from google.protobuf.message import Message
from grpc_tools import protoc

from protodantic import compile_fdset, generate_source, load_pool

PROTO_DIR = Path(__file__).parent / "protos"


@pytest.fixture(scope="module")
def pb2(tmp_path_factory):
    """Classic `protoc --python_out` module for demo.proto."""
    out_dir = tmp_path_factory.mktemp("pb2")
    wkt_include = str(importlib.resources.files("grpc_tools") / "_proto")
    args = [
        "protoc",
        f"-I{PROTO_DIR}",
        f"-I{wkt_include}",
        f"--python_out={out_dir}",
        str(PROTO_DIR / "demo.proto"),
    ]
    assert protoc.main(args) == 0
    sys.path.insert(0, str(out_dir))
    try:
        import demo_pb2
    finally:
        sys.path.remove(str(out_dir))
    yield demo_pb2
    sys.modules.pop("demo_pb2", None)


@pytest.fixture(scope="module")
def mod(generate):
    return generate("demo.proto")


def test_from_proto_accepts_pb2_instances(mod, pb2):
    """A _pb2 message (default descriptor pool) converts straight to a model,
    including nested messages resolved through the registry."""
    msg = pb2.User(id=7, name="kory", tags=["a", "b"], email="k@x.io")
    msg.address.city = "Warsaw"
    user = mod.User.from_proto(msg)
    assert user.id == 7
    assert user.address == mod.Address(city="Warsaw")
    assert user.tags == ["a", "b"]
    assert user.email == "k@x.io"


def test_model_bytes_parse_into_pb2_classes(mod, pb2):
    """to_proto_bytes output is canonical wire format any _pb2 class parses."""
    user = mod.User(id=9, name="ada", color=mod.Color.GREEN, phone="555")
    msg = pb2.User.FromString(user.to_proto_bytes())
    assert msg.id == 9
    assert msg.color == 2
    assert msg.WhichOneof("contact") == "phone"


def test_pb2_roundtrip_through_model(mod, pb2):
    """_pb2 -> model -> bytes -> _pb2 preserves everything the schema knows."""
    original = pb2.User(id=3, name="bo", nickname="b")
    reparsed = pb2.User.FromString(mod.User.from_proto(original).to_proto_bytes())
    assert reparsed == original


def test_to_proto_into_pb2_class(mod, pb2):
    """to_proto(into=TheirPb2Class) returns an instance of THEIR class — the
    grpc-call-site handoff without manual FromString plumbing."""
    user = mod.User(
        id=5,
        name="into",
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        email="e@x.io",
    )
    msg = user.to_proto(into=pb2.User)
    assert isinstance(msg, pb2.User)
    assert msg.id == 5
    assert msg.name == "into"
    assert msg.created_at.ToDatetime(tzinfo=timezone.utc) == user.created_at
    assert msg.WhichOneof("contact") == "email"


def test_to_proto_into_wrong_class_raises(mod, pb2):
    """A mismatched target class is a TypeError naming BOTH proto types —
    what was expected and what was given."""
    with pytest.raises(TypeError) as exc_info:
        mod.User(id=1).to_proto(into=pb2.Address)
    message = str(exc_info.value)
    assert "demo.User" in message
    assert "demo.Address" in message


def test_to_proto_into_roundtrip(mod, pb2):
    """model -> into-pb2 instance -> from_proto recovers the model exactly."""
    user = mod.User(id=9, name="loop", color=mod.Color.GREEN, tags=["a"])
    assert mod.User.from_proto(user.to_proto(into=pb2.User)) == user


def test_to_proto_into_requires_a_message_class(mod, pb2):
    """Instances and non-message classes are rejected with a clear TypeError
    up front — never a confusing downstream crash."""
    with pytest.raises(TypeError, match="message class"):
        mod.User(id=1).to_proto(into=pb2.User())  # an instance, not the class
    with pytest.raises(TypeError, match="message class"):
        mod.User(id=1).to_proto(into=dict)  # not a protobuf class at all
    with pytest.raises(TypeError, match="message class"):
        mod.User(id=1).to_proto(into=object)  # no DESCRIPTOR to leak on

    with pytest.raises(TypeError, match="message class"):
        mod.User(id=1).to_proto(into=Message)  # abstract base: DESCRIPTOR is None

    class HollowMessage(Message):
        pass

    with pytest.raises(TypeError, match="message class"):
        mod.User(id=1).to_proto(into=HollowMessage)  # subclass without a descriptor


def test_to_proto_into_tolerates_compatible_version_skew(tmp_path):
    """POLICY: into= requires a matching proto full name only; schema-version
    skew follows wire-compat semantics. A newer model handed to an older
    target class keeps the newer fields as protobuf unknown fields — nothing
    is silently lost in transit."""
    v1 = tmp_path / "v1.proto"
    v1.write_text('syntax = "proto3";\npackage skew;\nmessage Event { string id = 1; }\n')
    v2 = tmp_path / "v2.proto"
    v2.write_text(
        'syntax = "proto3";\npackage skew;\nmessage Event { string id = 1; int32 priority = 2; }\n'
    )

    module_path = tmp_path / "skew_v2_models.py"
    module_path.write_text(generate_source(compile_fdset([str(v2)])), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("skew_v2_models", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["skew_v2_models"] = module
    try:
        spec.loader.exec_module(module)

        old_pool = load_pool(compile_fdset([str(v1)]))
        old_cls = message_factory.GetMessageClass(old_pool.FindMessageTypeByName("skew.Event"))

        event = module.Event(id="e-1", priority=9)
        older_msg = event.to_proto(into=old_cls)  # same full name, older schema
        assert older_msg.id == "e-1"
        # the newer field survives the older class as an unknown field
        recovered = module.Event.from_proto_bytes(older_msg.SerializeToString())
        assert recovered.priority == 9
    finally:
        sys.modules.pop("skew_v2_models", None)
