# protodantic

Bidirectional bridge between **Protocol Buffers** and **Pydantic**.

Point it at your `.proto` files and it generates plain pydantic v2 models — with full validation — where every model round-trips losslessly to and from real protobuf messages, wire bytes, and proto JSON. The pydantic → proto direction is a first-class citizen: `to_proto_bytes()` produces genuine wire-format output that any protobuf consumer in any language can parse.

## Install

```sh
uv add protodantic   # or: pip install protodantic
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
protodantic demo.proto -o models.py
```

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

Field names that collide with python keywords or pydantic internals (`class`, `from`, `model_config`, ...) get a trailing underscore (`class_`) with the proto name kept as a populate alias. Same-named messages in different packages get package-qualified class names; every model is also reachable via `protodantic.model_for("pkg.Message")`.

Semantics worth knowing:

- **Validation on mutation is on by default** (`validate_assignment=True`): assigning a second oneof member or an out-of-range int raises immediately. Opt out per-model with standard pydantic config on a subclass.
- **`protodantic.NULL`** expresses an explicit JSON null in a `google.protobuf.Value` field (`None` means *unset*). In `model_dump_json()` it serializes as real `null`; python-mode dumps keep the sentinel.
- **Subclassing a generated model does not affect parsing**: `from_proto`/`model_for` keep resolving to the generated class. To make your subclass the resolution target (e.g. to add custom validators applied on parse), re-declare `__proto_full_name__` in its body — explicit opt-in.

## Interop with existing `_pb2` code

Already consuming a centralized proto package as protoc-generated `_pb2` modules? Generated models interoperate directly:

```python
user = User.from_proto(their_pb2_user_instance)   # accepts _pb2 messages
their_msg = their_pb2.User.FromString(user.to_proto_bytes())  # canonical bytes
```

## How it works

`protoc` (bundled via `grpcio-tools`) compiles your protos to a `FileDescriptorSet`, which codegen embeds in the generated module. At runtime, `ProtoModel` builds dynamic protobuf message classes from those descriptors — no `_pb2.py` files needed, and no protobuf internals leak into your models.

If several imported generated modules define the same proto type, the registry behind `model_for()` / nested-message resolution is last-import-wins.

## Status & roadmap

Requires Python ≥ 3.11. proto3 only by design (proto2 input is rejected with a clear error). The full supported-behavior spec lives in [tests/](tests/) — every test documents one use case. Documented policies: unknown fields are dropped when a model re-serializes (the model is the source of truth), and naive datetimes are interpreted as UTC.

- **0.1.0 (current)** — greenfield: `.proto` → pydantic codegen with lossless bidirectional round-trips, plus the semantics future drops build on.
- **0.2.0 — brownfield** — reverse schema codegen (pydantic models → `.proto`), generating from installed `_pb2` packages by descriptor reflection, `to_proto(into=TheirPb2Class)`.
- **0.3.0 — performance** — benchmark suite (vs `json.loads`+pydantic, raw `_pb2`, betterproto), then cached field plans and trusted-construction fast paths.

gRPC service stubs are out of scope: protodantic is a message layer.
