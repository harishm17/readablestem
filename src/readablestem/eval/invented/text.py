"""The source's side of the invented-content rules: the PDF's text layer (R) and the
text normalisations the rules share.

R is the text layer of the PDF, every page, with the running footer and the attribution
block the print adds (they are in the file, and a converted copy of them is supported).
It is read once into the forms the rules need: normalised words, word 2- and 4-grams,
the numbers it prints, its lines, and its stream of symbols (letters, digits, Greek
letters in reading order, the way `equation_step` bags the text layer of an equation).

Every normalisation here repeats one the pre-registration (section 7.5) names:
`norm_words` is the earlier chatbot analysis's (NFKC, ligatures and quotes
folded, lower case, runs of `[a-z0-9]`); numbers are read by `figure_details.numbers_in`
(minus variants, thousands separators, 0.50 = 0.5, a superscript run read apart from its
base); symbols are `equation_step`'s. A test checks that the symbol stream and the bag the
pipeline uses agree.
"""

from __future__ import annotations

import difflib
import hashlib
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property

import numpy as np

from readablestem.pipeline import figure_details

# A private-use character that stands where a maths span was cut out of a text. It splits
# a sentence into segments, so no word n-gram crosses a gap.
MATH = chr(0xE000)

SENTENCE_MIN_WORDS = 7
SENTENCE_GRAM = 4
SENTENCE_SUPPORT_MIN = 0.25  # share of a sentence's 4-grams that must be in R
HEADING_RATIO = 0.85
HEADING_WINDOW_LINES = 3
CELL_MIN_WORDS = 3
CELL_GRAM = 2
CELL_SUPPORT_MIN = 0.5
UNALIGNED_MIN_SYMBOLS = 3
UNALIGNED_RATIO = 0.8

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORDS = re.compile(r"[a-z0-9]+")
# Mathematical digits (bold, double-struck, ...) are digits; NFKC would also fold a
# superscript run into its base, which `numbers_in` must see first.
_MATH_DIGITS = {cp: ord("0") + (cp - 0x1D7CE) % 10 for cp in range(0x1D7CE, 0x1D800)}


def norm_words(text: str) -> list[str]:
    """NFKC, ligatures and quotes folded, lower case, runs of [a-z0-9] (as the earlier
    chatbot analysis does)."""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.replace("\N{LATIN SMALL LIGATURE FI}", "fi")
    folded = folded.replace("\N{LATIN SMALL LIGATURE FF}", "ff")
    folded = folded.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    folded = folded.replace("\N{LEFT SINGLE QUOTATION MARK}", "'").lower()
    return _WORDS.findall(folded)


def ngrams(words: Sequence[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def split_sentences(text: str) -> list[str]:
    """Split after ".", "!" or "?" followed by white space (the earlier rule's split)."""
    return [part for part in _SENTENCE_END.split(text) if part.strip()]


def segments(text: str) -> list[list[str]]:
    """The words of `text`, one list for each stretch between maths spans."""
    return [norm_words(part) for part in text.split(MATH)]


def numbers_of(text: str) -> list[Decimal]:
    """Every number printed in `text`, as `figure_details.numbers_in` reads them."""
    return figure_details.numbers_in(text.translate(_MATH_DIGITS).replace(MATH, " "))


def heading_key(text: str) -> str:
    """Heading text for matching: NFKC, case-folded, letters and digits and single spaces
    (the key the headings eval uses)."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(kept.split())


# ------------------------------------------------------------------ symbols


def symbol_stream(text: str) -> str:
    """Letters, digits and Greek letters of `text` in order: the same characters
    `equation_step`'s symbol bag counts (NFKC, dotless i and j, case-folded, accents
    dropped), kept in reading order."""
    from readablestem.pipeline.equation_step import _DOTLESS

    folded = unicodedata.normalize("NFKC", text).translate(_DOTLESS).casefold()
    return "".join(ch for ch in folded if ch.isalnum() and unicodedata.category(ch) != "Lm")


def symbol_bag(text: str) -> Counter[str]:
    """`equation_step`'s own bag (letters, digits, Greek, case-folded), the text-layer
    check this module reuses."""
    from readablestem.pipeline.equation_step import _symbol_bag

    return _symbol_bag(text)


class SymbolIndex:
    """Does a symbol sequence occur in R's stream, up to a few wrong symbols?

    A window of the same length as the sequence "matches" when its difflib ratio with
    the sequence is at least `UNALIGNED_RATIO`. Windows are found with prefix sums per
    symbol: a window can reach ratio r only if its bag of symbols overlaps the
    sequence's bag in at least ceil(r * length) symbols, which numpy tests for every
    window at once; only those windows are compared with difflib.
    """

    def __init__(self, stream: str) -> None:
        self.stream = stream
        self._codes = np.fromiter((ord(ch) for ch in stream), dtype=np.int32, count=len(stream))
        self._prefix: dict[str, np.ndarray] = {}

    def _counts(self, symbol: str) -> np.ndarray:
        cached = self._prefix.get(symbol)
        if cached is None:
            cached = np.concatenate(
                ([0], np.cumsum(self._codes == ord(symbol), dtype=np.int32))
            ).astype(np.int32)
            self._prefix[symbol] = cached
        return cached

    def best_ratio(self, sequence: str, *, at_least: float = UNALIGNED_RATIO) -> float:
        """The best difflib ratio of `sequence` against a same-length window, or 0.0
        when no window can reach `at_least`. Stops at the first window that does."""
        length = len(sequence)
        if length == 0 or length > len(self.stream):
            return 0.0
        need = math.ceil(at_least * length - 1e-9)
        wanted = Counter(sequence)
        overlap = np.zeros(len(self.stream) - length + 1, dtype=np.int32)
        for symbol, count in wanted.items():
            prefix = self._counts(symbol)
            overlap += np.minimum(prefix[length:] - prefix[:-length], count)
        candidates = np.flatnonzero(overlap >= need)
        if candidates.size == 0:
            return 0.0
        best = 0.0
        order = candidates[np.argsort(-overlap[candidates], kind="stable")]
        for start in order:
            window = self.stream[int(start) : int(start) + length]
            ratio = difflib.SequenceMatcher(None, sequence, window, autojunk=False).ratio()
            best = max(best, ratio)
            if ratio >= at_least:
                return ratio
        return best

    def supports(self, sequence: str) -> bool:
        return self.best_ratio(sequence) >= UNALIGNED_RATIO


# ------------------------------------------------------------------ the reference


@dataclass(frozen=True)
class Reference:
    """The PDF's text layer in the forms the rules read it. Build it with `from_pdf`
    (PyMuPDF words, every page) or `from_pages` (the text of each page, one line to a
    row)."""

    pages: int
    text: str
    words: tuple[str, ...]
    grams2: frozenset[tuple[str, ...]]
    grams4: frozenset[tuple[str, ...]]
    numbers: frozenset[Decimal]
    line_keys: tuple[str, ...]
    symbols: str
    sha256: str

    @classmethod
    def from_pages(cls, pages: Sequence[str], *, sha256: str | None = None) -> Reference:
        text = "\f".join(pages)
        words = norm_words(text)
        keys = tuple(
            key for page in pages for line in page.splitlines() if (key := heading_key(line))
        )
        digest = sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(
            pages=len(pages),
            text=text,
            words=tuple(words),
            grams2=frozenset(ngrams(words, CELL_GRAM)),
            grams4=frozenset(ngrams(words, SENTENCE_GRAM)),
            numbers=frozenset(numbers_of(text)),
            line_keys=keys,
            symbols=symbol_stream(text),
            sha256=digest,
        )

    @classmethod
    def from_pdf(cls, data: bytes) -> Reference:
        """Read the text layer with PyMuPDF: the words of each page in the order the file
        gives them, one text-layer line to a row (`block`, `line` of each word)."""
        import pymupdf

        pages: list[str] = []
        with pymupdf.open(stream=data, filetype="pdf") as document:
            for page in document:
                rows: dict[tuple[int, int], list[str]] = {}
                for word in page.get_text("words", sort=False):
                    rows.setdefault((word[5], word[6]), []).append(word[4])
                pages.append("\n".join(" ".join(row) for row in rows.values()))
        return cls.from_pages(pages, sha256=hashlib.sha256(data).hexdigest())

    @cached_property
    def symbol_index(self) -> SymbolIndex:
        return SymbolIndex(self.symbols)

    @cached_property
    def heading_windows(self) -> tuple[str, ...]:
        """Every run of up to `HEADING_WINDOW_LINES` consecutive text-layer lines, joined
        by a space (a heading printed over two lines is one line of the document)."""
        keys = self.line_keys
        windows: list[str] = []
        for size in range(1, HEADING_WINDOW_LINES + 1):
            windows += [" ".join(keys[i : i + size]) for i in range(len(keys) - size + 1)]
        return tuple(dict.fromkeys(windows))

    def heading_ratio(self, heading: str, *, at_least: float = HEADING_RATIO) -> float:
        """The best difflib ratio of a heading's key against a window of R's lines; it
        stops at the first window that reaches `at_least`."""
        key = heading_key(heading)
        if not key:
            return 0.0
        best = 0.0
        for window in self.heading_windows:
            # real_quick_ratio and quick_ratio are upper bounds of ratio: cheap rejections.
            if 2 * min(len(key), len(window)) / (len(key) + len(window)) < at_least:
                continue
            matcher = difflib.SequenceMatcher(None, key, window, autojunk=False)
            if matcher.quick_ratio() < at_least:
                continue
            ratio = matcher.ratio()
            best = max(best, ratio)
            if ratio >= at_least:
                return ratio
        return best


def overlap(grams: Iterable[tuple[str, ...]], reference: frozenset[tuple[str, ...]]) -> float:
    """Share of `grams` found in `reference` (1.0 for none: nothing to contradict)."""
    items = set(grams)
    if not items:
        return 1.0
    return len(items & reference) / len(items)
