"""USE CASES: package-scale generation. Pointing protodantic at a directory
tree of .proto files yields a python package tree mirroring the proto file
layout (one module per proto file, paths derived from file names — never from
proto packages), with relocatable relative imports and a single shared
descriptor pool. Layout defaults follow the input shape (directory -> tree,
files -> module) with an explicit --layout override. Hostile path segments are
sanitized deterministically; residual collisions and foreign files in the
output directory fail loudly; regeneration is managed-clean (stale modules
from deleted protos disappear).
"""

import importlib
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import protodantic
from protodantic import compile_fdset
from protodantic.cli import main

TREE_DIR = Path(__file__).parent / "protos" / "tree"
PROTO_DIR = Path(__file__).parent / "protos"

EXPECTED_MODULES = {
    "__init__.py",
    "_descriptors.py",
    "myorg/__init__.py",
    "myorg/common.py",
    "myorg/billing.py",
    "myorg/analytics/__init__.py",
    "myorg/analytics/events.py",
    "misc/__init__.py",
    "misc/oddly_placed.py",
}


@pytest.fixture(scope="module")
def tree_fdset():
    """fdset via the already-supported explicit-file API, so generate_tree
    specs stay independent of directory discovery."""
    files = sorted(str(p) for p in TREE_DIR.rglob("*.proto"))
    return compile_fdset(files, [str(TREE_DIR)])


@pytest.fixture(scope="module")
def genpkg(tmp_path_factory):
    """CLI-generated package tree from directory input, importable as genpkg.*"""
    out_root = tmp_path_factory.mktemp("treegen")
    out_dir = out_root / "genpkg"
    result = CliRunner().invoke(main, ["generate", str(TREE_DIR), "-o", str(out_dir)])
    assert result.exit_code == 0, result.output
    sys.path.insert(0, str(out_root))
    yield out_dir
    sys.path.remove(str(out_root))


# -- generate_tree API (decoupled from directory discovery) -----------------


def test_generate_tree_returns_file_map(tree_fdset):
    """generate_tree maps relative module paths to source text, mirroring the
    proto file layout; well-known google/ files get no modules."""
    files = protodantic.generate_tree(tree_fdset)
    assert set(files) == EXPECTED_MODULES
    assert "class Invoice(_pd.ProtoModel)" in files["myorg/billing.py"]


def test_module_paths_follow_file_paths_not_packages(tree_fdset):
    """misc/oddly_placed.proto declares package myorg.oddstuff — the module
    still lands at misc/oddly_placed.py (file path wins, always)."""
    files = protodantic.generate_tree(tree_fdset)
    assert "misc/oddly_placed.py" in files
    assert not any("oddstuff" in path for path in files)


# -- generated tree behavior (CLI end-to-end) --------------------------------


def test_directory_input_produces_package_tree(genpkg):
    on_disk = {p.relative_to(genpkg).as_posix() for p in genpkg.rglob("*.py")}
    assert on_disk == EXPECTED_MODULES


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


def test_shared_pool_identity(genpkg):
    """One pool object for the whole tree — the semantic behind the shared
    _descriptors.py module."""
    billing = importlib.import_module("genpkg.myorg.billing")
    common = importlib.import_module("genpkg.myorg.common")
    events = importlib.import_module("genpkg.myorg.analytics.events")
    assert common.Money.__proto_pool__ is billing.Invoice.__proto_pool__
    assert billing.Invoice.__proto_pool__ is events.PurchaseEvent.__proto_pool__


def test_descriptors_blob_stored_once(genpkg):
    blob_files = sorted(
        p.relative_to(genpkg).as_posix()
        for p in genpkg.rglob("*.py")
        if "b64decode" in p.read_text(encoding="utf-8")
    )
    assert blob_files == ["_descriptors.py"]


def test_model_for_resolves_tree_types(genpkg):
    from protodantic import model_for

    billing = importlib.import_module("genpkg.myorg.billing")
    events = importlib.import_module("genpkg.myorg.analytics.events")
    assert model_for("myorg.Invoice") is billing.Invoice
    assert model_for("myorg.analytics.PurchaseEvent") is events.PurchaseEvent


def test_tree_is_relocatable(genpkg, tmp_path):
    """The tree works under a different package name (imports must be relative,
    never self-referencing absolutes). Leaf module imported first to prove
    dependencies resolve automatically."""
    reloc_root = tmp_path / "elsewhere"
    reloc_root.mkdir()
    shutil.copytree(genpkg, reloc_root / "relocated_pkg")
    sys.path.insert(0, str(reloc_root))
    try:
        events = importlib.import_module("relocated_pkg.myorg.analytics.events")
        common = importlib.import_module("relocated_pkg.myorg.common")
        event = events.PurchaseEvent(
            at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            amount=common.Money(currency="USD", units=1),
        )
        assert events.PurchaseEvent.from_proto_bytes(event.to_proto_bytes()) == event
    finally:
        sys.path.remove(str(reloc_root))


# -- discovery, layout selection, determinism --------------------------------


def test_compile_fdset_accepts_directories():
    """A directory argument discovers **/*.proto with the dir as import root."""
    from google.protobuf import descriptor_pb2

    fdset = compile_fdset([str(TREE_DIR)])
    names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset).file}
    assert {
        "myorg/common.proto",
        "myorg/billing.proto",
        "myorg/analytics/events.proto",
        "misc/oddly_placed.proto",
    } <= names


def test_directory_pipeline_is_deterministic():
    """Two independent compile+generate runs over the same directory produce a
    byte-identical file map (discovery ordering included)."""
    first = protodantic.generate_tree(compile_fdset([str(TREE_DIR)]))
    second = protodantic.generate_tree(compile_fdset([str(TREE_DIR)]))
    assert first == second


def test_directory_with_module_layout_override(tmp_path):
    """--layout module collapses a directory input into a single module."""
    out = tmp_path / "models.py"
    result = CliRunner().invoke(
        main, ["generate", str(TREE_DIR), "--layout", "module", "-o", str(out)]
    )
    assert result.exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert "class Invoice(_pd.ProtoModel)" in text
    assert "class PurchaseEvent(_pd.ProtoModel)" in text


def test_file_inputs_with_tree_layout_override(tmp_path):
    """Explicit --layout tree works with file inputs too — input shape only
    sets the default."""
    out = tmp_path / "gen_from_files"
    result = CliRunner().invoke(
        main,
        ["generate", str(PROTO_DIR / "common.proto"), "--layout", "tree", "-o", str(out)],
    )
    assert result.exit_code == 0
    assert (out / "common.py").exists()
    assert (out / "_descriptors.py").exists()


def test_layout_output_mismatch_fails_loudly(tmp_path):
    """-o contradicting the layout is an error naming the fix, both ways."""
    tree_with_py = CliRunner().invoke(
        main, ["generate", str(TREE_DIR), "-o", str(tmp_path / "models.py")]
    )
    assert tree_with_py.exit_code == 1
    assert "layout" in (tree_with_py.output + tree_with_py.stderr).lower()

    module_with_dir = CliRunner().invoke(
        main,
        ["generate", str(PROTO_DIR / "common.proto"), "-o", str(tmp_path / "outdir")],
    )
    assert module_with_dir.exit_code == 1
    assert "layout" in (module_with_dir.output + module_with_dir.stderr).lower()


def test_file_input_still_defaults_to_single_module(tmp_path):
    """0.1.0 behavior anchored: file arguments produce one module."""
    out = tmp_path / "models.py"
    result = CliRunner().invoke(
        main, ["generate", str(TREE_DIR / "myorg" / "common.proto"), "-o", str(out)]
    )
    assert result.exit_code == 0
    assert "class Money(_pd.ProtoModel)" in out.read_text(encoding="utf-8")


# -- policy: external imports are emitted ------------------------------------


def test_external_imports_emitted_into_tree(tmp_path):
    """A tree proto importing a proto outside the input dir (via -I): the
    external file gets a module inside the output tree — self-contained."""
    ext_root = tmp_path / "ext_root"
    (ext_root / "shared").mkdir(parents=True)
    (ext_root / "shared" / "ext.proto").write_text(
        'syntax = "proto3";\npackage ext;\nmessage Marker { string tag = 1; }\n'
    )
    tree_root = tmp_path / "tree_root"
    (tree_root / "app").mkdir(parents=True)
    (tree_root / "app" / "uses.proto").write_text(
        'syntax = "proto3";\npackage app;\nimport "shared/ext.proto";\n'
        "message Wrapper { ext.Marker mark = 1; }\n"
    )
    out_root = tmp_path / "site"
    out = out_root / "extgen"
    result = CliRunner().invoke(
        main, ["generate", str(tree_root), "-I", str(ext_root), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert (out / "app" / "uses.py").exists()
    assert (out / "shared" / "ext.py").exists()

    sys.path.insert(0, str(out_root))
    try:
        uses = importlib.import_module("extgen.app.uses")
        ext = importlib.import_module("extgen.shared.ext")
        wrapper = uses.Wrapper(mark=ext.Marker(tag="t"))
        assert uses.Wrapper.from_proto_bytes(wrapper.to_proto_bytes()) == wrapper
    finally:
        sys.path.remove(str(out_root))


# -- policy: hostile paths sanitize deterministically, collisions fail -------


def test_hostile_paths_sanitized(tmp_path):
    """Keyword dirs, dashes, and reserved stems sanitize by the same
    deterministic rules as type names."""
    root = tmp_path / "hostile"
    (root / "class").mkdir(parents=True)
    (root / "class" / "def.proto").write_text(
        'syntax = "proto3";\npackage h1;\nmessage Thing { string v = 1; }\n'
    )
    (root / "foo-bar.proto").write_text(
        'syntax = "proto3";\npackage h2;\nmessage Item { string v = 1; }\n'
    )
    (root / "_descriptors.proto").write_text(
        'syntax = "proto3";\npackage h3;\nmessage Desc { string v = 1; }\n'
    )
    out_root = tmp_path / "site"
    out = out_root / "hostilegen"
    result = CliRunner().invoke(main, ["generate", str(root), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "class_" / "def_.py").exists()
    assert (out / "foo_bar.py").exists()
    assert (out / "_descriptors_.py").exists()

    sys.path.insert(0, str(out_root))
    try:
        thing_mod = importlib.import_module("hostilegen.class_.def_")
        thing = thing_mod.Thing(v="x")
        assert thing_mod.Thing.from_proto_bytes(thing.to_proto_bytes()) == thing
    finally:
        sys.path.remove(str(out_root))


def test_path_collisions_fail_loudly(tmp_path):
    """foo.proto next to foo/bar.proto demands module foo.py AND package foo/
    — refuse with an error naming both; sanitize-induced collisions too."""
    root_a = tmp_path / "coll_a"
    (root_a / "foo").mkdir(parents=True)
    (root_a / "foo.proto").write_text('syntax = "proto3";\npackage ca;\nmessage A { string v = 1; }\n')
    (root_a / "foo" / "bar.proto").write_text('syntax = "proto3";\npackage cb;\nmessage B { string v = 1; }\n')
    result_a = CliRunner().invoke(main, ["generate", str(root_a), "-o", str(tmp_path / "out_a")])
    assert result_a.exit_code == 1
    assert "foo" in (result_a.output + result_a.stderr).lower()

    root_b = tmp_path / "coll_b"
    root_b.mkdir()
    (root_b / "foo-bar.proto").write_text('syntax = "proto3";\npackage cc;\nmessage C { string v = 1; }\n')
    (root_b / "foo_bar.proto").write_text('syntax = "proto3";\npackage cd;\nmessage D { string v = 1; }\n')
    result_b = CliRunner().invoke(main, ["generate", str(root_b), "-o", str(tmp_path / "out_b")])
    assert result_b.exit_code == 1
    assert "foo_bar" in (result_b.output + result_b.stderr).lower()


# -- policy: managed-clean regeneration ---------------------------------------


def test_regeneration_removes_stale_modules(tmp_path):
    """Deleting a proto and regenerating removes its module — committed trees
    can never serve stale models."""
    root = tmp_path / "protos"
    root.mkdir()
    (root / "a.proto").write_text('syntax = "proto3";\npackage ra;\nmessage A { string v = 1; }\n')
    (root / "b.proto").write_text('syntax = "proto3";\npackage rb;\nmessage B { string v = 1; }\n')
    out = tmp_path / "gen"
    assert CliRunner().invoke(main, ["generate", str(root), "-o", str(out)]).exit_code == 0
    assert (out / "a.py").exists() and (out / "b.py").exists()

    (root / "b.proto").unlink()
    assert CliRunner().invoke(main, ["generate", str(root), "-o", str(out)]).exit_code == 0
    assert (out / "a.py").exists()
    assert not (out / "b.py").exists()


def test_regeneration_refuses_foreign_files(tmp_path):
    """A file without the protodantic header in the output dir aborts
    regeneration — we never delete anything we didn't generate."""
    root = tmp_path / "protos"
    root.mkdir()
    (root / "a.proto").write_text('syntax = "proto3";\npackage rf;\nmessage A { string v = 1; }\n')
    out = tmp_path / "gen"
    assert CliRunner().invoke(main, ["generate", str(root), "-o", str(out)]).exit_code == 0

    foreign = out / "handwritten.py"
    foreign.write_text("SECRET = 42\n")
    result = CliRunner().invoke(main, ["generate", str(root), "-o", str(out)])
    assert result.exit_code == 1
    assert "handwritten.py" in (result.output + result.stderr)
    assert foreign.read_text() == "SECRET = 42\n"
