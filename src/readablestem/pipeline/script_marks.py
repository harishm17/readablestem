"""Raised and lowered runs travelling inside a piece of text.

Layout builds a paragraph from pieces of text and cleans, joins and repairs them on the way
(blanks collapsed, hyphenation repaired, inline equations cut out). The offsets of a run in
the final `Element.text` cannot be kept through that, so a run travels as private-use
characters around its text and is cut out once, when the element is made:

    "H" + SUB_OPEN + "2" + SUB_CLOSE + "O"  ->  text "H2O", ScriptRun(1, 2, "sub")

None of the four characters can come out of a PDF (`layout._clean` keeps them only because
they are not control characters; a PDF that really holds one is read with it dropped by
`wrap`), so a mark is always one layout put there.
"""

from __future__ import annotations

from typing import Literal

from readablestem.core.types import ScriptRun

SUP_OPEN = "\ue000"
SUP_CLOSE = "\ue001"
SUB_OPEN = "\ue002"
SUB_CLOSE = "\ue003"
MARKS = frozenset({SUP_OPEN, SUP_CLOSE, SUB_OPEN, SUB_CLOSE})
Kind = Literal["sup", "sub"]
_OPEN: dict[Kind, str] = {"sup": SUP_OPEN, "sub": SUB_OPEN}
_CLOSE: dict[Kind, str] = {"sup": SUP_CLOSE, "sub": SUB_CLOSE}
_KIND: dict[str, Kind] = {SUP_OPEN: "sup", SUB_OPEN: "sub"}


def wrap(chars: list[str], kinds: list[Kind | None]) -> str:
    """The characters as text, each run of one kind between its marks. A mark that is
    itself among the characters is dropped."""
    out: list[str] = []
    current: Kind | None = None
    for char, kind in zip(chars, kinds, strict=True):
        if kind != current:
            if current is not None:
                out.append(_CLOSE[current])
            if kind is not None:
                out.append(_OPEN[kind])
            current = kind
        if char not in MARKS:
            out.append(char)
    if current is not None:
        out.append(_CLOSE[current])
    return "".join(out)


def has_marks(text: str) -> bool:
    return any(c in MARKS for c in text)


def strip_marks(text: str) -> str:
    return "".join(c for c in text if c not in MARKS)


def split(text: str) -> tuple[str, list[ScriptRun]]:
    """`text` without marks and the runs they enclosed, as offsets into the result with
    its leading and trailing blanks removed (what `Element.text` stores). A run left
    empty, or all blanks, by the removal is dropped; a mark that does not pair up is
    ignored."""
    if not has_marks(text):
        return text.strip(), []
    out: list[str] = []
    spans: list[tuple[int, int, Kind]] = []
    open_at: tuple[int, Kind] | None = None
    for char in text:
        if char in _KIND:
            open_at = (len(out), _KIND[char])
        elif char in (SUP_CLOSE, SUB_CLOSE):
            if open_at is not None and len(out) > open_at[0]:
                spans.append((open_at[0], len(out), open_at[1]))
            open_at = None
        else:
            out.append(char)
    joined = "".join(out)
    lead = len(joined) - len(joined.lstrip())
    stripped = joined.strip()
    runs = []
    for start, end, kind in spans:
        start, end = max(start - lead, 0), min(end - lead, len(stripped))
        if end > start and stripped[start:end].strip():
            runs.append(ScriptRun(start=start, end=end, kind=kind))
    return stripped, runs
