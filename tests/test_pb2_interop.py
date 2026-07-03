"""USE CASES: interop with classic protoc-generated _pb2 modules. Brownfield
orgs consume centralized proto packages as _pb2 code; protodantic models built
from the same schema must accept those instances directly and produce bytes
those classes parse.
"""

import importlib.resources
import sys
from pathlib import Path

import pytest
from grpc_tools import protoc

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
    return demo_pb2


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
