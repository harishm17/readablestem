"""Tree edit distance: known pairs plus a brute-force cross-check on random small trees."""

from __future__ import annotations

import random
from functools import cache

import pytest

from a11y_stem.eval.ted import Node, teds, tree_edit_distance


def T(spec) -> Node:
    return Node.build(spec)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # Identical trees.
        (("f", "a", "b"), ("f", "a", "b"), 0),
        # Single relabel.
        (("f", "a", "b"), ("f", "a", "c"), 1),
        # Single leaf insertion / deletion.
        (("f", "a"), ("f", "a", "b"), 1),
        (("f", "a", "b"), ("f", "a"), 1),
        # Classic example used in the Zhang-Shasha literature (and the `zss` package docs):
        # f(d(a, c(b)), e) -> f(c(d(a, b)), e) costs 2.
        (("f", ("d", "a", ("c", "b")), "e"), ("f", ("c", ("d", "a", "b")), "e"), 2),
        # Deleting an inner node lifts its children: f(g(a, b)) -> f(a, b).
        (("f", ("g", "a", "b")), ("f", "a", "b"), 1),
        # Single node vs single node.
        ("a", "a", 0),
        ("a", "b", 1),
        # Sibling order matters: f(a, b) -> f(b, a) needs 2 relabels.
        (("f", "a", "b"), ("f", "b", "a"), 2),
    ],
)
def test_known_pairs(a, b, expected):
    assert tree_edit_distance(T(a), T(b)) == expected
    # Symmetric with unit costs.
    assert tree_edit_distance(T(b), T(a)) == expected


def test_chain_vs_star_exceeds_max_size_and_teds_is_clamped():
    chain = T(("r", ("a", ("b", ("c", "d")))))  # depth 5, 5 nodes
    star = T(("r", "a", "b", "c", "d"))  # 5 nodes
    # A valid mapping must preserve ancestry. Every chain pair is ancestor-related, but in
    # the star only root-leaf pairs are, so at most 2 nodes map: 3 deletes + 3 inserts.
    assert tree_edit_distance(chain, star) == 6
    assert teds(chain, star) == 0.0  # 1 - 6/5 < 0, clamped


def test_teds_bounds():
    assert teds(T(("f", "a")), T(("f", "a"))) == 1.0
    assert teds(T("a"), T(("x", "y", "z", "w"))) == 0.0


# ----------------------------------------------------------------- brute force reference


def _size(forest) -> int:
    return sum(1 + _size(children) for _, children in forest)


@cache
def _forest_distance(f, g) -> int:
    """Textbook recursion on the rightmost roots of two ordered forests."""
    if not f and not g:
        return 0
    if not f:
        return _size(g)
    if not g:
        return _size(f)
    (vl, vc), (wl, wc) = f[-1], g[-1]
    return min(
        _forest_distance(f[:-1] + vc, g) + 1,  # delete v
        _forest_distance(f, g[:-1] + wc) + 1,  # insert w
        _forest_distance(f[:-1], g[:-1]) + _forest_distance(vc, wc) + (vl != wl),
    )


def _to_tuple(node: Node):
    return (node.label, tuple(_to_tuple(c) for c in node.children))


def _random_tree(rng: random.Random, max_nodes: int) -> Node:
    root = Node(rng.choice("abc"))
    nodes = [root]
    for _ in range(rng.randint(0, max_nodes - 1)):
        parent = rng.choice(nodes)
        child = Node(rng.choice("abc"))
        parent.children.append(child)
        nodes.append(child)
    return root


def test_matches_brute_force_on_random_trees():
    rng = random.Random(20261006)
    for _ in range(300):
        a = _random_tree(rng, 7)
        b = _random_tree(rng, 7)
        expected = _forest_distance((_to_tuple(a),), (_to_tuple(b),))
        assert tree_edit_distance(a, b) == expected


def test_deep_tree_does_not_hit_recursion_limit():
    deep = Node("x")
    node = deep
    for _ in range(3000):
        child = Node("x")
        node.children.append(child)
        node = child
    assert tree_edit_distance(deep, Node("x")) == 3000
