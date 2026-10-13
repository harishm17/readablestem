# Proof: Development sample: OpenStax Physics, chapter 2 (not held out)

a11y-stem assists a person who makes course material accessible. Nothing here measures conformance with WCAG or any other standard, and no number below may be read as a claim that a document is accessible. Automated HTML checks are reported as what a machine can detect. Judge numbers are agreement with a model judge, not accuracy.

> **Development set, not held out.** These sections were available while a11y-stem was built. The numbers show that the pipeline and this harness work end to end; they are not held-out results.

> **Incomplete.** Not computed in this build: Invented-content rate; Comparison with the chatbot recipe; Comparison with open PDF parsers; Human labels and judge agreement.

## Status

|  |  |
|---|---|
| Proof set | `smoke-physics-2` (development) |
| Mode | replay of a recorded live run: no model call, no key, no cost |
| Run recorded on | 2026-10-13 |
| Code | commit `c2773682c1c9` |
| Pipeline version | 0.4.0 |
| Models | main gpt-6.1-sol, cheap gpt-6-luna, second check gpt-6-luna |
| Prompt versions | equation equation-df0e12f1, figure figure-ac51f42d, judge judge_figure-137e2607, table table-b3769ce7 |
| Recorded spend | $0.2807 |
| Runs | document x1, equations x1, headings x1, figures x1 |

**Pins checked before the run**

| Pin | Result | Detail |
|---|---|---|
| gold manifest | ok | sha256 63b5de054889 |
| gold files | ok | 3 files match the manifest |
| gold sections | ok | 3 sections |
| recorded run | ok | 13 files match the manifest |

Replay compared with the recorded reports: **identical** (4 report(s): every metric and cost, timing excluded).

Replayed 149 model requests and 134 second-check requests from the bundle; 0 had no recorded answer.

**Parts of this proof**

| Part | Status |
|---|---|
| a11y-stem, production defaults | ok |
| Invented-content rate | not_wired |
| Comparison with the chatbot recipe | not_wired |
| Comparison with open PDF parsers | not_wired |
| Human labels and judge agreement | not_wired |

## The 3 sections

The three sections of OpenStax Physics (high school), chapter 2, that a11y-stem was developed on: the sample gold set in data/gold. This set exists so that the whole proof command, from the answer key to PROOF.md, can be run and checked by anyone and in CI. Its numbers say that the pipeline and the harness work end to end. They are not held-out results.

| Section | Title | PDF pages | Equations (block / inline) | G1 | Figures (with OpenStax alt) | Tables | Headings (in boxes) | PDF sha256 |
|---|---|---|---|---|---|---|---|---|
| physics/2-1-relative-motion-distance-and-displacement | 2.1 Relative Motion, Distance, and Displacement | 16 | 8 (1 / 7) | 0 | 6 (6) | 1 | 48 (41) | `dd3f3d850ccd` |
| physics/2-2-speed-and-velocity | 2.2 Speed and Velocity | 14 | 12 (10 / 2) | 7 | 2 (2) | 1 | 42 (35) | `5e2cf1e6f178` |
| physics/2-3-position-vs-time-graphs | 2.3 Position vs. Time Graphs | 13 | 10 (6 / 4) | 4 | 5 (5) | 1 | 31 (25) | `80ca81e557fc` |
| **Total** |  | **43** | **30** | **11** | **13** | **3** | **121** |  |

The PDF is the one every system was given. G1: equations whose gold MathML packs operators into text, which exact match can fail even when the answer is right.

- OpenStax Physics is a live CC BY 4.0 book. These three sections are also the gold set in data/gold and the sample behind docs/eval.md.

## Results: a11y-stem

Production settings (`Settings()` defaults: main model gpt-6.1-sol, cheap model gpt-6-luna, every content flag off, the second automated check on). Tables are off in production, so a detected data table is kept as an image with a required decision. Intervals: cluster bootstrap by section (B = 10,000, seed 0), which is approximate with this few sections and tends to be a little narrow; the Wilson interval is shown beside it for comparison and treats items as independent.

### a11y-stem on the whole section (document task)

**Equations through the whole pipeline (document task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| **end_to_end_exact_lenient (all gold equations)** | 0.7333 | 22/30 | [0.6000, 1.0000] | [0.5555, 0.8582] | 0.7333 |
| end_to_end_exact_lenient, without G1 equations | 0.8947 | 17/19 | [0.8000, 1.0000] | [0.6861, 0.9706] | 0.8947 |
| **unflagged_wrong_per_100 (wrong, not G1, not in a required decision)** | 3.33 | 1/30 | [0.00, 8.33] | n/a | 3.33 |
| matched_exact_lenient (aligned pairs only) | 0.7333 | 22/30 | [0.6000, 1.0000] | [0.5555, 0.8582] | 0.7333 |
| detection_recall | 1.0000 | 30/30 | [1.0000, 1.0000] | [0.8865, 1.0000] | 1.0000 |
| detection_precision | 0.5455 | 30/55 | [0.3810, 0.8000] | [0.4152, 0.6697] | 0.5455 |
| detection_precision, without prose maths | 0.9677 | 30/31 | [0.9091, 1.0000] | [0.8381, 0.9943] | 0.9677 |

Rates have every gold equation as denominator; an equation with no aligned output counts as wrong. G1 equations (operators packed into OpenStax text) can fail exact match even when the answer is right. `unflagged_wrong_per_100` is wrong aligned equations that no required decision pointed at, per 100 gold equations.

**Review load (what a person is asked to decide)**

| Measure | Value |
|---|---|
| PDF pages converted | 43 |
| Required decisions | 10 |
| Required fixes (several can share one decision) | 42 |
| **Required decisions per PDF page** | **0.2326** |
| Optional notes per PDF page | 0.5581 |
| Equation flags: precision | 1.0000 |
| Equation flags: recall | 0.1250 |
| Heading flags: precision | 0.5556 |
| Heading flags: recall | 0.7778 |
| Wrong items not asked about | 11 |

Flag precision is the share of required decisions that point at a real error; recall is the share of real errors a person is asked about. Both are measured against the gold set only; figures and equations with no gold partner have no answer key here.

**Reasons behind the required fixes**

| Reason | Fixes |
|---|---|
| heading.repeated_label | 41 |
| heading.weak_evidence | 41 |
| style_mismatch | 1 |

Counted per fix, and a fix can have several reasons, so the counts add up to more than the number of required decisions.

**Predicted equations with no gold partner**

| Kind | Count |
|---|---|
| prose_math | 24 |
| fragment | 0 |
| other | 1 |

`prose_math`: maths the source wrote as plain text, so the gold has no MathML for it; `fragment`: a piece of a gold equation or of a broken formula.

**Per section (runs pooled)**

| Section | PDF pages | Gold equations | Exact | Rate | Required | Calls | Cost |
|---|---|---|---|---|---|---|---|
| physics/2-1-relative-motion-distance-and-displacement | 16 | 8 | 8 | 1.0000 | 3 | 43 | $0.0594 |
| physics/2-2-speed-and-velocity | 14 | 12 | 8 | 0.6667 | 3 | 17 | $0.0319 |
| physics/2-3-position-vs-time-graphs | 13 | 10 | 6 | 0.6000 | 4 | 27 | $0.0509 |

Counts are summed over the runs. Cost includes the second automated check.

**Per book (point estimates; each rests on a few sections)**

| Book | Sections | End-to-end exact (lenient) | Count | Heading F1 |
|---|---|---|---|---|
| Physics | 3 | 0.7333 | 22/30 | 0.9344 |

### Equations as pictures (equations task)

**Each gold equation as a picture (equations task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| exact_lenient | 0.7667 | 23/30 | [0.6667, 1.0000] | [0.5907, 0.8821] | 0.7667 |
| exact_lenient, without G1 equations | 0.9474 | 18/19 | [0.8000, 1.0000] | [0.7536, 0.9906] | 0.9474 |
| exact_strict | 0.4000 | 12/30 | [0.1250, 0.5000] | [0.2459, 0.5768] | 0.4000 |
| mean_teds | 0.8648 | 25.94/30 | [0.7625, 0.9039] | n/a | 0.8648 |
| proposal_rate | 1.0000 | 30/30 | [1.0000, 1.0000] | [0.8865, 1.0000] | 1.0000 |
| unflagged wrong per 100 equations | 3.33 | 1/30 | [0.00, 8.33] | n/a | 3.33 |

Each equation is rendered to an image with its text layer and its surrounding text, as the whole-document run would give it. The picture comes from a browser print of the gold MathML, so it is cleaner than a crop from a real PDF.

**By status the pipeline gave the answer**

| Status | n | Share | exact_lenient | mean TEDS |
|---|---|---|---|---|
| accepted | 30 | 1.0000 | 0.7667 | 0.8648 |

Accepted answers should be more accurate than answers sent to review.

**By display**

| Display | n | Share | exact_lenient | mean TEDS |
|---|---|---|---|---|
| block | 17 | 0.5667 | 0.5882 | 0.8634 |
| inline | 13 | 0.4333 | 1.0000 | 0.8667 |

### Headings (headings task)

**Headings found in the printed sections (headings task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| **heading F1** | 0.9344 | n/a | [0.9130, 0.9600] | n/a | 0.9344 |
| heading precision | 0.8768 | 121/138 | [0.8400, 0.9231] | [0.8116, 0.9216] | 0.8768 |
| heading recall | 1.0000 | 121/121 | [1.0000, 1.0000] | [0.9692, 1.0000] | 1.0000 |
| level_accuracy (among matched headings) | 0.9917 | 120/121 | [0.9792, 1.0000] | [0.9547, 0.9985] | 0.9917 |

A PDF has no heading levels, so level accuracy compares each heading's level relative to the top level on its page. The headings task makes no model call and runs once. Most gold headings sit inside boxes (examples, notes, exercises); see recall by context.

**Recall by where the heading sits**

| Context | Gold headings | Recall |
|---|---|---|
| body | 20 | 1.0000 |
| learning-objectives | 3 | 1.0000 |
| links-to-physics | 2 | 1.0000 |
| note | 7 | 1.0000 |
| os-exercise | 2 | 1.0000 |
| os-teacher | 50 | 1.0000 |
| snap-lab | 6 | 1.0000 |
| teacher-demonstration | 2 | 1.0000 |
| tips-for-success | 7 | 1.0000 |
| virtual-physics | 2 | 1.0000 |
| watch-physics | 4 | 1.0000 |
| work-in-physics | 2 | 1.0000 |
| worked-example | 14 | 1.0000 |

### Figure descriptions (figures task)

**Alt text for each figure with an OpenStax reference (figures task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| judge_pass_rate (agreement with a model judge) | 1.0000 | 13/13 | [1.0000, 1.0000] | [0.7719, 1.0000] | 1.0000 |
| judge_factual_error_rate (among judged) | 0.0000 | 0/13 | [0.0000, 0.0000] | [0.0000, 0.2281] | 0.0000 |
| proposal_rate | 1.0000 | 13/13 | [1.0000, 1.0000] | [0.7719, 1.0000] | 1.0000 |
| check_pass_rate (the step's own deterministic checks) | 1.0000 | 13/13 | [1.0000, 1.0000] | [0.7719, 1.0000] | 1.0000 |

The judge is a model (gpt-6.1-sol) that sees the image, the candidate, the OpenStax alt text and the caption, never which system wrote the candidate. Its pass rate is agreement with that judge, not accuracy; it is not yet calibrated against human labels. A figure with no proposal counts as a fail. Judge cost is reported apart.

**Figures by status**

| Status | n |
|---|---|
| accepted | 13 |

### Cost and time

**Spend by run (list prices; models.pricing)**

| Run | Items | Model calls | Pipeline cost | Judge cost | Pipeline cost per PDF page |
|---|---|---|---|---|---|
| document run 1 | 3 | 87 | $0.1421 | n/a | $0.00331 |
| equations run 1 | 30 | 36 | $0.0509 | n/a | n/a |
| headings run 1 | 3 | 0 | $0.0000 | n/a | n/a |
| figures run 1 | 13 | 26 | $0.0471 | $0.0405 | n/a |

Pipeline cost includes the second automated check. Judge cost is never counted as the pipeline's. The per-page figure is for the document task only.

**Cost per PDF page across sections and runs (document task)**

| Median | p95 | Min | Max |
|---|---|---|---|
| 0.00371 | 0.00389 | 0.00228 | 0.00391 |

**Seconds per PDF page, as measured in the recorded live run (document task)**

| Median | p95 | Min | Max |
|---|---|---|---|
| 3.58608 | 5.49811 | 2.88243 | 5.71056 |

Wall clock of each section's conversion divided by its PDF pages, with the production concurrency (4 threads) on the recording machine. A replay cannot measure this, so it is read from the bundle.

**Sections over the app's per-job limits (production-configuration failures)**

| Section | Run | PDF pages | Calls / limit | Cost / limit |
|---|---|---|---|---|
| none |  |  |  |  |

The eval spends from one run-level budget and does not apply the app's per-job limits; a section listed here would have stopped part-way in the app.

### Runs that did not finish, and sections that failed

**Completion by run**

| Run | Complete | Stopped because | Sections the layout could not read |
|---|---|---|---|
| document run 1 | yes | none | none |
| equations run 1 | yes | none | none |
| headings run 1 | yes | none | none |
| figures run 1 | yes | none | none |

## Invented-content rate

**Not computed in this build.** The stage `invented_content` is a placeholder; its module `a11y_stem.proof.invented` is not installed, so this report shows no number for it.

It will show:

- per system and per type (prose sentence, number, heading, equation, figure description, table cell): the share of output units the source PDF does not support, with cluster intervals;
- the rule's audited precision and recall, from a blind sample labelled by the author, next to the raw rate.

It will read:

- the PDF text layer of every section (`media/pdfs/`);
- the HTML or intermediate output of every system (`systems/`).

## Comparison with the chatbot recipe

**Not computed in this build.** The stage `chatbot_comparison` is a placeholder; its module `a11y_stem.proof.comparison` is not installed, so this report shows no number for it.

It will show:

- family A of the preregistration: a11y-stem against the Purdue-prompt recipe on gpt-6.1-sol on end-to-end equation exact match, unflagged wrong equations, invented prose sentences, invented numbers and heading F1, with paired cluster intervals at the Bonferroni level;
- the expert-prompt arm and the gpt-6-luna arm as descriptive rows.

It will read:

- `systems/chatbot-*/` in the intermediate form of `proof.intermediate`.

## Comparison with open PDF parsers

**Not computed in this build.** The stage `parser_comparison` is a placeholder; its module `a11y_stem.proof.parsers` is not installed, so this report shows no number for it.

It will show:

- family B: a11y-stem against Docling, MinerU and PyMuPDF4LLM on shared measures only (heading F1, equations for the parsers that emit LaTeX), with what each tool is not built to do stated next to it;
- equation content kept in any form, reading order, images kept, tables, time and memory, as descriptive rows.

It will read:

- `systems/<parser>/` in the intermediate form of `proof.intermediate`.

## Human labels and judge agreement

**Not computed in this build.** The stage `human_labels` is a placeholder; its module `a11y_stem.proof.labels` is not installed, so this report shows no number for it.

It will show:

- the share of wrong equations that change the meaning, labelled blind by the author;
- Cohen's kappa between the figure judge and the author's labels, with a bootstrap interval, and the audited precision and recall of the invented-content rule.

It will read:

- label files the author writes after the run (none exist yet).

## What these numbers leave out

- Three sections of one book, all used while building a11y-stem: nothing here shows how it does on material it has not seen.
- One run of each task (the held-out set repeats them), so run-to-run variation is not visible.
- Exact match of MathML is strict: a correct answer can fail on markup (see G1), and a wrong digit in a long equation costs little tree-edit similarity. It is not a measure of how a screen reader would speak the result.
- With this few sections the bootstrap intervals are approximate and tend to be a little narrow. Per-book numbers are point estimates.
- The figure judge is a model and is not calibrated against human labels.
- A replay shows that the numbers follow from the recorded answers and this code. It cannot show that a new live run would give the same answers: model output varies from run to run, which is why tasks are repeated and each run's value is shown.

## Reproduce

```
uv sync --locked                      # Python 3.12+, uv; no key
make proof SET=smoke-physics-2      # replay: writes this file, no network needed
make proof-sources SET=smoke-physics-2   # also fetch the answer key from OpenStax and print the PDFs again (network, Chromium)
make proof-live SET=smoke-physics-2 MAX_USD=N   # new live run with OPENAI_API_KEY, capped
```
A replay writes byte-identical output on any machine with the same dependency versions. A live run calls the models again, so its numbers differ from the recorded ones by the usual run-to-run variation.

## Attribution

- Physics by Paul Peter Urone, Roger Hinrichs, OpenStax, version dfdfd7a, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original at https://openstax.org/details/books/physics.
- Changes: each section was reformatted as a printable page for evaluating a11y-stem, image alt text was left out of the print, and no wording was edited. a11y-stem is not affiliated with or endorsed by OpenStax.
