"""Inventions planted by hand in a real output, to see whether the rules find them.

The pre-registration (7.5) has the builder tested on development outputs "with inventions
planted by hand". A plan is a short JSON file written by a person:

    {"sentences": ["..."],            # appended as paragraphs, spread through the document
     "headings": ["..."],             # appended as h3
     "equations": ["<math>...</math>"],   # appended as display maths
     "edits": [{"kind": "number" | "word" | "equation_symbol",
                "find": "<exact text in the HTML>", "replace": "<what it becomes>"}]}

`apply_plan` returns the changed HTML and what was planted; `check` scores both versions
and says, for each planted unit, whether a unit the baseline did not flag is now flagged.

What each kind is meant to show:

- `sentence`, `heading`, `equations` (no gold partner): invented text the rules should find;
- `number`: a digit changed inside a sentence that is otherwise copied. The sentence rule
  cannot see it (it keeps most of its 4-grams); the number rule should;
- `word`: a word changed inside a sentence, same shape. Neither rule sees it, by design: this
  is the blind spot the pre-registration names, planted so its size is measured;
- `equation_symbol`: a symbol added to a real equation. Found when the equation aligns to a
  gold equation (an added symbol), or when it has no gold partner and the change makes its
  symbol sequence differ enough.

A planted unit is "found" when a unit the baseline did not flag is flagged in the changed
output and matches it (sentence: starts like it; heading and equation: same text; number:
the new number; word: contains the new words; equation_symbol: any newly flagged equation
no other plant explains). Newly flagged units that match no plant are counted too: they say
whether planting disturbed anything else.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readablestem.eval.invented import score as score_mod
from readablestem.eval.invented.read import read_output
from readablestem.eval.invented.text import Reference, numbers_of

PLANT_KINDS = ("sentence", "heading", "equation", "number", "word", "equation_symbol")
EDIT_KINDS = ("number", "word", "equation_symbol")
_PARAGRAPH_END = re.compile(r"</p>", re.IGNORECASE)
_BODY_END = re.compile(r"</body>", re.IGNORECASE)
MATCH_PREFIX_CHARS = 40


@dataclass(frozen=True)
class Planted:
    kind: str
    text: str  # sentence or heading text, MathML, or the replacement
    find: str = ""
    tokens: str = ""  # an equation's tokens as the scorer reads them
    number: str = ""  # a number edit's new number, as the scorer prints it

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text("utf-8"))
    for edit in plan.get("edits", []):
        if edit.get("kind") not in EDIT_KINDS or not edit.get("find") or "replace" not in edit:
            raise ValueError(f"an edit needs kind (one of {EDIT_KINDS}), find and replace")
    return plan


def _insert_points(html: str, count: int) -> list[int]:
    """`count` places in `html`, evenly spread over its paragraphs (after a `</p>`), or all
    before `</body>` when it has none."""
    ends = [m.end() for m in _PARAGRAPH_END.finditer(html)]
    if not ends:
        body = _BODY_END.search(html)
        return [body.start() if body else len(html)] * count
    return [ends[min(len(ends) - 1, (i * len(ends)) // max(count, 1))] for i in range(count)]


def apply_plan(html: str, plan: dict[str, Any]) -> tuple[str, list[Planted]]:
    """The plan applied to `html`. Raises ValueError when an edit's text is not found."""
    planted: list[Planted] = []
    for edit in plan.get("edits", []):
        if edit["find"] not in html:
            raise ValueError(f"edit text not found in the output: {edit['find'][:60]!r}")
        html = html.replace(edit["find"], edit["replace"], 1)
        new = [n for n in numbers_of(edit["replace"]) if n not in numbers_of(edit["find"])]
        planted.append(
            Planted(
                edit["kind"],
                edit["replace"],
                edit["find"],
                number=score_mod._plain_number(new[0]) if edit["kind"] == "number" and new else "",
            )
        )
    items: list[tuple[str, str]] = []
    for kind, key, template in (
        ("sentence", "sentences", "<p>{}</p>"),
        ("heading", "headings", "<h3>{}</h3>"),
        ("equation", "equations", "{}"),
    ):
        items += [
            (kind, template.format(html_lib.escape(text) if kind != "equation" else text))
            for text in plan.get(key, [])
        ]
    points = _insert_points(html, len(items))
    # Insert from the end so earlier offsets stay valid.
    for (_kind, fragment), point in sorted(
        zip(items, points, strict=True), key=lambda pair: -pair[1]
    ):
        html = html[:point] + fragment + html[point:]
    for kind, key in (
        ("sentence", "sentences"),
        ("heading", "headings"),
        ("equation", "equations"),
    ):
        for text in plan.get(key, []):
            tokens = (score_mod._tokens_of(text) or "") if kind == "equation" else ""
            planted.append(Planted(kind, text, tokens=tokens))
    return html, planted


def _flagged(verdicts: Sequence[score_mod.Verdict]) -> Counter[tuple[str, str]]:
    return Counter((v.kind, v.text) for v in verdicts if v.unsupported)


def _take(pool: Counter[tuple[str, str]], kinds: Sequence[str], match) -> bool:
    for key in list(pool):
        if key[0] in kinds and pool[key] > 0 and match(key[1]):
            pool[key] -= 1
            return True
    return False


def _squash(text: str) -> str:
    return " ".join(html_lib.unescape(text).split())


def check(baseline: score_mod.Score, planted_score: score_mod.Score, planted: Sequence[Planted]):
    """For each kind of plant: how many, how many found, and the newly flagged units no plant
    explains. Returns a JSON-ready dict (counts and rates only; no text)."""
    new = _flagged(planted_score.verdicts) - _flagged(baseline.verdicts)
    found: Counter[str] = Counter()
    total: Counter[str] = Counter()
    # Exact matches first, the loose "any newly flagged equation" match last.
    order = sorted(planted, key=lambda p: p.kind == "equation_symbol")
    for item in order:
        total[item.kind] += 1
        if item.kind == "sentence":
            prefix = _squash(item.text)[:MATCH_PREFIX_CHARS]
            hit = _take(new, ("sentence",), lambda text, prefix=prefix: text.startswith(prefix))
        elif item.kind == "heading":
            title = _squash(item.text)
            hit = _take(new, ("heading",), lambda text, t=title: text == t)
        elif item.kind == "equation":
            hit = bool(item.tokens) and _take(
                new,
                ("equation_unaligned", "equation_aligned"),
                lambda text, t=item.tokens: t.startswith(text.removesuffix(" [cut]")),
            )
        elif item.kind == "number":
            hit = bool(item.number) and _take(
                new, ("number",), lambda text, n=item.number: text == n
            )
        elif item.kind == "word":
            phrase = _squash(re.sub(r"<[^>]+>", " ", item.text))
            hit = _take(new, ("sentence",), lambda text, ph=phrase: ph in text)
        else:  # equation_symbol
            hit = _take(new, ("equation_aligned", "equation_unaligned"), lambda _text: True)
        found[item.kind] += hit
        if item.kind in ("sentence", "heading"):
            # The numbers a planted line carries are flagged for being in it: explained.
            for number in numbers_of(item.text):
                wanted = score_mod._plain_number(number)
                _take(new, ("number",), lambda text, n=wanted: text == n)
    return {
        "planted": {kind: total[kind] for kind in PLANT_KINDS if total[kind]},
        "found": {kind: found[kind] for kind in PLANT_KINDS if total[kind]},
        "recall": {kind: found[kind] / total[kind] for kind in PLANT_KINDS if total[kind]},
        "newly_flagged_unexplained": sum(new.values()),
        "baseline_flagged": sum(_flagged(baseline.verdicts).values()),
    }


def self_check(
    reference: Reference,
    html: str,
    plan: dict[str, Any],
    *,
    gold_equations: Sequence[str] | None = None,
    name: str = "output.html",
) -> dict[str, Any]:
    """Score `html` and `html` with the plan applied, and compare (`check`)."""
    changed, planted = apply_plan(html, plan)
    base = score_mod.score_output(
        reference, read_output(html, "html", name=name), gold_equations=gold_equations
    )
    after = score_mod.score_output(
        reference, read_output(changed, "html", name=name), gold_equations=gold_equations
    )
    result = check(base, after, planted)
    result["plan_in_reference"] = {
        # A number edit whose new number the PDF prints anyway cannot be found: say so.
        "numbers_the_pdf_prints": sum(
            1
            for p in planted
            if p.kind == "number"
            and p.number
            and any(score_mod._plain_number(n) == p.number for n in reference.numbers)
        )
    }
    return result
