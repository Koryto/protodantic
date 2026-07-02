"""Runtime support for generated protodantic models.

`ProtoModel` is the base class for all generated models. It converts between
pydantic instances and protobuf messages built dynamically from the descriptor
pool that each generated module embeds, so both directions (pydantic -> proto
and proto -> pydantic) use the real protobuf wire format.
"""

from __future__ import annotations

import datetime
import enum
import keyword
from typing import Any, ClassVar, Self

from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message
from pydantic import BaseModel, ConfigDict, model_validator

_TIMESTAMP = "google.protobuf.Timestamp"
_DURATION = "google.protobuf.Duration"
_ANY = "google.protobuf.Any"
_STRUCT_TYPES = frozenset({
    "google.protobuf.Struct",
    "google.protobuf.Value",
    "google.protobuf.ListValue",
})
_WRAPPER_TYPES = frozenset({
    "google.protobuf.DoubleValue",
    "google.protobuf.FloatValue",
    "google.protobuf.Int64Value",
    "google.protobuf.UInt64Value",
    "google.protobuf.Int32Value",
    "google.protobuf.UInt32Value",
    "google.protobuf.BoolValue",
    "google.protobuf.StringValue",
    "google.protobuf.BytesValue",
})

# proto full name -> generated model class, populated as generated modules import
_MODEL_REGISTRY: dict[str, type[ProtoModel]] = {}

# names a proto field cannot use as a python attribute
_RESERVED_NAMES = frozenset({
    "proto_class",
    "to_proto",
    "to_proto_bytes",
    "to_proto_json",
    "from_proto",
    "from_proto_bytes",
    "from_proto_json",
})


def python_field_name(proto_name: str) -> str:
    """Python attribute name for a proto field: keywords and reserved names get
    a trailing underscore; the proto name stays available as a pydantic alias."""
    if (
        keyword.iskeyword(proto_name)
        or proto_name.startswith("model_")
        or proto_name in _RESERVED_NAMES
    ):
        return proto_name + "_"
    return proto_name


def model_for(full_name: str) -> type[ProtoModel]:
    """Look up the generated model class for a proto full name (e.g. "pkg.Msg").

    The module defining the model must have been imported first.
    """
    try:
        return _MODEL_REGISTRY[full_name]
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
    """IntEnum matching proto3 open-enum semantics: values not declared in the
    schema are preserved as pseudo-members instead of raising."""

    @classmethod
    def _missing_(cls, value: object) -> OpenEnum | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        pseudo = int.__new__(cls, value)
        pseudo._name_ = None
        pseudo._value_ = value
        return pseudo


def _is_map(fd: FieldDescriptor) -> bool:
    return (
        fd.type == FieldDescriptor.TYPE_MESSAGE
        and fd.message_type.GetOptions().map_entry
    )


def _scalar_to_proto(value: Any) -> Any:
    # IntEnum members (incl. open-enum pseudo-members) flatten to plain ints
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _fill_message(target: Message, value: Any) -> None:
    """Copy a python value into an already-attached proto message field."""
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
        json_format.ParseDict(value, target)
    elif full_name in _WRAPPER_TYPES:
        target.value = value
    else:
        target.CopyFrom(value.to_proto())


def _message_to_python(msg: Message) -> Any:
    full_name = msg.DESCRIPTOR.full_name
    if full_name == _TIMESTAMP:
        return msg.ToDatetime(tzinfo=datetime.timezone.utc)
    if full_name == _DURATION:
        return msg.ToTimedelta()
    if full_name == _ANY:
        type_name = msg.type_url.rpartition("/")[2]
        model_cls = _MODEL_REGISTRY.get(type_name)
        if model_cls is None:
            raise LookupError(
                f"cannot unpack Any: no generated model imported for {type_name!r}"
            )
        inner = model_cls.proto_class()()
        if not msg.Unpack(inner):
            raise ValueError(f"failed to unpack Any containing {type_name!r}")
        return model_cls.from_proto(inner)
    if full_name in _STRUCT_TYPES:
        return json_format.MessageToDict(msg)
    if full_name in _WRAPPER_TYPES:
        return msg.value
    model_cls = _MODEL_REGISTRY.get(full_name)
    if model_cls is None:
        raise LookupError(
            f"no protodantic model registered for {full_name!r}; "
            "import the generated module that defines it first"
        )
    return model_cls.from_proto(msg)


class ProtoModel(BaseModel):
    """Pydantic base model bound to a protobuf message type."""

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    __proto_full_name__: ClassVar[str] = ""
    __proto_pool__: ClassVar[Any] = None
    # real (non-synthetic) oneof groups: name -> python field names
    __proto_oneofs__: ClassVar[dict[str, tuple[str, ...]]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if cls.__proto_full_name__:
            _MODEL_REGISTRY[cls.__proto_full_name__] = cls

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

    # -- pydantic -> proto -------------------------------------------------

    def to_proto(self) -> Message:
        """Convert this model to a protobuf message."""
        msg = self.proto_class()()
        for fd in msg.DESCRIPTOR.fields:
            value = getattr(self, python_field_name(fd.name))
            if value is None:
                continue
            if fd.is_repeated:
                target = getattr(msg, fd.name)
                if _is_map(fd):
                    value_fd = fd.message_type.fields_by_name["value"]
                    if value_fd.type == FieldDescriptor.TYPE_MESSAGE:
                        for key, item in value.items():
                            _fill_message(target[key], item)
                    else:
                        for key, item in value.items():
                            target[key] = _scalar_to_proto(item)
                elif fd.type == FieldDescriptor.TYPE_MESSAGE:
                    for item in value:
                        _fill_message(target.add(), item)
                else:
                    target.extend(_scalar_to_proto(item) for item in value)
            elif fd.type == FieldDescriptor.TYPE_MESSAGE:
                _fill_message(getattr(msg, fd.name), value)
            else:
                setattr(msg, fd.name, _scalar_to_proto(value))
        return msg

    def to_proto_bytes(self) -> bytes:
        """Serialize this model to protobuf wire format."""
        return self.to_proto().SerializeToString()

    def to_proto_json(self, **kwargs: Any) -> str:
        """Serialize this model to canonical proto JSON."""
        return json_format.MessageToJson(self.to_proto(), **kwargs)

    # -- proto -> pydantic -------------------------------------------------

    @classmethod
    def from_proto(cls, msg: Message) -> Self:
        """Build a model instance from a protobuf message."""
        data: dict[str, Any] = {}
        for fd in msg.DESCRIPTOR.fields:
            name = python_field_name(fd.name)
            if fd.is_repeated:
                value = getattr(msg, fd.name)
                if _is_map(fd):
                    value_fd = fd.message_type.fields_by_name["value"]
                    if value_fd.type == FieldDescriptor.TYPE_MESSAGE:
                        data[name] = {k: _message_to_python(v) for k, v in value.items()}
                    else:
                        data[name] = dict(value)
                elif fd.type == FieldDescriptor.TYPE_MESSAGE:
                    data[name] = [_message_to_python(item) for item in value]
                else:
                    data[name] = list(value)
            elif fd.has_presence and not msg.HasField(fd.name):
                data[name] = None
            elif fd.type == FieldDescriptor.TYPE_MESSAGE:
                data[name] = _message_to_python(getattr(msg, fd.name))
            else:
                data[name] = getattr(msg, fd.name)
        return cls(**data)

    @classmethod
    def from_proto_bytes(cls, data: bytes) -> Self:
        """Parse protobuf wire format into a model instance."""
        return cls.from_proto(cls.proto_class().FromString(data))

    @classmethod
    def from_proto_json(cls, data: str) -> Self:
        """Parse canonical proto JSON into a model instance."""
        return cls.from_proto(json_format.Parse(data, cls.proto_class()()))
