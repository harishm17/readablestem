"""The invented-content rules: which units of a system's output the PDF does not support.

The rules are the table in section 7.5 of the pre-registration, one function each. A unit
is unsupported when:

- sentence (7 or more words, in prose outside maths, headings, tables and descriptions):
  fewer than 25% of its word 4-grams are in R;
- number (each one in prose and headings, outside maths): it is not among R's numbers, read
  as `figure_details.numbers_in` reads them (minus variants, thousands separators);
- heading: no line of R matches it, difflib ratio at least 0.85;
- equation aligned to a gold equation: it holds a letter, digit or Greek letter more often
  than the gold equation does (an added symbol);
- equation with no gold partner and 3 or more symbols: no window of R's symbol stream of the
  same length matches its symbols, difflib ratio at least 0.8;
- table cell: a number in it is not among R's numbers, or it has 3 or more words and fewer
  than half of its word bigrams are in R;
- figure description: the image-aware judge sets `factual_error` (`figures.py`, sampled).

R is the PDF's text layer (`text.Reference`). Readings this module makes where the table
leaves room (all listed in docs/eval-invented.md):

- Maths is cut out of a sentence and the sentence's 4-grams are taken within the stretches
  between maths spans, so no 4-gram spans a gap; a sentence with no 4-gram inside a stretch
  falls back to the 4-grams of its words run together.
- A heading is compared with every run of up to three consecutive lines of R (a heading
  printed over two lines is one line of the document); its maths counts as its tokens.
- Numbers are read from every prose block and heading, not only from sentences of 7 words.
- Without gold equations every equation is "unaligned". With them, alignment is the
  document task's (weighted LCS, MathML signature only, threshold 0.5, reading order).
- Text a11y-stem wrote itself (class `a11y-stem`) is scored like the rest and counted
  apart: every count has an `authored` part, so a rate with and without it is one subtraction.

Nothing here calls a model. The rules cannot see a changed word in a sentence that keeps a
quarter of its 4-grams, a changed sign in an equation with no gold partner, or a correct
paraphrase (it counts as unsupported). That is measured, not assumed: `audit.py`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.equations import MathMLError, normalize
from a11y_stem.eval.invented.read import Output
from a11y_stem.eval.invented.text import (
    CELL_GRAM,
    CELL_MIN_WORDS,
    CELL_SUPPORT_MIN,
    HEADING_RATIO,
    SENTENCE_GRAM,
    SENTENCE_MIN_WORDS,
    SENTENCE_SUPPORT_MIN,
    UNALIGNED_MIN_SYMBOLS,
    UNALIGNED_RATIO,
    Reference,
    heading_key,
    ngrams,
    norm_words,
    numbers_of,
    overlap,
    segments,
    split_sentences,
    symbol_bag,
    symbol_stream,
)

SENTENCE = "sentence"
NUMBER = "number"
HEADING = "heading"
EQUATION_ALIGNED = "equation_aligned"
EQUATION_UNALIGNED = "equation_unaligned"
TABLE_CELL = "table_cell"
DESCRIPTION_CELL = "description_cell"
DESCRIPTION = "figure_description"
TYPES = (
    SENTENCE,
    NUMBER,
    HEADING,
    EQUATION_ALIGNED,
    EQUATION_UNALIGNED,
    TABLE_CELL,
    DESCRIPTION_CELL,
    DESCRIPTION,
)
SNIPPET_CHARS = 300


@dataclass
class Counts:
    """Units of one type. `authored` counts are the part of `n` and `unsupported` that is
    text a11y-stem wrote itself."""

    n: int = 0
    unsupported: int = 0
    n_authored: int = 0
    unsupported_authored: int = 0
    unchecked: int = 0  # the rule could not look at them (TeX that did not convert, ...)
    too_short: int = 0  # unaligned equations under 3 symbols
    # Of the unsupported aligned equations: those whose only added symbols are a printed
    # equation number at the end ("... = 1.7 m/s 2.1"; see `equation_tag`).
    tag_only: int = 0
    tag_only_authored: int = 0

    def add(self, unsupported: bool, authored: bool) -> None:
        self.n += 1
        self.unsupported += unsupported
        if authored:
            self.n_authored += 1
            self.unsupported_authored += unsupported

    def to_dict(self) -> dict[str, int]:
        return {
            "n": self.n,
            "unsupported": self.unsupported,
            "n_authored": self.n_authored,
            "unsupported_authored": self.unsupported_authored,
            "unchecked": self.unchecked,
            "too_short": self.too_short,
            "tag_only": self.tag_only,
            "tag_only_authored": self.tag_only_authored,
        }


@dataclass(frozen=True)
class Verdict:
    """One unit and what the rule made of it. `text` is capped; reports without units
    carry no text."""

    kind: str
    index: int
    text: str
    unsupported: bool
    authored: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "index": self.index,
            "text": self.text,
            "unsupported": self.unsupported,
            "authored": self.authored,
            **self.detail,
        }


@dataclass
class Score:
    reference: dict[str, Any]
    output: dict[str, Any]
    counts: dict[str, Counts]
    verdicts: list[Verdict]
    added_symbols: int = 0  # over aligned equations
    added_tag_symbols: int = 0  # of them, the symbols of a printed equation number
    aligned_symbols: int = 0  # output symbols of aligned equations
    aligned_pairs: int = 0
    descriptions_by_source: dict[str, int] = field(default_factory=dict)

    def to_dict(self, *, units: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "reference": self.reference,
            "output": self.output,
            "counts": {kind: self.counts[kind].to_dict() for kind in TYPES},
            "equation_symbols": {
                "added": self.added_symbols,
                "added_in_tags": self.added_tag_symbols,
                "aligned_output_symbols": self.aligned_symbols,
                "aligned_pairs": self.aligned_pairs,
            },
            "descriptions_by_source": self.descriptions_by_source,
            "per_page": {
                kind: _per_page(self.counts[kind].unsupported, self.reference["pages"])
                for kind in TYPES
            },
        }
        if units:
            data["units"] = [verdict.to_dict() for verdict in self.verdicts]
        return data


def _per_page(count: int, pages: int) -> float | None:
    return count / pages if pages else None


def _plain_number(number: Decimal) -> str:
    return format(number.normalize(), "f")


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS] + " [cut]"


# ------------------------------------------------------------------ prose


def sentence_coverage(sentence: str, reference: Reference) -> tuple[int, float]:
    """(words, share of the sentence's 4-grams in R) for one sentence of the output."""
    stretches = segments(sentence)
    words = [word for stretch in stretches for word in stretch]
    grams: set[tuple[str, ...]] = set()
    for stretch in stretches:
        grams |= ngrams(stretch, SENTENCE_GRAM)
    if not grams:
        grams = ngrams(words, SENTENCE_GRAM)
    return len(words), overlap(grams, reference.grams4)


def _score_sentences(
    reference: Reference, output: Output, counts: dict[str, Counts], verdicts: list[Verdict]
) -> None:
    index = 0
    for block in output.blocks:
        for sentence in split_sentences(block.text):
            words, coverage = sentence_coverage(sentence, reference)
            if words < SENTENCE_MIN_WORDS:
                continue
            unsupported = coverage < SENTENCE_SUPPORT_MIN
            counts[SENTENCE].add(unsupported, block.authored)
            verdicts.append(
                Verdict(
                    SENTENCE,
                    index,
                    _snippet(sentence.replace(chr(0xE000), "")),
                    unsupported,
                    block.authored,
                    {
                        "overlap4": round(coverage, 3),
                        "words": words,
                        "block_kind": block.kind,
                        "after_figure": block.after_figure,
                    },
                )
            )
            index += 1


def _score_numbers(
    reference: Reference, output: Output, counts: dict[str, Counts], verdicts: list[Verdict]
) -> None:
    index = 0
    sources = [(block.text, block.authored, "prose") for block in output.blocks]
    sources += [(heading.plain, heading.authored, "heading") for heading in output.headings]
    for text, authored, where in sources:
        for number in numbers_of(text):
            unsupported = number not in reference.numbers
            counts[NUMBER].add(unsupported, authored)
            verdicts.append(
                Verdict(
                    NUMBER,
                    index,
                    _plain_number(number),
                    unsupported,
                    authored,
                    {"in": where, "context": _snippet(text.replace(chr(0xE000), " "))[:120]},
                )
            )
            index += 1


def _score_headings(
    reference: Reference, output: Output, counts: dict[str, Counts], verdicts: list[Verdict]
) -> None:
    for index, heading in enumerate(output.headings):
        if not heading_key(heading.text):
            counts[HEADING].unchecked += 1
            continue
        ratio = reference.heading_ratio(heading.text)
        unsupported = ratio < HEADING_RATIO
        counts[HEADING].add(unsupported, heading.authored)
        verdicts.append(
            Verdict(
                HEADING,
                index,
                _snippet(heading.text),
                unsupported,
                heading.authored,
                {"ratio": round(ratio, 3), "level": heading.level},
            )
        )


# ------------------------------------------------------------------ tables


def _score_cells(
    reference: Reference, output: Output, counts: dict[str, Counts], verdicts: list[Verdict]
) -> None:
    for index, cell in enumerate(output.cells):
        kind = DESCRIPTION_CELL if cell.in_description else TABLE_CELL
        numbers = numbers_of(cell.text)
        missing = [_plain_number(n) for n in numbers if n not in reference.numbers]
        words = norm_words(cell.text)
        bigram_share = None
        weak_words = False
        if len(words) >= CELL_MIN_WORDS:
            bigram_share = overlap(ngrams(words, CELL_GRAM), reference.grams2)
            weak_words = bigram_share < CELL_SUPPORT_MIN
        unsupported = bool(missing) or weak_words
        counts[kind].add(unsupported, cell.authored)
        verdicts.append(
            Verdict(
                kind,
                index,
                _snippet(cell.text),
                unsupported,
                cell.authored,
                {
                    "table": cell.table,
                    "row": cell.row,
                    "col": cell.col,
                    "numbers_missing": missing,
                    "bigram_share": None if bigram_share is None else round(bigram_share, 3),
                },
            )
        )


# ------------------------------------------------------------------ equations


def gold_equation_mathml(
    gold_dir: Path, pages: Sequence[str] | None = None, *, book: str | None = None
) -> list[str]:
    """The MathML of the gold equations in reading order. `pages` lists the page slugs of
    a PDF made of several sections, in the order they were printed; omitted, every
    equation of the gold directory in file order. `book` limits them to one book (slugs
    such as `3-1-...` can repeat across books)."""
    records = [
        r
        for r in pe.load_gold(gold_dir)
        if r.get("kind") == "equation" and (book is None or r.get("book_slug") == book)
    ]
    if pages is None:
        return [r["mathml"] for r in records]
    by_page: dict[str, list[dict]] = {}
    for record in records:
        by_page.setdefault(record["page_slug"], []).append(record)
    missing = [slug for slug in pages if slug not in by_page]
    if missing:
        raise ValueError(f"no gold equations for page(s) {', '.join(missing)}")
    ordered = []
    for slug in pages:
        ordered += sorted(by_page[slug], key=lambda r: r["index_on_page"])
    return [r["mathml"] for r in ordered]


_TAG = re.compile(r"(?:^|\s)(?:\(\s*(\d+(?:\.\d+)*)\s*\)|(\d+\.[1-9]\d*))\s*$")


def equation_tag(tokens: str, added: Counter[str], reference: Reference) -> bool:
    """Are the symbols added to an aligned equation only its printed equation number?

    A system that keeps the number the print sets beside an equation ("(2.1)", "2.1")
    inside the equation holds symbols the gold equation does not. The number is in the PDF,
    so the pre-registered rule's flag is not an invention. This reading is reported next to
    the rule, never instead of it: the equation ends in `(n)`, `(n.m)` or `n.m`, the number
    is one R prints, and the added symbols are no more than that number's digits."""
    match = _TAG.search(tokens)
    if not match or not added:
        return False
    number = match.group(1) or match.group(2)
    if not any(n in reference.numbers for n in numbers_of(number)):
        return False
    return not (added - symbol_bag(number))


def _tokens_of(mathml: str) -> str | None:
    """The tokens of a MathML equation after the scorer's own normalisation (annotations,
    layout-only elements dropped; math letters folded); None when it will not parse."""
    try:
        root = normalize(mathml, lenient=True)
    except MathMLError:
        return None
    return " ".join(" ".join(root.itertext()).split())


def _score_equations(
    reference: Reference,
    output: Output,
    gold: Sequence[str] | None,
    counts: dict[str, Counts],
    verdicts: list[Verdict],
    score: Score,
) -> None:
    checkable: list[tuple[int, str, str]] = []  # (equation index, tokens, signature)
    for equation in output.equations:
        tokens = _tokens_of(equation.mathml) if equation.mathml else None
        if tokens is None:
            counts[EQUATION_UNALIGNED].unchecked += 1
            continue
        checkable.append((equation.index, tokens, pe.math_signature(equation.mathml)))
    pairs: dict[int, int] = {}  # position in `checkable` -> gold index
    gold_tokens: list[str | None] = []
    if gold:
        gold_tokens = [_tokens_of(g) for g in gold]
        gold_sigs = [pe.math_signature(g) for g in gold]
        aligned = pe.align(
            gold_sigs,
            [sig for _i, _t, sig in checkable],
            pe.similarity,
            min_similarity=pe.EQUATION_ALIGN_MIN,
        )
        pairs = {p: g for g, p, _s in aligned}
    for position, (index, tokens, _sig) in enumerate(checkable):
        equation = output.equations[index]
        stream = symbol_stream(tokens)
        if position in pairs and gold_tokens[pairs[position]] is not None:
            reference_tokens = gold_tokens[pairs[position]] or ""
            wanted = symbol_bag(reference_tokens)
            have = symbol_bag(tokens)
            added = have - wanted
            unsupported = bool(added)
            counts[EQUATION_ALIGNED].add(unsupported, equation.authored)
            tag_only = unsupported and equation_tag(tokens, added, reference)
            if tag_only:
                counts[EQUATION_ALIGNED].tag_only += 1
                counts[EQUATION_ALIGNED].tag_only_authored += equation.authored
                score.added_tag_symbols += sum(added.values())
            score.added_symbols += sum(added.values())
            score.aligned_symbols += sum(have.values())
            score.aligned_pairs += 1
            verdicts.append(
                Verdict(
                    EQUATION_ALIGNED,
                    index,
                    _snippet(tokens),
                    unsupported,
                    equation.authored,
                    {
                        "added": "".join(sorted(added.elements())),
                        "tag_only": tag_only,
                        "gold_index": pairs[position],
                        "source": equation.source,
                        "display": equation.display,
                    },
                )
            )
            continue
        if len(stream) < UNALIGNED_MIN_SYMBOLS:
            counts[EQUATION_UNALIGNED].too_short += 1
            continue
        ratio = reference.symbol_index.best_ratio(stream)
        unsupported = ratio < UNALIGNED_RATIO
        counts[EQUATION_UNALIGNED].add(unsupported, equation.authored)
        verdicts.append(
            Verdict(
                EQUATION_UNALIGNED,
                index,
                _snippet(tokens),
                unsupported,
                equation.authored,
                {
                    "ratio": round(ratio, 3),
                    "symbols": len(stream),
                    "source": equation.source,
                    "display": equation.display,
                },
            )
        )


# ------------------------------------------------------------------ the whole output


def score_output(
    reference: Reference,
    output: Output,
    *,
    gold_equations: Sequence[str] | None = None,
) -> Score:
    """Apply every deterministic rule to one output. Figure descriptions are only counted
    here (the judge decides them, `figures.py`)."""
    counts = {kind: Counts() for kind in TYPES}
    verdicts: list[Verdict] = []
    score = Score(
        reference={
            "sha256": reference.sha256,
            "pages": reference.pages,
            "words": len(reference.words),
            "numbers": len(reference.numbers),
            "lines": len(reference.line_keys),
            "symbols": len(reference.symbols),
        },
        output={"format": output.format, "sha256": output.sha256, **output.counts()},
        counts=counts,
        verdicts=verdicts,
    )
    _score_sentences(reference, output, counts, verdicts)
    _score_numbers(reference, output, counts, verdicts)
    _score_headings(reference, output, counts, verdicts)
    _score_cells(reference, output, counts, verdicts)
    _score_equations(reference, output, gold_equations, counts, verdicts, score)
    by_source = Counter(d.source for d in output.descriptions)
    score.descriptions_by_source = dict(by_source)
    for description in output.descriptions:
        counts[DESCRIPTION].n += 1
        counts[DESCRIPTION].n_authored += description.authored
    return score


def rule_summary() -> dict[str, Any]:
    """The thresholds the rules use, for a report to record."""
    return {
        "sentence_min_words": SENTENCE_MIN_WORDS,
        "sentence_gram": SENTENCE_GRAM,
        "sentence_support_min": SENTENCE_SUPPORT_MIN,
        "heading_ratio": HEADING_RATIO,
        "cell_min_words": CELL_MIN_WORDS,
        "cell_gram": CELL_GRAM,
        "cell_support_min": CELL_SUPPORT_MIN,
        "unaligned_min_symbols": UNALIGNED_MIN_SYMBOLS,
        "unaligned_ratio": UNALIGNED_RATIO,
        "equation_align_min": pe.EQUATION_ALIGN_MIN,
    }


__all__ = [
    "DESCRIPTION",
    "EQUATION_ALIGNED",
    "EQUATION_UNALIGNED",
    "HEADING",
    "NUMBER",
    "SENTENCE",
    "TABLE_CELL",
    "TYPES",
    "Counts",
    "Score",
    "Verdict",
    "gold_equation_mathml",
    "rule_summary",
    "score_output",
    "sentence_coverage",
]
