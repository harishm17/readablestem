"""Bold letters in running text: how many the layout makes maths, against the source.

Measures the bold-letter rule of `pipeline.layout` (on with `FEATURE_NOTATION_MEMORY`;
docs/layout.md "Bold letters in prose") on documents whose source says which letters in
the text are bold:

- the LaTeX fixtures in tests/fixtures/layout/latex/: a lone `$\\mathbf{x}$` (or `\\bm`,
  `\\boldsymbol`) in the running text is a bold letter;
- the OpenStax gold pages (printed to PDF by `eval/render.py`): a `<strong>x</strong>`
  (or `<b>`) of one letter in the page XHTML, outside headings and captions, that the
  text does not continue as a formula (`<sub>`/`<sup>` right after it, or a relation
  or operator: those are found as inline maths by the older rules).

The unit is one bold letter. Expected and found letters are matched by letter within a
document (a multiset), so `found` counts the one-letter inline equations the rule made,
with any evidence, and `true positives` the ones the source has. Offline: layout only,
no model calls. Content stays local: the report holds letters and counts.

    uv run python -m readablestem.eval.bold_letters [--openstax] [--notation "bold d" ...]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from readablestem.core.mathchars import base_char, styled_char
from readablestem.core.notation import canonical_symbol
from readablestem.core.types import ConversionOptions, Kind, NotationEntry

REPO = Path(__file__).resolve().parents[3]
LATEX_FIXTURES = REPO / "tests" / "fixtures" / "layout" / "latex"
# The two-column handout of MF2 (UX review): 10 bold vectors in its prose, and a bold
# omega that layout already found as maths.
HANDOUT = "linear_algebra_twocol"

_LATEX_LONE = re.compile(r"\$\s*\\(?:mathbf|bm|boldsymbol)\s*\{\s*(\\?[A-Za-z]+)\s*\}\s*\$")
_STRONG = re.compile(r"<(strong|b)\b[^>]*>\s*([A-Za-z\u0391-\u03c9])\s*</\1>(\s*)(<\w+|[^<]{0,3})")
_SKIPPED_PARENTS = re.compile(
    r"<(h[1-6]|figcaption|caption|title)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_FORMULA_AFTER = re.compile(r"^\s*(?:<su[bp]|[=<>+\-\u2212\u00d7\u00b7/^_])")


@dataclass
class Score:
    """One document: bold letters by notation key (styled letter)."""

    name: str
    expected: Counter[str] = field(default_factory=Counter)
    found: Counter[str] = field(default_factory=Counter)
    evidence: Counter[str] = field(default_factory=Counter)  # found letters by evidence

    @property
    def true_positives(self) -> int:
        return sum((self.expected & self.found).values())

    @property
    def missed(self) -> int:
        return self.expected.total() - self.true_positives

    @property
    def false_positives(self) -> int:
        return self.found.total() - self.true_positives

    @property
    def missed_letters(self) -> list[str]:
        return sorted((self.expected - self.found).elements())

    @property
    def extra_letters(self) -> list[str]:
        return sorted((self.found - self.expected).elements())


def _bold_key(letter: str) -> str:
    """ "a" -> bold a, "omega" or "\\omega" -> bold omega (notation keys)."""
    key = canonical_symbol(f"bold {letter.lstrip(chr(92))}")
    return key if key is not None else styled_char(letter, "bold")


def expected_from_latex(tex: str) -> Counter[str]:
    """Lone bold letters in the running text of a LaTeX source (inline `$...$` only)."""
    body = tex.split(r"\begin{document}", 1)[-1]
    body = re.sub(r"(?<!\\)%.*", "", body)  # comments
    body = re.sub(r"\\\[.*?\\\]", " ", body, flags=re.DOTALL)  # display maths
    body = re.sub(r"\\begin\{(equation|align)\*?\}.*?\\end\{\1\*?\}", " ", body, flags=re.DOTALL)
    return Counter(_bold_key(m.group(1)) for m in _LATEX_LONE.finditer(body))


def expected_from_xhtml(xhtml: str) -> Counter[str]:
    """One-letter `<strong>`/`<b>` in an OpenStax page, outside headings and captions,
    not continued as a formula in the text (see the module docstring)."""
    text = _SKIPPED_PARENTS.sub(" ", xhtml)
    text = re.sub(r"<math\b.*?</math>", " ", text, flags=re.DOTALL)
    out: Counter[str] = Counter()
    for match in _STRONG.finditer(text):
        if _FORMULA_AFTER.match(match.group(3) + match.group(4)):
            continue
        out[_bold_key(match.group(2))] += 1
    return out


def found_in(
    pdf: bytes, notation: Sequence[NotationEntry] = ()
) -> tuple[Counter[str], Counter[str]]:
    """(letters, evidence) of the one-letter inline equations layout makes with the
    feature on, by notation key and by the evidence reason after BOLD_LETTER_REASON."""
    from readablestem.pipeline import layout, options
    from readablestem.pipeline.notation_memory import BOLD_LETTER_REASON

    with options.using(ConversionOptions(notation_memory=True, notation=list(notation))):
        elements = layout.analyze(pdf).elements
    letters: Counter[str] = Counter()
    evidence: Counter[str] = Counter()
    for element in elements:
        if element.kind is Kind.EQUATION and BOLD_LETTER_REASON in element.reasons:
            text = (element.text or "").strip()
            key = canonical_symbol(f"bold {base_char(text)}") or text
            letters[key] += 1
            evidence[element.reasons[-1]] += 1
    return letters, evidence


def score(
    name: str, pdf: bytes, expected: Counter[str], notation: Sequence[NotationEntry] = ()
) -> Score:
    found, evidence = found_in(pdf, notation)
    return Score(name, expected=expected, found=found, evidence=evidence)


def latex_fixtures(
    root: Path = LATEX_FIXTURES, notation: Sequence[NotationEntry] = ()
) -> list[Score]:
    """Every fixture with a .tex source and its PDF."""
    scores = []
    for tex in sorted(root.glob("*.tex")):
        pdf = tex.with_suffix(".pdf")
        if pdf.is_file():
            expected = expected_from_latex(tex.read_text(encoding="utf-8"))
            scores.append(score(tex.stem, pdf.read_bytes(), expected, notation))
    return scores


def openstax_pages(
    gold_dir: Path, cache: Path, notation: Sequence[NotationEntry] = ()
) -> list[Score]:
    """The gold pages, printed to PDF (cached under `cache`/pages; printing a page that
    is not cached needs Chromium) and scored against their XHTML."""
    from readablestem.eval.pipeline_eval import load_gold
    from readablestem.eval.render import book_authors, page_source_from_records, render_page_pdf
    from readablestem.gold.openstax import HttpFetcher

    fetcher = HttpFetcher(cache_dir=REPO / ".cache" / "openstax")
    pages: dict[tuple[str, str], list[dict]] = {}
    for record in load_gold(gold_dir):
        pages.setdefault((record["book_slug"], record["page_slug"]), []).append(record)
    scores = []
    for (book, slug), records in sorted(pages.items()):
        source = page_source_from_records(records, fetcher, authors=book_authors(gold_dir, book))
        pdf = render_page_pdf(source, cache_dir=cache / "pages")
        scores.append(score(slug, pdf, expected_from_xhtml(source.xhtml), notation))
    return scores


def table(scores: Iterable[Score]) -> str:
    """The scores as a Markdown table, with a total row."""
    rows = list(scores)
    lines = [
        "| Document | Bold letters | Found | Missed | False positives | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for s in rows:
        evidence = ", ".join(f"{k}: {v}" for k, v in sorted(s.evidence.items())) or "none"
        missed = f"{s.missed} ({' '.join(s.missed_letters)})" if s.missed else "0"
        extra = f"{s.false_positives} ({' '.join(s.extra_letters)})" if s.false_positives else "0"
        lines.append(
            f"| {s.name} | {s.expected.total()} | {s.true_positives} | {missed} | {extra} "
            f"| {evidence} |"
        )
    expected = sum(s.expected.total() for s in rows)
    tp = sum(s.true_positives for s in rows)
    found = sum(s.found.total() for s in rows)
    recall = f"{tp / expected:.3f}" if expected else "n/a"
    precision = f"{tp / found:.3f}" if found else "n/a"
    lines.append(
        f"| **total** | {expected} | {tp} | {expected - tp} | {found - tp} "
        f"| recall {recall}, precision {precision} |"
    )
    return "\n".join(lines)


def _entries(symbols: Sequence[str]) -> list[NotationEntry]:
    out = []
    for symbol in symbols:
        key = canonical_symbol(symbol)
        if key is None:
            raise SystemExit(f"not a symbol this tool understands: {symbol!r}")
        out.append(NotationEntry(symbol=symbol, canonical=key, meaning="vector"))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m readablestem.eval.bold_letters")
    parser.add_argument("--openstax", action="store_true", help="also score the gold pages")
    parser.add_argument("--gold", type=Path, default=REPO / "data" / "gold")
    parser.add_argument("--cache", type=Path, default=REPO / ".cache" / "eval")
    parser.add_argument("--notation", nargs="*", default=[], help='course symbols: "bold d"')
    args = parser.parse_args(argv)
    notation = _entries(args.notation)
    print("LaTeX fixtures\n")
    print(table(latex_fixtures(notation=notation)))
    if args.openstax:
        print("\nOpenStax gold pages\n")
        print(table(openstax_pages(args.gold, args.cache, notation)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
