"""Equation step: crop image -> LaTeX -> validated MathML -> speech.

Routing: simple inline candidates (inline, text layer <= 25 characters) start on the
cheap model; display equations and anything the cheap model is unsure about (confidence
< settings.auto_accept_confidence, a failed error-level check, or any failed text-layer
check) escalate to the main model. The first attempt goes through
`models.router.call_with_fallback`, so an outage on one model falls back to the other.
The escalation is one main-model call with no fallback: the main model's fallback is the
cheap model, which has already answered this exact request, so falling back would pay
for the same answer twice. If the escalation fails, the cheap answer stands. Every
attempt's `ModelCall` ends up on the fix.

Nothing the model says is trusted as-is. Its LaTeX goes through `validate.check_latex`,
`validate.safe_latex_to_mathml` and `validate.check_mathml`, and is then cross-checked
against the PDF text layer: the letters, digits and Greek letters in the text layer
(when the PDF has one) and in the transcription should be the same. Any difference (one
wrong digit, a misread Greek letter, a dropped variable) is a failed warning check that
needs a decision; a strong mismatch, the cheapest signal of a hallucinated
transcription, also lowers the confidence. The text layer is deliberately not sent to
the model, so the cross-check stays independent. Letter styles are compared too
(`text_layer_style`): a bold vector read as a plain scalar (or a letter known to be
plain read as a bold vector), or a double-struck, script or fraktur letter read as a
plain one, is meaning-level and is treated like a symbol mismatch; any other style
difference (the bold subscript of a bold symbol, a bold digit, a double-struck R from
a font layout does not know) is cosmetic, an optional note only. Signs and relations are
compared as well (`signs.compare_signs`, check `equation.text_layer_signs`): a minus
that was dropped or added, ± read as +, ≤ read as <, = read as ≈, a dot product read
as a cross product. That is meaning-level too, and asked only when the
letters and digits mostly agree with the text layer (so it can be trusted). When the
letters and digits agree exactly, two more checks run (`text_layer_number_case_checks`):
the digits must make the same numbers (36 is not 63, 9.81 is not 98.1) and the capitals
must be the same capitals (W/g is not w/g); either is meaning-level too. Any failed
text-layer check on a cheap-model answer, a cosmetic one included, escalates to the main
model.

Course notation (`pipeline.notation_memory`, with `FEATURE_NOTATION_MEMORY` on and the
job's course list in the run's options): the list goes to the model as a fenced block
of the untrusted context, under a prompt version of its own (`NOTATION_PROMPT_NAME`;
without entries nothing changes); a letter the course writes bold, in a PDF whose text
layer shows no bold at all, read as a plain variable is the meaning-level
`style_mismatch`; speech reads such a letter by its meaning ("vector d"); and a bold
letter layout found in running text gets its fix without a model call.

A picture of an equation (`equation_images`, `Element.reasons`) has no text layer, so
nothing can confirm the reading: it gets the failed check `equation.no_text_layer`, not
a reason to ask the main model again, so a usable reading is NEEDS_REVIEW (review
"required") however sure the model is. The model may still answer that the picture is not
an equation (rule 2); the picture stays in the output, flagged.

Status rules, in order:
1. no parsed output from any attempt -> FAILED, reason "model_<outcome>";
2. the model says it is not an equation -> ABSTAINED "not_equation";
3. an empty transcription below the abstain threshold -> ABSTAINED "low_confidence"
   (the model abstained, as the prompt asks, rather than producing invalid LaTeX);
4. LaTeX that fails an error-level check after escalation -> FAILED "invalid_latex";
5. confidence < settings.abstain_confidence -> ABSTAINED "low_confidence";
6. confidence >= settings.auto_accept_confidence and no failed check other than a note
   (`NOTE_CODES`: a cosmetic style difference, no speech) -> ACCEPTED, review "none",
   or "optional" with notes;
7. otherwise NEEDS_REVIEW (review "required").
Rules 1-5 are review "required" as well. Only ACCEPTED and NEEDS_REVIEW fixes carry a
proposal (`Fix.equation`). BudgetExceeded propagates: the orchestrator decides what
happens to the rest of the document.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

import defusedxml.ElementTree as DET
from pydantic import BaseModel, ConfigDict, Field

from a11y_stem.config import Settings
from a11y_stem.core.mathchars import STYLE_GROUPS, base_char, char_variant
from a11y_stem.core.types import Check, Element, EquationFix, Fix, Kind, ModelCall, Status
from a11y_stem.models import router
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt
from a11y_stem.pipeline import equation_images, layout, notation_memory
from a11y_stem.pipeline.signs import compare_signs
from a11y_stem.pipeline.speech import SpeechError, mathml_to_speech
from a11y_stem.pipeline.text_layer_compare import compare_case, numbers_differ
from a11y_stem.pipeline.validate import (
    LatexConversionError,
    check_latex,
    check_mathml,
    escape_percent_signs,
    has_errors,
    safe_latex_to_mathml,
)

PROMPT_NAME = "equation"
# "<name>-<sha8 of the prompt text>": changes whenever the prompt file changes.
PROMPT_VERSION = load_prompt(PROMPT_NAME).version
# The same instructions plus how to read the course notation block; sent only when the
# conversion has course notation, so runs without it keep PROMPT_VERSION.
NOTATION_PROMPT_NAME = "equation_notation"
NOTATION_PROMPT_VERSION = load_prompt(NOTATION_PROMPT_NAME).version

# A cheap answer with passing checks and a confidence from here to the accept bar is not
# asked again (`_needs_escalation`).
SKIP_ESCALATION_FROM = 0.80
# ... and is then accepted as it stands (review "optional", with the note `single_reading`)
# instead of waiting for a decision. Without this the skip would turn every such answer into
# a required decision, because the cheap model's 0.80 is below the accept bar: on the
# development set (Claude, 2026-10-14) 8 of 8 second readings in the band gave the same
# MathML, and a decision costs a person more than the call it replaces. Set False to keep the
# status rules as they were (the answer then needs a decision).
ACCEPT_SINGLE_READING = True
SHORT_INLINE_CHARS = 25  # inline candidates up to this many text-layer chars start cheap
MAX_OUTPUT_TOKENS = 2048  # LaTeX up to 2000 chars plus reasoning tokens
# A model that thinks within its own allowance (claude: `pricing.Price.thinking_tokens`)
# needs the answer's room only: 2000 characters of LaTeX are about 700 tokens.
ANSWER_ONLY_MAX_OUTPUT_TOKENS = 1024


def output_limit(model: str) -> int:
    """The `max_output_tokens` to ask `model` for."""
    from a11y_stem.models.pricing import price_for

    return ANSWER_ONLY_MAX_OUTPUT_TOKENS if price_for(model).thinking_tokens else MAX_OUTPUT_TOKENS


# Text-layer cross-check: only with enough symbols to compare, and a "strong mismatch"
# is a Dice similarity of the two symbol bags below this.
TEXT_LAYER_MIN_SYMBOLS = 3
TEXT_LAYER_MISMATCH_BELOW = 0.5

Display = Literal["block", "inline"]


class EquationOutput(BaseModel):
    """Structured output the model must return (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    is_equation: bool = Field(
        description="True if the image shows mathematics; false for prose, pictures or nothing."
    )
    latex: str = Field(
        description="Math-mode LaTeX for exactly what the image shows; empty if not an equation."
    )
    confidence: float = Field(
        ge=0, le=1, description="Probability that the LaTeX matches the image symbol for symbol."
    )


# ----------------------------------------------------------------- model calls


def _call(
    client: VisionClient, request: VisionRequest, schema: type[T], settings: Settings
) -> tuple[T | None, list[ModelCall]]:
    """One routed call (model, then its fallback). Looked up on the module at call time
    so tests can substitute the router."""
    return router.call_with_fallback(client, request, schema, settings=settings)


def _call_once(
    client: VisionClient, request: VisionRequest, schema: type[T]
) -> tuple[T | None, list[ModelCall]]:
    """One call to request.model with no fallback (the escalation). The client still
    reserves spend first and raises BudgetExceeded when refused."""
    parsed, call = client.run(request, schema)
    return parsed, [call]


def _first_model(element: Element, settings: Settings) -> str:
    text = (element.text or "").strip()
    if element.display == "inline" and len(text) <= SHORT_INLINE_CHARS:
        return settings.cheap_model
    if settings.equation_display_first_cheap:
        return settings.cheap_model
    return settings.main_model


# ------------------------------------------------------------------ evaluation


_WRAPPERS = [
    re.compile(r"^\$\$(.*)\$\$$", re.S),
    re.compile(r"^\$(.*)\$$", re.S),
    re.compile(r"^\\\[(.*)\\\]$", re.S),
    re.compile(r"^\\\((.*)\\\)$", re.S),
    re.compile(
        r"^\\begin\{(?:equation\*?|displaymath)\}(.*)\\end\{(?:equation\*?|displaymath)\}$", re.S
    ),
]
_DISPLAYSTYLE = re.compile(r"\\(?:displaystyle|textstyle)(?![A-Za-z])")


# A script with nothing before it: at the start, or after a sign or an opening bracket. LaTeX
# hangs it on the previous symbol, so the numbers of a nuclide after a plus ("+ ^{4}_{2}He")
# become the plus's own; an empty group gives it an empty base (the nuclide's prefix scripts).
_BARE_SCRIPT = re.compile(r"(?:^|(?<=[+\-=(\[,<>]))(\s*)(?=[\^_])")


def clean_latex(latex: str) -> str:
    r"""Remove wrappers the prompt forbids but models still add ($...$, \[...\],
    equation environments, \displaystyle), escape bare `%` signs, and give a script that
    has nothing before it an empty base (`^{14}_{6}C` -> `{}^{14}_{6}C`, `+ ^{4}_{2}He` ->
    `+ {}^{4}_{2}He`). Never changes the mathematics: a bare `%` in one transcribed equation
    is a percent sign, but the converter would treat it as a comment and silently drop the
    rest of the line; a script with no base belongs to no symbol, and the converter would
    give it to the sign before it."""
    text = latex.strip()
    for pattern in _WRAPPERS:
        match = pattern.match(text)
        if match:
            text = match.group(1).strip()
    text = _DISPLAYSTYLE.sub("", text)
    text = text.removesuffix("\\\\").strip()
    text = _BARE_SCRIPT.sub(lambda m: m.group(1) + "{}", text)
    return escape_percent_signs(text)


# Spacing a model may leave before a sentence mark: blanks, ~, \, \; \: \! \  and quads.
_TRAILING_SPACING = re.compile(r"(?:\s|~|\\[,;:! ]|\\q?quad(?![A-Za-z]))+$")


def strip_trailing_punctuation(latex: str) -> str:
    r"""Drop sentence punctuation (, . ; : ?) from the end of an inline transcription.

    The paragraph text keeps the mark that follows an inline equation's marker, so if
    the model also transcribes it (the crop showed part of it) the output reads
    "d0, ,". Spacing left in front of the mark goes with it. A mark after a backslash is
    a command (\, \; \: are spaces) and stays; so does LaTeX that would be left empty.
    "!" stays: after maths it is a factorial. Display equations keep their punctuation:
    it is part of the line the reader sees.
    """
    text = latex.rstrip()
    while text and text[-1] in ",.;:?":
        backslashes = len(text[:-1]) - len(text[:-1].rstrip("\\"))
        if backslashes % 2:
            break  # "\," is a thin space, not a comma
        stripped = _TRAILING_SPACING.sub("", text[:-1])
        if not stripped.strip():
            break
        text = stripped
    return text


# Typesetting artefacts that are not symbols: the dotless i and j a LaTeX accent sits on
# (\hat{\imath} is the unit vector i-hat) are i and j. And TeX's capital Delta: the Adobe
# Glyph List maps the glyph name "Delta" to U+2206 INCREMENT, so every pdfTeX \Delta is
# U+2206 in the text layer, which NFKC keeps and which is not a letter (Omega and mu come
# out as U+2126 and U+00B5, which NFKC already turns into the Greek letters).
_DOTLESS = str.maketrans({"\u0131": "i", "\u0237": "j", "\u2206": "\u0394"})


def _symbol_bag(text: str) -> Counter[str]:
    """Letters, digits and Greek letters, case-folded; math italic glyphs map to ASCII.

    Modifier letters (Unicode category Lm, such as the U+02C6 circumflex a LaTeX hat
    accent leaves in a text layer) are accents, not symbols, so they are dropped."""
    folded = unicodedata.normalize("NFKC", text).translate(_DOTLESS).casefold()
    return Counter(ch for ch in folded if ch.isalnum() and unicodedata.category(ch) != "Lm")


def _mathml_symbols(mathml: str) -> Counter[str]:
    root = DET.fromstring(mathml, forbid_dtd=True)
    return _symbol_bag(" ".join(root.itertext()))


# A meaning-level difference from the text layer (a bold d, a vector, read as a plain d,
# a scalar; a sign or relation that differs: `signs.compare_signs`) scales confidence by
# this factor, once however many there are: enough to escalate a cheap-model answer and
# to keep the fix for a person's decision, without pushing a 0.95 answer below the
# abstain bar (two factors would, and the proposal would be lost). A cosmetic difference
# changes nothing.
MEANING_MISMATCH_FACTOR = 0.6
# Failed checks that are only notes: the fix is still ACCEPTED (review="optional").
# A cosmetic style difference, and a reading aid for the review screen that could not
# be made (the MathML in the output is unaffected).
SPEECH_CODES = frozenset({"speech_failed", "speech_empty"})
# The cheap answer was not read a second time (see `SKIP_ESCALATION_FROM`): a note only.
SINGLE_READING_CODE = "single_reading"
NOTE_CODES = frozenset({"style_cosmetic", SINGLE_READING_CODE, *SPEECH_CODES})
# A picture of an equation: no text layer to check the reading against, so a person decides.
NO_TEXT_LAYER_CODE = "no_text_layer"
# Layout found a fraction bar against the equation's region with the bar's other side
# holding glyphs of something else (`layout.REASON_HALF_FRACTION`): the crop shows half of
# a fraction, so whatever is read from it cannot be trusted. Never accepted without a
# decision, and its confidence is held under the auto-accept bar.
HALF_FRACTION_CODE = "half_fraction"
_MATHML_TOKENS = {"mi", "mn", "mo", "mtext", "ms"}
# Children after the first are scripts (sub/superscripts, limits, a root's index).
_SCRIPTED = {"msub", "msup", "msubsup", "munder", "mover", "munderover", "mmultiscripts", "mroot"}
PLAIN = "plain"
BOLD = "bold"
# Styles that make a different symbol of a letter (double-struck R, the reals; script L,
# a Lagrangian; fraktur g, a Lie algebra), unlike sans-serif or monospace: losing one
# changes what is read.
LETTER_STYLES = frozenset({"double-struck", "script", "fraktur"})

StyleVerdict = Literal["match", "meaning", "cosmetic"]


@dataclass
class _LetterStyles:
    """How one letter (or digit) is styled on one side, occurrence by occurrence."""

    bold: int = 0  # occurrences in a bold style
    plain: int = 0  # occurrences in no bold style
    other: set[str] = field(default_factory=set)  # non-bold style groups (double-struck...)
    styled: Counter[str] = field(default_factory=Counter)  # occurrences per `other` group
    known_plain: bool = False  # PDF only: a plain occurrence is known to be plain


@dataclass
class _MathLetters:
    """A letter's occurrences in the transcription: as a variable (a one-letter token
    that is not part of a bold symbol's label) or anywhere else (words like "avg", and
    the sub/superscripts of a bold base: the f of a bold d_f)."""

    var_bold: int = 0
    var_plain: int = 0
    other_bold: int = 0
    other_plain: int = 0
    styles: set[str] = field(default_factory=set)  # non-bold style groups
    styled: Counter[str] = field(default_factory=Counter)  # occurrences per `styles` group


def _group(variant: str | None) -> str:
    return STYLE_GROUPS.get(variant or "", PLAIN)


def _layer_letters(text_layer: str, regular: str) -> dict[str, _LetterStyles]:
    out: dict[str, _LetterStyles] = {}
    for ch in text_layer:
        if not ch.isalnum():
            continue
        variant = char_variant(ch)
        base = base_char(ch)
        entry = out.setdefault(base, _LetterStyles())
        group = _group(variant)
        if group == BOLD:
            entry.bold += 1
            continue
        entry.plain += 1
        if group != PLAIN:
            entry.other.add(group)
            entry.styled[group] += 1
        # Explicitly plain: a math italic code point (Chromium and Word write them), or a
        # glyph layout found in a regular font with a bold face (`Element.regular_letters`).
        if variant == "italic" or base in regular:
            entry.known_plain = True
    return out


def _has_bold(elem: Any, inherited: str | None) -> bool:
    """Does this MathML subtree show a bold letter or digit?"""
    variant = elem.get("mathvariant") or inherited
    if elem.tag.rsplit("}", 1)[-1] in _MATHML_TOKENS:
        chars = (ch for ch in elem.text or "" if ch.isalnum())
        return any(_group(char_variant(ch) or variant) == BOLD for ch in chars)
    return any(_has_bold(child, variant) for child in elem)


def _mathml_letters(mathml: str) -> dict[str, _MathLetters]:
    """Letters of a MathML tree with their styles (mathvariant inherited, as MathML
    specifies, or carried by the code point) and whether each occurrence is a variable.

    The scripts of a bold base (bold d_f, v_avg, d_0) are its label: a bold or plain f
    there is the same symbol. The scripts of any other base hold variables of their own:
    the bold k and r of e^{i k.r}, the bold x of lim_{x -> 0}, a sum over a bold k are
    vectors."""
    out: dict[str, _MathLetters] = {}

    def walk(elem: Any, inherited: str | None, label: bool) -> None:
        variant = elem.get("mathvariant") or inherited
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag in _MATHML_TOKENS:
            chars = [ch for ch in elem.text or "" if ch.isalnum()]
            variable = not label and len(chars) == 1
            for ch in chars:
                entry = out.setdefault(base_char(ch), _MathLetters())
                group = _group(char_variant(ch) or variant)
                if group != BOLD and group != PLAIN:
                    entry.styles.add(group)
                    entry.styled[group] += 1
                if group == BOLD:
                    if variable:
                        entry.var_bold += 1
                    else:
                        entry.other_bold += 1
                elif variable:
                    entry.var_plain += 1
                else:
                    entry.other_plain += 1
            return
        children = list(elem)
        bold_base = tag in _SCRIPTED and bool(children) and _has_bold(children[0], variant)
        for index, child in enumerate(children):
            walk(child, variant, label or (bold_base and index > 0))

    walk(DET.fromstring(mathml, forbid_dtd=True), None, False)
    return out


def _letter_verdict(pdf: _LetterStyles, got: _MathLetters, letter: str) -> StyleVerdict:
    """Meaning-level for
    - a variable (a one-letter token that is not a bold symbol's label, `_mathml_letters`)
      whose weight differs: a bold letter of the PDF read as a plain variable (a vector
      read as a scalar), or a letter the PDF shows plain in a known regular font read as
      a bold variable;
    - a double-struck, script or fraktur letter of the PDF (its own code point says so,
      as Chromium and Word write them) read without that style: the reals read as a
      variable R, a Lagrangian as L.
    Everything else that differs is cosmetic: digits, the labels of a bold symbol, words,
    and styles the text layer cannot show (double-struck R from MSBM is a plain "R"
    there, so `\\mathbb{R}` for it adds a style rather than losing one)."""
    if (pdf.other & LETTER_STYLES) - got.styles:
        return "meaning"
    differs = pdf.other != got.styles or pdf.styled != got.styled
    # Counted per occurrence too, for a letter the PDF shows both ways (the reals and a
    # variable R in one equation): one more plain R and one fewer reals is a lost style,
    # one more reals and one fewer plain R a variable read as the reals.
    pdf_plain = pdf.plain - pdf.styled.total()
    got_plain = got.var_plain + got.other_plain - got.styled.total()
    for style in LETTER_STYLES:
        if pdf.styled[style] > got.styled[style] and got_plain > pdf_plain:
            return "meaning"
        if got.styled[style] > pdf.styled[style] and pdf_plain > got_plain and pdf.known_plain:
            return "meaning"
    if letter.isalpha():
        lost = pdf.bold - (got.var_bold + got.other_bold)  # bold occurrences not bold now
        if lost > 0:
            # Plain PDF occurrences fill scripts and words first, so a plain variable left
            # over must be a bold one that lost its weight (when unsure, ask).
            spare_variables = got.var_plain - max(0, pdf.plain - got.other_plain)
            if spare_variables > 0:
                return "meaning"
            differs = True
        gained = got.var_bold > pdf.bold  # more bold variables than bold PDF letters
        unmatched_plain = pdf.plain > got.var_plain + got.other_plain  # a plain one went
        if got.var_bold and (not pdf.bold or (gained and unmatched_plain)):
            # A letter the PDF shows plain became a bold variable (a scalar, or a vector's
            # component u_i, read as a vector). Only meaning-level when the PDF is known
            # to show bold where there is bold; a text layer may also just miss glyphs.
            if pdf.known_plain:
                return "meaning"
            differs = True
        if got.other_bold > max(0, pdf.bold - got.var_bold):
            differs = True  # a bold script the PDF does not show bold
    elif bool(pdf.bold) != bool(got.var_bold + got.other_bold):
        differs = True  # a digit bold on one side only
    return "cosmetic" if differs else "match"


def text_layer_style(
    text_layer: str | None,
    mathml: str,
    *,
    regular_letters: str | None = None,
    course_bold: frozenset[str] = frozenset(),
) -> StyleVerdict | None:
    """Compare the letter styles of the PDF text layer and the transcription.

    None when the text layer says nothing about style: no styled letter and no letter
    known to be plain (most PDFs without bold maths; a font that does not mark bold).
    Otherwise "match", "meaning" (a weight difference on a variable, or a double-struck,
    script or fraktur letter lost, which changes what a student reads: see
    `_letter_verdict`) or "cosmetic" (any other difference). Only the letters both sides
    contain are compared: the text layer may miss or reorder glyphs.

    `course_bold`: letters the course notation writes bold
    (`notation_memory.course_bold_letters`). When the text layer shows no bold at all,
    such a letter it does not show to be plain, read as a plain variable and bold
    nowhere, is "meaning" too: the course says the letter is bold and nothing in the PDF
    says this one is not (`_course_bold_unseen`). Without course letters nothing changes.
    """
    if not text_layer:
        return None
    pdf = _layer_letters(text_layer, regular_letters or "")
    informative = any(s.bold or s.other or s.known_plain for s in pdf.values())
    expected = _course_bold_unseen(pdf, course_bold)
    if not informative and not expected:
        return None
    got = _mathml_letters(mathml)
    verdicts: set[StyleVerdict] = set()
    if informative:
        verdicts = {_letter_verdict(pdf[ch], got[ch], ch) for ch in pdf.keys() & got.keys()}
    for letter in expected & got.keys():
        found = got[letter]
        if found.var_plain and not (found.var_bold or found.other_bold):
            verdicts.add("meaning")
    if "meaning" in verdicts:
        return "meaning"
    return "cosmetic" if "cosmetic" in verdicts else "match"


def _course_bold_unseen(pdf: dict[str, _LetterStyles], course_bold: frozenset[str]) -> set[str]:
    """Letters the course writes bold whose weight this text layer cannot show: it shows
    no bold anywhere (a font that does not mark bold), and the letter is not known plain
    (a regular font with a bold face, a math italic code point)."""
    if not course_bold or any(s.bold for s in pdf.values()):
        return set()
    return {ch for ch in course_bold if ch in pdf and not pdf[ch].known_plain}


def text_layer_style_mismatch(text_layer: str | None, mathml: str) -> bool | None:
    """Any style difference at all, meaning-level or cosmetic (None: no style info)."""
    verdict = text_layer_style(text_layer, mathml)
    return None if verdict is None else verdict != "match"


def text_layer_similarity(text_layer: str | None, mathml: str) -> float | None:
    """Dice similarity of the symbol bags of the PDF text layer and the MathML.

    None when there is no text layer, or too few symbols in it to judge.
    """
    if not text_layer:
        return None
    expected = _symbol_bag(text_layer)
    if sum(expected.values()) < TEXT_LAYER_MIN_SYMBOLS:
        return None
    got = _mathml_symbols(mathml)
    total = sum(expected.values()) + sum(got.values())
    return 2 * sum((expected & got).values()) / total if total else None


def text_layer_symbols_differ(text_layer: str | None, mathml: str) -> bool | None:
    """Do the letters, digits and Greek letters of the PDF text layer and the MathML
    differ at all (as multisets: one wrong digit, one misread Greek letter, a dropped
    variable)? None when there is no text layer or it has no such symbol.

    Stricter than `text_layer_similarity`. On 181 real model answers with a text layer
    (the gold sample, the document eval and two converted documents, 2026-10-08) the
    bags differed for none once accents (modifier letters) were dropped, so a difference
    is rare and worth a decision.
    """
    if not text_layer:
        return None
    expected = _symbol_bag(text_layer)
    if not expected:
        return None
    return expected != _mathml_symbols(mathml)


def text_layer_number_case_checks(text_layer: str | None, mathml: str) -> list[Check]:
    """The two checks the symbol bag cannot make, for a pair whose bags already agree (the
    caller asks `text_layer_symbols_differ` first and passes only a `False`):

    - `equation.text_layer_numbers` / `number_mismatch`: the digits are the same but not
      the numbers (36 for 63, 9.81 for 98.1, 10^23 for 10^32). `text_layer_compare`.
    - `equation.text_layer_case` / `case_mismatch`: the letters are the same but a capital
      is small or the other way round (W/g for w/g, S for s).

    Each is a failed warning with a meaning-level code, like `symbol_mismatch`. A check
    that cannot be made (no number in the text layer, a search past its bound) is left
    out, not passed."""
    checks: list[Check] = []
    numbers = numbers_differ(text_layer, mathml)
    if numbers is not None:
        checks.append(
            Check(
                name="equation.text_layer_numbers",
                passed=not numbers.differ,
                severity="warning",
                code="number_mismatch" if numbers.differ else None,
            )
        )
    if text_layer:
        wrong_case = bool(compare_case(text_layer, mathml))
        checks.append(
            Check(
                name="equation.text_layer_case",
                passed=not wrong_case,
                severity="warning",
                code="case_mismatch" if wrong_case else None,
            )
        )
    return checks


@dataclass
class _Candidate:
    """One model attempt (with its fallback) and what the validators made of it."""

    output: EquationOutput | None
    calls: list[ModelCall]
    latex: str = ""
    mathml: str | None = None
    speech: str = ""
    checks: list[Check] = field(default_factory=list)
    confidence: float | None = None

    @property
    def valid(self) -> bool:
        """LaTeX converted to MathML and no error-level check failed."""
        return self.mathml is not None and not has_errors(self.checks)

    @property
    def usable(self) -> bool:
        return self.output is not None and (not self.output.is_equation or self.valid)


def _convert(latex: str, display: Display) -> tuple[str | None, list[Check]]:
    checks = check_latex(latex)
    if has_errors(checks):
        return None, checks
    try:
        mathml = safe_latex_to_mathml(latex, display)
    except LatexConversionError:
        return None, [*checks, Check(name="latex.converts", passed=False, code="conversion_failed")]
    mathml_checks = check_mathml(mathml)
    checks = [*checks, Check(name="latex.converts", passed=True), *mathml_checks]
    return (None if has_errors(mathml_checks) else mathml), checks


def _speech(mathml: str, spoken: dict[str, str] | None = None) -> tuple[str, Check]:
    try:
        # Without course readings, the one-argument call as before.
        speech = mathml_to_speech(mathml, readings=spoken) if spoken else mathml_to_speech(mathml)
    except SpeechError:
        return "", Check(
            name="speech.generated", passed=False, severity="warning", code="speech_failed"
        )
    code = None if speech else "speech_empty"
    return speech, Check(
        name="speech.generated", passed=code is None, severity="warning", code=code
    )


def _evaluate(
    output: EquationOutput | None,
    calls: list[ModelCall],
    element: Element,
    display: Display,
    *,
    course_bold: frozenset[str] = frozenset(),
    spoken: dict[str, str] | None = None,
) -> _Candidate:
    if output is None:
        return _Candidate(output, calls)
    candidate = _Candidate(output, calls, confidence=output.confidence)
    if not output.is_equation:
        return candidate
    candidate.latex = clean_latex(output.latex)
    if display == "inline":
        candidate.latex = strip_trailing_punctuation(candidate.latex)
    candidate.mathml, candidate.checks = _convert(candidate.latex, display)
    if candidate.mathml is None:
        return candidate
    candidate.speech, speech_check = _speech(candidate.mathml, spoken)
    candidate.checks.append(speech_check)
    meaning = False  # a meaning-level difference from the text layer
    mismatch = False  # the strong symbol mismatch: the text layer is no use for signs
    differ = text_layer_symbols_differ(element.text, candidate.mathml)
    if differ is not None:
        similarity = text_layer_similarity(element.text, candidate.mathml)
        mismatch = similarity is not None and similarity < TEXT_LAYER_MISMATCH_BELOW
        # A strong mismatch (likely a hallucination) also lowers the confidence; any
        # other difference in the symbols (a wrong digit, a misread Greek letter) keeps
        # it but still needs a decision.
        symbols = "text_layer_mismatch" if mismatch else ("symbol_mismatch" if differ else None)
        candidate.checks.append(
            Check(
                name="equation.text_layer",
                passed=symbols is None,
                severity="warning",
                code=symbols,
            )
        )
        if mismatch and similarity is not None:
            # scale towards half confidence as the symbol overlap goes to zero
            candidate.confidence = output.confidence * (0.5 + 0.5 * similarity)
    if differ is False:
        # The letters and digits agree as multisets: are the numbers the same numbers,
        # and the capitals the same capitals?
        extra = text_layer_number_case_checks(element.text, candidate.mathml)
        candidate.checks.extend(extra)
        meaning = any(not c.passed for c in extra)
    style = text_layer_style(
        element.text,
        candidate.mathml,
        regular_letters=element.regular_letters,
        course_bold=course_bold,
    )
    if style is not None:
        style_code = (
            None
            if style == "match"
            else ("style_mismatch" if style == "meaning" else "style_cosmetic")
        )
        candidate.checks.append(
            Check(
                name="equation.text_layer_style",
                passed=style_code is None,
                severity="warning",
                code=style_code,
            )
        )
        meaning = meaning or style == "meaning"
    # Signs and relations: only against a text layer whose letters and digits mostly
    # agree with the transcription (it has some, and no strong mismatch), so a garbled
    # layer or a different equation never adds a sign flag on top.
    signs = (
        compare_signs(element.text, candidate.mathml)
        if differ is not None and not mismatch
        else None
    )
    if signs is not None:
        candidate.checks.append(
            Check(
                name="equation.text_layer_signs",
                passed=not signs.mismatch,
                severity="warning",
                code="sign_mismatch" if signs.mismatch else None,
            )
        )
        meaning = meaning or signs.mismatch
    if meaning and candidate.confidence is not None:
        candidate.confidence *= MEANING_MISMATCH_FACTOR
    return candidate


def _accept_at(candidate: _Candidate, settings: Settings) -> float:
    """The confidence from which this candidate is accepted: the bar of the model that
    wrote it (`Settings.accept_confidence_by_model`), else the common one."""
    model = candidate.calls[-1].model if candidate.calls else None
    return settings.accept_confidence_for(model)


def _needs_escalation(candidate: _Candidate, settings: Settings) -> bool:
    """Ask the main model again when the cheap answer is unsure, unusable, or disagrees
    with the text layer in any way, a cosmetic style difference included: the style rule
    is a heuristic, so a second reading is cheaper than a hidden vector-to-scalar error.
    Only the speech notes do not escalate (they say nothing about the transcription).

    Not when the only reason is a confidence between SKIP_ESCALATION_FROM and the accept
    bar and every check passed (`_band_only`): on the 2026-10 runs the main model's
    second reading gave the same LaTeX for most escalated answers, and that band is where
    the cheap answer is most often right. Such a fix keeps the cheap answer and the usual
    status rules (below the accept bar it is a decision), and says so in `Fix.escalation`.
    """
    if candidate.output is None or candidate.confidence is None:
        return False  # the router already tried the other model as the fallback
    if candidate.calls and candidate.calls[-1].model == settings.main_model:
        return False  # the fallback was the main model: nothing better to ask
    if _band_only(candidate, settings):
        return False
    disagrees = any(not c.passed and c.code not in SPEECH_CODES for c in candidate.checks)
    return (
        candidate.confidence < _accept_at(candidate, settings) or not candidate.usable or disagrees
    )


def _band_only(candidate: _Candidate, settings: Settings) -> bool:
    """The cheap answer's only reason to be asked again is a confidence in
    [SKIP_ESCALATION_FROM, auto_accept_confidence): it is usable and no check failed
    (a failed speech note counts: with any failed check the old rule applies)."""
    confidence = candidate.confidence
    return (
        confidence is not None
        and SKIP_ESCALATION_FROM <= confidence < _accept_at(candidate, settings)
        and candidate.usable
        and candidate.output is not None
        and candidate.output.is_equation
        and all(c.passed for c in candidate.checks)
    )


_SINGLE_BRACED = re.compile(r"([_^])\{(\\[A-Za-z]+|[A-Za-z0-9])\}")


def _canonical(latex: str) -> str:
    """LaTeX without whitespace and without braces around a single symbol after `_` or `^`
    (`d_{0}` and `d_0` are one reading)."""
    return _SINGLE_BRACED.sub(r"\1\2", "".join(latex.split()))


def _same_reading(first: _Candidate, second: _Candidate) -> bool:
    """Do two attempts transcribe the same thing? Their LaTeX, whitespace and the braces
    around a single script aside."""
    return _canonical(first.latex) == _canonical(second.latex)


def _escalation_outcome(
    first: _Candidate, escalated: _Candidate | None, skipped: bool
) -> str | None:
    """`Fix.escalation`: what the main model's second reading did (None: none was asked)."""
    if escalated is None:
        return "skipped" if skipped else None
    if not escalated.usable or escalated.output is None or not escalated.output.is_equation:
        return "failed"
    if not first.usable or first.output is None or not first.output.is_equation:
        return "changed"
    return "same" if _same_reading(first, escalated) else "changed"


def _prefer(main: _Candidate, cheap: _Candidate) -> _Candidate:
    """The main model's answer wins unless only the cheap one is usable (or the main
    call produced nothing, for example during an outage)."""
    if main.output is None or (not main.usable and cheap.usable):
        return cheap
    return main


# ----------------------------------------------------------------------- status


def _fix(
    element: Element,
    candidate: _Candidate,
    calls: list[ModelCall],
    display: Display,
    settings: Settings,
) -> Fix:
    def make(
        status: Status, reason: str | None = None, proposal: bool = False, review: Any = None
    ) -> Fix:
        equation = None
        if proposal and candidate.mathml is not None:
            equation = EquationFix(
                latex=candidate.latex,
                mathml=candidate.mathml,
                speech=candidate.speech,
                display=display,
            )
        return Fix(
            element_id=element.id,
            kind=Kind.EQUATION,
            status=status,
            confidence=candidate.confidence,
            equation=equation,
            checks=candidate.checks,
            calls=calls,
            reason=reason,
            review=review,  # None: from the status (waiting -> required)
        )

    output, confidence = candidate.output, candidate.confidence
    if output is None or confidence is None:
        outcome = calls[-1].outcome if calls else "skipped"
        return make(Status.FAILED, f"model_{outcome}")
    if not output.is_equation:
        return make(Status.ABSTAINED, "not_equation")
    if not candidate.latex and confidence < settings.abstain_confidence:
        return make(Status.ABSTAINED, "low_confidence")
    if not candidate.valid:
        return make(Status.FAILED, "invalid_latex")
    if confidence < settings.abstain_confidence:
        return make(Status.ABSTAINED, "low_confidence")
    failed = [c for c in candidate.checks if not c.passed]
    single = ACCEPT_SINGLE_READING and any(c.code == SINGLE_READING_CODE for c in failed)
    bar = SKIP_ESCALATION_FROM if single else _accept_at(candidate, settings)
    if confidence >= bar and all(c.code in NOTE_CODES for c in failed):
        # Notes only (a cosmetic style difference, no speech text): accepted, listed as
        # optional checks on the review screen.
        return make(Status.ACCEPTED, proposal=True, review="optional" if failed else "none")
    return make(Status.NEEDS_REVIEW, proposal=True)


def transcribe(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    """Transcribe one equation crop. Never raises for model or validation problems (they
    become FAILED/ABSTAINED fixes); raises BudgetExceeded when spend is refused.

    A bold letter layout found in running text gets its fix without a model call
    (`notation_memory.bold_letter_fix`); with course notation in force, the model gets
    it as fenced context under `NOTATION_PROMPT_NAME` (module docstring)."""
    entries = notation_memory.entries_in_force()
    spoken = notation_memory.readings(entries)
    fixed = notation_memory.bold_letter_fix(element, spoken=spoken)
    if fixed is not None:
        return fixed
    course_bold = notation_memory.course_bold_letters(entries)
    if entries:
        prompt = load_prompt(NOTATION_PROMPT_NAME)
        context_text = notation_memory.notation_context(entries, context_text)
    else:
        prompt = load_prompt(PROMPT_NAME)
    display: Display = element.display or "block"

    def attempt(model: str, *, escalation: bool = False) -> _Candidate:
        request = VisionRequest(
            step="equation",
            model=model,
            prompt_version=prompt.version,
            instructions=prompt.text,
            images=[png],
            context_text=first_context if not escalation else context_text,
            max_output_tokens=output_limit(model),
            image_detail="high",
            effort=settings.anthropic_effort_equation,
        )
        if escalation:
            output, calls = _call_once(client, request, EquationOutput)
        else:
            output, calls = _call(client, request, EquationOutput, settings)
        return _evaluate(output, calls, element, display, course_bold=course_bold, spoken=spoken)

    first_model = _first_model(element, settings)
    # The text around the equation goes to the main model only when it re-reads a cheap
    # answer (`Settings.equation_context_on_escalation`); course notation always goes.
    first_context = context_text
    if settings.equation_context_on_escalation and first_model != settings.main_model:
        first_context = None if not entries else context_text
    candidate = first = attempt(first_model)
    calls = list(candidate.calls)
    cheap_start = first_model != settings.main_model
    reread: _Candidate | None = None
    if cheap_start and _needs_escalation(candidate, settings):
        reread = attempt(settings.main_model, escalation=True)
        calls += reread.calls
        candidate = _prefer(reread, candidate)
    answered_cheaply = not first.calls or first.calls[-1].model != settings.main_model
    skipped = cheap_start and reread is None and answered_cheaply and _band_only(first, settings)
    if skipped:
        # The note rides on the answer that stands, so the review screen can say it was
        # read once; with ACCEPT_SINGLE_READING it never makes the fix wait (`_fix`).
        candidate.checks.append(
            Check(
                name="equation.single_reading",
                passed=False,
                severity="warning",
                code=SINGLE_READING_CODE,
            )
        )
    if (
        equation_images.REASON in element.reasons
        and not element.text
        and candidate.output is not None
        and candidate.output.is_equation
    ):
        candidate.checks.append(
            Check(
                name="equation.no_text_layer",
                passed=False,
                severity="warning",
                code=NO_TEXT_LAYER_CODE,
            )
        )
    if layout.REASON_HALF_FRACTION in element.reasons:
        candidate.checks.append(
            Check(
                name="equation.region",
                passed=False,
                severity="warning",
                code=HALF_FRACTION_CODE,
            )
        )
        if candidate.confidence is not None:
            candidate.confidence = min(candidate.confidence, settings.auto_accept_confidence - 0.01)
    fix = _fix(element, candidate, calls, display, settings)
    outcome = _escalation_outcome(first, reread, skipped)
    if outcome is not None:
        fix = fix.model_copy(update={"escalation": outcome})
    # Second automated check, shadow mode (ADR 0005): records P(meaning_changing) for the
    # final proposal and the cheap answer before escalation; changes nothing else.
    from a11y_stem.pipeline import equation_shadow

    escalated_from = first.latex if first is not candidate and first.valid else None
    return equation_shadow.record(
        fix, png, client=client, settings=settings, cheap_latex=escalated_from
    )
