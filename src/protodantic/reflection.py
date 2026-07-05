from __future__ import annotations

import importlib
import pkgutil
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
    if not hasattr(package, "__path__"):
        return []
    names: list[str] = []
    for info in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + "."):
        if info.name.rpartition(".")[2].endswith("_pb2"):
            names.append(info.name)
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
