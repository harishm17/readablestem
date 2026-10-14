"""Invented content: output the source PDF does not support (PREREG section 7.5).

This is the harness's reading of the section 7.5 table for what a parser's output holds, so
that the adapters' output can be checked end to end. It is NOT the frozen invented-content
builder of the held-out run, which a separate task writes; where the two differ, that builder
and the PREREG table win. Rules implemented, with the table's wording:

| type | unit | unsupported when |
| prose sentence | a sentence of 7+ words in text outside maths, headings, tables and figure
  descriptions (split after . ! ? and a space) | fewer than 25% of its word 4-grams occur in R |
| number | each number in prose and headings, outside maths, read as `figure_details`
  reads numbers | it is not among the numbers of R read the same way |
| heading | each output heading | no line of R matches it, difflib ratio >= 0.85 |
| equation, aligned | an output equation aligned to a gold equation | its multiset of
  symbols holds a symbol more often than the gold equation does |
| equation, unaligned | an output equation with no gold partner, 3+ symbols | no window of
  R's symbol stream matches its symbol sequence, ratio >= 0.8 |

R is the PDF's own text layer, every page, normalised as `niche/chatbot-runs/scripts/common.py`
`norm_words` does (NFKC, ligatures and quotes folded, lower case, runs of [a-z0-9]).

Not implemented here: figure descriptions (need the image-aware judge) and table cells.
Text a tool read out of the inside of a picture (`figure_text`) is not prose; it is counted
apart, never inside the prose or number rates. Running headers and footers are `furniture`
and are not units.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf
from readablestem.eval import pipeline_eval as pe
from readablestem.pipeline.figure_details import numbers_in

from parsers_bench.intermediate import Intermediate
from parsers_bench.symbols import MIN_SYMBOLS, SymbolStream, symbol_bag, symbol_sequence

MIN_SENTENCE_WORDS = 7
SENTENCE_4GRAM_MIN = 0.25
HEADING_RATIO = 0.85
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PROSE_KINDS = ("para", "list", "caption")


def norm_words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.replace(chr(0xFB01), "fi").replace(chr(0xFB00), "ff")
    folded = folded.replace(chr(0x2019), "'").replace(chr(0x2018), "'").lower()
    return re.findall(r"[a-z0-9]+", folded)


def ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass
class Reference:
    """R: what the PDF itself prints."""

    four_grams: set[tuple[str, ...]]
    numbers: set[Decimal]
    lines: list[str]  # heading_signature of every text line
    symbols: SymbolStream
    words: int
    pages: int


def reference_from_pdf(path: Path) -> Reference:
    texts: list[str] = []
    with pymupdf.open(path) as document:
        pages = document.page_count
        for page in document:
            texts.append(page.get_text("text"))
    full = "\n".join(texts)
    words = norm_words(full)
    lines = [sig for line in full.splitlines() if (sig := pe.heading_signature(line))]
    return Reference(
        four_grams=ngrams(words, 4),
        numbers=set(numbers_in(full)),
        lines=lines,
        symbols=SymbolStream(symbol_sequence(full)),
        words=len(words),
        pages=pages,
    )


def rate(unsupported: int, n: int) -> dict[str, Any]:
    interval = pe.wilson_interval(unsupported, n)
    return {
        "n": n,
        "unsupported": unsupported,
        "rate": unsupported / n if n else None,
        "wilson95": list(interval) if interval else None,
    }


def prose_sentences(doc: Intermediate) -> list[str]:
    sentences = []
    for block in doc.blocks:
        if block.kind not in _PROSE_KINDS:
            continue
        for sentence in _SENTENCE_SPLIT.split(block.text):
            if len(norm_words(sentence)) >= MIN_SENTENCE_WORDS:
                sentences.append(sentence)
    return sentences


def _sentence_supported(sentence: str, ref: Reference) -> bool:
    grams = ngrams(norm_words(sentence), 4)
    return bool(grams) and len(grams & ref.four_grams) / len(grams) >= SENTENCE_4GRAM_MIN


def number_units(doc: Intermediate) -> list[Decimal]:
    texts = [b.text for b in doc.blocks if b.kind in _PROSE_KINDS]
    texts += [h.text for h in doc.headings]
    return [n for text in texts for n in numbers_in(text)]


def figure_text_numbers(doc: Intermediate) -> list[Decimal]:
    return [n for b in doc.blocks if b.kind == "figure_text" for n in numbers_in(b.text)]


def _heading_supported(text: str, ref: Reference) -> bool:
    signature = pe.heading_signature(text)
    return any(pe.similarity(signature, line) >= HEADING_RATIO for line in ref.lines)


def text_rates(doc: Intermediate, ref: Reference) -> dict[str, Any]:
    sentences = prose_sentences(doc)
    unsupported_sentences = sum(not _sentence_supported(s, ref) for s in sentences)
    numbers = number_units(doc)
    unsupported_numbers = sum(n not in ref.numbers for n in numbers)
    headings = [h.text for h in doc.headings]
    unsupported_headings = sum(not _heading_supported(h, ref) for h in headings)
    in_figures = figure_text_numbers(doc)
    pages = ref.pages or 1
    return {
        "prose_sentence": rate(unsupported_sentences, len(sentences)),
        "number": rate(unsupported_numbers, len(numbers)),
        "heading": rate(unsupported_headings, len(headings)),
        "figure_text_numbers_not_in_text_layer": {
            "n": len(in_figures),
            "not_in_text_layer": sum(n not in ref.numbers for n in in_figures),
            "note": "text read from inside pictures; the PDF's text layer cannot hold it, "
            "so it is reported apart and is not part of the number rate",
        },
        "per_pdf_page": {
            "unsupported_sentences": unsupported_sentences / pages,
            "unsupported_numbers": unsupported_numbers / pages,
        },
    }


def equation_rates(
    aligned_output: list[str], aligned_gold: list[str], unaligned: list[str], ref: Reference
) -> dict[str, Any]:
    """Aligned equations: has the output a symbol more often than its gold partner?
    Unaligned equations: does any window of R's symbols match? Inputs are MathML token texts
    (aligned pairs, in the same order) and symbol sequences (unaligned)."""
    added_pairs = 0
    added_symbols = 0
    for out, gold in zip(aligned_output, aligned_gold, strict=True):
        extra = symbol_bag(out) - symbol_bag(gold)
        added_pairs += bool(extra)
        added_symbols += sum(extra.values())
    total_output = sum(len(symbol_sequence(o)) for o in aligned_output)
    checkable = [s for s in unaligned if len(s) >= MIN_SYMBOLS]
    unsupported = sum(not ref.symbols.contains(s) for s in checkable)
    return {
        "equation_aligned": rate(added_pairs, len(aligned_output)),
        "added_symbols_per_1000_output_symbols": (
            1000 * added_symbols / total_output if total_output else None
        ),
        "equation_unaligned": rate(unsupported, len(checkable)),
        "equation_unaligned_too_short_to_check": len(unaligned) - len(checkable),
    }
