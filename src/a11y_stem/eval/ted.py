"""Tree edit distance (Zhang and Shasha, 1989) with unit costs.

Reference: K. Zhang and D. Shasha, "Simple fast algorithms for the editing distance
between trees and related problems", SIAM J. Computing 18(6), 1989.

Operations, each cost 1:
- delete a node (its children move up to its parent),
- insert a node,
- relabel a node (cost 0 when labels are equal).

The implementation numbers nodes in postorder, records each node's leftmost leaf
descendant, and fills a forest-distance table once per pair of "keyroots". It runs in
O(|A| * |B| * min(depth, leaves)^2) time, which is fine for equations (tens to a few
hundred nodes).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class Node:
    """An ordered, labelled tree node."""

    label: str
    children: list[Node] = field(default_factory=list)

    def size(self) -> int:
        return 1 + sum(child.size() for child in self.children)

    @classmethod
    def build(cls, spec: str | tuple) -> Node:
        """Build from a nested tuple, e.g. ("f", ("d", "a", ("c", "b")), "e")."""
        if isinstance(spec, str):
            return cls(spec)
        label, *kids = spec
        return cls(label, [cls.build(k) for k in kids])


def _postorder(root: Node) -> tuple[list[str], list[int]]:
    """Return (labels in postorder, leftmost-leaf-descendant index per node)."""
    labels: list[str] = []
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
        labels.append(node.label)
        index = len(labels) - 1
        own_leftmost = first_leaf if first_leaf is not None else index
        leftmost.append(own_leftmost)
        if stack:
            parent, parent_next, parent_first = stack.pop()
            if parent_first is None:
                parent_first = own_leftmost
            stack.append((parent, parent_next, parent_first))
    return labels, leftmost


def _keyroots(leftmost: Iterable[int]) -> list[int]:
    """Keyroots: for each distinct leftmost leaf, the highest node index that has it."""
    last_with: dict[int, int] = {}
    for index, leaf in enumerate(leftmost):
        last_with[leaf] = index
    return sorted(last_with.values())


def tree_edit_distance(a: Node, b: Node) -> int:
    """Minimum number of node deletions, insertions and relabels turning `a` into `b`."""
    labels_a, left_a = _postorder(a)
    labels_b, left_b = _postorder(b)
    n, m = len(labels_a), len(labels_b)
    tree_dist = [[0] * m for _ in range(n)]

    for i in _keyroots(left_a):
        for j in _keyroots(left_b):
            li, lj = left_a[i], left_b[j]
            rows, cols = i - li + 2, j - lj + 2
            forest = [[0] * cols for _ in range(rows)]
            for x in range(1, rows):
                forest[x][0] = forest[x - 1][0] + 1
            for y in range(1, cols):
                forest[0][y] = forest[0][y - 1] + 1
            for x in range(1, rows):
                ia = li + x - 1
                for y in range(1, cols):
                    jb = lj + y - 1
                    delete = forest[x - 1][y] + 1
                    insert = forest[x][y - 1] + 1
                    if left_a[ia] == li and left_b[jb] == lj:
                        # Both prefixes are whole trees: relabel the roots.
                        relabel = forest[x - 1][y - 1] + (labels_a[ia] != labels_b[jb])
                        forest[x][y] = min(delete, insert, relabel)
                        tree_dist[ia][jb] = forest[x][y]
                    else:
                        p, q = left_a[ia] - li, left_b[jb] - lj
                        forest[x][y] = min(delete, insert, forest[p][q] + tree_dist[ia][jb])
    return tree_dist[n - 1][m - 1]


def teds(a: Node, b: Node) -> float:
    """Tree-edit-distance similarity: 1 - TED / max(|a|, |b|), clamped to [0, 1].

    Same normalisation as TEDS for tables (Zhong et al., 2020, PubTabNet). With unit
    costs TED can exceed max(|a|, |b|) for very different shapes, hence the clamp.
    """
    distance = tree_edit_distance(a, b)
    largest = max(a.size(), b.size())
    return max(0.0, 1.0 - distance / largest)
