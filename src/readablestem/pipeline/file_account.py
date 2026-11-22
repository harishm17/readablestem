"""What the file stores under an equation's box, written for a model that reads the picture.

With `EQUATION_FILE_FACTS` a model that reads an equation from its picture is handed the file's
own account of the same place (rule 13 of the plan): the characters the PDF stores under the
box, in the file's order, glyph by glyph. They are good for which letter, which digit and which
style, and they are not good for order or structure (a fraction, a root and limits are flat or
scrambled in a text layer), so the prompt says the picture decides structure and a difference is
reported in a reply field, not settled silently. The comparison in code after the answer stays.

Each glyph is written as the file gives it, and how it was recovered travels with it
(`GlyphContext.how`): a character the file's own table decodes is plain; one read by the
glyph's name is marked `{name}`, one read by the font-code table `{font}`, one guessed
`{guess}`; a glyph the file does not name is `[gap]`, in its place, never dropped. Runs of one
letter style (italic, upright, bold, bold-italic, script, double-struck, fraktur) are written
on a line of their own, with `raised` or `lowered` when the glyphs sit off the box's baseline.
The first line of the block counts the glyphs the file does not name, so the model knows how much
of the account to trust.

When more than `MAX_GAP_SHARE` of the glyphs are gaps, or there are more than `MAX_GLYPHS`,
there is no account and the request is what it was before: a model told that most of the
file's characters are missing, or handed a page of them, is no better off than one given none.

The text is untrusted (the PDF's characters) and goes in the fenced document text. A run of
glyphs is joined into one string, so a run could spell a tag: `<` and `>` are written as words,
and control and invisible format characters are left out, so nothing in the block can close the
block or the fence.
"""

from __future__ import annotations

import unicodedata

from readablestem.core.types import GlyphContext
from readablestem.pipeline.output_chars import GAP

# More gaps than this share of the glyphs under the box: no account (see the module docstring).
MAX_GAP_SHARE = 0.3
MAX_GLYPHS = 250  # a longer box is not sent: the account would be longer than the picture helps

_HOW = {"n": "{name}", "f": "{font}", "?": "{guess}"}
_STYLE = {
    "italic": "italic",
    "upright": "upright",
    "bold": "bold",
    "bold-italic": "bold italic",
    "script": "script",
    "double-struck": "blackboard",
    "fraktur": "fraktur",
}
_LIFT = {"^": "raised", "v": "lowered"}
# A character that could open or close a tag, written as what it is.
_ANGLE = {"<": "[less-than]", ">": "[greater-than]"}


def _token(char: str) -> str:
    """One glyph's character as text the block can carry; "" for one that has no visible form."""
    if char in _ANGLE:
        return _ANGLE[char]
    return "" if unicodedata.category(char) in ("Cc", "Cf") else char


def _runs(glyphs: tuple[tuple[str, str | None], ...], how: str = "", lift: str = "") -> list[str]:
    """One line for each run of glyphs of one style and one height: `italic, raised: x 2`.
    A glyph that is no letter (a digit, a sign) goes with the run it stands in, and a run with
    no letter has no style."""
    runs: list[tuple[str | None, str, list[str]]] = []
    for i, (char, style) in enumerate(glyphs):
        if not char.strip():  # the file's own space: kept inside a run, never at its ends
            if runs and runs[-1][2][-1] != " ":
                runs[-1][2].append(" ")
            continue
        height = lift[i] if i < len(lift) and lift[i] in _LIFT else "-"
        how_i = how[i] if i < len(how) else "-"
        token = "[gap]" if how_i == "g" or char == GAP else _token(char) + _HOW.get(how_i, "")
        if not token:
            continue
        now = _STYLE.get(style or "")
        if runs and runs[-1][1] == height and (now is None or runs[-1][0] in (None, now)):
            runs[-1] = (runs[-1][0] or now, height, [*runs[-1][2], token])
        else:
            runs.append((now, height, [token]))
    return [
        ", ".join(part for part in (style or "no style", _LIFT.get(height)) if part)
        + ": "
        + "".join(tokens).strip()
        for style, height, tokens in runs
    ]


def _gaps(context: GlyphContext) -> tuple[int, int]:
    """The number of glyphs under the box and how many of them are gaps."""
    shown = [i for i, (c, _) in enumerate(context.span_glyphs) if c.strip()]
    gaps = sum(
        1 for i in shown if context.how[i : i + 1] == "g" or context.span_glyphs[i][0] == GAP
    )
    return len(shown), gaps


def account(context: GlyphContext | None, *, beside: bool = False) -> str | None:
    """The file's account of the box as a block of text, or None when there is nothing to say
    (no glyphs, more gaps than `MAX_GAP_SHARE`, more glyphs than `MAX_GLYPHS`).

    `beside`: also the maths glyphs on the same row to the left and right of the box, for a
    check that asks whether the box holds the whole formula."""
    if context is None:
        return None
    count, gaps = _gaps(context)
    if not count or count > MAX_GLYPHS or gaps / count > MAX_GAP_SHARE:
        return None
    lines = [f"unmapped characters in the box: {gaps} of {count}"]
    runs = _runs(context.span_glyphs, context.how, context.lift)
    if beside:
        lines += ["in the box:", *runs]
        # Stored nearest first; the left side is read from its far end towards the box.
        left = _runs(tuple(reversed(GlyphContext.decode(context.math_before))))
        right = _runs(GlyphContext.decode(context.math_after))
        lines += ["left of the box:", *(left or ["(none)"])]
        lines += ["right of the box:", *(right or ["(none)"])]
    else:
        lines += runs
    return "<file_characters>\n" + "\n".join(lines) + "\n</file_characters>"
