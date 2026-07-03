from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, PlainSerializer

Int32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
UInt32 = Annotated[int, Field(ge=0, le=2**32 - 1)]
Int64 = Annotated[int, Field(ge=-(2**63), le=2**63 - 1)]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]


class _NullType:
    """Singleton sentinel: an explicit JSON null in a google.protobuf.Value
    field, as opposed to None which means "field unset"."""

    _instance: _NullType | None = None

    def __new__(cls) -> _NullType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "protodantic.NULL"

    def __bool__(self) -> bool:
        return False

    # copies must preserve identity: `x is NULL` is the documented check
    def __copy__(self) -> _NullType:
        return self

    def __deepcopy__(self, memo: dict) -> _NullType:
        return self


NULL = _NullType()


def _strip_null_sentinel(value: Any) -> Any:
    if value is NULL:
        return None
    if isinstance(value, dict):
        return {k: _strip_null_sentinel(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_null_sentinel(v) for v in value]
    return value


# JSON dumps turn the sentinel into a real null; python-mode dumps keep it so
# NULL-vs-unset survives a model_dump()/model_validate() round-trip
_NullSafe = PlainSerializer(_strip_null_sentinel, when_used="json")

Struct = Annotated[dict[str, Any], _NullSafe]
Value = Annotated[Any, _NullSafe]
ListValue = Annotated[list[Any], _NullSafe]
