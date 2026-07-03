"""USE CASES: well-known types map to idiomatic python — Timestamp to aware
datetime, Duration to timedelta, wrappers to `T | None`, Struct/Value to plain
python data, Any (deferred).
"""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(scope="module")
def mod(generate):
    return generate("wkt.proto")


def test_timestamp_roundtrip_aware_utc(mod):
    """Timestamps round-trip as timezone-aware UTC datetimes."""
    at = datetime(2026, 7, 2, 12, 30, 45, 123456, tzinfo=timezone.utc)
    restored = mod.Temporal.from_proto_bytes(mod.Temporal(at=at).to_proto_bytes())
    assert restored.at == at
    assert restored.at.tzinfo is not None


def test_naive_datetime_treated_as_utc(mod):
    """Policy: naive datetimes are interpreted as UTC on the way in."""
    naive = datetime(2026, 1, 1, 8, 0, 0)
    restored = mod.Temporal.from_proto_bytes(mod.Temporal(at=naive).to_proto_bytes())
    assert restored.at == naive.replace(tzinfo=timezone.utc)


def test_pre_epoch_timestamp(mod):
    """Dates before 1970 work (negative seconds)."""
    at = datetime(1955, 11, 5, 6, 0, tzinfo=timezone.utc)
    restored = mod.Temporal.from_proto_bytes(mod.Temporal(at=at).to_proto_bytes())
    assert restored.at == at


def test_duration_roundtrip_incl_negative(mod):
    """Durations round-trip at microsecond precision, including negatives."""
    span = timedelta(days=-1, seconds=3, microseconds=250)
    restored = mod.Temporal.from_proto_bytes(mod.Temporal(span=span).to_proto_bytes())
    assert restored.span == span


def test_wkt_in_containers(mod):
    """Timestamps in repeated fields and Durations as map values."""
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 15, 23, 59, 59, tzinfo=timezone.utc)
    temporal = mod.Temporal(
        history=[t1, t2],
        budgets={"ci": timedelta(minutes=30), "deploy": timedelta(seconds=90)},
    )
    restored = mod.Temporal.from_proto_bytes(temporal.to_proto_bytes())
    assert restored == temporal


def test_wrapper_types_map_to_optional_scalars(generate):
    """google.protobuf.*Value wrappers become `T | None`: absent means None,
    present-with-default is preserved."""
    mod = generate("wrappers.proto")
    settings = mod.Settings(display_name="", volume=0, enabled=False)
    restored = mod.Settings.from_proto_bytes(settings.to_proto_bytes())
    assert restored.display_name == ""
    assert restored.volume == 0
    assert restored.enabled is False
    assert restored.gain is None  # never set -> None
    assert restored.blob is None

    empty = mod.Settings.from_proto_bytes(mod.Settings().to_proto_bytes())
    assert empty.display_name is None


def test_struct_maps_to_python_data(generate):
    """google.protobuf.Struct/Value/ListValue become dict / scalar / list."""
    mod = generate("structs.proto")
    blob = mod.Blob(
        meta={"name": "kory", "score": 9.5, "nested": {"ok": True}},
        single="just a string",
        items=[1.0, "two", None, False],
    )
    restored = mod.Blob.from_proto_bytes(blob.to_proto_bytes())
    assert restored == blob


def test_value_field_explicit_json_null(generate):
    """protodantic.NULL distinguishes an explicit JSON null in a Value field
    from an unset field: None = unset, NULL = null on the wire."""
    from protodantic import NULL

    mod = generate("structs.proto")
    blob = mod.Blob(single=NULL)
    assert blob.to_proto().HasField("single")

    restored = mod.Blob.from_proto_bytes(blob.to_proto_bytes())
    assert restored.single is NULL

    unset = mod.Blob.from_proto_bytes(mod.Blob().to_proto_bytes())
    assert unset.single is None


def test_null_sentinel_inside_containers_normalizes_to_none(generate):
    """Inside Struct/ListValue containers plain None already means JSON null,
    so a nested NULL sentinel is accepted and comes back as None."""
    from protodantic import NULL

    mod = generate("structs.proto")
    blob = mod.Blob(meta={"k": NULL}, items=[NULL, "x"])
    restored = mod.Blob.from_proto_bytes(blob.to_proto_bytes())
    assert restored.meta == {"k": None}
    assert restored.items == [None, "x"]


def test_null_sentinel_survives_copies(generate):
    """`is NULL` identity checks must hold across model_copy(deep=True)."""
    from protodantic import NULL

    mod = generate("structs.proto")
    clone = mod.Blob(single=NULL).model_copy(deep=True)
    assert clone.single is NULL


def test_null_sentinel_serializes_as_json_null(generate):
    """model_dump_json emits real null for NULL; python-mode dumps keep the
    sentinel so NULL-vs-unset survives dump/validate round-trips."""
    from protodantic import NULL

    mod = generate("structs.proto")
    blob = mod.Blob(single=NULL, meta={"k": NULL})
    text = blob.model_dump_json()
    assert '"single":null' in text
    assert '"k":null' in text
    assert blob.model_dump()["single"] is NULL


def test_any_field_packs_and_unpacks_models(generate):
    """google.protobuf.Any maps to `typing.Any`: the field accepts any generated
    ProtoModel; to_proto packs it (type_url + bytes), from_proto resolves the
    type_url against the model registry and unpacks the right model class."""
    mod = generate("anypayload.proto")
    envelope = mod.Envelope(id="e-1", payload=mod.Note(text="hello"))
    msg = envelope.to_proto()
    assert msg.payload.type_url.endswith("/test.anypkg.Note")

    restored = mod.Envelope.from_proto_bytes(envelope.to_proto_bytes())
    assert isinstance(restored.payload, mod.Note)
    assert restored.payload.text == "hello"


def test_any_field_unset_is_none(generate):
    """An unset Any field is None, like any other message field."""
    mod = generate("anypayload.proto")
    restored = mod.Envelope.from_proto_bytes(mod.Envelope(id="e-2").to_proto_bytes())
    assert restored.payload is None


def test_any_with_unregistered_type_is_clear_error(generate):
    """Unpacking an Any whose type_url has no imported model raises LookupError
    naming the missing type — never a silent wrong value."""
    mod = generate("anypayload.proto")
    raw = mod.Envelope.proto_class()()
    raw.id = "e-3"
    raw.payload.type_url = "type.googleapis.com/unknown.Type"
    raw.payload.value = b"\x0a\x03abc"
    with pytest.raises(LookupError, match="unknown.Type"):
        mod.Envelope.from_proto(raw)
