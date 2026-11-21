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

None of that applies when the line broke inside one token, a number or an address, where
there is no word to tell and a space would split it (`joins_inside_token`):

- a digit, a hyphen and a digit: "(2012-08-" / "17)" is one date, "5-" / "7 m" one range;
- a line that ends in "/", ".", "_", "-" or "=" inside an address ("project/" /
  "memspectrum/", "www.example." / "org"): the next line goes on from it;
- a non-breaking hyphen (U+2011) and its look-alikes after a letter or digit: they mark a
  compound that must not break, so what follows is its second half ("easy-" / "to-use").
"""

from __future__ import annotations

import gzip
import re
from collections.abc import Iterable
from functools import lru_cache
from importlib import resources
from itertools import pairwise

HYPHENS = "-\u2010"
# Hyphens that never break a word: the non-breaking hyphen (Word's Ctrl+Shift+hyphen), the small
# hyphen-minus and the fullwidth one. A break after one is a compound split over two lines,
# so the hyphen stays and no space follows. Not here: the en dash, the figure dash and the
# minus sign, which are punctuation or an operator when a line ends with them.
FIXED_HYPHENS = "\u2011\ufe63\uff0d"
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


_SCHEME = re.compile(r"[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_ADDRESS_END = "/._-="
_CUT_CHARS = 24  # how much of each side of a linked break is kept to know it again
_OPENERS = "([{<\"'\u201c\u2018"


def _address(token: str) -> bool:
    """A web address, an e-mail address or a path: it starts with a scheme or "www.", or
    holds "/" or "@". A lone slash ("a / b") is not one. "km/" / "s" and "and/" / "or" count
    as paths, and joining them is what a reader does."""
    body = token.lstrip(_OPENERS)
    if len(body) < 2 or not any(c.isalnum() for c in body[:-1]):
        return False
    return bool(
        _SCHEME.match(body) or body.lower().startswith("www.") or "/" in body or "@" in body
    )


def _host_unfinished(token: str) -> bool:
    """Does the address in `token` (it ends with a full stop) lack its top-level domain?

    A full stop ends a sentence as often as it ends a line inside an address, so it joins
    only a host that is still short of "com" or "org": "www.example." has a name and no
    suffix, "www.example.org." does not need one. Once there is a path the stop is too
    ambiguous to read, and the next line keeps its space."""
    body = token.lstrip(_OPENERS)
    scheme = _SCHEME.match(body)
    rest = body[scheme.end() :] if scheme else body
    if "/" in rest:
        return False
    if "@" in rest:
        rest = rest.rsplit("@", 1)[1]
    labels = rest.rstrip(".").split(".")
    if "@" not in body and labels[0].lower() == "www":
        labels = labels[1:]
    return len([label for label in labels if label]) <= 1


LinkCuts = frozenset[tuple[str, str]]


def link_cut(uri: str, first: str, second: str) -> tuple[str, str] | None:
    """What a printed address, linked on two lines, says about its break: (end of `first`,
    start of `second`), or None. `first` and `second` are the texts under two link
    annotations of the same `uri` on neighbouring lines. They are the address itself when
    the two, with their spaces taken out, run on one after the other in the `uri`; a named
    link ("Smith" over two lines) is not, and its break keeps its space."""
    a, b = "".join(first.split()), "".join(second.split())
    if not a or not b:
        return None
    shown = _SCHEME.sub("", a + b, count=1).removeprefix("mailto:").lower()
    if shown not in _SCHEME.sub("", uri, count=1).removeprefix("mailto:").lower():
        return None
    return a[-_CUT_CHARS:], b[:_CUT_CHARS]


def joins_inside_token(before: str, after: str, cuts: LinkCuts = frozenset()) -> bool:
    """Did the line break fall inside one token, so that `after` goes on from `before`
    with no space and the hyphen, if there is one, stays (module docstring)?

    `before` ends the earlier line and `after` starts the next, neither with spaces at the
    break. The caller has handled a soft hyphen. `cuts` are the breaks of the document's
    linked addresses (`link_cut`): a break the file's own link runs across is inside the
    address, whatever its last character is. Any other break inside an address is read from
    the last word of `before` alone."""
    if len(before) < 2 or not after or before[-1].isspace() or after[0].isspace():
        return False
    if any(before.endswith(end) and after.startswith(start) for end, start in cuts):
        return True
    last, previous = before[-1], before[-2]
    if last in HYPHENS + FIXED_HYPHENS and previous.isdecimal() and after[0].isdecimal():
        return True
    if last in FIXED_HYPHENS and previous.isalnum():
        return True
    if last not in _ADDRESS_END and last not in HYPHENS + FIXED_HYPHENS:
        return False
    token = before.rsplit(None, 1)[-1]
    if not _address(token):
        return False
    if last != ".":
        return True
    return _host_unfinished(token) and after[0].islower()  # the host's suffix: "com", "org"
