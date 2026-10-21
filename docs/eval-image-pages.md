# Scanned and handwritten pages read from an image: development measurement (2026-10-21)

What [ADR 0007](decisions/0007-image-pages.md) built, how it was measured, what it did, what it
cost, and what that does and does not say. Development material only: `data/dev-v2` (dev part)
and eight public-domain or CC BY-SA pages found for this purpose. The held-out set and the sealed
check part were not run, opened or used for anything. Every model answer was recorded once
(`--answers`) and every table below is re-scored from those recordings.

**The short version.** On 36 pages of development documents turned into synthetic scans, a page read
from its image by `claude-opus-5-5` ends with the same equations right, end to end, as the same
pages as typed PDFs through today's pipeline (0.593 on the primary `exact_lenient`, 0.834 against
0.830 on the symbols score), reads the prose at a word error rate of 3 to 5 %, and costs $0.106 a
page all in ($0.095 of it the page read) against $0.022 for the typed pages. `claude-haiku-5-5`
reads the same pages for $0.003 and loses 12 points of symbols-correct equations and the
reliability of its own confidence on handwriting. `claude-sonnet-5-5` is not better than Haiku
here and costs seventeen times as much. The price of all of them is review: 3.5 required decisions
per page against 1.4 for typed pages and 0.17 now, because no equation or table of an image page
can be accepted without a person. **Recommendation: leave `FEATURE_IMAGE_PAGES` off by default;
if it is offered, offer it as an opt-in with the main model (`IMAGE_PAGES_MODEL` unset), after a
held-out measurement on scans.** Reasons and the open risks are at the end.

## What was built

| Part | Where |
|---|---|
| The pass: prompt, strict output schema, validation, review tiers, the rescue of an unreadable crop | `src/readablestem/pipeline/image_page.py`, `src/readablestem/models/prompts/image_page.md` |
| Integration (kept pages, crops, headings, cost, notices) | `pipeline/run.py` (`_image_pages_pass`), `pipeline/layout.py` (`REASON_IMAGE_HEADING`), `pipeline/heading_step.py` unchanged |
| Output: notice, "Show the original page", about note, rejected text as a picture | `pipeline/assemble.py` |
| Review of the text of an image page; upload of scans; job outcome | `web/review.py`, `web/routes/review.py`, `web/routes/split.py`, templates, `web/pdf_probe.py`, `web/uploads.py`, `web/worker.py` |
| Settings | `FEATURE_IMAGE_PAGES`, `IMAGE_PAGES_MODEL`, `ANTHROPIC_EFFORT_IMAGE_PAGES`, `IMAGE_PAGES_TIMEOUT_S`, `IMAGE_PAGES_DPI`; `VisionRequest.timeout_s` |
| Eval: scans, prose word error rate, image page numbers | `eval/scan.py`, `eval/scan_media.py`, `eval/prose_wer.py`, `eval/image_pages.py`; `eval pipeline --scanned [--scan-seed]` |
| Dev scripts | `scripts/image_pages_probe.py` (read pages live, or a picture), `scripts/image_pages_table.py` (reports side by side) |
| Tests | `tests/test_image_page.py`, `test_image_page_run.py`, `test_web_image_pages.py`, `test_eval_scan.py`, `test_eval_image_pages.py`, browser: `test_image_page_browser.py`, `test_image_page_axe_browser.py` |

## How it was measured

**The set.** Six development documents, 36 PDF pages, 253 answer-key equations (116 display,
the rest inline), 76 answer-key headings: OpenStax Chemistry (Atoms First) 1.4 measurements, 12.3
second and third laws of thermodynamics, 14.1 Brønsted-Lowry acids and bases, 20.2 nuclear equations
(prose, nuclides, chemical formulas, tables, figures), and two documents written for this project:
`latex-matrices` (47 display systems and matrices) and `latex-exercises` (numbered exercises,
piecewise definitions). Chosen for variety and for their size, before any run, not for how a model
did on them; prompt and builder were developed on the first pages of these sections (a Haiku
pilot of 13 pages), so none of this is a clean confirmation set.

**Synthetic scans** (`eval/scan.py`). Each printed page is rendered at 300 dpi, resampled to 150 to
200 dpi, rotated up to 1.5 degrees, shaded towards one edge, tinted, given sensor grain (sigma 4 to 9
levels), blurred (radius 0.5 to 0.9 px) and saved as a JPEG at quality 55 to 75, then set in a PDF as the
only thing on the page. Deterministic from the seed (0 here), so the requests, and therefore the
recorded answers, replay. `page_type` calls every such page `scan_no_text`. The answer key and
scorers are the existing document task's, unchanged: equations (primary `exact_lenient`, and the
secondary symbols score), headings (the headings task's scorer), review load, cost. The same
pages as typed PDFs, with the flag off, are the comparison ("typed"); the same scans with the flag
off are "before" (every page kept as an image).

**Prose word error rate** (`eval/prose_wer.py`). Words of the whole section as one sequence each,
exact word-level Levenshtein distance, over the answer key's words (the page's own printable HTML).
Two versions: *all words*, formulas included on both sides as the MathML text they hold; and
*prose only*, formulas out of both sides and only words of three letters or more, so that two ways of
writing a formula cannot differ. The attribution block and the running footer are left out of both
(the prompt tells the reader to leave running footers out; a reader that transcribes the attribution
block is not penalised). Known quirks of the key: the printable HTML lacks the title row of some
tables, and a picture inside a table cell is described by the model (the nuclear-radiation table of
20.2): a few words per section either way, the same for every model.

**Models.** Page reader: `claude-opus-5-5` (the main model), `claude-sonnet-5-5`, `claude-haiku-5-5`,
each at effort `low`, 200 dpi. Everything else is as in production: equation step with
`claude-opus-5-5` main and `claude-haiku-5-5` cheap, no second automated check (`CLAUDE_SECOND_CHECK=off`).
Each arm is one run: there is no repeat, so a difference of a few points between arms is inside the
noise of a stochastic model on 36 pages (the effort and resolution runs below show differences of
that size from changes that should not matter much).

## Results

| | before (scans, flag off) | typed PDF (flag off) | Haiku | Sonnet | **Opus** |
|---|---|---|---|---|---|
| PDF pages / answer-key equations | 36 / 253 | 36 / 253 | 36 / 253 | 36 / 253 | 36 / 253 |
| **equations right end to end, primary `exact_lenient`** | 0.000 | 0.593 | 0.522 | 0.455 | **0.593** |
| equations right end to end, symbols (credit) | 0.000 | 0.830 | 0.715 | 0.692 | **0.834** |
| detection recall / precision | 0 / n.a. | 0.897 / 0.725 | 0.854 / 0.570 | 0.763 / 0.609 | 0.897 / 0.622 |
| heading F1 / relative level right among matched | n.a. | 0.967 / 1.000 | 0.934 / 0.634 | 0.961 / 0.743 | 0.961 / 0.662 |
| prose WER, all words (formulas included) | 1.000 | n.a. | 0.055 | 0.037 | 0.046 |
| prose WER, words of 3+ letters (formulas out) | 1.000 | n.a. | 0.024 | 0.031 | 0.034 |
| pages read / kept as image | 0 / 36 | n.a. | 36 / 0 | 36 / 0 | 36 / 0 |
| required decisions per PDF page | 0.17 | 1.42 | 3.83 | 3.83 | 3.50 |
| optional notes per page (all blocks) | 0 | 1.11 (equations, headings, figures only) | 9.56 | 8.78 | 10.19 |
| **page read: $ per page** (mean; median) | | | **0.003** (0.002) | **0.050** (0.036) | **0.095** (0.072) |
| whole pipeline: $ per PDF page | 0 | 0.022 (incl. $0.0065 of second check) | 0.015 | 0.061 | 0.106 |
| page read: seconds per page | | | 12.4 | 13.9 | 20.8 |

"Required decisions" for the image arms count every block's fix (`ImagePageRecord.required`);
for "before" and "typed" they are the existing report's number (equations, figures, tables,
headings). The two are the same kind of count, not the same thing: on typed pages prose has no fix.
The typed heading figures are the headings task on the same six documents (layout and heading
step, no model). Cost per page of the typed arm includes a shadow second check that the image arms
did not run ($0.24 of the $0.77).

**What the table says.**

- **Equations.** With Opus as the page reader the pipeline ends where it does for typed pages,
  on both scores. With Haiku it ends 7 points lower on `exact_lenient` and 12 on the symbols score.
  The reasons are in the boxes and the readings (below), not in the LaTeX: where a box is right the
  LaTeX is almost always right. Sonnet's low detection recall is a style: it wrote 79 inline
  formulas where Opus wrote 114 and left the rest as text.
- **The scorer's strictness.** The primary score fails an equation whose structure differs from the
  key's (`\text{H}_2\text{O}` against a mixed run, a system as an array against a braced list): 49 of
  Opus's 113 matched display equations "wrong" by `exact_lenient` are wrong in no way a reader would
  see. The symbols score is the better reading of the table. And the key's inline equations often
  carry the bracket or comma around them (`HCO3−)`): the 15 inline equations Opus got "wrong in
  the symbols" that were looked at are all that (the model's reading is right).
- **Prose.** 2 to 3 % of words wrong in prose and 4 to 5 % counting the formulas' tokens, all three
  models, with no model clearly ahead; the differences are the key's quirks and a described picture.
  No reader hallucinated a paragraph on these pages; none wrote `[illegible]` (the pages are legible).
- **Headings.** The reader finds the headings (F1 0.93 to 0.96 against 0.97 typed) but not their
  levels: 63 to 74 % of matched headings have the right relative level against 100 % from layout's
  font sizes. A level is one option on the review card; the outline repair keeps the document valid.
- **Review load.** 3.5 to 3.8 required decisions per page: 116 display equations, 9 tables and 2 to 4
  figures, nothing else, in every arm (prose and headings were optional notes unless a model said
  `medium`: 3 to 5 blocks of 600). It is the design, not a finding: all of them are the tier "required for
  equations and tables". A 30-page scanned document comes with about 105 decisions.
- **Cost.** Opus reads a page for a mean $0.095 (median $0.072). One Opus page ($0.37) and one Sonnet page ($0.25)
  cost several times the rest: those calls were re-run by the provider's refusal fallback on another
  model at its rates (a chemistry page whose content tripped a classifier); they are in the means.
  At Opus rates a 30-page scanned document costs about $3.2 all in, which is over the default
  `MAX_USD_PER_JOB` of $3: the job would stop on its cap.

### The review flags

| | Haiku | Sonnet | Opus |
|---|---|---|---|
| matched equations (display / inline) | 216 (100 / 116) | 193 (114 / 79) | 227 (113 / 114) |
| display equations: crop did not show an equation (kept as the page reader's reading, required) | 26 | 1 | 0 |
| display equations: crop and page readings disagree | 18 | 1 | 0 |
| display equations: readings agree and the equation is wrong by `exact_lenient` | 25 of 52 | 50 of 112 | 49 of 113 |
| wrong equations nobody is asked about, per 100 answer-key equations (`exact_lenient`) | 4.3 | 1.6 | 11.1 |
| ... wrong in the symbols | 0.8 | 0.8 | 5.9 |
| flag precision / recall, `exact_lenient` (asked: required fix, an inline formula through its paragraph) | 0.51 / 0.87 | 0.51 / 0.95 | 0.43 / 0.64 |

Every display equation is asked about, so display recall is 1.00 by construction. The unasked wrong
equations are all inline formulas, which ride with their paragraph (ADR 0007, "Trust"): a paragraph
the model calls `high` is an optional note whatever formulas it holds. Opus calls almost every
block `high` (1 % of blocks `medium` or `low`; Haiku 5 %, Sonnet 7 %), so for Opus nothing about the
inline formulas reaches a person: 28 wrong by `exact_lenient`, 15 by the symbols score, and every
one of those 15 that was looked at is the key's stray bracket, not a wrong reading. So **no inline
formula the model read wrongly was found on these pages, and none would have been asked about**.
The policy cost is therefore not measured as large here, and not measured as nil: if inline
formulas became required, the load rises by the inline formulas of the page (about 3 per page on
this slice) and the flag recall of inline formulas becomes 1.00.

**The second reading is only as independent as its two readers.** With Opus on both sides the readings
agree on every equation, including the 49 the scorer calls wrong: the same model reads the same
ink the same way twice, so agreement says almost nothing. With Haiku as the page reader they disagree
on 18 equations and the crop shows no equation for 26 more (its boxes are the problem: Haiku's
boxes are visibly off the equation, Opus's are close), and those are the equations a
person is sent to with the sharpest reason. A cheap page reader with the main model's second
reading is the combination in which the second reading carries information.

### Settings (Haiku, same 36 pages)

| | default (effort low, 200 dpi) | 150 dpi | effort medium |
|---|---|---|---|
| equations right (`exact_lenient` / symbols) | 0.522 / 0.715 | 0.557 / 0.739 | 0.573 / 0.719 |
| heading F1 | 0.934 | 0.934 | 0.873 |
| prose WER (all / 3+ letter words) | 0.055 / 0.024 | 0.065 / 0.033 | 0.110 / 0.076 |
| pages read / kept as image | 36 / 0 | 36 / 0 | 35 / 1 |
| seconds per page | 12.4 | 10.6 | 17.1 |

Resolution makes no measurable difference between 150 and 200 dpi on these pages (the scans are at
150 to 200 dpi themselves); effort `medium` is not better and one page of it was not usable. `low`
and 200 dpi stay the defaults. Haiku also fell back to the page image never at the default and once
at `medium` (a cut-off answer).

## Real pages (no answer key; qualitative)

Eight pages, found for this purpose, public domain or CC BY-SA, in
`.local/image-pages/real` (not committed; sources and licences in its `SOURCES.tsv`): four handwritten
(van 't Hoff's laboratory notes, a Ramanujan notebook page, an 1904 student's algebra, Bredig's pencil
notes with a graph) and four scanned prints (Poynting's *Physics* 1914, Edwards's *Differential
Calculus* 1896, a curved-page photograph of Beeton's *Chemistry*, Hedrick's logarithm tables 1920).
Read live by Haiku ($0.03 for all) and Opus ($0.64). Results, as seen by eye on the pictures:

| | Haiku | Opus |
|---|---|---|
| pages read / kept as image | 8 / 0 | 7 / 1 (the logarithm table: the answer reached the 8,192-token output limit, so it is kept as an image, $0.18) |
| required decisions per page | 0 to 13, 8 pages: 10, 12, 8, 13, 3, 9, 0, 2 | 14, 9, 7, 9, 6, 9, 0, 1 |
| `[illegible]` markers | 10 on Bredig's notes | 15 on Bredig's notes |
| cost per page | $0.001 to $0.012 | $0.04 to $0.11 |

- **Handwriting, Opus** reads the student's algebra (picture below: `docs/images/image-pages-handwritten-algebra.jpg`)
  nearly line for line: the fractions, the radicals, the section numbers "IV." and "V." and the
  teacher's "correct". Haiku read "IV." as "II." and the second "V." as "I.", and called both
  `high`: the one wrong, confident, optional-note reading found anywhere in this work, in a heading.
  An 1890s hand with Ramanujan's ligatures (the factorial `⌊n`) is read in part: required, and
  partly wrong, as it should be.
- **Scanned print.** Poynting's page, with the black scanner bar and speckle, is read correctly
  except one equation (`E₁ sin θ₁ = E₂ sin θ` lost its subscript and full stop, in the picture
  below), which is why every equation is a decision. The curved, shadowed Beeton page, in a
  yellowed photograph, is transcribed word for word (including the printer's `(HS)`), as optional
  notes only: the case where a confident, correct page needs no decision, and the case where a
  confident wrong one would not get one.
- **Where it fails.** A dense table of numbers (60 rows of ten) does not fit in the output
  allowance; the page stays an image, as designed.

![A student's handwritten algebra, as read by Opus](images/image-pages-handwritten-algebra.jpg)

![A scanned physics page and the converted output page, Opus](images/image-pages-scanned-physics.jpg)

![A scientist's handwritten notes, as read by Opus](images/image-pages-handwritten-chemistry.jpg)

(On the left, the picture; on the right, the output page with the original closed. The
screenshots are of the saved HTML at 760 px.)

## Spend

Recorded live spend, summed over every call in the three answers files (what each call was
charged at list price): **$9.23** of the $15 cap.

| Step and model | $ |
|---|---|
| page reader, `claude-opus-5-5` (36 slice pages, 8 real pages, 2 probe pages) | 4.06 |
| page reader, `claude-sonnet-5-5` | 1.79 |
| page reader, `claude-haiku-5-5` (pilot, slice, settings runs, real pages) | 0.32 |
| equation step, `claude-opus-5-5` (second readings and the typed baseline) | 2.75 |
| equation step `claude-haiku-5-5`, figure step, shadow second check (typed baseline only) | 0.31 |

No run was repeated to confirm another; the final tables were re-scored from the recordings at
no cost (`--answers-offline`).

## What this does not show

- Synthetic scans are a model of a scan. They are clean about the page's geometry (a printed
  page, not a photograph with a curved spine) and they come from born-digital printouts: they say
  how well the pass reads a degraded print, not a real book. The eight real pages say it can read
  real ones; they are eight.
- Handwriting is measured only by eye on four pages. There is no handwriting answer key and no
  claim here about a word or symbol error rate on handwriting.
- One run per arm, 36 pages, six documents; no interval. The Haiku-to-Opus gap on equations is
  large enough to take seriously, the Sonnet-to-Haiku one is not.
- The score of equations in prose (inline) is the key's, with its glued brackets and its text
  written maths; the flag numbers for inline formulas follow it.
- Cost is the provider's list price as recorded, with the refusal fallback's occasional dearer
  rerun in it; `MAX_USD_PER_JOB` and `MAX_USD_PER_PAGE` apply as they do to every call.
- Nothing here tests a reviewer: the review load is the pipeline's count of decisions, not a measure
  of how long they take or how many wrong readings a tired person lets through. That is the
  risk this design leans on most.

## Recommendation

1. **Keep `FEATURE_IMAGE_PAGES` off by default.** The measurement is on development material and
   synthetic scans; the held-out set has not been used (and should not be used to tune). A held-out
   measurement on scans of held-out sections (the same `--scanned` option, a fresh registration)
   decides the default.
2. **If it is offered before then, offer it as an opt-in with the main model** (`IMAGE_PAGES_MODEL`
   unset): it is the only reader that matched the typed pipeline on equations, read handwriting
   without a confident misread, and had boxes good enough for the second reading to read what the
   page reader meant. Raise the job caps for such documents (`MAX_USD_PER_JOB` to about $4 for 30 pages).
   Haiku at $0.003 a page is the choice when cost matters more than equations: its mistakes are
   mostly caught by its own second reading, but it will misread a handwritten heading with confidence.
   Sonnet is not recommended here.
3. **Turning it on means editing the public copy**, which is the author's: the README ("not an OCR
   engine for scans or handwriting"), `limits.html`, the site's method and home pages, and the
   known-limits text all say such pages are kept as images.
4. **Open questions for the author** (ADR 0007, "Consequences"): whether inline formulas stay part of
   their paragraph's item; whether a display equation the two readers agree on may become an optional
   note (here agreement is not evidence when the same model reads twice, and 49 agreed equations were
   wrong by the strict score); how much review a 30-page scan may ask of an instructor (105 decisions).
