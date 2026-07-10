"""USE CASES: proto2 policy. Default: any proto2 file fails fast with a clear
error — never silently generate models with wrong semantics. Opt-in
(proto2="skip" / --proto2 skip): mixed-syntax packages — common in enterprise
proto repos — generate the proto3 subset only, with skipped files kept in the
descriptor pool for import resolution, an audit comment naming them, and any
proto3→proto2 type reference failing loudly (protoc itself already forbids
proto3 fields of proto2 enum types; messages are the crossable case).
"""

import importlib.resources
import importlib.util
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from google.protobuf import descriptor_pb2
from grpc_tools import protoc

import protodantic
from protodantic import compile_fdset, generate_source, generate_tree
from protodantic.cli import main

_P2_LEGACY = 'syntax = "proto2";\npackage legacy;\nmessage OldRecord { optional string id = 1; }\n'
_P3_CLEAN = 'syntax = "proto3";\npackage app;\nmessage Fresh { string name = 1; }\n'
_P3_IMPORT_ONLY = (
    'syntax = "proto3";\npackage app;\nimport "legacy.proto";\nmessage Fresh { string name = 1; }\n'
)
_P3_BRIDGED = (
    'syntax = "proto3";\npackage app;\nimport "legacy.proto";\n'
    "message UsesMsg { legacy.OldRecord rec = 1; }\n"
)


def _mixed_fdset(*, tmp_path: Path, p3_source: str) -> bytes:
    root = tmp_path / "protos"
    root.mkdir(exist_ok=True)
    (root / "legacy.proto").write_text(_P2_LEGACY)
    (root / "app.proto").write_text(p3_source)
    return compile_fdset([str(root)])


def _import_source(*, source: str, name: str, tmp_path: Path):
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_proto2_is_rejected_with_clear_error(generate):
    """Default policy: a proto2 schema raises the documented error."""
    with pytest.raises(NotImplementedError, match="proto2"):
        generate("legacy.proto")


def test_mixed_package_default_still_errors(tmp_path):
    """Default policy applies to mixed packages too — skipping is opt-in, and
    the error suggests the flag because a proto3 subset exists to generate."""
    fdset = _mixed_fdset(tmp_path=tmp_path, p3_source=_P3_CLEAN)
    with pytest.raises(NotImplementedError, match="proto2") as exc_info:
        generate_source(fdset)
    assert "skip" in str(exc_info.value)


def test_pure_proto2_error_omits_skip_hint(tmp_path):
    """An all-proto2 input must NOT be told to pass --proto2 skip — following
    that advice would immediately fail with 'no proto3 files'."""
    root = tmp_path / "protos"
    root.mkdir()
    (root / "legacy.proto").write_text(_P2_LEGACY)
    fdset = compile_fdset([str(root)])
    with pytest.raises(NotImplementedError, match="proto2") as exc_info:
        generate_source(fdset)
    assert "skip" not in str(exc_info.value)


def test_skip_mode_generates_proto3_subset(tmp_path):
    """proto2="skip" on a cleanly separated mixed package generates the proto3
    models only, with a deterministic audit comment naming the skipped files."""
    fdset = _mixed_fdset(tmp_path=tmp_path, p3_source=_P3_CLEAN)
    source = generate_source(fdset, proto2="skip")
    assert "class Fresh(_pd.ProtoModel)" in source
    assert "OldRecord" not in source
    assert "proto2 files skipped" in source
    assert "legacy.proto" in source
    assert generate_source(fdset, proto2="skip") == source


def test_skip_mode_tree_layout(tmp_path):
    """Tree layout: skipped proto2 files get no modules; the audit comment
    lives in _descriptors.py."""
    fdset = _mixed_fdset(tmp_path=tmp_path, p3_source=_P3_CLEAN)
    files = generate_tree(fdset, proto2="skip")
    assert "app.py" in files
    assert "legacy.py" not in files
    assert "proto2 files skipped" in files["_descriptors.py"]
    assert "legacy.proto" in files["_descriptors.py"]


def test_skip_mode_keeps_pool_dependencies(tmp_path):
    """Skipped proto2 files stay in the embedded fdset: a proto3 file that
    merely imports one still loads its pool and round-trips."""
    fdset = _mixed_fdset(tmp_path=tmp_path, p3_source=_P3_IMPORT_ONLY)
    source = generate_source(fdset, proto2="skip")
    mod = _import_source(source=source, name="p2skip_import_only", tmp_path=tmp_path)
    fresh = mod.Fresh(name="works")
    assert mod.Fresh.from_proto_bytes(fresh.to_proto_bytes()) == fresh


def test_skip_mode_rejects_proto3_to_proto2_bridges(tmp_path):
    """A proto3 field embedding a proto2 message type is a coupling that makes
    the proto3 subset non-generatable: fail loudly naming the field, the type,
    and the file — in both layouts."""
    fdset = _mixed_fdset(tmp_path=tmp_path, p3_source=_P3_BRIDGED)
    for generate_fn in (generate_source, generate_tree):
        with pytest.raises(ValueError) as exc_info:
            generate_fn(fdset, proto2="skip")
        message = str(exc_info.value)
        assert "app.UsesMsg.rec" in message
        assert "legacy.OldRecord" in message
        assert "legacy.proto" in message


def test_skip_mode_with_only_proto2_errors(tmp_path):
    """Skipping everything is not generation: an all-proto2 input under skip
    mode errors instead of emitting an empty module."""
    root = tmp_path / "protos"
    root.mkdir()
    (root / "legacy.proto").write_text(_P2_LEGACY)
    fdset = compile_fdset([str(root)])
    with pytest.raises(ValueError, match="proto3"):
        generate_source(fdset, proto2="skip")


def test_editions_files_error_even_under_skip(tmp_path):
    """proto2="skip" is proto2-specific: an editions file (protobuf's future
    syntax) still errors under both modes — skipping it would mislabel the
    audit trail and silently drop schemas the flag was never about."""
    fdset = descriptor_pb2.FileDescriptorSet()
    future = fdset.file.add()
    future.name = "future.proto"
    future.package = "future"
    future.syntax = "editions"
    future.edition = descriptor_pb2.Edition.EDITION_2023
    current = fdset.file.add()
    current.name = "now.proto"
    current.package = "now"
    current.syntax = "proto3"
    for mode in ("error", "skip"):
        with pytest.raises(NotImplementedError, match="editions"):
            generate_source(fdset.SerializeToString(), proto2=mode)


def test_invalid_proto2_mode_rejected(tmp_path):
    """Unknown mode values fail loudly."""
    fdset = _mixed_fdset(tmp_path=tmp_path, p3_source=_P3_CLEAN)
    with pytest.raises(ValueError, match="bogus"):
        generate_source(fdset, proto2="bogus")


def test_cli_proto2_skip_flag(tmp_path):
    """--proto2 skip unlocks mixed directories; the default still errors."""
    root = tmp_path / "protos"
    root.mkdir()
    (root / "legacy.proto").write_text(_P2_LEGACY)
    (root / "app.proto").write_text(_P3_CLEAN)

    default_run = CliRunner().invoke(main, ["generate", str(root), "-o", str(tmp_path / "out_a")])
    assert default_run.exit_code == 1
    assert "proto2" in (default_run.output + default_run.stderr)

    out = tmp_path / "out_b"
    skip_run = CliRunner().invoke(main, ["generate", str(root), "--proto2", "skip", "-o", str(out)])
    assert skip_run.exit_code == 0, skip_run.output
    assert (out / "app.py").exists()
    assert not (out / "legacy.py").exists()


def test_reflection_mixed_package_skip(tmp_path):
    """The reported enterprise case end-to-end: a mixed proto2/proto3 _pb2
    package reflects into an fdset whose proto3 subset generates under skip."""
    proto_root = tmp_path / "protosrc"
    (proto_root / "mixedorg").mkdir(parents=True)
    (proto_root / "mixedorg" / "legacy.proto").write_text(_P2_LEGACY)
    (proto_root / "mixedorg" / "app.proto").write_text(
        'syntax = "proto3";\npackage app;\nimport "mixedorg/legacy.proto";\n'
        "message Fresh { string name = 1; }\n"
    )
    site = tmp_path / "site"
    site.mkdir()
    wkt = str(importlib.resources.files("grpc_tools") / "_proto")
    assert (
        protoc.main(
            [
                "protoc",
                f"-I{proto_root}",
                f"-I{wkt}",
                f"--python_out={site}",
                str(proto_root / "mixedorg" / "legacy.proto"),
                str(proto_root / "mixedorg" / "app.proto"),
            ]
        )
        == 0
    )
    (site / "mixedorg" / "__init__.py").write_text("")

    modules_before = set(sys.modules)
    sys.path.insert(0, str(site))
    try:
        fdset = protodantic.fdset_from_package("mixedorg")
        source = generate_source(fdset, proto2="skip")
        assert "class Fresh(_pd.ProtoModel)" in source
        assert "mixedorg/legacy.proto" in source  # named in the audit comment
    finally:
        sys.path.remove(str(site))
        for name in [
            m
            for m in sys.modules
            if m not in modules_before and (m == "mixedorg" or m.startswith("mixedorg."))
        ]:
            del sys.modules[name]
