# ADR 0007: Converting scanned and handwritten pages from an image of the page

| Field | Value |
|---|---|
| Status | **Accepted 2026-10-21** as an opt-in per upload with the main model, default off (the offer is `FEATURE_IMAGE_PAGES`); measured on the development sets and once on the real-world held-out papers ([eval-image-pages.md](../eval-image-pages.md)) |
| Date | 2026-10-21 |
| Decider | Harish |
| Revisits | the 2026-10-09 decision in `pipeline/page_type.py` and docs/layout.md ("There is no handwriting or scan conversion": such pages are kept as page images, marked "Not converted") |

## Context

The 2026-10-09 decision kept every page that `page_type` calls scanned or handwritten as its
image, marked "Not converted", with one required decision for the group. The reason was sound:
a scan has no text layer, so nothing can cross-check a model's reading (the hidden OCR layers
that came with real scans were exactly the garbage the page types were built to ignore), and
a page converted without a check would read as "finished" with nothing to decide. The cost is
that an instructor who uploads scanned lecture notes, a scanned problem set or an iPad notebook
gets back a document a screen reader cannot read at all, and the app's own advice is to find a
text version somewhere else.

Two things have changed since. A whole-page vision call is cheap enough to try (a page is
about 5,000 input tokens), and the repository now has the machinery that makes a read of
something with no text layer reviewable: the equation step already reads an equation that is a
picture and, because nothing can confirm it, makes it a required decision
(`equation_step.NO_TEXT_LAYER_CODE`); the page math pass (ADR 0006) already compares a page
reader's LaTeX with the crop reader's and turns a disagreement into a required decision; table
grids have structural checks; headings have a review tier. What was missing is a reader for the
whole page and a rule for how much to trust it. This ADR adds both, and measures whether the
result is good enough to switch on. It does not assume it is.

## Decision

For the pages `page_type` classifies as scanned or handwritten (reasons `scanned_page` and
`handwritten_page`, nothing else: not `unread_page`, not handwriting on a typed page), and
only when the conversion asked for it (in the web app the upload option "Convert scanned or
handwritten pages", offered by `FEATURE_IMAGE_PAGES`, ticked by the document's owner; for the CLI
and the eval the flag), read the page with **one vision call**, validate what
comes back, and put it into the document as ordinary elements with review flags that say it has
no text layer behind it. Without the request, or when anything below fails, the page is kept as
its image exactly as before.

1. **One call per page** (`pipeline/image_page.py`, prompt `models/prompts/image_page.md`).
   Input: the page rendered at 200 dpi (long side at most 2,576 px, the size at which Claude
   stops scaling the picture down, so a footnote's subscript is still legible). No text
   context: there is none to give. Model: `IMAGE_PAGES_MODEL`, default the main model;
   effort `ANTHROPIC_EFFORT_IMAGE_PAGES`. Which model, and at which effort, is a measurement
   (below), not a decision of this ADR.
   Output, strict structured: the page's blocks in reading order, each with a kind
   (`heading` with a level, `paragraph`, `list_item` with its marker and level, display
   `equation` with LaTeX and its printed number, `figure` with a short description, `table`
   as a grid of cells with header flags and spans), a bounding box on a 0 to 1000 scale, and a
   confidence (`high`, `medium`, `low`). Prose is a list of runs, text or inline-maths
   (LaTeX), so an inline formula is a span and not a guess made later. A word the model
   cannot read is written as the literal marker `[illegible]` in its place; the prompt forbids
   guessing, completing, solving and correcting, and omits running headers, footers and page
   numbers (layout drops those on a typed page too).
2. **Nothing is trusted as it comes** (`image_page.build`). Every LaTeX, display or inline, goes
   through the existing validators (`check_latex`, `safe_latex_to_mathml`, `check_mathml`) and
   the equation step's cleaning. A table's grid goes through the table step's own constructor
   and structure checks (`grid_problems`, header sanity, maths cells); a grid that is not a
   grid is kept as the table's image. A bounding box is checked, clamped to the page, padded
   a little and trimmed to the ink it holds (so a loose box cannot take in the line above); a
   box that is unusable removes the second reading for that block and nothing else. Headings
   enter the existing heading pipeline with the model's level as their evidence, so the
   outline repair and the grouping of repeated labels apply. Text is escaped, as ever; the page
   image is data and the prompt says so (a scan can carry the words "ignore the above").
3. **Trust: there is no text layer, so every converted block is a review item.**

   | Block | Tier |
   |---|---|
   | display equation | **required**, always (`equation.no_text_layer`); the second reading below raises its confidence or sharpens the decision, it does not remove it |
   | table | **required**, always |
   | paragraph, list item, heading, figure description at confidence `high` and no `[illegible]` | optional note |
   | any of those at `medium` or `low` confidence, or with `[illegible]` in it, or with an inline formula that failed validation | **required** |
   | page image missing, a block whose grid or LaTeX could not be used | that block is kept as its image, required (as today) |

   Inline formulas are part of their paragraph's item, not decisions of their own: a
   paragraph with six variables would otherwise be six decisions. The measurement reports how
   many wrong inline formulas that leaves nobody asked about; if it is too many, inline
   maths on these pages becomes required too (it is one constant).
4. **The page keeps its original** (`assemble`). Each converted page starts with a notice
   (**"Converted from an image of the page. Check it against the original."**, a visible text
   label, not colour) and the original page image in a `<details>` the reader opens ("Show the
   original page N"), with alt text that says what it is. No script. The notice is also a
   content-free job notice (`image_page_converted`), and the about section for students says
   which pages were converted from images. The wording follows architecture rule 7: it
   assists, and it never says that the conversion has been checked or is right.
5. **A second reading for display equations** (`equation_step`, unchanged). The block's
   bounding box is cut from the page and the existing equation step reads the crop; its LaTeX
   is compared with the page reader's as symbol streams (`page_math.latex_stream`, the same
   comparison ADR 0006 built). Agreement raises the confidence of a usable reading, a
   disagreement asks the main model once, and a reading nobody shares is a required decision
   that names the reason. Two readings by two different calls on two different pictures of the
   same ink are independent enough to be worth a lot, and not independent of the page image's
   legibility: where the ink is bad both can be wrong the same way, which is why agreement does
   not lower the tier.
6. **Fallback.** A refusal, an invalid or cut-off answer, an answer with no usable block, a
   budget refusal (`BudgetExceeded`, a job or page cap) or an exception leaves the page in the
   `kept_pages` path it would have been in: the page image, "Not converted", one grouped
   decision. A page the model read as blank is also kept. Calls that were paid for stay on
   the result: their cost is in `DocumentStats` and in the page's own record.
7. **Cost accounting.** An image page counts against the page quota like any page (the quota
   counts the upload's pages; this ADR does not change that), and its model cost is reported
   per page (`DocumentResult.image_pages.per_page`: model, calls, dollars, blocks, review load,
   outcome), is in `DocumentStats.cost_usd` and counts against the job's dollar and call caps
   like every call. The upload probe, which refuses a PDF whose every page is a certain scan
   or handwriting (`page_type.document_refusal`), lets such a PDF through when the owner ticked
   the upload option, and counts its scanned and handwritten pages (a file with none is not affected).

8. **Omission check** (added 2026-10-21, `pipeline/image_page_omission.py`; numbers in
   [eval-handwriting.md](../eval-handwriting.md), "The omission check", and
   [eval-image-pages.md](../eval-image-pages.md), "The omission check"). One call returns one answer,
   and an answer that stops early says nothing about what it left out: the page counts as converted
   and the only items are the blocks that came back. The page picture has ink and the blocks have
   boxes, so the picture is compared with the regions the boxes account for. Ink outside every
   box, grown by 3% of the width and 6% of the height (the reader's boxes sit off handwriting by
   up to a line), in a stretch above a size (2% of the page's ink, 5 grid rows high), is a figure
   element with a required decision that says the reading left something out
   (`image_page.omitted_region`; review sentence in `web/messages.py`; written as a picture marked
   "Not converted" until the reviewer describes it or keeps it). Rules, frames, a scanner's bar and
   rim, grain, and the strips where running headers, footers and page numbers sit are not content.
   No model call and no prompt change (the prompt hash is unchanged, so nothing was re-recorded);
   `IMAGE_PAGES_OMISSION_CHECK=false` turns it off. It cannot see a reading that is wrong, a
   dropped line next to a block (inside the margin of error of the boxes), content whose ink
   the boxes cover, and a page whose blocks have no usable box (it is not judged).

9. **A line that holds only a formula is a display equation** (added 2026-10-22,
   `image_page.lone_formula`; [eval-handwriting.md](../eval-handwriting.md), "Root causes and fixes").
   On handwriting the reader set 35 of 56 display equations as list items or paragraphs with one
   formula run in them ("3." and an equation), so they were read and set as inline maths. A
   paragraph or list item with one valid formula and no words is now an equation block, the
   marker its number, its box without the marker (and the prompt says so). Display equations keep
   their tier (required, with a read of the crop and a comparison with the page reading). It adds no
   required decision: the paragraph was `medium` and required already.
10. **Recovery of left-out writing** (2026-10-22, `image_page.recover_omissions`,
    `IMAGE_PAGES_RECOVER_OMISSIONS`, on). A stretch of ink the omission check finds (point 8) is cut
    out of the page picture and read with the same prompt; the blocks that come back join the page in
    reading order, so the stretch is converted (with its own confidence and decision) and no longer
    flagged. A stretch that gives nothing stays flagged. A request only for a page the check flags.
11. **Second reading of lines with maths, off by default** (2026-10-22,
    `pipeline/image_page_check.py`, `IMAGE_PAGES_SECOND_READING`, `IMAGE_PAGES_ACCEPT_AGREED`). Each
    `medium` paragraph or list item that holds a formula is cut out and read alone, several lines to a
    request (`image_page_crops` prompt); the verdict (formulas the same by their symbols, words the
    same by their letters, the second reading's own confidence) is a check on the block's fix. With
    acceptance on, a block whose readings agree, the second sure of every symbol, is an optional note
    instead of a required decision. Measured: a difference is informative (about 20% of such blocks
    right, against 50%), agreement is not (one model reads one ink the same wrong way), so acceptance
    removes about one decision in 140 and no silent wrong equation, and is off. This does not change the
    consequence in point 5: agreement of two readings by one model lowers no tier.

12. **Maths always required** (decided by the owner 2026-10-22,
    `Settings.image_pages_maths_always_required` / `IMAGE_PAGES_MATHS_ALWAYS_REQUIRED`, on). On a page read
    from an image, every block that holds maths is a required decision whatever the page reader's
    confidence: a display equation (always, point 3), and a paragraph, list item or heading with any
    formula in it. The option's promise is "you check every item", and the reader's `high` is not
    trustworthy on handwriting (two silent wrong equations of the fresh held-out set were `high` blocks;
    [eval-handwriting.md](../eval-handwriting.md)). The reason a person sees is
    `image_page.maths_always_checked` ("This was read from an image and holds maths, and every such block
    is checked, however sure the reader was ..."). Prose-only blocks keep the tiers of point 3; typed
    pages, which this pass never reads, and the typed equation step are untouched; a second reading's
    agreement (point 11) cannot lift such a block out. Measured by replay at $0: silent wrong equations 0
    by construction and verified on every set (the typed scans had 15 and the scanned papers 46 by the
    symbols score); required decisions a page 5.79 to 5.96 on the fresh handwriting set, 3.58 to 6.44 on
    the typed development scans and 2.00 to 5.69 on the scanned papers
    ([eval-image-pages.md](../eval-image-pages.md), "Update: maths always required"). It settles the open
    question of the "Consequences" about inline formulas riding with their paragraph.

## Measuring it

Development material only for the design and the choice of model; the sealed check part of
dev-v2 is not opened. The real-world held-out papers were used once afterwards, as scans, for the
decision to offer the option (see "Result"), registered before that run.

- **Synthetic scans.** The dev-v2 and `data/gold` pages that have an answer key are printed as
  they are for the existing document task, rendered at 300 dpi, degraded the way scanners
  degrade (resampled to 150 to 200 dpi, rotated by up to 1.5 degrees, grain, a blur, a JPEG
  pass), and rebuilt as image-only PDFs (`eval/scan.py`, deterministic from a seed so recorded
  answers replay). The existing gold and scorers apply unchanged: equations (primary
  `exact_lenient` and the symbols score), headings, review load, cost; plus a prose word error
  rate against the page's own source text with maths removed (`eval/prose_wer.py`).
- **Real pages.** A small set of openly licensed scans and handwritten notes, run for a
  qualitative look (and gold by hand for a few pages where it is feasible).
- **Models.** Claude Opus, Sonnet and Haiku (5.5) as the page reader, same pages, same
  everything else; effort on one slice. Every answer is recorded once and replayed (`--answers`);
  a total live spend cap of $15; a spend log.

Report, per model: equations (primary and symbols), headings F1, prose WER, required decisions
and optional notes per page, flag precision and recall for equations, wrong inline formulas not
asked about, cost per page and seconds per page, and how often the fallback fired.

## Result on the development material (2026-10-21)

Full numbers, caveats and screenshots in [docs/eval-image-pages.md](../eval-image-pages.md). On 36
pages of development documents made into synthetic scans (253 answer-key equations), page reader
`claude-opus-5-5`: equations right end to end 0.593 on the primary score and 0.834 on the symbols
score, the same as the same pages as typed PDFs (0.593 and 0.830); heading F1 0.961 (typed 0.967),
the relative level right for 66 % of matched headings (typed 100 %); prose word error rate 3 to 5 %; $0.095 a
page for the read and $0.106 all in (typed: $0.022); 3.5 required decisions per page (typed 1.4,
kept as images 0.17). `claude-haiku-5-5` reads for $0.003 a page and ends 12 points lower on the
symbols score; `claude-sonnet-5-5` is no better than Haiku at 17 times the price. Eight real pages (four
handwritten, four scanned prints, public domain or CC BY-SA, no answer key): all read but one
dense numeric table that exceeded the output limit and stayed an image; Opus reads a student's
handwritten algebra nearly line for line, Haiku misread two section numbers in it with `high`
confidence. Live spend $9.23 of the $15 cap.

Changes the measurement made to the design, all of them before the final runs: the table cell's
spans carry no lower bound in the schema (the provider's decoder does not enforce one, and one cell
with a span of 0 cost Haiku a whole page); a display equation whose crop shows no equation (a
misplaced box, 26 of Haiku's 116) keeps the page reader's reading as a required proposal instead
of an unexplained "not an equation"; the page read has its own timeout (180 s) and resolution
setting. Effort `low` and 200 dpi stay the defaults (neither `medium` nor 150 dpi was measurably
better on these pages).

**Decision from the development numbers: the flag stays off by default**, and offering it as an
opt-in with the main model is supported by them; a held-out measurement on scans was to decide
whether more than that. It was made once, on the real-world held-out papers, registered before
the run (`data/image-pages/PREREGISTRATION-HELDOUT-SCANS.md`): partial (9 of 15 papers, the $9
cap), 0.664 of the equations exactly right against 0.619 for the typed pipeline on the same
papers (symbols 0.693 against 0.664), $0.136 a page, 1.7 decisions a page. Not worse than typed,
at four times the cost of a typed page: enough for an opt-in, not a reason for a default.

## Consequences

**Decided 2026-10-21 (Harish; the coordinator carried the decision): ship scanned and handwritten
pages as an opt-in per upload, read by the main model, default off.**

- **What ships.** An upload checkbox, "Convert scanned or handwritten pages (slower; every converted
  equation and table needs your check)", offered where the server sets `FEATURE_IMAGE_PAGES` and
  used only for a PDF the probe finds scanned or handwritten pages in. The document's owner makes
  the choice (`Job.image_pages`); no document is ever converted this way unasked. The reader is
  the main model (`claude-opus-5-5`) unless `IMAGE_PAGES_MODEL` says otherwise. Pages count against the
  daily quota like any page, open-tier accounts included. A job that asked may spend
  `IMAGE_PAGES_USD_PER_PAGE` ($0.25) a page of the document, at most `IMAGE_PAGES_MAX_USD_PER_JOB`
  ($7.50), inside the daily cap and the open-tier pool ($4 a day), which still bound the cost.
- **Review stays required where nothing can confirm a reading.** Every display equation and every
  table is a required decision; prose, headings and figure descriptions are optional notes only
  when the reader was sure and nothing is `[illegible]`; each converted page starts with a notice
  and keeps its original in the file. Without a text layer to check against, a person is the only
  check, and a model that reads handwriting can misread a symbol with full confidence (measured: a
  cheaper model read two section numbers wrong and called them `high`).
- **Why opt-in and not default.** A page costs four to six times a typed page ($0.11 to $0.14
  against $0.02 to $0.03), takes 20 to 30 seconds and asks a person to check every display equation
  and table. The measurement is on synthetic scans, nine real papers and eight real pages: not
  worse than the typed pipeline on equations, and nothing on how long checking takes. Someone with
  scanned lecture notes who wants them readable, and will check them, is served; someone who
  uploads a PDF of scans by mistake is not charged for it.
- **Why the main model.** The only reader that matched the typed pipeline on equations on the
  development pages, read handwriting without a confident misread, and cut boxes good enough for
  the second reading to read what the page reader meant. The cheap model is cheaper by a factor of
  thirty and costs about twelve points of symbols-correct equations.
- **Easier.** A scanned or handwritten page can become text a screen reader reads, with every
  doubtful part in front of a person; the instructor gets one place (the page notice and its
  original) to check against. The refusal of a PDF of only scans now says how to ask.
- **Harder.** Every converted page is a model call with no text layer to catch an invented
  word: the review tiers are the only guard, and a page full of confident, wrong prose is an
  optional note a person can skip. Inline formulas ride with their paragraph, so a wrong one
  in a confident paragraph is not asked about (about 5 in 100 equations on the held-out papers,
  fewer than the typed pipeline leaves unasked, but not none). One more prompt, schema, setting
  group, upload option and migration; the public copy has to say what it can and cannot do (README,
  limits, site pages: updated with this decision).
- **Not decided here.** Whether the conversion should ever be the default; whether inline formulas
  should become required decisions; whether a display equation the two readers agree on may become
  an optional note (agreement says little when one model reads twice); a measurement of reviewers.
- **When to revisit.** If reviewers accept wrong equations from these pages, if the opt-in is used
  mostly by mistake or its cost per page rises, or if a cheaper reader closes the gap on equations
  and on handwriting, change the model or the allowance; if a held-out scan set shows more silent
  wrong words than the notes can catch, withdraw the option.
