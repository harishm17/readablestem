# Evaluation

How ReadableSTEM measures its own output, on what data, and what the numbers leave out.

Status (2026-11-02): the harness is built and tested offline, and real-model runs on the
sample are committed as `data/eval/latest-<task>.json` (pipeline 0.5.0, Claude; see "Versions
of the committed reports" and "Baselines and the gate" below). All of them come from three
sections of one OpenStax book, the same sample the checks were developed on. They are
development numbers: none of them is a held-out result, and they should not be read as an
estimate for documents in general. The sections "Results (pipeline 0.4.0, ...)" and the
held-out and real-paper sections that follow it describe runs of older pipeline versions and
say which; they are kept as they were.

The invented-content rate (how much of an output the PDF does not support, for ReadableSTEM
and for other systems) has its own page: [eval-invented.md](eval-invented.md).

**Registered held-out results, newest first.** Each was registered before any system ran, and
each page says which pipeline version it measured; an older one is history, not the current
system.

| Result | Report |
|---|---|
| fresh5, 2026-11-04: 26 documents, 1,715 gold equations. Registered verdict on the primary score: no difference this set can detect from the Opus chatbot (0.840 against 0.836); silent-error and flag-recall criteria not met; stratum D (chemistry) not a success. Pipeline 0.5.9 | [eval-equation-fresh5.md](eval-equation-fresh5.md) |
| fresh4, 2026-10-26: 15 papers, 1,600 equations | [eval-equation-round3.md](eval-equation-round3.md) |
| fresh3, 2026-10-23: a third set of papers | [eval-equation-round2.md](eval-equation-round2.md) |
| Real-world held-out part, 2026-10-21: 15 arXiv papers, 1,826 equations | [eval-realworld.md](eval-realworld.md) |
| First registered result, 2026-10-14: 15 OpenStax sections. Pipeline 0.4.0 | [PROOF.md](../PROOF.md) (the replayed report, frozen at the tag) |

## Versions of the committed reports

Two version numbers exist and they are not the same thing. `__version__` in
`src/readablestem/__init__.py` (0.1.0) is the package's own and has not been bumped for
pipeline changes. The version a result is measured under is the **pipeline version**,
`PIPELINE_VERSION` in `src/readablestem/pipeline/run.py` (0.5.11 as of 2026-11-11; its comment lists what
each bump changed). Every report records it as `pipeline_version`, and so does a converted
document (`DocumentResult.pipeline_version`); `eval gate` lists a change of it under the
table. A number in these docs is quoted with the pipeline version that produced it, and a
report from an older version is not edited to look current: it stays labeled with the
version it was made under.

The committed development-set reports (checked against the files by `tests/test_eval_docs.py`):

| File | Run date | Pipeline | Models |
|---|---|---|---|
| `baseline-equations.json`, `baseline-figures.json`, `baseline-headings.json`, `baseline-document.json`, `baseline-tables-local.json` | 2026-10-14 | 0.5.0 | claude-opus-5-5, claude-haiku-5-5 (figure judge: claude-opus-5-5; tables local calls no model) |
| `latest-headings.json`, `latest-tables-local.json` | 2026-11-18 | 0.5.11 | headings: the fake client (`eval pipeline --task headings --fake`; the task calls no model, so the model names in the file are only the defaults of a checkout with no settings); tables local: no model. `make reproduce-rerun` makes both again with no key and compares the headline numbers |
| `latest-figures.json` | 2026-10-14 | 0.5.0 | same. **Historical record**: not gated |
| `latest-equations.json`, `latest-document.json` | 2026-10-21 | 0.5.0 | same (the equation prompt changed after the baseline: `equation-df0e12f1` to `equation-68ebbbf9`). **Historical record**: not gated |

**What CI holds a pull request to.** The gate measures the code as it is: a `latest-*.json`
report is held to its baseline only when this pipeline version made it (`eval gate
--require-current` refuses `pipeline X (code: Y)`). Headings and tables-local cost nothing,
so CI's `eval-gate` job gates those two, re-made at 0.5.11. Equations, figures and document
need a paid run: their latest and baseline reports are historical records of the 0.5.0
system and no PR gate reads them. A recording could not stand in for a paid run: a replay of
the recorded answers held none of the requests those three tasks make (0 of 60, 26 and 114
answered). `eval-live.yml` re-measures them weekly on the current code and compares them
with the baselines; a paid recording of the three is owed before they can gate a pull
request again. The same job replays the corpus against the committed record (`eval burden
--same`, "The replay record, and CI" under `eval burden`) when a change touches the package,
which finds a change of behavior that a version number alone would not.

The dates are when the runs were made, not when the code was last changed: what keeps a
latest report from going stale is the gate's `--require-current` check on the pipeline version, prompt versions and
model ids, the eval guard on pull requests, and the weekly live run below.

## The idea

OpenStax publishes its textbooks as web pages with expert-written MathML, alt text and a
real heading outline. That makes a page an answer key. The harness:

1. takes the answers from the gold set (`data/gold/`, built by `readablestem gold fetch`),
2. rebuilds the question a user would bring: a PNG of an equation, or the page printed to
   an ordinary PDF (`eval/render.py`),
3. runs the real pipeline code on it,
4. scores the output against the answers.

| Task | Input rebuilt from gold | Code under test | Scored against |
|---|---|---|---|
| `equations` | each equation rendered to a PNG | `equation_step.transcribe` | gold MathML |
| `headings` | each page printed to PDF | `layout.analyze` + `heading_step.heading_fixes` | gold h1-h6 |
| `figures` | each figure's image file | `figure_step.describe` | OpenStax alt text, via an LLM judge |
| `document` | each page printed to PDF | `run.convert_pdf` (everything) | gold equations; HTML checks |
| `tables` | each table gold page printed in three table styles, plus authored documents | `layout.analyze` (tables) and `table_step.rebuild` | gold tables (see "Tables" below) |

## Datasets

### Gold sample (`data/gold/`)

Three sections of OpenStax *Physics* (high school), chapter 2, CC BY 4.0, retrieved
2026-10-06 (archive 20260604.144757). This sample is a pipeline smoke test, not the final
gold set; choosing the final gold-set pages is still open (ADR 0001).

| Kind | n | Notes |
|---|---|---|
| equation | 30 | 17 block, 13 inline; 12 sit inside a note or example box |
| figure | 13 | one image each; all 13 have a non-empty OpenStax alt text, 39 to 980 characters (median 266) |
| heading | 121 | h2: 3 (one section title per page), h3: 74, h4: 43, h5: 1. 50 are the repeated title "Teacher Support"; 20 are outside any box |
| table | 3 | not evaluated (tables are out of the 2026 beta) |
| page | 3 | each prints to 13-16 Letter pages |

`manifest.json` lists every file with its SHA-256; reports record the SHA-256 of the
manifest itself, so two reports can be checked for "same gold data".

### Heading records

Added for the headings eval: every `h1`-`h6` in the page content, in document order,
after the equation, figure and table records (so no earlier record or JSONL line
changed). Headings with no visible text are skipped.

| Field | Meaning |
|---|---|
| `id` | `openstax/<book>/<page>/hd-NNN` |
| `level` | 1-6, as published (OpenStax section titles are h2) |
| `text` | visible text, whitespace collapsed, footnote markers removed |
| `element_id` | the heading's `id` attribute, if any |
| `context` | enclosing box class (`os-teacher`, `worked-example`, ...) or null |

Plus the shared fields every gold record carries (see `docs/development.md`).

### How inputs are rebuilt (`eval/render.py`)

- **Equation PNG.** The gold MathML (annotations dropped, re-serialised from an
  allowlisted tree) is drawn by headless Chromium at device scale 2 (about 40 px per em,
  close to a 200 dpi crop) on white, cropped to the `<math>` box plus 4 px. Cached by the
  SHA-256 of the MathML under `.cache/eval/equations/`.
- **Equation text layer.** Inside `run.convert_pdf`, `layout` puts the PDF text of the
  equation's region on `Element.text`, and `equation_step` uses it twice: inline
  equations with 25 characters or fewer of it start on the cheap model, and the
  transcription is cross-checked against it (a mismatch adds a failed check and lowers
  the confidence, so the fix cannot be accepted). So the equation is also printed to PDF
  by Chromium, and the text of that PDF (control characters dropped, whitespace
  collapsed, as `layout` does) becomes `Element.text`. It looks like the text of the
  printed pages in the document task: math-italic letters, fractions flattened out of
  order (`distance𝑣avg = time`). Cached next to the PNG.
- **Equation context.** `run.convert_pdf` also passes the text around each equation as
  `context_text`. The eval builds the same window from the page's archive XHTML, finding
  the equation by its `index_on_page` (and refusing to go on if the equation there is not
  the gold one): an inline equation inside a paragraph, list item, table cell or heading
  gets up to 300 characters of it either side of `[this equation]`; a display equation
  (or an inline one alone in its paragraph) gets the end of the previous text block and
  the start of the next, one per line. Other equations show as `[equation]`.
- **Page PDF.** The page's archive XHTML (from the `gold fetch` cache, `.cache/openstax/`)
  is turned into plain printable HTML: headings at their published levels, paragraphs,
  lists, tables, MathML, figures with captions, note boxes with a border. Scripts,
  styles, iframes and links are dropped (link text stays). Chromium prints it to Letter
  (or A4) with a running footer ("Access for free at openstax.org. Physics (OpenStax),
  CC BY 4.0. Page N of M") and a full attribution block at the end.
- **The answer key stays out of the PDF.** Images get `alt=""`, Content MathML
  annotations are removed, and the PDF is untagged (no structure tree), so the pipeline
  cannot read OpenStax's alt text or MathML back out of the file. A browser test checks
  that the PDF has no `StructTreeRoot` and that the alt text is not in its text layer.
- **Figure images** are downloaded once from `images[].url` (openstax.org only) into
  `.cache/eval/images/`, converted to PNG and capped at 2048 px on the long side, the
  same cap `layout.crop` uses.
- **Inputs before spend.** A run builds every input first (all PNGs, text layers,
  contexts, page PDFs, figure downloads), then closes its Chromium, then makes the first
  model call. A failed download or render stops the run there with exit 2 and nothing
  spent; rerun it (finished renders and downloads are cached). Closing the browser first
  matters for the document task: Playwright refuses to start a second sync session in a
  thread that has one open, so axe-core (`html_checks.axe_checks`) would fail on every
  page printed in the same run. Inputs are held in memory for the run: a few kB per
  equation, up to a few MB per figure or page.

## Metrics

Conventions for every task:

- A **rate** is a fraction in [0, 1]. Unless stated, the denominator is every item in
  the run, and an item with no proposal counts as wrong.
- **95% CI** for a rate is the Wilson score interval (reliable at small n and near 0
  or 1). Reported as `[low, high]`.
- **Percentiles** use linear interpolation between closest ranks (numpy's default):
  for sorted values x[0..n-1], position (n - 1) * q / 100.
- **Cost** is the sum of `ModelCall.cost_usd` (usage-based, `models/pricing.py`, prices
  checked 2026-10-07). `per_item_usd` is pipeline cost divided by `n`; judge cost is
  reported separately and never included in it. `budget_spent_usd` is what the run's
  spend guard recorded; after a budget stop it can exceed `total_usd`, because the item
  in flight was paid for in part and then dropped (`dropped_usd` is the difference).
- **Latency** per item is the sum of its model calls' `latency_ms` (calls within an item
  run one after another). It excludes rendering and local processing. The document task
  is different: its item is one OpenStax *section* printed to a PDF of 13-16 pages, and
  its latency is `DocumentStats.latency_ms`, the wall-clock time of converting that whole
  PDF. The report's `latency_ms.basis` says which one applies.
- **Cost per page.** For the document task `per_item_usd` is per *section*
  (`per_item_unit` is `section`), not per PDF page, so the report also gives
  `cost.pdf_pages` (the PDF pages converted, from `DocumentStats.pages`; a section the
  layout could not read adds none) and `cost.per_pdf_page_usd`. The first real run cost
  $0.161 for 3 sections of 16, 14 and 13 PDF pages: $0.054 per section but **$0.0037
  per PDF page**. Quote the per-PDF-page figure for anything about running cost; it is
  the one `ops report` gives in production (cost per converted page from the
  aggregates). The gate's `document_cost` still compares `per_item_usd`, which is fine
  for runs over the same sections.

### equations

For each gold equation: render, call `transcribe(element, png, context_text=context)`
with `element.text` = the equation's text layer and `element.display` = the gold
`display` (see "How inputs are rebuilt"), then score the proposed MathML with
`eval.equations.score_mathml`.

| Metric | Definition |
|---|---|
| `exact_strict` | share whose normalised MathML equals the gold exactly (see the `eval/equations.py` docstring for the normalisation) |
| `exact_lenient` | same, after lenient normalisation (`mi`/`mn`/`mtext` alike, trailing sentence punctuation dropped) |
| `exact_lenient_ci95` | Wilson 95% interval for `exact_lenient` |
| `mean_teds` | mean of TEDS = 1 - TED / max(nodes), Zhang-Shasha tree edit distance on normalised trees; 0 with no proposal |
| `proposal_rate` | share where the step proposed MathML at all |
| `secondary_stream_v2` | the labeled secondary scorer (below): `exact` and `mean_similarity` of the symbol streams. Not a replacement: `exact_lenient` stays the primary number and `wrong` stays defined by it |
| `by_status` | per `Fix.status` (accepted, needs_review, abstained, failed): `n`, `share`, `exact_lenient`, `mean_teds`. Shows whether "accepted" really is more accurate than "needs review" |
| `by_route` | same, per route: `cheap` (cheap model only), `main` (main model only), `escalated` (cheap first, then main), `fallback` (any call made as a fallback), `none` (no call) |
| `by_display` | same, per gold `display` (block, inline) |
| `selective` | for t = 0.50, 0.55, ..., 0.95: keep only proposals with confidence >= t. `coverage` = kept / n; `accuracy` = `exact_lenient` among kept (n/a when none kept); `kept`; `mean_teds` among kept. A useful confidence makes accuracy rise with t |

`review_load` (see "Review load" below): `required`, `optional`, per item and per
section, the reasons for each tier, `flags` and `flags_excluding_scorer_gaps`
(precision and recall with wrong = not `exact_lenient`), `scorer_gaps` and
`wrong_not_required`. Per item in the JSONL: `review`, `tier_reasons`, `wrong`,
`wrong_not_required`, `scorer_gap`.

Ceiling: scored with known-correct LaTeX for all 30 sample equations, the scorer gives
exact_lenient 0.83, exact_strict 0.43, mean TEDS 0.87 (`make eval-latex-check`; 5 known
normalisation gaps). A perfect transcriber cannot score higher on this sample until
those gaps are closed.

**Secondary scorer: stream v2** (`eval/equations_v2.py`, backlog T2-12). The gaps above
are packing gaps: the answer key writes `<mtext>time =</mtext>`, `<mn>-5</mn>` or
`<mtext>m/s)(46 s)</mtext>` where a transcription gives text plus `<mo>`, and uses other
code points for the same dash, mu, prime or degree sign. The held-out run could not score
230 of 1,217 matched equations exact for this reason (A4). So the equations and document
tasks, `eval equations` and the reproduce check also score every equation as a *stream of
symbols* and report it beside the primary scorer, labeled `secondary`; it never replaces
it and the primary numbers, the gate and the committed reports do not change. Rules, in
order: the same lenient normalisation as the primary scorer; every token split into atoms
(a number, a letter, one other character; a word is its letters); look-alike characters
unified (every dash and the minus sign, micro sign and mu, apostrophe, right quote and
prime, degree sign and ring operator, middle dots, division slashes, a few relation and
Greek-letter glyph variants; bold and double-struck styles stay); a degree sign or primes
written as a superscript equal the inline character; a decimal point written as its own
token joins its numbers; fractions, scripts, roots and tables stay as open and close
markers, so `a/b` is not `a b`. The score is `exact` (equal streams) and a similarity
(1 - edit distance / longer stream). Everything the primary lenient scorer calls exact is
exact here too. On the 30 sample equations with known-correct LaTeX: primary 25 of 30,
stream v2 30 of 30. On dev-v2's 1,809 gold equations it tells 1,442 distinct streams apart
where the lenient trees give 1,443 (the one pair it merges is the same formula with the
degree sign written inline or as a superscript). In the document task a gold equation
that came out in pieces, or two that came out as one, is credited by
`secondary_stream_v2.end_to_end_exact_with_credit` when the pieces' (or the merged
equation's) streams, in order, equal the gold's (commas, semicolons, points, "and", "or"
between them ignored); `credited_split` and `credited_merged` count those, and the split
or merge is still a layout error in `split_gold_equations` and the detection numbers.
On the 7 wrong dev equations of the recorded document run whose answers are stored
(`data/eval/latest-document.json`, 2026-10-12), 5 are exact under stream v2 (all five are
packing gaps), so that run's matched 0.733 (22 of 30) is 0.900 (27 of 30) on the secondary
scorer; the eighth wrong equation, a required decision, has no stored answer. The two that
stay wrong are real differences: a subscript the answer key sets bold
(`\mathbf{v}_{\text{avg}}` against a bold `avg`), and a comma the model copied from the
text layer.

**The document task reads the output as the student's file states it.** Two things the
equation scorer used to leave out, which made correct output count as "not found" or "wrong"
(`pipeline_eval.document_page_item`, `with_table_maths`, `read_mathml`):

- *Table cells.* A rebuilt table is one TABLE element, but its cells hold formulas, and the
  output HTML a student reads has them inside the table. Every math cell (validated MathML),
  and every cell of text that states math with raised or lowered characters (`x²`,
  `10^(-3)`), is an inline equation of the page that the scorer aligns to the gold. This used
  to be a second reading kept beside the registered one (`table_maths_reading`, read by
  `flag_features.read_items`); it is the one reading now, and items recorded before this change
  keep the old key, which `read_items` still merges.
- *Math written as text with raised or lowered characters.* An equation element the pipeline
  left as text (`Element.script_runs`: `H₂O`, `cm³`) and a text token with Unicode
  superscript or subscript characters or a `^(...)` run are read as scripts, `<msub>` or
  `<msup>` of the atom before the run, as the gold writes them (`equations_v2.read_scripts`,
  `mathml_of_text`), not as the order of their characters. The primary scorer's rules are
  unchanged; an element with no proposal whose text states scripts is scored from that
  reading by every scorer, and one with no raised or lowered characters is still "no proposal".

No numbers in this document change with it: the stored outputs and the answer key of the
374-equation set this was found on are not in the repository or the corpus cache, so the
before and after counts have to be taken by running `eval pipeline` (a replay of stored answers
costs nothing) on that set.

### headings

For each gold page: print to PDF, `layout.analyze`, `heading_step.heading_fixes`. A
predicted heading is a fix with a `heading` whose status is accepted, needs_review or
edited.

Matching: both sides are normalised (NFKC, case-folded, punctuation to spaces, spaces
collapsed) and aligned **in reading order** by a weighted longest-common-subsequence:
pairs need a difflib ratio >= 0.85, may not cross, and the total similarity is
maximised. Order matters because one page repeats "Teacher Support" 22 times (an h3 and an h4 per box).

| Metric | Definition |
|---|---|
| `precision` | matched / predicted headings (micro-averaged over pages) |
| `recall` | matched / gold headings |
| `recall_ci95` | Wilson interval for recall |
| `f1` | harmonic mean of precision and recall |
| `level_accuracy` | among matched pairs, share whose **relative** level agrees: gold level minus the page's top gold level, against predicted level minus the page's top predicted level. A PDF has no levels, and OpenStax pages start at h2, so "two below the top" is what can be compared |
| `precision_by_status` | precision of accepted vs needs_review headings |
| `recall_by_context` | recall per enclosing box (`body` = none); `os-teacher` holds 50 of 121 gold headings |
| `per_page` | precision, recall and counts per page |
| `page_errors` | `LayoutError` codes for pages the pipeline could not read; such a page counts as "found nothing" (recall 0 for it), it does not stop the run |
| `review_load` | see "Review load": decisions per section and per PDF page (`pdf_pages` from `layout.analyze`), optional notes per PDF page, `flags` with wrong = not a gold heading or matched at the wrong relative level, `wrong_not_required` (text, level, status, tier reasons, `why_wrong`: `not_a_gold_heading` or `wrong_level`) |

### figures

For each gold figure: download its image, call `describe(element, png,
context_text=caption)`, then grade the proposed alt text with the judge.

| Metric | Definition |
|---|---|
| `proposal_rate` | share where the step proposed alt text (including decorative) |
| `check_pass_rate` | share with a proposal and no failed error-level `Check` (the step's own deterministic validators) |
| `failed_check_codes` | count per failing check category |
| `with_reference` | figures whose OpenStax alt is non-empty (the judge needs one) |
| `judge_pass_rate` | passes / figures measured. Measured = with a reference and either judged or with no proposal (an abstention gives the reader nothing, so it counts as a fail). Figures whose judge call failed are excluded and counted in `judge_errors` |
| `judge_pass_rate_ci95` | Wilson interval for it |
| `judge_pass_rate_answered` | passes / figures actually judged (abstentions left out) |
| `judge_mean_score` | mean 1-5 judge score over judged figures |
| `judge_factual_error_rate` | share of judged figures where the judge found a factual error |
| `decorative_rate` | share of proposals marked decorative |

**Judge pass** = `conveys_key_information` and not `factual_error`.

The judge (`eval/judge.py`, prompt `models/prompts/judge_figure.md`) uses the main model
(gpt-6.1-sol) with no fallback, so the instrument never changes mid-run. It sees three
texts as fenced data, in this order: the candidate, the OpenStax reference alt, the
caption, and the figure's image (the prompt then treats the image as the ground truth;
without an image it treats the reference as the facts). Nothing tells it which system
wrote which text.
Each text is capped (900 / 800 / 200 characters, marked " [cut]") so the block stays under
the 2000-character fence limit; 2 of the 13 sample references exceed 800 characters.
Output schema: `conveys_key_information` (bool), `factual_error` (bool), `score` (1-5),
`confidence` (0-1).

#### Long descriptions (FEATURE_LONG_DESCRIPTIONS on)

With `FEATURE_LONG_DESCRIPTIONS=true` in the environment, the figures task runs the step
with the options the app would use (`ConversionOptions.from_settings`), so it sends the
long prompt (`figure_long.md`). The gold figures are images, so the step gets no PDF
text from inside the figure: every data table counts as read from the image. A second
judge (`judge.judge_long`, prompt `models/prompts/judge_long_description.md`, a rubric of
its own, main model, no fallback, always with the image) grades every figure that got a
long description or a data table. It sees the long description, the table (one row a
line, cells separated by " | "), the OpenStax alt as a reference (not an answer key) and
the caption, fenced and capped at 1000 / 400 / 250 / 150 characters. Output:
`accurate`, `complete`, `invented_values` (bools), `score` (1-5), `table_values_ok`
(null without a table; true when every value equals a printed one or, read off the
chart, lies within half of the axis's smallest labeled step), `table_wrong_values`,
`confidence`. **Long pass** = accurate and complete and no invented values. The report
gains `metrics.long_descriptions` and a "Long descriptions" table:

| Metric | Definition |
|---|---|
| `described`, `described_rate` | figures with a long description, and their share of proposals |
| `complex_described_rate` | the same over charts and diagrams only (the rest get the notice `long_description_missing`) |
| `mean_chars` | mean length of the long descriptions |
| `judged`, `pass_rate` (+ `_ci95`) | long descriptions the judge graded, and the share that passed |
| `accurate_rate`, `complete_rate`, `invented_values_rate`, `mean_score` | the judge's fields over graded long descriptions |
| `unsure` | long descriptions the model marked unsure (an optional note each) |
| `data_tables`, `table_values_ok_rate`, `table_wrong_values` | data tables proposed; the share whose values were all within tolerance; cells outside it |
| `values_from_text` | tables whose every number is in the PDF text (always 0 here: the inputs are images) |
| `colour_only` | figures flagged as telling things apart by color alone |
| `required_decisions`, `tables_offered` | figures needing a decision; data tables read from the image, offered as optional extras (not in the file until a reviewer adds them, never a decision; `required_for_values` in reports before 2026-10-09, when each was a decision) |

The long judge is not calibrated against human labels yet. The 13 gold figures are too few for a
pass-rate claim; a 50-figure set (`data/gold-figures-v2/`, drawn by a written seeded rule from CC BY
sources) and a draft pre-registration with the judge-calibration plan are in
[eval-figures-long.md](eval-figures-long.md). `gold/figures_v2_table.py` scores a data table cell by
cell against a reference table, with no model.

### document

For each gold page: print to PDF, `run.convert_pdf`, then align predicted equations with
gold equations.

Alignment: in reading order, by the weighted LCS above with threshold 0.5. A predicted
equation's signature is the token text of its proposed MathML and, as a second chance,
the PDF text of its region (best of the two counts), so a detected but mistranscribed
equation still counts as detected. Gold signature: token text of the gold MathML after
lenient normalisation. Signatures are NFKC, case-folded, spaces removed, U+2212 as "-".

| Metric | Definition |
|---|---|
| `detection_precision` | matched / predicted equation elements |
| `detection_recall` | matched / gold equations |
| `detection_recall_by_display` | recall for gold block and inline equations separately |
| `matched_exact_lenient` | `exact_lenient` over matched pairs (no proposal = wrong) |
| `matched_mean_teds` | mean TEDS over matched pairs |
| `end_to_end_exact_lenient` | exactly right equations / gold equations: detection and transcription together |
| `unmatched_predicted`, `unmatched_by_reason` | predicted equations with no gold partner, by why: `prose_math` (math OpenStax wrote as HTML text, which has no gold MathML; only with evidence of a formula, see docs/layout.md) or `other` |
| `detection_precision_excluding_prose_math` | matched / (predicted - `prose_math`): an estimate, since the gold cannot confirm those are formulas |
| `unmatched_text_layer_supported`, `unmatched_text_layer_supported_by_reason`, `unmatched_not_supported` | the unmatched predictions the PDF's own text supports: not a fragment, and the transcription uses the same letters, digits and Greek letters as the region's text layer (`text_layer_symbols_differ` is False). That is real math the gold wrote as text (A15: 176 of the 439 unmatched on the held-out run), kept apart from fragments, which agree with their own text trivially and never count as supported. Needs a model run: without proposals (`--fake`) nothing is supported. Per unmatched entry: `text_layer_supported` |
| `detection_precision_excluding_text_layer_supported` | matched / (predicted - supported extras): the detection precision with the artefact taken out, beside the unchanged `detection_precision` and the heuristic `..._excluding_prose_math` |
| `secondary_stream_v2` | the labeled secondary scorer (see "equations"): `matched_exact`, `matched_mean_similarity`, `end_to_end_exact`, `end_to_end_exact_with_credit` (split and merged gold credited) and `credited_split`, `credited_merged` |
| `prose_classes` | only with dev-v2 gold: the scripts, code, glossary and hyphen scorers (see "Extended development set") |
| `split_gold_equations` | gold equations the pipeline found as several pieces |
| `chemistry` | only when the gold has chemistry equations (ids with `chemistry`); report fields beside the scorers, never in their place. `chem_right_excl_times`: `n`, `right`, `rate` of chemically right (`exact_chem`) with the lone multiplication sign gold left out of both (the sentence around it says "times"), `lone_times` (how many were left out) and `chem_right_n`, `chem_right`, `chem_right_rate` with them kept. `silent_harm`: `n`, `n_gold` and `per_100_gold` (counts only: no item ids, so a held-out report names no items) of aligned equations that are wrong on `exact_chem`, not a required decision, and spoken differently from the gold (a reading that lacks only a prefix nuclide's atomic number the page does not show is not harm). Neither number is read by `eval gate`, a floor or a tolerance: they are for reading, not for passing or failing a change. Per pair: `silent_harm`; per page: `chem_lone_times_gold_ids` (`eval/equations_chem.py`) |
| `static_violations` | failed error-level checks from `html_checks.static_checks` on the output HTML, summed over pages (warnings excluded) |
| `axe_violations` | same for `html_checks.axe_checks`, over the pages where axe ran |
| `axe_checked_pages` | pages where axe-core really ran (`axe_violations` is over these) |
| `axe_skipped_pages` | pages where axe could not run: `axe_checks` returned its single "axe" check coded `skipped` (Playwright or Chromium not installed) |
| `axe_error_pages` | pages where axe broke: a single failed "axe" check (WP4 codes it `axe_error`). Neither case counts as zero violations. Rule results are named `axe.<rule>`, so a failure of axe's real `skip-link` rule still counts as a violation |
| `page_errors` | `LayoutError` codes for pages the pipeline could not read; such a page counts as "found nothing", it does not stop the run |
| `review_load` | see "Review load": every kind's decisions per section and per PDF page, optional notes per PDF page, `equation_flags` (matched equations, wrong = not `exact_lenient`; also `equation_flags_excluding_scorer_gaps`), `heading_flags` (the headings task's rule, on the same PDF), `flags` and `flags_excluding_scorer_gaps` (both together), and `wrong_not_required` |

TODO(Harish): document precision is now limited mostly by prose math (in
document-run1, 25 inline formulas OpenStax wrote as HTML, with no gold MathML). Decide
whether to add them to the gold set (hand-written MathML for those spans) or keep
scoring them apart as `detection_precision_excluding_prose_math`; the report gives both
numbers until then.

### Review load (equations, headings, document)

How much the pipeline asks of a person, and whether it asks about the right things.

| Metric | Definition |
|---|---|
| `required_decisions` (`required` for equations) | fixes with `Fix.review == "required"`, a group of repeated headings counted once (`core.triage.units`) |
| `required_per_pdf_page`, `required_per_section` | the same divided by the PDF pages converted, or by the gold sections (the equations task prints no pages: per item and per section only) |
| `optional`, `optional_per_pdf_page` | fixes accepted with an optional note |
| `required_by_reason`, `optional_by_reason` | `core.triage.tier_reasons`: failed check codes, `Fix.reason`, `heading.repeated_label`, `below_accept` |
| `flag_precision` | required *decisions* with at least one wrong member / required decisions (a group of 11 correct headings is one imprecise decision) |
| `flag_recall` | wrong items inside required decisions / all wrong items: the share of real errors a person is asked about |
| `..._excluding_scorer_gaps` | the same without the 5 sample equations whose known-correct LaTeX (`tests/fixtures/sample_latex_predictions.jsonl`) still fails `exact_lenient` (`scorer_gap_ids`): any transcription of them scores wrong |
| `wrong_not_required` | every wrong item that was not required, with its tier and reasons, to read one by one |

Wrong = not `exact_lenient` for an equation (only equations matched to gold in the
document task); not a gold heading, or matched at the wrong relative level, for a
heading. Figures and unmatched equations have no answer key in these tasks: they count
in the load, not in the flags. A gold heading the pipeline missed is not an item a
person could be asked about, so it is not in flag recall (it is in heading recall).

### Second automated check (figures, equations, document)

The Decisions API check of ADR 0005 (docs/steps.md "Second automated check") runs in
every real eval when its settings are on (the default; `DECISIONS_FIGURE_VERIFY=false`
or `DECISIONS_EQUATION_SHADOW=false` turn it off for a run). Code:
`eval/verifier_metrics.py`. Per item in the JSONL: `verifier_*` (figures) or `shadow_*`
(equations; `shadow_cheap_*` for the cheap answer before escalation), plus
`verifier_cost_usd`, `verifier_latency_ms`, `verifier_requests`. Report:
`metrics.second_check` and, in `cost`, `verifier_usd`, `verifier_per_item_usd`
(document: `verifier_per_pdf_page_usd`). Its cost is part of `pipeline_usd` and
`per_item_usd`: the app pays it too.

| Metric | Task | Definition |
|---|---|---|
| `answered`, `outcomes` | figures | accepted figures the check answered; outcome codes of every check that ran (`ok`, `timeout`, `refused`, ...) |
| `flagged`, `flag_rate` | figures | answered figures with min(P(ok), 1 - P(factual error)) below `DECISIONS_FIGURE_FLAG_BELOW` (0.5): the ones that get the optional note; flagged / answered |
| `agreement_with_judge` | figures | over figures with both an answer and a judge verdict, flag against judge fail: `raw_agreement`, `kappa` (Cohen's), `confusion`, and `auroc_score_vs_judge_fail` (how well 1 - score ranks the judge's failures above its passes; threshold-free) |
| `final.auroc_vs_wrong` | equations | AUROC of P(meaning_changing) on the final proposal against wrong (not `exact_lenient`); `..._excluding_scorer_gaps` without the 5 known scorer gaps |
| `final.would_flag`, `would_flag_precision`, `would_flag_recall` | equations | proposals at or above `DECISIONS_EQUATION_FLAG_AT` (0.2) and how many of them are wrong / how many wrong ones they include. Shadow: nothing was flagged in the app |
| `cheap_stage` | equations | answers on the cheap model's answer before escalation: how many, how many would flag, mean P(meaning_changing). The cheap answer is not kept on the fix, so it is not scored against gold |
| `equations_matched` | document | the same as `final`, over the equations matched to gold |
| `figure_checked`, `figure_flagged`, `equation_checked`, `equation_would_flag` | document | counts over every fix of the converted pages |

Agreement with the judge is agreement between two models, not validity: only Harish's
labels (`eval calibrate`) can say which one is right.

### Speech under screen-reader engines (`--speech`, `scripts/screenreader_speech.py`)

A student hears our MathML through their own screen reader, not through our speech strings
(`pipeline/speech.py`). NVDA 2026.1 and JAWS speak MathML with MathCAT; MathJax and ChromeVox
use Speech Rule Engine (SRE). `eval/speech_oracle.py` runs any pair of MathML (ours and the
gold's) through both and reports where they sound different. Offline, no key, $0.

Engines read: MathCAT 0.7.7 (ClearSpeak and SimpleSpeak, English, Medium verbosity, NVDA's
defaults) and SRE 4.1.5 (`clearspeak` and `mathspeak` domains). Build them once; nothing is
installed system-wide:

```
scripts/speech_oracles/build.sh      # MathCAT at tag v0.7.7 (commit checked), a small batch tool
                                     # built with the toolchain pinned in rust-toolchain.toml,
                                     # SRE from the committed package-lock.json (npm ci
                                     # --ignore-scripts); all under .local/speech-oracles/
                                     # (SPEECH_ORACLES_DIR moves it); versions: versions.env
```

It needs git, node and npm, and about a minute of compile time. Rust comes from an existing
rustup or cargo; if there is none, `build.sh` downloads a pinned `rustup-init`, checks its
SHA-256 against `versions.env`, and installs into `~/.cargo` (no sudo). Without the build, the script
and `--speech` say so (`speech oracles skipped: ... run scripts/speech_oracles/build.sh`) and
produce nothing; the tests that speak MathML skip.

```
# any set whose rows came from `eval pipeline --items` (equations or document task) and gold dir
uv run python scripts/screenreader_speech.py --items items.jsonl --gold data/dev-v2/dev \
  --sections chemistry-atoms-first latex-chemistry --out rows.jsonl --report speech.json
# any other set: JSON lines {id, gold|gold_latex, ours|ours_latex|null, source?, class?}
uv run python scripts/screenreader_speech.py --pairs pairs.jsonl
# one expression
uv run python scripts/screenreader_speech.py --mathml '<math>...</math>'
# in an eval report (equations and document tasks): adds a `speech` block
uv run readablestem eval pipeline --task document --set dev-v2 ... --speech
```

Per equation (`--out`): the spoken strings under each engine for ours and the gold, whether
MathCAT recognized chemistry (`data-chem-formula` or `data-chem-equation` in its canonical
MathML) on each side, a word diff, and a class (`chemistry`, `table or matrix`, `prescripts`,
`limits, accents, arrows`, `fraction`, `root`, `scripts`, `text in math`, `plain`; or the
`class` the input gave). Per set: the share of ours whose speech equals the gold's under each
engine (case, pauses and spacing ignored) with a 95% Wilson interval and n, once counting an
equation with no proposal as different and once over proposals only, the worst classes over
proposals (at least 3 equations), and the most common word-level differences. The report's
`speech` block holds aggregates only: no ids, no speech strings and no per-book numbers. The
script refuses held-out and sealed gold (and rows kept under the cache home), as
`eval pipeline --set heldout` does. An engine that fails reports only its exit code, and each
engine process is killed after 15 minutes.

What it does not show. Equal speech means two markups read the same under that engine; it is
not a listening test and not a statement that the reading is right. The gold's own markup can
read badly (OpenStax sets the minus of `x - 3` inside the number, which MathCAT says as
"negative"), so a difference from the gold can be ours reading better. MathCAT and SRE are the
engines behind NVDA, JAWS, MathJax and ChromeVox; VoiceOver is not covered, and a screen
reader's own settings change what is said. The chemistry flag is MathCAT's own decision and
says only that it treats the input as chemistry.

### Judge calibration (`eval calibrate`)

Agreement with Harish's labels, on figures the judge graded:

| Metric | Definition |
|---|---|
| `raw_agreement` | share of figures where judge and human give the same pass/fail |
| `kappa` | Cohen's kappa, (p_o - p_e) / (1 - p_e): agreement corrected for chance. Undefined (null) when both raters gave one and the same answer to everything |
| `kappa_ci95` | percentile bootstrap: resample figures with replacement 2000 times (seed 0 by default), recompute kappa, take the 2.5th and 97.5th percentiles. Resamples where kappa is undefined are skipped and counted |
| `confusion` | counts of judge pass/fail by human pass/fail |
| `disagreements` | ids where they differ, for reading |

Bias probes, run on gold figures with a reference alt (no human needed):

| Probe | Candidate shown to the judge | Should |
|---|---|---|
| `reference` | the OpenStax alt itself | pass |
| `verbose` | the reference padded and repeated (about 2x as long, no new facts), or a hand-written paraphrase from `--paraphrases` | pass, with the same score |
| `empty` | "" | fail |
| `irrelevant` | the alt text of the figure half the list away (a fixed unrelated sentence if there is only one figure) | fail |

The probes attach each figure's image, like the figures eval (`judge_mode: image`);
`--text-only` runs the older text-only judge, a different instrument (`--fake` runs skip
the image downloads). Probe results from one mode say nothing about the other.

Per probe: `pass_rate`, `as_expected_rate`, `mean_score`, `mean_chars`. **Length bias** is
paired: per figure, verbose score minus reference score, averaged (`mean_score_delta`),
plus the same for pass/fail (`pass_rate_delta`) and the mean length ratio. Figures where
either text was cut for the judge, or either call failed, are excluded (`excluded`). A
positive delta means the judge rewards length.

### Figure spot check (`eval figures-spotcheck`)

Offline, no model call, no spend. After a figures run with long descriptions on, the long judge
has graded each long description; this draws what a person reads (`eval/figures_spotcheck.py`):
**every figure the judge failed** (the description not accurate and complete and free of invented
values, or a data table with wrong values) **plus a seeded sample of its passes** (10 by default,
`--passes`), drawn by SHA-256 of `seed|id`. Figures the judge did not grade (no long description,
a failed judge call) are counted and left out.

```
uv run readablestem eval figures-spotcheck --items data/eval/judged.jsonl --sheet data/eval/spotcheck.csv
# Fill human_accurate, human_complete, human_invented_values (yes/no); human_table_values_ok if a table is shown.
uv run readablestem eval figures-spotcheck --key data/eval/spotcheck.key.json --labels data/eval/spotcheck.csv --out data/eval/spotcheck.json
```

The sheet shows the image URL, caption, long description and table in a seeded order that mixes
fails and passes, and no verdict, so it cannot anchor the label; the verdicts are in the key file
beside it. The report gives two counts, each with its n and a Wilson 95% interval: how many judge
fails the person also fails, and how many sampled passes the person would fail (errors the judge
let through). 0 misses in 10 passes still allows about 0.28, so this checks the judge, it does not
measure its miss rate; the 25-item kappa calibration in
[eval-figures-long.md](eval-figures-long.md) (section 3.6) is the measure. In the product, the review
screen's per-item "Report this item" link (`web/report_item.py`) already collects spot reports on real documents.

### Regression gate (`eval gate`)

Compares a current report with a baseline report of the same task.

| Tracked metric | Task | Fails when |
|---|---|---|
| `exact_lenient` | equations | drops by more than 0.02 |
| `mean_teds` | equations | drops by more than 0.02 |
| `cost_per_equation` (`cost.per_item_usd`) | equations | rises by more than 25% (a zero baseline only allows zero) |
| `heading_recall` | headings | drops by more than 0.03 |
| `heading_precision` | headings | drops by more than 0.03 |
| `figure_judge_pass_rate` | figures | drops by more than 0.05 |
| `document_detection_recall` | document | drops by more than 0.02 |
| `document_detection_precision` | document | drops by more than 0.03 |
| `document_matched_exact_lenient` | document | drops by more than 0.03 |
| `document_axe_violations` | document | rises at all (`max_increase`, limit 0) |
| `document_cost` (`cost.per_item_usd`) | document | rises by more than 25% |

Exit 0: all within tolerance. Exit 1: a tracked metric regressed, or is missing from the
current report. Exit 2: not comparable, so no verdict: different task, different gold
manifest, different item selection (e.g. another `--limit`), a partial run on either
side, or a different judge (prompt version or model; `judge_changed`). The judge check
exists because `figures-run2` against `figures-run1` used to pass: the judge had started
seeing the image, and the pass rate went from 0.46 to 0.92 with the same alt texts.
Changed pipeline version, step prompt versions, model ids or client are listed under the
table (they are usually why the gate is being run). With `--require-current` (CI uses
it) the gate also refuses (`stale_report`) a current report produced with other prompts
or models than the code under test, so a prompt change cannot pass on an old committed
`latest-<task>.json`. The pipeline version counts too (`pipeline X (code: Y)`): a report made
by another version than the code's is refused whatever its prompts and models were, and a
`--fake` report has no models to compare. Tolerances live in `data/eval/tolerances.json`; an entry with only
`name` and `limit` adjusts a default. The tables task also has absolute floors, checked
with or without a baseline (see "Tables": "Gate").

### Development and held-out, and the two bars (`eval/split.py`, `eval bar`)

A bar set on the pages a fix was built on says nothing about the next document, and a score
under settings production does not run is a score for another product. So, for every eval
added from November 2026 on:

1. The set is split into development and held-out by document, never by page:
   `split.split(seed, {document id: sha256 of the file})`. A document's side depends only on
   the seed and its id, so a new document moves no other.
2. Thresholds, prompts, worked examples and rules are tuned on the development side only.
   The held-out side is read only by the scorer, and `Split.verify` checks its hash (ids and
   file hashes) against the registered one before a run.
3. Each eval has two bars. The release bar is the first held-out measurement under the
   production profile (`ops/production.profile.toml`): a release may not be worse. The goal
   bar is the figure the fix is built toward.
4. A held-out set that was used to choose between designs is spent. Its documents are passed
   as `spent` and are development from then on; new ones are drawn.

A report says four things about its run: `split.name` and `split.set_sha256`
(`Split.stamp`), `profile.sha256` and `pipeline_version`.

```bash
uv run readablestem eval bar report.json --measure order.clean --at-least 0.9
uv run readablestem eval bar report.json --measure errors_per_100 --at-most 3 --kind goal
```

| Output starts with | Exit | When |
|---|---|---|
| `bar met` | 0 | held-out documents, production profile, the number meets the bar |
| `bar not met` | 1 | held-out documents, production profile, the number does not |
| `development score` | 2 | scored on development documents, or not under the production profile |
| `refused` | 2 | the report lacks one of the four, or has no number at `metrics.<measure>` |

The older reports (`eval pipeline`, the 15 registered sections of `gold/heldout.py`) record
their profile and pipeline version but no split, so `eval bar` refuses them: their bars are
the ones `eval gate` checks.

## Report format

`eval pipeline` writes one JSON report per task (default `data/eval/latest-<task>.json`):

| Field | Content |
|---|---|
| `schema_version`, `task`, `date` | `date` comes from `--date` (default: today, taken in the CLI) |
| `pipeline_version`, `prompt_versions` | `run.PIPELINE_VERSION`; prompt versions as seen on the model calls, module constants when no call ran. 0.3.0 (2026-10-08) changed the review rules (a sign or relation that differs from the text layer is a required decision): reports before 0.3.0 used the old review policy, so compare review-load numbers (required decisions, flags) only between reports of the same pipeline version. 0.4.0 (2026-10-09) added the content features without changing equations, figures or headings while the flags are off, so 0.4.0 and 0.3.0 reports compare. Layout detects tables whatever the flags say, and each data table kept as an image is a required decision of its own; on the document gold it claims no data table (its two ruled key-term grids are claimed as grids of terms and written as lists, `tables.is_term_grid`, with no decision). Chart data tables read from the image are optional extras, never a decision (since 2026-10-09; "Content features after integration"). Which version made each committed `data/eval/latest-*.json`: "Results" |
| `models`, `models_used`, `client` | configured main/cheap (and judge) ids, ids actually called, and the provider that answered: `openai`, `anthropic` or `fake` |
| `dataset` | `gold_dir`, `manifest_sha256`, `limit`, `unit`, `selection_sha256` (hash of the planned item ids) |
| `n_planned`, `n`, `complete`, `stopped_reason` | `stopped_reason` is a budget category (e.g. `budget_job_usd`) when spend ran out |
| `metrics` | per task, as above |
| `cost`, `latency_ms` | as above |
| `recording` | a real run only: `run` (`<set>/runs/<timestamp>`), `sha` (commit, `+dirty` when the tree had changes) and `files` kept there: "Runs keep their recordings" |

`--items PATH` also writes one JSON line per item (per page for headings and document),
including predicted LaTeX, MathML or alt text. These come from public OpenStax pages; the
harness never reads anything from `private-inputs/`, except the tables task's
real-document check, which reads `private-inputs/tables/` and reports counts only (see
"Tables").

## How to rerun

Without a key, a network or a browser: `make reproduce` checks the gold files against their
manifests, checks that each committed report was scored against exactly that gold data,
runs the scorer on known-correct LaTeX to show its ceiling, and prints the committed
numbers (`eval/reproduce.py`). `make reproduce-rerun` also re-runs the headings and tables
tasks, which call no model, and compares them with the committed reports. The model tasks
below cost money and differ a little on every run, so they are not part of either.

One-time setup:

```
uv sync --all-groups
uv run playwright install chromium
uv run readablestem gold fetch --book physics --pages 3 --chapter 2 --date 2026-10-06
```

`gold fetch` also fills `.cache/openstax/`, which holds the page XHTML the page PDFs are
printed from and the equation contexts are read from. After that, rendering is offline
except for figure images (downloaded once).

Real runs need `OPENAI_API_KEY` in `.env` (or `ANTHROPIC_API_KEY` with `--provider
anthropic`) and a spend cap. Each model call reserves its
worst case before it is sent (about $0.020 on gpt-6.1-sol, $0.001 on gpt-6-luna, from
`estimate_max_cost_usd`), so the cap must stay above actual spend by at least one
reservation. When a reservation is refused the run stops, keeps what finished, and marks
the report partial.

With `--provider anthropic` (key `ANTHROPIC_API_KEY`) the same run is made with Claude:
`--main-model` and `--cheap-model` choose the models (defaults `claude-opus-5-5` and
`claude-haiku-5-5`; for example `--cheap-model claude-sonnet-5-5`), the report's `client` is
`anthropic` and `models` holds the Claude ids, and `--max-usd` works the same. The second
automated check does not run (it is OpenAI's), so the verifier numbers of a Claude report are
empty. A reservation is larger because it covers thinking (8,192 tokens above the step's
output limit) and a large image: about $0.29 on claude-opus-5-5, $0.12 on claude-sonnet-5-5
and $0.006 on claude-haiku-5-5 for one image and 1,024 output tokens, so keep `--max-usd`
well above one main-model reservation (the actual cost of a call is far less; a real
equation crop costs a fraction of a cent on haiku). `eval promote` refuses a Claude report
unless `--out-dir` is given, so it cannot replace the OpenAI baselines the CI gate reads;
`eval gate` between a Claude and an OpenAI report prints the differences and notes that
`models` and `client` changed.

Who judges the figures is a choice of its own: `--judge-provider` and `--judge-model` on
`eval pipeline --task figures`, `eval sample`, `eval calibrate --probes` and
`eval invented judge`. The default is the run's provider: its main model on OpenAI, and
`claude-opus-5-5` on Anthropic whatever the run's main model is, so a run with Sonnet as
main model is still graded by the same judge as one with Opus. The judge has its own client
on the run's budget (one `--max-usd` covers both), the same prompt on both providers, and on
Claude no server-side refusal fallback. The report records it: `models.judge` and
`judge_client` (provider and model); the invented-content judge result has `judge_provider`
and `judge_model`, the probe report `judge`. Figure pass rates graded by an OpenAI judge and
by a Claude judge are two instruments and do not compare (the gate refuses, `judge_changed`).
The chatbot arms of the registered comparison stay on OpenAI.

```
uv run readablestem eval pipeline --task equations --provider anthropic --limit 10 --max-usd 1.00 --out .local/eval/equations-claude.json
```

```
uv run readablestem eval pipeline --task equations --max-usd 0.50 --items data/eval/items-equations.jsonl
uv run readablestem eval pipeline --task headings  --max-usd 0.01
uv run readablestem eval pipeline --task figures   --max-usd 0.50 --items data/eval/judged.jsonl
uv run readablestem eval pipeline --task document  --max-usd 2.00 --items data/eval/items-document.jsonl
```

The figures task with long descriptions on (about $0.13 for the 13 gold figures):

```
FEATURE_LONG_DESCRIPTIONS=true uv run readablestem eval pipeline --task figures --max-usd 1.00 --out .local/eval/figures-long.json --items .local/eval/figures-long.jsonl
```

`--limit N` takes the first N equations or figures, or the first N pages. `--fake` swaps
in the offline `FakeVisionClient` with no recorded answers: every call is refused at zero
real cost, so a fake run checks the plumbing and nothing else (all scores are 0).
`--fake --max-usd X` caps the fake's simulated cost, which exercises the budget stop.

`--sections BOOK/PAGE ...` keeps only the equations, figures or pages whose "book/page" id
contains one of the strings (`--sections latex-matrices 5-2-solve`): a slice of a set for a run
that has to stay small, applied before `--every` and `--limit`; the report's `dataset` records
it. `FEATURE_PAGE_MATH=true` runs the document task with the page math pass (ADR 0006); its
cost is `cost.page_math_usd` and the pass's counts are in each item's `page_math`.
The real-world set (arXiv papers compiled by their authors, gold from the authors' own LaTeX, a registered
held-out split; `scripts/realworld_*.py`): [eval-realworld.md](eval-realworld.md).
Results on the development sets and the scripts that compare runs (`scripts/dev_buckets.py`,
`scripts/dev_spans.py`): [eval-page-math.md](eval-page-math.md). The second secondary equation
score (`secondary_symbols`) and what the secondary scores say about the held-out run:
[eval-secondary-metrics.md](eval-secondary-metrics.md).

`--scanned` (document task, development sets) turns every printed page into the image-only PDF
of a degraded scan before it is converted, adds a prose word error rate and, with
`FEATURE_IMAGE_PAGES=true`, the image page numbers; `--scan-seed N` picks the degradation. See
[eval-image-pages.md](eval-image-pages.md).

`--section-workers N` (document task, default 1) converts N sections at once. It shortens a
live run, which waits on a hosted model, and gives the same items in the same order (a
budget stop still ends the run at the first section it stopped, in section order, although
the sections after it may already have been converted and paid for). A section's
`latency_ms` then includes its wait for the others, so a run whose latencies are reported
keeps the default.

Pre-render or inspect inputs:

```
uv run readablestem eval render --equations
uv run readablestem eval render --page 2-2-speed-and-velocity --out /tmp/page.pdf
```

Judge calibration:

```
uv run readablestem eval calibrate --judge data/eval/judged.jsonl --write-template data/eval/labels.csv
# Label in a spreadsheet: human_pass = yes/no, notes optional. Do not look at the judge's verdict.
uv run readablestem eval calibrate --judge data/eval/judged.jsonl --labels data/eval/labels.csv --out data/eval/calibration.json
uv run readablestem eval calibrate --probes --max-usd 0.50 --out data/eval/probes.json
```

Baselines and the gate: after reviewing a full run, promote it and commit the file
(`promote` refuses partial and fake runs, and for `latest` a report made with other
prompts or models than the code):

```
uv run readablestem eval promote .local/eval/equations-run3.json --as baseline
uv run readablestem eval promote .local/eval/equations-run3.json --as latest
uv run readablestem eval gate --require-current --current data/eval/latest-equations.json --baseline data/eval/baseline-equations.json
```

After a prompt or model change, re-run the task, promote the new report `--as latest`
and commit it with the change; CI's gate fails (exit 2) until you do.

Baselines (2026-10-14, pipeline 0.5.0). `data/eval/baseline-<task>.json` and
`latest-<task>.json` were the same run, one per task (since then `latest-equations`,
`latest-document` and `latest-tables-local` were re-run on 2026-10-21, also 0.5.0; "Versions
of the committed reports"), made once on Claude
(`MODEL_PROVIDER=anthropic`, `claude-opus-5-5` main, `claude-haiku-5-5` cheap, Opus as the
figure judge) on the original development set (`data/gold`), and promoted with
`eval promote ... --out-dir data/eval`. CI's eval-gate job therefore runs; it compares each
`latest` with its `baseline`, so today it passes trivially, and from now on it catches a
change to prompts, models or code that lowers a tracked metric. The gate reads only the
original set; dev-v2 reports go to `.local/eval/dev-v2/` and are not gated. Each run was made
once, live, with every model answer kept in `.local/answers-baseline.jsonl` (git-ignored), so a
replay with `--answers-offline` is free (the second check's answers excepted, see below).

| Task | Run | Cost of the run |
|---|---|---|
| equations (30) | exact_lenient 0.767, mean TEDS 0.867, proposals 30 of 30 | $0.098 ($0.0033 per equation, of which the Claude check $0.0010) |
| figures (13) | judge pass 1.000, mean judge score 4.77 | $0.123 (figure step $0.050, Claude check $0.022, judge $0.052) |
| document (43 PDF pages, 10 sections) | detection recall 1.000, precision 0.750, matched exact_lenient 0.767, axe violations 0 | $0.235 ($0.0055 per PDF page, check $0.0017) |
| headings | precision 0.877, recall 1.000, F1 0.934, level accuracy 0.992 (replaced 2026-10-14: F1 0.821, see "Doubled box headings") | no model |
| tables (local) | 184 tables, flag recall 1.000, flag precision 0.223 | no model |

Spend: $0.456 of the $6 cap. The pipeline's own model calls cost $0.069 (equations), $0.050
(figures) and $0.161 (document); the rest is the Claude check ($0.029, $0.022, $0.074) and the
figure judge ($0.052). The runs they replace were made with
`gpt-6.1-sol` and `gpt-6-luna` on pipeline 0.4.0 (2026-10-12; equations 0.767 / TEDS 0.864,
document precision 0.545 and matched 0.733, headings and figures the same as now); they are in
git history before the commit that added the baselines, and the sections "Results" below
describe them, not the committed files. The OpenAI path has no committed baseline; run
`eval pipeline` with `--provider openai` and `promote --out-dir` elsewhere to compare.

Recorded answers, replays and batches (`--answers`, `--batch`; development sets only). A live
run is paid for once. `--answers FILE` keeps every model answer in a JSON-lines file keyed by
the question as it is sent (`models/question.py`): step, model, prompt version, the
instructions as sent, the images by what they show, the nearby text as sent (so the fence
around it and the number of characters kept are part of it), the token limit, the image
detail, the thinking effort the client will send, whether a refusal may go to the fallback
model, and the format the answer is read in. Nothing about the release is in the key: a new
pipeline version that sends the same question finds the same answer, and an edited prompt,
answer format or context limit asks again. A request already in the file is
answered from it, with the recorded tokens and cost, at no charge; any other goes to the model
and is appended. So one baseline recording serves every experiment that asks the same
thing, and an experiment pays only for requests that differ from the baseline's.
`--answers-offline` turns a miss into a refusal instead of a call (a replay that cannot spend),
and `--every N` takes every Nth equation or figure instead of the first few. `--answers` is
refused for the held-out set and the sealed check part.
Each row also holds the parts of its question (`"q"`), so a miss is reported with its reason:
the report's `answers_file.answers_file_missed_because` counts misses by what differs from the nearest
recorded question ("prompt equation-5 recorded, equation-6 asked", "nearby text changed",
"no answer recorded for this picture"). A file recorded before 2026-11-11 is keyed by an
older list (no answer format, no context limit). `--answers-rekey` moves it: a request found
only under the older key is answered from that row, and the row is appended under the new key
with its parts (`answers_file.answers_file_rekeyed` counts them). Run it once, with the code and settings
the file was recorded with, then drop the flag; a request that changed since the recording is
a miss either way.
The Claude second check (off by default since WS10.5; `CLAUDE_SECOND_CHECK=shadow` turns it on) asks the model as well; from 2026-10-14 its
answers are kept in the same file (the report's `answers_file.second_check`), so a replay and
`--answers-offline` spend nothing on it. The baselines of "Baselines and the gate" were
recorded before that, so their check answers are not in `.local/answers-baseline.jsonl`: the
first replay with the check on pays for them once (about $0.13). A `--batch` run leaves the
check live.

```
uv run readablestem eval pipeline --task equations --provider anthropic --max-usd 2 --answers .local/answers.jsonl
uv run readablestem eval pipeline --task equations --provider anthropic --answers .local/answers.jsonl --answers-offline
```

`--batch` (with `--answers` and `--provider anthropic`) sends what the file cannot answer to
the Message Batches API, which bills half the price and answers in minutes to an hour. A run
is made in stages (`eval/staged.py`, `models/anthropic_batch.py`): a pass that only collects
the requests it has no answer for, one batch, the same pass again (now reaching the
escalations and the judge calls the first answers led to), until a pass asks for nothing new;
that last pass is the run. The requests are the ones a live call sends (`batch_params`),
less the server-side refusal fallback, which the Batches API does not take; an answer is
recorded at list price, so runs made either way compare, and the cap is charged what the batch
billed. A batch's prompt-cache hits are best effort: cache numbers come from live runs. The
chatbot arms and the figure judge of a proof take `--batch` too
(`proof --add-systems chatbot --provider anthropic --batch`, `proof/chatbot_batch.py`): the
same message per section, `billed_usd` beside `cost_usd` (list price) in each record.

Weekly sample (drift check between full runs): a 5% share of the gold units, seeded by
the ISO week, scored like any other run (`eval/sample.py`). Reports go to
`.local/eval/weekly/<week>-<task>.json` (git-ignored):

```
uv run readablestem eval sample --task equations --max-usd 0.10
uv run readablestem eval sample --task figures --fraction 0.05 --week 2026-W41 --max-usd 0.10
```

The scheduled run of `eval-live.yml` (below) does not use this sample. Whether opted-in
production pages should join a sample once their review labels exist as gold is not decided.

Public demo:

A provisional demo is committed: OpenStax *Physics* 2.3 "Position vs. Time Graphs" (13 PDF
pages, 27 model calls, $0.055, built 2026-10-09 with the real models on pipeline 0.4.0
with every content flag off, as in production; axe-core clean at 1100 and 320 CSS px, no
horizontal scrolling at 320). It shows the pipeline's proposals as they come, including
ones a reviewer would fix: equation 2.5 has a plain v where the book has a bold vector,
which the review screen asks about (one of its 4 required decisions; the other 3 are
repeated headings). The 0.3.0 build made the same 27 calls with the same input tokens for
$0.026: the provider's prompt cache answered 38,356 of its 38,437 input tokens, against
20,826 this time (same prompts and models). The page is 668 KB with the
smaller figure images (docs/output.md, MF23); the 0.3.0 build was 2,211 KB.
TODO(Harish): keep this page or choose another, then rebuild with the command below. A
`--fake` build is marked as a placeholder and `ops launch-check` flags it.

```
uv run readablestem demo build --page 2-2-speed-and-velocity --max-usd 1.00
```

It prints the page to PDF, converts it, and writes `index.html` (the converted page with a
short note saying it is a precomputed demo of an OpenStax CC BY page, with attribution
and a link to the original), `report.json` (the `DocumentResult` without the HTML) and
`source.pdf` (the exact input) to `src/readablestem/web/demo/`. Pages outside the
gold set are looked up live and pass the same license gate as `gold fetch`.

### Runs keep their recordings

A run that calls a real model leaves its recording behind, so its numbers can be scored again and
its classes replayed without paying twice (the 2026-10-21 chemistry held-out run did not, and its
answers could not be recovered). `eval pipeline` (every task and set), `eval sample` and
`eval calibrate --probes` write

```
~/.cache/readablestem/<set>/runs/<UTC timestamp>/
    answers.jsonl   every model answer (replay with --answers, --answers-offline)
    items.jsonl     the per-item rows
    report.json     a copy of the report
    run.json        the commit the code was at, and whether the tree had changes
```

`<set>` is the set's cache folder: `heldout`, `dev-v2`, `gold` (the repository's `data/gold`, for
`sample` and `calibrate` too), or the folder given to `--heldout-dir` (for example
`chem-heldout`, so its runs are in `~/.cache/readablestem/chem-heldout/runs/`). The report names
the folder and the commit under `recording` (`run`: `<set>/runs/<timestamp>`, `sha`, `files`),
never a home path; the command prints the full path (`kept: ...`). `$XDG_CACHE_HOME` replaces
`~/.cache` as everywhere else.

`--answers FILE` and `--items FILE` still choose other files and are not copied. Nothing is kept
for a `--fake` run, a replay (`--answers-offline`), the tables task in local mode (no model), or
the sealed dev-v2 check part (its items are not listed, by design). A folder is made fresh for
each run, so a held-out run never replays an earlier one. `scripts/dev_chatbot.py score` always
writes its items (default `items-<arm>.jsonl` in `--out-dir`). The scripts that already keep an
answers file and items per run (`scripts/realworld_run.py`, `handwriting_eval.py`,
`typed_scans_check.py`, `dev_spans.py`) are unchanged.

Read a kept run with the chemistry classes script, which accepts the run folder:

```
uv run python scripts/dev_chem_classes.py --gold data/dev-v2/dev --speech \
  run=~/.cache/readablestem/dev-v2/runs/20261103T120000Z
```

`--speech` adds what a listener hears (`pipeline/speech.py`): whether each reading is spoken as the
gold is, the spoken defects it adds, and the silent harm per 100 gold. Without it the script prints
the outcome classes and how many of them harm a listener. The script lists equations one by one,
so it is for development sets only: it refuses a run folder of a held-out or sealed set (the
`set` in its `run.json`, or a held-out/sealed word in the path) and a held-out gold folder. A
held-out run's report holds counts only (the chemistry block names no item ids); read its
numbers there.

**Privacy and pruning.** A recording holds the text layers of the evaluated PDFs and the model's
answers: `runs/` and each run folder are mode 0700 and every file in them 0600. Delete old runs
with

```
uv run readablestem eval prune --older-than 90 --dry-run
uv run readablestem eval prune --older-than 90 [--set dev-v2 ...]
```

which removes the runs of `~/.cache/readablestem/<set>/runs/` older than that many days (by the
timestamp in the folder name; any other folder is left alone) and keeps every run a committed
report names under `recording.run` (it reads the tracked `*.json` files of the repository).
`--dry-run` lists what would go. The chemistry block's `chem_right_excl_times` and `silent_harm`
are report fields to read; no gate, floor or tolerance uses them.

### Weekly live run (`eval-live.yml`)

The CI `eval-gate` job only compares the committed `latest-<task>.json` with its baseline, so
on its own it cannot fail on a prompt or model change: nothing re-measures. `eval-live.yml`
does. Every Monday at 06:17 UTC (and on demand, "Run workflow", with a choice of one task or
all) it runs the full development sets against the production provider (Claude,
`MODEL_PROVIDER=anthropic` in `.railway/railway.ts`), one job per task, then
`readablestem eval gate --require-current` against the committed baseline. A regression, a
changed judge, a stale prompt or model, or a partial run (the cap stopped it) fails the job
and GitHub emails the owner. The reports are uploaded as artifacts (30 days) and never
committed; to move a baseline, re-run locally and `eval promote` it.

| Task | Run | Hard `--max-usd` | Baseline run cost (2026-10) |
|---|---|---|---|
| equations | 30 equations | $0.50 | $0.08 to $0.10 |
| headings | 3 pages, no model call | $0.25 | $0.00 |
| figures | 13 figures, Opus judge | $0.75 | $0.12 |
| document | 3 sections, 43 PDF pages | $1.00 | $0.24 |
| tables | `--mode model --styles grid` (no model-mode baseline: gated on its floors alone) | $0.45 | about $0.26 (see "Tables") |

The caps add up to $2.95, under the $3.00 weekly ceiling the workflow checks before it runs
anything; the expected spend is about $0.70 a week (the right column, same sets). Every cap
is above one worst-case reservation of the main model (about $0.29), which is why the caps
are not lower.

The workflow needs the repository secret `ANTHROPIC_API_KEY`. Without it a scheduled run
ends with a notice ("the weekly live eval is skipped") and spends nothing; a manual run fails
with an error, so a missing key is never silent. Adding the secret is what turns the weekly
spend on. The full development sets are used so that a report has the same items as its
baseline (the gate refuses reports of other selections); Chromium and the fonts are
installed as in `ci.yml`, and the OpenStax pages are fetched on the run.

### Eval guard (`eval-guard.yml`)

A pull request that changes a setting in `src/readablestem/config.py` that decides what a
model answers (the model ids such as `ANTHROPIC_MAIN_MODEL` or `claude_check_model`, the
`*_effort*` settings, `*max_output_tokens`, thinking and reasoning settings; an explicit list
in `scripts/eval_guard.py`, compared by parsed value, so reformatting is not a change;
timeouts, retries, concurrency and call limits are not on it) or a prompt (a `.md` file under
`src/readablestem/models/prompts/`, subfolders included; a file moved with its content
unchanged is not a change) fails unless it also changes the matching
`data/eval/latest-*.json` report(s). The message points to "How to rerun". The mapping is in
`scripts/eval_guard.py` (tests: `tests/test_eval_guard.py`): a prompt needs the reports of
the tasks that use it (`equation*.md`: equations and document; `figure*.md`: figures and
document; `judge_*`: figures; `check_equation` and `check_figure`: that task; `table.md`:
tables local and document; `image_page*.md`, `IMAGE_PAGES_MODEL` and
`ANTHROPIC_EFFORT_IMAGE_PAGES`: `latest-image-pages.json`, a run of the scanned and handwritten
pages pass (the document task reads typed PDFs and never calls that pass; no such report is committed
yet, the first prompt change adds it, and the practice-set run in docs/eval-handwriting.md, "Doubtful
symbols: tried, not shipped", is the model for it); `page_audit.md`, `PAGE_AUDIT_MODEL` and
`ANTHROPIC_EFFORT_PAGE_AUDIT`: `latest-page-audit.json`, the audit's own run (`eval page-audit`,
below: it runs in shadow and changes no output, so the document task does not measure it; no such
report is committed yet); any other prompt: document); a change to the Claude model pair
needs all four Claude reports (equations, headings, figures, document); the OpenAI pair
needs the tables report, which records it; any other model setting needs the document
report.

**What decides acceptance.** A change to a rule that decides whether a reading is accepted or
goes to a person needs the replay record, `data/eval/burden-corpus.json` ("The replay
record"), re-made in the same pull request. The rule is small and has no line numbers. A
module under `src/readablestem/pipeline/` with a function that sets `Status.ACCEPTED` or
`Status.NEEDS_REVIEW` decides acceptance: its module-level `UPPER_CASE` constants and those
deciding functions are the rules, and so is a `config.py` setting with `accept` in its name.
They are compared by parsed value (`ast`; a function without its docstring), so a comment, a
docstring or `0.80` written `0.8` is no change, and a constant in a module that decides
nothing (`layout.py`) is not a rule. A module is found by what it does, so a new step that
sets a status is covered without a list to update.

Opt-out, for a change that does not alter what a model is asked, which model answers or what
is accepted (a renamed constant, a comment): add the label `eval-unchanged` to the pull request, or put a
trailer `Eval-Unchanged: <reason>` in the last paragraph of any of its commit messages (the reason is required). The guard
prints the waiver and the reports it did not need, so a reviewer sees it. Prompt text a
model reads is never a refactor: change it and re-run.

## Results (pipeline 0.4.0, 2026-10-12, gpt-6.1-sol and gpt-6-luna)

Historical: these are the runs of 2026-10-12 on pipeline 0.4.0 with the OpenAI models. They
were committed as `data/eval/latest-<task>.json` then and were replaced on 2026-10-14 and
2026-10-21 by the pipeline 0.5.0 runs on Claude ("Versions of the committed reports"); the
numbers below are not those of the files now committed. Sample: 3 sections of OpenStax
*Physics* ch. 2 (CC BY 4.0). Small: read the confidence intervals before reading the point
estimates.

Pipeline 0.4.0 (the content features, every flag off, as in production): at the time all the
committed reports were 0.4.0 runs. Equations, figures and document were re-run with the
real models on 2026-10-12 (equations $0.052, figures $0.090 with the judge, document
$0.145) and promoted with `eval promote --as latest`; tables is the local run of the
same day (no model, `latest-tables-local.json`); headings is the 0.4.0 re-run of
2026-10-09 (no model calls, the same numbers as 0.3.0 to the last digit). Against the
0.3.0 reports they replace, `eval gate --require-current` passes on each: equations
exact_lenient 0.733 to 0.767 (23 of 30; inside each other's intervals, the models are
not deterministic), figures judge pass 0.923 to 1.000 (13 of 13), and the document task
the same to the last digit on equations (recall 1.000, precision 0.545, matched
exact_lenient 0.733) and in review load (10 required decisions, 0.233 per PDF page; 6 of
the 10 point at a real error). Of the 7 equation misses, 5 are the known scorer gaps
(2.2 eq-003, eq-004, eq-010; 2.3 eq-004, eq-007), one is 2.2 eq-007's bold subscript
"avg" (cosmetic by the policy: an optional note) and one 2.3 eq-005's comma after the
second row; no meaning-level miss was accepted. The layout changes made after these
runs (the silent-loss sweep, 2026-10-12, docs/layout.md "The safety net") leave the
document task as it was: run offline (`--fake`) before and after them, its per-page
rows are identical apart from timing, so the runs were not repeated.

The same document task with every content flag on (`FEATURE_TABLES`,
`FEATURE_LONG_DESCRIPTIONS`, `FEATURE_COLOUR`, `FEATURE_NOTATION_MEMORY`) is kept apart
as `data/eval/document-flags-on.json` ($0.144; it cannot be `latest`: the long-description
figure prompt is not the production one): 9 required decisions (0.209 per PDF page, the 9
heading groups; the equation the flags-off run asked about was read right this time),
27 optional notes (0.628 per PDF page: the 5 chart data tables read from the image are
optional extras), matched exact_lenient 0.767, 64 predicted equations (bold letters in
prose made math), 0 static and 0 axe violations. These replace the estimate in "What
this gives for the targets" below: flags off 0.233 (target 0.25), flags on 0.209 (target
0.28).

| Task | n | Headline | Cost |
|---|---|---|---|
| equations | 30 | exact_lenient 0.767 (95% CI 0.591-0.882; ceiling 0.833 with known-correct LaTeX), mean TEDS 0.864 | $0.0017 per equation |
| headings | 121 | recall 1.000 (CI 0.969-1.000), level accuracy 0.992, precision 0.877 (every extra is NEEDS_REVIEW) | no model calls |
| figures | 13 | image-aware judge pass 1.000 (CI 0.772-1.000), factual errors 0 (judge not yet calibrated) | $0.0039 per figure |
| document | 3 sections, 43 PDF pages | equation recall 1.000, precision 0.545 (0.968 excluding prose math), matched exact_lenient 0.733, 0 split equations, 0 axe and 0 static violations; 0.233 required decisions per PDF page | $0.0034 per PDF page |
| tables (local) | 197 gold data tables | recall 0.909, precision 0.984, TEDS 0.866 (0.954 on found), header exact 0.812 | no model calls |

The 0.3.0 runs of 2026-10-09 for comparison: equations exact_lenient 0.733 (CI
0.556-0.858), mean TEDS 0.861; figures judge pass 0.923 (CI 0.667-0.986); document as
above at $0.0033 per PDF page.

How the equations number got there (error analysis on real runs, same 30 equations):

| Run | Change | exact_lenient | Real misses auto-accepted |
|---|---|---|---|
| 1 | first run | 0.367 | (inputs wrong, see run 2) |
| 2 | eval harness fix: Chromium ignores `mathvariant`, so bold vectors were rendered plain; now drawn as Unicode math bold | 0.600 | 7 |
| 3 | text-layer style check: a bold letter in the PDF read as plain (or the reverse) lowers confidence, escalating cheap-model answers and keeping the fix in review | 0.767 | 1 |

The model's self-reported confidence was uninformative (every proposal >= 0.88); the
deterministic check against the PDF's own text layer is what separates right from wrong.
The figures judge went from pass 0.46 to 0.92 when it was given the image: the text-only
judge counted correct details missing from the reference as errors, and in one case the
reference itself was wrong (it says five miles; the caption says 10 km round trip).

## Review triage (2026-10-08, "meaning-level only")

Before: `feat/beta-app` at 4d574fb (every NEEDS_REVIEW, FAILED and ABSTAINED fix needed a
decision). After: `feat/review-triage`. Same day, same prompts (`equation-df0e12f1`,
`figure-ac51f42d`) and models; headings with the fake client (no model calls). The before
numbers apply the same metric code to the before run's own fixes (stored without tiers,
so their tier is the one their status implied). Reports: `/tmp/triage-<task>.json`
(after), `/tmp/triage-before/` (before); not committed.

| Measure | Before | After |
|---|---|---|
| equations: required decisions (30 equations, 3 sections) | 2 (0.67 per section) | 1 (0.33 per section) |
| equations: optional notes | 0 | 1 |
| equations: flag precision / recall | 0.50 (1/2) / 0.14 (1/7) | 1.00 (1/1) / 0.13 (1/8) |
| ... excluding the 5 scorer gaps | 0.50 (1/2) / 0.50 (1/2) | 1.00 (1/1) / 0.33 (1/3) |
| equations: exact_lenient | 0.767 | 0.733 |
| headings: required decisions (3 sections, 43 PDF pages) | 61 (1.42 per page) | 9 (0.21 per page) |
| headings: optional notes | 0 | 20 (0.47 per page) |
| headings: flag precision / recall | 0.30 (18/61) / 1.00 (18/18) | 0.56 (5/9) / 0.78 (14/18) |
| document: required decisions (43 PDF pages) | 65 (1.51 per page) | 10 (0.23 per page) |
| document: optional notes | 0 | 23 (0.53 per page) |
| document: flag precision / recall, all | 0.32 (20/63) / 0.77 (20/26) | 0.60 (6/10) / 0.60 (15/25) |
| ... excluding scorer gaps | 0.32 (20/63) / 0.95 (20/21) | 0.60 (6/10) / 0.75 (15/20) |
| document: equations only, excluding scorer gaps | 1.00 (2/2) / 0.67 (2/3) | 1.00 (1/1) / 0.50 (1/2) |
| convert, OpenStax 2.1 (16 pages) | 25 | 3 (heading groups: Teacher Support x11, Procedure x2, Distance vs. Displacement x2); 10 optional |
| convert, LaTeX two-column handout (1 page) | 2 | 2 (bold omega read as plain, twice); 3 optional (double-struck R) |

The model answers differ from run to run (the after equations run dropped the bold of
`Δ𝐝` in 2.1 eq-000 even after escalation, and the after convert read the handout's bold
omega as plain twice). Applied to the *same* before answers, the new style rule keeps
exactly the meaning-level flags (2.3 eq-002 `\mathbf{d} = vt + ...`, a bold v read as
plain; the handout's bold omega) and turns the cosmetic ones into notes (`d_0` and `d_f`
with bold subscripts, 2.2 eq-007's bold "avg" subscript, `\mathbb{R}^n`).

Every wrong item that is not required after the change, and why:

- equations: the 5 scorer gaps (2.2 eq-003, eq-004, eq-010; 2.3 eq-004, eq-007); 2.2
  eq-007, whose only difference from the gold is the bold subscript "avg" (cosmetic by
  the policy, an optional note); 2.3 eq-005 (equations task only), a comma after the
  second row instead of after the array (punctuation, not flagged before either).
- headings: "Solution" and "Discussion" on 2.1 and "Procedure" on 2.3, run-in labels used
  once on their page (optional notes: no reliable run-in rule, docs/layout.md "Run-in
  labels"), and "Displacement Problems" on 2.1, a gold h5 written two levels too high
  (a level is an optional note).

Targets: required decisions per page 0.21-0.23 on the gold sections, 0.19 on OpenStax 2.1,
2 on the one-page handout (both real errors), 0.29 over the two documents (target about 1).
Flag precision 0.56-0.60 on the gold sections, 0.60 on the two documents (3 useful of 5:
the Procedure group and the two omegas), up from 0.30-0.32 and 0.19 (target 0.5). Flag
recall of meaning-level errors: no real error *in these eval items* newly hidden; recall
over all wrong items drops (document 0.95 -> 0.75 excluding scorer gaps) because single
run-in labels, a heading level and a bold subscript are now optional notes, as the
policy decides.

### After the review of the triage (same day)

A review found meaning-level errors outside the eval items that the first version let
through, reproduced with real layout on a Chromium-printed PDF and FakeVisionClient
answers at confidence 0.95: one wrong digit (9.80 read as 9.30), `ω` read as `w`, the
`t` dropped from `e^{-λt}` and `ℝ` read as `ℕ` were accepted with nothing to review (the
symbol check only fired below a Dice similarity of 0.5); a bold `𝐤·𝐫` in an exponent
read plain, and an encoded `ℝ` read as a plain `R`, were optional notes. Fixed
(docs/steps.md "Text-layer cross-check" and "Letter-style check"): any difference in
letters, digits and Greek letters is required (`symbol_mismatch`); a bold letter in the
script of a plain base is a variable; an encoded double-struck, script or fraktur letter
read plain is meaning-level; and every failed text-layer check on a cheap-model answer,
a cosmetic one too, escalates to the main model. Still cosmetic, as the policy says: a
bold digit, also the lone zero vector `𝟎` read as `0` (it escalates, but if the main
model also reads `0` it is an optional note).

Applied to the 181 recorded real answers above (equations task, document task, both
converts), the new rules change no tier. Re-run with real models (commit 6508901, same
prompts and models; reports in `/tmp/triage-review/`, not committed; $0.30 spent,
including a document run stopped by its own `--max-usd` reservation cap and re-run at
`--concurrency 1`):

| Measure | First triage | After the review |
|---|---|---|
| equations: required / optional | 1 / 1 | 0 / 1 |
| equations: exact_lenient | 0.733 | 0.767 |
| equations: escalated route / cost | 5 / $0.048 | 6 / $0.053 |
| document: required decisions (43 PDF pages) / optional | 10 (0.23 per page) / 23 | 9 (0.21 per page) / 23 |
| document: flag precision / recall, excluding scorer gaps | 0.60 (6/10) / 0.75 (15/20) | 0.56 (5/9) / 0.70 (14/20) |
| convert, OpenStax 2.1 (16 pages) | 3 decisions, 10 optional, 43 calls, $0.028 | 3 decisions, 10 optional, 42 calls, $0.028 |
| convert, LaTeX handout (1 page) | 2 decisions, 3 optional, 32 calls, $0.019 | 1 decision, 3 optional, 34 calls, $0.020 |

The differences are model variance, not the rules: the first runs' extra required
equations were a bold `Δ𝐝` the main model read plain (2.1 eq-000, equations task), a 2.3
eq-005 answer at confidence 0.84 (document task) and the second bold omega of the
handout; this time the models read them right or surer. Every escalation in the
equations task was a cheap answer that dropped a bold `𝐝` (meaning-level, escalated
before as well); the sixth is one more of those. The headings are unchanged (no model
calls). The cosmetic notes (OpenStax `𝐝𝟎` and `𝐝𝐟`, the handout's three MSBM
`\mathbb{R}`) now go to the main model as well, which wrote the same thing, so they stay
optional notes (5 more main-model calls over the two documents, a few tenths of a cent;
the call totals also move with the other escalations). The bold omega that remains is
still required. No `symbol_mismatch` fired in any run.

### Sign and relation check (pipeline 0.3.0, same day)

A review found that a dropped or flipped sign was still accepted silently
(`e^{-λt}` read as `e^{λt}`, `a = -9.8` read as `a = 9.8`): the symbol check sees
letters, digits and Greek letters only. Harish's decision: sign and operator differences
are meaning-level, so required (`equation.text_layer_signs`, code `sign_mismatch`;
docs/steps.md "Sign and relation check"). The review rules changed, so the pipeline
version went to 0.3.0; every report above is a 0.2.0 report.

Offline replay (no model calls) of the new check on every recorded real model answer
that has a text layer:

| Source | Answers | Check ran | `sign_mismatch` |
|---|---|---|---|
| equations task: runs 1-3 and the final run (main checkout `.local/eval/`; text layers from the eval's Chromium cache) | 120 | 88 | 0 |
| equations task: triage before, after, review (`/tmp/triage-*/`) | 90 | 66 | 0 |
| document task: triage before, after, review and the partial review run (`Element.text` as captured; layout re-run today on the three page PDFs gives the same text for all 201 answers that can be matched to a section; the other 14, the partial run's last section, have no section id) | 215 | 116 | 0 |
| convert, OpenStax 2.1 and the LaTeX handout (three triage runs and the stored design copies) | 192 | 80 | 0 |
| the committed demo (OpenStax 2.3) | 19 | 14 | 0 |
| **all** | **636 (134 distinct text layer and MathML pairs)** | **364 (88 distinct)** | **0** |

So it adds no required decision on real outputs (the 46 distinct pairs where it did not
run have no sign or relation in their text layer: lone variables such as `𝐝0`). The
recorded answers hold no real sign error either, so recall comes from synthetic errors
put into the same 88 real answers and checked against their real text layers: a
dropped minus 45/45, minus to plus 45/45, `=` to `<`, `≤` or `≈` 146/146 each, `+` to
`±` 9/9, a dropped `+` 9/9. `×` to `·` was flagged 2 of 13 times: the two vector
products (the handout's `𝛚 × 𝐫`), never scientific notation (`1.1 × 10²`), as the policy
says.

A review of the check printed its own equations with Tectonic in 8 math font setups,
Chromium, KaTeX and LibreOffice (docs/steps.md "Review of the check"): on its 429 right
answers the check flagged 57 before the fixes and 6 after, and caught 51 of 52 wrong
answers (43 before); a dot product of arrow vectors read as a cross product is caught
in 111 of 180 swaps (0 before). The replay above, re-run after the fixes, is unchanged:
636 answers, the check ran on 364, 0 `sign_mismatch`, so no new required decision on
real answers. The same synthetic errors on the 88 real text layers are caught as
before (`×` to `·` still 2/13: the one real arrow vector, `→𝑠`, is in no product).

The same review found a false `symbol_mismatch` the eval items could not show: pdfTeX
writes `\Delta` as U+2206 INCREMENT, which is not a letter, so every correct `\Delta`
from a LaTeX PDF needed a decision. It now counts as `Δ` (no recorded answer changes:
none of them has a TeX `\Delta`).

### Re-run under 0.3.0 (2026-10-09)

The four tasks were re-run on the merged code (sign check, second automated check and
Split View) and committed as `data/eval/latest-*.json`; $0.28 in all. Headings are
unchanged (no model calls). Equations: exact_lenient 0.733 (0.767 on 2026-10-08; the
same 30 items, inside each other's CI: the models are not deterministic). Of the 8
misses, 5 are the known scorer gaps, one is a bold subscript and one a misplaced comma
(both small, accepted with no note), and one, 2.3 eq-003, was kept for a decision: no
meaning-level miss was accepted. Document: required decisions 0.233 per PDF page (10),
optional notes 0.535; figures: judge pass 0.923, one second-check note. The demo was
rebuilt on the same code (27 model calls, $0.026).

## Second automated check (2026-10-08, ADR 0005)

Branch `feat/decisions-verifier`, same prompts and models as above, the check on (its
defaults). Reports: `/tmp/dv-figures.json`, `/tmp/dv-equations.json`,
`/tmp/dv-document.json` (and `-items.jsonl`); not committed. Spent $0.25 in all
(figures $0.089 with the judge, equations $0.050, document $0.114, canary and live test
$0.002).

| Measure | Result |
|---|---|
| figures: answered / flagged | 13 of 13 accepted figures / 1 (flag rate 0.077): figure 2.9, the dense vector diagram the research also flagged; the judge passed it (score 4) |
| figures: agreement with the judge | 11 of 13 (kappa -0.08: the one flag and the judge's one failure are different figures); the judge's failure, 2.13 (score 3), had the second-lowest score, 0.55, just above the 0.5 threshold; AUROC of the score against judge failures 0.92 |
| figures: cost, latency | $0.00008 per figure (2% of describing it); 0.28 s median per check (0.2-1.9 s) |
| equations: shadow AUROC against not exact_lenient | 0.45 (0.48 without scorer gaps): no ranking signal on this sample, whose 7 wrong items are 5 scorer gaps, a bold subscript and a misplaced comma, not meaning-level errors |
| equations: what drives P(meaning_changing) | bold: every correct equation with a `\mathbf` vector scored 0.20-0.59 (median 0.37) and every plain one 0.01-0.16 (median 0.02), so the 0.2 shadow threshold would flag 16 of 30 (all 12 correct bold ones). The 6 cheap answers escalated (on this sample, so far always for a dropped bold) scored 0.02-0.09 and their bold final proposals 0.31-0.46: the check does not see bold in these crops |
| equations: cost | $0.00004 per equation, 36 requests (30 final, 6 cheap stage) |
| document: answers | 71 (16 figures, 55 equations), all answered |
| document: review load | 2 figure notes, so optional notes 23 -> 25 (0.58 per PDF page); required decisions unchanged, 9 (0.21 per page) |
| document: shadow on the 30 matched equations | AUROC 0.57 (0.68 without scorer gaps); 9 would flag |
| **added cost per page** | **$0.00010 per PDF page** (3.8% of this run's $0.0026 per page) |

Reading it: the figure check costs little and adds about one optional note per 20 PDF
pages, but on this sample it has no observed true positive yet (as in the research: 0 of
3 real flags right), so it stays a note. The equation check, as worded for an image and
LaTeX text, is not usable as a signal on bold vectors; that is what shadow mode is for.
Before any equation gate: label real crops, compare the predicate form the research
measured (variant b), and keep the text-layer style rule as the bold check.

## Tables (`--task tables`, 2026-10-09)

Status: the task, its gold and its gate are built, and since the integration
(2026-10-09) it runs against the real detector and table step: the first in-repo numbers
are in "Content features after integration" below. The scorer was checked against the
research benchmark first (below). Code: `eval/tables.py`; the design is in the author's
private research notes, which are not in this repository.

2026-10-21: the model mode was run again with claude-haiku-5-5 and claude-opus-5-5 (183 tables,
$2.26) and the document task with the flag on and off; the numbers, the gate result and the
decision are in [tables.md](tables.md), "Tables on, on the development sets". The document task's
report has a `table_cell_maths` block (formulas of the gold's table cells found in the output's
cells; `eval/table_maths.py`), and the chemistry measurements are in [chemistry.md](chemistry.md).

### Gold

| Set | Where | License | Documents | Data tables | Layout tables |
|---|---|---|---|---|---|
| OpenStax | `data/gold/tables/` (13 pages of *Physics* and *Statistics*, table records only) | CC BY 4.0 | 13 pages x 3 print styles = 39 | 54 x 3 = 162 | 10 x 3 = 30 (the "Key Terms" grids) |
| Authored | `data/authored/tables/` (`README.md` there) | MIT | 6 | 35 (32 source tables; 3 run over two pages) | 3 |

- **OpenStax pages.** `readablestem gold fetch --set tables --date 2026-10-08` fetches them
  (the license and state gates of `gold fetch` apply; `ATTRIBUTION.md` is generated). They
  are table-bench's corrected gold (64 tables, 54 data tables): the committed records are
  line for line the ones table-bench scored. `data/gold/tables` is a gold set of its own
  (its own `manifest.json`); the other tasks do not read it (`gold_scoring.
  iter_gold_records` leaves nested sets out), so their selections are unchanged.
- **Print styles.** `eval.render` takes `table_style`: `grid` (every cell ruled),
  `booktabs` (rules above, below and under the header row only) and `norules`. The three
  prints are table-bench's (same CSS): on this machine they print the same text on the
  same pages, except the attribution line, which now names the authors.
- **Gold pages.** Gold has no table boxes, and a table's page depends on the print, so
  each gold table gets its page from the print (`assign_pages`): the first page from the
  previous table's on whose text holds at least 95% of the most any page holds of its
  words; then, by reading order (longest common subsequence of the table's words in row
  order with a window of the page's words, pages chosen in order), a first-pass page that
  scores under 0.6 moves when another scores at least 0.15 more. That second pass is the
  fix for *Statistics* 4.2, whose small tables (tab-005, tab-006) first landed on page 4,
  which only mentions their words; they are on page 6. `reference_pages.json` records the
  pages on the reference prints; a report gives `pages_differ_from_reference` (0 on this
  machine for all 192 prints).
- **Authored documents** cover what the OpenStax prints lack: LaTeX (Tectonic) and Word
  (exported by LibreOffice) toolchains, explicit row headers, two-level headers, math in
  header cells, tables split across pages (one gold table per page, sharing a `cluster`),
  an unruled LaTeX table, PowerPoint's default table style, a form laid out as a table,
  and table-like traps (a matrix, `align*`, `cases`, multiple choice in two columns).

### Modes

| Mode | What runs | Scored grid | Review load | Spend |
|---|---|---|---|---|
| `local` | `layout.analyze` (detection and the grid from the PDF's own words and lines) | `Element.table` | `table_step.rebuild` with tables off: each table kept as an image, a required decision | none (no `--max-usd`) |
| `model` | the same, then `table_step.rebuild` on each table's crop with tables on (pool of `--concurrency` threads) | `Fix.table.grid`; a table kept as an image scores 0 | the step's own tiers | `--max-usd` caps it (`--fake` for plumbing) |

Detection is matched on the detector's own grid in both modes, so a table the step kept as
an image still counts as found. A step that raises becomes a FAILED fix
(`internal_error`), as in `run`; a spend stop ends the run partial, as in the other tasks.

### Metrics

| Metric | Definition |
|---|---|
| `detection_recall`, `detection_precision` | A predicted data table matches a gold table on the same page by content (gold has no boxes): word-token F1 >= 0.5, or F1 of the letters and digits >= 0.7 for math-heavy tables (ranked on one scale as F1 - 0.2), pairs taken greedily, best first, one to one. A prediction matched to a layout table counts neither way. Predictions the pipeline itself called layout tables (`is_data` False) are not data tables and take no part |
| `teds`, `teds_found` | TEDS (Zhong et al. 2020): 1 - TED / max(nodes) over table -> row -> cell trees. Unit cost to delete or insert a node; relabeling a cell costs 1 for another tag or span, else the normalised Levenshtein distance of its text (`eval/ted.py` relabel hook). Text is NFKC, one delta, one minus, no spaces. `teds` counts a missed table as 0; `teds_found` is over found tables only. Headers are ignored, as in table-bench, so its numbers reproduce |
| `teds_structure` (`_found`) | the same with cell text ignored |
| `teds_with_headers` (`_found`) | the same, with a header cell (and its direction) a different tag from a data cell |
| `headers.column`, `headers.row` | precision, recall and F1 of header cells as (row, column, scope, text), micro-averaged over found tables; `exact`: share of gold tables with such headers whose header texts (a multiset) equal gold's; a missed table is not exact. Header texts are compared after `header_text` (2026-10-09): a raised mark the grid read from the PDF writes "^([1])" counts as "[1]", as gold's text drops the raise |
| `column_header_exact` | `headers.column.exact`: the headline header number (OpenStax marks only column headers, so row headers proposed for an identifying first column are a convention gap, counted apart as `row_headers_where_gold_has_none`). `headers.share_found_without_headers`: found tables given no header cell at all |
| `header_exact` | table-bench's: all header texts equal gold's |
| `data_vs_layout` | `accuracy_on_negatives`: layout tables no data-table prediction matched; `data_called_layout`: missed data tables a layout-table prediction covers |
| `maths` | per gold math cell: best letter/digit F1 of any predicted cell (`letter_digit_f1`, `cells_ok` at >= 0.9), and, when the gold cell is one formula, whether the predicted cell there has MathML equal to gold after the lenient normalisation of `eval/equations.py` (`mathml_match`) |
| `text_fidelity` | cells whose text is gold's (exact after `fidelity_text`, else the same letters and digits); `wrong_cells` per table = max(predicted cells left over, gold cells left over), so a misread number is one, two cells merged into one are two; `wrong_cells_not_flagged`: wrong cells in found tables whose fix is not a required decision. Must be 0. `fidelity_text` (2026-10-09) makes the ways gold and a reader write the same thing one: gold's text drops a superscript's raise ("10–18" for 10⁻¹⁸) while a cell writes 10^-18, 10^{-18} or 10⁻¹⁸; gold's stacked 1/3 reads "13" ("1/3" and ⅓ too); a "____" fill-in blank is an empty cell, however long; a raised mark the grid read from the PDF writes "^([1])" is "[1]"; and a gold footnote row (`<tfoot>`, printed under the table) counts only when the prediction has it. TEDS keeps table-bench's normalisation |
| `by_style`, `by_feature` | recall, precision, TEDS and column headers per print style and authored, and for tables with spans, math or row headers |
| `review_load` | the table fixes' tiers (`core.triage`): `required_decisions`, per table and per PDF page, `optional`, `needs_review_share`, `kept_as_image`, `required_by_reason`; `flags` (precision and recall over found tables, wrong = a wrong cell, another structure, column headers not exact, or kept as an image) and `wrong_not_required` |
| `cost` | `per_table_usd`, `per_pdf_page_usd`, `seconds_per_table_p50`/`p95` (model time), model calls; `latency_ms` is per document, wall clock (layout, crops, the step; printing excluded) |

Statistics: rates get a Wilson interval (`detection_precision_ci95`,
`accuracy_on_negatives_ci95`); numbers over gold tables get a cluster bootstrap over source
tables, so the three prints of one OpenStax table (and the two parts of a split table) are
one draw: `detection_recall_ci95`, `teds_ci95`, `teds_structure_ci95`,
`column_header_exact_ci95`, `header_exact_ci95` (2,000 resamples, seed 0).

The report has the format of the other tasks plus `mode`, `dataset.styles` and
`dataset.authored_sha256` (one hash over the authored index, gold and PDFs). `--items`
writes one row per document with every table's numbers (gold ids, no cell text).

### The scorer reproduces table-bench

table-bench (2026-10-08) measured two local pipelines on 170 tables (162 OpenStax prints and
8 authored LaTeX and Word tables): A, PyMuPDF union+refine with its own grid, TEDS 0.820;
B, the same boxes with SLANet-plus's grid, TEDS 0.868. `eval.tables` gives the same number
for every one of the 170 tables (detection, TEDS, TEDS-structure, header exact): recall
0.912, precision 0.957 and 0.951, TEDS 0.8203 and 0.8680, header exact 0.759 and 0.806.
`tests/test_eval_tables_bench.py` checks it on a committed excerpt (five documents, both
rows, with the numbers table-bench's own scorer gives on them), and on all 170 tables when
`TABLE_BENCH_EVIDENCE` points at the unpacked research archive. Fed table-bench's row A
detections in place of `layout.analyze`, the task itself (its own prints, page assignment
and matching) gives table-bench's numbers on the 162 OpenStax tables: recall 0.914,
precision 0.955, TEDS 0.823 (cluster bootstrap 0.767-0.877).

### Real documents (the gate's second part)

The OpenStax prints cannot show what the research's challenge found on four real lab
manuals (393 pages): vector figures, tables of contents and equations taken for tables
(precision 0.68) and blank worksheet tables cut to their header row (full-extent recall
0.72). So the task also checks detection on a private set of real course PDFs when there
is one: `private-inputs/tables/` (git-ignored; or `--real-dir`) with the PDFs and
`labels.json`:

```
{"schema_version": 1,
 "documents": [{"file": "manual.pdf",
                "pages": [0, 1, 2],
                "tables": [{"page": 2, "bbox": [x0, y0, x1, y1]}]}]}
```

Pages are 0-based; boxes are PDF points in PyMuPDF's page frame (`Element.bbox`'s);
`pages` (optional) limits the check to the pages that were labeled. A page saved sideways with
no /Rotate is read turned, so its labeled boxes are mapped into the turned frame before the
comparison (`labelled_as_read`). A predicted data
table is a real table when it shares at least half of the smaller box with a labeled one;
a labeled table is found at full extent when one prediction covers at least 90% of it and
at least 75% of that prediction lies inside it (not cut short, not merged with a plot).
`metrics.real_documents` holds counts only: documents, pages, labeled, predicted and true
tables, `precision`, `full_extent_recall` (Wilson intervals), `any_extent_recall`, error
codes and the labels file's SHA-256. No file name, page or text leaves the private folder.
The research's four public manuals are copyrighted: use them for local work only, never
commit them or their labels.

`data/real-tables/` is a committed set of the same shape: five openly licensed (CC BY 4.0)
lecture notes, textbook and tutorial extracts, 64 pages, 25 labeled tables, with the floors and
the licenses in its README. Run it with `--real-dir data/real-tables`, free and without a model
(about four minutes). First measured 2026-10-21: precision 1.000 (14 of 14), full-extent recall
0.560 (14 of 25): the recall floor was missed. It is a development set now (the detector was
tuned on it, docs/tables.md "Recall on real documents"): after the fixes precision 1.000 (23 of 23)
and recall 0.920 (23 of 25). `data/real-tables-heldout/` is a fresh set of seven CC BY papers, 58
pages, 39 labeled tables, registered (documents, labels, floors, selection rule) before any
detection ran on it and run once: precision 0.914, full-extent recall 0.744, both under the floors
(docs/tables.md has the table-by-table account). A second fresh set, `data/real-tables-heldout2/`
(ten CC BY documents, 100 pages, 58 tables, registered before the run), gave precision 1.000 (55 of
55) and full-extent recall 0.948 (55 of 58), both floors met, the same on `main` and on the
round-two branch; `data/real-tables-heldout` is a development set from that round on. The committed `latest-tables-*` reports are made
without a real-document directory; check the floors on them with `eval gate --current <a run made
with --real-dir>`. A private `private-inputs/tables/` of your own course PDFs is still the better
test (lab-manual worksheets are not among the twelve papers).

### Gate

`readablestem eval gate --current data/eval/latest-tables-local.json` checks the floors with
no baseline (with `--baseline` it also checks regressions: TEDS, recall and column-header
exact may drop 0.02-0.03, precision 0.03, cost per table rise 25%; a baseline of the
other mode or other authored documents is refused).

| Floor | Mode | Bar |
|---|---|---|
| `table_detection_recall` | both | >= 0.86 |
| `table_detection_precision` | both | >= 0.93 |
| `table_teds_local` | local | >= 0.84 |
| `table_teds_model` | model | >= 0.88 |
| `table_column_header_exact` | both | >= 0.75 |
| `table_wrong_cells_not_flagged` | both | 0 |
| `real_table_precision` | real documents | >= 0.95 |
| `real_table_full_extent_recall` | real documents | >= 0.85 |

The real-document floors apply only when the report measured real documents; otherwise
they show as skipped and do not fail the gate. Exit 0: every floor (and tolerance) met;
1: a metric below its floor, regressed or missing; 2: not comparable. The starting floors
sit a little under table-bench's measurements; `data/eval/tolerances.json` can move them
(`{"floors": [{"name": "table_teds_local", "minimum": 0.85}]}`).

TODO(Harish): after the first full in-repo run (both modes), replace the floors with the
lower ends of its 95% intervals, and decide whether CI gates on the committed
`latest-tables-local.json`.

### How to run

```
uv run readablestem gold fetch --set tables --date 2026-10-08     # only to refresh the gold
uv run readablestem eval pipeline --task tables --mode local --items .local/eval/tables-local.jsonl
uv run readablestem eval pipeline --task tables --mode model --max-usd 1.00
uv run readablestem eval gate --current data/eval/latest-tables-local.json
```

`--styles grid,norules` prints fewer styles, `--no-authored` leaves the authored documents
out, `--limit N` takes the first N documents (a page's three prints are next to each
other). Printing needs Chromium and the OpenStax page XHTML in `.cache/openstax` (`gold
fetch` fills it); figure images download once into `.cache/eval/images`. A local run
prints 39 pages and reads 6 PDFs, then calls no model. A model run costs about $0.0007
per table on gpt-6-luna (table-bench), more for the tables that escalate: about $0.15
for the 197 data tables if few escalate, under $3 if all went to gpt-6.1-sol.
`data/authored/tables/make_authored.py` rebuilds the two HTML documents and their gold.

### What the table numbers do not show

- **Prints, not course files.** 162 of the 197 data tables are OpenStax web pages printed
  by one browser in three styles: clean text, one font, figures as images, no tables of
  contents. The challenge's real manuals scored far lower on detection. Read the real-
  document part as soon as a private set exists.
- **Header conventions.** OpenStax marks only column headers. Row headers on an
  identifying first column are valid markup but are not in its gold; that is why the
  headline header number is column headers only. Row-header gold: 7 authored tables.
- **Text equality is not meaning.** TEDS weighs a misread digit in a long cell lightly; the
  text-fidelity count and its gate (0 unflagged wrong cells) are what catch it.
- **Matching by content.** Without gold boxes, a prediction that holds half of a table's
  words matches it; a table cut short can still count as found (`teds` then shows it).
## Long descriptions (2026-10-09, FEATURE_LONG_DESCRIPTIONS on)

The figures task on the 13 gold figures with the long prompt (`figure_long-a2c98225`),
graded by the alt judge (`judge_figure-137e2607`, as before) and the long-description
judge (`judge_long_description-ba1c35dc`), gpt-6.1-sol. Report:
`data/eval/figures-long-descriptions.json` (kept apart from `latest-figures.json`, the
option being off by default). Small: read the intervals.

| | Long prompt (this run) | Short prompt (`latest-figures.json`, 2026-10-08) |
|---|---|---|
| alt judge pass | 0.846 (11 of 13; CI 0.578-0.957) | 0.923 (12 of 13) |
| alt factual errors | 0 | 0 |
| long descriptions | 9 of 13: every chart and diagram (9), no photo or painting; mean 480 characters | |
| long judge pass | 9 of 9 (CI 0.701-1.000): accurate 9, complete 9, invented values 0, mean score 5.0 | |
| data tables | 3 (the position-time graphs with marked or labeled points), values within tolerance 3 of 3, 0 wrong cells | |
| values from the PDF text | 0 of 3 (the inputs are images) | |
| decisions added | 3 required (`values_from_image`; since 2026-10-09 a note, the table an optional extra: 0 required), 0 optional notes (no description marked unsure), 0 color-only flags | 0 |
| cost | $0.0040 per figure for the step; $0.124 for the run with both judges | $0.0035 per figure |
| step latency | p50 7.7 s, p95 15.9 s (sum of call latencies) | p50 5.8 s, p95 10.0 s |

Reading it:

- The long descriptions follow the W3C two-part pattern: axes with units and ranges,
  the trend, the labeled points, and "about" for values read off an axis. The judge
  found every table value within tolerance, but every table asked for a decision because
  its values came from pixels (since 2026-10-09 such a table is not written and asks
  nothing: the review screen offers it, "Add the data table"); in a PDF whose chart
  labels are real text, the printed values (400 m, 525 m at 0.50 s, 2000 m at 6.40 s)
  would be found in the text and the table written.
- The one alt result that changed: "Graph of y versus x" lost "rise over run" from its alt
  text (the long description carries it), and the alt judge, which does not see the long
  description, marked it as missing key information. One figure is within run-to-run
  variation at this size, but it is the direction to watch: the prompt now says the alt
  text must stand on its own. A first full run with an earlier wording gave the same
  numbers.
- One of three descriptions with a table still mentions "the table" despite the prompt;
  removing the table on the review screen then leaves that sentence to edit.
- The long judge is not calibrated, and it agreed with every description: a 100% pass on
  9 is an upper bound, not a measurement of the error rate. Label these before quoting
  them (`eval calibrate` covers the alt judge only).

Spend for this work: $0.252 in all (a one-figure smoke run $0.007, the first full run
$0.121, this run $0.124).
## Course notation (2026-10-09, `FEATURE_NOTATION_MEMORY`)

What a course's notation list changes on the equations task (docs/steps.md "Course
notation"). `eval pipeline --notation` runs the task with a list in force, as a
conversion for that course would (`SYMBOL[=MEANING]`, vector by default; the report
records the entries under `notation`). The comparison run was made the same day without
the list, because one run of 30 differs from the next by a few escalations. Real models,
$0.087 for both runs. Reports in `.local/eval/notation/` (not committed: the list is not
part of the gate's baseline).

```
uv run --env-file .env readablestem eval pipeline --task equations --max-usd 0.15 \
  --notation "bold d" "bold v" "bold a" \
  --out .local/eval/notation/equations-notation.json --items .local/eval/notation/items-equations-notation.jsonl
uv run python -m readablestem.eval.notation_flags .local/eval/notation/items-equations-notation.jsonl \
  --notation "bold d" "bold v" "bold a"
```

| Run (OpenStax physics gold, 30 equations) | Prompt | exact_lenient | Escalated | Model calls | Cost |
|---|---|---|---|---|---|
| Committed `latest-equations.json` (2026-10-08) | `equation-df0e12f1` | 0.767 | 5 | 35 | $0.049 |
| Same day, no list | `equation-df0e12f1` | 0.767 | 6 | 36 | $0.048 |
| Same day, "bold d, v and a are vectors" | `equation_notation-59309812` | 0.767 | 0 | 30 | $0.038 |

- 23 of 30 right in all three. The same 7 are wrong in both same-day runs: 5 are the
  scorer's known gaps, 2 are near misses (TEDS 0.97 and 0.90) the list did not change.
  Every fix ACCEPTED in both same-day runs, with one optional note each.
- With the list, the six short equations of section 2.1 (each with a bold d) were
  accepted from the cheap model; without it, all six escalated to the main model. Fewer
  calls, about a fifth less spent. The answers kept differ only in spacing and in how a
  subscript is written (`\mathbf{d}_{\text{f}}` or `\mathbf{d}_f`).
- The course list as evidence in the style check (it fires only where the PDF's text
  shows no bold at all) never fires on this gold as printed: Chromium marks every
  weight. Checked again on the same answers with every style removed from the text
  layer (`eval/notation_flags.py`): 6 new required decisions on 30 equations, all false
  (a plain v for average speed, a plain `d_Q`, which the list says nothing about).
  Adding plain v and d to the list removes them. The same check on two earlier runs whose
  answers often dropped a bold letter flags every answer the style check flags as
  printed (16 and 5), plus the same 6.
- Bold letters in running text (layout, no model call; docs/layout.md "Bold letters in
  prose") are scored by `python -m readablestem.eval.bold_letters --openstax`: 9 of 9 on the
  three gold pages and 11 of 11 on the LaTeX fixtures (the two-column handout's 10 bold
  vectors and its bold omega), no false positives. The equations task does not see them:
  it scores the gold's equations, not its prose.

## Content features after integration (2026-10-09, pipeline 0.4.0)

The first runs of the integrated branch (`feat/content-integration`: all eleven content
builders merged). Reports and per-item rows are kept local (`.local/integration/smoke/`),
not promoted: the committed `latest-*.json` stay the baselines until Harish decides.
Real spend for everything below and the conversions
("Content features after integration"): $1.00 of a $1.50 cap.

### Review load with every flag on (document task)

The document task on the same three OpenStax sections (43 PDF pages, `data/gold`) as the
0.3.0 report on `feat/beta-app` (`latest-document.json`, 2026-10-09), with
`FEATURE_TABLES`, `FEATURE_LONG_DESCRIPTIONS`, `FEATURE_COLOUR` and
`FEATURE_NOTATION_MEMORY` on:

| | 0.3.0 | 0.4.0, every flag on |
|---|---|---|
| required decisions | 10 (0.233 per PDF page) | 17 (0.395 per PDF page) |
| of which headings / equations | 9 / 1 | 9 / 1 (the same) |
| new: chart data tables read from the image (`values_from_image`) | | 5 |
| new: ruled key-term grids the model calls "not a data table" (`not_data_table`, kept as an image) | | 2 |
| optional notes per PDF page | 0.535 | 0.535 |
| equations exact (lenient), matched and end to end | 0.733 / 0.733 | 0.733 / 0.733 |
| predicted equations (unmatched "other") | 55 (1) | 64 (10: bold letters in prose made math, review none) |
| static and axe violations | 0 / 0 | 0 / 0 |
| cost | $0.141 | $0.142 |

Reading it: outside the new items the tiers call for, the review load is the 0.3.0 load
(same headings, same equation). The 5 data-table decisions are the tier's ("values read
from the image": required); the 2 key-term grids are the "Key Terms" boxes OpenStax prints
as a ruled grid of single words: detection claims them, the model rightly says they are
not data tables, and the table is then kept as an image, which needs a decision and
turns readable words into a picture. With `FEATURE_TABLES` off the same two grids were
`table_not_rebuilt` decisions, so merging detection alone made this 12 (0.279 per PDF
page). Both causes were removed afterwards, and neither change is re-measured with the
models yet ("Review load after the meaning-level changes" below): a chart data table read
from the image is an optional extra, never a decision, and the two key-term grids are
written as lists of their terms (`tables.is_term_grid`), flag on or off.

### Tables (`--task tables`)

| run | tables | recall | precision | TEDS | column headers exact | wrong cells not flagged | review | cost |
|---|---|---|---|---|---|---|---|---|
| local (no model; SLANet-plus file present), every style + authored | 197 gold data | 0.909 | 0.978 | 0.867 (0.955 on found) | 0.843 | 0 | every table a decision (kept as image) | $0 |
| model, `grid` + authored | 89 | 0.978 | 0.989 | 0.958 | 0.888 | 14 | 20 decisions for 96 tables (0.21 per table), 7 kept as images | $0.260 ($0.0027 per table) |
| model, `booktabs` + `norules` (stopped by its $0.45 cap after 20 of 26 documents) | 54 | 0.963 | 0.945 | 0.945 | 0.926 | 8 | 26 decisions for 63 tables | $0.404 ($0.0064 per table) |

`eval gate` on the local report passes every gold floor (the real-document part is
skipped: no private set). On the model reports it fails one floor, `wrong cells not
flagged` (0): the 14 cells of the first run, looked at table by table (re-run on the four
tables, $0.034):

- 2 are real: the last row of the physics orders-of-magnitude table loses its wrapped
  second line ("... the known *universe*", "*(current upper limit)*") in both the local
  grid and the model's answer, so the cell-text check agrees with a wrong grid; the box
  ends a line early.
- 6 are run-to-run: the contingency table scored 0 errors when run again.
- The rest are gold text written another way: OpenStax's "10–18" for 10⁻¹⁸ (the model
  writes `10^-18` as text), "13" for ⅓, "____" fill-in blanks the model leaves empty,
  and a footnote row gold keeps inside the table.

So the floor of 0 needs either a scorer that knows these spellings or a few labeled
exceptions before it can gate model runs (TODO(Harish) in gate.py stands). Superscript
powers written as `^` in text cells are a prompt issue for the table step to take up.
Both were done afterwards (below): the scorer knows these spellings (`fidelity_text`),
and the wrapped row is joined back; the model runs are not repeated yet.

### Demo and fixtures (smoke, real models)

| input | pages | required decisions | what they are | cost |
|---|---|---|---|---|
| `tests/fixtures/layout/latex/tables.pdf` | 4 | 6 | equations only; all 5 detected tables rebuilt and ACCEPTED on gpt-6-luna, every check passed | $0.019 |
| `tests/fixtures/layout/latex/problem_set.pdf` | 1 | 0 | | $0.007 |
| `tests/fixtures/layout/latex/links_language_colour.pdf` | 1 | 0 | 4 web links, 4 cross-references and 2 internal links all resolve; the French passage is marked; 2 color classes, the lowest at 4.53:1 | $0.003 |
| demo `source.pdf` (OpenStax Physics 2.3) | 13 | 9 (0.3.0 demo: 3) | the same 3 headings; 5 chart data tables read from the image; 1 equation whose bold v the model dropped (a right catch) | $0.066 |

All four pass the static checks and axe. The committed demo was rebuilt afterwards with
every flag off ("Public demo" above): 4 required decisions, the same 3 headings and
again equation 2.5's dropped bold v.

## Review load after the meaning-level changes (2026-10-09, evening)

What changed (Harish's policy: additive
extras never add a required decision):

- A chart data table whose values are not all in the PDF text is not written into the
  student file and is not a decision; the review screen offers it ("Add the data
  table"). Tables whose values all come from the PDF text are written, as before.
- A claimed grid the local check calls a grid of terms (`tables.is_term_grid`) is
  written as a list of its terms, flag on or off: no table, no image, no decision. With
  the tables flag on, a grid the model calls "not a data table" is written as its text
  (an optional note, `written_as_text`). With the flag off, a grid the local check
  calls data stays an image with a required decision.
- The tables scorer compares gold's spellings as the same text (`fidelity_text`, and
  the raised marks in headers), and a table row the page break split is joined back.

**Not run: every real-model measurement of this stage.** The OpenAI account is out of
credit: every call returns HTTP 429 `credit_balance_exhausted` (confirmed 2026-10-09,
20:40 CDT). So the document task with the flags off and on (cap $0.40), the demo, and
the tables model run on `grid` + authored were not run, and nothing was spent. The
numbers below are offline: layout (no model), the document task with the fake client,
and the stored real answers of the integration runs with today's rules applied.

### Claimed grids (layout only, no model)

`layout.analyze` on each set, counting the grids `tables.find` keeps:

| set | PDFs (pages) | claimed data tables | claimed grids of terms, written as lists | grids left on the paragraph path |
|---|---|---|---|---|
| document gold (Physics 2.1-2.3) | 3 (43) | 0 | 2 | 0 |
| table gold prints (13 pages x 3 styles) | 39 (654) | 151 (150 tables after a split row is joined) | 14 | 3 |
| authored tables | 6 (21) | 34 | 0 | 0 |
| test fixtures (`tests/fixtures/layout`) | 12 (18) | 5 | 0 | 0 |
| demo `source.pdf` (Physics 2.3) | 1 (13) | 0 | 0 | 0 |

The document gold's two grids are the "Key Terms" boxes of 2.1 (8 terms, displacement to
vector) and 2.2 (6 terms, average speed to velocity). Converted offline, each is a `<ul>`
of its terms, with no table, no flagged image and no decision; static checks and axe
pass. In the tables eval, 31 of the 33 gold layout tables are kept out of the data
tables (17 of 33 before `is_word_grid`, 24 with it). The two left: the norules print of
the Physics 1.3 key terms, where a term wraps onto a row of its own (docs/tables.md
"Known misses"), and the authored contact grid (Instructor | Dr. Alex Rivera, Office | ...),
two columns of a label and a value with digits, which the local check calls data: with
the flag off it is an image and a required decision.

### Document task with the fake client (no model)

`eval pipeline --task document --fake`, the flags all off, then all on
(`.local/review-load/document-fake-{off,on}.json`, not committed). The fake refuses
every call, so every equation and figure is a FAILED decision (`model_refused`: 71):
those are not review load. Headings and tables do not depend on the model:

| | flags off | flags on |
|---|---|---|
| heading decisions (groups counted once) | 9 | 9 |
| table decisions | 0 | 0 |
| optional heading notes | 20 | 20 |
| predicted equations | 55 | 64 (bold letters in prose made math) |
| static and axe violations | 0 / 0 | 0 / 0 |

The 9 heading decisions are the 0.3.0 report's 9.

### Stored real answers, today's rules

The flags-on demo conversion of the integration (Physics 2.3, the third section of the
document gold; `.local/integration/smoke/demo.json`) had 9 required decisions: 3 heading
groups, equation 2.5's dropped bold v, and 5 figures whose only failed check was
`values_from_image` (confidence 0.96-0.99). With today's figure rule those 5 figures are
accepted with a note and their tables offered: 4 required decisions, the same 4 as the
flags-off demo rebuilt with real models during the sync.

### What this gives for the targets (an estimate, not a measurement)

| document gold, 43 PDF pages | measured with models | today's rules on the same answers | target |
|---|---|---|---|
| flags off | 12 (0.279; the 0.3.0 run's 10 plus the 2 key-term grids as images) | 10 (0.233) | <= 0.25 |
| flags on | 17 (0.395) | 10 (0.233): minus 5 chart tables, minus 2 key-term grids | <= 0.28 |

By cause, both: 9 heading decisions (measured offline above), 1 equation (equation 2.5,
`style_mismatch`, a right catch in every real run so far), 0 tables, 0 figures. Model
answers vary from run to run (an escalation more or less), so this is an estimate on
the integration's answers.

Measured on 2026-10-12 with real models ("Results"): flags off 10 (0.233), flags on 9
(0.209), each the 9 heading groups plus, flags off, equation 2.5. TODO(Harish): rebuild
the demo with the flags on (about $0.07) and run the tables model task on `grid` +
authored (about $0.26).

### Tables (`--task tables`, local mode, no model)

`.local/review-load/tables-local-final.json`, every style and the authored documents:

| | integration (13:00) | before the scorer change (20:30) | now |
|---|---|---|---|
| recall / precision | 0.909 / 0.978 | 0.909 / 0.984 | 0.909 / 0.984 |
| TEDS (on found) | 0.867 (0.955) | 0.866 (0.954) | 0.866 (0.954) |
| column headers exact | 0.843 | 0.792 | 0.843 |
| header exact | 0.812 | 0.761 | 0.812 |
| layout tables kept out | 17 of 33 | 31 of 33 | 31 of 33 |
| cells faithful, wrong cells | 0.981, 197 | 0.983, 188 | 0.983, 188 |
| wrong cells not flagged | 0 | 0 | 0 |

`eval gate` passes every gold floor (the real-document part is skipped). Column headers
fell to 0.792 when the local grid began writing a raised mark as "^([1])" (pipeline
"keep raised glyphs as exponents"), against gold's "Relative Strength[1]": a spelling,
not an error, so the header numbers now compare the mark's characters
(`eval.tables.header_text`). In local mode every table is kept as an image with a
decision, so "wrong cells not flagged" is 0 by construction; the floor matters for model
runs, which were not repeated. The wrapped last row of the orders-of-magnitude table is
joined in the `grid` print (17 rows, "the known universe", "(current upper limit)");
in the `booktabs` print the page break falls between rows; in the `norules` print the
next page's part (one row under a repeated header, no rules) was not detected when this
was measured, so the row ended "(current" and the rest was read as headings and
paragraphs; `tables.find_continued` now finds it (docs/tables.md, "Continued tables").

## Page types (2026-10-09, no model)

`pipeline.page_type` decides whether a page's text layer can be trusted and whether it
is handwritten or scanned (docs/layout.md "Hidden text and page types"). Its scorer:

```bash
uv run python -m readablestem.eval.page_types --labels LABELS.json [--labels ...] [--out rows.json]
```

A labels file is a JSON list of `{"file", "page" (1-based), "label" (a page kind),
"split", "group"}`; relative file names are read from the labels file's folder or the
one above it. It prints the confusion matrix of page kinds for all pages and per split,
the matrix of what the app does with each page ("text", "text_and_ink_images",
"page_image", "nothing"), every error with its counts, and the time per page. Kinds,
counts and file names only: never page text. No model is called.

The labeled sets are not in this repository: the research corpus holds derivatives of
a real lecturer's handwritten notes and PDFs from local research folders, and lives with
the author's private research (996 labeled pages). The
results, and why only one of them is out of sample, are in docs/layout.md "Accuracy,
honestly": page-type-v5 on fresh set 4 (31 pages built after it was frozen) 27 of 31
kinds and 28 of 31 actions; page-type-v5.1 on the 996-page corpus 984 kinds and 989
actions, on the design review's 41 adversarial pages 29 and 35; page-type-v5.2
(2026-10-10) on the 996-page corpus the same 984 and 989 (median 7 ms a page, p95
146 ms), on fresh set 4 29 and 29, on the 41 adversarial pages 29 and 35; page-type-v5.3
(2026-10-12) the same on all three (median 7 ms, p95 151 ms); page-type-v5.4 (the
silent-loss sweep, the same evening) the same kind and action for every page of the
three sets (median 8 ms, p95 157 ms on the corpus).

The headings and document evals were not re-run for this change: no model was called
in this stage. The committed born-digital fixtures (`tests/test_page_types.py`) have no
hidden character and no kept page, and the golden HTML is unchanged
(`tests/test_assemble_golden.py`), so the expected change on the gold is none; re-run
them with the next model run to confirm it (TODO). For the layout changes of 2026-10-12
(the safety net on pages with little text, light handwriting on typed pages, frames round
the page) the document task was run offline before and after (`--fake`): its per-page
rows are identical (30 of 30 gold equations matched among 55 predicted, the same headings
and the same 80 required decisions of the offline client), and layout gives the same
elements on every PDF in the repository (the three printed gold sections, the LaTeX
fixtures, the demo source).

The silent-loss sweep's changes (2026-10-12, docs/layout.md: diagrams of joined boxes
and line chains, small drawings and hand marks on typed pages, pictures with text on
them and covering the page, chart labels in the bands, near-white paint on a dark
ground) were checked the same way. The document task offline (`--fake`): identical
per-page rows. Layout on the repository's PDFs: the same elements on the three printed
gold sections, the 42 cached OpenStax renders, the LaTeX and layout fixtures and the
demo source; the authored tables slides lose a figure made of their table's colored
cells (the white lines between them are now seen, so the table's grid is drawn), with
the tables eval (local) the same to the last digit; and one handwriting fixture,
`overlays.pdf`, changes as intended (its page under a transparent picture of hand-drawn
circles keeps its image; its half-page striped mask is a figure). The 110 sweep PDFs: 3 LOST verdicts left, all
the generator's subtitle that was never written (41 before), 56 converted, 38 kept with
a decision, 13 refused; only the three typed controls say "Everything was converted".
On the research corpus (909 pages; labels as in "Page types"), no page labeled
born-digital keeps its image or changes kind. Twelve of them change: three keep the
same elements (a figure gains its white lines), seven gain content that was dropped
before (a report's two screenshots under their captions and a brochure's cover
pictures, once taken for backgrounds; a scatter of TikZ shapes and five flat spring
drawings under 24 pt; icons at a page edge; a small mark at a page's foot; one hand
mark, as a handwriting region), and two pages of machine-drawn loops the classifier
already calls `mixed_ink` (a known error) gain handwriting regions (a needless
decision). Three `vector_no_text` pages (two of typed text turned into outlines, a
hatched pie chart that became no figure) now keep their image.

The second final verify's cases (2026-10-12, docs/layout.md: a path's box from all its
points, axes and rulers, labels taken back from the bands, pen marks over typed text)
were checked the same way, no model called. The document task offline (`--fake`): the
same per-page rows and report as before, timing aside. Layout on the repository's PDFs
(the three printed gold sections, the 42 cached OpenStax renders, the authored tables,
the demo source, the layout, LaTeX, tables and handwriting fixtures): the same elements
and kept pages everywhere but one handwriting fixture, whose machine-drawn curve's box
grows by the start of a subpath MuPDF left out. Page types: the same 984 kinds and 989
actions on the 996-page corpus, 29 and 35 on the skeptic's 41 pages, 29 and 29 on fresh
set 4. On the research corpus's 909 analyzed pages, no page changes kind or kept image;
three born-digital pages change only a figure's box or path count, and one `mixed_ink`
page gains two handwriting regions (red pen marks over its typed lines). The 110 sweep
PDFs end to end: the same verdicts (56 converted, 38 kept with a decision, 13 refused;
the three LOST items are the generator's subtitle that was never written), and the
framed full-page plot has no painted pixel outside its elements (88 cells before). The
86 evidence PDFs and the 90 handwriting pages: the same verdicts and decisions; two
full-page charts read their tick labels as the figure's, not as one-digit paragraphs,
and a 30-page file of notes has more of its pages analyzed within the 10 s ink budget
(called handwritten, not "not analyzed"; kept as images either way): the machine's load,
not the change, as the base code run alone gives the same.

The drawings sweep's fixes (2026-10-13, docs/layout.md "Figures": evidence of a plot,
boxes round text and diagrams, bonds, boxes joined by wires, dots round a letter, hand
marks circled or filled in another color and over equations, answer fields, icons round
a glyph) were checked the same way, no model called, against the branch before them
(`91697be`) and the merged base (`9071495`). The document task offline (`--fake`): the
same report and per-document rows as the base, timing aside (the same 80 required
decisions of the offline client). Layout on the 96 born-digital PDFs (the 42 cached
OpenStax renders, the three printed gold sections, the authored tables, the demo source,
the layout, LaTeX, tables and handwriting fixtures): the same refusals, kept pages, kinds
and elements as before the fixes; against the base, only the five handwriting fixtures
the previous fix targeted differ. The research corpus's 909 analyzed pages, the
skeptic's 41, fresh set 4 and the evidence and sweep sets: no page changes kind, kept
image or elements, except a slide whose title, 18 pt and its only text, is read as a
heading instead of a figure's label, and one page of the 30-page file of notes that ran
out of the 10 s ink budget (called not analyzed instead of handwritten, and kept as an
image either way: the base code run twice on its own analyzes 26 and 27 of its pages).
The checks found four opposite errors in the first versions of the fixes, each fixed and
pinned by a control before the end: a worked example's frames round paragraphs with a
grid of cell borders made one figure of the box (its open chain of rules was taken for a
diagram; such signs now need a cluster without running text), a report's box of four
lines whose corners do not quite meet read as an L of axes (`AXIS_CLOSE_PT`), a yellow
highlight annotation was cut out as a hand mark (a filled mark must be a thin ribbon),
and a gradient band of thin strips behind text kept its page (filled strips are panels).
The drawings sweep (97 PDFs) end to end, after the fixes: 73 converted, 18 kept with a
decision, 6 LOST; before them 48, 16 and 33 (the base: 46, 15 and 36), with 22 pages
saying "Everything was converted". The six: four partial losses, where a chart is a
figure but its baseline along the page's edge (three times) or its axes that stop short
of their corner (once) are not in it (docs/layout.md "What is not detected"), and the
two documented ones, a gear icon drawn inside a sentence and three ticks close together
over a line, which still say "Everything was converted". The only other page that says
so is the exam cover, rightly: its comb fields hold nothing yet. The earlier 110 sweep PDFs, the 86
evidence PDFs, the 90 handwriting pages and the 88 extra pages end to end: the same
verdicts as before the fixes.

## Held-out set (pre-registered 2026-10-13)

The sections above were used to build and tune ReadableSTEM, so their numbers are
development numbers. The held-out set is 15 OpenStax sections that no development step
saw, fixed in a pre-registration before any system ran on them: 3 sections each from
College Physics, Chemistry, Precalculus, Introductory Statistics and Intermediate Algebra,
242 Letter pages, 1,369 equations, 70 figures (69 with OpenStax alt text), 27 tables and
509 headings. The pre-registration (version 2, SHA-256 `d4d3af030b403a84da2c1a6787dc9773775a826cfa4f86edf9b48f7ec3f288ec`)
holds the selection rule, the metrics, the statistics and what counts as a failure, and is
published with the results. The gold manifest's SHA-256 is
`9be8c5650fe492d0c5312199b3040aa6ea741595d5ba97df4b68e816455c9aa3`.

**Retired first editions.** Every live CC BY STEM book (Physics, Statistics) was used in
development, and every current edition of the others is CC BY-NC-SA (`gold licenses`,
2026-10-13). The set therefore comes from retired first editions, which are CC BY 4.0 in
the OpenStax archive and CMS. Whether a retired edition is acceptable ground truth was
answered for this evaluation on 2026-10-13 (`proof/DECISION-retired-editions-2026-10-13.md`,
ADR 0001 question 2), and the ADR as a whole stays open. Fetching needs `--allow-retired`; every record carries
`book_state: retired`. Their MathML and alt text are older in style than the current
editions'.

**The answer key is not in the repository.** The gold, the caches and the reports stay outside
it, by default under `~/.cache/readablestem/heldout`
(`$XDG_CACHE_HOME`, or `--heldout-dir`): the research sweeps read every PDF in
`.cache/eval/pages/`, and `data/eval/` feeds the CI gate, so a held-out file in either
would flow into development or into the gate. The exception is `proof/interim/heldout-2026-10/`:
the printed PDFs and the open parsers' and the headings task's outputs, which sit outside
`data/`, `tests/fixtures/` and the research sweeps' folders (`proof/REGISTERED.md`). `tests/test_heldout.py` checks that no
held-out section is under `data/` or `tests/fixtures/`, and the tests build their own
small synthetic set.

```
uv run readablestem gold fetch --set heldout --allow-retired --date 2026-10-13   # ~25 requests
uv run readablestem gold verify --set heldout
uv run readablestem eval render --set heldout --all            # prints 15 PDFs, counts pages
uv run readablestem eval equations --set heldout --self-check  # plumbing only, always 1.0
uv run readablestem eval pipeline --set heldout --task document --max-usd N
```

- **`gold fetch --set heldout`** writes what `gold fetch` writes for any set: one JSONL per
  section with its equations (MathML), figures (OpenStax alt text, caption, image URL),
  tables and headings, `_book.json` per book, `manifest.json` and `ATTRIBUTION.md`. Each
  book is read from the archive release that was current at registration
  (`20260604.144757`), not the current one, so the files do not change when OpenStax
  publishes a new release. With `--date 2026-10-13` the 22 files are byte for byte the
  registered ones; on another day they differ only in `retrieved_at`, and `gold verify`
  says so. `tests/test_heldout.py -m network` rebuilds them from OpenStax and compares.
- **`gold verify --set heldout`** compares a folder with the registered hashes: the 15
  JSONL files (and, for a mismatch, whether only `retrieved_at` differs), no other gold
  file, `manifest.json`, `ATTRIBUTION.md` and each `_book.json`. It proves the files are the
  registered ones, not that OpenStax's markup is right.
- **`--set heldout` on `eval pipeline`, `eval equations` and `eval render`** takes the gold
  from that folder and stops, naming what differs, unless it is the registered gold. It
  refuses `--fake` (a fake run still runs the layout and heading code on these sections,
  which the pre-registration does not allow before the registered run), `--notation`, the
  tables task, and any `--cache`, `--out`, `--items` or `--report` inside the repository.
  It changes no prompt, model, threshold, rule or scorer: it picks the data and the
  folders. The report gets `set: heldout` and the pre-registration's hash; its
  `dataset.manifest_sha256` is the registered manifest's.
- **`eval render --set heldout --all`** prints the 15 sections (Letter) into the render
  cache under the key the document task looks them up by, so the run converts these very
  files, and as `<book>-<page>.pdf` into `pdfs/` with a `pages.json` of page counts and
  SHA-256 values. It exits 1 if a Letter print has another page count than registered (242
  in all). Printing only counts pages; nothing converts the PDFs.

The run itself (ReadableSTEM, the chatbot recipe, the open parsers), the cluster bootstrap
and the invented-content rate are specified in the pre-registration and happen once, after
its sign-off items. Until then no number from this set exists.

## Extended development set (dev-v2, T2-0)

The original development set (`data/gold`: three Physics sections, 30 block equations at the
scorer's ceiling) cannot show the failures the held-out run found: inline stacked fractions
cut apart, systems and determinants cut into rows, exercise numbers glued onto equations,
equations pasted as pictures, chemistry and nuclide notation, flattened sub- and
superscripts, large slide formulas, repeated and sentence-like headings. Fixes found by
looking at held-out errors must be checked on a new held-out set, so the development set
has to contain those classes first (backlog item T2-0). `data/dev-v2/` is that set.
It is a development set: numbers on it say nothing about documents in general, and a rule
tuned against it is not validated until it passes a set nobody tuned against.

**What it holds.** Gold for OpenStax sections and authored documents, with every item
labeled with the Tier 2 classes it belongs to. Items per class (the `summary.md` table;
`readablestem gold devset summary` prints it; Check counts are totals of the sealed part):

| Class | Item | Backlog | Target | OpenStax | Authored | Dev | Check |
|---|---|---|---|---|---|---|---|
| Determinants (`determinant`) | equation | T2-2 | 20 | 81 | 22 | 103 | 45 |
| Large display formulas (slides) (`display_large`) | equation | T1-14 | 20 | 0 | 49 | 49 | 0 |
| Inline stacked fractions (`inline_fraction`) | equation | T2-1 | 40 | 264 | 79 | 343 | 81 |
| Matrices (`matrix`) | equation | T2-2 | 20 | 50 | 23 | 73 | 12 |
| Multi-row equations (unbraced systems, worked steps) (`multirow_equations`) | equation | T2-2 | 20 | 149 | 13 | 162 | 44 |
| Numbered exercises with equations (`numbered_exercise_eq`) | equation | T1-5 | 40 | 295 | 54 | 349 | 98 |
| Piecewise functions with words (`piecewise_words`) | equation | T2-2 | 20 | 9 | 16 | 25 | 14 |
| Systems of equations with a brace (`system_braced`) | equation | T2-2 | 20 | 65 | 28 | 93 | 21 |
| Equations as pictures (`equation_image`) | equation | T2-3 | 20 | 0 | 37 | 37 | 38 |
| Charges on ions (`chem_charge`) | equation | T2-4 | 20 | 69 | 42 | 111 | 58 |
| Chemical equations (`chem_equation`) | equation | T2-4 | 20 | 91 | 57 | 148 | 65 |
| Chemical formulas (`chem_formula`) | equation | T2-4 | 20 | 133 | 78 | 211 | 109 |
| Nuclide notation (stacked prefix scripts) (`nuclide`) | equation | T2-5 | 20 | 59 | 39 | 98 | 36 |
| Chemical subscripts and charges in prose (`prose_chem_script`) | script run | T2-4 | 40 | 113 | 150 | 263 | 165 |
| Nuclide prefix scripts in prose (`prose_nuclide`) | script run | T2-5 | 20 | 24 | 55 | 79 | 57 |
| Other sub- and superscripts in running text (`prose_script_other`) | script run | T2-4 | 40 | 119 | 139 | 258 | 161 |
| Units with superscripts in prose (cm3, m/s2) (`prose_unit_power`) | script run | T2-4 | 40 | 40 | 106 | 146 | 102 |
| Code blocks (`code_block`) | block | - | 20 | 0 | 40 | 40 | 0 |
| Glossary and key terms (`glossary_entry`) | entry | - | 40 | 67 | 59 | 126 | 44 |
| Headings long enough to wrap (`heading_multiline`) | heading | T2-8 | 20 | 33 | 14 | 47 | 5 |
| Repeated heading labels (`heading_repeated_label`) | heading | T2-7 | 40 | 153 | 37 | 190 | 42 |
| Headings that are sentences (bold lines) (`heading_sentence`) | heading | T2-7 | 40 | 80 | 49 | 129 | 33 |
| Words hyphenated at a line end (`hyphen_break`) | line end | T1-9 | 40 | 0 | 198 | 198 | 32 |
| Data tables (`table_data`) | table | - | 20 | 15 | 8 | 23 | 6 |
| Layout tables (`table_layout`) | table | - | 10 | 20 | 7 | 27 | 11 |

An item is an equation (record with `classes`), a sub- or superscript run in a paragraph
(`script` records), a heading, a table, a code block, a glossary entry, or a word hyphenated
at a line end (`hyphen_break` records, authored documents only). "Target" is the least wanted
across the whole set (the math classes 20 to 40).

**Sources and licenses.**

| Part | Source | License | Toolchain |
|---|---|---|---|
| OpenStax sections | Algebra and Trigonometry, College Algebra, Elementary Algebra, Chemistry: Atoms First (retired first editions) | CC BY 4.0, attribution in `ATTRIBUTION.md` and in every record | Chromium print by `eval render` (the document, headings, equations and figures tasks print them as for any gold page) |
| Authored documents | written for this project (`data/dev-v2/authored/build_authored.py`) | MIT | LaTeX built by Tectonic 0.17, Word files made with python-docx and exported by LibreOffice 26.2, HTML printed by Chromium |

Every record carries `license_name`, `license_url` and its source URL. The books are CC BY in
both the archive and the OpenStax catalogue (`check_public_license`); retired first
editions are accepted by the decision of 2026-10-13
(`proof/DECISION-retired-editions-2026-10-13.md`). Figures are referenced by URL, not stored.

**Staying clear of the held-out set.** Three rules, all checked by code:

1. None of the 15 registered sections is used, and no section of a book that has one
   (College Physics, Chemistry, Precalculus, Introductory Statistics, Intermediate Algebra).
   The candidate books are Algebra and Trigonometry, College Algebra, Elementary Algebra,
   Prealgebra, Chemistry: Atoms First and Physics. Those share a text lineage with held-out
   books (Precalculus, Chemistry, College Physics, Intermediate Algebra), so:
2. Every chosen section is measured against the 15 registered sections' text: the share of
   its word 8-grams (visible text, MathML removed; the measure of
   `proof/selection/features.py`, a test checks the two agree) found in the held-out text
   must be below 0.05. The measure does remove siblings: 15 of the 458 numbered sections of
   the candidate books reach 0.05 or more (the highest value kept is 0.048), among them
   Chemistry: Atoms First 19.3 and 14.7 (1.0), Algebra and Trigonometry 7.2 (0.82) and
   Elementary Algebra 5.5 (0.65, a mixture-application section that repeats Intermediate
   Algebra 4.3). The chosen sections' values are in
   `selection.json` (highest: 0.031); `gold devset verify --overlap` recomputes
   them against the held-out folder's archive cache.
3. `gold devset verify` (and a test) fails if a dev file is from a held-out section or book.

The held-out sections' archive pages are read from `~/.cache/readablestem/heldout/openstax` only
to compute these numbers; none is stored in the repository or its caches.

**How the sections are chosen** (`gold/devset.py`, `gold devset select`, deterministic and
pinned to archive 20260604.144757): candidates are the numbered sections of the candidate
books with at least 300 words and overlap below 0.05, not used in earlier development. Each
candidate's items are classified (`gold/devset_classify.py`: MathML shape, the sup and sub
elements of the page, heading text, `dl`/`pre` blocks). A greedy rule takes the section with
the most still-needed items per printed page until the class targets in `selection.json` are
met, prints each winner first (Chromium, as `eval render` does) and drops one longer than
30 Letter pages, the most the pipeline accepts; sections that repeat a chosen section
(8-gram overlap above 0.2) are skipped. Key-terms pages of the chosen chapters supply the
glossary entries. The dev part is 17 sections and one key-terms page of OpenStax (233 Letter pages) and 10 authored documents (105 pages): 28 documents, 338 pages, `manifest.json` SHA-256 `df760b3f59cdebe6c7eda3d6c13663d7ae5535881503f0b12da92fc60ecdf9c6`. The check part is 5 OpenStax sections and 4 authored documents, 101 pages. No section is over the limit (the longest is 30 pages).

**Records.** OpenStax records are the four kinds `gold fetch` writes (equation, figure, table,
heading; ids and fields unchanged) with `classes` added, equations also with
`exercise_number` (the number printed in front of an equation that opens a numbered
exercise) and `appearance`. New kinds: `script` (a paragraph's sub- and superscript runs:
the text with `^{...}` and `_{...}` marks, each run's value, base and class), `code`,
`glossary` (term and definition), and for authored documents `hyphen_break` (left and right
fragments, the intended word, whether the hyphen belongs to it) and `exercise_text` (an
exercise typed as text in Word). Authored records have `source: authored`, the `pdf` that
the eval reads, `toolchain` and `variant`; their equations carry the LaTeX they were set from
and its MathML (`latex_to_mathml`), the pictured ones the PNG as well.
Only what has a scorer today is scored: equations (every class), headings and tables. The
gold for prose sub- and superscripts (T2-4, T2-5) and for words hyphenated at a line end
(T1-9) waits for the changes that need it. Code blocks and glossary entries are scored by a
small local check, `eval/structure_check.py` ("Structure check", below), until the scorers
of `feat/t2-scorers` replace it.

**Two parts.**

- `dev` (`data/dev-v2/dev/`, `manifest.json` with every file's SHA-256 and the class counts)
  is for building. It is what `--set dev-v2` runs by default.
- `check` is small and sealed: `check.sealed.tar.gz`, whose SHA-256 is in `SEAL.json`
  (SHA-256 `09cf01cd0e8f3d0760801c8773a7792b1456d4be478a12b2be7975f35eccdc75`, 689,154 bytes, 927 records in 62 files). Its OpenStax sections are the greedy choice among the 15% of candidates
  whose SHA-256 of `salt:book/slug` falls below 0.15; its authored documents are fresh draws
  from the same generators (the same templates, different numbers, words and terms, so it
  checks plumbing and regressions more than new styles). It was written by the build script
  and not read while the set was built. `SEAL.json` holds counts and hashes and no item
  name. The tests compare hashes and section names (the dev part and the held-out
  sections must not overlap it) without printing anything. `--part check` verifies the
  archive against the seal, extracts it under `~/.cache/readablestem/dev-v2/`, keeps its
  render cache, archive cache and report outside the repository, refuses `--items`, and its
  report holds totals only. The seal is a hash and a rule of use, not secrecy: run it for
  the totals before a merge and do not open it.

**How inputs are rebuilt.** OpenStax sections as for any gold page (`eval/render.py`, page
XHTML from the `gold fetch` cache). Authored documents are the PDFs committed beside their
gold (`eval/devset_media.py`): `DevSetMedia.page_pdf` reads them and `equation_context`
returns the sentence stored with the equation. The PDFs are untagged. Tectonic runs with
`-Z deterministic-mode`; Word and LibreOffice output is rebuilt by the same script but
depends on the installed fonts and hyphenation dictionaries, so the committed PDFs, not a
rebuild, are the inputs.

```
uv run readablestem gold devset summary                  # items per class
uv run readablestem gold devset verify [--overlap]       # manifest, seal, held-out rules
uv run readablestem eval equations --set dev-v2 --self-check
uv run readablestem eval pipeline --task document --set dev-v2 --fake
uv run readablestem eval pipeline --task headings --set dev-v2 --part check --max-usd 2
uv run readablestem eval pipeline --task tables --set dev-v2 --fake
uv run readablestem eval classes --set dev-v2 --self-check     # scripts, code, glossary, hyphens
uv run readablestem eval classes --set dev-v2 --html-dir DIR   # converted pages <book>__<page>.html
# rebuild (needs network for the OpenStax archive, Chromium; the authored pool first):
uv run python data/dev-v2/authored/build_authored.py --out /tmp/pool --tectonic PATH
uv run readablestem gold devset build --authored-pool /tmp/pool --date 2026-10-14 --force
```

`--set dev-v2` works on `eval pipeline` (equations, headings, figures, document and tables;
reports go to `.local/eval/dev-v2/` for dev, not to `data/eval/`), `eval equations`
(`--self-check`, dev only) and `eval render` (dev pages). `--fake` is allowed on both
parts: a fake run still prints the sections and runs the layout and heading code, with every
model call refused at zero cost (the cost lines of a fake run are simulated reservations,
not spend).

**Scorers for the classes without an equation or heading scorer** (`eval/prose_classes.py`,
backlog T2-4, T2-5, T2-16, T1-9). Four kinds of dev-v2 gold had no scorer: sub- and
superscript runs in prose (`prose_unit_power`, `prose_chem_script`, `prose_nuclide`,
`prose_script_other`), code blocks, glossary entries and words hyphenated at a line end.
They read the converted HTML of the document task (`DocumentResult.html`) and report
`metrics.prose_classes` there (a per-page `prose_classes` in `--items`; the key is absent
without such gold, so the committed reports are unchanged); `eval classes --set dev-v2
--html-dir DIR` scores saved pages and `--self-check` scores HTML rendered from the gold.

| Class | Results (the first is the right one) |
|---|---|
| script runs, per run class | `sub_sup` (a `<sub>`/`<sup>`) or `math` (a script in inline MathML), together `scripted` and `scripted_rate`; `flattened` (the letters around it are there, the script glued on as plain text: SO42-, cm3); `lost` |
| code blocks | `exact` (a `<pre>` holds it line for line with its indentation), `in_pre` (a `<pre>` with other lines or indentation), `text_only` (the lines are in the page outside a `<pre>`), `missing`; `extra_pre` counts `<pre>` blocks matching no gold block |
| glossary entries | `definition_list` (a `<dt>` with the term, a `<dd>` with the definition), `text_block` (term and definition in one paragraph or list item), `found_apart`, `missing` |
| hyphenated words | `joined` (whole in the output; a compound keeps its hyphen), `kept_hyphen` (still "inves-tigates", or a compound that lost its hyphen), `split_words`, `missing` |

A script run is found by the letters and digits around it (8 before, 4 after, taken from the
gold paragraph's own `^{..}`/`_{..}` text), so a multiplication sign or space does not
matter; a script is credited when an output script of the same kind and value stands at
that place, once each. Hyphenated words are judged by count per word, and a wrong form
anywhere in the page counts against the word, so an unbroken copy elsewhere cannot hide a
broken one. A "lost" script can also be a run inside text the pipeline turned into an
equation that the run could not transcribe (a `--fake` run has none), so read `lost` with
the equation results. The self-check renders each gold record into the markup the scorers
want (`<sup>`/`<sub>`, `<pre><code>`, `<dl>`, a plain word) and scores that: all four
kinds, every class, 1.0 on 746 script runs, 40 code blocks, 126 glossary entries and 198
hyphenated words. Negative control on the same words: the raw PDF text (hyphen then line
break) scores `joined` 39 and `kept_hyphen` 137 on the LaTeX hyphenation document.

Layout-only starting point on the dev part (`--fake`, 28 pages, the current layout and
assembly, 2026-10-14; the model-free baseline other work packages measure against):

| Class | n | Result |
|---|---|---|
| `prose_unit_power` | 146 | scripted 0, flattened 50, lost 96 |
| `prose_chem_script` | 263 | scripted 0, flattened 128, lost 135 |
| `prose_nuclide` | 79 | scripted 0, flattened 78, lost 1 |
| `prose_script_other` | 258 | scripted 0, flattened 39, lost 219 |
| `code_block` | 40 | exact 0, text_only 33, missing 7 |
| `glossary_entry` | 126 | definition_list 0, text_block 57, found_apart 63, missing 6 |
| `hyphen_break` | 198 | joined 198 |

(The pipeline writes no `<sub>`, `<sup>`, `<pre>` or `<dl>` yet, hence the zeros; the
hyphens are already joined by the 2026-10 hyphen rule, T1-9.)

**Plumbing run (2026-10-14, `--fake`, no model call).** All five tasks ran with `--fake` on both parts and
finished complete, without page errors: on dev equations 1,809 of 1,809, headings 28 of 28
pages, figures 28 of 28, tables 31 of 31 documents, document 28 of 28 pages (338 Letter pages;
0 static and 0 axe violations); on check equations 508, headings 10 pages, figures 11, tables 14
documents, document 10 pages (101 pages). `eval equations --set dev-v2 --self-check` scores
the 1,809 gold equations against themselves 1.0 with 0 errors. The fake client refuses every
model call, so the model tasks score zero by construction (equations: every one `failed`,
exact_lenient 0.000; document: matched_exact_lenient 0.000) and say nothing about quality. The two
tasks that run no model give a first look at how the layout step does here, on the dev part:
headings precision 0.890, recall 0.917, level accuracy 0.898 over 683 gold headings (28 pages);
tables, local mode, detection recall 0.774 and precision 0.911, TEDS 0.718 (31 documents,
53 gold data tables; 52 of 67 layout tables kept out); document, detection precision
0.647 and recall 0.825 (the unmatched predictions are mostly prose math and fragments).
These are layout-only figures from one run, not a baseline anyone has agreed to; the gold
manifest hashes are in the reports (`df760b3f59cd...` dev, `2cea9ea7f587...` check).

**`symbol_mismatch` on perfect transcriptions (T2-14, 2026-10-14, no model).** The review
found 97 `symbol_mismatch` flags on the held-out run, 40 of them on exactly-correct
equations (A16). With no model to spend, the same question can be asked of dev-v2 by
making the transcriber perfect: run the layout and the alignment of the `--fake` document
task (no proposals), pair each region with its gold equation, and apply
`text_layer_symbols_differ` to the region's text layer and the *gold* MathML. Any flag is
then a region error or a text-layer artefact, never a misreading. Of 1,494 aligned pairs
with a text layer, 458 (30.7%) are flagged. By class: inline fractions 196 of 269, numbered
exercises 227 of 319, braced systems 55 of 89, unbraced multi-row 84 of 152, matrices 24 of
63, determinants 25 of 86, nuclides 20 of 62, chemical formulas, charges and equations
23-27%, slide formulas 2 of 45, unclassified 25 of 591. By what differs: 253 text layers
are cut (the region holds fewer symbols than the equation: a stacked fraction or a script
left in its own region), 189 carry extra symbols (181 of the 458 begin with an exercise
number "NN. "; 173 of those pass once the number is removed: the fraction exercises the
T1-5 rule does not catch, because the rest of the row alone is not a display equation), and
16 are both. None is the check's own doing: gold printed alone by Chromium differs from its
own text layer in 2 of 1,809 cases (both are wide equations clipped by the page edge). So
the check is doing its job and the flags follow T1-5 (exercise numbers) and T2-1, T2-2 and
T2-4 (region cuts); nothing in `symbol_mismatch` is separable from them, and it is left as
it is. Re-measure after those land: the same run should fall towards the 4% of the
unclassified class. The new number and case checks (T2-10, T2-9) on the same data: 1 false
number flag in 1,748 gold equations whose letters agree with their own print (a flattened
fraction whose numerator and denominator digits glue to a different number) and 0 case
flags; on the aligned layout pairs 7 number flags and 0 case flags where the bags agree. On
synthetic errors made in the same gold (two adjacent digits of a number swapped, one
letter's case flipped, the text layer left alone) they find 457 of 481 swaps and 1,438 of
1,438 case flips, none of which `symbol_mismatch` could see by construction.

**What this set does not show.**

- It is a development set built to contain failures. Its rates are not estimates for
  course PDFs, and which classes are covered is a choice: chemistry and algebra dominate
  the OpenStax part (no section of the high-school Physics book was needed to reach the class
  targets within the page limit), and calculus, statistics and scanned pages are not in it.
- The classes are defined by structure (MathML shape, sup/sub markup, heading text), not by
  what a reader sees. An equation counted as "inline fraction" is a fraction in a line of
  MathML; whether the Chromium print stacks it as the held-out PDFs did is checked by the
  tasks, not by the label. The class lists are not exhaustive and one item can be in
  several classes.
- OpenStax gold is Chromium-printed, so layout failures that need TeX or Word output
  (equation numbers at the margin, glyphs built from pieces, Word superscript runs) come
  from the authored documents, which are small and written by the project: they test the
  classes by construction, with the same hand that wrote the code.
- Piecewise functions with words are few in the OpenStax part (9 of 30 wanted;
  the one section that holds them is 43 pages, over the page limit); the authored
  documents supply the rest. In the sealed part the nuclide and piecewise items are all from
  authored documents, and it has no large display formula and no code block (dev has 49 and 40,
  all authored).
- The check part is a regression screen, not a held-out set: its authored documents are
  draws from templates the dev part was built from, and its sections come from the same
  books.
- Gold for the prose sub- and superscripts is the page's own markup. An OpenStax paragraph
  that writes a unit with a plain digit (`cm3`) is not in it.

**Structure check** (T2-16, 2026-10-14). `uv run python -m readablestem.eval.structure_check
[--out REPORT.json]` converts the four dev documents that have code or glossary gold
(`html-code-glossary`, `latex-structure`, `word-lecture` and the OpenStax Chemistry key-terms
page) with the offline fake client and reads the output HTML. It reads the dev part and the
dev pages `eval pipeline --set dev-v2` printed into `.cache/eval/pages`; it never opens the
sealed check part. Code: a gold block is *exact* when some `<pre>` holds its lines with
their indentation (trailing spaces aside), *flat* when it does when each line's indentation
is ignored. Glossary: *term found* when a `<dt>` holds the term, *entry right* when the
`<dd>` after it also starts with the first 25 letters and digits of the definition (ASCII
letters and digits only, so the math in a term, a placeholder here, does not decide it).

| Dev part, 126 glossary entries, 40 code blocks | Before (6c8bbda) | After T2-16 |
|---|---|---|
| code blocks written as `<pre>` | 0 | 40 |
| code exact / flat | 0 of 40 / 0 of 40 | 40 of 40 / 40 of 40 |
| glossary `<dl>` entries in the output | 0 | 116 |
| glossary term found / entry right | 0 of 126 / 0 of 126 | 112 of 126 (0.889) / 109 of 126 (0.865) |

Before, code was one or two flowing paragraphs per block ("def bisect(f, lo, hi, tol=1e-9):
# f(lo) and f(hi) must have opposite signs while hi - lo > tol:") and glossary entries
were paragraphs or, with a bold term, headings. The 14 entries not found: the ten of the
Word fixture's unruled two-column table (a glossary set as a table the table step does not
claim) and four OpenStax entries: "half-life (t1/2)" and "positron (...)", whose gold
terms have their math written out as text while the output has it as an equation, and
"nuclide" and "radioisotope", whose term is the last line of a page and definition the first
of the next. The 3 "entry right" misses that remain among the found terms are gold
definitions that begin with a parenthesis of math the fake client cannot convert. The
code is tuned on this set, which is all authored (the dev part has no OpenStax code), so
it shows the shapes handled, not a rate on other documents.

Headings, the same set (`eval pipeline --task headings --set dev-v2 --fake`, no model
call):

| | Before | After T2-7 (sentence rule), T2-8 and the glossary entries |
|---|---|---|
| precision / recall / F1 | 0.892 / 0.917 / 0.904 | 0.914 / 0.919 / 0.917 |
| predicted headings (gold 683) | 702 | 687 |
| required decisions (per PDF page) | 34 (0.101) | 59 (0.175) |
| optional notes | 163 | 124 |
| flag precision (required decisions with a wrong heading in them) | 0.118 (4 of 34) | 0.305 (18 of 59) |
| flag recall (wrong headings that were in a required decision) | 0.114 (16 of 140) | 0.236 (29 of 123) |
| wrong headings not asked about | 124 | 94 |

T2-8 accounts for two matches (two pages of OpenStax Elementary Algebra: "Practice Makes
Perfect" no longer joins the title below it); the glossary entries for the drop from 22
to 7 predicted headings on `html-code-glossary` (the terms are no longer headings) and
2 on the Word lecture; the sentence rule for 26 of the 59 decisions. On the original
development set (`data/gold`, 3 sections of Physics) F1 is unchanged, 0.9344 (precision
0.877, recall 1.000, level accuracy 0.992) and the 9 group decisions are unchanged. Whether
repeated-label groups should stay decisions was measured in four variants on both sets:
docs/layout.md "Review tiers" has the table and the choice (one decision per group).

### Structure fixes (2026-11-02)

Six layout fixes (docs/layout.md: "The PDF's outline", plain numbered lines, run-in labels,
math in headings, page and line joins, the document's spelling of compounds), measured with
the offline tasks only: `eval pipeline --task headings --fake`, `--task document --fake` (no
model call; every call is refused, so its equation numbers are 0 and only layout, headings
and the HTML checks mean anything) and `eval.structure_check`, before (3e2b7cd, a clean
checkout of main) and after (this branch). Gold: the original development set (3 OpenStax
Physics sections, 43 PDF pages, 121 gold headings) and the dev part of dev-v2 (28
documents, 338 PDF pages, 683 gold headings; nothing from the sealed check part). No
held-out data was used.

| | Original set, before | after | dev-v2 dev, before | after |
|---|---|---|---|---|
| heading precision | 0.850 | 0.914 | 0.922 | 0.922 |
| heading recall | 0.793 | 0.793 | 0.917 | 0.917 |
| F1 | 0.821 | 0.850 | 0.919 | 0.919 |
| level accuracy | 0.740 | 0.740 | 0.898 | 0.914 |
| predicted headings | 113 | 105 | 679 | 679 |
| required decisions (headings) | 6 | 4 | 59 | 59 |

- Precision on the original set: the 8 fewer predicted headings are repeated "Solution",
  "Discussion" and "Procedure" labels inside boxes on sections 2.2 and 2.3 (the gold counts
  them as not headings); the 9 other predicted headings the gold lacks stay, among them the
  labels on 2.1, a page set mostly in boxes. Recall does not move. 2 fewer required
  decisions (their groups), and the document task's required decisions go 63 to 61 for the
  same reason.
- Level accuracy on dev-v2: the bookmarks of the authored Word and LaTeX documents
  (`word-lecture`: 17 of its headings, `word-eq-images`: 6) give the author's own levels. The
  OpenStax pages are printed by Chromium without an outline, so the original set cannot
  move from this fix.
- Document task: detection precision, detection recall, split gold equations, static and
  axe violations are identical on both sets (0.732 / 1.000 on 3 sections; 0.672 / 0.870 on 28
  documents). Structure check: identical (40 of 40 code blocks exact, 112 of 126 glossary
  terms found).
- A first version of the label rule (no inset test) lost 10 gold headings of the authored
  documents and cost dev-v2 recall 0.9165 to 0.9019 (F1 0.9192 to 0.9112), and a second
  (inset against the page's edge, not the column's) 2 of them (recall 0.9136). Both were
  dropped for the inset against the column's edge, which loses none: docs/layout.md "Run-in
  labels".
- Not measured by these tasks: page joins, wrapped inline math and compounds (no gold of
  their own). A survey of the 67 PDFs in the repository (authored dev-v2 documents and test
  fixtures, 1 to 8 pages) changed 8 of them: 2 paragraphs joined over a page break (both
  reading right: "... Aluminium" / "sulfate has the formula ..."), no wrapped inline
  equation (none occurs in them), and outline reasons on 35 headings in 6 PDFs. The unit
  tests (`tests/test_layout_structure_fixes.py`) pin each fix on PDFs drawn in the test.
- **The review-load flags moved the wrong way on the original set**, and this is not a
  gain. Flag recall (the share of wrong headings that were in a required decision) fell
  from 0.333 to 0.176 and flag precision from 0.833 to 0.750 (`review_load.flags`, not
  gated). Why: the 8 repeated labels the run-in rule turned into bold text were wrong
  headings that sat in required group decisions; they are now not headings at all, so wrong
  headings fall from 42 to 34 and the wrong ones inside required decisions from 14 to 6,
  but the 28 wrong headings that no decision asks about are unchanged, so the share asked
  about is smaller. A person is asked about fewer headings (6 to 4 required decisions) and
  fewer of them are wrong, which is the point of the rule; what it does not do is catch the
  other wrong headings. On dev-v2 dev the same flags rose (precision 0.305 to 0.424, recall
  0.248 to 0.393, 59 required decisions both times: the bookmarks put some headings right
  that were wrong-level decisions). In the document task on the original set (fake client)
  flag recall goes 0.611 to 0.562 and flag precision 0.972 to 0.971, the same effect.
- `data/eval/latest-headings.json` is replaced with the new run (pipeline 0.5.1, fake
  client; model fields kept from the earlier run because the task calls no model).
  `baseline-headings.json` is **not** replaced: the gate (`heading_recall` -0.030 and
  `heading_precision` -0.030 allowed) passes against the 2026-10-14 baseline (precision
  0.850 to 0.914, recall the same; it notes the pipeline version change), and the ungated
  flags above are the old baseline's to compare with. The document and equation baselines
  and `latest-document.json` come from recorded model runs ($0.28 and up) that cannot be
  made again offline: the document task's required decisions and flags with a real model
  will be a little lower on heading decisions (2 of the original set's heading decisions
  went; the fake run's 63 to 61 is that, not a model number), and are not re-recorded
  here. `make reproduce-rerun` already failed on a clean checkout of main when run here
  with no key (its headings re-run exited with status 2, and its tables numbers differ from
  `latest-tables-local.json`); `eval pipeline --task headings --fake` reproduces the
  numbers above.
- Sample sizes are small and the gold is OpenStax Physics plus authored documents; none of
  this is a rate on other documents. The outline fix needs a PDF that has bookmarks, which
  no OpenStax page print has.

### Prose scripts, nuclides and pictures of equations (T2-3, T2-4, T2-5, 2026-10-14)

Measured on the dev part of this set only (nothing from the check part was opened, and it was
not run), before and after the changes of `docs/layout.md` "Raised and lowered text in prose"
and "Equations pasted as pictures". Before is the code of `feat/launch-integration` (6c8bbda),
after is this branch; both ran the pipeline on the same 17 development documents (the 14
OpenStax sections and authored documents holding chemistry, nuclides, units and the pictured
equations, plus `word-lecture`, `latex-structure` and `1-4-measurements`; 741 of the 746 prose
script runs and every gold equation of the five classes) with claude-opus-5-5 and
claude-haiku-5-5 (the Anthropic defaults). Every model answer was recorded once under the hash
of the whole request; the run after the change took an answer from the record when its request
was identical (the crop, the context and the prompt) and paid only for the new ones: before
$1.664 (960 calls), after $0.453 (221 new calls), $2.117 in all. Two later replays (a
normalisation of `clean_latex`, the reading order of a stacked pair) made no call and cost
nothing.

Prose runs are scored by the `prose_classes` scorer (`eval/prose_classes.py`, from
`feat/t2-scorers`, merged here) on the converted HTML: a run is scripted when the output shows it as
`<sub>`/`<sup>` or as a script inside inline MathML, flattened when its digits are glued on as
text, lost when its context is not there (counts of runs):

| Class | n | before: scripted (sub/sup + math) | flattened | lost | after: scripted | flattened | lost |
|---|---|---|---|---|---|---|---|
| Units with superscripts (`prose_unit_power`) | 146 | 77 (0 + 77) | 55 | 14 | 129 (120 + 9) | 4 | 13 |
| Chemical scripts (`prose_chem_script`) | 263 | 76 (0 + 76) | 144 | 43 | 235 (214 + 21) | 13 | 15 |
| Nuclide prefix scripts (`prose_nuclide`) | 79 | 1 (0 + 1) | 77 | 1 | 78 (77 + 1) | 0 | 1 |
| Other scripts (`prose_script_other`) | 253 | 166 (0 + 166) | 48 | 39 | 198 (49 + 149) | 17 | 38 |
| All | 741 | 320 | 324 | 97 | 640 | 34 | 67 |

What is left is almost all in tables (`1-4-measurements` 25 lost and 10 flattened, `rate-laws`
22 and 18: a cell keeps raised marks as superscript characters, `tables._marked`, which the
scorer does not count, and lowered ones flat; the notice `scripts_flattened` says which
tables), and variables with scripts that stay inline equations (`prose_script_other`: 149 of
the 198 scripted are inline MathML, as before).

Equations are paired to the gold by the document task's alignment (`document_page_item`); for
each gold equation of a class: matched (a predicted equation is paired with it), exact (the
primary scorer, `exact_lenient`), stream v2 (the secondary scorer of `feat/t2-scorers`), and
content-equal (a diagnostic of this work, not a scorer: the LaTeX is equal once `\text` and
`\mathrm`, spacing, braces and the order of a stacked pair of scripts are ignored; the primary
scorer calls the authored `\mathrm{Th}` and a model's `\text{Th}` different, the OpenStax gold
writes `<mtext>`):

| Class | n | before: matched / exact / v2 / content-equal / required | after: matched / exact / v2 / content-equal / required | written as sup/sub text instead |
|---|---|---|---|---|
| Chemical formulas | 211 | 143 / 11 / 40 / 32 / 32 | 125 / 8 / 37 / 28 / 18 | 20 |
| Charges on ions | 111 | 75 / 0 / 12 / 11 / 20 | 65 / 0 / 12 / 11 / 11 | 10 |
| Chemical equations | 148 | 95 / 2 / 27 / 17 / 16 | 89 / 2 / 27 / 17 / 11 | 0 |
| Nuclide notation | 98 | 69 / 21 / 31 / 36 / 35 | 70 / 20 / 30 / 45 / 11 | 0 |
| Equations as pictures | 37 | 0 / 0 / 0 / 0 / 0 | 37 / 33 / 32 / - / 37 | 0 |

- **Equations as pictures.** Before, the 37 pictures of `word-eq-images` were 34 figures (3 were
  dropped silently, a one-letter picture is under the 12 pt limit), all accepted by the figure
  step (33 with no review, 1 optional): no equation was read or checked. After, all 37 are
  equations (9 inline in their sentence, 28 displays, the equation numbers as labels), 33 read
  exactly, and each is a required decision (`equation.no_text_layer`). In four OpenStax algebra
  sections that are not in the live set (layout only, no model) 56 pictures that were figures
  are now equations: 26 in "Solve systems of equations by substitution", 26 in "Simplify
  complex rational expressions", 3 in Cramer's rule and 1 in "Sequences" (two sequences joined
  by arrows). A crop of each was looked at: all are pictures of equations, and the one
  picture that was not (a right triangle with two letters) is kept as a figure by the rule
  that a picture on a line of its own has at least three pieces of ink.
- **Chemical formulas and ions** that the PDF cannot tell from running text (`H$_2$O` and
  `$\mathrm{H_2O}$ ` are the same glyphs in a TeX PDF) are text with `<sub>`/`<sup>` now, so
  they leave the equation counts: 20 formulas and 10 charges of the authored documents are
  found only as raised or lowered text (matched, exact and v2 fall by that much), and the model
  is no longer asked about half a formula ("2O3", "34-"): 238 fewer model calls over the 17
  documents (960 to 722), 48 fewer required decisions (212 to 164, with the 37 of the pictures
  added), the recorded cost of the documents $1.665 to $1.462.
- **Nuclide notation.** A display nuclear equation used to be cut into four pieces (the mass
  numbers a heading, the symbol of the last nuclide lost); each is one equation now, and 45
  of 98 nuclide equations are content-equal against 36. 22 more are cells of the table
  "Nuclear Compositions of Atoms" in `2-3-atomic-structure-and-symbolism` and are found in
  neither run. The model also leaves out the
  empty group before a prefix script (`+ ^{4}_{2}He`, which LaTeX hangs on the plus sign);
  `clean_latex` adds `{}`, which gave back 5 equations that read right and scored wrong.
- **Where the equation classes do not move.** The scorers make most of the gap: primary exact
  stays low because the authored gold writes `\mathrm{...}` and the model `\text{...}` (and
  the speech of `\text` is the better one: "H sub 2 O", not "capital H sub 2 capital O"), and many
  gold equations of the OpenStax chemistry sections are cells of tables (the half-reactions
  of `16-3-standard-reduction-potentials`: 36 not found, before and after). The classes
  these changes were built for are the scripts and the pictures.

No gold file, golden output or held-out number changed. Three tests that encoded the old
behavior were rewritten (`test_layout_analyze`: an upright x with a raised 2 is raised text,
not math; `test_layout_fixtures`: "3 m/s2" in a text font is raised text; `test_layout_silent_loss`:
a picture of math is held as an equation, not as a figure).

## Model cost (2026-10-14; T3-22, T3-23, T3-24, T3-29, T2-19, T3-21)

Six cost levers for the Claude path, each a setting, each measured against one shared
baseline on the development sets only (nothing here was run on the held-out set or the sealed
check part). Baseline: `claude-opus-5-5` main at effort medium, `claude-haiku-5-5` cheap at low,
the code and prompts of this branch before the settings (pipeline 0.4.0). Every arm changed one
thing. A model answer was asked once and kept (`--answers`, above), so an arm paid only for
requests the baseline had not asked; the runs were made as Message Batches (T3-21), at half
price. Costs below are at list price, with the instructions read from the prompt cache
(batch and live runs differ in whether a call found the cache warm, which depends on what ran
before it; the "as billed" cost of two arms is not comparable, the cache-warm one is). The
sets: the original development set (30 equations, 13 figures, 43 PDF pages in the document
task), and the dev part of dev-v2 (302 equations, every 6th; 91 of them every 20th; 14 figures,
every 2nd). Spend: about $2.0 of the $6 cap (billed), of which the baseline about $0.75, the
effort arm $0.37, the Sonnet arm $0.39, the other arms under $0.05 each (they reused the
baseline's requests), and the live cache and judge checks about $0.45.

**Headings do not move.** The headings task calls no model, so none of these settings changes
it: original set precision 0.877, recall 1.000, F1 0.934, level accuracy 0.992; dev-v2 dev
precision 0.892, recall 0.917, F1 0.904, level accuracy 0.898 (all arms).

Equations, `exact_lenient` / decisions asked of a person / cost per equation (n in brackets).
"Silent" is a wrong equation (scorer gaps left out) that no one is asked about.

| Arm | Original (30) | dev-v2 dev (302) | silent, dev-v2 | Document task, per PDF page (43 pages) |
|---|---|---|---|---|
| Baseline | 0.767 / 1 / $0.00207 | 0.533 / 21 / $0.00163 | 128 | $0.00430; equations 0.767; 10 decisions |
| Effort low for equations and figures (T3-22) | 0.767 / 0 / $0.00207 | (91) 0.527 / 5 / $0.00187 vs baseline 0.527 / 7 / $0.00189 | (91) 39 vs 37 | $0.00429 |
| Haiku first for display equations (T3-29) | 0.767 / 1 / $0.00050 | 0.520 / 12 / $0.00055 | 135 | $0.00313 (-27%); equations 0.767; 10 decisions |
| Context only on escalation (T3-23) | 0.733 / 1 / $0.00206 | (91) 0.538 / 7 / $0.00191 vs 0.527 / 7 / $0.00189 | (91) 36 vs 37 | not run |
| Sonnet 5.5 as main model (T2-19), bar 0.85 | 0.767 / 8 / $0.00116 | 0.546 / 39 / $0.00090 | 113 | $0.00236 (-45%); equations 0.767; 28 decisions |
| Sonnet 5.5 with a bar of 0.80 for it | 0.767 / 0 / $0.00116 | 0.546 / 12 / $0.00090 | 129 | $0.00236; 12 decisions |
| Figure crops at most 768 px (T3-24) | not run (see below) | not run | | $0.00369 (-14%); equations 0.767; 10 decisions |
| Notice in the system block (T3-23) | 10 sampled: same answers | | | about $0.00405 (-5.6%, computed) |

Figures, judge pass rate / mean judge score / accepted without review / cost per figure:

| Arm | Original (13) | dev-v2 dev (14) |
|---|---|---|
| Baseline | 1.00 / 4.77 / 13 / $0.00352 | 1.00 / 4.50 / 11 / $0.00482 |
| Effort low | 1.00 / 4.77 / 12 / $0.00350 | 1.00 / 4.50 / 13 / $0.00484 |
| Figure crops at 768 px | identical to baseline (all images are smaller than 768 px) | not run |
| Sonnet 5.5 as main model | 1.00 / 4.54 / 6 / $0.00194 | 0.83 / 4.17 / 8 / $0.00258 |
| Notice in the system block (13, live) | 1.00 / 4.77 vs 4.69 / 12 vs 13 / -6.3% | not run |

What each result says:

- **T3-22, lower effort: no saving, leave it off.** The models think very little on these
  crops (an equation answer is about 80 output tokens at medium as at low), so effort does not
  move the bill: 0.0% on the original set, 1% on dev-v2, 0.2% per page. Answers are the same.
  The setting stays (`ANTHROPIC_EFFORT_EQUATION`, `_FIGURE`), unset.
- **T3-23, notice in the system block: kept, on by default.** The fixed notice (61 tokens) is
  cached with the instructions instead of being billed as input in every request. Live, in order
  and with the cache warm: an Opus equation call $0.00239 to $0.00216 (-9.6%), a 13-figure run
  $0.0456 to $0.0427 (-6.3%), cache-read share of a call 87% to 90%, and the same answers (10
  equations: the same correct and wrong ones; 8 of 10 the same LaTeX, as many as two live runs of
  the baseline give; figures judged 13 of 13 either way). The first call of a new prefix writes
  the cache (3,922 tokens at 1.25x); it did so in the baseline too, earlier. Turn off with
  `ANTHROPIC_NOTICE_IN_SYSTEM=false`. The second part, **context only on escalation**, saved
  nothing (a cheap call without the sentence is 100 tokens shorter, and one more equation in
  dev-v2 was re-read), and on the original set one equation of 30 got worse: off.
- **T3-24, figure crops at 768 px: off, needs a test the sets cannot give.** The gold figures
  are OpenStax web images of at most 500 px, so on the figures task the setting changes nothing
  (the requests are the baseline's). In the document task the crops are 200 dpi renders of the
  page: a page costs 14% less, the 13 captioned ones all pass the figure judge
  (mean score 4.77 to 4.69, one figure one point lower), and the status and confidence of every
  figure are the same. No dense chart or slide is in the sets, which is where a smaller picture
  loses text, so it stays off until such a figure is tested; 1,024 is the safer first value.
- **T3-29, Haiku first for display equations: off.** Cost per equation falls 66-76% and per
  page 27%, and accuracy is the same within noise (paired on dev-v2: 4 equations worse, none
  better, of 302; original set identical). What it loses is the review signal: the cheap model
  is confident more often than the main model, so fewer answers fall under the accept bar (12
  decisions against 21) and 7 more wrong equations are not asked about (135 against 128 of 302).
  The main model re-reads what the cheap one is unsure of, but not what it is wrongly sure of.
  It becomes a good trade when a bar for the cheap model's display answers is calibrated.
- **T2-19, Sonnet 5.5 as main model, dev sets only: cheaper and as accurate, but not at the
  common bar, and weaker on figures.** Equations are as accurate (0.546 against 0.533 on dev-v2:
  4 better, none worse), 45% cheaper, and per page $0.00430 to $0.00236. Its confidences sit
  lower (many at 0.80-0.82 where Opus says 0.88-0.95; high or low, they do not separate right
  from wrong in either model), so at the 0.85 bar it asks for 39 decisions instead of 21 on
  dev-v2, 28 instead of 10 on the document task, and 5 of 16 figures need review. With
  `ACCEPT_CONFIDENCE_BY_MODEL='{"claude-sonnet-5-5": 0.80}'` the load is the baseline's or less
  (12 of 302, 12 of 43 pages) and the wrong equations nobody is asked about are 129 against 128.
  Figures are the cost: judge pass 0.83 against 1.00 on dev-v2 and mean score 4.17 against 4.50
  (4.54 against 4.77 on the original set), on 14 and 13 figures, so one or two figures. The page
  crops in the document task passed the judge 13 of 13. So: 0.80 is evidence from three small
  development sets and calibrates nothing; no default changed. The setting
  (`accept_confidence_by_model`) is in and empty, and the main model is the existing
  `ANTHROPIC_MAIN_MODEL`. Not run on any held-out set.
- **T3-21, batches.** The same requests as a live call (`batch_params`), less the refusal
  fallback. Batch against live on the same 10 equations: 8 of 10 the same LaTeX and the same
  correctness for all 10 (two live runs differ as much). A staged run takes several stages of minutes each and half the price; one stage took 45
  minutes when the queue was long. A dropped
  connection while polling lost the results of one batch (about $0.05) before the runner
  retried such calls; it does now.

Sample sizes: 30 equations is 3.3 points per equation, and the 13 and 14 figures are at the
judge's ceiling (1.00), so figure quality moves only when the judge fails one, which makes the
figure comparison weak on both sets. The dev-v2 equation set was chosen to hold failures (its
exact rate is 0.53), so its rates are not course PDFs' rates.

## Doubled box headings (2026-10-14): heading F1 0.934 to 0.821 on purpose

OpenStax prints a box's label and its title as two lines ("Teacher Support" at 14 pt, then
"Teacher Support" again at 12 pt). Both are headings in the PDF and in OpenStax's own markup,
which is the gold: 50 of the 121 gold headings on the original set are these pairs. A
screen-reader user who navigates by headings hears the same name twice in a row, seven times
on the public demo (OpenStax Physics 2.3). The heading step (`heading_step.repeated_above`)
now writes a heading whose text equals the heading directly above it, with nothing between
them, as bold text, whatever its level. The same text with any element between the two copies,
repeated-label groups, the same-level rule and the sentence rule are as before. T1-13 had
narrowed the rule to the same level only, because the gold counts both lines and the broader
rule lowered the score; the product decision (2026-10-14) is that the outline a student hears
matters more than agreement with that quirk of the gold. No model is called by the headings
task, so these are exact re-runs (`eval pipeline --task headings --fake`; the fake client is
not used by this task).

| Headings, original set (3 pages) | before | after |
|---|---|---|
| precision | 0.877 | 0.850 |
| recall | 1.000 | 0.793 |
| F1 | 0.934 | 0.821 |
| level accuracy | 0.992 | 0.740 |
| predicted / matched (gold 121) | 138 / 121 | 113 / 96 |
| required decisions | 9 | 6 |
| flag precision / recall | 0.556 / 0.778 | 0.833 / 0.333 |

| Headings, dev-v2 dev part (28 pages) | before | after |
|---|---|---|
| precision / recall / F1 | 0.921 / 0.919 / 0.920 | the same |
| level accuracy | 0.898 | 0.898 |
| required decisions | 59 | 59 |

Where the drop is: `recall_by_context` for the Teacher Support boxes (`os-teacher`) goes from
1.00 to 0.50, every other context stays at 1.00. The 25 missed gold headings are the second
copies of the pairs. The level accuracy drop is the 24 surviving "Teacher Support" lines that
the scorer pairs with the gold's second copy (gold h4, relative level 2 below the page's top
heading, against our h2). The dev-v2 dev part does not move: the rule changes none of its headings.
The eleven-fold "Teacher Support" group decision on the demo's h3 lines also goes: the demo's
required decisions fall from 3 to 2 (its stored result is decided again by
`readablestem demo rerender`, no model). `data/eval/baseline-headings.json` and
`latest-headings.json` were replaced with the new run on purpose (the gate compares them with
each other, `heading_recall` and `heading_precision`); their model fields are those of the
earlier run because the task calls no model. Nothing under `proof/` or `PROOF.md` changed.

## Baselines recorded again (2026-10-14, after the rename)

The evaluation pages carry the product's name in their footer, so the rename changed the
page images and every recorded model answer missed. The three model baselines were recorded
again, once each, on Claude (`claude-opus-5-5` main, `claude-haiku-5-5` cheap, Opus as figure
judge, the second check on `claude-sonnet-5-5`), with `--answers .local/answers-baseline.jsonl`,
and promoted with `eval promote ... --out-dir data/eval` as baseline and latest:

| Task | Result | Before (earlier run) | Cost of the run |
|---|---|---|---|
| equations (30) | exact_lenient 0.733 (22 of 30), mean TEDS 0.870 | 0.767, 0.867 | $0.0819 (second check $0.0223) |
| figures (13) | judge pass 1.000, mean judge score 4.69 | 1.000, 4.77 | $0.1167 (judge $0.0545) |
| document (43 PDF pages, 3 sections) | end to end exact_lenient 0.767, detection recall 1.000, precision 0.750, 0 axe violations | 0.767, 1.000, 0.750 | $0.2366 |

The one-equation difference in the equations task is the run-to-run variation of the model,
not a change of code. Headings and tables (local mode) call no model and were recorded again
without cost. The public demo and the site examples were written again from a new conversion
(`demo build`): $0.0955 (Physics 2.3), $0.0729 (2.1) and $0.0676 (2.2). A first set of
conversions ($0.2744 in all) was discarded because the table-structure model was not found on
the machine, which changed the layout of 2.1; it is not used.

## The test corpus

The workflow tests and the layout evals read one set of files, listed in
`tests/workflows/corpus/corpus.json` and read through `src/readablestem/eval/corpus.py`.
As of 2026-11-11 it has 72 entries; 47 of them have reading-order labels (91 pages).

**Where a file may come from.** Every entry is one of three kinds.

- `public` (27 entries): a file on MIT OpenCourseWare (25) or arXiv (2). The
  manifest pins the URL, the SHA-256, the size, the license and who to credit. The file is
  fetched into `.cache/corpus/` (not committed) and the tests use a page range cut from it.
  A download whose hash differs is refused and never reaches the cache.
- `committed` (34 entries): a file in the corpus folder. 29 were made for this repository
  and sit next to the HTML, LaTeX, Markdown or Word source they were printed from; each is
  at most 200,000 bytes. 2 are ten-page cuts of OpenStax textbooks, kept because the
  publisher's download URLs stopped serving the bytes that were measured. 3 are files from
  Wikimedia Commons (CC BY 4.0, CC0 and public domain), kept because Wikimedia answers a
  CI runner's download with "too many requests". Someone else's file is committed only
  under one of those three licenses, with its credit, and is at most 5,000,000 bytes.
- `builder` (11 entries): a file made on demand by a builder in `tests/workflows/builders/`.
  The builders make 71 files in all; 68 give the same bytes on every run, and their hashes
  are in `builders/recorded.json`. The 3 encrypted ones cannot, and are checked by what
  they contain.

**Rules.**

1. No file from an instructor's course, and nothing from the private audit corpus, is
   committed, fetched or named. A manifest line that holds a path on one machine, or a URL
   outside the allowed sites, fails to load.
2. Someone else's file is committed only when its license is CC BY 4.0, CC0 or public
   domain, with the attribution in the manifest and in the corpus README, and only up to
   5,000,000 bytes. CC BY-NC-SA files (MIT OpenCourseWare) are fetched, never committed.
   A model's recorded answer about such a file repeats what the file says (its equations,
   its tables, a page read from a scan), so those answers are kept out of the repository
   too ("Recording and replay" below).
3. A URL is pinned to one version. arXiv entries name the version (`v1`, `v4`).
4. Only files listed here, or files a builder makes, are sent to a model.

**Gold labels.** A gold file gives, for each page, the blocks in reading order, each with a
role (heading, body, list, table, caption, footnote, aside, form, figure, or dropped for
text that must not be read, such as a running header). For files made here the block text is
stored. For anyone else's file no text is stored: a block is a list of character ranges into
the page's words (PyMuPDF's words in file order, NFKC-normalized, joined with nothing
between), the lengths of its tokens, and the SHA-256 of its text. `load_gold` rebuilds the
text from the file and checks every hash, so a different file, or a PyMuPDF that reads the
page differently, fails loudly instead of scoring against the wrong text. Where the labeled
text is not among the page's words (chart axis labels and some mathematical glyphs), the
characters are stored as written: 390 characters over 682 blocks.

The public-page labels were made on 2026-11-10 from candidate blocks produced by poppler's
`pdftotext -bbox-layout`, which is independent of this project's layout step; order and
roles were set by reading each rendered page, in one pass, with four blocks corrected in
review. They were not labeled twice by independent readers. Labels for files made here
come from the source each file was printed from. The manifest's `labels` table records the
method and date for every gold file.

**Commands.**

```bash
uv run python -m readablestem.eval.corpus check            # committed and built files, no network
uv run python -m readablestem.eval.corpus check --fetch    # also fetch the 27 public files by hash
uv run python tests/workflows/builders/record.py list      # the 71 built files and what each shows
uv run pytest tests/workflows/test_corpus_manifest.py      # add -m "slow or network" for the rest
```

### Recording and replay

The workflow tests (`tests/workflows`, `pytest -m workflow`) upload each corpus file to the
real app, with production's behavior settings (`ops/production.profile.toml`), and convert
it from recorded model answers. Nothing is sent to a model. A conversion that asks a
question the recording does not hold fails its test with the step and what changed since
the recording (a prompt, a model, a picture), so a change to what is sent to a model shows
up as a named miss and never as a silent call.

**Two files.** The recording is split by the license of the file an answer was read from:

| File | Holds | As of 2026-11-11 |
|---|---|---|
| `tests/workflows/corpus/answers.jsonl.gz` (committed) | answers read from files made here, and from CC BY 4.0, CC0 and public-domain files | 270 answers |
| `.cache/corpus/answers.jsonl.gz` (not committed; `$CORPUS_CACHE` moves the folder) | answers read from the 25 CC BY-NC-SA files | 1,303 answers |

An answer a committable file and a kept-out file both read goes to the second file. Where
the second file is absent, as in CI, a test that converts a CC BY-NC-SA file is skipped and
says why; the five workflows built first (typed notes, dense solutions, slides, a chemistry
exam, handwriting and scans) all read MIT OpenCourseWare files, so they run only where
that file is. The recorded cost of the 1,713 answers one full replay reads is $5.10; a
replay pays nothing.

**Answers still owed.** Ten files ask for 126 answers that were never recorded (pictures
that changed since the first recording, and files added after it). `harness.OWED` lists
them with the count for each, and the replay test fails if a file misses more or fewer than
its count. Their conversions still finish: a part with no answer is left for the reviewer,
as when a model is unavailable.

**Refreshing.** `scripts/corpus_refresh.py` runs the same tests against a working copy of
the recording and writes back what they read; an answer no test read is dropped.

```bash
uv run python scripts/corpus_refresh.py --check                # replay; write nothing
uv run python scripts/corpus_refresh.py                        # replay; drop unread answers
uv run python scripts/corpus_refresh.py --from OLD.jsonl       # move an older recording to today's keys
uv run python scripts/corpus_refresh.py --live --max-usd 0.50  # pay for what is missing
```

Only `--live` can spend, and it needs `--max-usd`: the most the run may pay for answers the
recording lacks, refused above $1.00. The limit is enforced where a real request is made
(`MODEL_ANSWERS_LIVE_USD`, `eval/answers.py`), because the app's daily cap counts replayed
costs as well. Past the limit a missing answer is refused and stays missing. The script
prints what was read, what was missing and why, and what was paid.

The answers were recorded with the table-structure model present (`make table-model`), as
in the deployed image. Without it tables are read another way, the questions differ, and
the tests say so instead of running.

**Marking an open finding.** A workflow test that a known, unfixed finding makes false is
marked `@harness.finding("F-nnn")`. The mark is a strict expected failure: when the fix
lands the test passes, the run fails, and the same change removes the mark. A finding
cannot be fixed by accident and stay marked open. The audit's register ends at F-558; a
finding met while the plan is built takes the next number.

### The hostile-input set (`tests/workflows/hostile_set.py`)

Files made to hurt the app, each from a builder (`tests/workflows/builders/hostile.py` and
`hostile_sources.py`), and for each the one way it must end: refused at upload with a named
code, kept for choosing its pages, or queued and ended `done` or `failed` with a named code.
`tests/workflows/test_wf49_hostile_input.py` uploads each file under production's size
limits while a second person uploads a two-page syllabus behind it, and checks four things:

| Check | What must hold |
|---|---|
| ends | the file ends as its case states |
| bound | the upload request and the job together take no more than the case's time (30 s for a job, 10 s for a refusal), and no process holds more than 600 MB |
| next | the second person's file is converted, from the recording alone, no later than the hostile job's bound plus 15 s |
| planted | a string the builder planted where it could run (a script, a link that runs its target, an open action) is in no page or download and no log line; planted markup is only ever text; no page or download has a handler attribute or a link that runs its target |

The queue is run by a worker in a process of its own, stopped after twice the bound, so a
file that hangs the worker or ends it fails its case and not the run. A LaTeX source runs
once under bubblewrap and once under Landlock, where the machine can start each.

As of 2026-11-11 the set has 36 files: 6 on the size of pages and pictures, 3 on page
count, 8 on the structure of the PDF, 5 on content that must stay data, 8 zips and 6 LaTeX
sources. All 36 end as stated. The one that did not, a page whose content inflates to 256 MB
from a 260 KB file (F-559), was taken and read for 7 s in the upload request and 24 s by the
worker; the content budget (docs/security.md) now refuses it at upload in under a second.

Owed:

- **That a model was not steered.** Three files carry text that tells a model what to
  write. The check reads the model's answers, and none is recorded for them (two a file).
  One recording under a cap of $0.25 pays for them; until then the set shows only that
  their output is safe.
- **Cases for readers the app does not have yet.** A second PDF text extractor, readers
  for Word, PowerPoint and notebook files, a structure checker and a TeX compile step each
  add their cases in the change that adds the reader (docs/dependencies.md names the test
  for each).
- **One slice of waiting.** With one worker the second person waits for the whole hostile
  job. When the queue has slots the `next` check becomes one page slice.

## Reading order (`eval/order.py`)

`src/readablestem/eval/order.py` scores what a listener hears on a page against the gold
blocks of "The test corpus". It takes the output as a list of elements in reading order,
made from a layout dump, from a conversion's element list or from the student HTML, and
never looks at boxes, so the layout step, the finished HTML and any other reader are scored
the same way. Its counts per page:

| Count | Meaning |
|---|---|
| found | at least half of a gold block's words are on the page |
| flow | the block's word 4-grams that appear unbroken in the page's word stream |
| broken | a block of 6 or more words with other text inside it, or under 80% of its 4-grams kept |
| neighbors in order | consecutive gold blocks whose places in the stream are in gold order |
| cross-join | an element holding text of two gold blocks that are not neighbors |
| leaked | text that must not be read (a running header, a page number) that is in the output |
| clean page | no broken block, every neighboring pair in order, no cross-join, half the blocks found |
| right page | clean, and no margin stamp read inside text, no index entries joined, numbered blanks in order |
| stray | a text element none of whose words any gold block accounts for |
| furniture | a stray element that is the page's furniture: the words of a block that must not be read, or of a line printed in the page's margins (a running head, a page number, a stamp) |
| cut | an unbroken block of 6 or more words heard in two or more elements |
| merged | neighboring gold blocks heard in one element; `heading merged` is the part where one of the two is a heading |
| caption wrong | a caption heard with, or nearest to, another picture or table than its own |
| faultless page | a right page with none of the five |

The module text gives the exact rules. A "clean page" is strict: one cut sentence or one
swapped pair fails the page. A right page can still read a running head aloud, cut a
paragraph in two, run two list items together or put a caption on the wrong picture, which
is what the last six rows count, so pages right is never reported alone. Two kinds of gold
are judged differently where they must be. Gold built from a source does not hold every
word of the page (a table's cells, a formula's line), so there a faultless page is one with
no furniture read, in place of no stray text. Gold labeled by hand may hold two paragraphs
in one block, so there only a cut inside a sentence is counted, and a block that ends inside
a sentence is not merged with the one after it.

None of the counts sees a formula missing from a sentence or from a line of its own. The
gold marks the tokens of a block that are not source words, but such a token is also a
drawing's label or another column's word on the same line, so it does not say where a
formula stands. The equations eval and the page audit count that.

**How the student page is heard.** `elements_from_html` reads the page as a screen reader
does, cut at the page markers: a block element is one element, a formula's letters and
digits stand side by side as the page prints them ("CH3", not "C H 3"), and the picture of
an equation that was not converted is heard as the PDF's own text of it, which is its alt
text. Text hidden from a voice is not heard. The writer's own words (a note that an equation
was not converted, a label, the title it puts above a document that has no first-level
heading) are not counted as text of any page. In a conversion's element list
(`elements_from_result`) a table is heard as the grid that is written, row by row, and not
as the words the file has in its region.

The scorer is the research scorer (version 4) moved into the package. Before it was
committed it was run next to the research script on every stored output of the layout
studies: 26 runs, 2,133 page rows, every count and every listed fault identical. On the
layout output of the main branch as stored on 2026-11-10, both give 59 clean pages of 78
(30 of 41 on public files, 29 of 37 on files made here) and 6 of 13 on the edge files.
Those numbers describe that stored output, read from a layout dump; they were not measured
under the production profile and are not the registered baseline, which `eval order`
records (below). Version 5 added the five further counts and `faultless`; for the same
elements its other counts are those of version 4. The first scorer (version 2) was not
moved: its element-level counts are still reported by this one (`pairs`, `stitched`,
`split`, `lost`).

### Gold built from sources (`gold/layout_gold.py`)

Hand labeling cannot supply held-out pages in number: the 78 hand-labeled pages of the
corpus have all been used to build readers. So reading-order gold is also built from a
document's source, with no model. The source gives the units (headings, paragraphs, list
items, captions, footnotes) in order: the HTML LaTeXML makes from a paper's LaTeX, the
archive XHTML of an OpenStax section, or the unit list written next to a file made here.
Every text line of a PDF page is matched to the unit that holds its words. A gold block is
the run of lines of one unit in one column of one page, so a paragraph cut by a page or a
column is two blocks, and a cut anywhere else is a reader's error. The caption of a float is
marked `float` (whole and unjoined is scored, its place is not), a footnote goes to the end
of its page, and a token that is not a word of the source (the glyphs of an inline formula)
is masked for the scorer.

Pages are chosen by rules that look only at the gold's own coverage, fixed before any
reader was scored: a paper gives pages 1 to 3 and two from its middle; a textbook gives the
first three well-covered pages of two section groups, four chapter-end pages and one index
page; a page needs three blocks whose order is scored, and a paper's page needs the source to
account for 0.75 of its prose lines.

A set is a folder outside the repository (`files/<id>.pdf` and `gold/<id>.json`, by default
under `~/.cache/readablestem/layout-gold/<set>/`). The repository keeps the set's registry,
`data/gold/layout/<set>.json`: ids, sources, counts and SHA-256 hashes, and no text.

```bash
uv run readablestem gold layout verify --set layout-dev-1
uv run readablestem gold layout sample --set layout-dev-1 --seed check-1 --out ~/layout-check
uv run readablestem gold layout agreement ~/layout-check/sheet.jsonl
```

`sample` draws 1 page in 10 of each kind by a seeded hash and writes each page's picture
with the gold blocks drawn and numbered, and `sheet.jsonl` with one row per block. A person
sets `ok` on each row: true when the block is one whole unit of the page, has the right
role and stands in its place. `agreement` reads the sheet: a kind whose checked blocks are
right in fewer than 95 of 100, or whose sheet is not filled, is not used as gold until its
matcher is fixed.

| Set | Documents | Pages | Blocks | Use |
|---|---|---|---|---|
| `layout-dev-1` | 28 arXiv papers, 10 OpenStax books, 22 files made here | 267 | 4,460 | development |
| `layout-heldout-1` | 28 arXiv papers, 8 OpenStax books, 15 course handouts | 256 | 3,811 | held-out |

`layout-dev-1` was built in the second measuring round of November 2026 by research scripts,
and `layout_gold.py` rebuilds all of it value for value from the kept sources. Half of it
was scored once as a sealed half and its failing pages were then used as cases, so all of it
is development now.

`layout-heldout-1` was drawn on November 11, 2026 from documents that are in no other set,
by three commands that fetch, build and choose with no model and no person in the loop:

```bash
uv run readablestem gold layout draw-papers --set layout-heldout-1 --seed SEED --pool pool.json
uv run readablestem gold layout draw-books --set layout-heldout-1 --seed SEED
uv run readablestem gold layout draw-course --set layout-heldout-1 --seed SEED
uv run readablestem gold layout register --set layout-heldout-1 --side held-out --note "..."
```

- `draw-papers` walks a pool of openly licensed arXiv papers in an order the seed fixes,
  takes four per class family (article, revtex, AMS, IEEE, conference, journal,
  other), downloads the source, and runs LaTeXML and the PDF build in the repository's
  sandbox (`gold/safe_tex.py`): a downloaded source is a stranger's program. 28 papers were
  taken and 123 passed over, by rule: their family was full, the source did not build, or no
  page met the coverage rule.
- `draw-books` walks the CC BY OpenStax books that are in no other set (8 taken, 6 passed
  over), and `draw-course` walks handouts and notes written in LaTeX in public repositories
  under MIT or CC BY-SA licenses (15 taken, 49 passed over).
- The seed is `layout-heldout-1-2026-11-11`. The registry lists every document taken, with
  its source and license, and names every one passed over.

The rebuilt fetch path was compared with the research set on the papers both can build: 91
of 110 pages and 1,045 of 1,070 blocks are identical. The papers of `layout-dev-1` took
their HTML from ar5iv, which runs another LaTeXML version; the differences are blocks that
version cuts differently.

No page of the held-out set was opened while it was built, and none is while the reader is
worked on: `eval bar` reads its scores, and a failing page is not looked at. What it still
lacks, and what a reading-order bar met on it therefore does not cover yet:

- Word-made documents (at least 20 pages) and hand-labeled pages (at least 20): neither
  source can be built by a rule, so both wait for a person.
- The hand check: `sample` and `agreement` on 1 page in 10 of each kind, by someone other
  than whoever changes the reader. Until it is done, a kind's gold is unchecked.
- Three of the eight books are not science or mathematics (government, psychology,
  economics): the walk takes books in seeded order and does not choose by subject. Their
  pages count as textbook layout, not as mathematics.

### `eval order`

Whether each gold page is read in the order it was written, and what else is wrong on a page
that is.

```bash
uv run readablestem eval order --out data/eval/order-corpus.json
uv run readablestem eval order --out order.json --against data/eval/order-corpus.json
uv run readablestem eval order --set layout-dev-1 --out data/eval/order-layout-dev-1.json
```

**On the corpus** the command is made from the measured replay of the next section: every
corpus file with gold is converted as the worker converts a job, under the production
profile, with the recorded answers in place of the provider. It spends nothing. Two things
are scored against the same gold:

- the **student page**: the HTML a student is given, read as a listener hears it. This is
  the product, and the headline is of it;
- the **element list**: what the pipeline holds before any file is written. Where the two
  differ, writing the page changed what is heard.

The groups are the manifest's (`labels.group`): `real` pages of published documents,
labeled by hand; `self-made` pages built from a source that carries each block's role;
`edge` pages generated to hold one hard case each. A figure "of 78" is of the real and the
self-made pages together. A file whose recording does not hold every answer its conversion
asks for is reported apart and left out of every total: an element whose answer is missing
fails, which is the recording's doing and not the reader's.

**The bar.** With `--against`, no page that was right in the earlier record is not right
now; a page that became right is named too. Two records are compared only when they are of
the same documents and were scored by the same version of the scorer, and every page scored
then must be scored now. Exit code 0: the bar is met, or none was asked; 1: a page was made
wrong; 2: the two records cannot be compared, or nothing could be measured. The record
keeps counts per page and no word of any document.

**The baseline** (`data/eval/order-corpus.json`, 44 files measured
whole, 88 gold pages, pipeline 0.5.10,
production profile `62b4f1678d36`,
no step stopped early). A count is given as its pages and, in brackets, its places.

| | Pages | Right | Faultless | Furniture read | Cut | Merged | Caption wrong | Broken blocks | Cross-joins |
|---|---|---|---|---|---|---|---|---|---|
| real, student page | 41 | 30 | 12 | 8 (10) | 15 (32) | 10 (19) | 1 of 2 | 11 | 4 |
| self-made, student page | 37 | 28 | 21 | 4 (4) | 1 (4) | 3 (27) | 0 of 2 | 32 | 13 |
| real and self-made, student page | 78 | 58 | 33 | 12 (14) | 16 (36) | 13 (46) | 1 of 4 | 43 | 17 |
| edge, student page | 10 | 6 | 4 | 3 (4) | 1 (1) | 0 (0) | 0 of 0 | 1 | 0 |
| real, element list | 41 | 30 | 12 | 8 (10) | 15 (32) | 10 (19) | 1 of 2 | 11 | 4 |
| self-made, element list | 37 | 28 | 21 | 4 (4) | 1 (4) | 3 (27) | 0 of 2 | 32 | 13 |
| real and self-made, element list | 78 | 58 | 33 | 12 (14) | 16 (36) | 13 (46) | 1 of 4 | 43 | 17 |
| edge, element list | 10 | 6 | 4 | 3 (4) | 1 (1) | 0 (0) | 0 of 0 | 1 | 0 |

Read with care:

- 58 of 78 pages are right in the student page and in the element list, and 33 are
  faultless. 12 of the 78 read a running head, a page number
  or a stamp aloud, 16 cut a block and
  13 run blocks together.
- Two labels were corrected on November 11, and the figures above are scored with them. A
  lab manual's first page had a block whose label read a Greek letter as the file's text
  layer has it, a Latin one; with the printed letter the student page is right there (57
  before, 58 now). On that page the answer written for an inline formula still ends with
  the word that follows the formula in the sentence, so the page says that word twice: one
  fault alone does not break the block, and the two together did. The glossary's second
  page had a heading and two continued pieces on the wrong side of the page break in its
  labels; with them in place, what is left wrong on that page is the reader's: a short
  two-column page is read across the gutter.
- The layout dumps of November 10 gave 59 of 78 for the same reader. The one page more was
  a matching exercise that is written as a table and read row by row: a dump holds the words
  of the table's region, in the order of the two lists, and the page a student gets does not.
- 3 files (`ar3_sideways90`, `ar4_sideways270`, `e1_mc_columns`; 10
  answers missing, 3 gold pages, none right) are
  apart until they are recorded again, so the edge group is
  10 pages and not 13.
- This is a development set: every page of it has been read while the reader was built.
  These figures guard against going backward and meet no release bar.

**On a layout gold set** (`--set NAME`, the registry `data/gold/layout/NAME.json` and the
set's folder) each file is converted as a job converts it, under production's behavior
settings, with no model: an equation, a picture and a table keep what the file gives, which
is what the reading order is made of. The folder must be the registered one
(`gold layout verify`), and the groups are the registry's kinds of document. A held-out set
is scored only with `--for-release`: once, for a release, and never while the reader is
being changed. Its record keeps totals and names no page, `--records`, `--from` and
`--against` are refused for it, and a failing page is not looked at.

`layout-dev-1` (60 documents, 267 pages;
`data/eval/order-layout-dev-1.json`), the student page:

| | Pages | Right | Faultless | Furniture read | Cut | Merged | Caption wrong | Broken blocks | Cross-joins |
|---|---|---|---|---|---|---|---|---|---|
| paper | 119 | 73 | 34 | 38 (39) | 42 (52) | 46 (102) | 0 of 0 | 32 | 17 |
| selfmade | 40 | 18 | 9 | 4 (8) | 6 (32) | 14 (39) | 0 of 5 | 17 | 12 |
| textbook | 108 | 58 | 2 | 103 (143) | 44 (123) | 8 (31) | 0 of 0 | 49 | 6 |
| all | 267 | 149 | 45 | 145 (190) | 92 (207) | 68 (172) | 0 of 5 | 98 | 35 |

The element list reads 150 of 267 of these pages right.
`layout-heldout-1` has not been scored.

## What a page costs, how long it takes, what it asks and what its files hold (`eval cost`, `eval speed`, `eval burden`, `eval outputs`)

The four commands are made from one **measured replay** of the test corpus (`eval/measure.py`).
Every upload of the corpus (`corpus.UPLOADS`: each file once, and again with the boxes a
person would tick for it) is converted the way the worker converts a job: production's
behavior settings (`ops/production.profile.toml`), the job's own caps, the conversion in a
process of its own, and the recorded answers in front of the model client. Nothing is sent
and nothing is spent. The database and the web app are not part of it.

Each upload is measured in a fresh process, one at a time by default, so its highest memory
use and its start-up are its own and no cache carries over. What is kept for an upload is
content-free: its pages and the kind of each, its step facts (operations.md, "Step facts and
the step report"), dollars, wall and CPU seconds, peak memory, and each decision it asks of
a reviewer (page, kind and reason codes).

**A page has one kind**, the first of these that fits, so that the kinds add up to the run:

| Kind | A page that |
|---|---|
| `scan` | is scanned or handwritten: read from its picture when the upload asked for that, kept as a picture when it did not |
| `source` | is a unit of a document read from its LaTeX source |
| `slide` | has the shape of a slide |
| `dense_math` | has 9 equations or more |
| `table`, then `figure` | has one |
| `typical` | is none of these |

```bash
uv run readablestem eval cost --out data/eval/cost-corpus.json
uv run readablestem eval speed --runs 2 --cores 2/3 --out speed.json --against data/eval/speed-corpus.json
uv run readablestem eval cost --only s15_charts_figures      # one file
uv run readablestem eval speed --records kept.json           # keep what was measured
uv run readablestem eval cost --from kept.json               # report on it again, measure nothing
uv run readablestem eval burden --out burden.json --against data/eval/burden-corpus.json
uv run readablestem eval outputs --out outputs.json         # builds and reads every file
```

The reports record the four things `eval bar` asks for, with the commit and the machine,
and hold their figures under `metrics`, so `eval bar` can read one
(`--measure kinds.dense_math.usd_page.p95`). The corpus is a development set: it was chosen
to cover kinds of document, and the readers were built while looking at it. So no
report can meet a bar or carry a public claim, and each says "Proof owed" in its header. A
replay of held-out whole documents needs a recording that does not exist yet.

A report also lists the steps that stopped before they were done (`not_finished` in the
record: the upload, the step, the page and the reason, from the step facts) and names them
in its header. A step stopped at a time limit gives another result on a busier machine: one
corpus run made while four test suites ran lost a handwritten page to the 10-second clock
of the page-type step, and its counts differed from the run before it. A baseline is a run
in which no step stopped at a time limit. When nothing could be measured, each command says
so and exits with 2.

### `eval cost`

A request to a model is a step fact with the page it was about. A page's dollars are the
dollars of its requests, plus an even share of any request about the whole document, so the
pages of a document add up to the document. The report prints:

- a document's dollars a page (its cost divided by its pages), over the documents;
- dollars of one page by page kind, at the median and the 95th percentile, with the mean;
- dollars by step, over the pages the step ran on, and its mean over every page;
- the mean of each step on each kind of page;
- the steps of the accuracy round that are not built (page audit, text check, line-strip
  check, table read, figure checker, structure read, cheap first read of a scan): what the
  run spent on each, which is 0 until it runs, and the estimate for one unit with what it
  rests on;
- the two figures the public pages are built from (`TYPICAL_PAGE_USD` and `DENSE_PAGE_USD`
  in `ops/limits.py`) beside what this run measured, and how many pages a day's open pool
  pays for with and without the checks.

An upload whose recording lacks an answer it asks for is left out and named: a refused
request costs nothing, so its pages would look cheaper than they are.

First run (2026-11-11, pipeline 0.5.10, production profile, 63 of the 73 uploads, 315
pages, $4.85 in the recording; `data/eval/cost-corpus.json`). Ten uploads are left out for
the 126 answers still owed ("Recording and replay").

| Page kind | Pages | Documents | Median | p95 | Mean |
|---|---|---|---|---|---|
| `scan` | 25 | 10 | $0.0295 | $0.0569 | $0.0246 |
| `slide` | 45 | 3 | $0.0000 | $0.0202 | $0.0053 |
| `dense_math` | 30 | 10 | $0.0716 | $0.1365 | $0.0676 |
| `table` | 24 | 17 | $0.0059 | $0.0486 | $0.0152 |
| `figure` | 60 | 19 | $0.0174 | $0.0450 | $0.0220 |
| `typical` | 131 | 43 | $0.0000 | $0.0091 | $0.0022 |

| Step | Requests | Pages it ran on | Median of such a page | p95 | Mean over every page | Total |
|---|---|---|---|---|---|---|
| `equation` | 1,314 | 87 | $0.0065 | $0.1119 | $0.0070 | $2.21 |
| `figure` | 260 | 92 | $0.0157 | $0.0510 | $0.0058 | $1.81 |
| `image_page` | 14 | 13 | $0.0364 | $0.0707 | $0.0016 | $0.49 |
| `table` | 44 | 28 | $0.0021 | $0.0486 | $0.0011 | $0.34 |

What the first run shows:

- **Most pages cost nothing.** A page of text alone sends nothing to a model, so the median
  `typical` page is $0. The money is in the pages with equations (46% of it on 87 pages),
  figures and tables. "What a typical page costs" has meant a document's cost divided by
  its pages: $0.0055 at the median document here, $0.043 at the 95th percentile and $0.012
  on average.
- **The kept figures stay.** `TYPICAL_PAGE_USD` is $0.0145 and `DENSE_PAGE_USD` is $0.034
  to $0.092, the audit's measurements. This run's dense pages are $0.072 at the median and
  $0.137 at the 95th percentile, above the kept range's top; that is a development set
  with one 14-page solution set in it, so the range is not changed from it, and the
  held-out replay decides.
- Every dollar of every job is on a step fact: the difference between a result's dollars
  and its facts' dollars is 0 for all 73 uploads.

### `eval speed`

A replay waits on no model, so its seconds are the local work. The report prints:

- seconds a page by page kind, at the median and the 95th percentile. The conversion does
  not time each page yet, so a page is given its document's seconds divided by its pages:
  exact for a document of one kind, blurred for a mixed one;
- seconds a page by stage (`layout`, `image_pages`, `steps`, `assemble`, `checks`), over the
  documents that have the stage, with the stage's mean over every page and its share;
- start-up apart, as seconds of a job: starting the conversion process and loading its code
  takes the same time for 1 page and for 40;
- wall and CPU seconds apart, and the most memory held by the conversion process and by the
  process that called it;
- what the models took when the answers were recorded, per step, apart from all of the
  above.

`--runs N` replays the corpus N times and counts each upload's fastest run: a slow run is
the machine, a fast one cannot be. `--cores A/B` pins run 1 to CPU list A and run 2 to B.
Where two runs of the same code differ by more than the allowance at a kind's median, the
report says the machine was too busy to judge.

**The bar** (`--against RECORD`): no page kind is slower than in the earlier record by more
than a tenth (`--allowance`, 1.0 for a shared runner) at the median or at the 95th
percentile. Two records are compared only when they name the same CPU model and core count.

| Exit | When |
|---|---|
| 0 | the bar is met, or no record was given |
| 1 | a page kind is slower than allowed; the lines name each |
| 2 | not judged: another machine class, or the machine was too busy |

First measurement (2026-11-11, pipeline 0.5.10, production profile, all 73 uploads, 338
pages, the faster of two runs on cores 2 and 3 of an AMD Ryzen 7 5800H). No speed record is
kept from it: other work ran on the machine, the two runs differ by more than the allowance
on `dense_math`, and the report says so ("the machine was too busy to judge"). The figures
show the scale and where the time goes; the record `--against` compares with is made on a
quiet machine and kept as `data/eval/speed-corpus.json`.

| | Median | p95 |
|---|---|---|
| Wall seconds a page, start-up apart | 0.71 | 2.57 |
| CPU seconds a page, start-up included | 1.63 | 3.73 |
| Start-up of a job (seconds) | 2.00 | 2.04 |

| Page kind | Pages | Documents | Median s | p95 s |
|---|---|---|---|---|
| `scan` | 25 | 10 | 0.77 | 4.06 |
| `slide` | 45 | 3 | 0.20 | 0.55 |
| `dense_math` | 34 | 13 | 0.84 | 1.70 |
| `table` | 29 | 22 | 1.00 | 1.61 |
| `figure` | 64 | 20 | 0.69 | 1.39 |
| `typical` | 141 | 49 | 0.52 | 1.21 |

Layout is 66% of the local time (0.48 s a page at the median), the steps' own work 24%,
assembly 7% and the checks 1%. The models are not waited for in a replay; when the answers
were recorded a request took 1.4 s at the median for an equation, 2.6 s for a figure, 3.2 s
for a table and 8.9 s for a page read as a picture. The conversion process held at most
515 MB and the process that called it 424 MB.

### `eval burden`

How many decisions a page asks of the person who reviews it, before anyone has reviewed.

**One definition.** A decision is what the review screen counts: a fix the pipeline marked
`required`, with a group of repeated headings (or of pages kept as pictures for one reason)
as one decision. `core/triage.py` holds it (`units`, `required_decisions`, and `decisions`,
which returns the same decisions as rows: page, kind, reason codes, members). The review
screen, the documents list, the review record, the weekly report, the command line's
summary and this report all count through it. Building the report found two counters that
did not: `triage.review_load` and the weekly report's review-load table counted only
equations, figures, headings and tables, so the paragraphs of a page read from its picture
(6 decisions in this corpus) were on the review screen and in neither. Both now count every
kind of fix.

A decision is on the page of its first member. The report prints:

- decisions a page over the whole set, and a document's decisions a page at the median and
  the 95th percentile over documents (what one instructor meets);
- decisions of one page by page kind, at the median and the 95th percentile, with the share
  of pages that ask nothing;
- decisions by what is decided (equation, figure, heading, table, paragraph);
- decisions by why they were asked, each with its rate per 100 pages. The reason codes are
  `triage.tier_reasons`: a failed check's code, the fix's own reason, a repeated heading,
  or `below_accept` when only the confidence held it back. A decision with two reasons is
  counted under both;
- the documents that ask the most a page.

An upload whose recording lacks an answer it asks for is left out and named: an unanswered
request fails its element, and that decision would be the recording's, not the
conversion's. A record kept before decisions were measured is "not measured", not a
document that asks nothing.

**The bar** (`--against RECORD`): the whole set and every page kind with at least 10 pages
ask for no more decisions a page than in the earlier record (`--allowance`, 0 by default).
Two records are compared only when they are of the same documents and measured the same
number of uploads.

| Exit | When |
|---|---|
| 0 | the bar is met, or no record was given |
| 1 | the set or a page kind asks for more than before; the lines name each |
| 2 | not judged: other documents, or a different number of uploads measured (with `--same`: no upload was measured) |

**Fewer is not better by itself.** A change that stops asking about something wrong lowers
this figure and raises the errors nobody is told about. The bar is read with the counts of
silent errors (the lane evals and the page audit), never alone.

**The replay record, and CI.** `data/eval/burden-corpus.json` is also the record CI's eval
gate holds a pull request to. With `--same` the bar is not "no more decisions" but "the
decisions the record has": every upload measured now must ask for exactly the decisions, on
exactly the pages, that the record has, fewer as well as more, because a rule that stops
asking about something wrong is as much a change as one that asks more (and raises the
silent errors the lane evals count). An upload measured now and not in the record is a
difference; an upload the record has and this machine cannot replay is not compared. CI
holds the public files only: the answers for the CC BY-NC-SA files are kept out of the
repository (`corpus.KEPT_OUT`), so CI replays about half the uploads and the box replays
all. The record is made on the box, with the kept-out answers, by this command and never by
hand:

```bash
uv run readablestem eval burden --out data/eval/burden-corpus.json
uv run readablestem eval burden --against data/eval/burden-corpus.json --same   # what CI runs
```

A pull request that changes the pipeline fails the second command when its change moves any
upload's decisions, and passes when the record was re-made in the same pull request. The
eval guard (`eval-guard.yml`) asks for the re-made record when a change touches a rule that
decides acceptance ("Eval guard"); the replay finds the change the guard could not name.

**The record now kept** (`data/eval/burden-corpus.json`, made 2026-11-18 at pipeline 0.5.11 by
`eval burden --out`, production profile, 45 of the 73 uploads, 122 pages, 70 decisions,
0.574 a page). Fewer uploads than the first baseline below: the changes of 0.5.11 (the
MathTime fonts, blanks, the legend and axis rules) make the pipeline ask questions the
recording does not hold, so 28 uploads are left out for 1,250 answers missing, among them the
equation-heavy ones (`w_notes_6042_ch2` 646, `w_lecture_5111`, `w_slides_60001_lec10`,
`r17_arxiv_ligo`). The decisions a page of the two records are not comparable: the dense
mathematics that carried most of the load in 0.5.10 is the part now left out. Against the
0.5.10 record, 43 of the 45 uploads ask the same; `d3_arabic_doc` asks for 3 decisions where
it asked for 0 and `s05_form_acroform` for 0 where it asked for 1, which is why the record
was re-made. **A paid recording of the answers is owed** (about 1,250 requests on the
current code) to bring those uploads back into the gate. The cost, order and outputs records
are still the 0.5.10 ones, and CI does not compare them.

First baseline (2026-11-11, pipeline 0.5.10, production profile, 63 of the 73 uploads, 315
pages; the figures below are that record's, no longer the file's). The ten uploads left out
are the ones the cost report leaves out, for the same 126 answers.

| | |
|---|---|
| Required decisions | 567 |
| Decisions a page, whole set | 1.80 |
| A document's decisions a page | 0.29 at the median, 7.44 at p95, 10.21 at the most |
| A page's decisions | 0 at the median, 9 at p95, 25 at the most |
| Pages that ask nothing | 56% |

| Page kind | Pages | Documents | Decisions | A page | Median page | p95 | Most |
|---|---|---|---|---|---|---|---|
| `dense_math` | 30 | 10 | 314 | 10.47 | 9 | 25 | 25 |
| `figure` | 60 | 19 | 105 | 1.75 | 1 | 7 | 13 |
| `scan` | 25 | 10 | 28 | 1.12 | 1 | 4 | 9 |
| `slide` | 45 | 3 | 13 | 0.29 | 0 | 2 | 5 |
| `table` | 24 | 17 | 12 | 0.50 | 0 | 1 | 2 |
| `typical` | 131 | 43 | 95 | 0.73 | 0 | 5 | 8 |

| What is decided | Decisions |
|---|---|
| equation | 384 |
| figure | 122 |
| heading | 44 |
| table | 11 |
| paragraph (a page read from its picture) | 6 |

| Why it was asked (the ten most frequent of 37 codes) | Decisions | Per 100 pages |
|---|---|---|
| `below_accept` | 154 | 48.9 |
| `symbol_mismatch` | 143 | 45.4 |
| `sign_mismatch` | 52 | 16.5 |
| `glyph_style_mismatch` | 51 | 16.2 |
| `low_confidence` | 41 | 13.0 |
| `heading.weak_evidence` | 38 | 12.1 |
| `heading.sentence_like` | 28 | 8.9 |
| `style_cosmetic` | 26 | 8.2 |
| `long_description_unsure` | 22 | 7.0 |
| `text_layer_mismatch` | 19 | 6.0 |

What the baseline shows:

- **The load is equations.** 384 of the 567 decisions are equations, and the 30 dense
  math pages (a tenth of the pages) ask for 314 of them: 10.5 a page, 25 on the worst. One
  14-page solution set asks for 143. More than half of all pages ask nothing.
- **Two reasons are half of it.** `below_accept` (the reading passed every check and its
  confidence stayed under the accept bar) and `symbol_mismatch` (a symbol of the text
  layer is not in the reading) are on 297 decisions. Whether those flags find errors is
  what the equation measurements answer; this report only says where the questions are.
- A document's 0.29 at the median against 1.80 for the set says the same thing from the
  other side: most documents ask little and a few ask most of it.

Two documents carry goals of their own, set before this report existed, and both are in the
table of documents the report prints: the notes chapter `w_notes_6042_ch2` asks 3.36 a page
today against a goal of 1.0 or fewer, and the solution set `w_solutions_1802` asks 10.21
against 4.0 or fewer. Neither is met. They are development documents, so meeting them here
proves nothing by itself; the release bar is judged on a held-out replay, which does not
exist yet.

### `eval outputs`

Every other eval stops at the element list or at the markup of the student page. This one
opens the files a student is given and counts what is wrong in them.

**What is read.** From each converted upload the command builds the student page, the Word
file, the EPUB, the two Canvas files (the page for a Canvas Page and the file for Canvas
Files), the Common Cartridge and the Listen transcript, through the code the downloads use,
and reads each one back from its bytes into one inventory (`pipeline/output_audit.py`):
the blocks of words in reading order, the equations and whether each has a spoken form, the
pictures and their alt text, the tables with their header cells, the links inside the file
and whether each lands, the notes and the language. Words a reader hears and does not see
(`alt`, `aria-label`, `alttext`, text for screen readers only) are read as what they are.

**What is compared.** An export is right when it says what the page says, so each export is
compared with the page it was made from. The page is compared with the stored result: every
element has a place, in the stored order, and every open decision and kept picture is
marked. Whether the words inside a place are the right words is the business of the lane
evals and the page audit, and is not counted here.

A count is one wrong place: a kind, the output, the page and the element id, and none of
the document's words. Each place is counted once.

| Kind | A place where |
|---|---|
| `out_char` | a character is not text (private use, a replacement character, a control) |
| `out_text_lost` | words of the page are in no place of the output |
| `out_text_twice` | words that are once on the page are twice in the output |
| `out_order` | a block is in another order than on the page |
| `out_math_fallback` | an equation is not an equation in the output, or has no spoken form |
| `out_link_dead` | a link inside the file leads nowhere in it |
| `out_alt_missing` | a picture that is not decoration has no alt text |
| `out_table_header` | a table has no header cell where the page's has one |
| `out_heading` | a heading is empty or skips a level, or the title is said twice |
| `out_lang` | the file states no language, or another than the document's |
| `out_note_lost` | a note of the page is not in the output |
| `out_hole` | a sentence has a gap: a space before its full stop or comma, "the -x" |
| `out_notice_missing` | an open decision or a kept picture has no note at its place |

**What an output leaves out on purpose** is a written list (`output_audit.ABSENT_BY_DESIGN`),
and nothing else may be missing: Word has no skip link and no visible page labels, the EPUB
has neither and builds its own contents list, the Canvas page has no skip link, and the
transcript says neither the page labels nor the title of the contents list.

**What a format cannot hold is not asked of it.** Each limit is one rule:

- A Word table can mark a row as a header and nothing else. A header column of the page is
  not asked of the Word file.
- Each format numbers a note's mark in its own way, so the mark's digits are not compared.
  A Word footnote with no words is a mark that leads nowhere, and is a dead link.
- A voice adds the word "link" before a link's name and reads a table row by row, so the
  transcript's words are compared by their letters. The Listen page reads the first 8 rows
  of a table and says how many more there are; the audit reads the transcript with every
  row, so every row's words are checked.
- The transcript closes the space before a full stop, and says a paragraph that is only a
  formula in words. A gap is therefore judged where the page has it, and counted for the
  transcript when the transcript says that sentence.
- A formula in a table cell is written once for a voice and once for the eyes. It is one
  thing: a file that shows both is counted as words twice.
- A picture is decoration only where the page says so; an export is held to the page's
  count of them.
- MathML counts as a spoken form.

**It measures the page before anyone has reviewed it.** The replay converts and nobody
decides anything, so every proposal that waits for a required decision is open. A page that
shows such a proposal with no mark at its place is counted under `out_notice_missing`. That
is the largest figure below, and it is a count of the draft, which cannot leave as a
finished file while a decision is open. It is counted because a draft can be downloaded
after a confirm, and its reader has only the banner at the top to go by.

**Known false positive.** `out_hole` also fires where the source itself writes a space
before a colon or a full stop (a slide that sets "O(1) :"). Five of the places below are
of that kind.

```bash
uv run readablestem eval outputs --out data/eval/outputs-corpus.json
uv run readablestem eval outputs --only r18_arxiv_opt        # one file
uv run readablestem eval outputs --from kept.json            # report on a kept replay
```

It spends nothing and gates nothing. Exit code 0: measured; 2: nothing could be measured.
An upload whose recording lacks an answer is reported apart, as in the other reports.

Baseline (2026-11-11, pipeline 0.5.10, production profile, 63 of the 73 uploads, 315
pages; `data/eval/outputs-corpus.json`). Every output of every upload could be built.

| Kind | Page | Word | EPUB | Canvas file | Canvas page | Cartridge | Transcript | Documents |
|---|---|---|---|---|---|---|---|---|
| `out_char` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_text_lost` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_text_twice` | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 4 |
| `out_order` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_math_fallback` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_link_dead` | 0 | 74 | 0 | 0 | 0 | 0 | 0 | 8 |
| `out_alt_missing` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_table_header` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| `out_heading` | 13 | 14 | 13 | 13 | 13 | 13 | 13 | 14 |
| `out_lang` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_note_lost` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `out_hole` | 43 | 43 | 43 | 43 | 43 | 43 | 43 | 6 |
| `out_notice_missing` | 519 | 0 | 0 | 0 | 0 | 0 | 0 | 33 |

What the figures are, checked place by place on 29 of the uploads:

- **`out_notice_missing`, 519 on the page.** Proposals that wait for a required
  decision and are shown with no mark (on the 29 uploads: 419 of 428; the other 9 are
  formulas inside table cells, where a kept or open formula has no mark of its own and the
  table has none). Every picture kept as the original with a place of its own is marked.
  The exports copy the page, so the same places are unmarked in each; they are counted once,
  on the page.
- **`out_hole`, 43 in each written output.** Sentences that lost an inline formula
  ("is denoted ."), stray punctuation left as a paragraph of its own, and a mis-decoded
  ellipsis; five are the false positive above. The transcript says every one of these
  sentences.
- **`out_link_dead`, 74 in Word.** pandoc keeps an id on a heading and drops it on a
  paragraph, so most links inside a Word file have no target. 13 of them are footnotes:
  every note's mark becomes a Word footnote with no words, the note's text stays a
  paragraph of the body, and its "Back to the text" link leads nowhere.
- **`out_heading`, 13 in every output.** The page opens with the document title as
  its `h1`, and the first heading of the document says the same words. In Word the title
  paragraph is dropped when the two are equal; where they differ only in case it is not,
  which is the one more in Word.
- **`out_text_twice`, 9 in Word.** The words written for a voice beside a formula
  in a table cell are ordinary text in Word, next to the formula.
- **`out_table_header`, 1 on the page.** A table with no header cell and an open decision
  on it.
- Zero everywhere: a character that is not text, words lost, order, an equation with no
  spoken form, a picture with no alt text, the language, a lost note.

None of these is fixed here. The Word defects belong to the Word export's rebuild, the
unmarked proposals to the page's notes, and the title to the one-`h1` rule; the counts are
their baseline, and the release bar is 0 for `out_char`, `out_text_lost`, `out_link_dead`,
`out_alt_missing` and `out_notice_missing`. They are development figures: the bar is judged
on a held-out replay, which does not exist yet.

What none of the reports has yet:

- A replay of held-out whole documents (the recording is not made), and so a bar or a
  public figure.
- Seconds of one page. They come with the per-page trace of the conversion; until then a
  kind's seconds are those of the documents its pages are in.
- A long document. The longest corpus file has 39 pages; the 100-page math-heavy document
  the audit timed is not in the corpus.
- A LaTeX source, a Word or a PowerPoint file: the corpus has none whose answers are
  recorded, so the `source` kind has no row.
- How long a background (batch) check takes to come back: no batch answer is recorded.

## The page audit in shadow (`eval page-audit`)

The page audit (steps.md, "Page audit") compares one page of the output with the page it
came from. This command runs it over a set and writes its verdicts. "In shadow" means that
nothing reads them: no output, stored result or decision changes. It is how the audit's
flags are measured before any of them is acted on.

```bash
uv run readablestem eval page-audit --corpus --fake            # no model: every page "not audited"
uv run readablestem eval page-audit --corpus --provider anthropic --max-usd 3.00 --batch
uv run readablestem eval page-audit --corpus --answers kept.jsonl --answers-offline   # replay, $0
uv run readablestem eval page-audit --gold-dir <a layout gold set's folder> --provider anthropic --max-usd 1.00
uv run readablestem eval page-audit --corpus --only s15_charts_figures --rendered
```

- **`--corpus`**: each corpus upload converted under the production profile from its
  recorded answers, as `eval cost` converts it. An upload this machine cannot replay is
  listed under `skipped` with its reason.
- **`--gold-dir DIR`**: the files of a layout gold set (`DIR/files/<id>.pdf`), each
  converted with no model, so what is audited is the layout and the reading order. The
  report goes in that folder (`DIR/page-audit.json`), so a held-out set's rows stay with
  the set and outside the repository.
- **`--rendered`** also sends the picture of the page's output (needs the browser group).
- **`--answers FILE`** keeps the answers (`eval/answers.py`); with `--answers-offline`
  nothing is sent and a page whose answer is not in the file is not audited. `--batch`
  sends what the file cannot answer as Message Batches at half price.
- A live run needs `--provider anthropic` and a `--max-usd` cap, and keeps its recording
  like every other live run ("Runs keep their recordings").

Exit code 0: every page has a verdict. 1: the spend cap ended the run, and the pages after
it are `not_audited` with `cap_reached`. 2: the run could not start.

The report (default `data/eval/latest-page-audit.json`) holds codes, ids, boxes, counts and
dollars, and no text of any document: the model, effort and prompt version; `verdicts`
(pass, flagged, not audited); `answered_by` (the model, or a test by code);
`not_audited_by_reason`; `findings` by code and severity; `requests`, `cost_usd` and
`usd_per_asked_page`; `stopped`; `skipped`; and one row a page with its verdict, findings,
reason and the key of the question asked.

One run's verdicts are not a measurement of the audit. Its bars (how many marked faults it
flags, how many clean pages it flags, and the share of verdicts that differ between two
runs of one input) are judged on pages whose faults a person marked. No live run is
recorded yet: the first shadow recording of the corpus waits for the owner's approval of
its cap.

## The quality ledger (`eval ledger`)

Each eval above reports its own lane on its own set. The ledger
(`src/readablestem/eval/ledger.py`) is one list of rows over all of them, led by the rows
that say what a student meets: how often a whole course document reaches them with an error
nobody was asked about. It measures nothing and spends nothing. It reads the records the
evals keep in `data/eval` (`order-*.json`, `burden-*.json`, `outputs-*.json` and
`whole-*.json`), and for every row and every record that feeds the row it prints the counts,
the figure, a bound and a label.

```bash
uv run readablestem eval ledger --out data/eval/ledger.json
uv run readablestem eval ledger --for-release          # the check a release runs
uv run readablestem eval ledger --register-bars        # after the first held-out run
```

**The labels.**

| Label | What it says |
|---|---|
| held-out | scored on held-out documents, under the production profile, by this release |
| development score | scored on documents the readers were built on, or under settings that are not the production profile; it guards against going backward and meets no bar |
| earlier release | the record was made by another pipeline version, or under another profile, than the code that prints the ledger; it says nothing of this release, so the evals are run again for every release |
| not measured: proof owed | no record feeds the row; the report says what proof is owed |

**The bound.** A count from a sample allows more than it shows: no error in 237 formulas
still allows about 1.3 in 100. Every figure is printed with a one-sided 95% bound on the
side that matters (the upper one where lower is better): exact for a share
(Clopper-Pearson) and for a rate of events (Poisson), an order statistic for a median. The
bound treats pages as independent. Errors come in clusters by document, so the true bound
is wider, and the rows that count documents are the ones to read for that.

**The two bars.** The goal bar is the figure a lane is built toward, written with the row.
It is met only when the bound is inside it, not when the figure is. A goal of none is met by
a count of none, and the line still prints what that count allows. The release bar is the
figure of the first held-out run, kept in `data/eval/ledger-bars.json` with the set it was
measured on. `--register-bars` writes the bars that are missing; a bar is replaced only by
naming its row (`--replace-bar ROW`), and a figure from a run in which a step stopped early
is no baseline and is not written. A release may not be worse than its release bar, held as
measured, with no bound. Only a held-out line is held against either bar.

Exit code 0: no held-out line is worse than its release bar. 1: one is, or, with
`--for-release`, a row that has a release bar has no held-out figure of this release on the
bar's set. 2: refused. A record that does not say its split, its set's hash, the profile's
hash and the pipeline version is not read around: the run stops and names the file.

**The whole-document record** (`whole-<set>.json`) is what the top rows are made of. Its
`metrics` hold one row a document, with no name: `pages`, `silent_errors` (anything wrong in
the student file that carried no required decision), `doubt_false` and `doubt_true` (doubt
flags: a check or a reader disagreed, and the flag turned out false or true) and
`rule_required` (decisions a rule asks for whatever the readers say, by rule). Beside them,
`rule_owners` (who shows on held-out pages what a rule catches) and `hand_sample` (the pages
that passed every check and were read by hand, and how many had a fault). False flags per
true flag is over doubt flags only. A rule-required decision is printed on its rule's line,
with the rule's owner, and in no ratio. The record is written by the run that audits
held-out whole documents and samples the pages it passed. That run needs a recording that
does not exist yet, so no such record is kept and the top rows are not measured.

**The file** (`--out`) holds, per release and row, the figures, their labels, the counts
and both bars, and no word of any document. Its lanes are named as the outcome events name
theirs (`web/events.py`, `LANES`) and its `release` is spelled as a `decision_outcome`
event's, so the admin page can set production's numbers of the same release (false flags
per true flag, edits of things nobody flagged, decisions a page) beside a lane's.

**Today** (`data/eval/ledger.json`, release `0.5.10+62b4f1678d36`). No row has a held-out
figure and no release bar is registered: every line below is a development score.

| Row | Set | Figure | Counts | 95% bound | Label | Goal bar |
|---|---|---|---|---|---|---|
| Required decisions a page, the median document | corpus | 0.29 | 63 documents | 0.60 | development score | at most 1.00 |
| Pages not read right in the student page, by the scorer | corpus | 27.3 per 100 | 24 of 88 pages | 36.2 per 100 | development score | - |
| Pages not read right in the student page, by the scorer | layout-dev-1 | 44.2 per 100 | 118 of 267 pages | 49.4 per 100 | development score | - |
| Documents with every gold page right | corpus | 56.8% | 25 of 44 documents | 43.3% | development score | - |
| Documents with every gold page right | layout-dev-1 | 13.3% | 8 of 60 documents | 6.8% | development score | - |
| Pages that read a running head, a page number or a stamp aloud | corpus | 17.0 per 100 | 15 of 88 pages | 25.0 per 100 | development score | at most 2.0 per 100 |
| Pages that read a running head, a page number or a stamp aloud | layout-dev-1 | 54.3 per 100 | 145 of 267 pages | 59.5 per 100 | development score | at most 2.0 per 100 |
| Wrong places in the html file | corpus | 182.9 per 100 | 576 in 315 pages | 195.9 per 100 | development score | - |
| Wrong places in the docx file | corpus | 44.4 per 100 | 140 in 315 pages | 51.1 per 100 | development score | - |
| Wrong places in the epub file | corpus | 17.8 per 100 | 56 in 315 pages | 22.2 per 100 | development score | - |
| Wrong places in the canvas_index file | corpus | 17.8 per 100 | 56 in 315 pages | 22.2 per 100 | development score | - |
| Wrong places in the canvas_page file | corpus | 17.8 per 100 | 56 in 315 pages | 22.2 per 100 | development score | - |
| Wrong places in the cartridge file | corpus | 17.8 per 100 | 56 in 315 pages | 22.2 per 100 | development score | - |
| Wrong places in the transcript file | corpus | 17.8 per 100 | 56 in 315 pages | 22.2 per 100 | development score | - |
| Files that could not be built | corpus | 0.0 per 100 | 0 of 441 files | 0.7 per 100 | development score | none |

Read with care:

- "Required decisions a page" is the median over documents of a document's decisions a
  page, as production counts it. Its bound is an order statistic of
  63 documents of a set chosen to cover kinds of
  document, not sampled from what people upload.
- The corpus line of the page rows counts every gold page measured whole
  (88, the edge pages with the others); `eval order`'s
  "of 78" leaves the edge pages out.
- Every gold page is right in 25 of 44 corpus documents and in
  8 of 60 documents of `layout-dev-1`. A document's gold pages are
  the ones labeled, which in a layout set is a sample of its pages.
- The output rows count places, and one fault can be many places: most of the HTML file's
  are proposals still open that the page does not mark (`eval outputs`, above).

Not measured, with the proof each is owed printed by the command: silent errors, documents with no silent error, false flags per true flag, doubt flags only, pages that passed every check and had a fault, read by hand, flags of the page check, and verdicts that change between two runs of one input, formulas whose meaning is wrong, with no flag, boxes that hold the whole formula, formulas inside tables read wrong, private-use or U+FFFD characters on a page with no flag, pages with a text error and no flag, words changed on a print page, wrong tables shown with no decision, wrong stated values on vector charts, drawn structures wrong with no question; and the rule-required decisions by rule.

## The same file gives the same document on any machine

A score or a replay means something only if a result does not depend on the machine that
made it. The rule (`core.types.NotFinished`, tests/test_determinism.py):

- A budget counts work: candidates tried, cells tested, pixels read. That is the same on
  every machine.
- A limit on time, memory or money may only stop a step. It never quietly changes what a
  page contains.
- A step that stops says so with a "not finished" fact: `{step, page, reason, done, of}`,
  with no document text (operations.md, "Step facts and the step report"). Every report made
  from a measured replay lists these facts in its record and names the steps in its header.

Three tests hold the rule:

1. **Every clock is named.** The test finds each read of a clock in `pipeline/` and
   `source/` and compares the list with a table in the test file that says what each is for:
   a timing field, a cache's expiry, a limit that ends the conversion with a code, a limit
   whose step writes the fact. A new read fails the test until it is added to the table,
   which is when to ask whether it can change a document.
2. **A planted step.** A conversion with a step planted in it that leaves a page out once
   its clock has passed a second is compared with the same conversion at full speed. The
   comparison names the earliest part of the result that differs, and passes only when the
   step wrote the fact.
3. **The corpus, twice** (`-m workflow`). Every corpus upload is replayed as it is and
   again held back to a quarter of the time (`eval.measure.run(..., share=0.25)`: the
   conversion and everything it started are stopped and continued ten times a second, so
   every clock they read runs four times as fast against their work). The two results are
   hashed part by part with the fields of seconds left out (`measure.parts`: page types,
   elements, the fixes of each kind, the HTML, the rest). An upload may differ only if a
   step of the slower run wrote a "not finished" fact the quicker one lacks, or if it is
   in the test's `KNOWN` list with the plan item that ends its difference.

What the corpus run found (2026-11-11, 73 uploads). The first run failed on one upload, a
handwritten lecture converted with its image pages read. The ink analysis has 10 seconds
of wall-clock time for a document (`page_type.INK_BUDGET_S`) and takes 5.4 of them on that
file at full speed. At a quarter of the speed the time ran out, one page was typed without
its ink and so was not seen as handwriting, and its page type, elements, fixes and HTML all
changed with nothing saying why. The stop now writes the fact (`page_type` with
`time_limit` for each such page, and `ink_strokes` when the strokes were not read), and the
test passes with an empty `KNOWN` list. Since plan WS14.9 (2026-11-11) the budget is a count
of work for each page (`page_type.INK_MAX_ITEMS`, `INK_MAX_SEGMENTS`) and the clock only a
guard against a hang (`INK_GUARD_S`, 60 s a page), so which pages are read for handwriting
no longer depends on how fast the machine is. Measured again that day, that upload kept its
page at a quarter of the speed under the old budget too, so the loss itself was not
reproduced; what the test shows is that a clock ten times faster or slower gives the same
document. The table search's time limit was not reached by any corpus upload at a quarter of
the speed; it is listed in the clock table and gets its own fact with the table work.

Not covered: a document long enough to reach the limits by its size. The corpus's longest
upload is short of that, so a 100-page math-heavy document converted under several CPU
limits is still owed.

## What the numbers do not show

- **Small samples.** 30 equations: one equation is 3.3 points, and at 0.80 the 95%
  interval is about 0.63 to 0.90. 13 figures: one figure is 7.7 points. The gate's 0.02
  tolerance fails on any single lost equation; that is deliberate while n is this small,
  and it means the gate detects changes, not significance.
- **Clean inputs.** Rendered equations and printed pages are cleaner than real course
  PDFs: one renderer, one set of fonts, single column, no scanning noise, no LaTeX
  (Computer Modern) typesetting, no handwriting, no slides. Expect real documents to
  score lower.
- **Equation inputs are rebuilt, not read.** The equations task gives the step a text
  layer from a Chromium print and a context window built from the page source. Real
  PDFs (LaTeX, Word) have other text layers, sometimes none, and `layout` may cut a
  region or a paragraph differently, so routing, the text-layer cross-check and the
  confidence it scales can differ from a real document. The document task reads both from
  the printed PDF the way the app does.
- **One book.** High-school physics, three sections of one chapter, OpenStax's own
  MathML style. Calculus, chemistry notation, matrices and long derivations are not in the
  sample.
- **The scorer's ceiling.** A perfect transcriber scores exact_lenient 0.83 here (5 known
  normalisation gaps), so equation numbers understate quality by up to 17 points.
  Exact match also ignores how a screen reader speaks the result; two different MathML
  trees can read the same.
- **The judge is a model.** Its pass rate means "agrees with gpt-6.1-sol reading the
  OpenStax alt text", not "helps a blind student". It treats the reference as truth, and
  OpenStax alt texts vary from 39 to 980 characters in length and style. Since it sees
  the image (and the prompt makes the image the ground truth), its numbers are not
  comparable with the earlier text-only runs. Until `eval calibrate` shows good
  agreement with Harish's labels (and the
  CI on kappa is narrow enough to mean something; 13 figures is not), judge numbers are
  provisional.
- **Headings.** 50 of 121 gold headings are the same two words ("Teacher Support"), so
  overall recall can look better than recall on real section headings; read
  `recall_by_context`. Matching assumes reading order. Relative levels hide an outline
  that is consistently shifted.
- **Automated HTML checks.** Static and axe-core checks find only the problems a machine
  can detect. Zero violations does not mean the output is usable with a screen reader;
  nothing here replaces testing with assistive technology and people who use it.
- **Cost and latency.** Fake runs report fake costs and 1 ms latencies. Real latency
  depends on the provider's load on the day; cost excludes rendering, storage and the
  judge's own calls (reported separately).
- **No conformance claim.** ReadableSTEM assists people making course material accessible.
  None of these numbers establishes conformance with WCAG or any other standard.

## Files

| Path | Role |
|---|---|
| `src/readablestem/eval/render.py` | equation PNGs, printable page HTML and PDF, image cache, demo wrapper |
| `src/readablestem/eval/pipeline_eval.py` | the four tasks, metrics, report, markdown summary |
| `src/readablestem/eval/run_record.py` | the folder a live run keeps its answers, rows and report in, with the commit |
| `src/readablestem/eval/measure.py` | one measured replay of the corpus: each upload converted as a job is, in its own process, with page kinds, step facts, dollars, seconds and memory |
| `src/readablestem/eval/cost.py`, `eval/speed.py`, `eval/burden.py` | `eval cost`, `eval speed` and `eval burden`: the reports made from a measured replay, and the speed and review-load bars |
| `src/readablestem/pipeline/output_audit.py`, `eval/outputs.py` | the output audit (a reader for each file a student is given, and the counts) and `eval outputs`, which builds the files from a measured replay and reports the counts |
| `data/eval/outputs-corpus.json` | the last output-audit record of the corpus (development set) |
| `src/readablestem/eval/order.py`, `eval/order_report.py` | the reading-order scorer, and `eval order`: its figures on the measured replay or on a layout gold set, and the bar that no right page is made wrong |
| `data/eval/order-corpus.json`, `data/eval/order-layout-dev-1.json` | the last reading-order records of the corpus and of the layout development set |
| `src/readablestem/eval/ledger.py` | the quality ledger and `eval ledger`: every row from the kept records, with counts, a bound, a label and the two bars |
| `data/eval/ledger.json`, `data/eval/ledger-bars.json` | the last ledger, and the release bars once a held-out run has been registered (none yet) |
| `src/readablestem/pipeline/page_audit.py`, `models/prompts/page_audit.md`, `eval/page_audit_eval.py` | the page audit, its prompt, and `eval page-audit`: the audit in shadow over the corpus or a layout gold set |
| `data/eval/cost-corpus.json` | the last cost record of the corpus (development set). The speed record, `data/eval/speed-corpus.json`, names its machine and is not kept yet (see "What a page costs and how long it takes") |
| `src/readablestem/eval/equations_chem.py` | the chemistry scorer and the chemistry report fields (`chem_right_excl_times`, `silent_harm`) |
| `scripts/dev_chem_classes.py` | the outcome classes of a chemistry run, what harms a listener, `--speech` |
| `src/readablestem/eval/judge.py` | figure judge request, schema, pass rule; the long-description judge (`judge_long`) |
| `src/readablestem/models/prompts/judge_long_description.md` | the long-description and data-table rubric (versioned by hash) |
| `data/eval/figures-long-descriptions.json` | the figures report with long descriptions on (2026-10-09) |
| `data/eval/document-flags-on.json` | the document report with every content flag on (pipeline 0.4.0, 2026-10-12) |
| `src/readablestem/eval/verifier_metrics.py` | the second automated check's eval metrics |
| `src/readablestem/models/prompts/judge_figure.md` | judge instructions (versioned by hash) |
| `src/readablestem/eval/calibration.py` | kappa, bootstrap CI, label template, bias probes |
| `src/readablestem/eval/gate.py` | regression gate (comparability, judge and stale-report checks) |
| `src/readablestem/eval/sample.py` | the weekly seeded sample of the gold set |
| `src/readablestem/eval/bold_letters.py` | bold letters in running text: found against the LaTeX and OpenStax sources (offline) |
| `src/readablestem/eval/notation_flags.py` | course notation as style evidence: new flags on stored answers, with and without the text layer's styles (offline) |
| `src/readablestem/cli/eval_cli.py` | `eval pipeline/gate/calibrate/render`, `demo build` |
| `src/readablestem/gold/heldout.py` | the pre-registered held-out set: its 15 sections, registered hashes and page counts, pinned fetch, `verify_gold` |
| `src/readablestem/cli/heldout.py` | `--set heldout`: where its files live (outside the repository), the guards |
| `tests/test_heldout.py` | held-out registration, fetch, verification and CLI (offline; one `network` test rebuilds the registered gold) |
| `src/readablestem/gold/devset.py`, `devset_classify.py`, `devset_build.py` | the extended development set: classes, selection against the held-out text, parts, seal, verification, build |
| `src/readablestem/cli/devset.py`, `src/readablestem/eval/devset_media.py` | `gold devset ...`, `--set dev-v2`; authored PDFs as eval inputs |
| `data/dev-v2/` | the extended development set (`README.md` there): dev gold, authored documents and their builder, sealed check archive, seal, selection |
| `tests/test_devset*.py` | classes, selection, seal and the committed set (offline) |
| `src/readablestem/eval/tables.py` | the tables task: documents, page assignment, matching, TEDS, headers, math, fidelity, review load, real documents, report |
| `src/readablestem/eval/ted.py` | tree edit distance with a relabel-cost hook (equations, table TEDS) |
| `data/gold/tables/` | table gold: 13 OpenStax pages (CC BY), `reference_pages.json` |
| `data/authored/tables/` | authored table documents (MIT): sources, PDFs, gold, `make_authored.py` |
| `tests/fixtures/tables/table_bench_excerpt.json` | table-bench predictions and its scores on five documents |
| `src/readablestem/eval/page_types.py` | the page-type scorer: confusion matrices, actions, errors, time (no model) |
| `data/eval/tolerances.json` | gate tolerances |
| `src/readablestem/eval/invented/` | the invented-content rate for any system's output, with its audit and a planted-inventions check (docs/eval-invented.md); `eval invented ...` |
| `tests/test_eval_*.py` | offline tests; `-m browser` runs the Chromium ones |
