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

## How it works

`protoc` (bundled via `grpcio-tools`) compiles your protos to a `FileDescriptorSet`, which codegen embeds in the generated module. At runtime, `ProtoModel` builds dynamic protobuf message classes from those descriptors — no `_pb2.py` files needed, and no protobuf internals leak into your models.

## Status

v0.1 — proto3 only by design (proto2 input is rejected with a clear error). The full supported-behavior spec lives in [tests/](tests/) — 63 use-case tests, all green. Documented policies: unknown fields are dropped when a model re-serializes (the model is the source of truth), and naive datetimes are interpreted as UTC. gRPC service stubs are out of scope for now.
