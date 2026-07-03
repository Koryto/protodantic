"""protodantic: bidirectional bridge between Protocol Buffers and Pydantic."""

from protodantic._version import __version__
from protodantic.codegen import generate_source
from protodantic.compiler import compile_fdset
from protodantic.runtime import NULL, OpenEnum, ProtoModel, load_pool, model_for
from protodantic.types import Int32, Int64, UInt32, UInt64

__all__ = [
    "Int32",
    "Int64",
    "NULL",
    "OpenEnum",
    "ProtoModel",
    "UInt32",
    "UInt64",
    "__version__",
    "compile_fdset",
    "generate_source",
    "load_pool",
    "model_for",
]
