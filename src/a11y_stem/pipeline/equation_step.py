"""Equation step: crop image -> LaTeX -> validated MathML -> speech.

Routing: simple inline candidates (inline, text layer <= 25 characters) start on the
cheap model; display equations and anything the cheap model is unsure about (confidence
< settings.auto_accept_confidence or a failed error-level check) escalate to the main
model. The first attempt goes through `models.router.call_with_fallback`, so an outage
on one model falls back to the other. The escalation is one main-model call with no
fallback: the main model's fallback is the cheap model, which has already answered this
exact request, so falling back would pay for the same answer twice. If the escalation
fails, the cheap answer stands. Every attempt's `ModelCall` ends up on the fix.

Nothing the model says is trusted as-is. Its LaTeX goes through `validate.check_latex`,
`validate.safe_latex_to_mathml` and `validate.check_mathml`, and is then cross-checked
against the PDF text layer: the symbols in the text layer (when the PDF has one) and in
the transcription should mostly be the same. A strong mismatch is the cheapest signal of
a hallucinated transcription; it adds a warning check and lowers the confidence. The
text layer is deliberately not sent to the model, so the cross-check stays independent.

Status rules, in order:
1. no parsed output from any attempt -> FAILED, reason "model_<outcome>";
2. the model says it is not an equation -> ABSTAINED "not_equation";
3. an empty transcription below the abstain threshold -> ABSTAINED "low_confidence"
   (the model abstained, as the prompt asks, rather than producing invalid LaTeX);
4. LaTeX that fails an error-level check after escalation -> FAILED "invalid_latex";
5. confidence < settings.abstain_confidence -> ABSTAINED "low_confidence";
6. confidence >= settings.auto_accept_confidence and no failed check -> ACCEPTED;
7. otherwise NEEDS_REVIEW.
Only ACCEPTED and NEEDS_REVIEW fixes carry a proposal (`Fix.equation`). BudgetExceeded
propagates: the orchestrator decides what happens to the rest of the document.
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
from a11y_stem.pipeline.speech import SpeechError, mathml_to_speech
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

SHORT_INLINE_CHARS = 25  # inline candidates up to this many text-layer chars start cheap
MAX_OUTPUT_TOKENS = 2048  # LaTeX up to 2000 chars plus reasoning tokens
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


def clean_latex(latex: str) -> str:
    r"""Remove wrappers the prompt forbids but models still add ($...$, \[...\],
    equation environments, \displaystyle), and escape bare `%` signs. Never changes the
    mathematics: a bare `%` in one transcribed equation is a percent sign, but the
    converter would treat it as a comment and silently drop the rest of the line."""
    text = latex.strip()
    for pattern in _WRAPPERS:
        match = pattern.match(text)
        if match:
            text = match.group(1).strip()
    text = _DISPLAYSTYLE.sub("", text)
    text = text.removesuffix("\\\\").strip()
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


def _symbol_bag(text: str) -> Counter[str]:
    """Letters, digits and Greek letters, case-folded; math italic glyphs map to ASCII."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return Counter(ch for ch in folded if ch.isalnum())


def _mathml_symbols(mathml: str) -> Counter[str]:
    root = DET.fromstring(mathml, forbid_dtd=True)
    return _symbol_bag(" ".join(root.itertext()))


# A style mismatch (the PDF has a bold d, the transcription a plain d) scales confidence
# by this factor: enough to escalate a cheap-model answer and to keep the fix in review.
STYLE_MISMATCH_FACTOR = 0.6
_MATHML_TOKENS = {"mi", "mn", "mo", "mtext", "ms"}


def _text_styles(text: str) -> set[tuple[str, str]]:
    """(base letter, style group) for every styled letter, e.g. {("d", "bold")}."""
    out = set()
    for ch in text:
        group = STYLE_GROUPS.get(char_variant(ch) or "")
        if group:
            out.add((base_char(ch), group))
    return out


def _mathml_styles(mathml: str) -> tuple[set[tuple[str, str]], set[str]]:
    """Styled letters of a MathML tree (mathvariant inherited, as MathML specifies, or
    carried by the code point) and every base letter it contains."""
    styled: set[tuple[str, str]] = set()
    letters: set[str] = set()

    def walk(elem: Any, inherited: str | None) -> None:
        variant = elem.get("mathvariant") or inherited
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag in _MATHML_TOKENS:
            for ch in elem.text or "":
                if not ch.isalnum():
                    continue
                own = char_variant(ch)
                base = base_char(ch)
                letters.add(base)
                group = STYLE_GROUPS.get(own or variant or "")
                if group:
                    styled.add((base, group))
            return
        for child in elem:
            walk(child, variant)

    walk(DET.fromstring(mathml, forbid_dtd=True), None)
    return styled, letters


def text_layer_style_mismatch(text_layer: str | None, mathml: str) -> bool | None:
    """Do the letter styles of the PDF text layer and the transcription disagree?

    None when the text layer carries no style at all (most PDFs without bold maths, and
    any text layer from a font that does not mark bold): absence of style is not evidence.
    Otherwise compares (letter, style) pairs for the letters both sides contain, so a bold
    vector read as a plain scalar, or a plain scalar made bold, is a mismatch.
    """
    if not text_layer:
        return None
    expected = _text_styles(text_layer)
    if not expected:
        return None
    got, letters = _mathml_styles(mathml)
    layer_letters = {base_char(ch) for ch in text_layer if ch.isalnum()}
    shared = layer_letters & letters
    expected = {pair for pair in expected if pair[0] in shared}
    got = {pair for pair in got if pair[0] in shared}
    return expected != got


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


def _speech(mathml: str) -> tuple[str, Check]:
    try:
        speech = mathml_to_speech(mathml)
    except SpeechError:
        return "", Check(
            name="speech.generated", passed=False, severity="warning", code="speech_failed"
        )
    code = None if speech else "speech_empty"
    return speech, Check(
        name="speech.generated", passed=code is None, severity="warning", code=code
    )


def _evaluate(
    output: EquationOutput | None, calls: list[ModelCall], element: Element, display: Display
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
    candidate.speech, speech_check = _speech(candidate.mathml)
    candidate.checks.append(speech_check)
    similarity = text_layer_similarity(element.text, candidate.mathml)
    if similarity is not None:
        mismatch = similarity < TEXT_LAYER_MISMATCH_BELOW
        candidate.checks.append(
            Check(
                name="equation.text_layer",
                passed=not mismatch,
                severity="warning",
                code="text_layer_mismatch" if mismatch else None,
            )
        )
        if mismatch:  # scale towards half confidence as the symbol overlap goes to zero
            candidate.confidence = output.confidence * (0.5 + 0.5 * similarity)
    style_mismatch = text_layer_style_mismatch(element.text, candidate.mathml)
    if style_mismatch is not None:
        candidate.checks.append(
            Check(
                name="equation.text_layer_style",
                passed=not style_mismatch,
                severity="warning",
                code="style_mismatch" if style_mismatch else None,
            )
        )
        if style_mismatch and candidate.confidence is not None:
            candidate.confidence *= STYLE_MISMATCH_FACTOR
    return candidate


def _needs_escalation(candidate: _Candidate, settings: Settings) -> bool:
    if candidate.output is None or candidate.confidence is None:
        return False  # the router already tried the other model as the fallback
    if candidate.calls and candidate.calls[-1].model == settings.main_model:
        return False  # the fallback was the main model: nothing better to ask
    return candidate.confidence < settings.auto_accept_confidence or not candidate.usable


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
    def make(status: Status, reason: str | None = None, proposal: bool = False) -> Fix:
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
    failed = any(not c.passed for c in candidate.checks)
    if confidence >= settings.auto_accept_confidence and not failed:
        return make(Status.ACCEPTED, proposal=True)
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
    become FAILED/ABSTAINED fixes); raises BudgetExceeded when spend is refused."""
    prompt = load_prompt(PROMPT_NAME)
    display: Display = element.display or "block"

    def attempt(model: str, *, escalation: bool = False) -> _Candidate:
        request = VisionRequest(
            step="equation",
            model=model,
            prompt_version=prompt.version,
            instructions=prompt.text,
            images=[png],
            context_text=context_text,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            image_detail="high",
        )
        if escalation:
            output, calls = _call_once(client, request, EquationOutput)
        else:
            output, calls = _call(client, request, EquationOutput, settings)
        return _evaluate(output, calls, element, display)

    first_model = _first_model(element, settings)
    candidate = attempt(first_model)
    calls = list(candidate.calls)
    if first_model != settings.main_model and _needs_escalation(candidate, settings):
        escalated = attempt(settings.main_model, escalation=True)
        calls += escalated.calls
        candidate = _prefer(escalated, candidate)
    return _fix(element, candidate, calls, display, settings)
