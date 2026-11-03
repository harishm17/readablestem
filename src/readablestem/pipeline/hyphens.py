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

The document can overrule the list, because its own spelling is better evidence than any
dictionary: when the same two pieces are written with a hyphen inside a line elsewhere
("eigen-vector"), the hyphen stays; when the joined word is written whole elsewhere
("antiderivative"), a break in the middle of it is soft hyphenation and the hyphen goes,
even for two pieces that are words with a join the list lacks. A single letter before the
hyphen (rule 1) is never overruled by the second.
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterable
from functools import lru_cache
from importlib import resources
from itertools import pairwise

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


_TOKEN = re.compile(r"[^\W\d_]+(?:[-\u2010][^\W\d_]+)*")


def document_spellings(lines: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """(hyphenated pairs, whole words) the lines write inside a line, all lower-case.

    "mass-energy-balance" gives the pairs "mass-energy" and "energy-balance"; a word with
    no hyphen is a whole word. A word cut at the end of a line is not counted (its second
    half is on the next line), so a break never vouches for itself."""
    pairs: set[str] = set()
    whole: set[str] = set()
    for line in lines:
        for token in _TOKEN.findall(line):
            parts = [part.lower() for part in re.split("[-\u2010]", token)]
            if len(parts) == 1:
                whole.add(parts[0])
            else:
                pairs.update(f"{a}-{b}" for a, b in pairwise(parts))
    return frozenset(pairs), frozenset(whole)


def keeps_hyphen(
    before: str,
    after: str,
    compounds: frozenset[str] = frozenset(),
    whole: frozenset[str] = frozenset(),
) -> bool:
    """Is the hyphen that ends `before` (it is its last character) part of a compound?

    `after` is the next line and starts with a lower-case letter; the caller has decided
    that already. `compounds` and `whole` are the document's own spellings
    (`document_spellings`). See the module docstring for the rules."""
    stem = _tail_letters(before[:-1])
    head = _head_letters(after)
    if stem and head and f"{stem}-{head}".lower() in compounds:
        return True
    if len(stem) == 1:
        return True
    if (stem + head).lower() in whole:
        return False
    vocabulary = words()
    if not vocabulary or len(stem) < MIN_PIECE or len(head) < MIN_PIECE:
        return False
    if (stem + head).lower() in vocabulary:
        return False
    return stem.lower() in vocabulary and head.lower() in vocabulary
