"""Conversion benchmarks: protodantic against the protobuf runtime and pydantic
JSON on the same generated models. Run with `uv run python benchmarks/run.py`.

Each size is checked for a lossless round-trip before it is timed, so the
script doubles as a smoke test.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
import tempfile
import timeit
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

import click

from protodantic import compile_fdset, generate_source

_PROTO = Path(__file__).parent / "order.proto"
_SIZES = (1, 20, 200)
_REPEATS = 5


def _load_models(*, workspace: Path) -> ModuleType:
    path = workspace / "bench_models.py"
    path.write_text(generate_source(compile_fdset([str(_PROTO)])), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("bench_models", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # pydantic resolves the generated module's forward references via sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_order(*, models: ModuleType, items: int) -> Any:
    return models.Order(
        id=123456789012,
        customer="acme-corp",
        status=models.Status.ACTIVE,
        items=[
            models.LineItem(
                sku=f"SKU-{i}",
                title=f"Widget number {i}",
                quantity=i % 7 + 1,
                price=models.Money(currency="USD", units=i * 3, nanos=990000000),
                tags=["a", "bb", "ccc"],
                note="n" if i % 2 else None,
                weight=1.5 * i,
                taxable=bool(i % 2),
            )
            for i in range(items)
        ],
        labels={f"k{i}": f"v{i}" for i in range(5)},
        created=datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
        total=models.Money(currency="USD", units=999, nanos=0),
        signature=b"\x01\x02" * 16,
        card_token="tok_123",
    )


def _microseconds(*, call: Callable[[], object], number: int) -> float:
    return min(timeit.repeat(call, number=number, repeat=_REPEATS)) / number * 1e6


def _report(*, models: ModuleType, items: int) -> None:
    order = _build_order(models=models, items=items)
    wire = order.to_proto_bytes()
    json_text = order.model_dump_json()
    message_class = models.Order.proto_class()
    message = message_class.FromString(wire)
    assert models.Order.from_proto_bytes(wire) == order
    assert message_class.FromString(models.Order.from_proto(message).to_proto_bytes()) == message

    number = max(20, 4000 // (items + 3))
    rows = {
        "raw protobuf": (partial(message_class.FromString, wire), message.SerializeToString),
        "pydantic JSON": (
            partial(models.Order.model_validate_json, json_text),
            order.model_dump_json,
        ),
        "protodantic": (partial(models.Order.from_proto_bytes, wire), order.to_proto_bytes),
    }
    timings = {
        name: (_microseconds(call=decode, number=number), _microseconds(call=encode, number=number))
        for name, (decode, encode) in rows.items()
    }

    click.echo(f"\nitems={items}  wire={len(wire)} B  json={len(json_text)} B")
    click.echo(f"  {'':24}{'decode us':>12}{'encode us':>12}")
    for name, (decode_us, encode_us) in timings.items():
        click.echo(f"  {name:24}{decode_us:12.1f}{encode_us:12.1f}")
    ours, baseline = timings["protodantic"], timings["pydantic JSON"]
    click.echo(
        f"  {'protodantic / JSON':24}{ours[0] / baseline[0]:11.1f}x{ours[1] / baseline[1]:11.1f}x"
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        models = _load_models(workspace=Path(tmp_dir))
    for items in _SIZES:
        _report(models=models, items=items)


if __name__ == "__main__":
    main()
