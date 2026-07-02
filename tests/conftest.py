"""Shared fixture: compile protos from tests/protos, generate models, import them.

Every test file states use cases in its docstrings; the suite is the living
specification for protodantic (red tests = accepted-but-unimplemented use cases).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from protodantic import compile_fdset, generate_source

PROTO_DIR = Path(__file__).parent / "protos"


@pytest.fixture(scope="session")
def generate(tmp_path_factory):
    """Factory fixture: ``generate("a.proto", "b.proto")`` returns the imported
    generated module. Results are cached per proto-file combination."""
    cache: dict[tuple[str, ...], object] = {}
    counter = 0

    def _generate(*proto_names: str):
        nonlocal counter
        key = tuple(proto_names)
        if key in cache:
            return cache[key]
        paths = [str(PROTO_DIR / name) for name in proto_names]
        fdset = compile_fdset(paths, [str(PROTO_DIR)])
        source = generate_source(fdset)
        counter += 1
        module_name = f"protodantic_gen_{counter}"
        module_path = tmp_path_factory.mktemp("generated") / f"{module_name}.py"
        module_path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        cache[key] = module
        return module

    return _generate
