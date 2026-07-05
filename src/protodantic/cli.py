from __future__ import annotations

import keyword
import shutil
import tempfile
import uuid
from pathlib import Path

import click

from ._version import __version__
from .codegen import GENERATED_MARKER, generate_source, generate_tree
from .compiler import compile_fdset
from .reflection import PB2_MODULE_GLOB, fdset_from_package


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="protodantic")
def main() -> None:
    """Bidirectional bridge between Protocol Buffers and pydantic models."""


@main.command()
@click.argument("protos", nargs=-1)
@click.option(
    "-I", "--include", "includes", multiple=True, metavar="DIR",
    help="Additional import search path (repeatable, .proto inputs only).",
)
@click.option(
    "--from-package", "from_package", metavar="PACKAGE", default=None,
    help="Generate from an installed protobuf (_pb2) package by import name.",
)
@click.option(
    "-o", "--out", "out", required=True, metavar="PATH",
    help="Output: a .py module file, or a package directory for tree layout.",
)
@click.option(
    "--layout", type=click.Choice(["module", "tree"]), default=None,
    help="Override the input-shape default (files -> module, directory/package -> tree).",
)
def generate(
    protos: tuple[str, ...],
    includes: tuple[str, ...],
    from_package: str | None,
    out: str,
    layout: str | None,
) -> None:
    """Generate pydantic models from .proto files, a directory of them, or an
    installed _pb2 package."""
    if protos and from_package:
        raise click.ClickException(
            "positional .proto inputs and --from-package cannot be used together"
        )
    if not protos and not from_package:
        raise click.ClickException(
            "nothing to generate: pass .proto files/directories, or --from-package "
            "for an installed protobuf package"
        )
    if from_package and includes:
        raise click.ClickException(
            "-I/--include applies to .proto compilation and cannot be combined "
            "with --from-package (an installed package needs no import paths)"
        )
    if layout:
        resolved_layout = layout
    elif from_package or any(Path(p).is_dir() for p in protos):
        resolved_layout = "tree"
    else:
        resolved_layout = "module"
    out_is_module = out.endswith(".py")
    if resolved_layout == "tree" and out_is_module:
        raise click.ClickException(
            "layout 'tree' writes a package directory, but -o ends with .py; "
            "pass --layout module for a single module or point -o at a directory"
        )
    if resolved_layout == "module" and not out_is_module:
        raise click.ClickException(
            "layout 'module' writes a single .py file, but -o does not end with .py; "
            "pass --layout tree for a package tree or point -o at a .py file"
        )
    if resolved_layout == "tree":
        out_name = Path(out).name
        if not out_name.isidentifier() or keyword.iskeyword(out_name):
            raise click.ClickException(
                f"tree output directory {out_name!r} is not a valid python package name; "
                "the tree root is imported as a package — use letters, digits, and underscores"
            )

    try:
        if from_package:
            fdset = fdset_from_package(from_package)
        else:
            _reject_non_proto_inputs(protos=protos)
            fdset = compile_fdset(protos=protos, includes=includes)
        if resolved_layout == "module":
            source = generate_source(fdset_bytes=fdset)
            out_path = Path(out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(source, encoding="utf-8")
            click.echo(f"wrote {out_path}")
        else:
            files = generate_tree(fdset_bytes=fdset)
            _write_tree(out_dir=Path(out), files=files)
            click.echo(f"wrote {len(files)} files under {out}")
    except (RuntimeError, NotImplementedError, ValueError, OSError, ImportError) as exc:
        raise click.ClickException(str(exc)) from exc


def _reject_non_proto_inputs(*, protos: tuple[str, ...]) -> None:
    """Redirect compiled-protobuf inputs to --from-package. Runs inside the
    command's exception boundary so filesystem errors surface cleanly."""
    for entry in protos:
        if entry.endswith(".py"):
            raise click.ClickException(
                f"{entry} is a python module, not a .proto file; for compiled "
                "protobuf packages use --from-package with the package's import name"
            )
        entry_path = Path(entry)
        if (
            entry_path.is_dir()
            and not any(entry_path.rglob("*.proto"))
            and any(entry_path.rglob(PB2_MODULE_GLOB))
        ):
            raise click.ClickException(
                f"{entry} contains compiled _pb2 modules but no .proto sources; "
                "use --from-package with the package's import name"
            )


def _write_tree(*, out_dir: Path, files: dict[str, str]) -> None:
    """Managed-clean, failure-atomic write: refuse to touch anything foreign,
    build the replacement in a staging dir, then swap — a failed write can
    never destroy the previous valid tree."""
    if out_dir.exists() and not out_dir.is_dir():
        raise click.ClickException(
            f"output path {out_dir} exists and is not a directory; refusing to touch it"
        )
    if out_dir.exists():
        foreign = _foreign_files(out_dir=out_dir)
        if foreign:
            raise click.ClickException(
                f"output directory {out_dir} contains files not generated by protodantic: "
                f"{', '.join(foreign)}; refusing to modify"
            )

    # unique staging/backup paths: we only ever delete directories we created
    # in this run — a pre-existing sibling is foreign data, and concurrent
    # generators can't race over shared names
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f"{out_dir.name}.", suffix=".protodantic-staging", dir=out_dir.parent)
    )
    try:
        for rel_path, source in sorted(files.items()):
            target = staging / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if out_dir.exists():
        backup = out_dir.parent / f"{out_dir.name}.{uuid.uuid4().hex}.protodantic-backup"
        out_dir.rename(backup)
        try:
            staging.rename(out_dir)
        except BaseException:
            backup.rename(out_dir)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(backup)
    else:
        staging.rename(out_dir)


def _foreign_files(*, out_dir: Path) -> list[str]:
    foreign: list[str] = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(out_dir).parts
        # bytecode caches are byproducts of our own generated modules; judge
        # relative to out_dir so a __pycache__ ANCESTOR can't blind the scan
        if "__pycache__" in rel_parts:
            continue
        rel = "/".join(rel_parts)
        if path.suffix != ".py":
            foreign.append(rel)
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
        if GENERATED_MARKER not in first_line:
            foreign.append(rel)
    return foreign


if __name__ == "__main__":
    main()
