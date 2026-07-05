"""USE CASES: generating models from an installed _pb2 package by descriptor
reflection. Reflection produces FileDescriptorSet bytes for the standard
code-generation path.
CLI: `protodantic generate --from-package NAME -o gen/` (tree layout default,
module paths derive from the proto file names recorded in the descriptors —
never from the _pb2 python layout).
"""

import importlib
import importlib.resources
import shutil
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


def _purge_modules(*, prefix: str, baseline: set[str]) -> None:
    for name in [
        module
        for module in sys.modules
        if module not in baseline and (module == prefix or module.startswith(prefix + "."))
    ]:
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
    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    yield "myorg"
    sys.path.remove(str(site))
    _purge_modules(prefix="myorg", baseline=modules_before)


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
    """Reflection is byte-identical across calls and orders dependencies before
    dependents, with lexical ordering between independent files."""
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
    """Reflection and source compilation produce identical model modules."""
    via_reflection = protodantic.generate_tree(protodantic.fdset_from_package(myorg_pb2))
    via_protoc = protodantic.generate_tree(compile_fdset(MYORG_PROTOS, [str(TREE_DIR)]))
    reflection_modules = {k: v for k, v in via_reflection.items() if k != "_descriptors.py"}
    protoc_modules = {k: v for k, v in via_protoc.items() if k != "_descriptors.py"}
    assert reflection_modules == protoc_modules


def test_fdset_from_package_missing_package_raises():
    """A package that isn't installed surfaces as ModuleNotFoundError — the
    honest python error, not a swallowed empty result."""
    with pytest.raises(ModuleNotFoundError):
        protodantic.fdset_from_package("definitely_not_installed_xyz")


def test_fdset_from_package_without_descriptors_raises(tmp_path):
    """A package with no protobuf modules is a clear error, not empty output."""
    pkg = tmp_path / "emptypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    modules_before = set(sys.modules)
    sys.path.insert(0, str(tmp_path))
    try:
        with pytest.raises(ValueError, match="emptypkg"):
            protodantic.fdset_from_package("emptypkg")
    finally:
        sys.path.remove(str(tmp_path))
        _purge_modules(prefix="emptypkg", baseline=modules_before)


def _build_pb2_site(*, tmp_path: Path, package: str, with_init: bool) -> Path:
    """A site dir holding <package>/mini_pb2.py plus three booby-trapped
    neighbors that raise if imported: a helper module, a _pb2_grpc stub, and
    an unrelated subpackage."""
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
    # the realistic adjacent hazard: grpc stubs live NEXT to _pb2 modules and
    # need grpcio at import — a sloppy '_pb2 in name' match would import them
    (site / package / "mini_pb2_grpc.py").write_text(
        "raise RuntimeError('grpc stub must not be imported')\n"
    )
    # an unrelated subpackage with a trapped initializer and no _pb2 content:
    # discovery must never import it (package traversal != package importing)
    danger = site / package / "danger"
    danger.mkdir()
    (danger / "__init__.py").write_text("raise RuntimeError('unrelated subpackage imported')\n")
    (danger / "config_helper.py").write_text("raise RuntimeError('unrelated module imported')\n")
    if with_init:
        (site / package / "__init__.py").write_text("")
    return site


def test_reflection_imports_only_pb2_modules(tmp_path):
    """Enterprise packages carry helper modules with import side effects (or
    grpc stubs needing extra deps): reflection must import only *_pb2 modules
    — the protoc naming contract — never the rest."""
    site = _build_pb2_site(tmp_path=tmp_path, package="sidefx", with_init=True)
    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        fdset_bytes = protodantic.fdset_from_package("sidefx")
        names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes).file}
        assert "sidefx/mini.proto" in names
    finally:
        sys.path.remove(str(site))
        _purge_modules(prefix="sidefx", baseline=modules_before)


def test_reflection_supports_namespace_packages(tmp_path):
    """PEP 420 namespace packages (no __init__.py) — common for enterprise
    proto wheels — are discoverable too."""
    site = _build_pb2_site(tmp_path=tmp_path, package="sidens", with_init=False)
    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        fdset_bytes = protodantic.fdset_from_package("sidens")
        names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes).file}
        assert "sidens/mini.proto" in names
    finally:
        sys.path.remove(str(site))
        _purge_modules(prefix="sidens", baseline=modules_before)


def test_reflection_supports_nested_namespace_packages(tmp_path):
    """PEP 420 all the way down: _pb2 modules nested in namespace SUBdirs
    (no __init__.py at any level) are discovered."""
    proto_root = tmp_path / "protosrc"
    (proto_root / "acme" / "contracts").mkdir(parents=True)
    (proto_root / "acme" / "contracts" / "foo.proto").write_text(
        'syntax = "proto3";\npackage ac;\nmessage Foo { string v = 1; }\n'
    )
    site = tmp_path / "site"
    site.mkdir()
    assert protoc.main([
        "protoc", f"-I{proto_root}", f"--python_out={site}",
        str(proto_root / "acme" / "contracts" / "foo.proto"),
    ]) == 0
    # no __init__.py anywhere — nested namespace package

    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        fdset_bytes = protodantic.fdset_from_package("acme")
        names = {f.name for f in descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes).file}
        assert "acme/contracts/foo.proto" in names
    finally:
        sys.path.remove(str(site))
        _purge_modules(prefix="acme", baseline=modules_before)


def test_reflection_rejects_pb2_module_without_descriptor(tmp_path):
    """A module matching the *_pb2 naming contract but exposing no protobuf
    FileDescriptor is a loud error naming the module — never a silently
    smaller fdset."""
    site = _build_pb2_site(tmp_path=tmp_path, package="brokenpkg", with_init=True)
    (site / "brokenpkg" / "broken_pb2.py").write_text("DESCRIPTOR = None\n")
    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        with pytest.raises(ValueError, match="broken_pb2"):
            protodantic.fdset_from_package("brokenpkg")
    finally:
        sys.path.remove(str(site))
        _purge_modules(prefix="brokenpkg", baseline=modules_before)


def test_reflection_fails_loudly_on_broken_subpackage(tmp_path):
    """An import failure in a discovered _pb2 subpackage is propagated instead
    of producing an incomplete descriptor set."""
    proto_root = tmp_path / "protosrc"
    (proto_root / "badpkg" / "sub").mkdir(parents=True)
    (proto_root / "badpkg" / "mini.proto").write_text(
        'syntax = "proto3";\npackage bp;\nmessage T1 { string v = 1; }\n'
    )
    (proto_root / "badpkg" / "sub" / "other.proto").write_text(
        'syntax = "proto3";\npackage bps;\nmessage T2 { string v = 1; }\n'
    )
    site = tmp_path / "site"
    site.mkdir()
    assert protoc.main([
        "protoc", f"-I{proto_root}", f"--python_out={site}",
        str(proto_root / "badpkg" / "mini.proto"),
        str(proto_root / "badpkg" / "sub" / "other.proto"),
    ]) == 0
    (site / "badpkg" / "__init__.py").write_text("")
    (site / "badpkg" / "sub" / "__init__.py").write_text("raise ImportError('boom')\n")

    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        with pytest.raises(ImportError, match="boom"):
            protodantic.fdset_from_package("badpkg")
    finally:
        sys.path.remove(str(site))
        _purge_modules(prefix="badpkg", baseline=modules_before)


def test_cli_pb2_directory_input_redirects_to_from_package(tmp_path):
    """A directory holding compiled _pb2 modules but no .proto sources gets
    the same helpful redirect as a _pb2.py file input."""
    pkg = tmp_path / "compiled"
    pkg.mkdir()
    (pkg / "models_pb2.py").write_text("# compiled protobuf module")
    result = CliRunner().invoke(main, ["generate", str(pkg), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "--from-package" in (result.output + result.stderr)


# positional args by contract: monkeypatch replacement for Path.rglob(self, pattern)
def _rglob_permission_denied(self, pattern):
    raise PermissionError("denied by test")


def test_cli_preflight_oserror_fails_cleanly(tmp_path, monkeypatch):
    """Filesystem errors during input preflight (e.g. permission denied while
    scanning a directory) surface as clean CLI errors, never tracebacks."""
    root = tmp_path / "protos"
    root.mkdir()
    (root / "a.proto").write_text('syntax = "proto3";\npackage pf;\nmessage A { string v = 1; }\n')

    monkeypatch.setattr(Path, "rglob", _rglob_permission_denied)
    result = CliRunner().invoke(main, ["generate", str(root), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "denied by test" in (result.output + result.stderr)


def test_layout_follows_descriptor_names_not_python_layout(tmp_path):
    """_pb2 modules buried under an unrelated python container still generate
    at their DESCRIPTOR-recorded proto paths — the package's python layout
    never determines the generated layout."""
    proto_root = tmp_path / "protosrc"
    (proto_root / "wire").mkdir(parents=True)
    (proto_root / "wire" / "format.proto").write_text(
        'syntax = "proto3";\npackage wf;\nmessage Frame { string v = 1; }\n'
    )
    build = tmp_path / "build"
    build.mkdir()
    assert protoc.main(
        ["protoc", f"-I{proto_root}", f"--python_out={build}", str(proto_root / "wire" / "format.proto")]
    ) == 0

    # bury the compiled module two container levels deep — python layout
    # (orgbundle.inner.wire) deliberately disagrees with proto path (wire/)
    site = tmp_path / "site"
    container = site / "orgbundle" / "inner"
    shutil.copytree(build / "wire", container / "wire")
    for directory in (site / "orgbundle", container, container / "wire"):
        (directory / "__init__.py").write_text("")

    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        tree = protodantic.generate_tree(protodantic.fdset_from_package("orgbundle"))
        assert "wire/format.py" in tree
        assert not any("orgbundle" in path or "inner" in path for path in tree)
    finally:
        sys.path.remove(str(site))
        _purge_modules(prefix="orgbundle", baseline=modules_before)


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

    modules_before = set(sys.modules)
    sys.path.insert(0, str(out_root))
    try:
        billing = importlib.import_module("reflected.myorg.billing")
        common = importlib.import_module("reflected.myorg.common")
        invoice = billing.Invoice(id="r-1", total=common.Money(currency="PLN", units=7))
        assert billing.Invoice.from_proto_bytes(invoice.to_proto_bytes()) == invoice
    finally:
        sys.path.remove(str(out_root))
        _purge_modules(prefix="reflected", baseline=modules_before)


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
    """No positional protos and no --from-package is a specific contract
    error naming both input forms — not a generic usage message."""
    result = CliRunner().invoke(main, ["generate", "-o", str(tmp_path / "x.py")])
    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert ".proto" in combined
    assert "--from-package" in combined


def test_cli_from_package_unknown_package_fails_cleanly(tmp_path):
    """A typo'd package name exits 1 with the name in the message — no
    traceback."""
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
