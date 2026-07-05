from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

from google.protobuf import descriptor_pb2
from google.protobuf.descriptor import FileDescriptor


def fdset_from_package(package: str) -> bytes:
    """Serialized FileDescriptorSet reflected from an installed _pb2 package.

    Imports only ``*_pb2`` modules under the package (the protoc naming
    contract — helper modules and ``*_pb2_grpc`` stubs are never imported),
    collects their file descriptors plus transitive dependencies, and emits
    them in canonical order: dependencies before dependents, lexicographic
    within each ready wave — deterministic across processes by construction.
    """
    root = importlib.import_module(package)
    files: dict[str, FileDescriptor] = {}
    _collect_module_descriptor(module=root, into=files)
    for module_name in _iter_pb2_module_names(package=root):
        _collect_module_descriptor(module=importlib.import_module(module_name), into=files)
    if not files:
        raise ValueError(f"no protobuf modules (*_pb2) found under {package!r}")

    fdset = descriptor_pb2.FileDescriptorSet()
    for file_descriptor in _canonical_order(files=files):
        file_descriptor.CopyToProto(fdset.file.add())
    return fdset.SerializeToString()


def _iter_pb2_module_names(*, package: ModuleType) -> list[str]:
    """Discover *_pb2 module names by filesystem traversal, importing nothing.

    pkgutil-style walking is unusable here: it cannot see nested PEP 420
    namespace directories, and it imports every subpackage it recurses
    through. Path traversal descends namespace dirs for free, and the only
    imports that ever happen are the matched _pb2 modules themselves (whose
    ancestor packages python imports precisely because they are required —
    an unrelated trapped subpackage is never touched)."""
    names: set[str] = set()
    for root in getattr(package, "__path__", []):
        root_path = Path(root)
        for pb2_file in root_path.rglob("*_pb2.py"):
            relative = pb2_file.relative_to(root_path).with_suffix("")
            names.add(package.__name__ + "." + ".".join(relative.parts))
    return sorted(names)


def _collect_module_descriptor(*, module: ModuleType, into: dict[str, FileDescriptor]) -> None:
    descriptor = getattr(module, "DESCRIPTOR", None)
    if isinstance(descriptor, FileDescriptor):
        _add_with_dependencies(fd=descriptor, into=into)


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
