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
  reported separately and never included in it.
- **Latency** per item is the sum of its model calls' `latency_ms` (calls within an item
  run one after another). It excludes rendering and local processing. The document task
  uses `DocumentStats.latency_ms` per page instead.

### equations

For each gold equation: render, call `transcribe(element, png, context_text=None)`,
score the proposed MathML with `eval.equations.score_mathml`.

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
caption. It does not see the image, and nothing tells it which system wrote which text.
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
| `static_violations` | failed error-level checks from `html_checks.static_checks` on the output HTML, summed over pages (warnings excluded) |
| `axe_violations` | same for `html_checks.axe_checks` |
| `axe_skipped_pages` | pages where axe could not run (its single "skipped" warning) |
| `page_errors` | `LayoutError` codes for pages the pipeline could not read; such a page counts as "found nothing", it does not stop the run |

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
| `figure_judge_pass_rate` | figures | drops by more than 0.05 |

Exit 0: all within tolerance. Exit 1: a tracked metric regressed, or is missing from the
current report. Exit 2: not comparable, so no verdict: different task, different gold
manifest, different item selection (e.g. another `--limit`), or a partial run on either
side. Changed pipeline version, prompt versions, model ids or client are listed under the
table (they are usually why the gate is being run). Tolerances live in
`data/eval/tolerances.json`; an entry with only `name` and `limit` adjusts a default.

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
printed from. After that, rendering is offline except for figure images (downloaded once).

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

Baselines and the gate: after reviewing a full run, copy it to
`data/eval/baseline-<task>.json` and commit it. Then, for any change:

```
uv run a11y-stem eval gate --current data/eval/latest-equations.json --baseline data/eval/baseline-equations.json
```

Public demo (do not commit the output until the page is chosen):

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
  (Computer Modern) typesetting, no handwriting, no slides. Isolated equation PNGs come
  with no surrounding text, which the real pipeline has. Expect real documents to score
  lower.
- **One book.** High-school physics, three sections of one chapter, OpenStax's own
  MathML style. Calculus, chemistry notation, matrices and long derivations are not in the
  sample.
- **The scorer's ceiling.** A perfect transcriber scores exact_lenient 0.83 here (5 known
  normalisation gaps), so equation numbers understate quality by up to 17 points.
  Exact match also ignores how a screen reader speaks the result; two different MathML
  trees can read the same.
- **The judge is a model.** Its pass rate means "agrees with gpt-6.1-sol reading the
  OpenStax alt text", not "helps a blind student". It treats the reference as truth, and
  OpenStax alt texts vary from 39 to 980 characters in length and style. It does not see
  the image. Until `eval calibrate` shows good agreement with Harish's labels (and the
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
| `src/a11y_stem/eval/gate.py` | regression gate |
| `src/a11y_stem/cli/eval_cli.py` | `eval pipeline/gate/calibrate/render`, `demo build` |
| `data/eval/tolerances.json` | gate tolerances |
| `tests/test_eval_*.py` | offline tests; `-m browser` runs the Chromium ones |
