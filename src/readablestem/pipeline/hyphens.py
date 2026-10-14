"""Hyphens at the end of a line: a break inside a word, or part of a compound?

A paragraph set in narrow columns breaks "velocity" as "veloc-" / "ity", and "well-known"
as "well-" / "known". Joining the lines without a hyphen is right for the first and wrong
for the second ("wellknown" reads as nonsense, and a screen reader says so). Deciding
needs to know what is a word; `data/words-en.txt.gz` (general English, see
`data/README.md` for its source and licence) says that.

The rule, for a lower-case continuation (a capital after the hyphen keeps it, as in
"Newton-" / "Raphson"):

1. the hyphen follows a single letter ("x-" / "ray", "T-" / "shirt"): kept. No line is
   broken after one letter, so it is part of a compound;
2. the two pieces joined are a word ("veloc" + "ity", "semi" + "conductor", "non" +
   "linear"): the hyphen goes;
3. both pieces are words and their join is not ("well" + "known", "cross" + "section",
   "self" + "consistent"): the hyphen stays, a compound;
4. anything else ("eigen" + "vector": a technical word the list lacks, neither piece a
   word): the hyphen goes, as it did before the list existed.

A piece is the run of letters next to the hyphen: "mass-energy-" / "balance" tests
"energy" and "balance".
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from importlib import resources

HYPHENS = "-\u2010"
MIN_PIECE = 2  # the word list holds words of two letters or more


@lru_cache(maxsize=1)
def words() -> frozenset[str]:
    """The lower-case English words the pipeline ships (empty if the file is missing, so a
    broken install joins as it always did rather than failing a conversion)."""
    try:
        data = (
            resources.files("readablestem.pipeline").joinpath("data/words-en.txt.gz").read_bytes()
        )
        return frozenset(gzip.decompress(data).decode("utf-8").split())
    except (OSError, ValueError):
        return frozenset()


def _tail_letters(text: str) -> str:
    end = len(text)
    start = end
    while start > 0 and text[start - 1].isalpha():
        start -= 1
    return text[start:end]


def _head_letters(text: str) -> str:
    end = 0
    while end < len(text) and text[end].isalpha():
        end += 1
    return text[:end]


def keeps_hyphen(before: str, after: str) -> bool:
    """Is the hyphen that ends `before` (it is its last character) part of a compound?

    `after` is the next line and starts with a lower-case letter; the caller has decided
    that already. See the module docstring for the rules."""
    stem = _tail_letters(before[:-1])
    head = _head_letters(after)
    if len(stem) == 1:
        return True
    vocabulary = words()
    if not vocabulary or len(stem) < MIN_PIECE or len(head) < MIN_PIECE:
        return False
    if (stem + head).lower() in vocabulary:
        return False
    return stem.lower() in vocabulary and head.lower() in vocabulary
