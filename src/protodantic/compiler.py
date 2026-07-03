from __future__ import annotations

import importlib.resources
import os
import tempfile
from collections.abc import Iterable

from grpc_tools import protoc as _protoc


def compile_fdset(protos: Iterable[str], includes: Iterable[str] = ()) -> bytes:
    """Compile .proto files into a serialized FileDescriptorSet, imports
    included, so the result is self-contained. The directory of each input and
    the well-known types shipped with grpcio-tools are always on the path."""
    proto_paths = [os.path.abspath(p) for p in protos]
    if not proto_paths:
        raise ValueError("at least one .proto file is required")

    include_paths = [os.path.abspath(i) for i in includes]
    for path in proto_paths:
        parent = os.path.dirname(path)
        if parent not in include_paths:
            include_paths.append(parent)
    include_paths.append(str(importlib.resources.files("grpc_tools") / "_proto"))

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_path = os.path.join(tmp_dir, "fdset.bin")
        args = [
            "protoc",
            f"--descriptor_set_out={out_path}",
            "--include_imports",
            *[f"-I{i}" for i in include_paths],
            *proto_paths,
        ]
        exit_code = _protoc.main(args)
        if exit_code != 0:
            raise RuntimeError(f"protoc failed with exit code {exit_code} (args: {args[1:]})")
        with open(out_path, "rb") as f:
            return f.read()
