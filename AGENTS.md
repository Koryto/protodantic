# protodantic — agent guide

Bidirectional bridge between Protocol Buffers and Pydantic. Distribution name `protodantic-py`, import name `protodantic`. proto3 only, by design. Read this before changing anything: the project runs on a small set of hard conventions, and code review enforces them strictly — working code that violates them gets rejected.

## Architecture

```
src/protodantic/
  compiler.py   .proto files/directories -> serialized FileDescriptorSet bytes (protoc via grpcio-tools)
  codegen.py    fdset bytes -> single module source (generate_source) or package tree (generate_tree)
  runtime.py    ProtoModel base: conversion both ways, registries, OpenEnum, field-name escaping
  types.py      range-validated ints, Struct/Value/ListValue aliases, NULL sentinel
  cli.py        click group; `generate` subcommand w/ --layout module|tree (future verbs must be additive)
  _version.py   single version source (hatch + generated-code stamps read it)
```

The **fdset-bytes boundary is load-bearing**: codegen takes `FileDescriptorSet` bytes and nothing else. Future input sources (installed `_pb2` packages via descriptor reflection, planned for 0.2) must produce those bytes and feed the same codegen. Do not add codegen inputs that bypass it.

## Philosophy (non-negotiable)

1. **The test suite is the specification.** Every supported behavior exists as a use-case test with a "USE CASE" style docstring. Workflow is red/green: write the failing spec test first, run it to confirm it fails *for the intended reason*, then implement, then confirm green. A red test in the suite is an accepted roadmap item, not a broken build.
2. **Fail loudly; no magic.** proto2 input → `NotImplementedError`. Name collisions (flattened types, escaped enum members, module paths) → `ValueError` naming the culprits and telling the user what to rename. Wrong message type in `from_proto` → `TypeError`. Unknown `Any` type URL → `LookupError`. Unknown field names at construction → rejected (`extra="forbid"`). Never emit silently-wrong data or auto-disambiguate with positional suffixes. Exactly two automatic renames are allowed, both pure functions of the name alone (never dependent on what else exists in the schema): (a) the trailing-underscore escape for python keywords/reserved names (`class` → `class_`), applied to fields, type names, and enum members; (b) module-path normalization for tree output — per segment, characters outside `[A-Za-z0-9_]` become `_` (1:1, no collapsing), a leading digit gains a `_` prefix, and keywords/reserved stems (`__init__`, `_descriptors`) get the trailing underscore. Post-rename collisions always fail loudly.
3. **Validation is the product.** Models validate at construction *and* on assignment, and assignment is atomic: a rejected mutation must leave the model exactly as it was. If you add a validator, prove atomicity in a test.
4. **Scope decisions belong to the maintainer.** If "should we support X?" has no clear answer in the tests or this file, raise it as a question before implementing. Precedents: proto2 was consciously dropped; `Any` consciously maps to `typing.Any` with registry pack/unpack.

## Semantics ledger (do not regress; each line is pinned by tests)

- `None` means *unset on the wire*; explicit zero values keep their presence bit. `protodantic.NULL` (identity-checked singleton) means explicit JSON null in `Value` fields.
- proto3 enums are open: unknown wire values become `OpenEnum` pseudo-members, never errors, and re-serialize byte-identically.
- Unknown fields are dropped when a model re-serializes (the model is the source of truth). Naive datetimes are interpreted as UTC.
- Generated modules are deterministic (byte-identical output for the same input — they're meant to be committed and diffed), version-stamped, and depend only on `protodantic` + pydantic + protobuf at runtime (never `grpc_tools`). All imports in generated code are underscore-aliased (`import protodantic as _pd`, `import typing as _typing`, ...) so user message names (`message Any`, `message list`) cannot shadow them.
- Nested-message resolution is scoped per descriptor pool (per generated module), so duplicate generated modules coexist in one process. `model_for()` is global, last-import-wins. Plain subclasses of generated models do **not** register; re-declaring `__proto_full_name__` in the subclass body is the explicit opt-in.
- `_pb2` interop is a public contract: `from_proto()` accepts classic protoc-generated instances; `to_proto_bytes()` output parses into `_pb2` classes.
- Tree output (`generate_tree` / directory input): one module per proto **file** (paths derive from file paths, never proto packages), a single shared pool in `_descriptors.py`, root-anchored relative imports (trees are relocatable), external `-I` imports emitted into the tree. Layout defaults follow input shape (directory → tree, files → module); `--layout` overrides; layout/`-o` contradictions fail loudly.
- Tree regeneration is managed-clean: if every file in the output dir carries the generated header (bytecode caches excluded), the dir is replaced wholesale so stale modules die; any foreign file aborts *before* any mutation.

## Code conventions

- **Keyword-only parameters for all internal functions** (`def _fill_map(*, target, fd, value)`). Positional args are reserved for framework-fixed signatures (pydantic serializer callbacks) and stdlib-stable idioms — if a signature could ever grow a flag, it takes kwargs.
- **No nested function definitions.** State + recursion live in classes (see `_ModuleGenerator`); pure helpers go to module level.
- **Relative imports** inside the package. Generated code imports only the top-level `protodantic`.
- **No trivial docstrings or comments.** Comment only the non-obvious "why" (e.g. why registration checks `cls.__dict__`). Public functions get short contract docstrings.
- Magic strings for well-known types are forbidden — derive full names from the shipped descriptors (`timestamp_pb2.Timestamp.DESCRIPTOR.full_name`).
- CLI output goes through click (`click.echo`, `click.ClickException`), never bare `print`.

## Workflow

- uv-managed: `uv sync`, then `uv run pytest tests/ -q`.
- Python floor is 3.11. When touching `runtime.py`/`codegen.py`, run both: `uv run pytest tests/` and `uv run --python 3.11 --isolated pytest tests/`. CI runs ubuntu+windows × 3.11/3.12/3.13; all six must be green.
- Releases happen on merge to `master`: the workflow publishes to PyPI iff `_version.py` holds a version not yet published. A releasing PR bumps `_version.py` **and** moves `CHANGELOG.md` `[Unreleased]` entries under the new version heading, atomically.
- Update `CHANGELOG.md` `[Unreleased]` for any user-facing change (Keep a Changelog format).
- Never commit generated artifacts, secrets, or machine-local paths. Test protos live in `tests/protos/`; temporary schemas belong in `tmp_path`.

## Roadmap context (shapes what "don't block the future" means)

- **0.1.0 (current)** — greenfield: `.proto` → pydantic with lossless bidirectional round-trips.
- **0.2.0 — brownfield**: reverse schema codegen (pydantic models → `.proto`), generating from installed `_pb2` packages by descriptor reflection, `to_proto(into=TheirPb2Class)`. Weigh brownfield adoption at least as high as greenfield polish.
- **0.3.0 — performance**: benchmark suite first (vs `json.loads`+pydantic, raw `_pb2`, betterproto) — **benchmarks before perf claims** — then cached field plans and trusted-construction fast paths. The conversion internals are deliberately unconstrained by public API; keep it that way.
- gRPC service stubs are permanently out of scope: protodantic is a message layer.
