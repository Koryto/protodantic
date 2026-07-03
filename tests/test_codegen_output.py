"""USE CASES: properties of the generated source itself — deterministic output
(CI-diffable), no codegen-time dependencies leaking into runtime, and clear
generated-file marking.
"""

from pathlib import Path

import pytest

from protodantic import compile_fdset, generate_source

PROTO_DIR = Path(__file__).parent / "protos"


@pytest.fixture(scope="module")
def fdset():
    return compile_fdset([str(PROTO_DIR / "demo.proto")], [str(PROTO_DIR)])


def test_generation_is_deterministic(fdset):
    """Same input always yields byte-identical output (safe to commit/diff)."""
    assert generate_source(fdset) == generate_source(fdset)


def test_compile_is_deterministic():
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
    from protodantic import __version__

    source = generate_source(fdset)
    assert "DO NOT EDIT" in source
    assert f"protodantic {__version__}" in source


def test_proto_without_package_generates(generate, tmp_path):
    """Files with no package declaration are handled."""
    proto = tmp_path / "nopkg.proto"
    proto.write_text('syntax = "proto3";\nmessage Bare { string x = 1; }\n')
    fdset = compile_fdset([str(proto)])
    source = generate_source(fdset)
    assert "class Bare(ProtoModel)" in source
