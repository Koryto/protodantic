"""USE CASES: multi-file schemas — imports across files, transitively pulled
dependencies, and same-named messages in different packages coexisting.
"""

def test_cross_file_imports(generate):
    """A message using a type imported from another file round-trips."""
    mod = generate("orders.proto", "common.proto")
    order = mod.Order(
        id="o-1",
        total=mod.Money(currency="PLN", units=250),
        refunds=[mod.Money(currency="PLN", units=50)],
    )
    restored = mod.Order.from_proto_bytes(order.to_proto_bytes())
    assert restored == order


def test_imported_dependencies_generated_transitively(generate):
    """Passing only the top-level proto still generates models for its imports."""
    mod = generate("orders.proto")
    assert hasattr(mod, "Money")
    order = mod.Order(id="o-2", total=mod.Money(currency="EUR", units=9))
    assert mod.Order.from_proto_bytes(order.to_proto_bytes()) == order


def test_same_message_name_across_packages(generate):
    """alpha.Thing and beta.Thing coexist in one generated module; each can be
    looked up by its proto full name and round-trips independently."""
    from protodantic import model_for

    mod = generate("collision_a.proto", "collision_b.proto")
    alpha_thing = model_for("alpha.Thing")
    beta_thing = model_for("beta.Thing")
    assert alpha_thing is not beta_thing

    a = alpha_thing(value="from-alpha")
    b = beta_thing(value="from-beta")
    assert alpha_thing.from_proto_bytes(a.to_proto_bytes()) == a
    assert beta_thing.from_proto_bytes(b.to_proto_bytes()) == b

    holder_a = model_for("alpha.Holder")(thing=a)
    restored = holder_a.__class__.from_proto_bytes(holder_a.to_proto_bytes())
    assert restored.thing.value == "from-alpha"
