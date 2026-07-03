"""USE CASES: the `protodantic` CLI — a `generate` subcommand (future drops add
more verbs) that compiles one or many protos, honors include paths, and fails
loudly with a nonzero exit code on bad input.
"""

from pathlib import Path

from click.testing import CliRunner

from protodantic.cli import main

PROTO_DIR = Path(__file__).parent / "protos"


def test_cli_generates_module(tmp_path):
    out = tmp_path / "models.py"
    result = CliRunner().invoke(main, ["generate", str(PROTO_DIR / "demo.proto"), "-o", str(out)])
    assert result.exit_code == 0
    assert "class User(_pd.ProtoModel)" in out.read_text(encoding="utf-8")


def test_cli_include_path(tmp_path):
    """-I resolves imports living outside the proto's own directory."""
    schema_dir = tmp_path / "schemas"
    include_dir = tmp_path / "includes"
    schema_dir.mkdir()
    include_dir.mkdir()
    (include_dir / "common.proto").write_text(
        'syntax = "proto3";\npackage test.shared;\n'
        "message Money { int64 units = 1; }\n",
        encoding="utf-8",
    )
    orders_proto = schema_dir / "orders.proto"
    orders_proto.write_text(
        'syntax = "proto3";\npackage test.orders;\nimport "common.proto";\n'
        "message Order { test.shared.Money total = 1; }\n",
        encoding="utf-8",
    )
    out = tmp_path / "orders.py"

    without_include = CliRunner().invoke(
        main, ["generate", str(orders_proto), "-o", str(out)]
    )
    assert without_include.exit_code == 1
    assert not out.exists()

    result = CliRunner().invoke(
        main, ["generate", str(orders_proto), "-I", str(include_dir), "-o", str(out)]
    )
    assert result.exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert "class Order(_pd.ProtoModel)" in text
    assert "class Money(_pd.ProtoModel)" in text


def test_cli_multiple_protos(tmp_path):
    out = tmp_path / "combined.py"
    result = CliRunner().invoke(
        main,
        ["generate", str(PROTO_DIR / "common.proto"), str(PROTO_DIR / "wire.proto"), "-o", str(out)],
    )
    assert result.exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert "class Money(_pd.ProtoModel)" in text
    assert "class EventV1(_pd.ProtoModel)" in text


def test_cli_creates_parent_dirs(tmp_path):
    out = tmp_path / "deep" / "nested" / "models.py"
    result = CliRunner().invoke(main, ["generate", str(PROTO_DIR / "common.proto"), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()


def test_cli_bad_proto_fails_nonzero(tmp_path):
    out = tmp_path / "nope.py"
    result = CliRunner().invoke(
        main, ["generate", str(tmp_path / "does_not_exist.proto"), "-o", str(out)]
    )
    assert result.exit_code == 1
    assert not out.exists()
    assert "error" in result.output.lower() or "error" in result.stderr.lower()


def test_cli_unwritable_output_fails_cleanly(tmp_path):
    """A bad -o target (e.g. an existing directory) reports a clean error,
    not a traceback."""
    result = CliRunner().invoke(
        main, ["generate", str(PROTO_DIR / "common.proto"), "-o", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert result.exception is None or not isinstance(result.exception, OSError)
    assert "error" in result.output.lower() or "error" in result.stderr.lower()


def test_cli_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "protodantic" in result.output
