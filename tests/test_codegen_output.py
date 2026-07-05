"""USE CASES: properties of the generated source itself — deterministic output
(CI-diffable), no codegen-time dependencies leaking into runtime, and clear
generated-file marking.
"""

import importlib.resources
from pathlib import Path

import pytest

from protodantic import __version__, compile_fdset, generate_source, generate_tree

PROTO_DIR = Path(__file__).parent / "protos"


@pytest.fixture(scope="module")
def fdset():
    return compile_fdset([str(PROTO_DIR / "demo.proto")], [str(PROTO_DIR)])


def test_generation_is_deterministic(fdset):
    """Same input always yields byte-identical output (safe to commit/diff)."""
    assert generate_source(fdset) == generate_source(fdset)


def test_compile_is_deterministic():
    """Repeated protoc compilation produces generation-equivalent fdsets."""
    a = compile_fdset([str(PROTO_DIR / "demo.proto")], [str(PROTO_DIR)])
    b = compile_fdset([str(PROTO_DIR / "demo.proto")], [str(PROTO_DIR)])
    assert generate_source(a) == generate_source(b)


def test_generated_code_has_no_codegen_dependencies(fdset):
    """Generated modules need only pydantic + protobuf + protodantic runtime;
    grpcio-tools is a codegen-time-only dependency."""
    source = generate_source(fdset)
    assert "grpc_tools" not in source
    assert "protodantic.compiler" not in source
    assert "protodantic.codegen" not in source


def test_generated_code_is_marked_with_version(fdset):
    """Output carries the DO NOT EDIT marker and the protodantic version that
    produced it (future compat checks against committed generated code)."""
    source = generate_source(fdset)
    assert "DO NOT EDIT" in source
    assert f"protodantic {__version__}" in source


def test_flatten_collision_fails_loudly(tmp_path):
    """Nested-type flattening can collide with a literal underscore name
    (Outer.Inner vs Outer_Inner) — codegen must refuse with an error naming
    both proto types, never silently overwrite one class with the other."""
    proto = tmp_path / "flatcoll.proto"
    proto.write_text(
        'syntax = "proto3";\npackage test.flat;\n'
        "message Outer { message Inner { string a = 1; } Inner inner = 1; }\n"
        "message Outer_Inner { int32 b = 1; }\n"
    )
    fdset = compile_fdset([str(proto)])
    with pytest.raises(ValueError) as exc_info:
        generate_source(fdset)
    message = str(exc_info.value)
    assert "test.flat.Outer.Inner" in message
    assert "test.flat.Outer_Inner" in message


def test_enum_member_escape_collision_fails_loudly(tmp_path):
    """Two enum members that escape to the same python name (only reachable via
    allow_alias, e.g. `def` and `def_`) must fail with an error naming both,
    never crash at import with an opaque enum TypeError."""
    proto = tmp_path / "enumcoll.proto"
    proto.write_text(
        'syntax = "proto3";\npackage test.enumcoll;\n'
        "enum E {\n  option allow_alias = true;\n  def = 0;\n  def_ = 0;\n}\n"
    )
    fdset = compile_fdset([str(proto)])
    with pytest.raises(ValueError) as exc_info:
        generate_source(fdset)
    message = str(exc_info.value)
    assert "def and def_ both map to 'def_'" in message


def test_user_protos_under_google_namespace_generate(tmp_path):
    """Only the runtime-handled well-known files are withheld from generation;
    a user proto that happens to live under google/protobuf/ still produces
    models — never silently vanishes. Both layouts."""
    nested = tmp_path / "google" / "protobuf"
    nested.mkdir(parents=True)
    (nested / "company.proto").write_text(
        'syntax = "proto3";\npackage gp;\nmessage Company { string name = 1; }\n'
    )
    fdset = compile_fdset([str(tmp_path)])
    assert "class Company(_pd.ProtoModel)" in generate_source(fdset)
    assert "google/protobuf/company.py" in generate_tree(fdset)


def test_shadowed_wkt_file_with_diverging_content_fails_loudly(tmp_path):
    """A file reusing a reserved runtime-handled name (google/protobuf/
    timestamp.proto) with content that diverges from the shipped descriptor
    fails loudly in both layouts — its types would otherwise be silently
    replaced by runtime equivalents that no longer match the schema."""
    nested = tmp_path / "google" / "protobuf"
    nested.mkdir(parents=True)
    (nested / "timestamp.proto").write_text(
        'syntax = "proto3";\npackage google.protobuf;\n'
        "message Timestamp { string totally_not_seconds = 1; }\n"
    )
    fdset = compile_fdset([str(tmp_path)])
    with pytest.raises(ValueError, match="google/protobuf/timestamp.proto"):
        generate_source(fdset)
    with pytest.raises(ValueError, match="google/protobuf/timestamp.proto"):
        generate_tree(fdset)


def test_shadowed_wkt_with_diverging_labels_fails_loudly(tmp_path):
    """Divergence detection covers ALL wire-significant structure, not just
    names/numbers/types: a shadow with the right fields but a repeated label
    is still a different schema and must be refused."""
    nested = tmp_path / "google" / "protobuf"
    nested.mkdir(parents=True)
    (nested / "timestamp.proto").write_text(
        'syntax = "proto3";\npackage google.protobuf;\n'
        "message Timestamp { repeated int64 seconds = 1; int32 nanos = 2; }\n"
    )
    fdset = compile_fdset([str(tmp_path)])
    with pytest.raises(ValueError, match="google/protobuf/timestamp.proto"):
        generate_source(fdset)
    with pytest.raises(ValueError, match="google/protobuf/timestamp.proto"):
        generate_tree(fdset)


def test_shadowed_wkt_with_custom_json_name_fails_loudly(tmp_path):
    """An explicit json_name override is schema metadata, not a cosmetic
    default emitted by protoc, so a modified vendored WKT is rejected."""
    wkt_root = importlib.resources.files("grpc_tools") / "_proto"
    shipped = (wkt_root / "google" / "protobuf" / "timestamp.proto").read_text()
    modified = shipped.replace(
        "int64 seconds = 1;",
        'int64 seconds = 1 [json_name = "customSeconds"];',
    )
    assert modified != shipped
    nested = tmp_path / "google" / "protobuf"
    nested.mkdir(parents=True)
    (nested / "timestamp.proto").write_text(modified)
    fdset = compile_fdset([str(tmp_path)])
    with pytest.raises(ValueError, match="google/protobuf/timestamp.proto"):
        generate_source(fdset)
    with pytest.raises(ValueError, match="google/protobuf/timestamp.proto"):
        generate_tree(fdset)


def test_vendored_identical_wkt_is_accepted(tmp_path):
    """Orgs vendor the google WKT protos to pin versions: an identical
    vendored copy passes validation and stays runtime-handled."""
    wkt_root = importlib.resources.files("grpc_tools") / "_proto"
    shipped = (wkt_root / "google" / "protobuf" / "timestamp.proto").read_text()
    nested = tmp_path / "google" / "protobuf"
    nested.mkdir(parents=True)
    (nested / "timestamp.proto").write_text(shipped)
    (tmp_path / "uses.proto").write_text(
        'syntax = "proto3";\npackage v;\nimport "google/protobuf/timestamp.proto";\n'
        "message Evt { google.protobuf.Timestamp at = 1; }\n"
    )
    source = generate_source(compile_fdset([str(tmp_path)]))
    assert "class Evt(_pd.ProtoModel)" in source
    assert "_datetime.datetime" in source


def test_proto_without_package_generates(generate, tmp_path):
    """Files with no package declaration are handled."""
    proto = tmp_path / "nopkg.proto"
    proto.write_text('syntax = "proto3";\nmessage Bare { string x = 1; }\n')
    fdset = compile_fdset([str(proto)])
    source = generate_source(fdset)
    assert "class Bare(_pd.ProtoModel)" in source
