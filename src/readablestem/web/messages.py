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

from readablestem.core.types import Check, Notice
from readablestem.pipeline.validate import ALT_MAX_CHARS, ALT_WARN_CHARS, MAX_LATEX_CHARS

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
    ("equation.text_layer_numbers", "number_mismatch"): "A number differs from the text stored "
    "in the PDF: the digits are the same but make a different number (36 for 63, 9.81 for "
    "98.1, a decimal point or an exponent moved), so compare the numbers with the image.",
    ("equation.text_layer_case", "case_mismatch"): "A capital letter is small here, or a small "
    "letter is a capital, compared with the text stored in the PDF (W for w, S for s), so "
    "compare the letters with the image.",
    ("equation.text_layer_signs", "sign_mismatch"): "A sign, relation or arrow differs from "
    "the PDF (a minus missing or added, ± read as +, =, <, ≤ or ≈ mixed up, a vector arrow "
    "dropped), so check the minus signs, plus/minus, relations and arrows.",
    ("equation.text_layer_style", "style_mismatch"): "A style that carries meaning differs "
    "from the PDF (a bold letter, a vector, is plain here, a plain one became bold, or a "
    "double-struck letter such as the R of the reals became plain), so check those letters.",
    ("equation.text_layer_style", "style_cosmetic"): "A letter's style differs from the PDF "
    "only where it does not change the meaning (a bold subscript or digit, or a style such "
    "as double-struck that the PDF's text cannot show), so this was accepted as it is.",
    ("equation.page_reading", "page_reading_mismatch"): "Reading the crop and reading the "
    "whole page gave different answers, and a second look at the crop agreed with neither, so "
    "compare this equation with the page symbol by symbol.",
    ("equation.no_text_layer", "no_text_layer"): "This equation is a picture, so the PDF holds "
    "no text to check the transcription against: compare it with the picture symbol by "
    "symbol.",
    # A page converted from an image (pipeline.image_page, ADR 0007)
    ("image_page.no_text_layer", "image_page"): "This was read from an image of the page, so "
    "nothing in the PDF can confirm it: compare it with the original page.",
    ("image_page.read_confidence", "read_medium"): "The reader was not sure of every word or "
    "symbol here and used the context to settle some, so compare it with the original page.",
    ("image_page.read_confidence", "read_low"): "Part of this was hard to read, so compare "
    "it with the original page and correct what is wrong.",
    ("image_page.second_reading", "second_reading_differs"): "The line was read a second time "
    "from a picture of it alone and the two readings differ, so compare this with the original "
    "page symbol by symbol.",
    ("image_page.second_reading", "second_reading_agrees_unsure"): "The line was read a second "
    "time from a picture of it alone and the readings agree, but that reading was not sure of "
    "every symbol either, so compare this with the original page.",
    ("image_page.second_reading", "second_reading_agrees"): "The line was read a second time "
    "from a picture of it alone and both readings agree; the page is still the only thing "
    "that can confirm them.",
    ("image_page.illegible", "illegible"): "Some of this could not be read and is marked "
    "[illegible]: look at the original page and write what it says.",
    ("image_page.inline_math", "inline_math_invalid"): "A formula in this text could not be "
    "converted and is shown as its LaTeX in square brackets: look at the original page and "
    "correct it.",
    ("image_page.single_reading", "single_reading_no_box"): "The place of this equation on "
    "the page could not be found, so it was read once, from the whole page, and not read "
    "again from its own picture: compare it with the original page symbol by symbol.",
    ("image_page.single_reading", "single_reading_crop_failed"): "The picture cut out for this "
    "equation did not show an equation (its place on the page may be off), so this is the "
    "reading from the whole page, not read again: compare it with the original page symbol by "
    "symbol.",
    ("image_page.omitted_region", "omitted_region"): "There is writing here that nothing in "
    "the converted page accounts for, so the reading probably left it out: look at the picture "
    "of this part of the page, then write what it says or keep it as an image.",
    ("table.no_text_layer", "no_text_layer"): "This table was read from an image of the page, "
    "so nothing in the PDF can confirm its cells: compare every value with the original.",
    ("equation.region", "half_fraction"): "The part of the page this equation was read "
    "from ends at a fraction bar and may hold only the top or the bottom of a fraction, so "
    "compare it with the page and write the whole fraction if part is missing.",
    ("equation.single_reading", "single_reading"): "The model read this equation once, "
    "and its answer passed every check, so no second reading was made and it was accepted "
    "as it stands.",
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
    # Tables (pipeline.table_step)
    ("table.grid", "grid_invalid"): "The rebuilt table's cells overlap, leave gaps or reach "
    "outside the table, so check its rows and columns against the image.",
    ("table.headers", "header_problem"): "The header cells look wrong (a header below the "
    "data, a data cell among the headers, or a header that runs into the data), so check "
    "which cells are headers.",
    ("table.headers", "no_headers"): "No cell is marked as a header, so screen readers cannot "
    "say what each value means: mark the header row or column.",
    ("table.grid_agreement", "grid_size_differs"): "The model and the PDF's own layout found "
    "a different number of rows or columns, so check that no row or column is missing, "
    "split or merged.",
    ("table.grid_agreement", "spans_differ"): "The model and the PDF's own layout disagree on "
    "which cells are merged across rows or columns, so check the merged cells.",
    ("table.cell_text", "text_mismatch"): "A word or number in the rebuilt table differs from "
    "the text stored in the PDF (missing, added twice or changed), so compare the cells with "
    "the image.",
    ("table.cell_text", "cell_mismatch"): "Every word and number is in the right row, but one "
    "sits in a different column than in the PDF (an empty cell left out, or two values "
    "swapped), so check each row's cells against the image.",
    ("table.cell_text", "row_mismatch"): "Every word and number is there, but one sits in a "
    "different row than in the PDF, so check the rows against the image.",
    ("table.cell_text", "whitespace_differs"): "The cells match the text stored in the PDF "
    "once spacing is ignored, so this was accepted as it is.",
    ("table.cell_text", "no_pdf_text"): "The PDF stores no text for this table to compare "
    "the cells with, so check every value against the image.",
    ("table.edge_text", "text_at_edge"): "There is text right under this table, in the same "
    "size and lined up with one of its columns, that is not in a cell: probably the rest of a "
    "wrapped cell, so check the last row against the image.",
    ("table.math", "invalid_latex"): "The maths in a cell could not be converted, so that "
    "cell shows plain text instead: check it against the image.",
    ("table.math", "flattened_maths"): "Some cells of the grid read from the PDF hold maths "
    "that its text gives flat (a fraction, or an accent such as a vector arrow), so the "
    "table cannot be used as it is: write those cells as LaTeX, or keep the image.",
    ("table.math_symbols", "symbol_mismatch"): "A letter, digit or Greek letter in a maths "
    "cell differs from the text stored in the PDF, so compare that cell with the image "
    "symbol by symbol.",
    ("table.math_signs", "sign_mismatch"): "A sign or relation in a maths cell differs from "
    "the PDF (a minus missing or added, ± read as +, = and ≈ mixed up), so check the signs "
    "in the maths cells.",
    ("table.math_style", "style_mismatch"): "A style that carries meaning differs from the "
    "PDF in a maths cell (a bold vector read as a plain letter, or the other way round), so "
    "check those letters.",
    ("table.math_style", "style_cosmetic"): "A letter's style in a maths cell differs from "
    "the PDF only where it does not change the meaning, so this was accepted as it is.",
    ("table.row_headers", "row_headers_dropped"): "A column of row labels comes after data "
    "cells and could not be used as one, so the table has no row headers: check that each "
    "value can still be found by its row.",
    ("table.source", "local_grid"): "No model could read this table, so this is the grid "
    "built from the PDF's own text with the first row as headers: check it against the image.",
    ("table.kind", "written_as_text"): "The model judged that this is not a table of data, so "
    "students get its text in reading order; if it is one, use the grid read from the PDF.",
    ("table.kind", "numbers_not_a_table"): "The model judged that this is not a table of data, "
    "but its cells hold numbers, so this is the grid built from the PDF's own text with the "
    "first row as headers: check it against the image.",
    # Long descriptions and chart data tables (pipeline.figure_step, long descriptions on)
    ("figure.data_values", "values_from_image"): "A data table was read from the chart "
    "image, so its values may be off; it is added only if you choose to after checking them.",
    ("figure.long_description", "long_description_unsure"): "Part of the figure was hard "
    "to read, so the longer description may be wrong in places; worth a look.",
    ("figure.source_alt", "source_alt_unchecked"): "No description from the model was usable, "
    "so this is the alt text the PDF's own tags give the picture: check it against the "
    "image.",
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
    ("heading.sentence", "heading.sentence_like"): "It reads like a sentence or a lead-in "
    "(it ends in a full stop, a question mark or a colon), not a title: decide whether it is "
    "a heading or just bold text.",
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
    # Tables in the output HTML (pipeline.html_checks, tables-step block)
    ("html.table_headers", "table_no_headers"): "A data table in the document has no header "
    "cells, so screen readers cannot say what its values mean.",
    ("html.table_header_scope", "th_without_scope"): "A header cell in a table does not say "
    "whether it labels a row or a column.",
    ("html.table_header_text", "empty_th"): "A header cell in a table is empty.",
    ("html.table_caption", "table_caption_missing"): "A table's caption (Table N) sits in the "
    "text beside it instead of being the table's own caption.",
    ("html.table_layout", "layout_table"): "A table in the document is used only to lay out "
    "text, but tables should hold data that is read across rows and columns.",
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
    "not_data_table": "The model judged that this is not a table of data (for example text "
    "set out in boxes), so the image is kept: rebuild it as a table yourself if it is one.",
    "invalid_table": "The model's answer could not be made into a table, so the image is "
    "kept: rebuild it as a table yourself or keep the image.",
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
    # Pages and handwriting kept as images before any model work (pipeline.page_type).
    # Final wording for the beta (handwriting design, section 3.3).
    "handwritten_page": "This page is handwritten and was not converted, so it is kept as an "
    "image, marked “Not converted” (upload again with “Convert scanned or handwritten pages” "
    "ticked).",
    "scanned_page": "This page is a scanned image and was not converted, so it is kept as an "
    "image, marked “Not converted” (upload again with “Convert scanned or handwritten pages” "
    "ticked).",
    "unread_page": "Nothing on this page could be read as text, so the page is kept as an "
    "image, marked “Not converted”.",
    "handwriting_on_page": "This is handwriting on a typed page and this tool does not "
    "convert handwriting on typed pages, so it is kept as an image after the typed text.",
}

# DocumentResult.notices: one sentence per notice code for the job page's list ("Not
# handled in this file" and "Worth checking"; core.types.Notice). Kept apart from the
# check and fix-reason tables because a notice is not a review decision. Every code in
# core.types.NOTICE_CODES has an entry (tests/test_web_review_messages.py). The wording
# is a placeholder until the builder that emits each code writes its own sentence.
NOTICE_MESSAGES: dict[str, str] = {
    "table_not_rebuilt": "A table is kept as an image of the original, so screen readers "
    "cannot read its cells.",
    # Pages and hidden text (pipeline.page_type). "{count}" is replaced by the number of
    # characters (web.job_page). Final wording for the beta (handwriting design, 3.3).
    "scanned_page": "A scanned image, not converted: the page is kept as an image.",
    "handwritten_page": "Handwritten, not converted: the page is kept as an image.",
    "unread_page": "Drawings or writing this tool could not read, not converted: the page is "
    "kept as an image, after any text read from it.",
    "handwriting_on_page": "Handwriting on a typed page: the typed text was converted, and "
    "the handwriting is kept as an image after it.",
    "ocr_text_layer_ignored": "Hidden text that a tablet or a text recognition (OCR) program "
    "guessed from the page ({count} characters) was not used.",
    "hidden_text_ignored": "Some text in the PDF ({count} characters) cannot be seen when "
    "the page is shown or printed, so it was left out: if students need it, add it to your "
    "source file and upload that again.",
    "code_block": "Program code is kept as a code block with its line breaks and "
    "indentation, without highlighting.",
    "footnote": "A footnote is kept where it falls in the text; its mark in the text links "
    "to it where the mark could be found.",
    "colour_reference": "The text refers to a colour (for example, shown in red), so add "
    "a cue that does not depend on seeing the colour.",
    "colour_only": "A figure tells things apart by colour alone, so add labels or patterns "
    "that do not depend on seeing the colour.",
    "scripts_flattened": "Raised or lowered text here (an exponent, a charge, a subscript) "
    "could not be kept and is written on the line, so check it reads right.",
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
    "image_page_converted": "A scanned or handwritten page was converted from an image of "
    "it: nothing in the PDF can confirm the reading, so check it against the original, which "
    "is kept with the page.",
    "page_omitted": "You left this page out of the student file and of the review, and the "
    "file says that it is not there.",
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
