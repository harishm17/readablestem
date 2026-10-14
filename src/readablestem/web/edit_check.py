"""A reviewer's LaTeX, checked the way a model's proposal is (UX review MF3).

An edit used to pass the LaTeX and MathML checks only, so "bold ω" typed as words, or a
plain ω for a vector the PDF has bold, was saved without a word and the warning that
asked for the decision went away. Now an edit goes through what the equation step runs
on a proposal (`pipeline.equation_step._evaluate`): the same cleaning, the LaTeX
validators, the conversion, the MathML checks, the spoken version, and the cross-checks
against the PDF's own text: letters, digits and Greek letters, numbers and capitals,
letter styles (a vector's bold) and signs. What fails is said in plain words, as three kinds:

- *blocking*: the LaTeX cannot be used (an error-level check). Never saved.
- *meaning*: it differs from the PDF's text where that changes what a student reads.
  Saved only when the reviewer chooses "Use my edit anyway" (the failed checks stay on
  the fix and the ReviewEdit records the choice): the PDF's text is sometimes wrong.
- *notes*: a small difference (a cosmetic style, no spoken version). Saved and listed.

The review POST (`web.review._edit_equation`) and the live check while typing
(`routes/split.py`, a server-rendered fragment for htmx) share this, so the two never
disagree. A table's maths cell is checked the same way (`check_cell_edit`, used by
`web.review._edit_cell`), against the PDF's text in that cell. The validators and the
speech are looked up on their modules at call time (as
in `web.review`), so tests can swap in stubs; `tests/test_web_review_ux.py` checks the
result is the step's own on real input. Nothing here is logged or stored except the
checks' codes on a saved fix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from readablestem.core.types import Check, Element, EquationFix, Fix, Kind
from readablestem.web.messages import explain_check

logger = logging.getLogger(__name__)

# Failed checks that are about the meaning: the edit is not used without the reviewer's
# explicit "Use my edit anyway". The same codes make a proposal a required decision.
MEANING_CODES = frozenset(
    {
        "symbol_mismatch",
        "text_layer_mismatch",
        "style_mismatch",
        "sign_mismatch",
        "number_mismatch",
        "case_mismatch",
    }
)
# What the compare sentences (`web.compare`) are about, for each meaning-level code.
_SAID_FOR = {
    "style_mismatch": ("style",),
    "symbol_mismatch": ("symbol",),
    "text_layer_mismatch": ("symbol",),
    "sign_mismatch": ("sign",),
    "number_mismatch": ("number",),
    "case_mismatch": ("case",),
}
MAX_NAMED = 3  # unknown commands named in one message
Display = Literal["block", "inline"]
_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


@dataclass
class EditCheck:
    """What the checks make of one LaTeX edit."""

    latex: str  # as it would be stored (cleaned like model output)
    mathml: str | None  # None when the LaTeX cannot be used
    speech: str
    checks: list[Check]
    display: Display
    blocking: list[str] = field(default_factory=list)  # sentences: why it cannot be used
    meaning: list[str] = field(default_factory=list)  # ... what differs from the PDF
    notes: list[str] = field(default_factory=list)  # ... small things, saved anyway
    unchanged: bool = False  # the LaTeX the fix already has
    has_text_layer: bool = False  # the PDF has text for this equation to compare with

    @property
    def empty(self) -> bool:
        return not self.latex

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def check_equation_edit(element: Element | None, fix: Fix, typed: str) -> EditCheck:
    """Run the proposal checks on `typed` for this equation (see the module docstring)."""
    from readablestem.pipeline import equation_step

    if fix.equation is not None:
        display = fix.equation.display
    else:
        display = (element.display if element else None) or "block"
    latex = equation_step.clean_latex(typed)
    if display == "inline":
        latex = equation_step.strip_trailing_punctuation(latex)
    text = element.text if element is not None else None
    out = EditCheck(latex=latex, mathml=None, speech="", checks=[], display=display)
    out.has_text_layer = bool(text and any(ch.isalnum() for ch in text))
    if not latex:
        return out
    out.unchanged = fix.equation is not None and _same(latex, fix.equation.latex)
    out.mathml, out.speech, out.checks = evaluate(latex, display, element)
    if out.mathml is None:
        out.blocking = latex_problems(latex, out.failed)
        return out
    edited = fix.model_copy(
        update={
            "equation": EquationFix(
                latex=latex, mathml=out.mathml, speech=out.speech, display=display
            )
        }
    )
    from readablestem.web.compare import compare_equation

    said = compare_equation(element, edited, other="your edit").said
    for check in out.failed:
        code = check.code or ""
        if code in MEANING_CODES:
            found = [s for about in _SAID_FOR[code] for s in said.get(about, [])]
            out.meaning.extend(
                s for s in found or [explain_check(check).text] if s not in out.meaning
            )
        elif code == "style_cosmetic":
            out.notes.extend(said.get("cosmetic", []) or [explain_check(check).text])
        elif check.severity != "error":
            out.notes.append(explain_check(check).text)
    return out


def check_cell_edit(layer: str | None, typed: str, *, current: str | None = None) -> EditCheck:
    """The proposal checks on `typed` for a maths cell of a table: the cleaning, the
    LaTeX and MathML checks, the spoken version and the cross-checks against the PDF's
    text in that cell (`layer`: `table_step.cell_text_layer`, None when it is not
    known), as an equation's edit gets them. `current` is the LaTeX the cell has. A
    cell is inline maths, and a short one, so the difference is said in the checks' own
    sentences (the equation compare is about a whole equation's picture)."""
    from readablestem.pipeline import equation_step

    latex = equation_step.clean_latex(typed)
    out = EditCheck(latex=latex, mathml=None, speech="", checks=[], display="inline")
    out.has_text_layer = bool(layer and any(ch.isalnum() for ch in layer))
    if not latex:
        return out
    out.unchanged = current is not None and _same(latex, current)
    stand_in = Element(
        id="cell", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0,
        text=layer or None, display="inline",
    )  # fmt: skip
    out.mathml, out.speech, out.checks = evaluate(latex, "inline", stand_in)
    if out.mathml is None:
        out.blocking = latex_problems(latex, out.failed)
        return out
    for check in out.failed:
        said = explain_check(check).text
        if (check.code or "") in MEANING_CODES:
            if said not in out.meaning:
                out.meaning.append(said)
        elif check.severity != "error" and said not in out.notes:
            out.notes.append(said)
    return out


def _same(typed: str, stored: str) -> bool:
    return " ".join(typed.split()) == " ".join(stored.split())


def evaluate(
    latex: str, display: str, element: Element | None
) -> tuple[str | None, str, list[Check]]:
    """(MathML or None, speech, checks) for already cleaned LaTeX: the equation step's
    `_evaluate` without a model answer, step by step in the same order with the same
    check names and codes."""
    from readablestem.pipeline import equation_step, notation_memory, signs, validate

    # The course notation in force (web.notation.in_force), as the step reads it: its
    # bold letters are evidence for the style check, and its meanings read the maths.
    entries = notation_memory.entries_in_force()
    course_bold = notation_memory.course_bold_letters(entries)
    spoken = notation_memory.readings(entries)
    checks = list(validate.check_latex(latex))
    if validate.has_errors(checks):
        return None, "", checks
    try:
        mathml = validate.safe_latex_to_mathml(latex, display)
    except ValueError:  # LatexConversionError and latex2mathml's own errors
        failed = Check(name="latex.converts", passed=False, code="conversion_failed")
        return None, "", [*checks, failed]
    mathml_checks = list(validate.check_mathml(mathml))
    checks = [*checks, Check(name="latex.converts", passed=True), *mathml_checks]
    if validate.has_errors(mathml_checks):
        return None, "", checks
    speech, speech_check = _speech(mathml, spoken)
    checks.append(speech_check)
    text = element.text if element is not None else None
    regular = element.regular_letters if element is not None else None
    mismatch = False
    differ = equation_step.text_layer_symbols_differ(text, mathml)
    if differ is not None:
        similarity = equation_step.text_layer_similarity(text, mathml)
        mismatch = similarity is not None and similarity < equation_step.TEXT_LAYER_MISMATCH_BELOW
        symbols = "text_layer_mismatch" if mismatch else ("symbol_mismatch" if differ else None)
        checks.append(
            Check(
                name="equation.text_layer", passed=symbols is None, severity="warning", code=symbols
            )
        )
    if differ is False:
        checks.extend(equation_step.text_layer_number_case_checks(text, mathml))
    style = equation_step.text_layer_style(
        text, mathml, regular_letters=regular, course_bold=course_bold
    )
    if style is not None:
        code = (
            None
            if style == "match"
            else ("style_mismatch" if style == "meaning" else "style_cosmetic")
        )
        checks.append(
            Check(
                name="equation.text_layer_style", passed=code is None, severity="warning", code=code
            )
        )
    compared = signs.compare_signs(text, mathml) if differ is not None and not mismatch else None
    if compared is not None:
        checks.append(
            Check(
                name="equation.text_layer_signs",
                passed=not compared.mismatch,
                severity="warning",
                code="sign_mismatch" if compared.mismatch else None,
            )
        )
    return mathml, speech, checks


def _speech(mathml: str, readings: dict[str, str] | None = None) -> tuple[str, Check]:
    """The step's speech check (with the course's readings, "vector d", as the step reads
    them). Speech is a reading aid: any failure is a note."""
    from readablestem.pipeline import speech

    try:
        # Without course readings, the one-argument call as before (as the step does).
        spoken = (
            speech.mathml_to_speech(mathml, readings=readings)
            if readings
            else speech.mathml_to_speech(mathml)
        )
    except Exception as exc:
        logger.warning("speech_failed", extra={"exc_type": type(exc).__name__})
        return "", Check(
            name="speech.generated", passed=False, severity="warning", code="speech_failed"
        )
    code = None if spoken else "speech_empty"
    return spoken, Check(
        name="speech.generated", passed=code is None, severity="warning", code=code
    )


# --------------------------------------------------------------------------- words


def latex_problems(latex: str, failed: list[Check]) -> list[str]:
    """One sentence per failed error-level check, naming the problem where the LaTeX
    shows it ("It has one more { than }.", "\\foo is not a command this tool supports.").
    No check codes: those are for bug reports, not for the person typing."""
    out: list[str] = []
    for check in failed:
        if check.severity != "error":
            continue
        code = check.code or ""
        if code == "unbalanced_braces":
            sentence = _braces(latex)
        elif code == "unknown_command":
            sentence = _unknown_commands(latex) or explain_check(check).text
        elif code == "forbidden_command":
            sentence = _forbidden_commands(latex) or explain_check(check).text
        elif code == "unknown_environment":
            sentence = _unknown_environments(latex) or explain_check(check).text
        elif code == "conversion_failed":
            sentence = (
                "It could not be turned into an equation, often because of a missing "
                "argument or a typo."
            )
        else:
            sentence = explain_check(check).text
        if sentence not in out:
            out.append(sentence)
    return out or ["It could not be checked."]


def _count(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _braces(latex: str) -> str:
    from readablestem.pipeline.validate import _tokens

    opened = closed = depth = 0
    early = False
    for kind, _, _ in _tokens(latex):
        if kind == "open":
            opened += 1
            depth += 1
        elif kind == "close":
            closed += 1
            depth -= 1
            early = early or depth < 0
    if opened > closed:
        return f"It has {_count(opened - closed)} more {{ than }}: add the missing }}."
    if closed > opened:
        return f"It has {_count(closed - opened)} more }} than {{: add the missing {{."
    if early:
        return "A } comes before the { it closes."
    return "A { has no matching } (or the other way round)."


def _names(found: list[str]) -> str:
    unique = list(dict.fromkeys(found))[:MAX_NAMED]
    if len(unique) == 1:
        return unique[0]
    return ", ".join(unique[:-1]) + " and " + unique[-1]


def _unknown_commands(latex: str) -> str | None:
    from readablestem.pipeline.validate import (
        ALLOWED_COMMANDS,
        ALLOWED_CONTROL_SYMBOLS,
        FORBIDDEN_COMMANDS,
        _tokens,
    )

    found = [
        "\\" + value
        for kind, value, _ in _tokens(latex)
        if (kind == "word" and value not in ALLOWED_COMMANDS and value not in FORBIDDEN_COMMANDS)
        or (kind == "symbol" and value and value not in ALLOWED_CONTROL_SYMBOLS)
    ]
    if not found:
        return None
    many = len(set(found)) > 1
    verb = "are not commands" if many else "is not a command"
    return f"{_names(found)} {verb} this tool supports: check for a typo, or write it another way."


def _forbidden_commands(latex: str) -> str | None:
    from readablestem.pipeline.validate import FORBIDDEN_COMMANDS, _tokens

    found = [
        "\\" + v for kind, v, _ in _tokens(latex) if kind == "word" and v in FORBIDDEN_COMMANDS
    ]
    if not found:
        return None
    return f"{_names(found)} could read files or change settings, so it is never allowed."


def _unknown_environments(latex: str) -> str | None:
    from readablestem.pipeline.validate import ALLOWED_ENVIRONMENTS, _environment_name, _tokens

    found = [
        name
        for kind, value, end in _tokens(latex)
        if kind == "word"
        and value == "begin"
        and (name := _environment_name(latex, end)) is not None
        and name not in ALLOWED_ENVIRONMENTS
    ]
    if not found:
        return None
    return f"The {_names(found)} environment is not supported here."


def meaning_message(check: EditCheck) -> str:
    """The refusal of an edit that differs from the PDF where it changes the meaning."""
    return (
        "Your LaTeX differs from the PDF where it changes the meaning: "
        + " ".join(check.meaning)
        + " Correct it, or choose Use my edit anyway if you are sure."
    )


def blocking_message(check: EditCheck) -> str:
    return "Your LaTeX was not saved. " + " ".join(check.blocking)
