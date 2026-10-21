"""Prose word error rate for the image page evaluation (ADR 0007).

The page reader writes the words of a page; the answer key for the words is the page's own
source: the printable HTML an OpenStax page is printed from (`eval/render.py`), with the
mathematics taken out of both sides, so what is scored is the prose (headings, paragraphs,
list items, captions, table cells), not the equations (the equation scorers do those).

    WER = (substitutions + deletions + insertions) / words in the answer key

over the words of a whole section, as one sequence each, in reading order. Words are the
letters and digits between spaces and punctuation, lower-cased, with the ligatures and
compatibility forms of NFKC folded and curly quotes made straight, so a typographic quote or
a dash does not count as an error and a wrong letter does.

What is left out of the page reader's words before they are compared, because the answer key
does not have it either: the running footer every printed page carries (`RUNNING_FOOTER`:
"Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 3 of 14"); the
instruction to the reader is to leave running footers out, and a reader that writes one
anyway is not penalised for it here (it is a different failure: the review screen shows it).

The distance is the exact word-level Levenshtein distance, by Myers' bit-parallel algorithm
(Hyyro 2003) over Python integers: a section of 5,000 words against 5,000 takes a fraction of
a second where the plain table takes tens.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser

from readablestem.core.types import INLINE_MARKER_RE, DocumentResult, Kind

_WORD = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*", re.UNICODE)
RUNNING_FOOTER = re.compile(
    r"Access for free at openstax\.org\.[^\n]*?\(OpenStax\),[^\n]*?Page\s*\d+\s*of\s*\d+",
    re.IGNORECASE,
)
_SKIP_TAGS = {"math", "script", "style", "svg", "head", "title"}
_VOID = {"br", "hr", "img", "meta", "link", "input"}


def words(text: str) -> list[str]:
    """The words of `text` as compared (see the module docstring)."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return [w.lower() for w in _WORD.findall(text)]


class _Prose(HTMLParser):
    """The text of an HTML document outside `math`, `script`, `style`, `svg` and `head`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS and tag not in _VOID:
            self._skip += 1
        elif not self._skip and tag in {"p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif not self._skip and tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def gold_words(printable_html: str) -> list[str]:
    """The words of a printable page, mathematics out (the answer key)."""
    parser = _Prose()
    parser.feed(printable_html)
    parser.close()
    return words(" ".join(parser.parts))


def predicted_text(result: DocumentResult) -> str:
    """The prose the document says, in reading order: headings, paragraphs and list items
    with each inline formula taken out, figure and table captions, and the cells of tables
    (those of a fix's grid when it has one), but not alt text (it is a description, not a
    printed word), and not the running footer."""
    fixes = {f.element_id: f for f in result.fixes}
    parts: list[str] = []
    for element in sorted(result.elements, key=lambda e: e.order):
        if element.kind in (Kind.HEADING, Kind.PARAGRAPH, Kind.LIST_ITEM):
            fix = fixes.get(element.id)
            if element.kind is Kind.HEADING and fix is not None and fix.heading is not None:
                text = fix.heading.text
            else:
                text = element.text or ""
            parts.append(INLINE_MARKER_RE.sub(" ", text))
        elif element.kind is Kind.TABLE:
            fix = fixes.get(element.id)
            grid = fix.table.grid if fix is not None and fix.table is not None else element.table
            if grid is not None:
                parts.append(" ".join(c.text for c in grid.cells if not c.latex))
            if element.caption:
                parts.append(element.caption)
        elif element.kind is Kind.FIGURE and element.caption:
            parts.append(element.caption)
    return RUNNING_FOOTER.sub(" ", "\n".join(parts))


def predicted_words(result: DocumentResult) -> list[str]:
    return words(predicted_text(result))


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Word-level Levenshtein distance between two sequences (Myers/Hyyro, exact)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    m = len(a)
    mask = (1 << m) - 1
    peq: dict[str, int] = {}
    for i, word in enumerate(a):
        peq[word] = peq.get(word, 0) | (1 << i)
    vp, vn, score = mask, 0, m
    last = 1 << (m - 1)
    for word in b:
        eq = peq.get(word, 0)
        xv = eq | vn
        xh = (((eq & vp) + vp) ^ vp) | eq
        hp = vn | (~(xh | vp) & mask)
        hn = vp & xh
        if hp & last:
            score += 1
        elif hn & last:
            score -= 1
        hp = ((hp << 1) | 1) & mask
        hn = (hn << 1) & mask
        vp = hn | (~(xv | hp) & mask)
        vn = hp & xv
    return score


@dataclass(frozen=True)
class Wer:
    gold_words: int
    predicted_words: int
    errors: int

    @property
    def wer(self) -> float | None:
        return self.errors / self.gold_words if self.gold_words else None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "gold_words": self.gold_words,
            "predicted_words": self.predicted_words,
            "errors": self.errors,
            "wer": None if self.wer is None else round(self.wer, 5),
        }


def score(gold: Sequence[str], predicted: Sequence[str]) -> Wer:
    return Wer(len(gold), len(predicted), edit_distance(gold, predicted))


def pooled(items: Iterable[dict[str, float | int | None]]) -> dict[str, float | int | None]:
    """The word error rate over sections pooled (errors over gold words), not the mean of
    the sections' rates: a long section counts for its words."""
    rows = list(items)
    gold = sum(int(r["gold_words"] or 0) for r in rows)
    errors = sum(int(r["errors"] or 0) for r in rows)
    return {
        "sections": len(rows),
        "gold_words": gold,
        "predicted_words": sum(int(r["predicted_words"] or 0) for r in rows),
        "errors": errors,
        "wer": round(errors / gold, 5) if gold else None,
    }
