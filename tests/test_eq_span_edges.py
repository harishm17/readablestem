"""`scripts/eq_span_edges.py`: edge classes and the summary, on made-up rows."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import eq_span_edges as edges  # noqa: E402


def test_an_edge_error_is_a_difference_in_letters_and_digits():
    assert edges.edge_class("q(x)", "pq(x)") == "ran on"
    assert edges.edge_class("pm,q(x)", "q(x)") == "cut short"
    assert edges.edge_class("pm,q", "qmp") == "inside"  # the same letters in another order
    assert edges.edge_class("δt", "∆t") == "inside"  # Delta and the increment sign
    assert edges.edge_class("ab", "ac") == "ran on and cut short"
    assert edges.edge_class("a", "") == "empty"


def row(gid, display, found, sim, gold_sig="ab", pred_sig="ab"):
    return {
        "id": gid,
        "display": display,
        "in_float": False,
        "tokens": 2,
        "latex": "",
        "found": found,
        "sim": sim,
        "gold_sig": gold_sig,
        "pred_sig": pred_sig,
    }


def test_the_summary_counts_edge_errors_for_inline_gold_only():
    papers = [
        {
            "rows": [
                row("a", "inline", True, 1.0),
                row("b", "inline", True, 0.6, "ab", "abc"),
                row("c", "inline", True, 0.6, "ab", "ba"),
                row("d", "inline", False, 0.0),
                row("e", "block", True, 0.5, "ab", "abc"),
            ],
            "unmatched": [],
            "n_pred_inline": 3,
        }
    ]
    s = edges.summarise(papers)
    assert (s["gold_inline"], s["found_inline"], s["span_wrong"], s["edge_wrong"]) == (4, 3, 2, 1)
    assert s["span_edges"] == {"ran on": 1, "inside": 1}
