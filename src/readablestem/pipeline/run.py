"""The conversion pipeline end to end.

convert_pdf: layout.analyze -> per page: crops (serial, calling thread) -> equation/figure/
table steps (thread pool); once every crop is made and the steps are at work, still on the
calling thread: text_pass.apply and heading_fixes (they need no model, so they overlap the
model calls instead of delaying the first one) -> assemble.render_html ->
html_checks.static_checks.

Design notes:

- PyMuPDF is not thread-safe, so every crop is made on the calling thread. Model calls
  are I/O-bound, so the steps run in a `ThreadPoolExecutor(concurrency)`. Cropping page
  N+1 overlaps with the model calls for page N.
- One element's failure never sinks the document. A step that raises becomes a FAILED
  fix with reason "internal_error" (a crop that fails: "crop_error"). The exception
  message is not logged: it may quote document text.
- A misconfigured run is different: `config.ConfigError` (no API key; WP1's client reads
  it on its first call) or `pricing.UnknownModelError` (an unpriced model) would fail
  every element the same way, so the first one stops the run and is re-raised. The
  caller reports one clear error instead of a document with everything flagged.
- A `BudgetExceeded` from any step (or a step returning reason "budget_<scope>") stops
  new model work: queued tasks are cancelled and every element not yet processed
  becomes FAILED with reason "budget_<scope>". The document is still assembled, with
  the originals kept and flagged.
- Calls a step made before it raised were still billed, so each step gets a recording
  wrapper around the client and those calls are kept on the FAILED fix: cost and call
  counts stay true.
- Static checks of the HTML are stored on `DocumentResult.html_checks` and, as before,
  returned on `PipelineOutput.checks` (a subclass of `ConversionOutput`).
- Pages kept as images: layout says which pages are handwritten or scanned, or have
  something painted on them from which nothing was read (`Layout.kept_pages`,
  `pipeline.page_type`; hidden text is never read at all). Each becomes a figure element
  for the whole page that is FAILED before any model work, with the reason
  "handwritten_page", "scanned_page" or "unread_page" (cropped, so the output keeps the
  page image, marked "Not converted"); handwriting on a typed page arrives as figure
  elements for its regions and is FAILED the same way ("handwriting_on_page"). Nothing of
  them goes to a model. The fixes with the same reason are one decision (`Fix.group_key`
  "pg-..." and `group_size`), so a 20-page handwritten file asks once. The pages are
  listed on `DocumentResult.image_only_pages`, and every page's type on
  `DocumentResult.page_types` (content-free). The web app reports them to the user.
- With `FEATURE_IMAGE_PAGES` (ADR 0007), the kept pages whose content is a picture or ink
  (`page_type.image_kind`) are read from a picture of the page first, one vision call each
  (`_image_pages_pass`). A page that is read leaves the kept pages: its blocks become
  elements, the fixes decided without a model are put in place before any step runs, the
  display equations are read from the crops of their boxes by the equation step (with the
  page reader's LaTeX as a second reading), the headings go through the heading step and
  `image_page.tier_headings`, and the page is listed in `DocumentResult.image_pages` with its
  own cost. A page that is not read stays kept, exactly as before.
- Run-level outcomes are recorded on the result, content-free: `stopped_by` (the
  "budget_<scope>" that stopped model work early, else None) and `html_checks`.
- `layout` (WP2) keeps a few opened PDFs cached for cropping. Once the last crop is made
  (or the run fails) the document is released, so a long-lived worker does not keep a
  user's PDF in memory after its job.
- Tracer spans (document, headings, page, step, model_call, assemble) carry ids, page
  numbers, models, token counts, costs, statuses and reason codes, never document text.
  Attributes are scalars only (WP6 drops anything else); each model request gets its own
  `model_call` span opened with `model=`, which WP6 records as a Langfuse generation.
- Options (`ConversionOptions`: feature flags and the course's notation) are in force for
  the whole run (`pipeline.options.using`), so every step reads them with
  `options.current()`, pool threads included (each task runs in a copy of the context).
  They decide what the steps produce; the stored result alone decides the HTML.
- Content features hook in here: TABLE elements go to
  `table_step.rebuild` like equations and figures go to their steps; `stage_progress`
  receives `ProgressEvent`s; notices and source facts go on the result. The text pass
  (links, cross-references, the document and passage languages, colour runs) runs once
  after layout, before any step; it sets `lang`, `lang_source` and its own notices, and
  never fails the conversion.
- Live progress (`stage_progress`, for the job page): "layout" when analysis starts and
  again with the pages and the elements found per kind; "pages" with the scanned pages read
  from their pictures (`image_page.run`), on a counter of its own; "equations", "figures" and
  "tables" with how many of each are done, first with their totals and then after every
  element (the receiver decides how often to store them); "assemble" and "checks" as the
  HTML is written and checked. All on the calling thread, content-free.
- Finished steps can be kept and reused (`StepCache`, T3-1): with a cache, every model
  step's answer that is worth keeping (`reusable`: not a failure that a new try could
  change) is stored under a key made from the element, its crop, its nearby text, the
  options, the behaviour settings and the conversion code (`step_key`), and a later run of
  the same document finds it there instead of calling the model again. The passes that ask
  about a whole page (page math, the page witness, pages read from a picture) keep each
  answer under the key of its question (`models.question.question_key`) and ask only for
  what a previous run did not get (`_PageAnswers`). The web app keeps both per job so that
  "Try again" on a stopped conversion pays only for what was not finished.
- After the steps, `pipeline.notices` derives the notices that follow from the fixes
  (a table kept as an image, a scanned page, a colour-only figure, a chart without a
  long description) and `pipeline.source_facts` measures the PDF for the before/after
  card. Neither can fail the run.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import threading
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache, partial
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field

from readablestem.config import ConfigError, Settings
from readablestem.core import triage
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import (
    BBox,
    CallFact,
    Check,
    ConversionOptions,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    ImagePagesSummary,
    Kind,
    ModelCall,
    NotFinished,
    Notice,
    PageMathSummary,
    PageStageFact,
    PageStats,
    PageWitnessSummary,
    ProgressEvent,
    SourceFacts,
    Stage,
    Status,
    inline_marker,
)
from readablestem.models import question
from readablestem.models.base import T, VisionClient, VisionRequest
from readablestem.models.math_readers.base import MathReader
from readablestem.models.pricing import UnknownModelError
from readablestem.observability.tracing import NoopTracer, Tracer
from readablestem.pipeline import (
    assemble,
    decoration,
    equation_scale,
    equation_step,
    figure_step,
    font_codes,
    heading_step,
    html_checks,
    image_page,
    image_page_check,
    image_page_reconcile,
    image_page_vector,
    layout,
    page_labels,
    page_math,
    page_math_reconcile,
    page_type,
    page_witness,
    say_once,
    shadow,
    slides,
    step_facts,
    table_math,
    table_step,
    tables,
    tagged,
    text_pass,
    witness_reconcile,
)
from readablestem.pipeline import notices as notice_rules
from readablestem.pipeline import options as run_options
from readablestem.pipeline import source_facts as pdf_facts
from readablestem.source import envelope as source_envelope

log = logging.getLogger(__name__)

# 0.3.0 (2026-10-08): review rules changed, a sign or relation that differs from the text
# layer is a required decision (`signs.compare_signs`). Results and eval reports from
# 0.2.0 were made under the old review policy.
# 0.4.0 (2026-10-09): the content-feature data model:
# TABLE elements and fixes, figure long descriptions and data tables, links, colour
# runs, passage languages, notices and source facts on the result, conversion options.
# Results from 0.3.0 load unchanged (every new field has a default); a table detected by
# 0.4.0 is a required decision ("table_not_rebuilt") until rebuilding is switched on.
# 0.5.0 (2026-10-14): the Tier 1 and Tier 2 changes of the launch integration (CHANGELOG.md):
# layout and structure rules, the Claude second check, speech readings, review rules for
# equations as pictures and half fractions, and the model-cost settings. Results from 0.4.0
# load unchanged; their numbers were made under the old rules.
# 0.5.1 (2026-11-02): layout structure fixes (docs/layout.md, "Known failure modes"):
# bookmarks as heading evidence, numbered and run-in headings, paragraphs joined over page
# breaks (element ids after the first join shift), inline maths joined over a line break.
# Heading fingerprints of the review carry change where a heading had math-italic letters.
# Results from 0.5.0 load unchanged; their numbers were made under the old rules.
# 0.5.2 (2026-11-03): the MathML shape for screen readers (docs/chemistry.md, "MathML shape"):
# one upright mi per element symbol, states as ( aq ), nuclides as mmultiscripts, `intent` on the
# math element of a formula or a reaction, the degree sign, the overline and a labelled arrow.
# The MathML of an equation with chemistry in it (and `^\circ`, `\overline`, `\xrightarrow`)
# changes; its LaTeX does not, and review carry compares the LaTeX, so revised uploads carry what
# is unchanged. Results from 0.5.1 load unchanged; their MathML keeps the old shape.
# 0.5.3 (2026-11-03): slide decks are read frame by frame (`pipeline/slides.py`): an overlay step
# repeated on its frame's last page is not read, frame titles are headings, headline furniture is
# dropped. Every PDF: a ligature's letters come out in order ("Stiffness", not "Stifnf ess"), and
# UTF-16 page labels are read. Results from 0.5.2 load unchanged.
# 0.5.4 (2026-11-03): inline formula edges from the glyphs (`pipeline/glyph_math.py`,
# docs/layout.md "Inline span edges"): a comma, colon or semicolon set as an index belongs to
# the formula, a radical is not cut from its radicand, an upright function name touching its
# bracket and a percent sign
# after a number are in the formula, and the unit after a number ("24 h", "m^3") is out of it. An
# inline formula of a TeX-made PDF can start or end at another glyph, so its crop, its id and the
# paragraph's marker text change. Results from 0.5.3 load unchanged.
# 0.5.5 (2026-11-03): a table cell that is one formula by its glyphs is an inline equation of the
# table (`layout._cell_maths`, `pipeline/table_math.py`, docs/tables.md "Maths cells from glyphs"):
# the equation step reads its crop and the page writes the reading into the table's cell when the
# table step wrote it as text. TeX-made PDFs with tables on: more equation elements and model
# calls, and `<math>` in table cells. Results from 0.5.4 load unchanged.
# 0.5.6 (2026-11-03): a relation set in a maths font before a number (`\approx 0.14`, `\geq 2`,
# `\geq 90\%`) is one inline formula in a TeX-made PDF (`glyph_math.relation_runs`, docs/layout.md
# "Short formulas the runs miss"); the run finder had dropped the relation as the tail of a formula.
# Rarely changes a paper (3 formulas in 25 development papers). Results from 0.5.5 load unchanged.
# 0.5.7 (2026-11-03): a PDF is TeX-made when its producer or creator names TeX as a whole word
# (`page_type.TEX_PRODUCER`): XeLaTeX and Tectonic (`xdvipdfmx`), LuaTeX, dvips and dvipdfm(x) now
# get the TeX reading rules, and Quartz ("PDFContext") and iText no longer do. TeX files with
# those producers read differently: units out of a formula, table-cell and inline-formula edges.
# Results from 0.5.6 load unchanged.
# 0.5.8 (2026-11-03): arXiv's own build ("arXiv GenPDF (tex2pdf:...)") and VTeX count as TeX
# producers again (0.5.7's whole-word match lost them: 3 of 25 development papers read with no TeX
# rules, so units stayed in formulas); a table cell of only maths-symbol glyphs (a lone
# `\checkmark`, `\times`) is an inline cell equation; a cell equation's line crop and glyph
# context are cut to its own column (the next column's "(5)" was read into it). Results from 0.5.7
# load unchanged.
# 0.5.9 (2026-11-03): corrections from the PDF's own glyphs, behind EQUATION_GLYPH_RECONCILE (off by
# default, so results are unchanged until it is turned on): one named lookalike symbol of a
# TeX-made PDF, a unit past the box, a printed slash, a display's equation number, `\mathscr` for
# an RSFS face, and `\boldsymbol` for a table cell's bold italic letter. Results from 0.5.8 load
# unchanged.
# 0.5.10 (2026-11-11): a LaTeX source's result carries what LaTeXML reported
# (`DocumentResult.source_report`, `source.report`), and a source that loses content outside a
# formula stops with a code that names the cause. PDFs are unchanged. Results from 0.5.9 load
# unchanged and are not judged again.
# 0.5.11 (2026-11-11): the text and figure boxes of a PDF page change. Codes of symbol and TeX
# fonts are read as what the font draws (`font_codes`, behind FONT_CODE_TABLES, on): TeX's
# ligatures and accents at control codes, Symbol's Greek, Wingdings bullets. A vector chart's
# figure takes its outer labels (axis values, category names, a rotated or centered axis title),
# so its crop grows to them; a small drawing on a typed page is handwriting only with pen
# evidence in its strokes (a skeletal structure's bonds now go to the figure step). A LaTeX
# source's outcome changes too (`source.support`, the same unreleased wave): a source in a class
# read as an article converts instead of stopping on that class, one in a refused class or
# package stops in the conversion child with its code and name, and a package stop records
# whether the package is the instructor's own. The LaTeX reader leaves nothing out unsaid
# (`source.ltx_reader`): a picture outside a float is a figure after its block, a float's other
# words are read, `\intertext` and `\date` are paragraphs, a formula LaTeX could not read in a
# table, heading or caption makes that item a required decision, and what no rule reads is
# counted (`SourceReport.unread_words`, `unread_pictures`) and said (`source_unread`). The stop
# rule names a missing bibliography and a special character in the text, so source jobs gain
# figures and decisions. A date, an address or a compound broken at a line end keeps its token
# whole (`hyphens.joins_inside_token`): no space inside "(2012-08-17)" or "project/memspectrum/".
# A figure is written with empty alt text only as decoration with evidence
# (`decoration.settle`, WS6.4, the same wave): one the model calls decorative with no repeat,
# rule shape or person behind it is a required decision, and one whose reading failed is
# decoration when the evidence says so, so the review load moves. A glyph the file's own table
# does not decode is read by the name its font gives it (`glyph_names`, the same wave), and a
# row of numbers inside a maths row's box joins that equation, so the text of equations and of
# the paragraphs around them changes on TeX pages. Results from 0.5.10 load
# unchanged; their page is made again (`assemble.RENDER_VERSION` 6).
# 0.5.12 (2026-11-19): the rest of the wave that 0.5.11 began, raised once more because reports
# and records were made at 0.5.11 before it (CHANGELOG.md has each change). What a PDF's result
# holds moves: words in bold, italic, a fixed-pitch face or under a highlight, struck text,
# blanks and checkboxes are runs (`Element.runs`, CONTENT_RUNS on), and so is a kept line end
# ("break": a poem, an address); a name in small capitals is text in its own case; a formula's
# ActualText is chosen by page; an equation number at the far edge stays with its equation; a
# slide deck is found by its pages and a file may hold several; "Abstract" alone on its line is
# a heading, an algorithm's steps are one code block, a glossary, index or bibliography entry is
# a block of its own, footnotes the file names are notes; a long table caption is attached and
# a table with no header cell is a required choice (`header_source`); links come from typed
# addresses, numbered references and a LaTeX or Word file's own references; the document's
# language is settled once. Results from 0.5.10 and 0.5.11 load unchanged and their page is
# made again (`assemble.RENDER_VERSION` 8). An older release refuses a result that holds a
# value its closed lists do not have (a link of kind "typed", a new notice code, the origin
# "docx"), so a deploy of this version is not rolled back past it once a document has been
# converted (docs/deploy.md "Roll back").
# 0.5.13 (2026-11-19): what a PDF's result holds moves again (CHANGELOG.md has each change).
# Lists: a bullet is told by how it behaves (a sign repeated at one edge), a bullet glued to
# its text is an item when a sibling has it, sub-bullets under a numbered item are a level
# down, a grid of options and the two series of a matching exercise are lists under their
# question, and a style change inside an item no longer cuts it. Blocks: one function decides
# where a block starts (a note, a theorem label, the next number of a numbered column), and a
# paragraph or list item cut by a page break is one element (`joins._goes_on`). A table's box
# no longer takes the next question; the region tree is not applied to the wrong pages. A
# paragraph can hold a role (`Element.role`: a sidebar, a quotation), which the Word and LaTeX
# readers set for a block quotation and a reviewer sets by hand, and a reviewer can leave one
# paragraph out (`DocumentResult.left_out_blocks`). Results from 0.5.10 to 0.5.12 load unchanged
# and their page is made again (`assemble.RENDER_VERSION` 9). An older release ignores a role
# (the paragraph is plain again) and refuses a result that holds the notice code
# `block_omitted` (a paragraph left out), so the roll-back limit of 0.5.12 stands
# (docs/deploy.md "Roll back").
# 0.5.14 (2026-11-19): one raise for the wave after 0.5.13 (CHANGELOG.md has each change). What
# a PDF's result holds moves: a one-page file keeps no bare page number and no "Page 3 of 9"
# line; equation numbers left in the text or made paragraphs go to their equations, and the mark
# that ends a fraction stays with it; more of the footnotes a file names are found and linked;
# lettered titles, "(i)" after "(h)" and option lists; a table that goes on from the page before
# is marked and written with that part's header row; an algorithm with line numbers, wrapped
# steps or an equation in it is one or two code blocks with the numbers set apart; the table
# search stops on CPU time; text painted where nobody can see it is left out and counted
# (`PageStats.unseen_chars`). A Word or LaTeX file's result: bold, italic and code in table
# cells (`TableCell.runs`), struck code stays struck, a table with no header marking is a
# required choice. New notice codes: `table_header_missing`, `table_header_carried`,
# `struck_not_kept`, `hidden_text_check`, `tables_not_searched`, `word_table_flattened`.
# Results from 0.5.10 to 0.5.13 load unchanged and their page is made again
# (`assemble.RENDER_VERSION` 10). 0.5.13 opens a 0.5.14 result (it has no words for the new
# notice lines and drops a cell's runs), so the roll-back limit stays at 0.5.12
# (docs/deploy.md "Roll back").
# 0.5.15 (2026-11-20): one raise for the wave after 0.5.14 (CHANGELOG.md has each change). What
# a PDF's result holds moves: a bold or numbered line in a run of its series is an item, not a
# heading; a lettered title that wraps is one heading; a series that starts again after a cut is
# a level down (`Element.list_level`); a reference list's initials are no lettered list; a number or
# a row of labels is no footnote, and a note the file names that no link points at is linked by
# its mark; a paragraph is no glossary entry with the region tree on (the tree stays off); a
# small drawing does not join a figure across a line of prose; a raised digit in a figure
# caption is kept raised (`Element.caption_marked`); an algorithm that holds a bold letter keeps
# its lines; tables with no ruling lines are found (a box under a "Table N" line, a strip a few
# lines cross, a table of marks on a first page) and two unruled columns with no caption are no
# table. A Word or LaTeX file's result: a link in a table cell (`TableCell.links`, new, left out
# when empty), emphasis and links in a table too large for a grid, a single italic letter makes
# no run, a strike set by a paragraph style, "(picture below)" where a picture stood in a
# sentence. No new notice code. Results from 0.5.10 to 0.5.14 load unchanged and their page is
# made again (`assemble.RENDER_VERSION` 11). 0.5.14 opens a 0.5.15 result (it ignores
# `TableCell.links`: the cell shows its words, no links; it serves a stored render-11 page as it
# is, links shown, until a review change makes the page again and writes the result back through
# its own model, which drops the links for good), so the roll-back limit stays at 0.5.12
# (docs/deploy.md "Roll back").
# 0.5.16 (2026-11-21): one raise for the wave after 0.5.15 (CHANGELOG.md has each change). What
# a PDF's result holds moves: a file with a page the probe cannot draw is read page by page and
# that page is a figure with no picture, reason `page_not_rendered` (a required decision) and
# the notice of that name, where it failed whole; a section number or a page number beside math
# in a contents entry stays text, a contents title that wraps is one entry, two lines of prose
# ending in an ellipsis are no contents page, and a capital alone on its line heads an index
# section only when its first entry is near; a line that is a formula or a bare number is no
# slide title, a deck with scanned slides is one deck and its headings are levelled with it; a
# web address printed on two lines is joined with no space where the file's link annotations run
# across the break; a table caption may run to eight lines and two text columns with prose are
# no table; the columns under the head of a first page are found and a numbered line that runs
# to the right edge keeps its block; a page read without its ActualText has the notice
# `actualtext_ignored`. A LaTeX file's result: a heading holds its words only (a picture in it
# is a figure of its own), a link of only a picture reads as the picture's alt text or its
# address, a picture in a table cell leaves "(picture below)". New notice codes:
# `page_not_rendered` (required) and `actualtext_ignored` (optional); the new figure reason is
# `page_not_rendered`. No new field in a stored result. The page trace (`StepFacts.page_steps`,
# `PageStageFact`) is kept in a measurement's run record only; the stage "pages" is a key of a
# running job's progress row; a split paragraph is a new `ReviewEdit.action` ("split_block")
# whose parts are ordinary elements. Results from 0.5.10 to 0.5.15 load unchanged and their
# page is not made again (`assemble.RENDER_VERSION` stays 11: no stored result can hold
# `page_not_rendered`, the one new word on the student page, "Not converted. This page could not
# be shown."). 0.5.15 opens a 0.5.16 result (run on 2026-11-21: it loads all 58 notice codes and
# the split parts, drops no stored key when a review change writes the result back, and undoes a
# split), but it has no words for the new reason: a page that could not be drawn is written
# "No description. This figure has no text description yet." (as a draft, "Needs a description.")
# as soon as it makes the page again (a review change, a draft, a cleared page), and its record
# leaves the split change out. The roll-back limit stays at 0.5.12 (docs/deploy.md "Roll back").
# 0.5.17 (2026-11-23): one raise for wave 4 (CHANGELOG.md has each change). What a PDF's result
# holds moves: the head or foot of a one-page file set smaller than the body and ending in a bare
# page number leaves the text; a contents line with no leaders that runs up to its page number
# is an entry; a numbered paragraph whose first line is indented keeps its second line; a
# lettered title of four words or more may end in a full stop; no inline equation is read inside
# a typed web address; the asterisk operator is a note sign, a number and a sign printed together
# are two marks, and a raised digit after a math letter is no note mark; five more algorithm
# shapes are one block. A Word or LaTeX file's result: a figure's or a table's caption keeps its
# links (`Element.caption_links`, `TableGrid.caption_links`, left out of the JSON when empty); a
# bracketed expression to the power T no longer stops a LaTeX source; a source that runs out of
# memory stops as `source_timeout`. The status "Needs review" reads "Needs your decision" (web).
# Behind switches that stay off (no change): LAYOUT_INLINE_JOINS, LAYOUT_DISPLAY_GROUPING,
# LAYOUT_JOIN_PANELS, REGION_TREE, EQUATION_FILE_FACTS, TABLE_FILE_FACTS, SCAN_LAYER_CHECK,
# PAGE_CHECK_SIGNALS, FIGURE_LABELS_ALWAYS. One new notice code, only behind SCAN_LAYER_CHECK:
# `image_page_layer_differs`; no new figure reason. Results from 0.5.10 to 0.5.16 load
# unchanged and their page is not made again (`assemble.RENDER_VERSION` stays 11: a stored
# result has no caption links, and without them a caption is written as before). 0.5.16 opens
# a 0.5.17 result (checked on 2026-11-23: an element and a table with caption links load) but
# drops the caption links, so a caption written by 0.5.16 again has no links.
# The roll-back limit stays at 0.5.12 (docs/deploy.md "Roll back").
# 0.5.18 (2026-11-23): the production profile turns on REGION_TREE,
# LAYOUT_INLINE_JOINS, LAYOUT_DISPLAY_GROUPING and LAYOUT_JOIN_PANELS (docs/eval.md "The switch
# release of 2026-11-23"), so a PDF's columns are found by the region tree, an inline formula or
# a display set over several lines is one equation box, and the panels of one figure are one
# figure: what a PDF's result holds and what its equation and figure requests carry move. The
# result's schema is the same, so results from 0.5.10 to 0.5.17 load unchanged and their page is
# not made again (`assemble.RENDER_VERSION` stays 11), and 0.5.17 opens a 0.5.18 result. Each
# switch goes back off alone with its profile row and the Railway variable of the same name.
# The roll-back limit stays at 0.5.12 (docs/deploy.md "Roll back").
# 0.5.19 (2026-11-23): one raise for wave 5 (CHANGELOG.md has each change). What a PDF's result
# holds moves: six more code font names, numbered listings one block, addresses and name lists
# not code, a code-face run inside a formula stays in it; algorithm steps listed before their
# numbers stay in the block; panel header labels belong to their figure; two more
# equation-number shapes label their display, one number to a display. Behind a new
# switch that stays off (no change): LAYOUT_FIGURE_ART (art a clip hides, rows a figure's edge
# cuts, a hand-drawn figure named by typed labels).
# The result's schema is the same: results from 0.5.10 to 0.5.18 load unchanged and their page
# is not made again (`assemble.RENDER_VERSION` stays 11), and 0.5.18 opens a 0.5.19 result.
# The roll-back limit stays at 0.5.12 (docs/deploy.md "Roll back").
PIPELINE_VERSION = "0.5.19"

ProgressFn = Callable[[int, int], None]  # (pages_done, pages_total)
# Live progress by stage, for the job page (content-free; see `ProgressEvent`).
StageProgressFn = Callable[[ProgressEvent], None]

# Neighbouring text sent with each crop as untrusted context (the client fences it).
# List items are text too: a display equation inside a numbered solution step is
# introduced by that step, not by the paragraph before the list.
CONTEXT_CHARS = 600
_TEXT_KINDS = (Kind.PARAGRAPH, Kind.HEADING, Kind.LIST_ITEM)
_MODEL_KINDS = (Kind.EQUATION, Kind.FIGURE, Kind.TABLE)
# Errors that mean the run itself is misconfigured, not that one element is hard: they
# stop the run and propagate (see the module docstring).
FATAL_ERRORS: tuple[type[Exception], ...] = (ConfigError, UnknownModelError)
# Fix.reason of the figure that stands for a scanned page (nothing on it to convert).
SCANNED_PAGE_REASON = page_type.SCANNED_PAGE
# Fix reasons of what is kept as an image before any model work: a whole page, and a
# region of handwriting on a typed page (module docstring).
PAGE_REASONS = frozenset(
    {
        page_type.SCANNED_PAGE,
        page_type.HANDWRITTEN_PAGE,
        page_type.UNREAD_PAGE,
        page_type.PAGE_READ_FAILED,
        page_type.PAGE_NOT_RENDERED,
    }
)
KEPT_REASONS = PAGE_REASONS | {page_type.HANDWRITING_ON_PAGE}
# `Fix.group_key` of the kept images with one reason: "pg-" + the first one's element id
# (content-free, like a heading group's "hg-...").
PAGE_GROUP_PREFIX = "pg-"
LETTER_BOX: BBox = (0.0, 0.0, 612.0, 792.0)  # a page whose size could not be read
# The live-progress stage of each kind that goes to a model step.
_STAGE_OF: dict[Kind, Stage] = {
    Kind.EQUATION: "equations",
    Kind.FIGURE: "figures",
    Kind.TABLE: "tables",
}


class PipelineOutput(ConversionOutput):
    """`ConversionOutput` plus the static checks of `result.html`.

    The same checks are on `result.html_checks` (stored with the result); this field is
    kept for callers written before that existed.
    """

    checks: list[Check] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers


class _RecordingClient:
    """Passes requests to the real client and keeps every `ModelCall` it returns.

    Each request also gets a `model_call` span. It is opened with `model=` (known from the
    request before anything is sent) so a WP6 tracer records it as a generation, and it
    is closed with the call's tokens, cost and outcome.
    """

    def __init__(
        self,
        inner: VisionClient,
        tracer: Tracer,
        observer: Callable[[ModelCall], None] | None = None,
    ) -> None:
        self._inner = inner
        self._tracer = tracer
        self._observer = observer
        self.calls: list[ModelCall] = []

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        with _span(
            self._tracer,
            "model_call",
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
        ) as span:
            parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
            self.calls.append(call)
            if self._observer is not None and not call.shared:  # a shared answer was not sent
                self._observer(call)
            _annotate(
                span,
                model=call.model,
                outcome=call.outcome,
                attempt=call.attempt,
                input_tokens=call.input_tokens,
                cached_input_tokens=call.cached_input_tokens,
                output_tokens=call.output_tokens,
                reasoning_tokens=call.reasoning_tokens,
                cost_usd=call.cost_usd,
                latency_ms=call.latency_ms,
                shared=call.shared,
            )
        return parsed, call

    def __getattr__(self, name: str) -> Any:  # anything else a step may read
        return getattr(self._inner, name)


class _Asked:
    """One request of the job, while it is in flight and after: the first caller's answer,
    given to every caller of the same request once it has one."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.answer: tuple[BaseModel, ModelCall] | None = None


class _SharedAnswers:
    """The job's model client: a request this job already asked is not sent again.

    Equal `question.question_key`s (and the same fallback flag) are the same question. The
    first caller sends it; a caller that arrives while it is out waits for it, and one that
    arrives later does not wait. Each gets the first answer, as a copy, with a `ModelCall`
    that costs nothing and says `shared`, so the job's money and call count are the first
    call's. Only an answer that was read and whose call ended "ok" is given again: after
    any other end (a refusal, a timeout, an unreadable reply, or the first caller raising)
    the waiting caller sends its own request, as it would without this, so a retry or a
    fallback never meets a stored failure. Two identical pictures therefore get one
    description. Shared by every thread of the job; it sits above the recording client.
    """

    def __init__(self, inner: VisionClient, settings: Settings) -> None:
        self._inner = inner
        self._settings = settings
        self._lock = threading.Lock()
        self._asked: dict[str, _Asked] = {}
        self._handed: dict[str, list[ModelCall]] = {}  # every call given out, by question

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        key = f"{question.question_key(self._settings, request, schema)}:{is_fallback}"
        with self._lock:
            entry = self._asked.get(key)
            first = entry is None
            if entry is None:
                entry = self._asked[key] = _Asked()
        if not first:
            entry.done.wait()
            if entry.answer is not None:
                parsed, call = entry.answer
                handed = _shared(call)
                with self._lock:
                    self._handed[key].append(handed)
                return cast(T, parsed.model_copy(deep=True)), handed
            return self._inner.run(request, schema, is_fallback=is_fallback)
        try:
            parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
            if parsed is not None and call.outcome == "ok":
                entry.answer = (parsed.model_copy(deep=True), call)
                with self._lock:
                    self._handed[key] = [call]
            return parsed, call
        finally:
            entry.done.set()  # also when the call raised: a waiter sends its own

    def settle(self, fixes: Sequence[Fix]) -> None:
        """Put what an answer cost on the first fix of the document that has it. Which of two
        identical pictures asked first is a matter of thread timing, and the one that did is
        the one that paid; here the money moves to the one that comes first in reading order,
        so the same file gives the same fixes whatever the timing (`fixes` are in reading
        order). The total is what it was."""
        of = {id(call): n for n, fix in enumerate(fixes) for call in fix.calls}
        for calls in self._handed.values():
            kept = [call for call in calls if id(call) in of]
            paid = next((call for call in kept if not call.shared), None)
            first = min(kept, key=lambda call: of[id(call)], default=None)
            if paid is None or first is None or first is paid:
                continue
            for name in _PAID_FIELDS:
                one, other = getattr(paid, name), getattr(first, name)
                setattr(paid, name, other)
                setattr(first, name, one)

    def __getattr__(self, name: str) -> Any:  # anything else a step may read
        return getattr(self._inner, name)


# What a request cost and whether it was sent: the fields a shared call has none of.
_PAID_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "latency_ms",
    "billed_unseen_usd",
    "shared",
)


def _shared(call: ModelCall) -> ModelCall:
    """The call a caller is given for an answer that was already paid for."""
    none = {"cost_usd": 0.0, "billed_unseen_usd": 0.0, "shared": True}
    return call.model_copy(update={**dict.fromkeys(_PAID_FIELDS, 0), **none})


class _Stop:
    """Run-wide stop signals, set by workers and read by workers and the scheduler.

    `scope`: the first refused spend reservation; the rest of the run fails
    "budget_<scope>" and the document is still assembled. `fatal`: the first
    configuration error (`FATAL_ERRORS`); no further step runs and the run re-raises it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.scope: str | None = None
        self.fatal: Exception | None = None

    def trip(self, scope: str) -> None:
        with self._lock:
            if self.scope is None:
                self.scope = scope

    def abort(self, exc: Exception) -> None:
        with self._lock:
            if self.fatal is None:
                self.fatal = exc

    def reason(self) -> str:
        return f"budget_{self.scope or 'unknown'}"


def _failed(element: Element, reason: str, calls: Iterable[ModelCall] = ()) -> Fix:
    return Fix(
        element_id=element.id,
        kind=element.kind,
        status=Status.FAILED,
        reason=reason,
        calls=list(calls),
    )


class StepCache(Protocol):
    """Where finished steps are kept between runs of one document (module docstring).

    `lookup` returns the stored fix for this element when `key` is what it was stored
    under, else None; `store` keeps one. Both are called on the calling thread.

    `answer` returns what an earlier run kept for a page-level question (`{"payload": the
    model's answer, "call": its `ModelCall`}`), else None; `keep` keeps one. Both may be
    called from several threads at once. `answer` gives back only what was kept before this
    run began: a request this run sends twice is asked twice, as without a cache.

    A cache must not raise: a failure to read or write is a miss, never a failed run."""

    def lookup(self, element: Element, key: str) -> Fix | None: ...

    def store(self, element: Element, key: str, fix: Fix) -> None: ...

    def answer(self, key: str) -> dict[str, Any] | None: ...

    def keep(self, key: str, row: dict[str, Any]) -> None: ...


class _PageAnswers:
    """The client of a page-level pass when finished steps are kept (`StepCache`): a
    question an earlier run of this document already has the answer to is not asked again,
    and each new answer is kept. Only an answer the model gave and the step could read is
    kept; a refusal, an outage or an unreadable reply is asked again next time."""

    def __init__(self, inner: VisionClient, cache: StepCache, settings: Settings) -> None:
        self._inner = inner
        self._cache = cache
        self._settings = settings

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        key = question.question_key(self._settings, request, schema)
        kept = self._cache.answer(key)
        if kept is not None:
            try:
                return schema.model_validate(kept["payload"]), ModelCall.model_validate(
                    kept["call"]
                )
            except (KeyError, TypeError, ValueError):
                pass  # not readable by this release: ask
        parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
        if parsed is not None and call.outcome == "ok":
            self._cache.keep(
                key,
                {"payload": parsed.model_dump(mode="json"), "call": call.model_dump(mode="json")},
            )
        return parsed, call

    def __getattr__(self, name: str) -> Any:  # anything else a pass may read
        return getattr(self._inner, name)


# `Fix.reason` codes of a failure that trying again could change: the model did not answer
# or answered something unusable, or the step itself broke. Everything else a step ends
# with is its answer (a proposal, a refusal to propose, a check that failed).
_TRANSIENT_REASONS = frozenset({"internal_error", "crop_error"})
_TRANSIENT_PREFIXES = ("model_", "budget_")


def reusable(fix: Fix) -> bool:
    """Whether a later run may use this fix as it is instead of asking again."""
    if fix.status is not Status.FAILED:
        return True
    reason = fix.reason or ""
    return reason not in _TRANSIENT_REASONS and not reason.startswith(_TRANSIENT_PREFIXES)


# The settings a stored fix depends on whatever the profile lists (`_behaviour`).
_KEY_SETTINGS = (
    "model_provider",
    "main_model",
    "cheap_model",
    "decisions_model",
    "decisions_figure_verify",
    "decisions_equation_shadow",
    "claude_check_model",
    "claude_check_scope",
)
# The packages whose code and prompts make a fix out of a model's answer.
_CONVERSION_PACKAGES = ("core", "models", "pipeline", "source")


@cache
def code_identity() -> str:
    """A digest of the conversion code this process runs: every file of the packages that
    turn a model's answer into a fix, prompts and data files included. A stored fix is the
    model's answer after this code has checked and shaped it, so it is good for this code
    and no other; hashing the files asks nobody to remember a version bump. Where the
    files cannot be read it is the pipeline version."""
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    count = 0
    try:
        for package in _CONVERSION_PACKAGES:
            for path in sorted((root / package).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    digest.update(path.relative_to(root).as_posix().encode())
                    digest.update(b"\x00")
                    digest.update(path.read_bytes())
                    count += 1
    except OSError:
        count = 0
    return digest.hexdigest() if count else PIPELINE_VERSION


def _behaviour(settings: Settings) -> str:
    """The run's value of every setting that decides what a conversion gives: the ones the
    production profile lists as behaviour (`ops.profile`), and the models and checks named
    here in case the profile cannot be read."""
    names: set[str] = set(_KEY_SETTINGS)
    try:
        from readablestem.ops import profile

        names |= set(profile.load().settings)
    except Exception as exc:  # the key is then built from the named settings alone
        log.warning("step key: the profile was not read: %s", type(exc).__name__)
    values = {name: str(getattr(settings, name, "")) for name in sorted(names)}
    return json.dumps(values, separators=(",", ":"))


# The conversion options each step reads (`pipeline.options.current()`), by the kind it
# handles. A step's key holds these and no others, so a choice a step never reads (exam
# mode, for an equation or a table) leaves its stored answers findable. A step that starts
# to read another option names it here, or it would be given an answer made without it.
_STEP_OPTIONS: dict[Kind, tuple[str, ...]] = {
    Kind.EQUATION: ("notation", "notation_memory"),  # `notation_memory.entries_in_force`
    Kind.TABLE: ("tables",),  # `table_step.rebuild`
    Kind.FIGURE: ("exam_mode", "long_descriptions"),  # `figure_step.describe`
}


def _options_read(kind: Kind, options: ConversionOptions) -> str:
    names = _STEP_OPTIONS.get(kind)
    if names is None:
        return options.model_dump_json()
    return json.dumps(options.model_dump(mode="json", include=set(names)), sort_keys=True)


def step_key(
    element: Element,
    png: bytes,
    context_text: str | None,
    options: ConversionOptions,
    settings: Settings,
    words: Sequence[tables.PrintedWord] = (),
) -> str:
    """What a step's stored fix depends on, as a digest: the element (its text layer is
    checked against the answer), the picture, the nearby text sent with it, a table's words
    (`layout.table_words`, only when there are any), the conversion
    options (notation, exam mode, feature flags), every behaviour setting (models, efforts,
    switches) and the conversion code with its prompts (`code_identity`). Equal keys mean
    the same question, asked and handled the same way. Of the options, the key holds those
    the element's step reads (`_STEP_OPTIONS`). A recorded model answer is keyed by
    its question alone (`models.question`); this key is for a finished fix."""
    parts = [
        code_identity(),
        element.model_dump_json(exclude={"id", "order"}),
        hashlib.sha256(png).hexdigest(),
        context_text or "",
        _options_read(element.kind, options),
        _behaviour(settings),
    ]
    if words:
        parts.append(repr(words))
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _budget_scope(fix: Fix) -> str | None:
    """Scope if a step reported a refused reservation itself instead of raising."""
    reason = fix.reason or ""
    if fix.status is Status.FAILED and reason.startswith("budget_"):
        return reason.removeprefix("budget_") or "unknown"
    return None


_SCALARS = (str, int, float, bool)


def _annotate(span: Any, **attrs: Any) -> None:
    """Add result attributes to an open span. Never raises.

    WP6's span handle takes `set(**attrs)`; `update(**attrs)` is accepted too (simpler
    tracers). Only scalar values are sent and None is left out: WP6 keeps numbers,
    booleans and short code-like strings and silently drops anything else, so a dict or
    list here would just disappear.
    """
    method = getattr(span, "set", None)
    if not callable(method):
        method = getattr(span, "update", None)
    if not callable(method):
        return
    values = {key: value for key, value in attrs.items() if isinstance(value, _SCALARS)}
    if not values:
        return
    try:
        method(**values)
    except Exception:  # tracing must never break a conversion
        log.debug("span update failed", exc_info=False)


def _status_counts(prefix: str, fixes: Iterable[Fix]) -> dict[str, int]:
    """{"<prefix>_accepted": 3, ...}: per-status counts as flat scalar span attributes."""
    counts = Counter(fix.status.value for fix in fixes)
    return {f"{prefix}_{status}": n for status, n in sorted(counts.items())}


def _single_model(calls: Iterable[ModelCall]) -> str | None:
    """The model a step used, if it used exactly one (each call has its own span)."""
    models = {call.model for call in calls}
    return models.pop() if len(models) == 1 else None


@contextmanager
def _span(tracer: Tracer, name: str, **attrs: Any) -> Generator[Any]:
    """`tracer.span`, except that tracing never changes the outcome of the work.

    A tracer that fails to open or close a span is logged at debug level and ignored;
    an exception from the work itself always propagates (a tracer cannot swallow it).
    """
    try:
        manager = tracer.span(name, **attrs)
        span = manager.__enter__()
    except Exception:
        log.debug("tracer could not open a %s span", name)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            log.debug("tracer could not close a %s span", name)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            log.debug("tracer could not close a %s span", name)


# --------------------------------------------------------------------------- context


def _without_markers(text: str) -> str:
    """Document text for the model: our inline markers become a readable placeholder."""
    return assemble.INLINE_MARKER_RE.sub("[equation]", text)


def _head(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "... " + text[-limit:].lstrip()


def _inline_context(element: Element, parent: Element) -> str | None:
    """A window of the parent paragraph centred on this equation's marker."""
    text = parent.text or parent.caption_marked or ""
    marker = inline_marker(element.id)
    at = text.find(marker)
    if at == -1:
        return None
    half = CONTEXT_CHARS // 2
    before = _without_markers(text[:at])
    after = _without_markers(text[at + len(marker) :])
    if not (before.strip() or after.strip()):
        return None
    return f"{_tail(before, half)}[this equation]{_head(after, half)}".strip()


_CONTEXT_LABELS = {Kind.FIGURE: "[this figure]", Kind.TABLE: "[this table]"}


def _neighbour_context(element: Element, before: str | None, after: str | None) -> str | None:
    half = CONTEXT_CHARS // 2
    label = _CONTEXT_LABELS.get(element.kind, "[this equation]")
    parts = []
    if before:
        parts.append(_tail(_without_markers(before), half))
    parts.append(label)
    if after:
        parts.append(_head(_without_markers(after), half))
    return "\n".join(parts) if (before or after) else None


def _first_text(elements: list[Element], indexes: Iterable[int], skip: str | None) -> str | None:
    """Text of the first element at `indexes` that is not `skip` (the parent paragraph)."""
    return next((elements[i].text for i in indexes if elements[i].id != skip), None)


def context_texts(elements: list[Element]) -> dict[str, str | None]:
    """Untrusted neighbouring text for each equation and figure (reading order).

    Inline equations get a window of their own paragraph around their marker. Block
    equations and figures (and an inline equation alone in its paragraph) get the end
    of the previous text element and the start of the next one, ~CONTEXT_CHARS in
    total. Inline markers become "[equation]".
    """
    by_id = {e.id: e for e in elements}
    text_at = [i for i, e in enumerate(elements) if e.kind in _TEXT_KINDS and e.text]
    result: dict[str, str | None] = {}
    for index, element in enumerate(elements):
        if element.kind not in _MODEL_KINDS:
            continue
        parent = by_id.get(element.parent_id or "")
        parent_text = parent.text or parent.caption_marked if parent is not None else None
        if parent is not None and parent_text and inline_marker(element.id) in parent_text:
            context = _inline_context(element, parent)
            if context is not None:
                result[element.id] = context
                continue
        skip = parent.id if parent is not None else None
        pos = bisect_left(text_at, index)  # first text element after this one
        before = _first_text(elements, (text_at[i] for i in range(pos - 1, -1, -1)), skip)
        after = _first_text(elements, (text_at[i] for i in range(pos, len(text_at))), skip)
        result[element.id] = _neighbour_context(element, before, after)
    return result


# --------------------------------------------------------------------------- steps


@dataclass(frozen=True)
class _Task:
    element: Element
    png: bytes
    context_text: str | None
    # An inline equation's line with its span boxed (`Settings.equation_line_context`).
    line_png: bytes | None = None
    # A table's words with their places in its crop (`layout.table_words`, `TABLE_FILE_FACTS`);
    # empty when the setting is off or the PDF prints none in the box.
    words: tuple[tables.PrintedWord, ...] = ()


@dataclass(frozen=True)
class _Done:
    element: Element
    fix: Fix
    elapsed_ms: int


def _step_for(kind: Kind) -> tuple[str, Callable[..., Fix]]:
    # Looked up at call time so tests (and the eval harness) can swap a step module's
    # function without touching this one.
    if kind is Kind.EQUATION:
        return "equation", equation_step.transcribe
    if kind is Kind.TABLE:
        return "table", table_step.rebuild
    return "figure", figure_step.describe


def _validated(fix: object, element: Element, calls: list[ModelCall]) -> Fix:
    if not isinstance(fix, Fix) or fix.element_id != element.id or fix.kind != element.kind:
        log.warning("step returned a fix for another element (kind=%s)", element.kind.value)
        return _failed(element, "internal_error", calls)
    if len(calls) > len(fix.calls):  # keep every billed call, even ones a step dropped
        return fix.model_copy(update={"calls": calls})
    return fix


def _run_step(
    task: _Task,
    *,
    client: VisionClient,
    settings: Settings,
    stop: _Stop,
    tracer: Tracer,
    observer: Callable[[ModelCall], None] | None = None,
) -> _Done:
    """Worker body: one element through its step.

    Raises only `FATAL_ERRORS` (after setting `stop.fatal`) and BaseException; any other
    exception becomes a FAILED fix.
    """
    element = task.element
    if stop.fatal is not None:  # the run is failing; this result is never used
        return _Done(element, _failed(element, "internal_error"), 0)
    if stop.scope is not None:  # budget gone while this task was queued
        return _Done(element, _failed(element, stop.reason()), 0)
    step_name, step = _step_for(element.kind)
    recorder = _RecordingClient(client, tracer, observer)
    started = time.perf_counter()
    with _span(
        tracer,
        "step",
        step=step_name,
        kind=element.kind.value,
        element_id=element.id,
        page=element.page,
    ) as span:
        try:
            extra: dict[str, Any] = {"line_png": task.line_png} if task.line_png is not None else {}
            if task.words:
                extra["words"] = task.words
            fix = step(
                element,
                task.png,
                client=recorder,
                settings=settings,
                context_text=task.context_text,
                **extra,
            )
        except BudgetExceeded as exc:
            stop.trip(exc.scope)
            fix = _failed(element, f"budget_{exc.scope}", recorder.calls)
        except FATAL_ERRORS as exc:  # misconfigured: every element would fail the same way
            log.warning("%s step stopped the run: %s", step_name, type(exc).__name__)
            stop.abort(exc)
            raise
        except Exception as exc:  # isolate: one element never sinks the document
            log.warning("%s step failed: %s", step_name, type(exc).__name__)
            fix = _failed(element, "internal_error", recorder.calls)
        else:
            fix = image_page.rescued(_validated(fix, element, recorder.calls), element)
            if (scope := _budget_scope(fix)) is not None:
                stop.trip(scope)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _annotate(
            span,
            status=fix.status.value,
            reason=fix.reason,
            model=_single_model(fix.calls),
            calls=sum(1 for c in fix.calls if not c.shared),
            cost_usd=fix.cost_usd,
            latency_ms=elapsed_ms,
        )
    return _Done(element, fix, elapsed_ms)


def _text_pass(
    pdf_bytes: bytes,
    elements: list[Element],
    pages: list[int],
    layout_lang: str,
    options: ConversionOptions,
    tracer: Tracer,
    figure_art: bool = False,
) -> text_pass.TextPassResult:
    """Links, cross-references, language, (with FEATURE_COLOUR) colour runs and (with
    CONTENT_RUNS; counted either way) struck runs on the elements, before any step
    (`text_pass.apply`; it never raises)."""
    with _span(
        tracer, "text", elements=len(elements), colour=options.colour, runs=options.content_runs
    ) as span:
        text = text_pass.apply(
            pdf_bytes,
            elements,
            pages=pages,
            layout_lang=layout_lang,
            colour=options.colour,
            runs=options.content_runs,
            chosen_lang=options.language,
            figure_art=figure_art,
        )
        _annotate(span, lang_source=text.lang_source, **text.counts())
    return text


def _in_deck(element: Element, decks: dict[int, int]) -> Element:
    """A heading the page reader made on a page of a slide deck says which deck it is in, as
    a typed slide's heading does (`slides.finish`), so it is levelled with the deck's slides."""
    if element.kind is Kind.HEADING and (number := decks.get(element.page)) is not None:
        reasons = [*element.reasons, f"{slides.DECK_REASON} {number}"]
        return element.model_copy(update={"reasons": reasons})
    return element


def _heading_fixes(elements: list[Element], tracer: Tracer, settings: Settings) -> list[Fix]:
    headings = [e for e in elements if e.kind is Kind.HEADING]
    with _span(tracer, "headings", count=len(headings)) as span:
        try:
            fixes = list(heading_step.heading_fixes(elements))
            image_page.tier_headings(
                fixes, elements, maths_required=settings.image_pages_maths_always_required
            )
        except Exception as exc:
            log.warning("heading step failed: %s", type(exc).__name__)
            fixes = [_failed(e, "internal_error") for e in headings]
        _annotate(span, fixes=len(fixes), **_status_counts("fixes", fixes))
    return fixes


# --------------------------------------------------------------------------- scheduling


class _Pages:
    """Tracks outstanding tasks per page; reports a page once all of its tasks are done."""

    def __init__(self, pages: list[int], progress: ProgressFn | None) -> None:
        self.total = len(pages)
        self.done = 0
        self._pending: dict[int, int] = dict.fromkeys(pages, 0)
        self._open: set[int] = set(pages)  # still being cropped/submitted
        self._progress = progress

    def add(self, page: int) -> None:
        self._pending[page] = self._pending.get(page, 0) + 1

    def submitted(self, page: int) -> None:
        self._open.discard(page)
        self._maybe_done(page)

    def finished(self, page: int) -> None:
        self._pending[page] -= 1
        self._maybe_done(page)

    def _maybe_done(self, page: int) -> None:
        if page in self._open or self._pending.get(page, 0) or page not in self._pending:
            return
        del self._pending[page]
        self.done += 1
        if self._progress is not None:
            self._progress(self.done, self.total)


def _send(
    stage_progress: StageProgressFn | None,
    stage: Stage,
    done: int = 0,
    total: int = 0,
    counts: dict[str, int] | None = None,
) -> None:
    """One `ProgressEvent` to the caller, if it asked for them. Like `progress`, an
    exception from the receiver aborts the run."""
    if stage_progress is not None:
        stage_progress(ProgressEvent(stage=stage, done=done, total=total, counts=counts or {}))


def _kind_counts(elements: Iterable[Element]) -> dict[str, int]:
    """Elements found per kind ("equation": 21), for the "layout" event."""
    return dict(sorted(Counter(e.kind.value for e in elements).items()))


def _unit_scale(_element: Element) -> float:
    return 1.0


def recrop(
    pdf: bytes, elements: list[Element], kinds: dict[int, str] | None = None
) -> dict[str, bytes]:
    """The pictures a conversion of `pdf` cuts for these elements, without converting
    anything: the crop of every equation, figure and table, an equation's cut sharp for the
    size it is shown at (T3-15), exactly as `_Run.crop_and_submit` does. For showing a
    stored result again (the sample document, the demo): no model is asked and nothing is
    paid. An element whose crop fails has none."""
    scale = equation_scale.scaler(elements)
    crops: dict[str, bytes] = {}
    vector: dict[int, image_page_vector.VectorPage] = {}
    # `kinds` (each page's `page_type` kind) is given only by a caller whose conversion drew
    # the pictures from strokes (`IMAGE_PAGES_VECTOR_CROPS`, off by default).
    if kinds:
        wanted = {e.page for e in elements if image_page_vector.wants_picture(e)}
        vector = image_page_vector.analyse(pdf, wanted, kinds)
    try:
        for element in elements:
            if element.kind not in _MODEL_KINDS or page_type.PAGE_NOT_RENDERED in element.reasons:
                continue
            try:
                png = layout.crop(pdf, element)
                if element.kind is Kind.EQUATION:
                    png = layout.crop(
                        pdf, element, dpi=equation_scale.crop_dpi(scale(element)), tag_dpi=True
                    )
                    png = (
                        image_page_vector.element_picture(
                            vector,
                            element,
                            dpi=equation_scale.crop_dpi(scale(element)),
                            tag_dpi=True,
                        )
                        or png
                    )
                elif (drawn := image_page_vector.element_picture(vector, element)) is not None:
                    png = drawn
            except Exception as exc:
                log.warning("crop failed: %s", type(exc).__name__)
                continue
            crops[element.id] = png
    finally:
        layout.release(pdf)  # the cached copy of a PDF is not kept
    return crops


def refresh_heading_fixes(result: DocumentResult) -> bool:
    """Decide the headings of a stored result again with today's heading step, in place;
    True if anything changed. The heading step needs no model (`heading_step`), so a stored
    result can follow a change to its rules without a new conversion: each heading's fix is
    replaced (same element, same place in the list), and the review counts in `stats` are
    counted again. Every other fix, and the models' answers, stay as stored."""
    fresh = {f.element_id: f for f in heading_step.heading_fixes(result.elements)}
    fixes = [fresh.get(f.element_id, f) if f.kind is Kind.HEADING else f for f in result.fixes]
    if fixes == result.fixes:
        return False
    result.fixes = fixes
    stats = result.stats
    stats.by_status = dict(Counter(f.status.value for f in fixes))
    stats.by_review = dict(Counter(f.review for f in fixes))
    stats.required_decisions = triage.required_decisions(fixes)
    return True


@dataclass
class _Run:
    """State of one conversion while its steps are in flight."""

    pdf_bytes: bytes
    settings: Settings
    options: ConversionOptions
    stage_progress: StageProgressFn | None  # live progress by stage, for the job page
    client: VisionClient
    tracer: Tracer
    pages: _Pages
    contexts: dict[str, str | None]
    pool: ThreadPoolExecutor
    stop: _Stop
    fixes: dict[str, Fix]
    crops: dict[str, bytes]
    page_ms: Counter[int]
    futures: dict[Future[_Done], Element]
    # Per model kind: how many elements it has, and the ids that have their fix.
    stage_totals: dict[Kind, int] = field(default_factory=dict)
    stage_done: dict[Kind, set[str]] = field(default_factory=dict)
    # Finished steps kept between runs (T3-1), and the key each element's step was asked under.
    cache: StepCache | None = None
    keys: dict[str, str] = field(default_factory=dict)
    # Told how each model request ended (`ModelCall`), for the status line about the model provider.
    observer: Callable[[ModelCall], None] | None = None
    # The scale an equation's picture is shown at in the student file (T3-15).
    eq_scale: Callable[[Element], float] = field(default_factory=lambda: _unit_scale)
    # Pen strokes of the handwritten vector pages read as images: the pictures of their lines
    # are drawn from these (`image_page_vector`), by 0-based page.
    vector: dict[int, image_page_vector.VectorPage] = field(default_factory=dict)
    # The paragraph (or list item, figure) each inline equation sits in, by id.
    parents: dict[str, Element] = field(default_factory=dict)
    # The time of each page's crops (`PageTrace`).
    trace: step_facts.PageTrace = field(default_factory=lambda: step_facts.PageTrace(enabled=False))

    def start_stages(self, elements: list[Element]) -> None:
        """Each model stage's total, counting what was decided before any model work (a
        scanned page) as done."""
        self.eq_scale = equation_scale.scaler(elements)
        self.parents = {e.id: e for e in elements if e.kind is not Kind.EQUATION}
        for kind, stage in _STAGE_OF.items():
            # (a converted page's own picture is cropped, not sent anywhere)
            members = [e.id for e in elements if e.kind is kind and not image_page.is_original(e)]
            self.stage_totals[kind] = len(members)
            self.stage_done[kind] = {i for i in members if i in self.fixes}
            if members:
                _send(self.stage_progress, stage, len(self.stage_done[kind]), len(members))

    def step_done(self, element: Element) -> None:
        """Count one equation, figure or table as done and say so."""
        stage = _STAGE_OF.get(element.kind)
        done = self.stage_done.get(element.kind)
        if stage is None or done is None or element.id in done:
            return
        done.add(element.id)
        _send(self.stage_progress, stage, len(done), self.stage_totals.get(element.kind, 0))

    def process_page(self, page: int, elements: list[Element]) -> None:
        """Crop this page's equations, figures and tables here (PyMuPDF), queue their steps."""
        work = [
            e
            for e in elements
            if (
                e.kind in _MODEL_KINDS
                or image_page.is_original(e)
                or image_page.wants_crop(e, self.fixes)
            )
            and not image_page.no_crop(e, self.fixes)
            and page_type.PAGE_NOT_RENDERED not in e.reasons  # no picture of it can be cut
        ]
        with (
            _span(self.tracer, "page", page=page, elements=len(elements), tasks=len(work)),
            self.trace.step(page, "crops"),
        ):
            for element in work:
                self.crop_and_submit(element)
        self.pages.submitted(page)

    def sharp_equation(self, element: Element) -> bytes | None:
        """An equation's crop for the student file, or None (not an equation, or the cut
        failed: the ordinary crop is used then)."""
        if element.kind is not Kind.EQUATION:
            return None
        if element.page in self.vector:
            return image_page_vector.element_picture(
                self.vector,
                element,
                dpi=equation_scale.crop_dpi(self.eq_scale(element)),
                tag_dpi=True,
            )
        try:
            return layout.crop(
                self.pdf_bytes,
                element,
                dpi=equation_scale.crop_dpi(self.eq_scale(element)),
                tag_dpi=True,
            )
        except Exception as exc:
            log.warning("sharp crop failed: %s", type(exc).__name__)
            return None

    def line_crop(self, element: Element) -> bytes | None:
        """The line of an inline equation with its span boxed, when the setting is on."""
        if not (
            self.settings.equation_line_context
            and element.kind is Kind.EQUATION
            and element.display == "inline"
        ):
            return None
        parent = self.parents.get(element.parent_id or "")
        across = layout.line_across(parent, element)
        try:
            return layout.crop_line(self.pdf_bytes, element, across)
        except Exception as exc:
            log.warning("line crop failed: %s", type(exc).__name__)
            return None

    def table_words(self, element: Element, png: bytes) -> tuple[tables.PrintedWord, ...]:
        """The words the PDF prints in a table's box with their places in its crop, when
        `TABLE_FILE_FACTS` is on; none otherwise, or for what is not a table, a page with no
        text there, or a failed read (the step then works as it did)."""
        if not self.settings.table_file_facts or element.kind is not Kind.TABLE:
            return ()
        if element.page in self.vector:
            return ()  # (a handwritten page's strokes are drawn: its picture is not the PDF's)
        try:
            return tuple(layout.table_words(self.pdf_bytes, element, png))
        except Exception as exc:
            log.warning("table words failed: %s", type(exc).__name__)
            return ()

    def read_glyphs(self, element: Element) -> None:
        """Keep the glyphs under an equation and beside it with the element, when the setting
        is on (`Element.glyphs`): the evidence of every check of a reading against the PDF.
        The neighbours are only read for an inline equation read with its line."""
        if not (self.settings.equation_glyph_checks and element.kind is Kind.EQUATION):
            return
        if element.glyphs is not None or not element.text:
            return
        reach = self.settings.equation_line_context and element.display == "inline"
        parent = self.parents.get(element.parent_id or "")
        across = layout.line_across(parent, element)
        try:
            glyphs = layout.line_glyphs(
                self.pdf_bytes,
                element,
                across,
                reach=reach,
                facts=self.settings.equation_file_facts,
            )
        except Exception as exc:
            log.warning("glyph read failed: %s", type(exc).__name__)
            return
        element.glyphs = glyphs

    def crop_and_submit(self, element: Element) -> None:
        self.raise_if_fatal()
        try:
            png = layout.crop(self.pdf_bytes, element)
        except Exception as exc:
            log.warning("crop failed: %s", type(exc).__name__)
            # (a prose block's crop, and a converted page's picture, are only conveniences)
            if element.kind in _MODEL_KINDS and not image_page.is_original(element):
                self.fixes[element.id] = _failed(element, "crop_error")
                self.step_done(element)
            return
        # The student file shows an unconverted equation at the size of its text, cut
        # sharp for that size (T3-15); the model is sent the same crop as ever, and the
        # review screen shows this one. Any other crop is as it was. On a vector page of
        # handwriting the picture is drawn from the strokes, for the model and the person alike.
        drawn = image_page_vector.element_picture(self.vector, element)
        if drawn is not None:
            png = drawn
        self.crops[element.id] = self.sharp_equation(element) or png
        if element.id in self.fixes or image_page.is_original(element):
            return  # decided before any model work: a scanned page, a converted page's picture
        if self.stop.scope is not None:  # still cropped: the output shows the original
            self.fixes[element.id] = _failed(element, self.stop.reason())
            self.step_done(element)
            return
        self.raise_if_fatal()  # a step may have failed while this element was being cut
        line_png = self.line_crop(element)
        words = self.table_words(element, png)
        self.read_glyphs(element)
        if self.cache is not None:
            key = step_key(
                element,
                png + (line_png or b""),
                self.contexts.get(element.id),
                self.options,
                self.settings,
                words,
            )
            if (kept := self.cache.lookup(element, key)) is not None:
                self.fixes[element.id] = kept  # finished in an earlier run: no model call
                self.step_done(element)
                return
            self.keys[element.id] = key
        work = partial(
            _run_step,
            _Task(element, png, self.contexts.get(element.id), line_png, words),
            client=self.client,
            settings=self.settings,
            stop=self.stop,
            tracer=self.tracer,
            observer=self.observer,
        )
        # Each task runs in a copy of this context, so a contextvars-based tracer nests the
        # step span under this page's span even though it runs on a pool thread.
        context = contextvars.copy_context()
        future: Future[_Done] = self.pool.submit(lambda: context.run(work))
        self.futures[future] = element
        self.pages.add(element.page)

    def collect(self, *, block: bool) -> None:
        """Record finished tasks; cancel the queue once the budget is gone."""
        while self.futures:
            done, _ = wait(
                list(self.futures), timeout=None if block else 0, return_when=FIRST_COMPLETED
            )
            if not done:
                return
            # (A failed step last: the others that finished with it are recorded first.)
            try:
                for future in sorted(
                    done, key=lambda f: not f.cancelled() and f.exception() is not None
                ):
                    self.record(future, self.futures.pop(future))
            except FATAL_ERRORS:
                # The failed step's own error surfaces here, usually before the steps that
                # were still running beside it were collected: keep those first.
                self.keep_finished()
                raise
            self.raise_if_fatal()
            if self.stop.scope is not None:
                for future in self.futures:
                    future.cancel()

    def raise_if_fatal(self) -> None:
        """Re-raise a worker's configuration error here (the pool is then shut down)."""
        if self.stop.fatal is not None:
            self.keep_finished()
            raise self.stop.fatal

    def keep_finished(self) -> None:
        """Before a configuration error ends the run: store the steps that did finish (the
        ones running at that moment included; the pool waits for them anyway). They were paid
        for, and "Try again" must not ask the model for them a second time."""
        if self.cache is None:
            return
        for future in list(self.futures):
            future.cancel()
        for future, element in list(self.futures.items()):
            if future.cancelled() or future.exception() is not None:
                continue  # not run, or the failed step itself, or a worker that died
            done = future.result()
            key = self.keys.get(element.id)
            if key and reusable(done.fix):
                self.cache.store(element, key, done.fix)
            self.futures.pop(future, None)

    def record(self, future: Future[_Done], element: Element) -> None:
        if future.cancelled():
            self.fixes[element.id] = _failed(element, self.stop.reason())
        else:
            done = future.result()  # re-raises a FATAL_ERRORS from the worker
            self.fixes[element.id] = done.fix
            self.page_ms[element.page] += done.elapsed_ms
            if self.cache is not None and (key := self.keys.get(element.id)) and reusable(done.fix):
                self.cache.store(element, key, done.fix)
        self.pages.finished(element.page)
        self.step_done(element)


# --------------------------------------------------------------------------- results


def _ordered_fixes(elements: list[Element], fixes: dict[str, Fix]) -> list[Fix]:
    ids = [e.id for e in elements]
    known = set(ids)
    return [fixes[i] for i in ids if i in fixes] + [f for i, f in fixes.items() if i not in known]


def _stats(
    pages: list[int],
    elements: list[Element],
    fixes: list[Fix],
    page_ms: Counter[int],
    wall_ms: int,
) -> DocumentStats:
    """Totals plus per-page numbers.

    `latency_ms` on the document is wall-clock time for the whole conversion. On a page
    it is the time spent inside that page's steps (model calls, retries, validation),
    summed: pages overlap in the thread pool, so per-page wall time would mislead.
    """
    page_of = {e.id: e.page for e in elements}
    per_page = []
    for page in pages:
        page_fixes = [f for f in fixes if page_of.get(f.element_id) == page]
        per_page.append(
            PageStats(
                page=page,
                elements=sum(1 for e in elements if e.page == page),
                fixes=len(page_fixes),
                cost_usd=round(sum(f.cost_usd for f in page_fixes), 8),
                latency_ms=page_ms[page],
            )
        )
    return DocumentStats(
        pages=len(pages),
        cost_usd=round(sum(f.cost_usd for f in fixes), 8),
        latency_ms=wall_ms,
        model_calls=sum(1 for f in fixes for c in f.calls if not c.shared),
        by_kind=dict(Counter(f.kind.value for f in fixes)),
        by_status=dict(Counter(f.status.value for f in fixes)),
        by_review=dict(Counter(f.review for f in fixes)),
        required_decisions=triage.required_decisions(fixes),
        per_page=per_page,
    )


def _prompt_versions(fixes: list[Fix]) -> dict[str, str]:
    """Declared step prompt versions, overridden by the versions actually sent."""
    versions: dict[str, str] = {}
    for step, module in (
        ("equation", equation_step),
        ("figure", figure_step),
        ("table", table_step),
    ):
        declared = getattr(module, "PROMPT_VERSION", None)
        if isinstance(declared, str) and declared:
            versions[step] = declared
    used: defaultdict[str, set[str]] = defaultdict(set)
    for fix in fixes:
        for call in fix.calls:
            used[call.step].add(call.prompt_version)
    for step, seen in used.items():
        versions[step] = ",".join(sorted(seen))
    return versions


# --------------------------------------------------------------------------- kept pages


def _page_boxes(
    pdf_bytes: bytes, pages: Iterable[int], sizes: dict[int, tuple[float, float]]
) -> dict[int, BBox]:
    """Each page's box in layout's frame, from the sizes layout measured; a page it did
    not measure (a layout stand-in in tests) is read from the PDF. PyMuPDF is not
    thread-safe: hold layout's lock (when it has one) like every other PyMuPDF call."""
    pages = list(pages)
    boxes: dict[int, BBox] = {p: (0.0, 0.0, *sizes[p]) for p in pages if p in sizes}
    missing = [p for p in pages if p not in boxes]
    if not missing:
        return boxes
    import pymupdf

    lock = getattr(layout, "_LOCK", None)
    with lock if lock is not None else contextlib.nullcontext():
        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                for number in missing:
                    rect = doc[number].rect
                    boxes[number] = (rect.x0, rect.y0, rect.x1, rect.y1)
            finally:
                doc.close()
        except Exception as exc:  # a size is never worth failing the conversion for
            log.warning("page size unreadable: %s", type(exc).__name__)
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store can quote the PDF
    return {p: boxes.get(p, LETTER_BOX) for p in pages}


def _next_id(page: int, elements: list[Element]) -> str:
    """A free "p{page+1}-e{index}" id on this page (one past the highest index used)."""
    prefix = f"p{page + 1}-e"
    used = [
        int(e.id.removeprefix(prefix))
        for e in elements
        if e.id.startswith(prefix) and e.id.removeprefix(prefix).isdecimal()
    ]
    return f"{prefix}{max(used) + 1 if used else 0}"


def _with_page_images(
    elements: list[Element], kept: dict[int, str], boxes: dict[int, BBox]
) -> tuple[list[Element], list[Fix]]:
    """`elements` plus one figure per kept page (placed after the page's other elements,
    orders renumbered), and the FAILED fixes that keep them, and any handwriting region
    layout cut out of a typed page, from the model. Fixes with the same reason share a
    group key: one decision for all of them."""
    added: list[Element] = []
    result: list[Element] = []
    pending = sorted(kept)
    for element in elements:
        while pending and pending[0] < element.page:
            added.append(_page_image(pending.pop(0), kept, boxes, elements))
            result.append(added[-1])
        result.append(element)
    for page in pending:
        added.append(_page_image(page, kept, boxes, elements))
        result.append(added[-1])
    renumbered = [e.model_copy(update={"order": i}) for i, e in enumerate(result)]
    fixes = [_failed(e, e.reasons[0]) for e in added]
    fixes += [
        _failed(e, page_type.HANDWRITING_ON_PAGE)
        for e in renumbered
        if e.kind is Kind.FIGURE and page_type.HANDWRITING_REGION in e.reasons
    ]
    order = {e.id: e.order for e in renumbered}
    return renumbered, _grouped(sorted(fixes, key=lambda f: order.get(f.element_id, 0)))


def _page_image(
    page: int, kept: dict[int, str], boxes: dict[int, BBox], elements: list[Element]
) -> Element:
    return Element(
        id=_next_id(page, elements),
        kind=Kind.FIGURE,
        page=page,
        bbox=boxes[page],
        order=0,  # renumbered by the caller
        reasons=[kept[page]],
    )


def _grouped(fixes: list[Fix]) -> list[Fix]:
    """Kept images with the same reason as one decision: "pg-" + the first one's element
    id, and how many there are (one page alone is a group of one, so the review screen
    shows every kept page the same way)."""
    by_reason: defaultdict[str, list[Fix]] = defaultdict(list)
    for fix in fixes:
        by_reason[fix.reason or ""].append(fix)
    for members in by_reason.values():
        key = PAGE_GROUP_PREFIX + members[0].element_id
        for fix in members:
            fix.group_key, fix.group_size = key, len(members)
    return fixes


def _source_facts(
    pdf_bytes: bytes, pages: list[int], elements: list[Element], fixes: list[Fix]
) -> SourceFacts | None:
    """`source_facts.measure`, which never raises; None if it did anyway (a bug there
    must not cost the user a finished conversion)."""
    try:
        return pdf_facts.measure(pdf_bytes, pages=pages, elements=elements, fixes=fixes)
    except Exception as exc:
        log.warning("source facts failed: %s", type(exc).__name__)
        return None


def _page_labels(pdf_bytes: bytes, pages: list[int], unloaded: list[int]) -> dict[int, str]:
    """`page_labels.read`, which never raises; none if it did anyway (page markers are a
    convenience: a bug there must not cost the user a finished conversion)."""
    lock = getattr(layout, "_LOCK", None)
    try:
        with lock if lock is not None else contextlib.nullcontext():
            return page_labels.read(pdf_bytes, pages, unloaded)
    except Exception as exc:
        log.warning("page labels failed: %s", type(exc).__name__)
        return {}


# --------------------------------------------------------------------------- entry point


class _CachedDocument:
    """Releases `layout`'s cached copy of one PDF, once.

    WP2's `layout` keeps a few opened documents (and their bytes) so crops do not reopen
    the file, and asks the pipeline to call `release(pdf_bytes)` when a document is done
    so user content does not stay in a long-lived worker's memory. Looked up at call
    time: a `layout` without `release` (the stub, a test fake) is fine.
    """

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        release = getattr(layout, "release", None)
        if not callable(release):
            return
        try:
            release(self._pdf_bytes)
        except Exception as exc:  # a cache problem must not fail the conversion
            log.warning("layout.release failed: %s", type(exc).__name__)


def _process(
    run: _Run,
    elements: list[Element],
    page_list: list[int],
    cached: _CachedDocument,
    while_waiting: Callable[[], None] | None = None,
) -> None:
    """Crop page by page on this thread, run the steps in the pool, collect every result.

    `while_waiting` runs once every crop is made and the steps are at work: the text pass
    and the heading decisions, which no step needs, are done while the models answer."""
    by_page: defaultdict[int, list[Element]] = defaultdict(list)
    for element in elements:
        by_page[element.page].append(element)
    try:
        run.start_stages(elements)
        for page in page_list:
            run.process_page(page, by_page[page])
            run.collect(block=False)  # report finished pages while cropping the rest
        cached.release()  # every crop is made; the steps only need the PNGs
        if while_waiting is not None:
            while_waiting()
        run.collect(block=True)
    finally:
        run.pool.shutdown(wait=True, cancel_futures=True)


def convert_pdf(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None = None,
    progress: ProgressFn | None = None,
    tracer: Tracer | None = None,
    concurrency: int = 4,
    notice: str | None = None,
    source_note: str | None = None,
    options: ConversionOptions | None = None,
    stage_progress: StageProgressFn | None = None,
    step_cache: StepCache | None = None,
    call_observer: Callable[[ModelCall], None] | None = None,
    readers: Mapping[str, MathReader] | None = None,
    time_limit_s: float | None = None,
) -> ConversionOutput:
    """Convert one PDF (0-based `pages`, all by default) into a `PipelineOutput`.

    Raises `layout.LayoutError` when the PDF cannot be read (or has more than
    `settings.max_pages_per_job` pages), and `config.ConfigError` or
    `pricing.UnknownModelError` when the model client is misconfigured (no API key, an
    unpriced model): the first one stops the run, since every element would fail the
    same way. Nothing else from the steps escapes. Calls `progress(pages_done,
    pages_total)` on the calling thread each time a page's steps have all finished. An
    exception from `progress` aborts the run (queued steps are cancelled, running ones
    finish) and propagates.

    `notice` is plain text shown at the top of the HTML (the CLI uses it to mark an
    offline test run); `source_note` is plain text for its "About this conversion"
    section, e.g. an OpenStax attribution.

    `options` (feature flags, the course's notation) are in force for the whole run, for
    every step to read with `pipeline.options.current()`; None means
    `ConversionOptions.from_settings(settings)`. `stage_progress` receives
    `ProgressEvent`s on the calling thread as each stage starts and moves on (see the
    module docstring); like `progress`, an exception from it aborts the run.

    `step_cache` keeps the answers of finished model steps and gives them back to a later
    run of the same document (module docstring); without one every step is asked.
    `call_observer` is told each model request's `ModelCall` when it ends (any thread).

    `readers` are the second readers of handwriting (`models.math_readers.build_readers`) for
    `Settings.image_pages_second_reader`; without them the local reader is built when it is
    the chosen one (`image_page_reconcile.default_readers`), which is all the conversion child
    can do (it has no keys and no network). `time_limit_s` is the job's own wall-clock limit when
    the caller has one: the second readings are kept inside what it leaves.
    """
    if source_envelope.is_envelope(pdf_bytes):  # a LaTeX source job (`readablestem.source`)
        from readablestem.source.convert import convert_source

        return convert_source(
            pdf_bytes,
            settings=settings,
            client=client,
            progress=progress,
            tracer=tracer,
            concurrency=concurrency,
            notice=notice,
            source_note=source_note,
            options=options,
            stage_progress=stage_progress,
            step_cache=step_cache,
            call_observer=call_observer,
        )
    if options is None:
        options = ConversionOptions.from_settings(settings)
    with run_options.using(options):
        return _convert(
            pdf_bytes,
            settings=settings,
            client=client,
            pages=pages,
            progress=progress,
            tracer=tracer,
            concurrency=concurrency,
            notice=notice,
            source_note=source_note,
            options=options,
            stage_progress=stage_progress,
            step_cache=step_cache,
            call_observer=call_observer,
            readers=readers,
            time_limit_s=time_limit_s,
        )


def _second_readings(
    elements: list[Element],
    run: _Run,
    settings: Settings,
    readers: Mapping[str, MathReader] | None,
    *,
    share_s: float,
) -> image_page_reconcile.Summary | None:
    """Compare each handwritten equation's reading with the second reader's, when one is
    chosen and can be built. Optional: whatever goes wrong here leaves every item as it was."""
    try:
        pool = readers if readers is not None else image_page_reconcile.default_readers(settings)
        reader = image_page_reconcile.chosen(settings, pool)
        if reader is None:
            # (a reader that only the parent can build, with no way to reach it from here)
            log.warning("second_reader_not_built: %s", settings.image_pages_second_reader)
            return None
        return image_page_reconcile.reconcile(
            elements, run.fixes, run.crops, reader, settings, share_s=share_s
        )
    except Exception as exc:
        log.warning("second readings failed: %s", type(exc).__name__)
        return None


def _page_math_pass(
    pdf_bytes: bytes,
    doc: Any,
    collector: page_math_reconcile.PageMathHook,
    *,
    client: VisionClient,
    settings: Settings,
    pages: list[int] | None,
    concurrency: int,
    tracer: Tracer,
    measured: list[CallFact],
) -> tuple[Any, PageMathSummary]:
    """The page math pass (ADR 0006) and the second layout run that lays its verified plan
    over layout's rules. Fails open: a page the model could not answer, a cap that stopped
    the pass, or a second run that fails leaves the pages as the first run has them.
    `ConfigError` and `UnknownModelError` propagate like they do from every step."""
    kept = set(getattr(doc, "kept_pages", None) or {})
    offered = {
        number: (rows, partial(layout.render_page, pdf_bytes, number))
        for number, rows in collector.rows.items()
        if number not in kept
    }
    with _span(tracer, "page_math", pages=len(offered)) as span:
        result = page_math.run(client, settings, offered, concurrency=concurrency)
        plan = result.plan
        calls = result.calls
        for number, asked in plan.pages.items():
            measured.extend(step_facts.of_calls(asked.calls, number))
        applied = page_math_reconcile.PageMathHook(plan)
        answered = [n for n, p in plan.pages.items() if p.failed is None]
        if answered:
            grouping: dict[str, Any] = (
                {"display_grouping": True} if settings.layout_display_grouping else {}
            )
            if settings.layout_inline_joins:
                grouping["inline_joins"] = True
            if settings.region_tree == "on":
                grouping["region_tree"] = True
            if settings.layout_join_panels:
                grouping["join_panels"] = True
            if settings.layout_figure_art:
                grouping["figure_art"] = True
            if settings.layout_matching_under:
                grouping["matching_under"] = True
            if settings.table_search_unlimited:
                grouping["table_search_limited"] = False
            try:
                doc = layout.analyze(
                    pdf_bytes,
                    pages=pages,
                    max_pages=settings.max_pages_per_job,
                    page_math=applied,
                    **grouping,
                )
            except Exception as exc:  # the first run's pages stand
                log.warning("page math: the second layout run failed: %s", type(exc).__name__)
                applied = page_math_reconcile.PageMathHook()
        stats, hook = plan.stats, applied.stats
        summary = PageMathSummary(
            pages=len(plan.pages),
            failed=sum(p.failed is not None for p in plan.pages.values()),
            calls=len(calls),
            cost_usd=round(sum(c.cost_usd for c in calls), 8),
            proposed=stats.proposed,
            verified=stats.verified,
            relocated=stats.relocated,
            dropped=stats.dropped,
            overlapped=stats.overlapped,
            applied_pages=hook.pages_applied,
            stale_pages=hook.pages_stale,
            blocks=hook.blocks,
            inline=hook.inline,
            numbers=hook.numbers,
            labels=hook.labels,
            refused=hook.refused,
            inconsistent=hook.inconsistent,
            absorbed=hook.absorbed,
            stopped_by=result.stopped.scope if result.stopped is not None else None,
        )
        _annotate(
            span,
            cost_usd=summary.cost_usd,
            calls=summary.calls,
            verified=summary.verified,
            dropped=summary.dropped,
        )
    return doc, summary


def _page_witness_pass(
    pdf_bytes: bytes,
    doc: Any,
    collector: page_math_reconcile.PageMathHook,
    *,
    client: VisionClient,
    settings: Settings,
    concurrency: int,
    tracer: Tracer,
    measured: list[CallFact],
) -> tuple[list[Element], PageWitnessSummary]:
    """The page witness (`pipeline/page_witness.py`) over the document's maths pages and its
    reconciliation with the elements layout found (`pipeline/witness_reconcile.py`): the
    elements back, each equation with its witness reading and any formula only the witness
    found as a new element. Fails open: a page the model could not answer, a cap that stopped
    the pass, or a reconciliation that fails leaves the elements as layout has them.
    `ConfigError` and `UnknownModelError` propagate like they do from every step."""
    elements: list[Element] = list(doc.elements)
    kept = set(getattr(doc, "kept_pages", None) or {})
    equations = Counter(e.page for e in elements if e.kind is Kind.EQUATION)
    offered = {
        number: (collector.rows[number], partial(_witness_image, pdf_bytes, number))
        for number in collector.rows
        if number not in kept
        and page_witness.is_maths_page(
            collector.objects.get(number, {}), equations=equations.get(number, 0)
        )
    }
    with _span(tracer, "page_witness", pages=len(offered)) as span:
        result = page_witness.run(client, settings, offered, concurrency=concurrency)
        plan = result.plan
        calls = result.calls
        for number, asked in plan.pages.items():
            measured.extend(step_facts.of_calls(asked.calls, number))
        reconciled = witness_reconcile.ReconcileStats()
        try:
            done = witness_reconcile.reconcile(elements, plan, collector.rows, collector.objects)
            elements, reconciled = done.elements, done.stats
        except Exception as exc:  # the elements as layout has them stand
            log.warning("page witness: reconciliation failed: %s", type(exc).__name__)
        stats = plan.stats
        summary = PageWitnessSummary(
            pages=len(plan.pages),
            failed=sum(p.failed is not None for p in plan.pages.values()),
            refused=stats.refused,
            calls=len(calls),
            split_pages=sum(p.stats.windows > 1 or p.stats.splits > 0 for p in plan.pages.values()),
            unoffered_rows=stats.unoffered_rows,
            cost_usd=round(sum(c.cost_usd for c in calls), 8),
            proposed=stats.proposed,
            verified=stats.verified,
            dropped=stats.dropped + stats.overlapped,
            matched=reconciled.matched,
            extent_differs=reconciled.extent_differs,
            added=reconciled.added,
            no_glyph_evidence=reconciled.no_glyph_evidence,
            inside_text=reconciled.inside_text,
            stopped_by=result.stopped.scope if result.stopped is not None else None,
        )
        _annotate(
            span,
            cost_usd=summary.cost_usd,
            calls=summary.calls,
            verified=summary.verified,
            added=summary.added,
        )
    return elements, summary


def _witness_image(pdf_bytes: bytes, number: int) -> bytes:
    """The page's picture for the witness: as large as the vision models read it."""
    return layout.render_page(
        pdf_bytes,
        number,
        dpi=page_witness.IMAGE_DPI,
        max_side_px=page_witness.IMAGE_MAX_SIDE_PX,
    )


def _image_pages_pass(
    pdf_bytes: bytes,
    pages: list[int],
    typed: Mapping[int, list[Element]],
    boxes: dict[int, BBox],
    vector: dict[int, image_page_vector.VectorPage],
    *,
    client: VisionClient,
    settings: Settings,
    concurrency: int,
    tracer: Tracer,
    observer: Callable[[ModelCall], None] | None,
    measured: list[CallFact],
    step_cache: StepCache | None = None,
    stage_progress: StageProgressFn | None = None,
    scanned: frozenset[int] = frozenset(),
) -> tuple[dict[int, image_page.Built], ImagePagesSummary | None]:
    """Read the kept pages `pages` (`image_page.kept_reads`) from a picture of each (ADR 0007).

    Returns what was built for the pages that were read, and the summary (None when there
    was no page to read). A page not in the first result stays a kept page. Fails safe: a
    page whose answer cannot be used, a spend cap that stops the pass or an exception while
    building leaves that page as it was. `ConfigError` and `UnknownModelError` propagate like
    they do from every step."""
    if not pages:
        return {}, None
    # (An answer kept by an earlier run is given back in front of the recording client: it
    # is not a request, so it gets no span and the observer does not hear of it.)
    sending = _RecordingClient(client, tracer, observer)
    recording: Any = sending if step_cache is None else _PageAnswers(sending, step_cache, settings)
    renders = {
        number: partial(
            layout.render_page,
            pdf_bytes,
            number,
            dpi=settings.image_pages_dpi,
            max_side_px=image_page.MAX_SIDE_PX,
        )
        for number in pages
    }
    with _span(tracer, "image_pages", pages=len(pages)) as span:
        result = image_page.run(
            recording,
            settings,
            renders,
            concurrency=concurrency,
            # a counter of its own: the layout's count (the table search's pages) is done by
            # now, and the bar must not step back to this pass's first page
            on_page=(
                None
                if stage_progress is None
                else lambda done, total: _send(stage_progress, "pages", done, total)
            ),
        )
        for number, read in result.reads.items():
            read.vector = vector.get(number)  # (empty when both vector settings are off)
        if (
            settings.image_pages_omission_check
            and settings.image_pages_recover_omissions
            and result.stopped is None
        ):
            image_page.recover_omissions(recording, settings, result, concurrency=concurrency)
        if settings.image_pages_second_reading and result.stopped is None:
            image_page.second_readings(recording, settings, result, concurrency=concurrency)
        built: dict[int, image_page.Built] = {}
        for number, read in sorted(result.reads.items()):
            if read.output is None:
                continue
            try:
                page_built = image_page.build(
                    number,
                    read.output,
                    box=boxes.get(number, LETTER_BOX),
                    png=read.png,
                    check_omission=settings.image_pages_omission_check,
                    verdicts=read.verdicts,
                    accept_agreed=settings.image_pages_accept_agreed,
                    maths_required=settings.image_pages_maths_always_required,
                    page_ink=read.vector if settings.image_pages_vector_checks else None,
                    typed=typed.get(number, ()),
                )
            except Exception as exc:  # the page stays an image: a bad answer is not worth more
                log.warning("image page build failed: %s", type(exc).__name__)
                page_built = None
            if page_built is not None:
                if settings.scan_layer_check and number in scanned:
                    # a scan's layer is OCR; a tablet's is not
                    try:
                        layer = layout.page_words(pdf_bytes, number)
                        page_built.layer_missing = image_page.layer_missing(layer, read.output)
                    except Exception as exc:  # a check of the read never costs the page
                        log.warning("image page layer check failed: %s", type(exc).__name__)
                built[number] = page_built
        summary = image_page.summarise(result.reads, built, settings, result.stopped)
        for number, read in result.reads.items():
            measured.extend(step_facts.of_calls(read.calls, number))
        _annotate(
            span,
            cost_usd=summary.cost_usd,
            calls=summary.calls,
            converted=summary.converted,
            fell_back=summary.fell_back,
        )
    return built, summary


def _convert(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None,
    progress: ProgressFn | None,
    tracer: Tracer | None,
    concurrency: int,
    notice: str | None,
    source_note: str | None,
    options: ConversionOptions,
    stage_progress: StageProgressFn | None,
    step_cache: StepCache | None = None,
    call_observer: Callable[[ModelCall], None] | None = None,
    readers: Mapping[str, MathReader] | None = None,
    time_limit_s: float | None = None,
) -> ConversionOutput:
    """`convert_pdf`'s body, with its options already in force."""
    tracer = tracer or NoopTracer()
    started = time.perf_counter()
    font_codes.configure(settings.font_code_tables)
    # What this conversion measures of itself (`pipeline.step_facts`): each stage's wall
    # time, and every request to a model with its page.
    watch = step_facts.Stopwatch()
    measured: list[CallFact] = []
    # Where each page's time went: layout's steps come with its result, the crops are timed
    # here (`step_facts.PageTrace`).
    crop_trace = step_facts.PageTrace()
    page_steps: list[PageStageFact] = []
    # The steps run in shadow (`pipeline.shadow`): a step that is given this runs beside
    # the conversion and can change nothing of it.
    shadowed = shadow.Shadow()
    # Pages a layout run typed without their ink, and handwritten pages whose strokes were
    # not read, each with its cause: a count of work ran out, or the clock guard fired
    # (`page_type.InkBudget`).
    ink_late: dict[int, str] = {}
    strokes_late: dict[int, str] = {}
    # Pages the table search's CPU time for the document refused (tables.DetectorBudget).
    tables_late: set[int] = set()
    # The page-level passes ask through the job's kept answers when it has any.
    page_client: VisionClient = (
        client if step_cache is None else _PageAnswers(client, step_cache, settings)
    )
    with _span(tracer, "document", pipeline_version=PIPELINE_VERSION) as doc_span:
        cached = _CachedDocument(pdf_bytes)
        try:
            _send(stage_progress, "layout")
            watch.stage("layout")
            collector = (
                page_math_reconcile.PageMathHook()
                if options.page_math or options.page_witness
                else None
            )
            # (`page_math` only when asked for: stand-ins for `analyze` take no such argument)
            extra: dict[str, Any] = {} if collector is None else {"page_math": collector}
            if settings.layout_display_grouping:
                extra["display_grouping"] = True
            if settings.layout_inline_joins:
                extra["inline_joins"] = True
            if settings.region_tree == "on":
                extra["region_tree"] = True
            if settings.layout_join_panels:
                extra["join_panels"] = True
            if settings.layout_figure_art:
                extra["figure_art"] = True
            if settings.layout_matching_under:
                extra["matching_under"] = True
            if settings.table_search_unlimited:
                extra["table_search_limited"] = False
            if stage_progress is not None:
                # the table search is most of the layout's time: its pages are the count
                extra["on_page"] = lambda done, total: _send(stage_progress, "layout", done, total)
            doc = layout.analyze(
                pdf_bytes,
                pages=pages,
                max_pages=settings.max_pages_per_job,
                **extra,
            )
            ink_late.update(page_type.ink_stops(getattr(doc, "page_types", None) or {}))
            tables_late.update(getattr(doc, "tables_not_searched", None) or ())
            # (the second layout run of the page math pass is not in the trace)
            page_steps.extend(getattr(doc, "page_steps", None) or [])
            page_math_summary: PageMathSummary | None = None
            witness_summary: PageWitnessSummary | None = None
            witnessed: list[Element] | None = None
            if collector is not None and options.page_math:
                watch.stage("page_math")
                doc, page_math_summary = _page_math_pass(
                    pdf_bytes,
                    doc,
                    collector,
                    client=page_client,
                    settings=settings,
                    pages=pages,
                    concurrency=concurrency,
                    tracer=tracer,
                    measured=measured,
                )
                # (the pass lays the plan over a second layout run, which types the pages again)
                ink_late.update(page_type.ink_stops(getattr(doc, "page_types", None) or {}))
                tables_late.update(getattr(doc, "tables_not_searched", None) or ())
            if collector is not None and options.page_witness:
                watch.stage("page_witness")
                witnessed, witness_summary = _page_witness_pass(
                    pdf_bytes,
                    doc,
                    collector,
                    client=page_client,
                    settings=settings,
                    concurrency=concurrency,
                    tracer=tracer,
                    measured=measured,
                )
            watch.stage("layout")
            elements = sorted(
                doc.elements if witnessed is None else witnessed, key=lambda e: e.order
            )
            if not options.tables:  # a table kept as its picture has no cell to write maths in
                elements = table_math.without_cell_equations(elements)
            kept: dict[int, str] = dict(getattr(doc, "kept_pages", None) or {})
            sizes = dict(zip(doc.pages, getattr(doc, "page_sizes", None) or [], strict=False))
            boxes = _page_boxes(pdf_bytes, kept, sizes)
            image_built: dict[int, image_page.Built] = {}
            image_summary: ImagePagesSummary | None = None
            vector_pages: dict[int, image_page_vector.VectorPage] = {}
            if options.image_pages:
                watch.stage("image_pages")
                # Every kept page whose content is a picture or ink (`page_type.image_kind`:
                # a scan, handwriting, a picture under a text layer that does not cover it, an
                # unread page of vector ink), asked to be read.
                page_types = getattr(doc, "page_types", None) or {}
                to_read = image_page.kept_reads(kept, page_types)
                # What layout read from them, a title over notes: the read keeps what it
                # leaves out (`image_page.build`).
                typed: dict[int, list[Element]] = {}
                for e in elements:
                    if e.page in to_read:
                        typed.setdefault(e.page, []).append(e)
                if settings.image_pages_vector_crops or settings.image_pages_vector_checks:
                    page_kinds = {n: r.kind for n, r in page_types.items()}
                    vector_pages = image_page_vector.analyse(
                        pdf_bytes, to_read, page_kinds, stopped=strokes_late
                    )
                image_built, image_summary = _image_pages_pass(
                    pdf_bytes,
                    to_read,
                    typed,
                    boxes,
                    vector_pages,
                    client=client,
                    settings=settings,
                    concurrency=concurrency,
                    tracer=tracer,
                    observer=call_observer,
                    step_cache=step_cache,
                    measured=measured,
                    stage_progress=stage_progress,
                    scanned=frozenset(n for n in to_read if kept.get(n) == SCANNED_PAGE_REASON),
                )
                watch.stage("layout")
                for number in image_built:
                    kept.pop(number)  # read: no longer an image kept whole
                # What layout read from a page the pass read (the watermark, header or page
                # number over a photographed page) is the pass's to read again: the page
                # would hold that text twice.
                deck_pages = getattr(doc, "deck_pages", None) or {}
                elements = sorted(
                    [
                        *(e for e in elements if e.page not in image_built),
                        *(
                            _in_deck(e, deck_pages)
                            for b in image_built.values()
                            for e in b.elements
                        ),
                    ],
                    key=lambda e: (e.page, e.order),
                )
            page_list = list(dict.fromkeys([*doc.pages, *(e.page for e in elements)]))
            # A page the probe could not draw is never opened in this process again: nothing
            # reads its tags, links or images (it has only its note).
            readable = [p for p in page_list if kept.get(p) != page_type.PAGE_NOT_RENDERED]
            elements, scan_fixes = _with_page_images(elements, kept, boxes)
            # Tagged-PDF structure (headings, lists, tables the author marked up) is applied
            # before the steps start: they read the elements it settles. The text pass and
            # the heading decisions run later, beside the steps.
            elements = tagged.apply(elements, tagged.read(pdf_bytes, readable))
            read = len(page_list)
            _send(stage_progress, "layout", read, read, _kind_counts(elements))
            fixes = {f.element_id: f for f in scan_fixes}
            for page_built in image_built.values():
                fixes.update(page_built.fixes)
            shared_answers = _SharedAnswers(client, settings)
            run = _Run(
                pdf_bytes=pdf_bytes,
                settings=settings,
                options=options,
                stage_progress=stage_progress,
                client=shared_answers,
                tracer=tracer,
                pages=_Pages(page_list, progress),
                contexts=context_texts(elements),
                pool=ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="step"),
                stop=_Stop(),
                fixes=fixes,
                crops={},
                page_ms=Counter(),
                futures={},
                cache=step_cache,
                observer=call_observer,
                vector=vector_pages if settings.image_pages_vector_crops else {},
                trace=crop_trace,
            )
            # The text pass adds links, colour runs and passage languages to the elements
            # without touching their text or anything a step reads, and the heading
            # decisions need no model: both are done once the steps are at work, not
            # before the first one starts (they were 2-3% of a job's wall time).
            made: list[text_pass.TextPassResult] = []

            def text_and_headings() -> None:
                made.append(
                    _text_pass(
                        pdf_bytes,
                        elements,
                        readable,
                        doc.lang,
                        options,
                        tracer,
                        settings.layout_figure_art,
                    )
                )
                for fix in _heading_fixes(made[0].elements, tracer, settings):
                    run.fixes.setdefault(fix.element_id, fix)  # a kept page's fix wins

            watch.stage("steps")
            _process(run, elements, page_list, cached, text_and_headings)
            text = made[0]
            elements = text.elements
        finally:
            cached.release()  # also when analysis, a step or `progress` failed

        if settings.image_pages_vector_checks and vector_pages and image_built:
            image_page_vector.add_checks(elements, run.fixes, vector_pages)
        reading_cost = 0.0
        reading_notices: list[Notice] = []
        stops: list[NotFinished] = []
        if image_built and settings.image_pages_second_reader != "off":
            watch.stage("second_readings")
            with _span(tracer, "second_readings") as reading_span:
                share = image_page_reconcile.share_s(
                    settings, time_limit_s, time.perf_counter() - started
                )
                reconciled = _second_readings(elements, run, settings, readers, share_s=share)
                if reconciled is not None:
                    reading_cost = reconciled.cost_usd
                    measured.extend(
                        step_facts.of_reader_call(call, page) for page, call in reconciled.calls
                    )
                    reading_notices = reconciled.notices()
                    asked = reconciled.considered
                    stops += step_facts.stopped(
                        step_facts.SECOND_READER,
                        "time_limit",
                        done=asked - reconciled.out_of_time,
                        of=asked,
                    ) + step_facts.stopped(
                        step_facts.SECOND_READER,
                        "cap_reached",
                        done=asked - reconciled.over_budget,
                        of=asked,
                    )
                    if image_summary is not None:
                        image_summary.second_reader = reconciled.record()
                    _annotate(
                        reading_span,
                        considered=reconciled.considered,
                        agreed=reconciled.agreed,
                        differed=reconciled.differed,
                        cost_usd=reconciled.cost_usd,
                    )
        watch.stage("assemble")
        if settings.image_pages_maths_always_required and image_built:
            image_page.enforce_maths_required(elements, run.fixes)
        elements, run.fixes = table_math.settle(elements, run.fixes)
        # No empty alt text on content (WS6.4): decoration only with evidence, after every step.
        decoration.settle(elements, run.fixes, run.crops, kept=assemble.KEPT_IMAGE_WORDS)
        # A paragraph or list item layout doubts (`option_labels`, `rtl_text`) gets its decision.
        for fix in notice_rules.text_decisions(elements, run.fixes):
            run.fixes[fix.element_id] = fix
        triage.stamp(run.fixes.values())  # the risk band each decision was asked at
        ordered = _ordered_fixes(elements, run.fixes)
        shared_answers.settle(ordered)
        page_of = {e.id: e.page for e in elements}
        for fix in ordered:
            measured.extend(step_facts.of_fix(fix, page_of.get(fix.element_id)))
        labels = _page_labels(pdf_bytes, page_list, [p for p in page_list if p not in readable])
        footnotes = [n.element_id for n in doc.notices if n.code == "footnote" and n.element_id]
        source_facts = _source_facts(pdf_bytes, readable, elements, ordered)
        # A PDF repaired when it was opened whose last pages are empty looks cut off: the
        # student note says so, and so does the job page until the owner answers.
        repaired = pdf_facts.repair_notices(pdf_bytes, getattr(doc, "page_types", None) or {})
        cut_off = notice_rules.cut_off_pages(repaired)
        with _span(tracer, "assemble", elements=len(elements), fixes=len(ordered)):
            _send(stage_progress, "assemble")
            html = assemble.render_html(
                title=doc.title,
                lang=text.lang,
                elements=elements,
                fixes=ordered,
                crops=run.crops,
                source_note=source_note,
                notice=notice,
                math_alttext=settings.mathml_alttext,
                page_labels=labels,
                footnote_ids=footnotes,
                cut_off_pages=cut_off,
                pipeline_version=PIPELINE_VERSION,
            )
            _send(stage_progress, "checks")
            watch.stage("checks")
            checks = html_checks.static_checks(html)
        if image_summary is not None:
            stops += step_facts.pages_capped(
                "image_page",
                [r.page for r in image_summary.per_page if r.outcome == "budget_exceeded"],
            )
        stops += step_facts.ink_stopped(step_facts.PAGE_TYPE, ink_late)
        stops += step_facts.ink_stopped(step_facts.INK_STROKES, strokes_late)
        stops += step_facts.pages_capped(step_facts.TABLE_SEARCH, sorted(tables_late), "time_limit")
        # The say-once measure, in shadow (SW60): a page whose words an element does not say
        # is noted, with its share, in the conversion's own record and nowhere a person sees.
        # A page kept whole as a picture, or read from one, has its own decision.
        shadowed.over_line(
            say_once.NOTICE_CODE,
            lambda: say_once.over_line(
                say_once.measure(
                    getattr(doc, "sources", None) or {}, elements, skip={*kept, *image_built}
                )
            ),
        )
        facts = step_facts.gathered(
            measured,
            watch,
            stops + step_facts.capped(ordered),
            shadowed.facts(),
            sorted([*page_steps, *crop_trace.facts()], key=lambda fact: fact.page),
        )
        wall_ms = round((time.perf_counter() - started) * 1000)
        stats = _stats(page_list, elements, ordered, run.page_ms, wall_ms)
        if page_math_summary is not None:
            stats = stats.model_copy(
                update={
                    "cost_usd": round(stats.cost_usd + page_math_summary.cost_usd, 8),
                    "model_calls": stats.model_calls + page_math_summary.calls,
                }
            )
        if witness_summary is not None:
            stats = stats.model_copy(
                update={
                    "cost_usd": round(stats.cost_usd + witness_summary.cost_usd, 8),
                    "model_calls": stats.model_calls + witness_summary.calls,
                }
            )
        if reading_cost:
            stats = stats.model_copy(update={"cost_usd": round(stats.cost_usd + reading_cost, 8)})
        if image_summary is not None:
            costs = {r.page: r.cost_usd for r in image_summary.per_page}
            stats = stats.model_copy(
                update={
                    "cost_usd": round(stats.cost_usd + image_summary.cost_usd, 8),
                    "model_calls": stats.model_calls + image_summary.calls,
                    "per_page": [
                        p.model_copy(update={"cost_usd": round(p.cost_usd + costs[p.page], 8)})
                        if p.page in costs
                        else p
                        for p in stats.per_page
                    ],
                }
            )
            image_page.final_counts(image_summary, elements, ordered)
        _annotate(
            doc_span,
            pages=stats.pages,
            fixes=len(ordered),
            model_calls=stats.model_calls,
            cost_usd=stats.cost_usd,
            latency_ms=stats.latency_ms,
            reason=run.stop.reason() if run.stop.scope is not None else None,
            image_only_pages=len(kept),
            checks_failed=sum(1 for c in checks if not c.passed),
            **_status_counts("fixes", ordered),
        )
    log.info(
        "convert_pdf done: pages=%d fixes=%d calls=%d cost_usd=%.4f latency_ms=%d",
        stats.pages,
        len(ordered),
        stats.model_calls,
        stats.cost_usd,
        stats.latency_ms,
    )
    # Content-free additions of the content features:
    # each feature adds its notices here (the text pass's first); those that follow from
    # the fixes are derived once, then everything is merged (one notice per code and
    # element, or code and page) and ordered by page and reading order.
    notices: list[Notice] = [
        *text.notices,
        *doc.notices,
        *repaired,
        *image_page.notices(image_built),
        *reading_notices,
    ]
    derived = [
        *notice_rules.from_fixes(elements, ordered, long_descriptions=options.long_descriptions),
        *notice_rules.from_reasons(elements, ordered),
    ]
    notices = notice_rules.ordered(notice_rules.merge(derived, notices), elements)
    result = DocumentResult(
        pipeline_version=PIPELINE_VERSION,
        prompt_versions=_prompt_versions(ordered),
        title=doc.title,
        lang=text.lang,
        elements=elements,
        fixes=ordered,
        stats=stats,
        html=html,
        stopped_by=run.stop.reason() if run.stop.scope is not None else None,
        image_only_pages=sorted(kept),
        html_checks=checks,
        page_sizes=dict(zip(doc.pages, doc.page_sizes, strict=False)),
        page_labels=labels,
        notices=notices,
        source_facts=source_facts,
        lang_source=text.lang_source,
        page_types=[
            kind.to_record(page)
            for page, kind in sorted((getattr(doc, "page_types", None) or {}).items())
        ],
        page_math=page_math_summary,
        page_witness=witness_summary,
        image_pages=image_summary,
    )
    if page_math_summary is not None:
        result.prompt_versions["page_math"] = page_math.PROMPT_VERSION
    if witness_summary is not None:
        result.prompt_versions["page_witness"] = page_witness.PROMPT_VERSION
    if image_summary is not None:
        result.prompt_versions["image_page"] = image_page.PROMPT_VERSION
        if settings.image_pages_second_reading:
            result.prompt_versions["image_page_crops"] = image_page_check.PROMPT_VERSION
    return PipelineOutput(result=result, crops=run.crops, checks=checks, facts=facts)
