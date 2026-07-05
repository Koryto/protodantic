"""USE CASES: package-scale generation. Pointing protodantic at a directory
tree of .proto files yields a python package tree mirroring the proto file
layout (one module per proto file), with relocatable relative imports and a
single shared descriptor pool. Layout defaults follow the input shape
(directory -> tree, files -> module) with an explicit --layout override.
"""

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import protodantic
from protodantic import compile_fdset
from protodantic.cli import main

TREE_DIR = Path(__file__).parent / "protos" / "tree"


@pytest.fixture(scope="module")
def genpkg(tmp_path_factory):
    """CLI-generated package tree, importable as `genpkg.*`."""
    out_root = tmp_path_factory.mktemp("treegen")
    out_dir = out_root / "genpkg"
    result = CliRunner().invoke(main, ["generate", str(TREE_DIR), "-o", str(out_dir)])
    assert result.exit_code == 0, result.output
    sys.path.insert(0, str(out_root))
    yield out_dir
    sys.path.remove(str(out_root))


def test_directory_input_produces_package_tree(genpkg):
    """Modules mirror the proto file paths; descriptors and inits are emitted."""
    assert (genpkg / "__init__.py").exists()
    assert (genpkg / "_descriptors.py").exists()
    assert (genpkg / "myorg" / "__init__.py").exists()
    assert (genpkg / "myorg" / "common.py").exists()
    assert (genpkg / "myorg" / "billing.py").exists()
    assert (genpkg / "myorg" / "analytics" / "events.py").exists()


def test_same_package_cross_file_reference(genpkg):
    """billing.proto uses Money from common.proto (same proto package)."""
    billing = importlib.import_module("genpkg.myorg.billing")
    common = importlib.import_module("genpkg.myorg.common")
    invoice = billing.Invoice(
        id="i-1",
        total=common.Money(currency="PLN", units=100),
        lines=[common.Money(currency="PLN", units=60), common.Money(currency="PLN", units=40)],
    )
    restored = billing.Invoice.from_proto_bytes(invoice.to_proto_bytes())
    assert restored == invoice
    assert isinstance(restored.total, common.Money)


def test_cross_package_reference(genpkg):
    """events.proto (myorg.analytics) references myorg.Money and a WKT."""
    events = importlib.import_module("genpkg.myorg.analytics.events")
    common = importlib.import_module("genpkg.myorg.common")
    event = events.PurchaseEvent(
        at=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
        amount=common.Money(currency="EUR", units=5),
    )
    restored = events.PurchaseEvent.from_proto_bytes(event.to_proto_bytes())
    assert restored == event
    assert isinstance(restored.amount, common.Money)


def test_descriptors_shared_not_duplicated(genpkg):
    """Exactly one module carries the embedded FileDescriptorSet."""
    blob_files = sorted(
        p.relative_to(genpkg).as_posix()
        for p in genpkg.rglob("*.py")
        if "b64decode" in p.read_text(encoding="utf-8")
    )
    assert blob_files == ["_descriptors.py"]


def test_model_for_resolves_tree_types(genpkg):
    from protodantic import model_for

    importlib.import_module("genpkg.myorg.billing")
    assert model_for("myorg.Invoice").__name__ == "Invoice"
    assert model_for("myorg.analytics.PurchaseEvent").__name__ == "PurchaseEvent"


def test_compile_fdset_accepts_directories():
    """A directory argument discovers **/*.proto with the dir as import root."""
    fdset = compile_fdset([str(TREE_DIR)])
    source_names = {"myorg/common.proto", "myorg/billing.proto", "myorg/analytics/events.proto"}
    from google.protobuf import descriptor_pb2

    parsed = descriptor_pb2.FileDescriptorSet.FromString(fdset)
    assert source_names <= {f.name for f in parsed.file}


def test_tree_generation_is_deterministic():
    """Same input -> byte-identical file map (committed and diffed like any
    generated code)."""
    fdset = compile_fdset([str(TREE_DIR)])
    assert protodantic.generate_tree(fdset) == protodantic.generate_tree(fdset)


def test_directory_with_module_layout_override(tmp_path):
    """--layout module collapses a directory input into today's single module."""
    out = tmp_path / "models.py"
    result = CliRunner().invoke(
        main, ["generate", str(TREE_DIR), "--layout", "module", "-o", str(out)]
    )
    assert result.exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert "class Invoice(_pd.ProtoModel)" in text
    assert "class PurchaseEvent(_pd.ProtoModel)" in text


def test_layout_output_mismatch_fails_loudly(tmp_path):
    """Directory input defaults to tree layout; a .py output target is a
    contradiction and must produce a clear error, not silent reinterpretation."""
    result = CliRunner().invoke(
        main, ["generate", str(TREE_DIR), "-o", str(tmp_path / "models.py")]
    )
    assert result.exit_code == 1
    combined = result.output.lower() + (result.stderr or "").lower()
    assert "layout" in combined


def test_file_input_still_defaults_to_single_module(tmp_path):
    """Existing behavior unchanged: file arguments produce one module."""
    out = tmp_path / "models.py"
    result = CliRunner().invoke(
        main,
        ["generate", str(TREE_DIR / "myorg" / "common.proto"), "-o", str(out)],
    )
    assert result.exit_code == 0
    assert "class Money(_pd.ProtoModel)" in out.read_text(encoding="utf-8")
