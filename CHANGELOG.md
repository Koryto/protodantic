# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Generation from installed `_pb2` packages by descriptor reflection: `protodantic generate --from-package my_org_protos -o generated/` and the `fdset_from_package()` API — no `.proto` sources or protoc invocation needed. Only `*_pb2` modules are imported (helper modules and `_pb2_grpc` stubs are never touched), namespace packages are supported, and module paths derive from the descriptor-recorded proto file names, never the package's python layout. Reflection output is generation-identical to compiling the `.proto` sources.
- `to_proto(into=TheirPb2Class)`: convert a model directly into an instance of a classic `_pb2` class (same proto full name required; `TypeError` naming both types otherwise). Schema-version skew follows wire-compat semantics — newer fields survive older target classes as protobuf unknown fields.
- Clear CLI guardrails: `--from-package` is mutually exclusive with positional protos and `-I`; feeding a `.py` file as a positional input redirects to `--from-package`.

## [0.1.1] - 2026-07-05

### Added

- Python 3.14 support.
- Package-scale generation: `protodantic generate ./protos -o generated/` accepts directories and emits a python package tree mirroring the proto file layout — one module per proto file, relocatable relative imports, and a single shared descriptor pool in `_descriptors.py`. Public API: `generate_tree()`.
- `--layout module|tree` CLI option; defaults follow the input shape (directory → tree, files → module), and layout/`-o` contradictions fail with a clear error.
- `compile_fdset()` accepts directories (recursive `**/*.proto` discovery, dir becomes an import root).
- Deterministic module-path normalization for hostile file names (`foo-bar.proto` → `foo_bar.py`, `class/` → `class_/`, `2fa/` → `_2fa/`, reserved stems escaped); path collisions fail loudly naming both files.
- Managed-clean regeneration: re-running into an existing generated tree replaces it wholesale (stale modules from deleted protos disappear); any non-generated file aborts the run before anything is modified.
- External `-I` imports are emitted into the generated tree, keeping it self-contained.

## [0.1.0] - 2026-07-03

Initial release: the greenfield drop.

### Added

- `protodantic generate` CLI and `compile_fdset()`/`generate_source()` API: compile `.proto` files (proto3) into pydantic v2 models with embedded descriptors — no `_pb2.py` files needed.
- Lossless bidirectional round-trips on every generated model: `to_proto()`, `to_proto_bytes()`, `to_proto_json()`, `from_proto()`, `from_proto_bytes()`, `from_proto_json()` — the wire output is canonical protobuf, parseable by any runtime in any language.
- Interop with classic protoc-generated `_pb2` classes: `from_proto()` accepts their instances directly.
- Validation at the boundary: proto integer range constraints, oneof mutual exclusion (construction *and* mutation, atomically), `extra="forbid"`, all overridable via standard pydantic config.
- Presence semantics: `None` ⇄ unset for `optional` fields, oneof members, and singular messages; explicit zero values keep their presence bit.
- Open enums (`OpenEnum`): unknown wire values are preserved as pseudo-members instead of raising, matching proto3 semantics.
- Well-known types: `Timestamp` ⇄ `datetime` (UTC), `Duration` ⇄ `timedelta`, wrappers ⇄ `T | None`, `Struct`/`Value`/`ListValue` ⇄ plain python data with the `protodantic.NULL` sentinel for explicit JSON null, `Any` ⇄ any generated model via registry-based pack/unpack.
- Field-name hazard handling: python keywords and pydantic-reserved names get a trailing underscore with the proto name as populate alias; generated imports are shadow-proof against user message names.
- `model_for("pkg.Message")` lookup; duplicate generated modules coexist safely (nested resolution is scoped per generated module).
- Fail-loud guarantees: proto2 input, flattened-name collisions, unrelated message types in `from_proto()`, and unknown `Any` type URLs all raise clear errors — no silent wrong data.

### Notes

- Distribution name is `protodantic-py`; the import name is `protodantic`.
- Requires Python >= 3.11. proto3 only, by design. gRPC service stubs are out of scope.
