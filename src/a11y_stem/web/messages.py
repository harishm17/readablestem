"""Plain-English sentences for the pipeline's category codes, for the review screen.

The pipeline records what went wrong as content-free codes (`Check.name` and `Check.code`,
`Fix.reason`) so they can be logged and aggregated. A reviewer should not have to decode
`heading.evidence: heading.weak_evidence`; this module turns each code into one sentence
that says what is wrong and what to look at. The code itself stays available as a small
secondary line for debugging and bug reports.

Every code the pipeline can emit has an entry here: `tests/test_web_review_messages.py`
reads the pipeline's source, collects every check name/code pair and fix reason, and
fails when one has no message (or when a message belongs to a code that no longer
exists). An unknown code still gets a neutral sentence plus the code, never a blank.

Notices (`DocumentResult.notices`, the job page's list beyond the review decisions) have
their own table, `NOTICE_MESSAGES`, one entry per code in `core.types.NOTICE_CODES`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from a11y_stem.core.types import Check, Notice
from a11y_stem.pipeline.validate import ALT_MAX_CHARS, ALT_WARN_CHARS, MAX_LATEX_CHARS

# Failed checks, keyed by (check name, code). Names are what was checked, codes are what
# went wrong; a code alone can be ambiguous ("empty" LaTeX vs "empty" alt text).
CHECK_MESSAGES: dict[tuple[str, str], str] = {
    # LaTeX (pipeline.validate.check_latex)
    ("latex.not_empty", "empty"): "The transcription is empty: type the LaTeX for this "
    "equation, or reject it to keep the image.",
    ("latex.length", "too_long"): f"The LaTeX is longer than {MAX_LATEX_CHARS:,} characters, "
    "which usually means more than one equation was transcribed.",
    ("latex.balanced_braces", "unbalanced_braces"): "A { has no matching } (or the other way "
    "round).",
    ("latex.balanced_environments", "unbalanced_env"): "A \\begin{...} has no matching "
    "\\end{...} (or the other way round).",
    ("latex.no_math_delimiters", "math_delimiter"): "The LaTeX contains a $ sign: remove the "
    "$...$ delimiters and keep only the maths.",
    ("latex.no_comments", "comment"): "The LaTeX contains a % comment, which hides the rest of "
    "the line (write a percent sign as \\%).",
    ("latex.no_forbidden_commands", "forbidden_command"): "The LaTeX uses a command that could "
    "read files or change settings, so it is never allowed.",
    ("latex.allowed_commands", "unknown_command"): "The LaTeX uses a command that is not on "
    "the list of supported maths commands, so check for a typo or rewrite it.",
    ("latex.allowed_environments", "unknown_environment"): "The LaTeX uses an environment "
    "(\\begin{...}) that is not supported.",
    ("latex.command_arguments", "bad_argument"): "A command has an argument in a form that is "
    "not supported, for example a \\hspace width.",
    ("latex.plain_text_arguments", "markup_in_text"): "Text inside \\text{...} contains "
    "characters that would turn into markup.",
    ("latex.converts", "conversion_failed"): "The LaTeX could not be converted to MathML, "
    "often because of a missing argument or a typo.",
    # MathML (pipeline.validate.check_mathml)
    ("mathml.size", "too_large"): "The MathML is far too large for one equation, which "
    "usually means the transcription repeats itself.",
    ("mathml.well_formed", "forbidden_xml"): "The MathML uses XML features that are not "
    "allowed, such as entity definitions.",
    ("mathml.well_formed", "not_well_formed"): "The MathML is not valid XML, so it cannot be "
    "shown.",
    ("mathml.root", "wrong_root"): "The MathML does not start with a <math> element.",
    ("mathml.root", "missing_namespace"): "The MathML has no MathML namespace, so browsers "
    "would not show it as maths.",
    ("mathml.root", "nested_math"): "The MathML has a <math> element inside another one.",
    ("mathml.known_elements", "unknown_element"): "The MathML uses an element that is not on "
    "the list of supported ones.",
    ("mathml.safe_attributes", "unsafe_attribute"): "The MathML has an attribute that could "
    "run code or load something, so it is not allowed.",
    ("mathml.safe_attributes", "unknown_attribute"): "The MathML has an attribute that is not "
    "on the list of supported ones.",
    ("mathml.token_content", "markup_in_token"): "A MathML symbol contains other elements "
    "instead of plain text.",
    ("mathml.depth", "too_deep"): "The MathML is nested too deeply to be a real equation.",
    ("mathml.structure", "bad_arity"): "A fraction, root, subscript or similar has the wrong "
    "number of parts, often because an argument is missing.",
    ("mathml.converted", "unconverted_command"): "Part of the LaTeX was copied through as text "
    "instead of becoming maths, often because of an unsupported command.",
    # Equations (pipeline.equation_step)
    ("speech.generated", "speech_failed"): "The spoken version could not be made, though the "
    "equation itself may still be right.",
    ("speech.generated", "speech_empty"): "The spoken version came out empty, though the "
    "equation itself may still be right.",
    ("equation.text_layer", "text_layer_mismatch"): "The symbols in this transcription differ "
    "a lot from the text stored in the PDF, so compare it with the image symbol by symbol.",
    ("equation.text_layer", "symbol_mismatch"): "A letter, digit or Greek letter in this "
    "transcription differs from the text stored in the PDF (a wrong digit, a misread or "
    "missing symbol), so compare it with the image symbol by symbol.",
    ("equation.text_layer_signs", "sign_mismatch"): "A sign, relation or arrow differs from "
    "the PDF (a minus missing or added, ± read as +, =, <, ≤ or ≈ mixed up, a vector arrow "
    "dropped), so check the minus signs, plus/minus, relations and arrows.",
    ("equation.text_layer_style", "style_mismatch"): "A style that carries meaning differs "
    "from the PDF (a bold letter, a vector, is plain here, a plain one became bold, or a "
    "double-struck letter such as the R of the reals became plain), so check those letters.",
    ("equation.text_layer_style", "style_cosmetic"): "A letter's style differs from the PDF "
    "only where it does not change the meaning (a bold subscript or digit, or a style such "
    "as double-struck that the PDF's text cannot show), so this was accepted as it is.",
    # Alt text (pipeline.validate.check_alt)
    ("alt.decorative", "decorative_with_text"): "A decorative figure must have empty alt "
    "text, so clear the text or untick Decorative.",
    ("alt.not_empty", "empty"): "The alt text is empty: describe the figure, or mark it as "
    "decorative.",
    ("alt.length", "too_long"): f"The alt text is long: aim for {ALT_WARN_CHARS} characters or "
    f"fewer (over {ALT_MAX_CHARS} it cannot be saved).",
    ("alt.no_redundant_prefix", "redundant_prefix"): "The alt text starts with words like "
    '"image of", which screen readers already say, so start with what it shows.',
    ("alt.not_filename", "filename"): "The alt text looks like a file name, so describe what "
    "the figure shows instead.",
    ("alt.no_bidi_controls", "bidi_control"): "The alt text contains invisible characters that "
    "reverse the reading direction, so retype it without them.",
    ("alt.no_url_or_markup", "url_or_markup"): "The alt text contains a web address, HTML or "
    "a LaTeX command, so use plain words instead.",
    ("alt.not_placeholder", "placeholder"): 'The alt text is a placeholder (like "image" or '
    '"Figure 2") and says nothing about the figure.',
    ("alt.not_caption_copy", "caption_copy"): "The alt text repeats the caption, which is read "
    "out anyway, so say what the figure shows instead.",
    # Figures (pipeline.figure_step)
    ("figure.decorative_consistency", "decorative_conflict"): "The figure was marked "
    "decorative but looks like it carries information (a chart, a caption or a "
    "description), so check before hiding it from screen readers.",
    ("figure.second_check", "second_check_mismatch"): "A second automated check found a "
    "possible mismatch between the alt text and the image; worth a look.",
    # Headings (pipeline.heading_step)
    ("heading.text", "heading.empty_text"): "This heading has no text, so it cannot be used "
    "as a heading.",
    ("heading.outline", "heading.level_repaired"): "The level was changed so the outline does "
    "not skip a level, so check it fits the sections around it.",
    ("heading.evidence", "heading.weak_evidence"): "Only weak signs say this is a heading "
    "(bold at body size, no larger font or section number), so check it is not a label in "
    "the text.",
    ("heading.all_caps", "heading.all_caps"): "The text is all capitals, which is often a "
    "label or running header rather than a section heading.",
    ("heading.length", "heading.very_short"): "The text is very short, which is often a label "
    '(like "Ex.") rather than a section heading.',
    ("heading.list_like", "heading.list_like"): 'It starts like a numbered list item ("1.", '
    '"A)"), so it may be part of a list rather than a heading.',
    # Whole-document checks of the output HTML (pipeline.html_checks)
    ("html.lang", "missing_lang"): "The document does not say which language it is in.",
    ("html.title", "missing_title"): "The document has no title.",
    ("html.main", "main_count"): "The document should have exactly one main region.",
    ("html.heading_order", "heading_skip"): "The headings skip a level (for example from "
    "level 1 straight to level 3).",
    ("html.heading_text", "empty_heading"): "A heading in the document is empty.",
    ("html.img_alt", "img_missing_alt"): "An image in the document has no alt attribute.",
    ("html.unique_ids", "duplicate_id"): "Two elements in the document share an id, which can "
    "break links and confuse screen readers.",
    ("html.list_structure", "list_structure"): "A list in the document is malformed (an empty "
    "item, or a list inside a paragraph), so screen readers may not announce it as a list.",
    ("html.math_display", "math_missing_display"): "An equation does not say whether it sits "
    "in the text or on its own line.",
    ("html.self_contained", "external_resource"): "The document loads something from another "
    "site, but it should work on its own.",
    ("html.wording", "banned_wording"): "The document text promises more than this tool can "
    "back up (for example that it meets a legal standard).",
    # Links, language and colour in the output HTML (pipeline.html_checks, text work)
    ("html.link_text", "empty_link"): "A link in the document has no text, so screen "
    "readers cannot say where it goes.",
    ("html.link_href", "unsafe_link"): "A link in the document is not a web or email address "
    "or a place in the document itself.",
    ("html.lang_tags", "invalid_lang"): "A language marked in the document is not a valid "
    "language code.",
    ("html.text_contrast", "low_contrast"): "Some coloured text is too light to read easily "
    "on the white background.",
    ("axe", "skipped"): "The automatic accessibility check (axe-core) was skipped because no "
    "browser was available.",
    ("axe", "axe_error"): "The automatic accessibility check (axe-core) could not run on the "
    "output.",
}

# axe-core reports its own rule ids ("color-contrast"); they become checks named
# "axe.<rule>" with the rule as the code (pipeline.html_checks.violations_to_checks).
AXE_RULE_PREFIX = "axe."
AXE_RULE_MESSAGE = (
    'The automatic accessibility check (axe-core) reported a problem with its rule "{rule}".'
)

# Fix.reason: why there is no usable proposal (ABSTAINED or FAILED).
REASON_MESSAGES: dict[str, str] = {
    # Equation and figure steps
    "low_confidence": "The model was not sure enough to make a proposal, so write one "
    "yourself or reject it to keep the original from the PDF.",
    "not_equation": "The model judged that this is not an equation: if it is, type the LaTeX, "
    "and if not, reject it to keep the original.",
    "invalid_latex": "The model's LaTeX failed a required check (listed below), so correct it "
    "or reject it to keep the image.",
    "invalid_alt": "The model's alt text failed a required check (listed below), so write new "
    "alt text or reject it to keep the figure flagged.",
    # f"model_{outcome}" for every models.base Outcome: no usable answer from any attempt
    "model_ok": "The model answered, but its answer could not be used.",
    "model_refused": "The model declined to answer for this item.",
    "model_invalid_output": "The model's answer was not in the expected format.",
    "model_timeout": "The model did not answer in time.",
    "model_api_error": "The model service returned an error.",
    "model_budget_exceeded": "The spending limit was reached before this item could be sent "
    "to the model.",
    "model_skipped": "No model was asked about this item.",
    # Heading step
    "heading.empty_text": "This heading has no text, so it cannot be used as a heading.",
    # Table step (pipeline.table_step)
    "table_not_rebuilt": "This table is kept as an image of the original, so screen readers "
    "cannot read its cells: keep it as an image or rebuild it as a table yourself.",
    # Orchestrator (pipeline.run)
    "internal_error": "Something went wrong while this item was processed, so the original "
    "is kept, but you can still fill it in yourself.",
    "crop_error": "The image of this item could not be cut out of the PDF, so the model never "
    "saw it.",
    # f"budget_{scope}" for every BudgetExceeded scope (core.budget, web.worker)
    "budget_daily": "Today's spending limit was reached before this item was converted.",
    "budget_job_usd": "This document's spending limit was reached before this item was converted.",
    "budget_job_calls": "This document's limit on model calls was reached before this item "
    "was converted.",
    "budget_cancelled": "The conversion was stopped before this item was converted.",
    "budget_unknown": "A spending limit was reached before this item was converted.",
}

# DocumentResult.notices: one sentence per notice code for the job page's list ("Not
# handled in this file" and "Worth checking"; core.types.Notice). Kept apart from the
# check and fix-reason tables because a notice is not a review decision. Every code in
# core.types.NOTICE_CODES has an entry (tests/test_web_review_messages.py). The wording
# is a placeholder until the builder that emits each code writes its own sentence.
NOTICE_MESSAGES: dict[str, str] = {
    "table_not_rebuilt": "A table is kept as an image of the original, so screen readers "
    "cannot read its cells.",
    "scanned_page": "A page is only a picture of a page (a scan), so nothing on it could "
    "be converted.",
    "code_block": "Program code is kept as plain text, without its layout or highlighting.",
    "footnote": "A footnote is kept where it falls in the text, not linked to its mark.",
    "colour_reference": "The text refers to a colour (for example, shown in red), so add "
    "a cue that does not depend on seeing the colour.",
    "colour_only": "A figure tells things apart by colour alone, so add labels or patterns "
    "that do not depend on seeing the colour.",
    "link_scheme_dropped": "A link in the PDF was not kept because it was not a web or "
    "email address.",
    "link_text_unclear": "A link's text does not say where it goes (for example, click "
    "here), so reword it.",
    "lang_detected": "The language of the document was detected from its text, so check it "
    "is right.",
    "lang_assumed": "The PDF does not say which language it is in and none was detected, "
    "so English is assumed: check it is right.",
    "long_description_missing": "A complex figure, such as a chart or a diagram, has no "
    "long description yet.",
}

UNKNOWN_CHECK_MESSAGE = "A check did not pass, so compare the proposal with the original closely."
UNKNOWN_REASON_MESSAGE = "This item could not be converted, so the original is kept."
UNKNOWN_NOTICE_MESSAGE = "Something in this file may need a look."

SEVERITY_LABELS = {"error": "Must be fixed", "warning": "Worth checking"}
# Failed checks of a fix accepted with optional notes (`Fix.review == "optional"`): they
# never block anything, so they are labelled as notes, not by severity.
NOTE_LABEL = "Note"

# Confidence words. "High" starts at the default auto-accept bar
# (Settings.auto_accept_confidence), so a "High" item in review was held back by a check,
# not by the model's own doubt.
CONFIDENCE_WORDS = ((85, "High"), (60, "Medium"), (0, "Low"))


@dataclass(frozen=True)
class Explained:
    """A sentence for the reviewer plus the raw code it explains."""

    text: str
    code: str  # e.g. "latex.allowed_commands: unknown_command", for the secondary line
    known: bool  # False when the code has no message of its own (neutral fallback)


def explain_check(check: Check) -> Explained:
    code = f"{check.name}: {check.code}" if check.code else check.name
    text = CHECK_MESSAGES.get((check.name, check.code or ""))
    if text is not None:
        return Explained(text, code, known=True)
    if check.name.startswith(AXE_RULE_PREFIX) and check.code:
        return Explained(AXE_RULE_MESSAGE.format(rule=check.code), code, known=True)
    return Explained(UNKNOWN_CHECK_MESSAGE, code, known=False)


def explain_reason(reason: str) -> Explained:
    text = REASON_MESSAGES.get(reason)
    return Explained(text or UNKNOWN_REASON_MESSAGE, reason, known=text is not None)


def explain_notice(notice: Notice) -> Explained:
    text = NOTICE_MESSAGES.get(notice.code)
    return Explained(text or UNKNOWN_NOTICE_MESSAGE, notice.code, known=text is not None)


def severity_label(check: Check) -> str:
    return SEVERITY_LABELS.get(check.severity, check.severity)


def confidence_percent(confidence: float) -> int:
    """Whole percent, rounded half up. The review screen shows this number and the bulk
    threshold compares against it, so "80%" on screen always means "at or above 80%"."""
    return math.floor(confidence * 100 + 0.5)


def confidence_word(confidence: float) -> str:
    percent = confidence_percent(confidence)
    return next(word for floor, word in CONFIDENCE_WORDS if percent >= floor)


def confidence_label(confidence: float | None) -> str:
    """'Low (45%)'. A word first, so the number is not the only cue."""
    if confidence is None:
        return "Not given"
    return f"{confidence_word(confidence)} ({confidence_percent(confidence)}%)"
