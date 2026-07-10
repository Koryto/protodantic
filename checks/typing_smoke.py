from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from mypy import api as mypy_api

from protodantic import compile_fdset, generate_source

_ROOT = Path(__file__).resolve().parents[1]
_PROTO = _ROOT / "tests" / "protos" / "demo.proto"
_CONSUMER = """\
from typing import assert_type

from google.protobuf.message import Message

from models import Address, User

address = Address(city="Warsaw")
user = User(id=7, address=address)

assert_type(user.id, int)
assert_type(user.address, Address | None)
assert_type(User.from_proto_bytes(user.to_proto_bytes()), User)
assert_type(user.to_proto(), Message)
assert_type(user.to_proto_bytes(), bytes)
"""


def _run_smoke(*, workspace: Path) -> int:
    models = workspace / "models.py"
    models.write_text(generate_source(compile_fdset([str(_PROTO)])), encoding="utf-8")
    consumer = workspace / "consumer.py"
    consumer.write_text(_CONSUMER, encoding="utf-8")

    stdout, stderr, status = mypy_api.run(
        [
            "--strict",
            "--no-incremental",
            "--follow-imports=silent",
            "--show-error-codes",
            "--python-executable",
            sys.executable,
            str(consumer),
        ]
    )
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return status


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        return _run_smoke(workspace=Path(tmp_dir))


if __name__ == "__main__":
    raise SystemExit(main())
