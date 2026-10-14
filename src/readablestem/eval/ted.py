"""Tree edit distance (Zhang and Shasha, 1989), unit costs or a relabel-cost hook.

Reference: K. Zhang and D. Shasha, "Simple fast algorithms for the editing distance
between trees and related problems", SIAM J. Computing 18(6), 1989.

Operations:
- delete a node (its children move up to its parent): cost 1,
- insert a node: cost 1,
- relabel a node: cost 0 when labels are equal, else 1; or whatever a `relabel` hook
  says (between 0 and 1). Table TEDS uses the hook: a cell's text costs its normalised
  Levenshtein distance (Zhong et al. 2020, `eval.tables`).

The implementation numbers nodes in postorder, records each node's leftmost leaf
descendant, and fills a forest-distance table once per pair of "keyroots". It runs in
O(|A| * |B| * min(depth, leaves)^2) time, which is fine for equations (tens to a few
hundred nodes) and tables (three levels: table, rows, cells). The hook is called once per
pair of nodes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass
class Node:
    """An ordered, labelled tree node. `content` is extra data a relabel hook may
    compare (a table cell's text); the unit-cost distance looks at `label` only."""

    label: str
    children: list[Node] = field(default_factory=list)
    content: str = ""

    def size(self) -> int:
        return 1 + sum(child.size() for child in self.children)

    @classmethod
    def build(cls, spec: str | tuple) -> Node:
        """Build from a nested tuple, e.g. ("f", ("d", "a", ("c", "b")), "e")."""
        if isinstance(spec, str):
            return cls(spec)
        label, *kids = spec
        return cls(label, [cls.build(k) for k in kids])


# Cost of turning one node into another (0 = the same, 1 = as bad as delete + insert would
# be for one of them). The default compares labels.
Relabel = Callable[[Node, Node], float]


def _postorder(root: Node) -> tuple[list[Node], list[int]]:
    """Return (nodes in postorder, leftmost-leaf-descendant index per node)."""
    nodes: list[Node] = []
    leftmost: list[int] = []

    # Iterative postorder so deep trees cannot hit the recursion limit.
    stack: list[tuple[Node, int, int | None]] = [(root, 0, None)]
    # Each frame: (node, next child index, leftmost leaf index seen so far)
    while stack:
        node, child_idx, first_leaf = stack.pop()
        if child_idx < len(node.children):
            stack.append((node, child_idx + 1, first_leaf))
            stack.append((node.children[child_idx], 0, None))
            continue
        nodes.append(node)
        index = len(nodes) - 1
        own_leftmost = first_leaf if first_leaf is not None else index
        leftmost.append(own_leftmost)
        if stack:
            parent, parent_next, parent_first = stack.pop()
            if parent_first is None:
                parent_first = own_leftmost
            stack.append((parent, parent_next, parent_first))
    return nodes, leftmost


def _keyroots(leftmost: Iterable[int]) -> list[int]:
    """Keyroots: for each distinct leftmost leaf, the highest node index that has it."""
    last_with: dict[int, int] = {}
    for index, leaf in enumerate(leftmost):
        last_with[leaf] = index
    return sorted(last_with.values())


def _unit_relabel(a: Node, b: Node) -> int:
    return int(a.label != b.label)


def _distance(a: Node, b: Node, relabel: Relabel) -> float:
    nodes_a, left_a = _postorder(a)
    nodes_b, left_b = _postorder(b)
    n, m = len(nodes_a), len(nodes_b)
    tree_dist: list[list[float]] = [[0] * m for _ in range(n)]

    for i in _keyroots(left_a):
        for j in _keyroots(left_b):
            li, lj = left_a[i], left_b[j]
            rows, cols = i - li + 2, j - lj + 2
            forest: list[list[float]] = [[0] * cols for _ in range(rows)]
            for x in range(1, rows):
                forest[x][0] = forest[x - 1][0] + 1
            for y in range(1, cols):
                forest[0][y] = forest[0][y - 1] + 1
            for x in range(1, rows):
                ia = li + x - 1
                row, above = forest[x], forest[x - 1]
                whole_a = left_a[ia] == li
                for y in range(1, cols):
                    jb = lj + y - 1
                    delete = above[y] + 1
                    insert = row[y - 1] + 1
                    if whole_a and left_b[jb] == lj:
                        # Both prefixes are whole trees: relabel the roots.
                        cost = relabel(nodes_a[ia], nodes_b[jb])
                        best = min(delete, insert, above[y - 1] + cost)
                        row[y] = best
                        tree_dist[ia][jb] = best
                    else:
                        p, q = left_a[ia] - li, left_b[jb] - lj
                        row[y] = min(delete, insert, forest[p][q] + tree_dist[ia][jb])
    return tree_dist[n - 1][m - 1]


def tree_edit_distance(a: Node, b: Node) -> int:
    """Minimum number of node deletions, insertions and relabels turning `a` into `b`."""
    return int(_distance(a, b, _unit_relabel))


def weighted_edit_distance(a: Node, b: Node, relabel: Relabel) -> float:
    """Tree edit distance with unit deletes and inserts and `relabel` for relabels.

    `relabel(x, y)` must be 0 for equal nodes and at most 1 (more would make a relabel
    worse than a delete plus an insert, which the algorithm then takes instead).
    """
    return _distance(a, b, relabel)


def teds(a: Node, b: Node, relabel: Relabel | None = None) -> float:
    """Tree-edit-distance similarity: 1 - TED / max(|a|, |b|), clamped to [0, 1].

    Same normalisation as TEDS for tables (Zhong et al., 2020, PubTabNet). With unit
    costs TED can exceed max(|a|, |b|) for very different shapes, hence the clamp.
    """
    distance = _distance(a, b, relabel or _unit_relabel)
    largest = max(a.size(), b.size())
    return max(0.0, 1.0 - distance / largest)
