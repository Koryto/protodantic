"""Command-line interface: protodantic <files.proto> -o models.py"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from protodantic.codegen import generate_source
from protodantic.compiler import compile_fdset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="protodantic",
        description="Generate pydantic models with proto round-trip support from .proto files.",
    )
    parser.add_argument("protos", nargs="+", help=".proto files to compile")
    parser.add_argument(
        "-I", "--include", action="append", default=[], metavar="DIR",
        help="additional import search path (repeatable)",
    )
    parser.add_argument(
        "-o", "--out", required=True, metavar="FILE",
        help="output python module path (e.g. models.py)",
    )
    args = parser.parse_args(argv)

    try:
        fdset = compile_fdset(args.protos, args.include)
        source = generate_source(fdset)
    except (RuntimeError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(source, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
