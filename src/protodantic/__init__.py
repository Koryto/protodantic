"""Bidirectional bridge between Protocol Buffers and Pydantic."""

from ._version import __version__
from .codegen import generate_source, generate_tree
from .compiler import compile_fdset
from .runtime import OpenEnum, ProtoModel, load_pool, model_for
from .types import NULL, Int32, Int64, ListValue, Struct, UInt32, UInt64, Value

__all__ = [
    "Int32",
    "Int64",
    "ListValue",
    "NULL",
    "OpenEnum",
    "ProtoModel",
    "Struct",
    "UInt32",
    "UInt64",
    "Value",
    "__version__",
    "compile_fdset",
    "generate_source",
    "generate_tree",
    "load_pool",
    "model_for",
]
