"""eval/notation_flags.py: the course evidence's extra style flags, counted offline."""

from __future__ import annotations

import json

from a11y_stem.eval import notation_flags
from a11y_stem.pipeline.validate import safe_latex_to_mathml

BOLD_V = "\U0001d42f"
ITALIC_V = "\U0001d463"
ITALIC_T = "\U0001d461"


def mathml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "block")


def test_unstyled_removes_every_math_style():
    assert notation_flags.unstyled(f"{BOLD_V} = d/{ITALIC_T}") == "v = d/t"


def test_flags_are_counted_with_and_without_the_styles_of_the_text_layer():
    rows = [
        # A bold v read plain: the printed layer already says so; stripped, the course does.
        (f"{BOLD_V} = 5", mathml("v = 5"), True),
        # A plain v (speed) read plain: right, but stripped the course questions it.
        (f"{ITALIC_V} = 5", mathml("v = 5"), False),
        # Bold kept: nothing to say either way.
        (f"{BOLD_V} = 5", mathml(r"\mathbf{v} = 5"), False),
    ]
    printed = notation_flags.count_flags(rows, frozenset({"v"}), strip=False)
    assert (printed.items, printed.wrong) == (3, 1)
    assert (printed.flagged_before, printed.flagged_after) == (1, 1)
    assert (printed.new_true, printed.new_false) == (0, 0)
    stripped = notation_flags.count_flags(rows, frozenset({"v"}), strip=True)
    assert (stripped.flagged_before, stripped.flagged_after) == (0, 2)
    assert (stripped.new_true, stripped.new_false) == (1, 1)


def test_rows_come_from_an_items_file_and_the_render_cache(tmp_path, monkeypatch):
    from a11y_stem.eval import pipeline_eval, render

    gold = [{"id": "g/eq-0", "kind": "equation", "mathml": "<math><mi>v</mi></math>"}]
    monkeypatch.setattr(pipeline_eval, "load_gold", lambda _: gold)
    monkeypatch.setattr(render, "render_equation_text", lambda m, c: f"{BOLD_V} = 5")
    items = tmp_path / "items.jsonl"
    rows = [
        {"id": "g/eq-0", "pred_mathml": "<math><mi>v</mi></math>", "exact_lenient": False},
        {"id": "g/eq-0", "pred_mathml": None, "exact_lenient": False},  # no proposal
        {"id": "other", "pred_mathml": "<math/>", "exact_lenient": True},  # not in gold
    ]
    items.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    loaded = notation_flags.load_rows(items, tmp_path, tmp_path)
    assert loaded == [(f"{BOLD_V} = 5", "<math><mi>v</mi></math>", True)]
