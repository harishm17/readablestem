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

TODO(Harish): document precision is now limited mostly by prose maths (in
document-run1, 25 inline formulas OpenStax wrote as HTML, with no gold MathML). Decide
whether to add them to the gold set (hand-written MathML for those spans) or keep
scoring them apart as `detection_precision_excluding_prose_math`; the report gives both
numbers until then.

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
`name` and `limit` adjusts a default.

## Report format

`eval pipeline` writes one JSON report per task (default `data/eval/latest-<task>.json`):

| Field | Content |
|---|---|
| `schema_version`, `task`, `date` | `date` comes from `--date` (default: today, taken in the CLI) |
| `pipeline_version`, `prompt_versions` | prompt versions as seen on the model calls; module constants when no call ran |
| `models`, `models_used`, `client` | configured main/cheap (and judge) ids, ids actually called, `openai` or `fake` |
| `dataset` | `gold_dir`, `manifest_sha256`, `limit`, `unit`, `selection_sha256` (hash of the planned item ids) |
| `n_planned`, `n`, `complete`, `stopped_reason` | `stopped_reason` is a budget category (e.g. `budget_job_usd`) when spend ran out |
| `metrics` | per task, as above |
| `cost`, `latency_ms` | as above |

`--items PATH` also writes one JSON line per item (per page for headings and document),
including predicted LaTeX, MathML or alt text. These come from public OpenStax pages; the
harness never reads anything from `private-inputs/`.

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

TODO(Harish): no baseline is committed yet, so CI's eval-gate job skips. The real runs
are in the main checkout's `.local/eval/` (equations-run3, headings-run1, figures-run2,
document-run1); review them and promote each as both baseline and latest. Note that
figures-run1 was graded by the older text-only judge and cannot be a baseline for runs
graded with the image.

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

Public demo (do not commit the output until the page is chosen):

TODO(Harish): choose the demo page and approve the spend for a real-model build
(about $0.05 for one section at the first document run's cost), then commit the
output. Until then `/demo` shows the "being prepared" page and `ops launch-check`
lists the demo as unfinished; a `--fake` build is marked as a placeholder and is
flagged too.

```
uv run a11y-stem demo build --page 2-2-speed-and-velocity --max-usd 1.00
```

It prints the page to PDF, converts it, and writes `index.html` (the converted page with a
short note saying it is a precomputed demo of an OpenStax CC BY page, with attribution
and a link to the original), `report.json` (the `DocumentResult` without the HTML) and
`source.pdf` (the exact input) to `src/a11y_stem/web/static/demo/`. Pages outside the
gold set are looked up live and pass the same license gate as `gold fetch`.

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
| `src/a11y_stem/eval/judge.py` | figure judge request, schema, pass rule |
| `src/a11y_stem/models/prompts/judge_figure.md` | judge instructions (versioned by hash) |
| `src/a11y_stem/eval/calibration.py` | kappa, bootstrap CI, label template, bias probes |
| `src/a11y_stem/eval/gate.py` | regression gate (comparability, judge and stale-report checks) |
| `src/a11y_stem/eval/sample.py` | the weekly seeded sample of the gold set |
| `src/a11y_stem/cli/eval_cli.py` | `eval pipeline/gate/calibrate/render`, `demo build` |
| `data/eval/tolerances.json` | gate tolerances |
| `tests/test_eval_*.py` | offline tests; `-m browser` runs the Chromium ones |
