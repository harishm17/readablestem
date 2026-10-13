# Evaluation

How a11y-stem measures its own output, on what data, and what the numbers leave out.

Status (2026-10-07): the harness is built and tested offline. No real-model numbers exist
yet. The first real run happens after the pipeline work packages (layout, steps, OpenAI
client, `convert_pdf`) are merged; until then the pipeline modules are stubs and
`eval pipeline` reports "still a stub".

## The idea

OpenStax publishes its textbooks as web pages with expert-written MathML, alt text and a
real heading outline. That makes a page an answer key. The harness:

1. takes the answers from the gold set (`data/gold/`, built by `a11y-stem gold fetch`),
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
gold set; choosing the gold-set pages is Harish's decision (ADR 0001).

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
chart, lies within half of the axis's smallest labelled step), `table_wrong_values`,
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
| `colour_only` | figures flagged as telling things apart by colour alone |
| `required_decisions`, `tables_offered` | figures needing a decision; data tables read from the image, offered as optional extras (not in the file until a reviewer adds them, never a decision; `required_for_values` in reports before 2026-10-09, when each was a decision) |

The long judge is not calibrated against human labels yet.

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
| `unmatched_predicted`, `unmatched_by_reason` | predicted equations with no gold partner, by why: `prose_math` (maths OpenStax wrote as HTML text, which has no gold MathML; only with evidence of a formula, see docs/layout.md) or `other` |
| `detection_precision_excluding_prose_math` | matched / (predicted - `prose_math`): an estimate, since the gold cannot confirm those are formulas |
| `split_gold_equations` | gold equations the pipeline found as several pieces |
| `static_violations` | failed error-level checks from `html_checks.static_checks` on the output HTML, summed over pages (warnings excluded) |
| `axe_violations` | same for `html_checks.axe_checks`, over the pages where axe ran |
| `axe_checked_pages` | pages where axe-core really ran (`axe_violations` is over these) |
| `axe_skipped_pages` | pages where axe could not run: `axe_checks` returned its single "axe" check coded `skipped` (Playwright or Chromium not installed) |
| `axe_error_pages` | pages where axe broke: a single failed "axe" check (WP4 codes it `axe_error`). Neither case counts as zero violations. Rule results are named `axe.<rule>`, so a failure of axe's real `skip-link` rule still counts as a violation |
| `page_errors` | `LayoutError` codes for pages the pipeline could not read; such a page counts as "found nothing", it does not stop the run |
| `review_load` | see "Review load": every kind's decisions per section and per PDF page, optional notes per PDF page, `equation_flags` (matched equations, wrong = not `exact_lenient`; also `equation_flags_excluding_scorer_gaps`), `heading_flags` (the headings task's rule, on the same PDF), `flags` and `flags_excluding_scorer_gaps` (both together), and `wrong_not_required` |

TODO(Harish): document precision is now limited mostly by prose maths (in
document-run1, 25 inline formulas OpenStax wrote as HTML, with no gold MathML). Decide
whether to add them to the gold set (hand-written MathML for those spans) or keep
scoring them apart as `detection_precision_excluding_prose_math`; the report gives both
numbers until then.

### Review load (equations, headings, document)

How much the pipeline asks of a person, and whether it asks about the right things
(docs/design/beta-architecture.md "Review tiers").

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
`latest-<task>.json`. Tolerances live in `data/eval/tolerances.json`; an entry with only
`name` and `limit` adjusts a default. The tables task also has absolute floors, checked
with or without a baseline (see "Tables": "Gate").

## Report format

`eval pipeline` writes one JSON report per task (default `data/eval/latest-<task>.json`):

| Field | Content |
|---|---|
| `schema_version`, `task`, `date` | `date` comes from `--date` (default: today, taken in the CLI) |
| `pipeline_version`, `prompt_versions` | `run.PIPELINE_VERSION`; prompt versions as seen on the model calls, module constants when no call ran. 0.3.0 (2026-10-08) changed the review rules (a sign or relation that differs from the text layer is a required decision): reports before 0.3.0 used the old review policy, so compare review-load numbers (required decisions, flags) only between reports of the same pipeline version. 0.4.0 (2026-10-09) added the content features (docs/design/content-features.md) without changing equations, figures or headings while the flags are off, so 0.4.0 and 0.3.0 reports compare. Layout detects tables whatever the flags say, and each data table kept as an image is a required decision of its own; on the document gold it claims no data table (its two ruled key-term grids are claimed as grids of terms and written as lists, `tables.is_term_grid`, with no decision). Chart data tables read from the image are optional extras, never a decision (since 2026-10-09; "Content features after integration"). Which version made each committed `data/eval/latest-*.json`: "Results" |
| `models`, `models_used`, `client` | configured main/cheap (and judge) ids, ids actually called, `openai` or `fake` |
| `dataset` | `gold_dir`, `manifest_sha256`, `limit`, `unit`, `selection_sha256` (hash of the planned item ids) |
| `n_planned`, `n`, `complete`, `stopped_reason` | `stopped_reason` is a budget category (e.g. `budget_job_usd`) when spend ran out |
| `metrics` | per task, as above |
| `cost`, `latency_ms` | as above |

`--items PATH` also writes one JSON line per item (per page for headings and document),
including predicted LaTeX, MathML or alt text. These come from public OpenStax pages; the
harness never reads anything from `private-inputs/`, except the tables task's
real-document check, which reads `private-inputs/tables/` and reports counts only (see
"Tables").

## How to rerun

One-time setup:

```
uv sync --all-groups
uv run playwright install chromium
uv run a11y-stem gold fetch --book physics --pages 3 --chapter 2 --date 2026-10-06
```

`gold fetch` also fills `.cache/openstax/`, which holds the page XHTML the page PDFs are
printed from and the equation contexts are read from. After that, rendering is offline
except for figure images (downloaded once).

Real runs need `OPENAI_API_KEY` in `.env` and a spend cap. Each model call reserves its
worst case before it is sent (about $0.020 on gpt-6.1-sol, $0.001 on gpt-6-luna, from
`estimate_max_cost_usd`), so the cap must stay above actual spend by at least one
reservation. When a reservation is refused the run stops, keeps what finished, and marks
the report partial.

```
uv run a11y-stem eval pipeline --task equations --max-usd 0.50 --items data/eval/items-equations.jsonl
uv run a11y-stem eval pipeline --task headings  --max-usd 0.01
uv run a11y-stem eval pipeline --task figures   --max-usd 0.50 --items data/eval/judged.jsonl
uv run a11y-stem eval pipeline --task document  --max-usd 2.00 --items data/eval/items-document.jsonl
```

The figures task with long descriptions on (about $0.13 for the 13 gold figures):

```
FEATURE_LONG_DESCRIPTIONS=true uv run a11y-stem eval pipeline --task figures --max-usd 1.00 --out .local/eval/figures-long.json --items .local/eval/figures-long.jsonl
```

`--limit N` takes the first N equations or figures, or the first N pages. `--fake` swaps
in the offline `FakeVisionClient` with no recorded answers: every call is refused at zero
real cost, so a fake run checks the plumbing and nothing else (all scores are 0).
`--fake --max-usd X` caps the fake's simulated cost, which exercises the budget stop.

Pre-render or inspect inputs:

```
uv run a11y-stem eval render --equations
uv run a11y-stem eval render --page 2-2-speed-and-velocity --out /tmp/page.pdf
```

Judge calibration:

```
uv run a11y-stem eval calibrate --judge data/eval/judged.jsonl --write-template data/eval/labels.csv
# Label in a spreadsheet: human_pass = yes/no, notes optional. Do not look at the judge's verdict.
uv run a11y-stem eval calibrate --judge data/eval/judged.jsonl --labels data/eval/labels.csv --out data/eval/calibration.json
uv run a11y-stem eval calibrate --probes --max-usd 0.50 --out data/eval/probes.json
```

Baselines and the gate: after reviewing a full run, promote it and commit the file
(`promote` refuses partial and fake runs, and for `latest` a report made with other
prompts or models than the code):

```
uv run a11y-stem eval promote .local/eval/equations-run3.json --as baseline
uv run a11y-stem eval promote .local/eval/equations-run3.json --as latest
uv run a11y-stem eval gate --require-current --current data/eval/latest-equations.json --baseline data/eval/baseline-equations.json
```

After a prompt or model change, re-run the task, promote the new report `--as latest`
and commit it with the change; CI's gate fails (exit 2) until you do.

TODO(Harish): no baseline is committed yet, so CI's eval-gate job skips. The committed
`data/eval/latest-<task>.json` are all pipeline 0.4.0 runs (equations, figures, document
and tables re-run on 2026-10-12, headings on 2026-10-09; see "Results" below); review
them and promote each as the baseline. Earlier runs are in the main checkout's
`.local/eval/`. figures-run1 was graded by the older text-only judge and cannot be a
baseline for runs graded with the image.

Weekly sample (drift check between full runs): a 5% share of the gold units, seeded by
the ISO week, scored like any other run (`eval/sample.py`). Reports go to
`.local/eval/weekly/<week>-<task>.json` (git-ignored):

```
uv run a11y-stem eval sample --task equations --max-usd 0.10
uv run a11y-stem eval sample --task figures --fraction 0.05 --week 2026-W41 --max-usd 0.10
```

TODO(Harish): decide whether to schedule it (it spends money; `eval-live.yml` is
manual-only on purpose), and whether opted-in production pages should join the sample
once their review labels exist as gold.

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
uv run a11y-stem demo build --page 2-2-speed-and-velocity --max-usd 1.00
```

It prints the page to PDF, converts it, and writes `index.html` (the converted page with a
short note saying it is a precomputed demo of an OpenStax CC BY page, with attribution
and a link to the original), `report.json` (the `DocumentResult` without the HTML) and
`source.pdf` (the exact input) to `src/a11y_stem/web/static/demo/`. Pages outside the
gold set are looked up live and pass the same license gate as `gold fetch`.

## Results (pipeline 0.4.0, 2026-10-12, gpt-6.1-sol and gpt-6-luna)

Reports: `data/eval/latest-<task>.json`. Sample: 3 sections of OpenStax *Physics* ch. 2 (CC
BY 4.0). Small: read the confidence intervals before reading the point estimates.

Pipeline 0.4.0 (the content features, every flag off, as in production): all the
committed reports are 0.4.0 runs. Equations, figures and document were re-run with the
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
prose made maths), 0 static and 0 axe violations. These replace the estimate in "What
this gives for the targets" below: flags off 0.233 (target 0.25), flags on 0.209 (target
0.28).

| Task | n | Headline | Cost |
|---|---|---|---|
| equations | 30 | exact_lenient 0.767 (95% CI 0.591-0.882; ceiling 0.833 with known-correct LaTeX), mean TEDS 0.864 | $0.0017 per equation |
| headings | 121 | recall 1.000 (CI 0.969-1.000), level accuracy 0.992, precision 0.877 (every extra is NEEDS_REVIEW) | no model calls |
| figures | 13 | image-aware judge pass 1.000 (CI 0.772-1.000), factual errors 0 (judge not yet calibrated) | $0.0039 per figure |
| document | 3 sections, 43 PDF pages | equation recall 1.000, precision 0.545 (0.968 excluding prose maths), matched exact_lenient 0.733, 0 split equations, 0 axe and 0 static violations; 0.233 required decisions per PDF page | $0.0034 per PDF page |
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

A review of the check printed its own equations with Tectonic in 8 maths font setups,
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

Status: the task, its gold and its gate are built (branch `feat/tables-eval`), and since
the integration (2026-10-09) it runs against the real detector and table step: the first
in-repo numbers are in "Content features after integration" below. The scorer was
checked against the research benchmark first (below). Code: `eval/tables.py`; design: `PDF-RECOMMENDATION.md` 4.6 and
its challenge (Harish's research notes, not in the repo).

### Gold

| Set | Where | Licence | Documents | Data tables | Layout tables |
|---|---|---|---|---|---|
| OpenStax | `data/gold/tables/` (13 pages of *Physics* and *Statistics*, table records only) | CC BY 4.0 | 13 pages x 3 print styles = 39 | 54 x 3 = 162 | 10 x 3 = 30 (the "Key Terms" grids) |
| Authored | `data/authored/tables/` (`README.md` there) | MIT | 6 | 35 (32 source tables; 3 run over two pages) | 3 |

- **OpenStax pages.** `a11y-stem gold fetch --set tables --date 2026-10-08` fetches them
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
  (exported by LibreOffice) toolchains, explicit row headers, two-level headers, maths in
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
| `detection_recall`, `detection_precision` | A predicted data table matches a gold table on the same page by content (gold has no boxes): word-token F1 >= 0.5, or F1 of the letters and digits >= 0.7 for maths-heavy tables (ranked on one scale as F1 - 0.2), pairs taken greedily, best first, one to one. A prediction matched to a layout table counts neither way. Predictions the pipeline itself called layout tables (`is_data` False) are not data tables and take no part |
| `teds`, `teds_found` | TEDS (Zhong et al. 2020): 1 - TED / max(nodes) over table -> row -> cell trees. Unit cost to delete or insert a node; relabelling a cell costs 1 for another tag or span, else the normalised Levenshtein distance of its text (`eval/ted.py` relabel hook). Text is NFKC, one delta, one minus, no spaces. `teds` counts a missed table as 0; `teds_found` is over found tables only. Headers are ignored, as in table-bench, so its numbers reproduce |
| `teds_structure` (`_found`) | the same with cell text ignored |
| `teds_with_headers` (`_found`) | the same, with a header cell (and its direction) a different tag from a data cell |
| `headers.column`, `headers.row` | precision, recall and F1 of header cells as (row, column, scope, text), micro-averaged over found tables; `exact`: share of gold tables with such headers whose header texts (a multiset) equal gold's; a missed table is not exact. Header texts are compared after `header_text` (2026-10-09): a raised mark the grid read from the PDF writes "^([1])" counts as "[1]", as gold's text drops the raise |
| `column_header_exact` | `headers.column.exact`: the headline header number (OpenStax marks only column headers, so row headers proposed for an identifying first column are a convention gap, counted apart as `row_headers_where_gold_has_none`). `headers.share_found_without_headers`: found tables given no header cell at all |
| `header_exact` | table-bench's: all header texts equal gold's |
| `data_vs_layout` | `accuracy_on_negatives`: layout tables no data-table prediction matched; `data_called_layout`: missed data tables a layout-table prediction covers |
| `maths` | per gold maths cell: best letter/digit F1 of any predicted cell (`letter_digit_f1`, `cells_ok` at >= 0.9), and, when the gold cell is one formula, whether the predicted cell there has MathML equal to gold after the lenient normalisation of `eval/equations.py` (`mathml_match`) |
| `text_fidelity` | cells whose text is gold's (exact after `fidelity_text`, else the same letters and digits); `wrong_cells` per table = max(predicted cells left over, gold cells left over), so a misread number is one, two cells merged into one are two; `wrong_cells_not_flagged`: wrong cells in found tables whose fix is not a required decision. Must be 0. `fidelity_text` (2026-10-09) makes the ways gold and a reader write the same thing one: gold's text drops a superscript's raise ("10–18" for 10⁻¹⁸) while a cell writes 10^-18, 10^{-18} or 10⁻¹⁸; gold's stacked 1/3 reads "13" ("1/3" and ⅓ too); a "____" fill-in blank is an empty cell, however long; a raised mark the grid read from the PDF writes "^([1])" is "[1]"; and a gold footnote row (`<tfoot>`, printed under the table) counts only when the prediction has it. TEDS keeps table-bench's normalisation |
| `by_style`, `by_feature` | recall, precision, TEDS and column headers per print style and authored, and for tables with spans, maths or row headers |
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
`pages` (optional) limits the check to the pages that were labelled. A predicted data
table is a real table when it shares at least half of the smaller box with a labelled one;
a labelled table is found at full extent when one prediction covers at least 90% of it and
at least 75% of that prediction lies inside it (not cut short, not merged with a plot).
`metrics.real_documents` holds counts only: documents, pages, labelled, predicted and true
tables, `precision`, `full_extent_recall` (Wilson intervals), `any_extent_recall`, error
codes and the labels file's SHA-256. No file name, page or text leaves the private folder.
The research's four public manuals are copyrighted: use them for local work only, never
commit them or their labels.

TODO(Harish): add 3-5 of your own course PDFs (or colleagues' with permission) to
`private-inputs/tables/` and label their tables; the real-document gate has no data
until then.

### Gate

`a11y-stem eval gate --current data/eval/latest-tables-local.json` checks the floors with
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
uv run a11y-stem gold fetch --set tables --date 2026-10-08     # only to refresh the gold
uv run a11y-stem eval pipeline --task tables --mode local --items .local/eval/tables-local.jsonl
uv run a11y-stem eval pipeline --task tables --mode model --max-usd 1.00
uv run a11y-stem eval gate --current data/eval/latest-tables-local.json
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
| data tables | 3 (the position-time graphs with marked or labelled points), values within tolerance 3 of 3, 0 wrong cells | |
| values from the PDF text | 0 of 3 (the inputs are images) | |
| decisions added | 3 required (`values_from_image`; since 2026-10-09 a note, the table an optional extra: 0 required), 0 optional notes (no description marked unsure), 0 colour-only flags | 0 |
| cost | $0.0040 per figure for the step; $0.124 for the run with both judges | $0.0035 per figure |
| step latency | p50 7.7 s, p95 15.9 s (sum of call latencies) | p50 5.8 s, p95 10.0 s |

Reading it:

- The long descriptions follow the W3C two-part pattern: axes with units and ranges,
  the trend, the labelled points, and "about" for values read off an axis. The judge
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
uv run --env-file .env a11y-stem eval pipeline --task equations --max-usd 0.15 \
  --notation "bold d" "bold v" "bold a" \
  --out .local/eval/notation/equations-notation.json --items .local/eval/notation/items-equations-notation.jsonl
uv run python -m a11y_stem.eval.notation_flags .local/eval/notation/items-equations-notation.jsonl \
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
  prose") are scored by `python -m a11y_stem.eval.bold_letters --openstax`: 9 of 9 on the
  three gold pages and 11 of 11 on the LaTeX fixtures (the two-column handout's 10 bold
  vectors and its bold omega), no false positives. The equations task does not see them:
  it scores the gold's equations, not its prose.

## Content features after integration (2026-10-09, pipeline 0.4.0)

The first runs of the integrated branch (`feat/content-integration`: all eleven content
builders merged). Reports and per-item rows are kept local (`.local/integration/smoke/`),
not promoted: the committed `latest-*.json` stay the baselines until Harish decides.
Real spend for everything below and the conversions in docs/design/content-features.md
("Status after integration"): $1.00 of a $1.50 cap.

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
| predicted equations (unmatched "other") | 55 (1) | 64 (10: bold letters in prose made maths, review none) |
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

So the floor of 0 needs either a scorer that knows these spellings or a few labelled
exceptions before it can gate model runs (TODO(Harish) in gate.py stands). Superscript
powers written as `^` in text cells are a prompt issue for the table step to take up.
Both were done afterwards (below): the scorer knows these spellings (`fidelity_text`),
and the wrapped row is joined back; the model runs are not repeated yet.

### Demo and fixtures (smoke, real models)

| input | pages | required decisions | what they are | cost |
|---|---|---|---|---|
| `tests/fixtures/layout/latex/tables.pdf` | 4 | 6 | equations only; all 5 detected tables rebuilt and ACCEPTED on gpt-6-luna, every check passed | $0.019 |
| `tests/fixtures/layout/latex/problem_set.pdf` | 1 | 0 | | $0.007 |
| `tests/fixtures/layout/latex/links_language_colour.pdf` | 1 | 0 | 4 web links, 4 cross-references and 2 internal links all resolve; the French passage is marked; 2 colour classes, the lowest at 4.53:1 | $0.003 |
| demo `source.pdf` (OpenStax Physics 2.3) | 13 | 9 (0.3.0 demo: 3) | the same 3 headings; 5 chart data tables read from the image; 1 equation whose bold v the model dropped (a right catch) | $0.066 |

All four pass the static checks and axe. The committed demo was rebuilt afterwards with
every flag off ("Public demo" above): 4 required decisions, the same 3 headings and
again equation 2.5's dropped bold v.

## Review load after the meaning-level changes (2026-10-09, evening)

What changed (docs/design/content-features.md "Review tiers"; Harish's policy: additive
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
| predicted equations | 55 | 64 (bold letters in prose made maths) |
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
next page's part (one row under a repeated header, no rules) is not detected, so the
row ends "(current" and the rest is read as headings and paragraphs (a detection miss of
unruled tables, docs/tables.md "Known misses"; not fixed: tables get no further work
for now).

## Page types (2026-10-09, no model)

`pipeline.page_type` decides whether a page's text layer can be trusted and whether it
is handwritten or scanned (docs/layout.md "Hidden text and page types"). Its scorer:

```bash
uv run python -m a11y_stem.eval.page_types --labels LABELS.json [--labels ...] [--out rows.json]
```

A labels file is a JSON list of `{"file", "page" (1-based), "label" (a page kind),
"split", "group"}`; relative file names are read from the labels file's folder or the
one above it. It prints the confusion matrix of page kinds for all pages and per split,
the matrix of what the app does with each page ("text", "text_and_ink_images",
"page_image", "nothing"), every error with its counts, and the time per page. Kinds,
counts and file names only: never page text. No model is called.

The labelled sets are not in this repository: the research corpus holds derivatives of
a real lecturer's handwritten notes and PDFs from local research folders, and lives with
the research (`a11y-research/handwriting/detect/corpus/labels_all.json`, 996 pages). The
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
demo source; the authored tables slides lose a figure made of their table's coloured
cells (the white lines between them are now seen, so the table's grid is drawn), with
the tables eval (local) the same to the last digit; and one handwriting fixture,
`overlays.pdf`, changes as intended (its page under a transparent picture of hand-drawn
circles keeps its image; its half-page striped mask is a figure). The 110 sweep PDFs: 3 LOST verdicts left, all
the generator's subtitle that was never written (41 before), 56 converted, 38 kept with
a decision, 13 refused; only the three typed controls say "Everything was converted".
On the research corpus (909 pages; labels as in "Page types"), no page labelled
born-digital keeps its image or changes kind. Twelve of them change: three keep the
same elements (a figure gains its white lines), seven gain content that was dropped
before (a report's two screenshots under their captions and a brochure's cover
pictures, once taken for backgrounds; a scatter of TikZ shapes and five flat spring
drawings under 24 pt; icons at a page edge; a small mark at a page's foot; one hand
mark, as a handwriting region), and two pages of machine-drawn loops the classifier
already calls `mixed_ink` (a known error) gain handwriting regions (a needless
decision). Three `vector_no_text` pages (two of typed text turned into outlines, a
hatched pie chart that became no figure) now keep their image.

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
- **No conformance claim.** a11y-stem assists people making course material accessible.
  None of these numbers establishes conformance with WCAG or any other standard.

## Files

| Path | Role |
|---|---|
| `src/a11y_stem/eval/render.py` | equation PNGs, printable page HTML and PDF, image cache, demo wrapper |
| `src/a11y_stem/eval/pipeline_eval.py` | the four tasks, metrics, report, markdown summary |
| `src/a11y_stem/eval/judge.py` | figure judge request, schema, pass rule; the long-description judge (`judge_long`) |
| `src/a11y_stem/models/prompts/judge_long_description.md` | the long-description and data-table rubric (versioned by hash) |
| `data/eval/figures-long-descriptions.json` | the figures report with long descriptions on (2026-10-09) |
| `data/eval/document-flags-on.json` | the document report with every content flag on (pipeline 0.4.0, 2026-10-12) |
| `src/a11y_stem/eval/verifier_metrics.py` | the second automated check's eval metrics |
| `src/a11y_stem/models/prompts/judge_figure.md` | judge instructions (versioned by hash) |
| `src/a11y_stem/eval/calibration.py` | kappa, bootstrap CI, label template, bias probes |
| `src/a11y_stem/eval/gate.py` | regression gate (comparability, judge and stale-report checks) |
| `src/a11y_stem/eval/sample.py` | the weekly seeded sample of the gold set |
| `src/a11y_stem/eval/bold_letters.py` | bold letters in running text: found against the LaTeX and OpenStax sources (offline) |
| `src/a11y_stem/eval/notation_flags.py` | course notation as style evidence: new flags on stored answers, with and without the text layer's styles (offline) |
| `src/a11y_stem/cli/eval_cli.py` | `eval pipeline/gate/calibrate/render`, `demo build` |
| `src/a11y_stem/eval/tables.py` | the tables task: documents, page assignment, matching, TEDS, headers, maths, fidelity, review load, real documents, report |
| `src/a11y_stem/eval/ted.py` | tree edit distance with a relabel-cost hook (equations, table TEDS) |
| `data/gold/tables/` | table gold: 13 OpenStax pages (CC BY), `reference_pages.json` |
| `data/authored/tables/` | authored table documents (MIT): sources, PDFs, gold, `make_authored.py` |
| `tests/fixtures/tables/table_bench_excerpt.json` | table-bench predictions and its scores on five documents |
| `src/a11y_stem/eval/page_types.py` | the page-type scorer: confusion matrices, actions, errors, time (no model) |
| `data/eval/tolerances.json` | gate tolerances |
| `tests/test_eval_*.py` | offline tests; `-m browser` runs the Chromium ones |
