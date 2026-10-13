"""Symbol sequences: letters, digits and Greek letters of a piece of maths, in order.

Used by two measures of PREREG section 7.5 and PARSERS-PLAN section 4.4(d):
- "equation content kept in any form": is a gold equation's symbol sequence found somewhere in
  the tool's output text, as a window of the same length with difflib ratio >= 0.8?
- the unaligned-equation invented-content rule, which asks the same question of an output
  equation against the PDF's text layer.

Symbols are the same ones `equation_step` counts: NFKC (math italic and bold letters become
ASCII), case-folded, only letters and digits (Greek letters are letters); the dotless i and j
a TeX accent sits on, and U+2206 INCREMENT, are mapped to i, j and Greek capital Delta.
"""

from __future__ import annotations

import difflib
import unicodedata
from collections import Counter, defaultdict

MIN_SYMBOLS = 3
RATIO = 0.8
_MAX_WINDOWS = 60
_FIXES = str.maketrans({chr(0x131): "i", chr(0x237): "j", chr(0x2206): chr(0x394)})


def symbol_sequence(text: str | None) -> str:
    """The symbols of `text`, in order, as one string."""
    folded = unicodedata.normalize("NFKC", (text or "").translate(_FIXES)).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def symbol_bag(text: str | None) -> Counter[str]:
    return Counter(symbol_sequence(text))


class SymbolStream:
    """An output's symbols in reading order, indexed by character pairs for quick lookup."""

    def __init__(self, sequence: str) -> None:
        self.sequence = sequence
        self._pairs: dict[str, list[int]] = defaultdict(list)
        for i in range(len(sequence) - 1):
            self._pairs[sequence[i : i + 2]].append(i)

    def contains(self, needle: str) -> bool:
        """Is there a window of len(needle) symbols whose difflib ratio to `needle` is >= 0.8?

        Candidate windows start where one of the needle's character pairs occurs; the best
        supported starts are tried first. A needle shorter than three symbols is not checked
        (the caller reports it as too short).
        """
        n = len(needle)
        if n < MIN_SYMBOLS or len(self.sequence) < 1:
            return False
        votes: Counter[int] = Counter()
        for k in range(n - 1):
            for position in self._pairs.get(needle[k : k + 2], ()):
                votes[max(position - k, 0)] += 1
        for start, _ in votes.most_common(_MAX_WINDOWS):
            window = self.sequence[start : start + n]
            if difflib.SequenceMatcher(None, needle, window, autojunk=False).ratio() >= RATIO:
                return True
        return False
