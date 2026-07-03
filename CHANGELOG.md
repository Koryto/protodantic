# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
