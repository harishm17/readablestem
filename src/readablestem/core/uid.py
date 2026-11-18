"""Stable element ids (`uid`): a name for an element that does not move when another one is
added, removed or edited (WS9.1).

An element's `id` ("p3-e12") is its place on its page: the thirteenth block layout found
there. A paragraph added above it on a revised file, or a block layout finds on a second
run that it missed on the first, renames it and everything after it on the page. The `uid`
is made from what the element *is* instead:

    uid = "u{VERSION}-" + sha256(VERSION, scope, fingerprint, ordinal)[:16]

- **fingerprint** (`fingerprint`): a SHA-256 of the element's kind and its content, read
  from the fields it has: its words with whitespace and case folded, and per kind what
  tells two of them apart (a heading's size and weight, an equation's display mode and its
  printed number, a figure's caption and its own alt text, a table's caption and cells).
  Nothing positional goes in: not the box, not the reading order, and not the ids of
  inline equations inside a text (a marker counts, its id does not), so moving an element
  down the page or renumbering its neighbours leaves it alone. Of the positional id only
  its shape is read: an element a reviewer added by hand ("p3-x0") is kept apart from what
  the tool found. A fix, which is where a
  reviewer's decision lives, is not read either, so a decision never renames its item.
- **scope**: the 0-based page for a PDF, whose pages are fixed. A document read from a
  LaTeX source has no pages of its own (a witness may paginate it differently next time),
  so its scope is the whole document.
- **ordinal**: the element's place among the elements of the same scope with the same
  fingerprint, in reading order. Two different elements in one document therefore never
  share a uid: either their scope or fingerprint differs, or they are in one group and their
  ordinals differ. The hash is truncated to 64 bits; should two truncated hashes ever meet,
  the later element (in reading order) takes the whole digest instead, so the map stays one
  to one.

What a uid holds: hex digits only. The document's words go into a hash and never into the
id, so a uid is safe in a URL, a log line or an event.

**Identical elements.** Two elements whose fingerprints agree on one page (the same formula
printed twice, two pictures without a caption or text) are told apart by their order alone.
Removing the second leaves the first its uid. Removing the first gives the second the uid
the first had: for text, the content is the same, so a link lands on the same words. For a
picture whose fields say nothing (no text layer, no caption) the uid says only "the n-th
such picture on page p": whatever matches items *across* two conversions must still compare
the pictures, as decision carry-over does (`fingerprint` with the crop, `web.carry`).

**Versions and aliases.** The version is in the prefix and in the hash. When the
fingerprint changes (the element model of part 2 gains fields), the new function is added
to `FINGERPRINTS` under the next number and the old one stays: `assign` computes every
version's uids, so an id handed out by an earlier version (a bookmarked link, a stored
reference) still resolves to its element (`Uids.resolve`), and `Uids.aliases` maps it to
the current one. A uid of a version this code does not know, or one that names no element,
resolves to None: it is never matched to the nearest element.

The positional id stays what people see (labels, the review's "p3-e12") and what results,
fixes, crops and review rows are keyed by; it is itself an alias of the uid. Nothing here
is stored: uids are computed from a result whenever they are needed, so a result stored
before this module existed has them too, without being rewritten.

A uid is a function of the element as it stands. A reviewer's edit to an element's words
or kind gives it the uid of what it now says; its positional id still names it.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from readablestem.core.types import INLINE_MARKER_RE, DocumentResult, Element, Kind

# Kinds whose content is a picture cut from the page: with the crop at hand (decision
# carry-over), the crop's bytes stand for the content.
PICTURE_KINDS = frozenset({Kind.EQUATION, Kind.FIGURE, Kind.TABLE})
# Origins read from a source and not from pages (`DocumentResult.origin`): uids are counted
# over the whole document.
SOURCE_ORIGINS = frozenset({"latex", "beamer", "docx"})
# Elements a reviewer added by hand ("p3-x0", `web.page_edits.next_added_id`) are a group of
# their own, so adding one never renumbers what the tool found.
_ADDED = re.compile(r"-x\d+$")
_SPACES = re.compile(r"\s+")
_UID = re.compile(r"u(\d{1,4})-[0-9a-f]{16}(?:[0-9a-f]{48})?")
SHORT = 16  # hex digits of a uid's hash (64 bits)


def words(text: str | None) -> str:
    """Text with whitespace collapsed and case folded: what "the same words" means."""
    return _SPACES.sub(" ", (text or "").strip()).casefold()


def _unmarked(text: str | None) -> str:
    """The text with each inline equation's marker kept and its positional id dropped."""
    return INLINE_MARKER_RE.sub("⟦⟧", text or "")


def _hash(parts: list[str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def fingerprint(element: Element, crop: bytes | None = None, *, crop_sha: str | None = None) -> str:
    """The content of one element, as a hex digest: two elements with the same fingerprint
    say the same thing. The one function behind both the uid and decision carry-over.

    `crop` is the picture of an equation, figure or table (carry-over has it): the picture
    then stands for the content, so a picture that differs by one pixel is another item.
    `crop_sha` is the SHA-256 of that picture when only the digest was kept (the snapshot of an
    earlier version, `web.carry_snapshot`); it gives the same fingerprint as the picture.
    Without either (uids are computed from the result alone) the element's fields are read.
    """
    kind = element.kind
    parts = [kind.value]
    if crop is not None:
        crop_sha = hashlib.sha256(crop).hexdigest()
    if crop_sha is not None and kind in PICTURE_KINDS:
        parts.append(crop_sha)
        if kind is Kind.EQUATION:
            parts.append(element.display or "")
        elif kind is Kind.FIGURE:
            parts.append(words(element.caption))
        return _hash(parts)
    parts.append(words(_unmarked(element.text)))
    if kind is Kind.HEADING:
        parts += [f"{element.font_size or 0:.1f}", "b" if element.bold else "r"]
    elif kind is Kind.EQUATION:
        parts += [element.display or "", element.equation_label or ""]
    elif kind is Kind.FIGURE:
        parts += [words(element.caption), words(element.source_alt)]
    elif kind is Kind.TABLE:
        parts.append(words(element.caption))
        if element.table is not None:
            cells = sorted(element.table.cells, key=lambda c: (c.row, c.col))
            parts += [f"{c.row},{c.col}:{words(c.text)}" for c in cells]
    elif element.caption:
        parts.append(words(element.caption))
    if _ADDED.search(element.id):
        parts.append("added")
    return _hash(parts)


# Every version's fingerprint, oldest first. A change to `fingerprint` that would give any
# element another value is a new version: copy the old function here under its number,
# and add the new one under the next.
FINGERPRINTS: dict[int, Callable[[Element], str]] = {1: fingerprint}


def version() -> int:
    """The version new uids are made with."""
    return max(FINGERPRINTS)


def is_uid(ref: str) -> bool:
    """Has `ref` the shape of a uid (of any version)? Positional ids never do."""
    return _UID.fullmatch(ref) is not None


def _all_elements(result: DocumentResult) -> list[Element]:
    """The result's elements with those of the pages left out (`DocumentResult.omitted`):
    leaving a page out and putting it back renames nothing."""
    held = [e for page in result.omitted for e in page.elements]
    return sorted([*result.elements, *held], key=lambda e: (e.page, e.order))


def _uids(
    elements: list[Element], number: int, print_: Callable[[Element], str], whole: bool
) -> dict[str, str]:
    """Positional id -> uid of version `number`, for `elements` in reading order."""
    seen: defaultdict[tuple[str, str], int] = defaultdict(int)
    out: dict[str, str] = {}
    taken: set[str] = set()
    for element in elements:
        scope = "doc" if whole else str(element.page)
        content = print_(element)
        ordinal = seen[(scope, content)]
        seen[(scope, content)] += 1
        digest = _hash([str(number), scope, content, str(ordinal)])
        uid = f"u{number}-{digest[:SHORT]}"
        if uid in taken:  # two truncated hashes met: the whole digest is unique
            uid = f"u{number}-{digest}"
        taken.add(uid)
        out[element.id] = uid
    return out


@dataclass(frozen=True)
class Uids:
    """The uids of one result: the current one of each element, and the earlier versions'
    as aliases."""

    by_id: Mapping[str, str]  # positional id -> current uid
    _to_id: Mapping[str, str]  # uid of any version, or positional id -> positional id

    def uid(self, element_id: str) -> str | None:
        """The current uid of the element with this positional id."""
        return self.by_id.get(element_id)

    def resolve(self, ref: str) -> str | None:
        """The positional id of the element `ref` names: a uid of any known version, or a
        positional id. None when it names no element of this result."""
        return self._to_id.get(ref)

    @property
    def aliases(self) -> dict[str, str]:
        """Every other name of an element -> its current uid: its positional id and its uids
        of earlier versions."""
        return {
            ref: self.by_id[element_id]
            for ref, element_id in self._to_id.items()
            if ref != self.by_id.get(element_id)
        }


def assign(result: DocumentResult) -> Uids:
    """The uids of every element of `result` (pages left out included), every version's."""
    elements = _all_elements(result)
    whole = result.origin in SOURCE_ORIGINS
    current = version()
    to_id: dict[str, str] = {e.id: e.id for e in elements}
    by_id: dict[str, str] = {}
    for number, print_ in FINGERPRINTS.items():
        uids = _uids(elements, number, print_, whole)
        if number == current:
            by_id = uids
        to_id.update({uid: element_id for element_id, uid in uids.items()})
    return Uids(by_id=by_id, _to_id=to_id)
