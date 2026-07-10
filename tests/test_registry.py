"""USE CASE: model registry semantics — when several generated modules define
the same proto type (e.g. two versions of a central proto package), the most
recently imported module wins. Documented, deliberate behavior.
"""

import importlib.util
import sys

from protodantic import compile_fdset, generate_source, model_for


def _import_source(source: str, name: str, tmp_path):
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_registry_last_import_wins(tmp_path):
    """Importing a duplicate proto type replaces the global registry target."""
    proto = tmp_path / "regsem.proto"
    proto.write_text('syntax = "proto3";\npackage test.regsem;\nmessage Token { string v = 1; }\n')
    source = generate_source(compile_fdset([str(proto)]))

    first = _import_source(source, "regsem_first", tmp_path)
    assert model_for("test.regsem.Token") is first.Token

    second = _import_source(source, "regsem_second", tmp_path)
    assert model_for("test.regsem.Token") is second.Token
    assert model_for("test.regsem.Token") is not first.Token


def test_reimport_does_not_break_earlier_module(tmp_path):
    """Two generated modules with the same proto types coexist: each module's
    models resolve nested messages to classes from their own module, so
    importing a duplicate never breaks previously imported models."""
    proto = tmp_path / "nestreg.proto"
    proto.write_text(
        'syntax = "proto3";\npackage test.nestreg;\n'
        "message Child { string v = 1; }\nmessage Parent { Child child = 1; }\n"
    )
    source = generate_source(compile_fdset([str(proto)]))

    first = _import_source(source, "nestreg_first", tmp_path)
    data = first.Parent(child=first.Child(v="x")).to_proto_bytes()
    second = _import_source(source, "nestreg_second", tmp_path)

    restored = first.Parent.from_proto_bytes(data)
    assert isinstance(restored.child, first.Child)
    assert isinstance(second.Parent.from_proto_bytes(data).child, second.Child)


def test_plain_subclass_does_not_hijack_registry(tmp_path):
    """Subclassing a generated model (e.g. to add validators) must NOT change
    what from_proto/model_for resolve — registration is not inherited."""
    proto = tmp_path / "subreg.proto"
    proto.write_text('syntax = "proto3";\npackage test.subreg;\nmessage Badge { string v = 1; }\n')
    source = generate_source(compile_fdset([str(proto)]))
    mod = _import_source(source, "subreg_gen", tmp_path)

    class Extended(mod.Badge):
        pass

    assert model_for("test.subreg.Badge") is mod.Badge


def test_subclass_registers_by_explicit_redeclaration(tmp_path):
    """Opt-in: a subclass that re-declares __proto_full_name__ takes over
    resolution for that proto type (deliberate, visible in the class body)."""
    proto = tmp_path / "optin.proto"
    proto.write_text('syntax = "proto3";\npackage test.optin;\nmessage Seal { string v = 1; }\n')
    source = generate_source(compile_fdset([str(proto)]))
    mod = _import_source(source, "optin_gen", tmp_path)

    class SealPlus(mod.Seal):
        __proto_full_name__ = "test.optin.Seal"

    assert model_for("test.optin.Seal") is SealPlus
