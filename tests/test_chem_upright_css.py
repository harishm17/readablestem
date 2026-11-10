"""An all-chemistry equation has no style on its element symbols (`intent` is on `<math>`), and
every place MathML is shown draws them upright with `math[intent] mi { text-transform: none }`:
the student file (`assemble.py`) and the review screen (`web/static/app.css`)."""

from __future__ import annotations

from pathlib import Path

APP_CSS = Path(__file__).resolve().parents[1] / "src/readablestem/web/static/app.css"
RULE = "math[intent] mi { text-transform: none; }"


def test_the_rule_is_in_the_review_stylesheet_and_the_student_file() -> None:
    from readablestem.pipeline import assemble

    assert RULE in APP_CSS.read_text(encoding="utf-8")
    assert RULE in Path(assemble.__file__).read_text(encoding="utf-8")
