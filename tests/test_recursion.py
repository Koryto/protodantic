"""USE CASES: self-recursive and mutually recursive message types generate
valid models and round-trip at arbitrary depth."""

import pytest


@pytest.fixture(scope="module")
def mod(generate):
    return generate("recursion.proto")


def test_self_recursive_tree(mod):
    tree = mod.TreeNode(
        name="root",
        children=[
            mod.TreeNode(name="a", children=[mod.TreeNode(name="a1")]),
            mod.TreeNode(name="b"),
        ],
    )
    restored = mod.TreeNode.from_proto_bytes(tree.to_proto_bytes())
    assert restored == tree
    assert restored.children[0].children[0].name == "a1"


def test_mutually_recursive_messages(mod):
    ping = mod.Ping(tag="1", pong=mod.Pong(tag="2", ping=mod.Ping(tag="3")))
    restored = mod.Ping.from_proto_bytes(ping.to_proto_bytes())
    assert restored == ping
    assert restored.pong.ping.tag == "3"
    assert restored.pong.ping.pong is None
