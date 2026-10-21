"""Equations in table cells, for the document task: how many of the gold table cells' formulas
the converted page has in a table cell of its own (docs/tables.md, "Equations in cells").

An OpenStax table cell holds its formulas as MathML; the document task's equation scorer sees
only the equations the pipeline made as elements of their own, and a table claimed by the table
step is one TABLE element, so its cells' formulas are not equations to it: "undetected" in the
held-out analysis (bucket A2, table cells 2.7% of the gold). With `FEATURE_TABLES` off the table
is an image and the formulas are lost; with it on the table step reads them as maths cells.
This counts, per section, the `<math>` elements of the gold's tables and of the output's table
cells, and pairs them one to one by the symbols scorer (`eval.equations_symbols`), best first.

Pure functions of two HTML strings and the gold records of the page; no model, no network.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from html.parser import HTMLParser

from readablestem.eval import equations_symbols as sym
from readablestem.eval.equations import MathMLError, score_mathml

_MATH = re.compile(r"<math\b.*?</math>", re.S | re.I)
SIMILAR = 0.9  # symbols similarity from which a pair counts as "found, not exact"


def gold_cell_maths(records: Sequence[dict]) -> list[str]:
    """The MathML of every `<math>` in the HTML of the gold's data tables."""
    out: list[str] = []
    for record in records:
        if record.get("kind") == "table" and record.get("html") and not record.get("is_layout"):
            out += _MATH.findall(record["html"])
    return out


class _CellMaths(HTMLParser):
    """The `<math>` elements inside `<td>` and `<th>` of an HTML document, as text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.cells = 0
        self.depth_math = 0
        self.parts: list[str] = []
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("td", "th"):
            self.cells += 1
        if self.cells and (tag == "math" or self.depth_math):
            self.depth_math += 1
            attributes = "".join(f' {k}="{v or ""}"' for k, v in attrs)
            self.parts.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cells:
            self.cells -= 1
        if self.depth_math:
            self.parts.append(f"</{tag}>")
            self.depth_math -= 1
            if not self.depth_math:
                self.found.append("".join(self.parts))
                self.parts = []

    def handle_data(self, data: str) -> None:
        if self.depth_math:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.depth_math:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.depth_math:
            self.parts.append(f"&#{name};")


def output_cell_maths(html: str) -> list[str]:
    parser = _CellMaths()
    parser.feed(html)
    parser.close()
    return parser.found


def _pairs(predicted: Sequence[str], gold: Sequence[str]) -> list[tuple[int, int, float, bool]]:
    """(predicted index, gold index, symbols similarity, symbols exact), one to one, best first,
    for pairs at least `SIMILAR` alike."""
    scored: list[tuple[float, int, int, bool]] = []
    for g, gold_math in enumerate(gold):
        for p, pred_math in enumerate(predicted):
            try:
                s = sym.score_symbols(pred_math, gold_math)
            except MathMLError:
                continue
            if s.similarity >= SIMILAR:
                scored.append((s.similarity, p, g, s.exact))
    scored.sort(key=lambda row: (-row[0], not row[3]))
    used_p: set[int] = set()
    used_g: set[int] = set()
    out = []
    for similarity, p, g, exact in scored:
        if p in used_p or g in used_g:
            continue
        used_p.add(p)
        used_g.add(g)
        out.append((p, g, similarity, exact))
    return out


def score_page(html: str, records: Sequence[dict]) -> dict | None:
    """`{"gold", "predicted", "found", "found_exact_symbols", "found_exact_lenient"}` for a
    page whose gold has data tables with formulas in their cells, else None."""
    gold = gold_cell_maths(records)
    if not gold:
        return None
    predicted = output_cell_maths(html)
    pairs = _pairs(predicted, gold)
    lenient = 0
    for p, g, _, _ in pairs:
        with contextlib.suppress(MathMLError):
            lenient += bool(score_mathml(predicted[p], gold[g]).exact_lenient)
    return {
        "gold": len(gold),
        "predicted": len(predicted),
        "found": len(pairs),
        "found_exact_symbols": sum(1 for *_, exact in pairs if exact),
        "found_exact_lenient": lenient,
    }


def summarize(items: Sequence[dict]) -> dict:
    """The per-page counts added up, with the shares of the gold."""
    total = {k: sum(i[k] for i in items) for k in ("gold", "predicted", "found")}
    total["found_exact_symbols"] = sum(i["found_exact_symbols"] for i in items)
    total["found_exact_lenient"] = sum(i["found_exact_lenient"] for i in items)
    gold = total["gold"]
    return {
        **total,
        "pages": len(items),
        "found_rate": total["found"] / gold if gold else 0.0,
        "exact_symbols_rate": total["found_exact_symbols"] / gold if gold else 0.0,
        "exact_lenient_rate": total["found_exact_lenient"] / gold if gold else 0.0,
    }
