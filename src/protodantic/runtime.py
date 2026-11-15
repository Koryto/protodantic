from __future__ import annotations

import datetime
import enum
import keyword
from collections.abc import Callable
from functools import partial
from typing import Any, ClassVar, Self, get_args

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
_WKT_TYPES = frozenset({_TIMESTAMP, _DURATION, _ANY}) | _STRUCT_TYPES | _WRAPPER_TYPES

# nested-message resolution is scoped per descriptor pool (i.e. per generated
# module) so duplicate generated modules can coexist in one process; the
# by-name registry backs model_for() and Any fallback (last import wins)
_MODELS_BY_POOL: dict[tuple[Any, str], type[ProtoModel]] = {}
_MODELS_BY_NAME: dict[str, type[ProtoModel]] = {}

# conversion plans bake in registry lookups, so every registration clears them
_PLANS: dict[type[ProtoModel], _MessagePlan] = {}

# how a field's value sits in its message; "plain" is a scalar or an enum
(
    _PLAIN,
    _PLAIN_OPTIONAL,
    _PLAIN_REPEATED,
    _PLAIN_MAP,
    _MESSAGE,
    _MESSAGE_REPEATED,
    _MESSAGE_MAP,
) = range(7)

_RESERVED_NAMES = frozenset(
    {
        "proto_class",
        "to_proto",
        "to_proto_bytes",
        "to_proto_json",
        "from_proto",
        "from_proto_bytes",
        "from_proto_json",
    }
)


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
        raise KeyError(f"no generated model imported for proto type {full_name!r}") from None


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
        # nameless by design; the enum machinery types _name_ as str
        pseudo._name_ = None  # type: ignore[assignment]
        pseudo._value_ = value
        return pseudo


def _message_type(*, fd: FieldDescriptor) -> Descriptor:
    message_type = fd.message_type
    assert message_type is not None, f"{fd.full_name} is not a message field"
    return message_type


def _is_map(*, fd: FieldDescriptor) -> bool:
    if fd.type != FieldDescriptor.TYPE_MESSAGE:
        return False
    return _message_type(fd=fd).GetOptions().map_entry


def _resolve_model(*, full_name: str, pool: Any) -> type[ProtoModel] | None:
    model_cls = _MODELS_BY_POOL.get((pool, full_name))
    if model_cls is not None:
        return model_cls
    return _MODELS_BY_NAME.get(full_name)


def _models_in(*, annotation: Any) -> set[type[ProtoModel]]:
    args = get_args(annotation)
    if args:
        return set().union(*(_models_in(annotation=arg) for arg in args))
    if isinstance(annotation, type) and issubclass(annotation, ProtoModel):
        return {annotation}
    return set()


def _fill_wkt(*, target: Any, value: Any) -> None:
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
    else:
        target.value = value


def _wkt_to_python(*, msg: Any, pool: Any) -> Any:
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
    return msg.value


def _unpack_any(*, msg: Any, pool: Any) -> ProtoModel:
    type_name = msg.type_url.rpartition("/")[2]
    model_cls = _resolve_model(full_name=type_name, pool=pool)
    if model_cls is None:
        raise LookupError(f"cannot unpack Any: no generated model imported for {type_name!r}")
    inner = model_cls.proto_class()()
    if not msg.Unpack(inner):
        raise ValueError(f"failed to unpack Any containing {type_name!r}")
    return model_cls.from_proto(inner)


def _plan_for(*, model: type[ProtoModel]) -> _MessagePlan:
    plan = _PLANS.get(model)
    if plan is None:
        plan = _PLANS[model] = _MessagePlan(model=model)
    return plan


class _MessageField:
    """Message-typed field of a plan. Well-known types convert through the
    runtime. Model types resolve on first use: an unimported type only matters
    once data actually carries it."""

    def __init__(self, *, owner: type[ProtoModel], py_name: str, descriptor: Descriptor) -> None:
        self._owner = owner
        self._py_name = py_name
        self._descriptor = descriptor
        self._reader: Callable[..., Any] | None = None
        self.fill: Callable[..., None] = self._fill_model
        if descriptor.full_name in _WKT_TYPES:
            self._reader = partial(_wkt_to_python, pool=owner.__proto_pool__)
            self.fill = _fill_wkt

    def reader(self) -> Callable[..., Any]:
        if self._reader is None:
            self._reader = self._resolve_reader()
        return self._reader

    def _resolve_reader(self) -> Callable[..., Any]:
        full_name = self._descriptor.full_name
        model = _resolve_model(full_name=full_name, pool=self._owner.__proto_pool__)
        if model is None:
            raise LookupError(
                f"no protodantic model registered for {full_name!r}; "
                "import the generated module that defines it first"
            )
        plan = _plan_for(model=model)
        # a plain dict is handed to pydantic only when the annotation provably
        # builds this very class from it; takeover subclasses and models from
        # another pool are built here instead
        annotation = self._owner.model_fields[self._py_name].annotation
        if plan.descriptor is self._descriptor and _models_in(annotation=annotation) == {model}:
            return plan.read
        return plan.decode

    def _fill_model(self, *, target: Any, value: Any) -> None:
        plan = _PLANS.get(type(value))
        if plan is None and isinstance(value, ProtoModel):
            plan = _plan_for(model=type(value))
        # fields are written by name, which is only sound for the same type
        # in the same pool
        if plan is None or plan.descriptor is not self._descriptor:
            raise TypeError(
                f"{self._owner.__name__}.{self._py_name} expects a model of "
                f"{self._descriptor.full_name!r}, got "
                f"{type(value).__module__}.{type(value).__qualname__}"
            )
        plan.fill(msg=target, model=value)


class _MessagePlan:
    """What conversion needs to know about one model class, derived once: its
    message class and, per field, the python name, proto name, kind, and (for
    message-typed fields) the nested type."""

    def __init__(self, *, model: type[ProtoModel]) -> None:
        self.model = model
        self.descriptor: Descriptor = model.__proto_pool__.FindMessageTypeByName(
            model.__proto_full_name__
        )
        self.message_class = message_factory.GetMessageClass(self.descriptor)
        self._fields = tuple(self._plan_field(fd=fd) for fd in self.descriptor.fields)

    def _plan_field(self, *, fd: FieldDescriptor) -> tuple[str, str, int, Any]:
        py_name = python_field_name(proto_name=fd.name)
        element = fd
        if _is_map(fd=fd):
            kind = _MESSAGE_MAP
            element = _message_type(fd=fd).fields_by_name["value"]
        elif fd.is_repeated:
            kind = _MESSAGE_REPEATED
        else:
            kind = _MESSAGE
        if element.type != FieldDescriptor.TYPE_MESSAGE:
            if kind == _MESSAGE_MAP:
                return py_name, fd.name, _PLAIN_MAP, None
            if kind == _MESSAGE_REPEATED:
                return py_name, fd.name, _PLAIN_REPEATED, None
            return py_name, fd.name, _PLAIN_OPTIONAL if fd.has_presence else _PLAIN, None
        nested = _MessageField(
            owner=self.model, py_name=py_name, descriptor=_message_type(fd=element)
        )
        return py_name, fd.name, kind, nested

    def read(self, *, msg: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for py_name, name, kind, nested in self._fields:
            if kind == _PLAIN:
                data[py_name] = getattr(msg, name)
            elif kind == _PLAIN_OPTIONAL:
                data[py_name] = getattr(msg, name) if msg.HasField(name) else None
            elif kind == _PLAIN_REPEATED:
                data[py_name] = list(getattr(msg, name))
            elif kind == _PLAIN_MAP:
                data[py_name] = dict(getattr(msg, name))
            elif kind == _MESSAGE:
                data[py_name] = (
                    nested.reader()(msg=getattr(msg, name)) if msg.HasField(name) else None
                )
            elif kind == _MESSAGE_REPEATED:
                read = nested.reader()
                data[py_name] = [read(msg=item) for item in getattr(msg, name)]
            else:
                read = nested.reader()
                data[py_name] = {key: read(msg=item) for key, item in getattr(msg, name).items()}
        return data

    def decode(self, *, msg: Any) -> Any:
        # a message from another pool (a _pb2 class, another schema revision)
        # crosses over on the wire, so skew follows wire-compat semantics
        if msg.DESCRIPTOR is not self.descriptor:
            msg = self.message_class.FromString(msg.SerializeToString())
        return self.model.model_validate(self.read(msg=msg))

    def fill(self, *, msg: Any, model: ProtoModel) -> None:
        values = model.__dict__
        for py_name, name, kind, nested in self._fields:
            value = values[py_name]
            if value is None:
                continue
            if kind == _PLAIN or kind == _PLAIN_OPTIONAL:
                setattr(msg, name, value)
            elif kind == _PLAIN_REPEATED:
                getattr(msg, name).extend(value)
            elif kind == _PLAIN_MAP:
                getattr(msg, name).update(value)
            elif kind == _MESSAGE:
                target = getattr(msg, name)
                # a set submessage with nothing inside still carries presence
                target.SetInParent()
                nested.fill(target=target, value=value)
            elif kind == _MESSAGE_REPEATED:
                fill, add = nested.fill, getattr(msg, name).add
                for item in value:
                    fill(target=add(), value=item)
            else:
                fill, target = nested.fill, getattr(msg, name)
                for key, item in value.items():
                    fill(target=target[key], value=item)


class ProtoModel(BaseModel):
    """Pydantic base model bound to a protobuf message type."""

    model_config = ConfigDict(
        populate_by_name=True,
        protected_namespaces=(),
        validate_assignment=True,
        extra="forbid",
        # bytes are arbitrary binary, so JSON carries them as base64 both ways
        ser_json_bytes="base64",
        val_json_bytes="base64",
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
            # after the registry writes: plans built from here on resolve
            # against them
            _PLANS.clear()

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
        return _plan_for(model=cls).message_class

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
        plan = _plan_for(model=type(self))
        msg = plan.message_class()
        plan.fill(msg=msg, model=self)
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
        Works with any message of that type, including classic _pb2 instances
        and other revisions of the schema (which convert as their wire bytes
        would); unrelated message types are rejected."""
        if msg.DESCRIPTOR.full_name != cls.__proto_full_name__:
            raise TypeError(
                f"{cls.__name__}.from_proto expects {cls.__proto_full_name__!r}, "
                f"got {msg.DESCRIPTOR.full_name!r}"
            )
        return _plan_for(model=cls).decode(msg=msg)

    @classmethod
    def from_proto_bytes(cls, data: bytes) -> Self:
        """Parse protobuf wire format into a model instance."""
        return cls.from_proto(cls.proto_class().FromString(data))

    @classmethod
    def from_proto_json(cls, data: str, **kwargs: Any) -> Self:
        """Parse canonical proto JSON into a model instance. kwargs pass
        through to json_format.Parse (e.g. ignore_unknown_fields=True)."""
        return cls.from_proto(json_format.Parse(data, cls.proto_class()(), **kwargs))
