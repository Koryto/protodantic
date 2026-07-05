"""Shared fixture: compile protos from tests/protos, generate models, import them.

Every test file states use cases in its docstrings; the suite is the living
specification for protodantic (red tests = accepted-but-unimplemented use cases).
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from protodantic import compile_fdset, generate_source

PROTO_DIR = Path(__file__).parent / "protos"


class _Generator:
    """Callable behind the `generate` fixture: ``generate("a.proto", "b.proto")``
    returns the imported generated module, cached per proto-file combination."""

    def __init__(self, *, tmp_path_factory: pytest.TempPathFactory) -> None:
        self._tmp_path_factory = tmp_path_factory
        self._cache: dict[tuple[str, ...], ModuleType] = {}
        self._counter = 0

    def __call__(self, *proto_names: str) -> ModuleType:
        key = tuple(proto_names)
        if key in self._cache:
            return self._cache[key]
        paths = [str(PROTO_DIR / name) for name in proto_names]
        fdset = compile_fdset(paths, [str(PROTO_DIR)])
        source = generate_source(fdset)
        self._counter += 1
        module_name = f"protodantic_gen_{self._counter}"
        module_path = self._tmp_path_factory.mktemp("generated") / f"{module_name}.py"
        module_path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._cache[key] = module
        return module


@pytest.fixture(scope="session")
def generate(tmp_path_factory):
    return _Generator(tmp_path_factory=tmp_path_factory)
