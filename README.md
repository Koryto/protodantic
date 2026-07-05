# protodantic

[![CI](https://github.com/Koryto/protodantic/actions/workflows/ci.yml/badge.svg)](https://github.com/Koryto/protodantic/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/protodantic-py)](https://pypi.org/project/protodantic-py/)
[![Python](https://img.shields.io/pypi/pyversions/protodantic-py)](https://pypi.org/project/protodantic-py/)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/latest/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Bidirectional bridge between **Protocol Buffers** and **Pydantic**.

Point it at your `.proto` files and it generates plain pydantic v2 models — with full validation — where every model round-trips losslessly to and from real protobuf messages, wire bytes, and proto JSON. The pydantic → proto direction is a first-class citizen: `to_proto_bytes()` produces genuine wire-format output that any protobuf consumer in any language can parse.

## Install

```sh
uv add protodantic-py   # or: pip install protodantic-py
```

The distribution is named `protodantic-py` (the plain name is squatted on PyPI); the import stays `protodantic`:

```python
import protodantic
```

## Usage

Given `demo.proto`:

```proto
syntax = "proto3";
package demo;

message Address {
  string street = 1;
  string city = 2;
}

message User {
  int64 id = 1;
  string name = 2;
  Address address = 3;
  repeated string tags = 4;
  optional string nickname = 5;
}
```

Generate models:

```sh
protodantic generate demo.proto -o models.py
```

Or point it at a whole directory of protos to get a python package tree mirroring your proto files (one module per file, single shared descriptor pool, relocatable):

```sh
protodantic generate ./protos -o generated/
# generated/myorg/billing.py, generated/myorg/common.py, ...
```

```python
from generated.myorg.billing import Invoice
```

Layout follows the input shape (files → single module, directory → package tree); override with `--layout module|tree`. Regenerating into an existing tree is managed-clean: stale modules from deleted protos are removed, and any file protodantic didn't generate aborts the run untouched.

Then:

```python
from models import User, Address

user = User(id=7, name="kory", address=Address(city="Warsaw"), tags=["a", "b"])

# pydantic -> proto: real wire format, readable by any protobuf runtime
data: bytes = user.to_proto_bytes()
msg = user.to_proto()          # a live protobuf Message
json_str = user.to_proto_json()  # canonical proto JSON

# proto -> pydantic: parse + validate in one step
restored = User.from_proto_bytes(data)
assert restored == user
```

Or drive it from Python:

```python
from protodantic import compile_fdset, generate_source

source = generate_source(compile_fdset(["demo.proto"]))
```

## Type mapping

| proto | pydantic |
| --- | --- |
| `int32/64`, `uint32/64`, `sint`, `fixed` | range-validated `int` (out-of-range fails at construction) |
| `float`, `double` | `float` |
| `string` / `bytes` / `bool` | `str` / `bytes` / `bool` |
| `enum` | generated `OpenEnum` (`IntEnum` that preserves unknown wire values — proto3 enums are open) |
| `message` | generated `ProtoModel` (nested types flattened as `Outer_Inner`) |
| `repeated T` / `map<K, V>` | `list[T]` / `dict[K, V]` |
| `optional`, oneof members, singular messages | `T \| None` (presence-aware: `None` ⇄ unset) |
| oneof groups | mutual exclusion enforced by a model validator |
| `google.protobuf.Timestamp` | `datetime.datetime` (UTC; naive input treated as UTC) |
| `google.protobuf.Duration` | `datetime.timedelta` |
| `google.protobuf.*Value` wrappers | `T \| None` |
| `google.protobuf.Struct` / `Value` / `ListValue` | `dict[str, Any]` / `Any` / `list[Any]` |
| `google.protobuf.Any` | `typing.Any` — accepts any `ProtoModel`; packed/unpacked via the model registry |

Field names that collide with python keywords or pydantic internals (`class`, `from`, `model_config`, ...) get a trailing underscore (`class_`) with the proto name kept as a populate alias. The same rule applies to message/enum type names and enum members that are python keywords or would shadow generated code (`message list` → `class list_`) — the proto full name stays the source of truth. Same-named messages in different packages get package-qualified class names; every model is also reachable via `protodantic.model_for("pkg.Message")`.

Semantics worth knowing:

- **Validation on mutation is on by default** (`validate_assignment=True`): assigning a second oneof member or an out-of-range int raises immediately. Opt out per-model with standard pydantic config on a subclass.
- **`protodantic.NULL`** expresses an explicit JSON null in a `google.protobuf.Value` field (`None` means *unset*). In `model_dump_json()` it serializes as real `null`; python-mode dumps keep the sentinel.
- **Subclassing a generated model does not affect parsing**: `from_proto`/`model_for` keep resolving to the generated class. To make your subclass the resolution target (e.g. to add custom validators applied on parse), re-declare `__proto_full_name__` in its body — explicit opt-in.

## Interop with existing `_pb2` code

Already consuming a centralized proto package as protoc-generated `_pb2` modules? Generate models straight from it — no `.proto` sources needed:

```sh
protodantic generate --from-package my_org_protos -o generated/
```

Reflection imports only the `*_pb2` modules (helpers and grpc stubs are never touched) and produces output identical to compiling the original `.proto` files. And generated models interoperate with `_pb2` instances directly:

```python
user = User.from_proto(their_pb2_user_instance)   # accepts _pb2 messages
their_msg = user.to_proto(into=their_pb2.User)    # returns THEIR class
raw = their_pb2.User.FromString(user.to_proto_bytes())  # canonical bytes
```

## How it works

`protoc` (bundled via `grpcio-tools`) compiles your protos to a `FileDescriptorSet`, which codegen embeds in the generated module. At runtime, `ProtoModel` builds dynamic protobuf message classes from those descriptors — no `_pb2.py` files needed, and no protobuf internals leak into your models.

If several imported generated modules define the same proto type, the registry behind `model_for()` / nested-message resolution is last-import-wins.

## Status & roadmap

Requires Python ≥ 3.11. proto3 only by design (proto2 input is rejected with a clear error). The full supported-behavior spec lives in [tests/](tests/) — every test documents one use case. Documented policies: unknown fields are dropped when a model re-serializes (the model is the source of truth), and naive datetimes are interpreted as UTC.

- **0.1.x (current)** — greenfield: `.proto` files *and directories* → pydantic codegen (single module or package tree) with lossless bidirectional round-trips, plus the semantics future drops build on.
- **0.1.2 — greenfield closeout** — generation from installed `_pb2` packages by descriptor reflection (no `.proto` sources needed), plus `to_proto(into=TheirPb2Class)`.
- **0.2.x — brownfield** — reverse schema codegen: pydantic models → `.proto`.
- **0.3.0 — performance** — benchmark suite (vs `json.loads`+pydantic, raw `_pb2`, betterproto), then cached field plans and trusted-construction fast paths.

gRPC service stubs are out of scope: protodantic is a message layer.
