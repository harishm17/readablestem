# ADR 0007: Converting scanned and handwritten pages from an image of the page

| Field | Value |
|---|---|
| Status | **Proposed**, behind `FEATURE_IMAGE_PAGES` (default off); measured on the development sets only ([eval-image-pages.md](../eval-image-pages.md)) |
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
only with `FEATURE_IMAGE_PAGES` on, read the page with **one vision call**, validate what
comes back, and put it into the document as ordinary elements with review flags that say it has
no text layer behind it. With the flag off, or when anything below fails, the page is kept as
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
   or handwriting (`page_type.document_refusal`), lets such a PDF through when the flag is
   on.

## Measuring it

Development material only; the held-out set is not used for anything here, and the sealed check
part of dev-v2 is not opened.

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

**Decision from the numbers: the flag stays off by default.** The evidence is development-set and
synthetic; the review it asks for (105 decisions for a 30-page scan) is the cost that was not
measured as time; Opus at about $3.2 for 30 pages exceeds the default job cap. Offering it as an
opt-in with the main model is supported by the numbers; a held-out measurement on scans decides
the default.

## Consequences

TODO(Harish): this section is for the author to write or confirm (architecture rule 8). What
the design implies, as the implementer sees it:

- **Easier.** A scanned or handwritten page can become text a screen reader reads, with every
  doubtful part in front of a person; the instructor gets one place (the page notice and its
  original) to check against.
- **Harder.** Every converted page is a model call with no text layer to catch an invented
  word: the review tiers are the only guard, and a page full of confident, wrong prose is an
  optional note a person can skip. One more prompt, one more schema, one more place a student
  can read a model's guess as a fact. Review load rises with the number of equations and
  tables on these pages, since none of them can be accepted without a decision.
- **Not decided here.** The default for `FEATURE_IMAGE_PAGES`, which model reads, and whether
  agreed inline formulas may stay optional; those wait for the numbers above and for a
  held-out measurement that includes scans.
- **When to revisit.** If a held-out scanned set shows more silent wrong words than the review
  tiers can catch, or if the hidden-layer pages (`scan_ocr`) turn out to be better read from
  their OCR layer after all, switch the flag off and keep the pages as images.
