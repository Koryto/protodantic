"""USE CASE (negative): proto2 is out of scope. Feeding protodantic a proto2
file must fail fast with a clear error — never silently generate models with
wrong semantics (required fields, explicit defaults, groups...).
"""

import pytest


def test_proto2_is_rejected_with_clear_error(generate):
    """A proto2 schema raises the documented unsupported-syntax error."""
    with pytest.raises(NotImplementedError, match="proto2"):
        generate("legacy.proto")
