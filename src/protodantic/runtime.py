from __future__ import annotations

import datetime
import enum
import keyword
from typing import Any, ClassVar, Self

from google.protobuf import (
    any_pb2,
    descriptor_pb2,
    descriptor_pool,
    duration_pb2,
    json_format,
    message_factory,
    struct_pb2,
    timestamp_pb2,
    wrappers_pb2,
)
from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.protobuf.message import Message
from pydantic import BaseModel, ConfigDict, model_validator

from .types import NULL, _strip_null_sentinel

_TIMESTAMP = timestamp_pb2.Timestamp.DESCRIPTOR.full_name
_DURATION = duration_pb2.Duration.DESCRIPTOR.full_name
_ANY = any_pb2.Any.DESCRIPTOR.full_name
_VALUE = struct_pb2.Value.DESCRIPTOR.full_name
_STRUCT_TYPES = frozenset(
    m.DESCRIPTOR.full_name for m in (struct_pb2.Struct, struct_pb2.Value, struct_pb2.ListValue)
)
_WRAPPER_TYPES = frozenset(
    m.DESCRIPTOR.full_name
    for m in (
        wrappers_pb2.DoubleValue,
        wrappers_pb2.FloatValue,
        wrappers_pb2.Int64Value,
        wrappers_pb2.UInt64Value,
        wrappers_pb2.Int32Value,
        wrappers_pb2.UInt32Value,
        wrappers_pb2.BoolValue,
        wrappers_pb2.StringValue,
        wrappers_pb2.BytesValue,
    )
)

# nested-message resolution is scoped per descriptor pool (i.e. per generated
# module) so duplicate generated modules can coexist in one process; the
# by-name registry backs model_for() and Any fallback (last import wins)
_MODELS_BY_POOL: dict[tuple[Any, str], type[ProtoModel]] = {}
_MODELS_BY_NAME: dict[str, type[ProtoModel]] = {}

_RESERVED_NAMES = frozenset({
    "proto_class",
    "to_proto",
    "to_proto_bytes",
    "to_proto_json",
    "from_proto",
    "from_proto_bytes",
    "from_proto_json",
})


def python_field_name(*, proto_name: str) -> str:
    """Python attribute for a proto field: keywords, model_*, and ProtoModel API
    names get a trailing underscore; the proto name stays usable as an alias."""
    if (
        keyword.iskeyword(proto_name)
        or proto_name.startswith("model_")
        or proto_name in _RESERVED_NAMES
    ):
        return proto_name + "_"
    return proto_name


def model_for(full_name: str) -> type[ProtoModel]:
    """Generated model class for a proto full name (e.g. "pkg.Msg"). The module
    defining it must be imported first; on duplicates the last import wins."""
    try:
        return _MODELS_BY_NAME[full_name]
    except KeyError:
        raise KeyError(
            f"no generated model imported for proto type {full_name!r}"
        ) from None


def load_pool(fdset_bytes: bytes) -> descriptor_pool.DescriptorPool:
    """Build a fresh DescriptorPool from a serialized FileDescriptorSet."""
    fdset = descriptor_pb2.FileDescriptorSet.FromString(fdset_bytes)
    pool = descriptor_pool.DescriptorPool()
    for file_proto in fdset.file:
        pool.Add(file_proto)
    return pool


class OpenEnum(enum.IntEnum):
    """IntEnum matching proto3 open-enum semantics: values missing from the
    schema become pseudo-members instead of raising."""

    @classmethod
    def _missing_(cls, value: object) -> OpenEnum | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        pseudo = int.__new__(cls, value)
        pseudo._name_ = None
        pseudo._value_ = value
        return pseudo


def _is_map(*, fd: FieldDescriptor) -> bool:
    return (
        fd.type == FieldDescriptor.TYPE_MESSAGE
        and fd.message_type.GetOptions().map_entry
    )


def _scalar_to_proto(*, value: Any) -> Any:
    # enum members (incl. open-enum pseudo-members) flatten to plain ints
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _resolve_model(*, full_name: str, pool: Any) -> type[ProtoModel] | None:
    model_cls = _MODELS_BY_POOL.get((pool, full_name))
    if model_cls is not None:
        return model_cls
    return _MODELS_BY_NAME.get(full_name)


def _fill_message(*, target: Message, value: Any) -> None:
    full_name = target.DESCRIPTOR.full_name
    if full_name == _TIMESTAMP:
        target.FromDatetime(value)
    elif full_name == _DURATION:
        target.FromTimedelta(value)
    elif full_name == _ANY:
        if not isinstance(value, ProtoModel):
            raise TypeError(
                f"google.protobuf.Any fields accept ProtoModel instances, got {type(value).__name__}"
            )
        target.Pack(value.to_proto())
    elif full_name in _STRUCT_TYPES:
        json_format.ParseDict(_strip_null_sentinel(value), target)
    elif full_name in _WRAPPER_TYPES:
        target.value = value
    else:
        target.CopyFrom(value.to_proto())


def _message_to_python(*, msg: Message, pool: Any) -> Any:
    full_name = msg.DESCRIPTOR.full_name
    if full_name == _TIMESTAMP:
        return msg.ToDatetime(tzinfo=datetime.timezone.utc)
    if full_name == _DURATION:
        return msg.ToTimedelta()
    if full_name == _ANY:
        return _unpack_any(msg=msg, pool=pool)
    if full_name in _STRUCT_TYPES:
        result = json_format.MessageToDict(msg)
        # a set-but-null Value maps to NULL; None is reserved for "unset"
        if full_name == _VALUE and result is None:
            return NULL
        return result
    if full_name in _WRAPPER_TYPES:
        return msg.value
    model_cls = _resolve_model(full_name=full_name, pool=pool)
    if model_cls is None:
        raise LookupError(
            f"no protodantic model registered for {full_name!r}; "
            "import the generated module that defines it first"
        )
    return model_cls.from_proto(msg)


def _unpack_any(*, msg: Message, pool: Any) -> ProtoModel:
    type_name = msg.type_url.rpartition("/")[2]
    model_cls = _resolve_model(full_name=type_name, pool=pool)
    if model_cls is None:
        raise LookupError(
            f"cannot unpack Any: no generated model imported for {type_name!r}"
        )
    inner = model_cls._new_message()
    if not msg.Unpack(inner):
        raise ValueError(f"failed to unpack Any containing {type_name!r}")
    return model_cls.from_proto(inner)


def _set_proto_field(*, msg: Message, fd: FieldDescriptor, value: Any) -> None:
    if _is_map(fd=fd):
        _fill_map(target=getattr(msg, fd.name), fd=fd, value=value)
        return
    if fd.is_repeated:
        _fill_repeated(target=getattr(msg, fd.name), fd=fd, value=value)
        return
    if fd.type == FieldDescriptor.TYPE_MESSAGE:
        _fill_message(target=getattr(msg, fd.name), value=value)
        return
    setattr(msg, fd.name, _scalar_to_proto(value=value))


def _fill_map(*, target: Any, fd: FieldDescriptor, value: dict) -> None:
    value_fd = fd.message_type.fields_by_name["value"]
    if value_fd.type == FieldDescriptor.TYPE_MESSAGE:
        for key, item in value.items():
            _fill_message(target=target[key], value=item)
    else:
        for key, item in value.items():
            target[key] = _scalar_to_proto(value=item)


def _fill_repeated(*, target: Any, fd: FieldDescriptor, value: list) -> None:
    if fd.type == FieldDescriptor.TYPE_MESSAGE:
        for item in value:
            _fill_message(target=target.add(), value=item)
    else:
        target.extend(_scalar_to_proto(value=item) for item in value)


def _read_proto_field(*, msg: Message, fd: FieldDescriptor, pool: Any) -> Any:
    if _is_map(fd=fd):
        return _read_map(target=getattr(msg, fd.name), fd=fd, pool=pool)
    if fd.is_repeated:
        value = getattr(msg, fd.name)
        if fd.type == FieldDescriptor.TYPE_MESSAGE:
            return [_message_to_python(msg=item, pool=pool) for item in value]
        return list(value)
    if fd.has_presence and not msg.HasField(fd.name):
        return None
    if fd.type == FieldDescriptor.TYPE_MESSAGE:
        return _message_to_python(msg=getattr(msg, fd.name), pool=pool)
    return getattr(msg, fd.name)


def _read_map(*, target: Any, fd: FieldDescriptor, pool: Any) -> dict:
    value_fd = fd.message_type.fields_by_name["value"]
    if value_fd.type == FieldDescriptor.TYPE_MESSAGE:
        return {key: _message_to_python(msg=item, pool=pool) for key, item in target.items()}
    return dict(target)


class ProtoModel(BaseModel):
    """Pydantic base model bound to a protobuf message type."""

    model_config = ConfigDict(
        populate_by_name=True,
        protected_namespaces=(),
        validate_assignment=True,
        extra="forbid",
    )

    __proto_full_name__: ClassVar[str] = ""
    __proto_pool__: ClassVar[Any] = None
    __proto_oneofs__: ClassVar[dict[str, tuple[str, ...]]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # own-body declaration only: plain subclasses don't hijack resolution;
        # re-declaring __proto_full_name__ is the explicit opt-in to take over
        if cls.__dict__.get("__proto_full_name__"):
            _MODELS_BY_POOL[(cls.__proto_pool__, cls.__proto_full_name__)] = cls
            _MODELS_BY_NAME[cls.__proto_full_name__] = cls

    def __setattr__(self, name: str, value: Any) -> None:
        # pydantic mutates before after-validators run; restore on failure so
        # a rejected assignment cannot leave the model in an invalid state
        had_value = name in self.__dict__
        old_value = self.__dict__.get(name)
        was_set = name in self.__pydantic_fields_set__
        try:
            super().__setattr__(name, value)
        except Exception:
            if had_value:
                self.__dict__[name] = old_value
            else:
                self.__dict__.pop(name, None)
            if not was_set:
                self.__pydantic_fields_set__.discard(name)
            raise

    @model_validator(mode="after")
    def _validate_oneofs(self) -> Self:
        for group, fields in type(self).__proto_oneofs__.items():
            set_fields = [name for name in fields if getattr(self, name) is not None]
            if len(set_fields) > 1:
                raise ValueError(
                    f"oneof {group!r} allows at most one field to be set, got {set_fields}"
                )
        return self

    @classmethod
    def proto_class(cls) -> type[Message]:
        """The dynamic protobuf message class this model is bound to."""
        descriptor = cls.__proto_pool__.FindMessageTypeByName(cls.__proto_full_name__)
        return message_factory.GetMessageClass(descriptor)

    @classmethod
    def _new_message(cls) -> Message:
        message_cls = cls.proto_class()
        return message_cls()

    def to_proto(self, *, into: type[Message] | None = None) -> Message:
        """Convert this model to a protobuf message. ``into`` accepts a message
        class of the same proto full name (e.g. a classic _pb2 class) and
        returns an instance of it."""
        if into is not None:
            # type checks BEFORE touching DESCRIPTOR: hostile attributes must
            # not leak arbitrary exceptions past the promised TypeError
            if not (isinstance(into, type) and issubclass(into, Message)):
                raise TypeError(
                    f"to_proto(into=...) expects a protobuf message class, got {into!r}"
                )
            # abstract Message subclasses pass issubclass but carry DESCRIPTOR = None
            descriptor = getattr(into, "DESCRIPTOR", None)
            if not isinstance(descriptor, Descriptor):
                raise TypeError(
                    f"to_proto(into=...) expects a protobuf message class, got {into!r}"
                )
            target_name = descriptor.full_name
            if target_name != self.__proto_full_name__:
                raise TypeError(
                    f"{type(self).__name__}.to_proto(into=...) expects a class for "
                    f"{self.__proto_full_name__!r}, got {target_name!r}"
                )
            target = into()
            target.ParseFromString(self.to_proto_bytes())
            return target
        msg = self._new_message()
        for fd in msg.DESCRIPTOR.fields:
            value = getattr(self, python_field_name(proto_name=fd.name))
            if value is not None:
                _set_proto_field(msg=msg, fd=fd, value=value)
        return msg

    def to_proto_bytes(self) -> bytes:
        """Serialize this model to protobuf wire format."""
        return self.to_proto().SerializeToString()

    def to_proto_json(self, **kwargs: Any) -> str:
        """Serialize this model to canonical proto JSON."""
        return json_format.MessageToJson(self.to_proto(), **kwargs)

    @classmethod
    def from_proto(cls, msg: Message) -> Self:
        """Build a model from a protobuf message of this model's proto type.
        Works with any message whose descriptor matches this schema, including
        classic _pb2 instances; unrelated message types are rejected."""
        if msg.DESCRIPTOR.full_name != cls.__proto_full_name__:
            raise TypeError(
                f"{cls.__name__}.from_proto expects {cls.__proto_full_name__!r}, "
                f"got {msg.DESCRIPTOR.full_name!r}"
            )
        data = {
            python_field_name(proto_name=fd.name): _read_proto_field(
                msg=msg, fd=fd, pool=cls.__proto_pool__
            )
            for fd in msg.DESCRIPTOR.fields
        }
        return cls(**data)

    @classmethod
    def from_proto_bytes(cls, data: bytes) -> Self:
        """Parse protobuf wire format into a model instance."""
        return cls.from_proto(cls.proto_class().FromString(data))

    @classmethod
    def from_proto_json(cls, data: str, **kwargs: Any) -> Self:
        """Parse canonical proto JSON into a model instance. kwargs pass
        through to json_format.Parse (e.g. ignore_unknown_fields=True)."""
        return cls.from_proto(json_format.Parse(data, cls._new_message(), **kwargs))
