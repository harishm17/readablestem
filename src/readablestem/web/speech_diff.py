"""Where an equation's spoken reading differs from its PDF's own text (UX review BW12).

The review screen says roughly how a screen reader reads the proposal ("omega times bold
r"). For some meaning errors that line was the only place they showed, but it is one
more string to read closely. So the PDF's text layer is read the same way (each
character as a MathML token, through `pipeline.speech`: a bold 𝛚 is "bold omega") and
the words that differ are marked in both readings: "omega" in the proposal's, "bold
omega" in the PDF's.

Only the symbols are compared, never the words that carry structure ("to the power",
"sub", "the fraction ... over", "end root"): the text layer has no structure (x² is
"x2" there), so those would differ on every page. A symbol is a letter or Greek letter
with its style ("bold", "double-struck", "capital"), a number, or a sign or relation the
sign check compares (plus, minus, equals, less than, ...). Multiplication and division
are left out, as the checks leave them out (a style choice, not the meaning). "Squared"
and "cubed" count as the 2 and 3 they stand for.

The screen shows the PDF's reading only when the step's checks found a difference
(`web.compare`), so a page with nothing to decide gains no line to read. Nothing here is
stored or logged: it is worked out when the screen is shown, for its owner.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

STYLES = ("bold script", "bold fraktur", "double-struck", "script", "fraktur", "bold")
SIGNS = (
    "plus or minus",
    "minus or plus",
    "less than or equal to",
    "greater than or equal to",
    "approximately equals",
    "not equal to",
    "less than",
    "greater than",
    "equals",
    "plus",
    "minus",
)
POWERS = {"squared": "2", "cubed": "3"}
MAX_TEXT_LAYER = 400  # characters read from a text layer; longer ones are not compared
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True)
class Segment:
    text: str
    differs: bool = False


@dataclass
class SpokenDiff:
    """Both readings as segments, the words that differ marked."""

    said: list[Segment] = field(default_factory=list)  # the proposal (or edit)
    pdf_said: list[Segment] = field(default_factory=list)  # the PDF's text layer

    @property
    def differs(self) -> bool:
        return any(s.differs for s in (*self.said, *self.pdf_said))


def _greek_names() -> frozenset[str]:
    from readablestem.pipeline.speech import GREEK

    return frozenset(name.removeprefix("capital ") for name in GREEK.values())


def _symbol_at(words: list[str], i: int, greek: frozenset[str]) -> int:
    """How many words from `i` voice one symbol (0: none, a word of structure)."""
    for sign in SIGNS:
        parts = sign.split()
        if words[i : i + len(parts)] == parts:
            return len(parts)
    j = i
    for style in STYLES:
        parts = style.split()
        if words[j : j + len(parts)] == parts:
            j += len(parts)
            break
    if j < len(words) and words[j] == "capital":
        j += 1
    if j >= len(words):
        return 0
    word = words[j]
    if (len(word) == 1 and word.isalpha()) or word in greek or _NUMBER.fullmatch(word):
        return j - i + 1
    if j == i and word in POWERS:
        return 1
    return 0


def tokens(speech: str) -> list[tuple[str, bool]]:
    """The reading as (words, is a symbol) pieces, in order."""
    words = speech.split()
    greek = _greek_names()
    out: list[tuple[str, bool]] = []
    i = 0
    while i < len(words):
        n = _symbol_at(words, i, greek)
        if n:
            out.append((" ".join(words[i : i + n]), True))
            i += n
        else:
            out.append((words[i], False))
            i += 1
    return out


def _key(piece: str) -> str:
    return POWERS.get(piece, piece)


UNSEEN_STYLES = ("double-struck", "script", "fraktur")


def _unseen(proposal: str, pdf: str) -> bool:
    return any(proposal == f"{style} {pdf}" for style in UNSEEN_STYLES)


def compare_readings(said: str, pdf_said: str) -> SpokenDiff:
    """Mark the symbols that differ between two readings (structure words never)."""
    left, right = tokens(said), tokens(pdf_said)
    left_symbols = [i for i, (_, symbol) in enumerate(left) if symbol]
    right_symbols = [i for i, (_, symbol) in enumerate(right) if symbol]
    matcher = difflib.SequenceMatcher(
        None,
        [_key(left[i][0]) for i in left_symbols],
        [_key(right[i][0]) for i in right_symbols],
        autojunk=False,
    )
    marked_left: set[int] = set()
    marked_right: set[int] = set()
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        pairs = zip(left_symbols[a0:a1], right_symbols[b0:b1], strict=False)
        if tag == "replace" and a1 - a0 == b1 - b0:
            # Not a difference: a letter style the PDF's text cannot carry (a double-
            # struck R from an MSBM font is a plain R there), as `web.compare` has it.
            same = {(i, j) for i, j in pairs if _unseen(left[i][0], right[j][0])}
        else:
            same = set()
        marked_left.update(i for i in left_symbols[a0:a1] if i not in {p[0] for p in same})
        marked_right.update(j for j in right_symbols[b0:b1] if j not in {p[1] for p in same})
    return SpokenDiff(
        said=_segments(left, marked_left),
        pdf_said=_segments(right, marked_right),
    )


def _segments(pieces: list[tuple[str, bool]], marked: set[int]) -> list[Segment]:
    """Runs of unmarked words joined, each marked symbol on its own (the page puts a
    space between segments)."""
    out: list[Segment] = []
    for i, (text, _) in enumerate(pieces):
        differs = i in marked
        if out and not differs and not out[-1].differs:
            out[-1] = Segment(out[-1].text + " " + text)
        else:
            out.append(Segment(text, differs))
    return out


# --------------------------------------------------------------------------- the PDF


_FUNCTION_RUN = re.compile(r"[A-Za-z]{2,}")


def text_layer_mathml(text: str) -> str | None:
    """The text layer as flat MathML, one token per symbol, for `pipeline.speech`:
    letters (math alphanumerics too, which carry their style) as <mi>, digits as <mn>,
    everything else as <mo>. A run of Latin letters that is a function name ("sin")
    stays one token, so it reads as the proposal's does. None when there is nothing to
    read, or too much."""
    from readablestem.pipeline.speech import FUNCTIONS, LIMIT_OPERATORS

    text = " ".join(text.split())
    if not text or len(text) > MAX_TEXT_LAYER:
        return None
    root = ET.Element("math", {"xmlns": "http://www.w3.org/1998/Math/MathML"})
    row = ET.SubElement(root, "mrow")
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        run = _FUNCTION_RUN.match(text, i)
        if run and (run.group(0) in FUNCTIONS or run.group(0) in LIMIT_OPERATORS):
            ET.SubElement(row, "mi").text = run.group(0)
            i = run.end()
            continue
        folded = unicodedata.normalize("NFKC", ch)
        if folded.isdigit():
            j = i + 1
            while j < len(text) and unicodedata.normalize("NFKC", text[j]).isdigit():
                j += 1
            ET.SubElement(row, "mn").text = text[i:j]
            i = j
            continue
        ET.SubElement(row, "mi" if ch.isalpha() else "mo").text = ch
        i += 1
    if not len(row):
        return None
    return ET.tostring(root, encoding="unicode")


def read_text_layer(text: str) -> str:
    """Roughly how the speech rules read the PDF's text for an equation ("" when they
    cannot)."""
    from readablestem.pipeline import speech

    mathml = text_layer_mathml(text)
    if mathml is None:
        return ""
    try:
        return speech.mathml_to_speech(mathml)
    except Exception as exc:  # a reading aid: never an error on the screen
        logger.warning("text_layer_speech_failed", extra={"exc_type": type(exc).__name__})
        return ""


def spoken_diff(said: str | None, text_layer: str | None) -> SpokenDiff | None:
    """Both readings with the differing symbols marked, or None when there is nothing to
    compare or no symbol differs."""
    if not said or not text_layer:
        return None
    pdf_said = read_text_layer(text_layer)
    if not pdf_said:
        return None
    diff = compare_readings(said, pdf_said)
    return diff if diff.differs else None
