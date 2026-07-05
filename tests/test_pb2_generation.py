"""USE CASES: 0.1.2 greenfield closeout — generating models from an installed
_pb2 package by descriptor reflection. The _pb2 package is a proxy form of the
.proto files: reflection produces FileDescriptorSet bytes that feed the SAME
codegen seam, so its output is provably equivalent to compiling the sources.
CLI: `protodantic generate --from-package NAME -o gen/` (tree layout default,
module paths derive from the proto file names recorded in the descriptors —
never from the _pb2 python layout).
"""

import importlib.resources
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from google.protobuf import descriptor_pb2
from grpc_tools import protoc

import protodantic
from protodantic import compile_fdset
from protodantic.cli import main

TREE_DIR = Path(__file__).parent / "protos" / "tree"
MYORG_PROTOS = sorted(str(p) for p in (TREE_DIR / "myorg").rglob("*.proto"))


def _purge_modules(prefix: str) -> None:
    for name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        del sys.modules[name]


@pytest.fixture(scope="module")
def myorg_pb2(tmp_path_factory):
    """An installed-like classic _pb2 package (`import myorg`) compiled from
    the committed tree fixtures — the central-proto-wheel scenario."""
    site = tmp_path_factory.mktemp("pb2site")
    wkt = str(importlib.resources.files("grpc_tools") / "_proto")
    args = ["protoc", f"-I{TREE_DIR}", f"-I{wkt}", f"--python_out={site}", *MYORG_PROTOS]
    assert protoc.main(args) == 0
    for directory in [site / "myorg", *(p for p in (site / "myorg").rglob("*") if p.is_dir())]:
        (directory / "__init__.py").write_text("")
    sys.path.insert(0, str(site))
    yield "myorg"
    sys.path.remove(str(site))
    _purge_modules("myorg")


# -- fdset_from_package API ---------------------------------------------------


def test_fdset_from_package_collects_all_files(myorg_pb2):
    """Reflection gathers every proto file in the package plus transitive
    dependencies (well-known types included) into one fdset."""
    fdset_bytes = protodantic.fdset_from_package(myorg_pb2)
    names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes).file}
    assert {
        "myorg/common.proto",
        "myorg/billing.proto",
        "myorg/analytics/events.proto",
    } <= names
    assert "google/protobuf/timestamp.proto" in names  # transitive dep


def test_fdset_from_package_canonical_order(myorg_pb2):
    """Byte-identical across calls AND a pinned canonical file order:
    dependencies before dependents, lexicographic among the independent —
    process-independent by construction, not by accident of dict ordering."""
    first = protodantic.fdset_from_package(myorg_pb2)
    assert first == protodantic.fdset_from_package(myorg_pb2)
    names = [f.name for f in descriptor_pb2.FileDescriptorSet.FromString(first).file]
    assert names == [
        "google/protobuf/timestamp.proto",
        "myorg/common.proto",
        "myorg/analytics/events.proto",
        "myorg/billing.proto",
    ]


def test_reflection_equals_source_compilation(myorg_pb2):
    """THE proxy claim, pinned: per-module generated sources from reflection
    are identical to those from compiling the .proto sources."""
    via_reflection = protodantic.generate_tree(protodantic.fdset_from_package(myorg_pb2))
    via_protoc = protodantic.generate_tree(compile_fdset(MYORG_PROTOS, [str(TREE_DIR)]))
    strip = lambda tree: {k: v for k, v in tree.items() if k != "_descriptors.py"}  # noqa: E731
    assert strip(via_reflection) == strip(via_protoc)


def test_fdset_from_package_missing_package_raises(myorg_pb2):
    with pytest.raises(ModuleNotFoundError):
        protodantic.fdset_from_package("definitely_not_installed_xyz")


def test_fdset_from_package_without_descriptors_raises(tmp_path):
    """A package with no protobuf modules is a clear error, not empty output."""
    pkg = tmp_path / "emptypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(ValueError, match="emptypkg"):
            protodantic.fdset_from_package("emptypkg")
    finally:
        sys.path.remove(str(tmp_path))
        _purge_modules("emptypkg")


def _build_pb2_site(*, tmp_path: Path, package: str, with_init: bool) -> Path:
    """A site dir holding <package>/mini_pb2.py plus a booby-trapped helper
    module that raises if imported."""
    proto_root = tmp_path / "protosrc"
    (proto_root / package).mkdir(parents=True)
    (proto_root / package / "mini.proto").write_text(
        f'syntax = "proto3";\npackage {package};\nmessage Tiny {{ string v = 1; }}\n'
    )
    site = tmp_path / "site"
    site.mkdir()
    wkt = str(importlib.resources.files("grpc_tools") / "_proto")
    args = ["protoc", f"-I{proto_root}", f"-I{wkt}", f"--python_out={site}",
            str(proto_root / package / "mini.proto")]
    assert protoc.main(args) == 0
    (site / package / "evil_helper.py").write_text("raise RuntimeError('must not be imported')\n")
    if with_init:
        (site / package / "__init__.py").write_text("")
    return site


def test_reflection_imports_only_pb2_modules(tmp_path):
    """Enterprise packages carry helper modules with import side effects (or
    grpc stubs needing extra deps): reflection must import only *_pb2 modules
    — the protoc naming contract — never the rest."""
    site = _build_pb2_site(tmp_path=tmp_path, package="sidefx", with_init=True)
    sys.path.insert(0, str(site))
    try:
        fdset_bytes = protodantic.fdset_from_package("sidefx")
        names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes).file}
        assert "sidefx/mini.proto" in names
    finally:
        sys.path.remove(str(site))
        _purge_modules("sidefx")


def test_reflection_supports_namespace_packages(tmp_path):
    """PEP 420 namespace packages (no __init__.py) — common for enterprise
    proto wheels — are discoverable too."""
    site = _build_pb2_site(tmp_path=tmp_path, package="sidens", with_init=False)
    sys.path.insert(0, str(site))
    try:
        fdset_bytes = protodantic.fdset_from_package("sidens")
        names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes).file}
        assert "sidens/mini.proto" in names
    finally:
        sys.path.remove(str(site))
        _purge_modules("sidens")


# -- CLI ----------------------------------------------------------------------


def test_cli_from_package_generates_tree(myorg_pb2, tmp_path):
    """--from-package defaults to tree layout; module paths mirror the proto
    file names from the descriptors, never the _pb2 python layout."""
    out_root = tmp_path / "site"
    out = out_root / "reflected"
    result = CliRunner().invoke(main, ["generate", "--from-package", myorg_pb2, "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "myorg" / "billing.py").exists()
    assert (out / "myorg" / "analytics" / "events.py").exists()
    assert not list(out.rglob("*_pb2*"))

    sys.path.insert(0, str(out_root))
    try:
        import importlib

        billing = importlib.import_module("reflected.myorg.billing")
        common = importlib.import_module("reflected.myorg.common")
        invoice = billing.Invoice(id="r-1", total=common.Money(currency="PLN", units=7))
        assert billing.Invoice.from_proto_bytes(invoice.to_proto_bytes()) == invoice
    finally:
        sys.path.remove(str(out_root))


def test_cli_from_package_module_layout(myorg_pb2, tmp_path):
    """--layout module collapses a reflected package into a single module."""
    out = tmp_path / "models.py"
    result = CliRunner().invoke(
        main, ["generate", "--from-package", myorg_pb2, "--layout", "module", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "class Invoice(_pd.ProtoModel)" in out.read_text(encoding="utf-8")


def test_cli_from_package_and_positional_are_exclusive(myorg_pb2, tmp_path):
    """Positional protos and --from-package together is a contradiction with a
    specific contract message (not a generic usage error)."""
    result = CliRunner().invoke(
        main,
        ["generate", str(TREE_DIR / "myorg" / "common.proto"), "--from-package", myorg_pb2,
         "-o", str(tmp_path / "x")],
    )
    assert result.exit_code == 1
    assert "cannot be used together" in (result.output + result.stderr)


def test_cli_from_package_rejects_includes(myorg_pb2, tmp_path):
    """-I belongs to protoc compilation; combining it with --from-package is
    unspecifiable and must error, never be silently ignored."""
    result = CliRunner().invoke(
        main,
        ["generate", "--from-package", myorg_pb2, "-I", str(tmp_path), "-o", str(tmp_path / "g")],
    )
    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert "--from-package" in combined
    assert "include" in combined.lower()


def test_cli_requires_some_input(tmp_path):
    result = CliRunner().invoke(main, ["generate", "-o", str(tmp_path / "x.py")])
    assert result.exit_code != 0


def test_cli_from_package_unknown_package_fails_cleanly(tmp_path):
    result = CliRunner().invoke(
        main, ["generate", "--from-package", "definitely_not_installed_xyz", "-o", str(tmp_path / "x")]
    )
    assert result.exit_code == 1
    assert "definitely_not_installed_xyz" in (result.output + result.stderr)


def test_cli_pb2_file_input_redirects_to_from_package(tmp_path):
    """Feeding a compiled _pb2.py as a positional arg gets a helpful redirect
    instead of a cryptic protoc error."""
    fake = tmp_path / "models_pb2.py"
    fake.write_text("# compiled protobuf module")
    result = CliRunner().invoke(main, ["generate", str(fake), "-o", str(tmp_path / "x.py")])
    assert result.exit_code == 1
    assert "--from-package" in (result.output + result.stderr)
