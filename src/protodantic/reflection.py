from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

from google.protobuf import descriptor_pb2
from google.protobuf.descriptor import FileDescriptor

# the protoc naming contract; also used by the CLI's input-redirect preflight
_PB2_MODULE_GLOB = "*_pb2.py"


def fdset_from_package(package: str) -> bytes:
    """Serialized FileDescriptorSet reflected from an installed _pb2 package.

    Imports only ``*_pb2`` modules; file order is canonical (dependencies
    first) and deterministic.
    """
    root = importlib.import_module(package)
    files: dict[str, FileDescriptor] = {}
    # the root package itself may be a _pb2 module; its __init__ usually isn't
    root_descriptor = getattr(root, "DESCRIPTOR", None)
    if isinstance(root_descriptor, FileDescriptor):
        _add_with_dependencies(fd=root_descriptor, into=files)
    for module_name in _iter_pb2_module_names(package=root):
        _add_with_dependencies(fd=_required_descriptor(module_name=module_name), into=files)
    if not files:
        raise ValueError(f"no protobuf modules (*_pb2) found under {package!r}")

    fdset = descriptor_pb2.FileDescriptorSet()
    for file_descriptor in _canonical_order(files=files):
        file_descriptor.CopyToProto(fdset.file.add())
    return fdset.SerializeToString()


def _iter_pb2_module_names(*, package: ModuleType) -> list[str]:
    # filesystem traversal instead of pkgutil: descends nested PEP 420
    # namespace dirs and imports nothing — only matched _pb2 modules are ever
    # imported (their ancestor packages implicitly, because they are required)
    names: set[str] = set()
    for root in getattr(package, "__path__", []):
        root_path = Path(root)
        for pb2_file in root_path.rglob(_PB2_MODULE_GLOB):
            relative = pb2_file.relative_to(root_path).with_suffix("")
            names.add(package.__name__ + "." + ".".join(relative.parts))
    return sorted(names)


def _required_descriptor(*, module_name: str) -> FileDescriptor:
    descriptor = getattr(importlib.import_module(module_name), "DESCRIPTOR", None)
    if not isinstance(descriptor, FileDescriptor):
        raise ValueError(
            f"{module_name} matches the *_pb2 naming contract but exposes no "
            "protobuf FileDescriptor"
        )
    return descriptor


def _add_with_dependencies(*, fd: FileDescriptor, into: dict[str, FileDescriptor]) -> None:
    if fd.name in into:
        return
    into[fd.name] = fd
    for dependency in fd.dependencies:
        _add_with_dependencies(fd=dependency, into=into)


def _canonical_order(*, files: dict[str, FileDescriptor]) -> list[FileDescriptor]:
    remaining = dict(files)
    done: set[str] = set()
    ordered: list[FileDescriptor] = []
    while remaining:
        ready = sorted(
            name
            for name, fd in remaining.items()
            if all(dep.name in done for dep in fd.dependencies)
        )
        if not ready:  # proto forbids import cycles; defensive guard only
            raise RuntimeError(f"dependency cycle among proto files: {sorted(remaining)}")
        for name in ready:
            ordered.append(remaining.pop(name))
            done.add(name)
    return ordered
