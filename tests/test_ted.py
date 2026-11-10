"""Tree edit distance: known pairs plus a brute-force cross-check on random small trees."""

from __future__ import annotations

import random
from functools import cache

from readablestem.eval.ted import Node, tree_edit_distance


def T(spec) -> Node:
    return Node.build(spec)


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
