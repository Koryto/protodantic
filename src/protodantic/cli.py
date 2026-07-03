from __future__ import annotations

from pathlib import Path

import click

from ._version import __version__
from .codegen import generate_source
from .compiler import compile_fdset


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="protodantic")
def main() -> None:
    """Bidirectional bridge between Protocol Buffers and pydantic models."""


@main.command()
@click.argument("protos", nargs=-1, required=True)
@click.option(
    "-I", "--include", "includes", multiple=True, metavar="DIR",
    help="Additional import search path (repeatable).",
)
@click.option(
    "-o", "--out", "out", required=True, metavar="FILE",
    help="Output python module path (e.g. models.py).",
)
def generate(protos: tuple[str, ...], includes: tuple[str, ...], out: str) -> None:
    """Generate pydantic models from .proto files."""
    try:
        fdset = compile_fdset(protos=protos, includes=includes)
        source = generate_source(fdset_bytes=fdset)
    except (RuntimeError, NotImplementedError) as exc:
        raise click.ClickException(str(exc)) from exc

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(source, encoding="utf-8")
    click.echo(f"wrote {out_path}")


if __name__ == "__main__":
    main()
