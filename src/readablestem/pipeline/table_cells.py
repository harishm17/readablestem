"""A table cell of words with maths in it, written as words and maths.

The table step's model gives a maths cell as LaTeX for the whole cell, words in `\\text{...}`:
`\\text{Muon (}\\mu\\text{)}`, `\\text{Array of the Transverse Momenta } (p_{T}) \\text{ of the
muons.}`.
Written as one `<math>`, a sentence is read by a screen reader as maths, letter by letter or not at
all, and the maths in it is no element of its own. This cuts the LaTeX of such a cell into its
`\\text{}` runs, which are text, and what stands between them, which is maths: the cell is then
the words with a `<math>` for each formula in them, as a paragraph is. A cell whose LaTeX is only
maths (`1.5 \\times 10^{-3}`), or only words, is not cut: it is written as before.

Rules, all on the LaTeX of the cell (no model, no text layer):

- a `\\text{}` (or `\\textrm`, `\\mbox`) run with nothing in it but plain characters and the
  escapes `\\%`, `\\&`, `\\_`, `\\#`, `\\$`, `\\ ` and `~` is text; anything else in it (a command)
  leaves the cell whole;
- what lies between two runs is maths when it holds a letter, a symbol or a script; a bare
  number, a sign, a comma or a bracket between words is text again (`2 \\text{ of}` is "2 of");
- the brackets that wrap a whole formula and the sentence punctuation after it belong to the text:
  `\\text{Momenta } (p_{T}) \\text{ of}` is "Momenta (", p_T, ") of";
- a cut that would leave a formula with unbalanced brackets (`g\\ (\\text{m/s}^2)` gives `g\\ (`
  and `^2)`) is not made: the cell is left whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A run of words: the command and its braced argument (no braces inside it but the escapes).
_TEXT_COMMANDS = ("text", "textrm", "mbox", "textnormal")
_ESCAPES = {r"\%": "%", r"\&": "&", r"\_": "_", r"\#": "#", r"\$": "$", r"\ ": " ", "~": " "}
_ESCAPE = re.compile(r"\\[%&_#$ ]|~")
_COMMAND = re.compile(r"\\[A-Za-z]+")
_PLAIN_MATHS = re.compile(r"^[\s\d.,;:+\-−()\[\]/]*$")


@dataclass(frozen=True)
class Piece:
    """One run of a cell: `kind` "text" (`value` is the words) or "math" (`value` is LaTeX)."""

    kind: str
    value: str


def _group_end(latex: str, start: int) -> int | None:
    """The index just after the brace that closes the group opened at `start`, or None."""
    depth = 0
    i = start
    while i < len(latex):
        ch = latex[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _text_of(argument: str) -> str | None:
    """The words in a `\\text{...}` argument, or None when it holds anything but words."""
    if "{" in argument or "}" in argument or _COMMAND.search(argument.replace("\\ ", "")):
        return None
    return _ESCAPE.sub(lambda m: _ESCAPES[m.group(0)], argument)


def _split(latex: str) -> list[Piece] | None:
    pieces: list[Piece] = []
    maths: list[str] = []
    i = 0
    pattern = re.compile(r"\\(" + "|".join(_TEXT_COMMANDS) + r")\s*\{")
    while i < len(latex):
        match = pattern.search(latex, i)
        if match is None:
            break
        end = _group_end(latex, match.end() - 1)
        if end is None:
            return None
        words = _text_of(latex[match.end() : end - 1])
        if words is None:
            return None
        if latex[i : match.start()].strip():
            maths.append(latex[i : match.start()])
        if maths:
            pieces.append(Piece("math", " ".join(m.strip() for m in maths)))
            maths = []
        pieces.append(Piece("text", words))
        i = end
    if latex[i:].strip():
        maths.append(latex[i:])
    if maths:
        pieces.append(Piece("math", " ".join(m.strip() for m in maths)))
    return pieces


_WRAPPED = re.compile(r"^\s*\(\s*(.+?)\s*\)\s*([.,;:]?)\s*$", re.S)


def _balanced(inner: str) -> bool:
    depth = 0
    for ch in inner:
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            return False
    return depth == 0


def _brackets_balanced(value: str) -> bool:
    """Every (, [ and { in the LaTeX closes in it, in order (escaped braces aside)."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs and (not stack or stack.pop() != pairs[ch]):
            return False
        i += 1
    return not stack


def _unwrap(value: str) -> tuple[str, str, str]:
    """("(", "p_{T}", ")") for "(p_{T})"; ("", value, "") when the brackets are not a pair round
    the whole formula. A closing "." or "," after it is the sentence's: (before, maths, after)."""
    match = _WRAPPED.match(value)
    if match and _balanced(match.group(1)):
        return "(", match.group(1), ")" + match.group(2)
    stripped = value.strip()
    tail = ""
    while stripped and stripped[-1] in ".,;:" and len(stripped) > 1:
        tail = stripped[-1] + tail
        stripped = stripped[:-1].rstrip()
    return "", stripped, tail


def _is_maths(value: str) -> bool:
    """Real maths between words: a letter, a symbol command or a script, not a bare number."""
    return bool(value.strip()) and not _PLAIN_MATHS.match(value)


def cell_pieces(latex: str | None) -> list[Piece] | None:
    """The cell's words and formulas in order, or None when the cell is not words with maths in
    them (no `\\text{}` run, only words, or a run the cell cannot be cut at)."""
    if not latex or ("\\text" not in latex and "\\mbox" not in latex and "\\textrm" not in latex):
        return None
    pieces = _split(latex)
    if pieces is None or not any(p.kind == "text" and p.value.strip() for p in pieces):
        return None
    if any(p.kind == "math" and not _brackets_balanced(p.value) for p in pieces):
        return None
    out: list[Piece] = []

    def add(kind: str, value: str) -> None:
        if not value:
            return
        if out and out[-1].kind == "text" and kind == "text":
            out[-1] = Piece("text", out[-1].value + value)
        else:
            out.append(Piece(kind, value))

    for piece in pieces:
        if piece.kind == "text":
            add("text", piece.value)
        elif not _is_maths(piece.value):
            words = " ".join(piece.value.split())
            lead = " " if piece.value[:1].isspace() else ""
            trail = " " if piece.value[-1:].isspace() else ""
            add("text", f"{lead}{words}{trail}" if words else " ")
        else:
            before, inner, after = _unwrap(piece.value)
            add("text", before)
            if _is_maths(inner):
                add("math", inner)
            else:
                add("text", inner)
            add("text", after)
    if not any(p.kind == "math" for p in out):
        return None
    return out
