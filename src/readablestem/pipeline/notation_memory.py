"""A course's notation in a conversion: hints, spoken readings, bold letters in prose.

The instructor keeps a short list per course ("bold d is a vector", "double-struck R is a
set": `core.types.NotationEntry`, keyed by `core.notation.canonical_symbol`). With
`FEATURE_NOTATION_MEMORY` on, the job's course list travels in the run's
`ConversionOptions` and the steps read it here (`entries_in_force`):

- **Hints.** The equation step sends the list to the model as a fenced "course
  notation" block of the untrusted context (`notation_context`), with a prompt version
  of its own that says how to use it. Entries are user content: they never go into the
  instructions, only into the fenced data, like document text.
- **Evidence for the style check.** A letter the course writes bold, in a PDF whose text
  layer shows no bold at all (a font that does not mark it), read as a plain variable is
  suspicious: the step's existing meaning-level `style_mismatch` (`course_bold_letters`).
- **Spoken readings.** "vector d" instead of "bold d" when the course says bold d is a
  vector (`readings`, used by `speech.mathml_to_speech`).
- **Bold letters in prose.** Layout turns a single bold letter in running text into an
  inline equation (reason `BOLD_LETTER_REASON`); the equation step then makes its fix
  here, without a model call (`bold_letter_fix`): `\\mathbf{a}` or `\\boldsymbol{\\omega}`,
  accepted, review "none".

Nothing here is document content in a log: entries and letters only reach the fix and
the fenced context.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping

from readablestem.core.mathchars import STYLE_GROUPS, base_char, char_variant
from readablestem.core.notation import ACCENTS, SYMBOLS
from readablestem.core.types import (
    Check,
    Element,
    EquationFix,
    Fix,
    Kind,
    NotationEntry,
    NotationMeaning,
    Status,
)
from readablestem.pipeline import options

# Element.reasons of an inline equation layout made from one bold letter in running text.
# The reason after it says what made it maths (`layout._settle_bold_letters`).
BOLD_LETTER_REASON = "bold letter in prose"
EVIDENCE_MATHS_FONT = "maths font"
EVIDENCE_DOCUMENT = "bold in the document's maths"
EVIDENCE_COURSE = "course notation"
EVIDENCE_NEARBY = "maths in the paragraph"
# A deterministic fix: the letter and its weight come from the PDF's own text layer.
BOLD_LETTER_CONFIDENCE = 0.95

# The course notation block of the model's context: at most this many entries and
# characters, so the window of document text after it keeps most of the fence's room.
MAX_CONTEXT_ENTRIES = 24
MAX_CONTEXT_CHARS = 700
CONTEXT_HEADER = "[course notation: symbols the instructor says this course uses]"
CONTEXT_FOOTER = "[end of course notation]"

# Meanings that replace the word "bold" when a bold letter is read aloud ("vector d"
# for "bold d"). Other styles keep their words: "double-struck capital R" already says
# the reals to a reader who knows the notation, and "set capital R" would say less.
SPOKEN_MEANINGS: frozenset[str] = frozenset({"vector", "matrix"})

# How a meaning reads after "is": "bold d is a vector".
MEANING_PHRASES: dict[str, str] = {
    "vector": "a vector",
    "matrix": "a matrix",
    "set": "a set",
    "operator": "an operator",
    "constant": "a constant",
    "unit": "a unit",
    "other": "a symbol of this course",
}

# A key's mathvariant -> the words in front of the letter, and the LaTeX around it.
_STYLE_WORDS = {
    "bold": "bold",
    "bold-italic": "bold",
    "double-struck": "double-struck",
    "script": "script",
    "bold-script": "bold script",
    "fraktur": "fraktur",
    "bold-fraktur": "bold fraktur",
    "sans-serif": "sans-serif",
    "sans-serif-italic": "sans-serif",
    "bold-sans-serif": "bold sans-serif",
    "sans-serif-bold-italic": "bold sans-serif",
    "monospace": "monospace",
}
_STYLE_LATEX = {
    "double-struck": r"\mathbb{{{}}}",
    "script": r"\mathcal{{{}}}",
    "bold-script": r"\boldsymbol{{\mathcal{{{}}}}}",
    "fraktur": r"\mathfrak{{{}}}",
    "bold-fraktur": r"\boldsymbol{{\mathfrak{{{}}}}}",
    "sans-serif": r"\mathsf{{{}}}",
    "sans-serif-italic": r"\mathsf{{{}}}",
    "bold-sans-serif": r"\boldsymbol{{\mathsf{{{}}}}}",
    "sans-serif-bold-italic": r"\boldsymbol{{\mathsf{{{}}}}}",
    "monospace": r"\mathtt{{{}}}",
}
_ACCENT_WORDS = {"vec": "with an arrow", "hat": "with a hat"}
_ACCENT_OF_MARK = {mark: name for name, mark in ACCENTS.items()}
_SYMBOL_LATEX = {char: name for name, char in SYMBOLS.items() if name != "infinity"}
_SYMBOL_LATEX["∅"] = "emptyset"
# Capital Greek letters LaTeX has commands for (the rest look like Latin capitals).
_LATEX_GREEK_CAPITALS = frozenset(
    {"Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"}
)


# --------------------------------------------------------------------------- the options


def entries_in_force() -> list[NotationEntry]:
    """The course notation of the conversion in progress: none unless the feature is on
    (`ConversionOptions.notation_memory`) and the job has a course with entries."""
    current = options.current()
    return list(current.notation) if current.notation_memory else []


# --------------------------------------------------------------------------- keys


def _parts(canonical: str) -> tuple[str, str | None, str | None]:
    """(plain base, mathvariant or None, accent name or None) of a notation key."""
    chars = unicodedata.normalize("NFD", canonical)
    first, marks = chars[:1], chars[1:]
    accent = _ACCENT_OF_MARK.get(marks) if marks else None
    if len(first) != 1:
        return canonical, None, accent
    first = unicodedata.normalize("NFC", first)
    return base_char(first), char_variant(first), accent


def _greek_name(ch: str) -> str | None:
    """'ω' -> "omega", 'Ω' -> "Omega" (LaTeX's spelling), None for any other character."""
    name = unicodedata.name(ch, "")
    for prefix, capital in (("GREEK SMALL LETTER ", False), ("GREEK CAPITAL LETTER ", True)):
        if name.startswith(prefix):
            word = name.removeprefix(prefix).lower().replace("lamda", "lambda")
            return word.capitalize() if capital else word
    return None


def base_letter(canonical: str) -> str:
    """The plain letter (or symbol) of a key: "d" for bold d and for d with an arrow."""
    return _parts(canonical)[0]


def describe(canonical: str) -> str:
    """A key in plain words: "bold d", "bold ω", "double-struck R", "v with an arrow"."""
    base, variant, accent = _parts(canonical)
    words = [_STYLE_WORDS[variant]] if variant in _STYLE_WORDS else []
    words.append(base)
    if accent in _ACCENT_WORDS:
        words.append(_ACCENT_WORDS[accent])
    return " ".join(words)


def latex_of(canonical: str) -> str | None:
    r"""The LaTeX for a key: `\mathbf{d}`, `\boldsymbol{\omega}`, `\mathbb{R}`, `\vec{v}`;
    None when LaTeX has no plain way to write it (a capital Greek letter that looks Latin)."""
    base, variant, accent = _parts(canonical)
    if base in _SYMBOL_LATEX:
        inner = "\\" + _SYMBOL_LATEX[base]
    elif (greek := _greek_name(base)) is not None:
        if greek[0].isupper() and greek not in _LATEX_GREEK_CAPITALS:
            return None
        inner = "\\" + greek
    elif len(base) == 1 and base.isascii() and base.isalnum():
        inner = base
    else:
        return None
    group = STYLE_GROUPS.get(variant or "")
    if variant in _STYLE_LATEX:
        latex = _STYLE_LATEX[variant].format(inner)
    elif group == "bold":
        # \mathbf makes Latin letters and digits bold; Greek letters need \boldsymbol.
        command = "mathbf" if len(inner) == 1 else "boldsymbol"
        latex = rf"\{command}{{{inner}}}"
    else:
        latex = inner
    if accent == "vec":
        latex = rf"\vec{{{latex}}}"
    elif accent == "hat":
        latex = rf"\hat{{{latex}}}"
    return latex


def phrase(canonical: str, meaning: NotationMeaning | str) -> str:
    """ "bold d is a vector": one entry as a sentence fragment."""
    return f"{describe(canonical)} is {MEANING_PHRASES.get(meaning, 'a symbol of this course')}"


def course_bold_letters(entries: Iterable[NotationEntry]) -> frozenset[str]:
    """Letters the course writes bold (without an accent): "d" for bold d. A letter the
    course also lists plain (a plain v for speed beside a bold v for velocity) is left
    out: then a plain one is expected too, and is no evidence of a lost bold."""
    bold, plain = set(), set()
    for entry in entries:
        base, variant, accent = _parts(entry.canonical)
        if accent is not None or not base.isalpha():
            continue
        if STYLE_GROUPS.get(variant or "") == "bold":
            bold.add(base)
        elif variant is None:
            plain.add(base)
    return frozenset(bold - plain)


def readings(entries: Iterable[NotationEntry]) -> dict[str, str]:
    """Key -> the word read in place of "bold" ("vector"), for the course's bold letters
    that are vectors or matrices (`speech.mathml_to_speech(readings=...)`)."""
    out = {}
    for entry in entries:
        _, variant, accent = _parts(entry.canonical)
        bold = STYLE_GROUPS.get(variant or "") == "bold"
        if bold and accent is None and entry.meaning in SPOKEN_MEANINGS:
            out[entry.canonical] = entry.meaning
    return out


# --------------------------------------------------------------------------- hints


def notation_context(entries: Iterable[NotationEntry], context_text: str | None) -> str:
    """The equation step's untrusted context with the course notation in front of the
    document text. One line per entry, as its symbol, its LaTeX and its meaning
    (the styled letter, then "(bold d, \\mathbf{d}): vector"); the instructor's own symbol
    spelling and note
    are not sent (the key says the same thing, and less user text means less to fence).
    """
    lines = [CONTEXT_HEADER]
    used = len(CONTEXT_HEADER) + len(CONTEXT_FOOTER) + 2
    for entry in list(entries)[:MAX_CONTEXT_ENTRIES]:
        latex = latex_of(entry.canonical)
        spelled = describe(entry.canonical)
        if latex and latex != entry.canonical:
            spelled += f", {latex}"
        line = f"{entry.canonical} ({spelled}): {entry.meaning}"
        if used + len(line) + 1 > MAX_CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    lines.append(CONTEXT_FOOTER)
    block = "\n".join(lines)
    return f"{block}\n\n{context_text}" if context_text else block


# --------------------------------------------------------------------------- bold letters


def bold_letter_latex(text: str) -> str | None:
    r"""`\mathbf{a}` for a bold Latin letter, `\boldsymbol{\omega}` for a bold Greek one
    (as the text layer writes them: one maths bold code point); None for anything else."""
    text = text.strip()
    if len(text) != 1 or STYLE_GROUPS.get(char_variant(text) or "") != "bold":
        return None
    return latex_of(text) if base_char(text).isalpha() else None


def bold_letter_fix(element: Element, *, spoken: Mapping[str, str] | None = None) -> Fix | None:
    """The fix for a bold letter layout found in running text (`BOLD_LETTER_REASON`):
    the letter as LaTeX, its MathML and speech, accepted with review "none" and no model
    call. None when the element is not one, or its text is not one bold letter (the
    equation step then asks the model as for any other equation)."""
    if BOLD_LETTER_REASON not in element.reasons or element.kind is not Kind.EQUATION:
        return None
    latex = bold_letter_latex(element.text or "")
    if latex is None:
        return None
    from readablestem.pipeline.speech import SpeechError, mathml_to_speech
    from readablestem.pipeline.validate import (
        LatexConversionError,
        check_latex,
        check_mathml,
        has_errors,
        safe_latex_to_mathml,
    )

    display = element.display or "inline"
    checks = list(check_latex(latex))
    try:
        mathml = safe_latex_to_mathml(latex, display)
    except LatexConversionError:
        return None
    checks += [Check(name="latex.converts", passed=True), *check_mathml(mathml)]
    if has_errors(checks):
        return None
    try:
        speech = mathml_to_speech(mathml, readings=spoken)
    except SpeechError:
        speech = ""
    checks.append(Check(name="equation.bold_letter", passed=True))
    return Fix(
        element_id=element.id,
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=BOLD_LETTER_CONFIDENCE,
        equation=EquationFix(latex=latex, mathml=mathml, speech=speech, display=display),
        checks=checks,
        review="none",
    )
