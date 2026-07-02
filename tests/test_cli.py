"""USE CASES: the `protodantic` CLI — generate from one or many protos, honor
include paths, and fail loudly with a nonzero exit code on bad input.
"""

from pathlib import Path

from protodantic.cli import main

PROTO_DIR = Path(__file__).parent / "protos"


def test_cli_generates_module(tmp_path):
    out = tmp_path / "models.py"
    assert main([str(PROTO_DIR / "demo.proto"), "-o", str(out)]) == 0
    assert "class User(ProtoModel)" in out.read_text(encoding="utf-8")


def test_cli_include_path(tmp_path):
    """-I resolves imports living outside the proto's own directory."""
    out = tmp_path / "orders.py"
    code = main([str(PROTO_DIR / "orders.proto"), "-I", str(PROTO_DIR), "-o", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "class Order(ProtoModel)" in text
    assert "class Money(ProtoModel)" in text


def test_cli_multiple_protos(tmp_path):
    out = tmp_path / "combined.py"
    code = main([
        str(PROTO_DIR / "common.proto"),
        str(PROTO_DIR / "wire.proto"),
        "-o", str(out),
    ])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "class Money(ProtoModel)" in text
    assert "class EventV1(ProtoModel)" in text


def test_cli_creates_parent_dirs(tmp_path):
    out = tmp_path / "deep" / "nested" / "models.py"
    assert main([str(PROTO_DIR / "common.proto"), "-o", str(out)]) == 0
    assert out.exists()


def test_cli_bad_proto_fails_nonzero(tmp_path, capsys):
    out = tmp_path / "nope.py"
    code = main([str(tmp_path / "does_not_exist.proto"), "-o", str(out)])
    assert code == 1
    assert not out.exists()
    assert "error" in capsys.readouterr().err.lower()
