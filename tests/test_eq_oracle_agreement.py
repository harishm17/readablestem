"""The agreement oracle (`scripts/eq_oracle_agreement.py`) on ten paired items written here.
Offline; nothing reads the real-world cache."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import eq_oracle_agreement as oracle  # noqa: E402


def pair(gid: str, latex: str, right: bool, text: str = "", review: str = "none") -> dict:
    return {
        "gold_id": gid,
        "gold_display": "inline",
        "pred_latex": latex,
        "pred_text": text,
        "exact_lenient": right,
        "review": review,
    }


# gold g0..g9: what each arm read and whether it is right
OURS = {
    "id": "paper",
    "n_gold": 10,
    "missed_gold_ids": ["g6", "g7"],
    "pairs": [
        pair("g0", "x_1", True, "x1"),  # both right, same
        pair("g1", r"\mathrm{d}x", True, "dx"),  # same under the upright-word fold
        pair("g2", "x_1", False, "x1"),  # ours wrong, chatbot right, differ
        pair("g3", "p", False, "p", review="required"),  # both wrong, differ, already asked
        pair("g4", "n", True, "n"),  # the chatbot has no reading
        pair("g5", "a+b", False, "a+b"),  # the same wrong reading in both
        pair("g8", "y", True, "y"),  # ours right, chatbot wrong
        pair("g9", "k", True, ""),  # same, but no text layer to check the span against
    ],
}
CHAT = {
    "id": "paper",
    "pairs": [
        pair("g0", "x_{1}", True),
        pair("g1", r"\text{d}x", True),
        pair("g2", "x_2", True),
        pair("g3", "q", False),
        pair("g5", "a+b", False),
        pair("g6", "m", True),  # a gold equation we did not detect
        pair("g8", "z", False),
        pair("g9", "k", True),
    ],
}


def counts(**kw) -> dict[str, int]:
    vec = oracle.paper_vector(OURS, CHAT, **kw)
    return {name: int(vec[oracle.COL[name]]) for name in oracle.COLUMNS}


def test_union_and_detection():
    c = counts()
    assert (c["gold"], c["ours_right"], c["chat_right"], c["union_right"]) == (10, 5, 5, 7)
    assert (c["undetected"], c["undetected_chat_found"], c["undetected_chat_right"]) == (2, 1, 1)


def test_agreement_needs_canonical_equality_and_a_span_within_one_glyph():
    c = counts()
    assert (c["ours_read"], c["both_read"]) == (8, 7)
    assert c["agree"] == 4  # g0, g1, g5, g9
    assert c["agree_span"] == 3  # g9 has no text layer, so its span cannot be checked
    assert c["agree_span_ours_right"] == 2  # g5 agrees and is wrong: a shared error
    assert c["agree_ours_right"] == 3


def test_disagree_flags_our_wrong_readings():
    c = counts()
    assert c["ours_wrong"] == 3
    assert (c["disagree"], c["wrong_disagree"]) == (4, 2)  # g2, g3, g8, g9; wrong: g2, g3
    assert (c["not_agree"], c["wrong_not_agree"]) == (5, 2)  # g4 has no witness
    assert (c["silent_wrong"], c["silent_disagree"], c["silent_not_agree"]) == (2, 1, 1)
    assert c["ours_wrong_chat_right"] == 1


def test_span_check_stand_in():
    assert oracle.glyphs_within_one("x1", "x_1") is True
    assert oracle.glyphs_within_one("x1", "x_1 + y") is False
    assert oracle.glyphs_within_one("", "x_1") is None


def test_summary_and_gate():
    summary = oracle.summarise({"p": oracle.paper_vector(OURS, CHAT)})
    assert summary["papers"] == 1 and summary["gold"] == 10
    assert summary["union_gain"][0] == pytest.approx(0.2)
    assert summary["agree_span_correct"][0] == pytest.approx(2 / 3)
    assert summary["recall_disagree"][0] == pytest.approx(2 / 3)
    assert summary["precision_disagree"][0] == pytest.approx(2 / 4)
    verdict = oracle.gate(summary)
    assert verdict["union_ok"] and not verdict["agree_ok"] and not verdict["go"]


def test_gate_goes_when_both_thresholds_hold():
    summary = {"union_gain": (0.05, None), "agree_span_correct": (0.97, None)}
    assert oracle.gate(summary)["go"]
    summary = {"union_gain": (0.039, None), "agree_span_correct": (0.99, None)}
    assert not oracle.gate(summary)["go"]
