# Proof: Development sample: OpenStax Physics, chapter 2 (not held out)

a11y-stem assists a person who makes course material accessible. Nothing here measures conformance with WCAG or any other standard, and no number below may be read as a claim that a document is accessible. Automated HTML checks are reported as what a machine can detect. Judge numbers are agreement with a model judge, not accuracy.

> **Development set, not held out.** These sections were available while a11y-stem was built. The numbers show that the pipeline and this harness work end to end; they are not held-out results.

> **Incomplete.** Not computed in this build: Human labels and judge agreement.

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
| recorded run | ok | 43 files match the manifest |

Replay compared with the recorded reports: **identical** (4 report(s): every metric and cost, timing excluded).

Replayed 149 model requests and 134 second-check requests from the bundle; 0 had no recorded answer.

**Parts of this proof**

| Part | Status |
|---|---|
| a11y-stem, production defaults | done |
| Invented-content rate | done |
| Comparison with the chatbot recipe | done |
| Comparison with open PDF parsers | done |
| Human labels and judge agreement | not computed |

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

The invented-content rate of a type of output unit is the share of the system's units of that type that the source PDF does not support (preregistration 7.5). The reference is the PDF's own text layer, footer and attribution included.

Rules: `a11y_stem.eval.invented`, code fingerprint `b2f7596bdf9acae5b19e2ce3d284f7076f73c1a9f9df8896b79755f80a08f7e0`. Units: prose sentences of 7 or more words (unsupported when under 25% of their word 4-grams are in the PDF); numbers (not among the PDF's numbers); headings (no line of the PDF within difflib ratio 0.85); equations aligned to a gold equation (hold a letter, digit or Greek letter the gold equation does not); equations with no gold partner and 3 or more symbols (no matching run of the PDF's symbols, ratio 0.8); table cells.

**Primary rows** are prose sentences and numbers. a11y-stem copies prose from the PDF's text layer, so for it those rows measure the copying, plus the sentences the tool writes itself (the "About this version" section and notices), which are counted in the first table and left out in the second. The types where a11y-stem can invent are equations and figure descriptions.

Intervals: cluster bootstrap by section (the number to quote), shown as `rate (unsupported/units) [95% interval]`. With few sections that have units of a type the interval is not given.

**What the rule cannot see.** A changed word inside a sentence that keeps a quarter of its 4-grams passes the sentence rule (a changed number does not pass the number rule); a faithful paraphrase or a reordering counts as unsupported (a false positive); the PDF holds what is printed, so a correct description of a figure is never "supported" by it, which is why figures use the judge. **The audit of the rule's precision and recall (blind labels by the author, section 7.5) has not been done**: until it is, the raw rates are the only rates, and a rate for a system that re-types the page includes paraphrase the rule cannot tell from invention.

**Unsupported units / units, all text (the rate to quote)**

| Unit | a11y-stem | chatbot-luna-purdue | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|---|
| prose sentences (7+ words) | 0.019 (12/632) [0.013, 0.026] | 0.054 (62/1153) [0.016, 0.162] | 0.000 (0/619) [0.000, 0.000] | 0.000 (0/620) [0.000, 0.000] | 0.002 (1/628) [0.000, 0.006] |
| numbers in prose and headings | 0.000 (0/278) [0.000, 0.000] | 0.004 (2/477) [0.000, 0.011] | 0.000 (0/276) [0.000, 0.000] | 0.000 (0/273) [0.000, 0.000] | 0.000 (0/448) [0.000, 0.000] |
| headings | 0.021 (3/141) [0.019, 0.027] | 0.177 (40/226) [0.087, 0.361] | 0.000 (0/146) [0.000, 0.000] | 0.000 (0/133) [0.000, 0.000] | 0.000 (0/146) [0.000, 0.000] |
| equations aligned to gold (added symbol) | 0.000 (0/30) [0.000, 0.000] | 0.035 (2/57) [0.000, 0.111] | 0/0 | 0.375 (9/24) [0.286, 0.429] | 0/0 |
| equations with no gold partner | 0.000 (0/9) [0.000, 0.000] | 0.339 (20/59) [0.000, 0.381] | 0/0 | 0.111 (2/18) [0.000, 0.500] | 0/0 |
| table cells | 0/0 | 0.000 (0/8) interval n/a | 0.000 (0/18) [0.000, 0.000] | 0.000 (0/14) [0.000, 0.000] | 0.000 (0/11) [0.000, 0.000] |

**The same without the text a11y-stem wrote itself**

| Unit | a11y-stem |
|---|---|
| prose sentences (7+ words) | 0.000 (0/620) [0.000, 0.000] |
| numbers in prose and headings | 0.000 (0/278) [0.000, 0.000] |
| headings | 0.000 (0/138) [0.000, 0.000] |
| equations aligned to gold (added symbol) | 0.000 (0/30) [0.000, 0.000] |
| equations with no gold partner | 0.000 (0/9) [0.000, 0.000] |
| table cells | 0/0 |

**Symbols added to aligned equations**

| System | Added / output symbols | Per 1,000 | Per 1,000, less a kept equation number | Aligned equations |
|---|---|---|---|---|
| a11y-stem | 0/368 | 0.00 | 0.00 | 30 |
| chatbot-luna-purdue | 6/721 | 8.32 | 8.32 | 57 |
| docling | 0/0 | n/a | n/a | 0 |
| mineru-basic | 31/265 | 116.98 | 116.98 | 24 |
| pymupdf4llm | 0/0 | n/a | n/a | 0 |

**Equations the rule could not check**

| System | TeX that did not convert | No gold partner and under 3 symbols |
|---|---|---|
| a11y-stem | 0 | 16 |
| chatbot-luna-purdue | 0 | 105 |
| docling | 0 | 0 |
| mineru-basic | 5 | 15 |
| pymupdf4llm | 0 | 0 |

These are counted, never scored as correct.

**Figure descriptions: judged as stating something the image contradicts**

| System | Judge: factual error / descriptions judged | Rate [cluster 95%] |
|---|---|---|
| a11y-stem | 0/13 | 0.0000 [0.0000, 0.0000] |
| chatbot-luna-purdue | 1/26 | 0.0385 [0.0000, 0.0833] |
| docling | 0/0 | n/a |
| mineru-basic | 0/0 | n/a |
| pymupdf4llm | 0/0 | n/a |

Agreement with a model judge reading the image (gpt-6.1-sol), not accuracy; the judge is not calibrated against human labels in this run. A figure with no description is not judged here (it is a fail in the figure tables).

Units counted per system and section, with the text of every flagged unit, are not part of this file; `a11y-stem eval invented score --units` writes them for one output.

## Comparison with the chatbot recipe

The chatbot recipe is what an instructor does today: upload the PDF to a general model and type one sentence. Prompt of the primary arm, from Purdue Innovative Learning ("Accessibility in Mathematics"), verbatim, and quoted as the input it is, not as a claim this project makes:

<!-- quote -->
> Convert to accessible HTML compliant with WCAG 2.2 AA with complete MathJax headers and remove [cite].
<!-- /quote -->

Each arm sends the PDF as a file and a 150 dpi image of every page, then the prompt; no system instructions, no tools, `store=False`, default reasoning effort, at most 64,000 output tokens (`proof.chatbot`). The HTML in the reply is scored as it comes: a reply that was cut off or empty is a failure of the section, not asked again. Equations are read from the reply's MathJax TeX or MathML, converted the way a11y-stem converts its own model's LaTeX, and aligned to the gold equations by the document task's code, so the same scorer reads both systems.

Arms: `chatbot-sol-purdue` is the primary arm (gpt-6.1-sol, two runs); `chatbot-sol-expert` is the careful prompt the earlier research wrote (one run) so that the comparison is not only against the weakest prompt an instructor might copy; `chatbot-luna-purdue` is the same Purdue prompt on the cheaper gpt-6-luna (two runs). The last two are descriptive.

**Not run in this recording:** `chatbot-sol-purdue`, `chatbot-sol-expert` (see the status table).

**All chatbot arms: equations (preregistration 7.1)**

| Metric | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 0.7333 [0.6000, 1.0000] | 0.5000 [0.3000, 1.0000] |
| end_to_end_exact_lenient, without G1 equations | 0.8947 [0.8000, 1.0000] | 0.7368 [0.5000, 1.0000] |
| unflagged_wrong_per_100 | 3.33 [0.00, 8.33] | 15.00 [0.00, 30.00] |
| matched_exact_lenient (aligned pairs only) | 0.7333 [0.6000, 1.0000] | 0.5263 [0.3333, 1.0000] |
| detection_recall | 1.0000 [1.0000, 1.0000] | 0.9500 [0.9000, 1.0000] |
| detection_precision | 0.5455 [0.3810, 0.8000] | 0.2579 [0.1682, 0.4792] |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 0.5000 [0.3000, 1.0000] |

`value [cluster 95% interval by section]`. A system that asks no questions has every wrong equation unflagged. G1 equations (operators packed into OpenStax text) can fail exact match when the answer is right. The allowlist-off row converts a system's TeX without a11y-stem's LaTeX allowlist (descriptive; a11y-stem's own allowlist is a product rule, so it has no such row).

**All chatbot arms: headings (7.2)**

| Metric | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| heading F1 | 0.9344 [0.9130, 0.9600] | 0.4957 [0.4065, 0.5854] |
| heading precision | 0.8768 [0.8400, 0.9231] | 0.5133 [0.4098, 0.6000] |
| heading recall | 1.0000 [1.0000, 1.0000] | 0.4793 [0.4032, 0.5714] |
| heading level_accuracy (among matched) | 0.9917 [0.9792, 1.0000] | 0.4397 [0.3958, 0.4800] |

Levels are compared relative to each side's top level in the section. A system that writes an `h1` document title the PDF does not have gets a false positive for it.

**All chatbot arms: figures (7.3)**

| Metric | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| figure judge_pass_rate (no description = fail) | 1.0000 [1.0000, 1.0000] | 0.8846 [0.7500, 0.9167] |
| figure judge_factual_error_rate (among judged) | 0.0000 [0.0000, 0.0000] | 0.0385 [0.0000, 0.0833] |
| figures with no description | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |

Over the figures with an OpenStax alt text. Agreement with a model judge (gpt-6.1-sol) that sees the image, the candidate, the OpenStax alt and the caption, never which system wrote the candidate; not accuracy, and not calibrated against human labels in this run. A figure with no description is a fail. Judge cost is reported apart.

**All chatbot arms: invented content (7.5; the units are in the invented-content section)**

| Metric | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| invented prose sentences | 0.0190 [0.0133, 0.0261] | 0.0538 [0.0155, 0.1621] |
| invented numbers | 0.0000 [0.0000, 0.0000] | 0.0042 [0.0000, 0.0108] |
| headings the PDF does not support | 0.0213 [0.0189, 0.0270] | 0.1770 [0.0875, 0.3607] |
| aligned equations with an added symbol | 0.0000 [0.0000, 0.0000] | 0.0351 [0.0000, 0.1111] |
| equations with no gold partner and no match in the PDF | 0.0000 [0.0000, 0.0000] | 0.3390 [0.0000, 0.3810] |

All text, including text a11y-stem wrote itself. A section a system failed on has no units, so it adds nothing here; the raw rates include paraphrase the rule counts as unsupported, and the audit that measures that has not been done. A rate of exactly 0 has a zero-width bootstrap interval (every resample is 0): the Wilson upper bound in the last table is the bound to read.

**All chatbot arms: coverage (7.6)**

| Metric | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| coverage: PDF word 6-grams found in the output | 0.8060 [0.7788, 0.8232] | 0.5592 [0.2563, 0.6859] |

**All chatbot arms: counts behind the rates**

| Count | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 22/30 | 30/60 |
| end_to_end_exact_lenient, without G1 equations | 17/19 | 28/38 |
| unflagged_wrong_per_100 | 1/30 | 9/60 |
| matched_exact_lenient (aligned pairs only) | 22/30 | 30/57 |
| detection_recall | 30/30 | 57/60 |
| detection_precision | 30/55 | 57/221 |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 30/60 |
| heading F1 | 121/138 pred, 121/121 gold | 116/226 pred, 116/242 gold |
| heading precision | 121/138 | 116/226 |
| heading recall | 121/121 | 116/242 |
| heading level_accuracy (among matched) | 120/121 | 51/116 |
| figure judge_pass_rate (no description = fail) | 13/13 | 23/26 |
| figure judge_factual_error_rate (among judged) | 0/13 | 1/26 |
| figures with no description | 0/13 | 0/26 |
| invented prose sentences | 12/632 | 62/1,153 |
| invented numbers | 0/278 | 2/477 |
| headings the PDF does not support | 3/141 | 40/226 |
| aligned equations with an added symbol | 0/30 | 2/57 |
| equations with no gold partner and no match in the PDF | 0/9 | 20/59 |
| coverage: PDF word 6-grams found in the output | 9,010/11,178 | 12,501/22,356 |

**All chatbot arms: Wilson 95% intervals (items treated as independent; narrower than the cluster intervals)**

| Wilson 95% (items treated as independent) | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| end_to_end_exact_lenient (all gold equations) | [0.5555, 0.8582] | [0.3774, 0.6226] |
| end_to_end_exact_lenient, without G1 equations | [0.6861, 0.9706] | [0.5799, 0.8503] |
| matched_exact_lenient (aligned pairs only) | [0.5555, 0.8582] | [0.3992, 0.6501] |
| detection_recall | [0.8865, 1.0000] | [0.8630, 0.9829] |
| detection_precision | [0.4152, 0.6697] | [0.2047, 0.3194] |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | [0.3774, 0.6226] |
| heading precision | [0.8116, 0.9216] | [0.4484, 0.5777] |
| heading recall | [0.9692, 1.0000] | [0.4172, 0.5421] |
| figure judge_pass_rate (no description = fail) | [0.7719, 1.0000] | [0.7102, 0.9600] |
| figure judge_factual_error_rate (among judged) | [0.0000, 0.2281] | [0.0068, 0.1889] |
| figures with no description | [0.0000, 0.2281] | [0.0000, 0.1287] |
| invented prose sentences | [0.0109, 0.0329] | [0.0422, 0.0683] |
| invented numbers | [0.0000, 0.0136] | [0.0012, 0.0152] |
| headings the PDF does not support | [0.0073, 0.0607] | [0.1328, 0.2320] |
| aligned equations with an added symbol | [0.0000, 0.1135] | [0.0097, 0.1192] |
| equations with no gold partner and no match in the PDF | [0.0000, 0.2991] | [0.2314, 0.4663] |

**Each run on its own**

| Run | end_to_end_exact_lenient | unflagged_wrong_per_100 | heading F1 | invented sentences | invented numbers |
|---|---|---|---|---|---|
| chatbot-luna-purdue run 1 | 0.5667 | 13.33 | 0.4828 | 0.0740 | 0.0043 |
| chatbot-luna-purdue run 2 | 0.4333 | 16.67 | 0.5085 | 0.0332 | 0.0041 |

**End-to-end exact match by section**

| Section | Gold equations | a11y-stem | chatbot-luna-purdue |
|---|---|---|---|
| physics/2-1-relative-motion-distance-and-displacement | 8 | 1.0000 | 1.0000 |
| physics/2-2-speed-and-velocity | 12 | 0.6667 | 0.3333 |
| physics/2-3-position-vs-time-graphs | 10 | 0.6000 | 0.3000 |

**End-to-end exact match by book**

| Book (point estimates; three sections each) | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| Physics | 0.7333 | 0.5000 |

**Heading F1 by book**

| Book (point estimates; three sections each) | a11y-stem | chatbot-luna-purdue |
|---|---|---|
| Physics | 0.9344 | 0.4957 |

**Cost and time per PDF page**

| System | Total API cost | Cost/page median | p95 | min | max | Seconds/page median | p95 | min | max | Peak memory (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| a11y-stem (document task) | $0.1421 | 0.00371 | 0.00389 | 0.00228 | 0.00391 | 3.58608 | 5.49811 | 2.88243 | 5.71056 | n/a |
| chatbot-luna-purdue | $0.0436 | 0.00050 | 0.00070 | 0.00031 | 0.00071 | 5.13819 | 5.56451 | 4.74615 | 5.57143 | n/a |

Chatbot: list-price cost of each call from its usage, and the call's wall clock, per PDF page of the section. a11y-stem: pipeline plus second check per page, and the document task's wall clock at concurrency 4 on the recording machine. Judge cost is not included for any system.

**Sections that did not convert**

| System | Outputs | Expected | ok | truncated | empty | timeout | crashed | not run |
|---|---|---|---|---|---|---|---|---|
| chatbot-luna-purdue | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 |

A section that is not ok scores as found nothing: its gold items stay in every denominator.

**Not measured in this recording:** whether a wrong equation changes the meaning (the blind labels of preregistration 7.8 are the author's, and none exist yet), and the agreement of the figure judge with human labels.

## Comparison with open PDF parsers

The parsers solve a different problem: getting a PDF's text into an LLM. They are here because they are what an engineer reaches for first, and because equations, headings and reading order are the hard part of both problems. The comparison is on the measures they share, and there is no overall score and no winner. Every parser runs on the same PDFs, byte for byte, with the tool's defaults on CPU, once per section, in a fresh process; a crash, an empty output or a 30-minute limit scores the section as found nothing. Each parser's Markdown export is read by the same reader as the chatbot's HTML (`eval.invented.read`), its maths converted and aligned to gold by the same code as a11y-stem's.

**What each system is built for (not failures: design differences)**

| System | Built for | Not built for |
|---|---|---|
| a11y-stem | accessible HTML from STEM course PDFs, with a review screen | re-typing prose (it keeps the PDF's text); tables in production (behind a flag); scans |
| Docling | lossless document conversion for gen-AI pipelines (Markdown, JSON; HTML is one export) | accessibility remediation: no alt text on pictures, one heading level in the export, formulas kept as pictures unless the opt-in enrichment is on |
| MinerU | extraction for LLM corpora and RAG (Markdown, JSON) | accessible HTML: empty alt text on images, one heading level; its higher tiers want a GPU |
| PyMuPDF4LLM | fast text-layer to Markdown for LLM and RAG ingestion | mathematics (no formula recognition); images are off by default |

**Considered and not run**

| System | Why |
|---|---|
| Marker 2.0.0 | waits for a licence decision: its weights' licence counts evaluating as use and bars competing services, with no research exception |
| Nougat 0.1.17 | on CPU the first of three development pages took 19 minutes; trained on arXiv papers; unmaintained since 2023 |
| olmOCR 0.4.27 | needs an NVIDIA GPU with at least 12 GB; the machine has no working driver |
| Mathpix | paid API; no key on the machine |

**Parsers that ran**

| Arm | Versions | Equations written as TeX/MathML | Note |
|---|---|---|---|
| docling | docling 2.138.0 | 0 | writes no LaTeX in its default settings (a formula stays a picture whose alt text is the PDF's flattened text) |
| mineru-basic | mineru 4.0.12 | 62 |  |
| pymupdf4llm | pymupdf4llm 1.28.2 | 0 | has no formula recognition |

**Family B: a11y-stem against each parser**

| Measure | Against | a11y-stem | Other | Difference (a11y-stem minus other) | Cluster 99.167% (k = 6): the claim | Cluster 95% | Result |
|---|---|---|---|---|---|---|---|
| end_to_end_exact_lenient | docling | 0.7333 | 0.0000 | 0.7333 | [0.6000, 1.0000] | [0.6000, 1.0000] | **a11y-stem is better** |
| heading F1 | docling | 0.9344 | 0.8764 | 0.0580 | [0.0000, 0.1219] | [0.0000, 0.1219] | **no difference this set can detect** |
| end_to_end_exact_lenient | mineru-basic | 0.7333 | 0.2000 | 0.5333 | [0.3000, 0.6667] | [0.3000, 0.6667] | **a11y-stem is better** |
| heading F1 | mineru-basic | 0.9344 | 0.9055 | 0.0289 | [0.0098, 0.0433] | [0.0098, 0.0433] | **a11y-stem is better** |
| end_to_end_exact_lenient | pymupdf4llm | 0.7333 | 0.0000 | 0.7333 | [0.6000, 1.0000] | [0.6000, 1.0000] | **a11y-stem is better** |
| heading F1 | pymupdf4llm | 0.9344 | 0.9064 | 0.0280 | [0.0000, 0.0543] | [0.0000, 0.0543] | **no difference this set can detect** |

Preregistration 8.3 as written: 2 comparisons per parser, k = 6, the 99.167% paired cluster-bootstrap interval of the difference decides. For a parser that writes no LaTeX by design (`docling`, `pymupdf4llm`) the equation row compares a11y-stem with a tool that does not attempt equations; read it as a description of what the tool is built for, not as evidence that a11y-stem transcribes better. Flat heading levels in a parser's export are a property of that export.

**All parsers: equations (preregistration 7.1)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 0.7333 [0.6000, 1.0000] | 0.0000 [0.0000, 0.0000] | 0.2000 [0.0000, 0.3750] | 0.0000 [0.0000, 0.0000] |
| end_to_end_exact_lenient, without G1 equations | 0.8947 [0.8000, 1.0000] | 0.0000 [0.0000, 0.0000] | 0.3158 [0.0000, 0.5000] | 0.0000 [0.0000, 0.0000] |
| unflagged_wrong_per_100 | 3.33 [0.00, 8.33] | 0.00 [0.00, 0.00] | 33.33 [25.00, 50.00] | 0.00 [0.00, 0.00] |
| matched_exact_lenient (aligned pairs only) | 0.7333 [0.6000, 1.0000] | n/a | 0.2500 [0.0000, 0.4286] | n/a |
| detection_recall | 1.0000 [1.0000, 1.0000] | 0.0000 [0.0000, 0.0000] | 0.8000 [0.5833, 1.0000] | 0.0000 [0.0000, 0.0000] |
| detection_precision | 0.5455 [0.3810, 0.8000] | n/a | 0.3871 [0.3333, 0.5000] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 0.0000 [0.0000, 0.0000] | 0.1333 [0.0000, 0.3750] | 0.0000 [0.0000, 0.0000] |

`value [cluster 95% interval by section]`. A system that asks no questions has every wrong equation unflagged. G1 equations (operators packed into OpenStax text) can fail exact match when the answer is right. The allowlist-off row converts a system's TeX without a11y-stem's LaTeX allowlist (descriptive; a11y-stem's own allowlist is a product rule, so it has no such row).

**All parsers: headings (7.2)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| heading F1 | 0.9344 [0.9130, 0.9600] | 0.8764 [0.8381, 0.9254] | 0.9055 [0.8923, 0.9167] | 0.9064 [0.8986, 0.9130] |
| heading precision | 0.8768 [0.8400, 0.9231] | 0.8014 [0.7719, 0.8611] | 0.8647 [0.8235, 0.9167] | 0.8288 [0.8158, 0.8400] |
| heading recall | 1.0000 [1.0000, 1.0000] | 0.9669 [0.9167, 1.0000] | 0.9504 [0.9167, 1.0000] | 1.0000 [1.0000, 1.0000] |
| heading level_accuracy (among matched) | 0.9917 [0.9792, 1.0000] | 0.0256 [0.0227, 0.0323] | 0.0261 [0.0227, 0.0345] | 1.0000 [1.0000, 1.0000] |

Levels are compared relative to each side's top level in the section. A system that writes an `h1` document title the PDF does not have gets a false positive for it.

**All parsers: figures (7.3)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| figure judge_pass_rate (no description = fail) | 1.0000 [1.0000, 1.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| figure judge_factual_error_rate (among judged) | 0.0000 [0.0000, 0.0000] | n/a | n/a | n/a |
| figures with no description | 0.0000 [0.0000, 0.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] |

Over the figures with an OpenStax alt text. Agreement with a model judge (gpt-6.1-sol) that sees the image, the candidate, the OpenStax alt and the caption, never which system wrote the candidate; not accuracy, and not calibrated against human labels in this run. A figure with no description is a fail. Judge cost is reported apart.

**All parsers: invented content (7.5; the units are in the invented-content section)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| invented prose sentences | 0.0190 [0.0133, 0.0261] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0016 [0.0000, 0.0056] |
| invented numbers | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| headings the PDF does not support | 0.0213 [0.0189, 0.0270] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| aligned equations with an added symbol | 0.0000 [0.0000, 0.0000] | n/a | 0.3750 [0.2857, 0.4286] | n/a |
| equations with no gold partner and no match in the PDF | 0.0000 [0.0000, 0.0000] | n/a | 0.1111 [0.0000, 0.5000] | n/a |

All text, including text a11y-stem wrote itself. A section a system failed on has no units, so it adds nothing here; the raw rates include paraphrase the rule counts as unsupported, and the audit that measures that has not been done. A rate of exactly 0 has a zero-width bootstrap interval (every resample is 0): the Wilson upper bound in the last table is the bound to read.

**All parsers: coverage (7.6)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| coverage: PDF word 6-grams found in the output | 0.8060 [0.7788, 0.8232] | 0.8583 [0.8242, 0.8912] | 0.8513 [0.8184, 0.8807] | 0.8939 [0.8793, 0.9108] |

**All parsers: counts behind the rates**

| Count | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 22/30 | 0/30 | 6/30 | 0/30 |
| end_to_end_exact_lenient, without G1 equations | 17/19 | 0/19 | 6/19 | 0/19 |
| unflagged_wrong_per_100 | 1/30 | 0/30 | 10/30 | 0/30 |
| matched_exact_lenient (aligned pairs only) | 22/30 | 0/0 | 6/24 | 0/0 |
| detection_recall | 30/30 | 0/30 | 24/30 | 0/30 |
| detection_precision | 30/55 | 0/0 | 24/62 | 0/0 |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 0/30 | 4/30 | 0/30 |
| heading F1 | 121/138 pred, 121/121 gold | 117/146 pred, 117/121 gold | 115/133 pred, 115/121 gold | 121/146 pred, 121/121 gold |
| heading precision | 121/138 | 117/146 | 115/133 | 121/146 |
| heading recall | 121/121 | 117/121 | 115/121 | 121/121 |
| heading level_accuracy (among matched) | 120/121 | 3/117 | 3/115 | 121/121 |
| figure judge_pass_rate (no description = fail) | 13/13 | 0/13 | 0/13 | 0/13 |
| figure judge_factual_error_rate (among judged) | 0/13 | 0/0 | 0/0 | 0/0 |
| figures with no description | 0/13 | 13/13 | 13/13 | 13/13 |
| invented prose sentences | 12/632 | 0/619 | 0/620 | 1/628 |
| invented numbers | 0/278 | 0/276 | 0/273 | 0/448 |
| headings the PDF does not support | 3/141 | 0/146 | 0/133 | 0/146 |
| aligned equations with an added symbol | 0/30 | 0/0 | 9/24 | 0/0 |
| equations with no gold partner and no match in the PDF | 0/9 | 0/0 | 2/18 | 0/0 |
| coverage: PDF word 6-grams found in the output | 9,010/11,178 | 9,594/11,178 | 9,516/11,178 | 9,992/11,178 |

**All parsers: Wilson 95% intervals (items treated as independent; narrower than the cluster intervals)**

| Wilson 95% (items treated as independent) | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | [0.5555, 0.8582] | [0.0000, 0.1135] | [0.0951, 0.3731] | [0.0000, 0.1135] |
| end_to_end_exact_lenient, without G1 equations | [0.6861, 0.9706] | [0.0000, 0.1682] | [0.1536, 0.5399] | [0.0000, 0.1682] |
| matched_exact_lenient (aligned pairs only) | [0.5555, 0.8582] | n/a | [0.1200, 0.4490] | n/a |
| detection_recall | [0.8865, 1.0000] | [0.0000, 0.1135] | [0.6269, 0.9049] | [0.0000, 0.1135] |
| detection_precision | [0.4152, 0.6697] | n/a | [0.2758, 0.5115] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | [0.0000, 0.1135] | [0.0531, 0.2968] | [0.0000, 0.1135] |
| heading precision | [0.8116, 0.9216] | [0.7293, 0.8580] | [0.7962, 0.9126] | [0.7594, 0.8812] |
| heading recall | [0.9692, 1.0000] | [0.9181, 0.9871] | [0.8960, 0.9771] | [0.9692, 1.0000] |
| figure judge_pass_rate (no description = fail) | [0.7719, 1.0000] | [0.0000, 0.2281] | [0.0000, 0.2281] | [0.0000, 0.2281] |
| figure judge_factual_error_rate (among judged) | [0.0000, 0.2281] | n/a | n/a | n/a |
| figures with no description | [0.0000, 0.2281] | [0.7719, 1.0000] | [0.7719, 1.0000] | [0.7719, 1.0000] |
| invented prose sentences | [0.0109, 0.0329] | [0.0000, 0.0062] | [0.0000, 0.0062] | [0.0003, 0.0090] |
| invented numbers | [0.0000, 0.0136] | [0.0000, 0.0137] | [0.0000, 0.0139] | [0.0000, 0.0085] |
| headings the PDF does not support | [0.0073, 0.0607] | [0.0000, 0.0256] | [0.0000, 0.0281] | [0.0000, 0.0256] |
| aligned equations with an added symbol | [0.0000, 0.1135] | n/a | [0.2116, 0.5729] | n/a |
| equations with no gold partner and no match in the PDF | [0.0000, 0.2991] | n/a | [0.0310, 0.3280] | n/a |

**End-to-end exact match by section**

| Section | Gold equations | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|---|
| physics/2-1-relative-motion-distance-and-displacement | 8 | 1.0000 | 0.0000 | 0.3750 | 0.0000 |
| physics/2-2-speed-and-velocity | 12 | 0.6667 | 0.0000 | 0.0000 | 0.0000 |
| physics/2-3-position-vs-time-graphs | 10 | 0.6000 | 0.0000 | 0.3000 | 0.0000 |

**Heading F1 by book**

| Book (point estimates; three sections each) | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| Physics | 0.9344 | 0.8764 | 0.9055 | 0.9064 |

**Time and memory per PDF page**

| System | Total API cost | Cost/page median | p95 | min | max | Seconds/page median | p95 | min | max | Peak memory (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| a11y-stem (document task) | $0.1421 | 0.00371 | 0.00389 | 0.00228 | 0.00391 | 3.58608 | 5.49811 | 2.88243 | 5.71056 | n/a |
| docling | $0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 2.15413 | 2.30521 | 1.92700 | 2.32200 | 2454 |
| mineru-basic | $0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 2.52886 | 2.53141 | 2.31869 | 2.53169 | 3563 |
| pymupdf4llm | $0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.29557 | 0.29691 | 0.29531 | 0.29706 | 378 |

Parsers: wall clock of one fresh process per section divided by its PDF pages, model load included, CPU only, API cost $0 (hosting not estimated); peak memory is the process tree's maximum. a11y-stem: seconds of the document task at concurrency 4 (it waits on a hosted model, so its time is not CPU time). Timings are from one machine; the load average at each start is in the recorded run, and a busy machine makes a time an upper bound.

**Sections that did not convert**

| System | Outputs | Expected | ok | truncated | empty | timeout | crashed | not run |
|---|---|---|---|---|---|---|---|---|
| docling | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| mineru-basic | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| pymupdf4llm | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 |

A section that is not ok scores as found nothing: its gold items stay in every denominator.

Parser timings: 9 runs started on a quiet machine (1-minute load under 1.5) and 0 did not. Host: AMD Ryzen 7 5800H with Radeon Graphics, 16 logical CPUs.

## Human labels and judge agreement

**Not done.** The labels are written by the author, blind to the system, after the run (preregistration 7.8). None exist, so this report shows no number from them. Until they do:

- whether a wrong equation changes the meaning is not known: every wrong equation is counted as wrong, and the scorer's ceiling on a hand-written sample is stated in the registered addendum;
- the figure judge (a model) is not calibrated against human labels, so its numbers are agreement with that judge and nothing more;
- the invented-content rule's precision and recall are not measured, so its rates include paraphrase it cannot tell from invention.

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
