# Proof: Held-out OpenStax sections (pre-registered 2026-10-13)

a11y-stem assists a person who makes course material accessible. Nothing here measures conformance with WCAG or any other standard, and no number below may be read as a claim that a document is accessible. Automated HTML checks are reported as what a machine can detect. Judge numbers are agreement with a model judge, not accuracy.

> **Incomplete.** Not computed in this build: Human labels and judge agreement.

## Status

|  |  |
|---|---|
| Proof set | `heldout-2026-10` (held out) |
| Mode | replay of a recorded live run: no model call, no key, no cost |
| Run recorded on | 2026-10-14 |
| Code | commit `297126f34137` |
| Pipeline version | 0.4.0 |
| Models | main claude-opus-5-5, cheap claude-haiku-5-5, no second check |
| Prompt versions | equation equation-df0e12f1, figure figure-ac51f42d, judge judge_figure-137e2607, table table-b3769ce7 |
| Recorded spend | $10.8775 |
| Runs | document x2, headings x1, figures x2, equations x2 |
| Recording | interrupted once and resumed: document.run1, document.run2, headings.run1, figures.run1, figures.run2, equations.run1 finished before the interruption and were read back |

**Pins checked before the run**

| Pin | Result | Detail |
|---|---|---|
| gold manifest | ok | sha256 e9e7e429a5ce |
| gold files | ok | 15 files match the manifest |
| gold sections | ok | 15 sections |
| preregistration | ok | sha256 2c16815a6d18 |
| recorded run | ok | 270 files match the manifest |

Replay compared with the recorded reports: **identical** (7 report(s): every metric and cost, timing excluded).

Replayed 7,953 model requests and 0 second-check requests from the bundle; 0 had no recorded answer.

**Parts of this proof**

| Part | Status |
|---|---|
| a11y-stem, production defaults | done |
| Invented-content rate | done |
| Comparison with the chatbot recipe | done |
| Comparison with open PDF parsers | done |
| Human labels and judge agreement | not computed |

## The 15 sections

Fifteen sections of five OpenStax books (242 Letter pages), chosen by rule and registered, with this set's answer key hashed, before any system ran on them. The books are retired first editions, which are CC BY 4.0 (the current editions are CC BY-NC-SA 4.0 and the live CC BY STEM books were used in development).

| Section | Title | PDF pages | Equations (block / inline) | G1 | Figures (with OpenStax alt) | Tables | Headings (in boxes) | PDF sha256 |
|---|---|---|---|---|---|---|---|---|
| college-physics/29-3-photon-energies-and-the-electromagnetic-spectrum | 29.3 Photon Energies and the Electromagnetic Spectrum | 10 | 47 (10 / 37) | 4 | 6 (5) | 1 | 23 (19) | `a150c2c0a643` |
| college-physics/5-3-elasticity-stress-and-strain | 5.3 Elasticity: Stress and Strain | 14 | 121 (22 / 99) | 10 | 8 (8) | 1 | 31 (27) | `84cf20ef786b` |
| college-physics/28-4-relativistic-addition-of-velocities | 28.4 Relativistic Addition of Velocities | 9 | 68 (9 / 59) | 13 | 5 (5) | 0 | 26 (22) | `657a9f3a8e97` |
| chemistry/19-3-spectroscopic-and-magnetic-properties-of-coordination-compounds | 19.3 Spectroscopic and Magnetic Properties of Coordination Compounds | 8 | 9 (3 / 6) | 2 | 8 (8) | 0 | 21 (16) | `7809c1758041` |
| chemistry/7-3-lewis-symbols-and-structures | 7.3 Lewis Symbols and Structures | 9 | 25 (4 / 21) | 4 | 4 (4) | 0 | 23 (12) | `08ee533f3e6b` |
| chemistry/14-7-acid-base-titrations | 14.7 Acid-Base Titrations | 11 | 63 (36 / 27) | 10 | 3 (3) | 1 | 14 (10) | `bae853afb066` |
| precalculus/5-4-right-triangle-trigonometry | 5.4 Right Triangle Trigonometry | 29 | 162 (21 / 141) | 1 | 15 (15) | 1 | 48 (32) | `92d90a5ea6c0` |
| precalculus/1-6-absolute-value-functions | 1.6 Absolute Value Functions | 28 | 205 (19 / 186) | 1 | 12 (12) | 2 | 55 (42) | `34f9466cd9d0` |
| precalculus/3-1-complex-numbers | 3.1 Complex Numbers | 20 | 206 (24 / 182) | 0 | 3 (3) | 1 | 68 (51) | `ae89112656cc` |
| introductory-statistics/2-4-box-plots | 2.4 Box Plots | 9 | 0 (0 / 0) | 0 | 5 (5) | 0 | 13 (12) | `792ba6362f87` |
| introductory-statistics/4-5-hypergeometric-distribution | 4.5 Hypergeometric Distribution | 6 | 4 (0 / 4) | 0 | 1 (1) | 0 | 17 (15) | `96b79c680b69` |
| introductory-statistics/3-1-terminology | 3.1 Terminology | 8 | 31 (0 / 31) | 0 | 0 (0) | 1 | 11 (10) | `ea51bd3362b2` |
| intermediate-algebra/4-3-solve-mixture-applications-with-systems-of-equations | 4.3 Solve Mixture Applications with Systems of Equations | 28 | 65 (4 / 61) | 1 | 0 (0) | 6 | 40 (31) | `8cb8fcd4dc26` |
| intermediate-algebra/4-6-solve-systems-of-equations-using-determinants | 4.6 Solve Systems of Equations Using Determinants | 27 | 183 (5 / 178) | 0 | 0 (0) | 9 | 55 (45) | `4cc8457f7609` |
| intermediate-algebra/1-5-properties-of-real-numbers | 1.5 Properties of Real Numbers | 26 | 180 (6 / 174) | 2 | 0 (0) | 4 | 64 (55) | `9fb4330b5563` |
| **Total** |  | **242** | **1369** | **48** | **70** | **27** | **509** |  |

The PDF is the one every system was given. G1: equations whose gold MathML packs operators into text, which exact match can fail even when the answer is right.

- Every section of this set is from a retired first edition. Whether such an edition is acceptable ground truth was decided before the run; the report states it.

## Results: a11y-stem

Production settings with the Anthropic provider (main model claude-opus-5-5 at effort medium, cheap model claude-haiku-5-5 at effort low, the main model's server-side refusal fallback on, every content flag off, no second automated check: it is OpenAI's). Tables are off in production, so a detected data table is kept as an image with a required decision. Intervals: cluster bootstrap by section (B = 10,000, seed 0), which is approximate with this few sections and tends to be a little narrow; the Wilson interval is shown beside it for comparison and treats items as independent.

### a11y-stem on the whole section (document task)

**Equations through the whole pipeline (document task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| **end_to_end_exact_lenient (all gold equations)** | 0.5037 | 1,379/2,738 | [0.3755, 0.5985] | [0.4849, 0.5224] | 0.5004 / 0.5069 |
| end_to_end_exact_lenient, without G1 equations | 0.5204 | 1,375/2,642 | [0.3949, 0.6141] | [0.5014, 0.5394] | 0.5170 / 0.5238 |
| **unflagged_wrong_per_100 (wrong, not G1, not in a required decision)** | 23.30 | 638/2,738 | [18.97, 29.46] | n/a | 22.86 / 23.74 |
| matched_exact_lenient (aligned pairs only) | 0.5666 | 1,379/2,434 | [0.4361, 0.6619] | [0.5468, 0.5861] | 0.5629 / 0.5703 |
| detection_recall | 0.8890 | 2,434/2,738 | [0.8454, 0.9176] | [0.8767, 0.9002] | 0.8890 / 0.8890 |
| detection_precision | 0.7349 | 2,434/3,312 | [0.5974, 0.8295] | [0.7196, 0.7497] | 0.7349 / 0.7349 |
| detection_precision, without prose maths | 0.8223 | 2,434/2,960 | [0.7532, 0.8853] | [0.8081, 0.8356] | 0.8223 / 0.8223 |

Rates have every gold equation as denominator; an equation with no aligned output counts as wrong. G1 equations (operators packed into OpenStax text) can fail exact match even when the answer is right. `unflagged_wrong_per_100` is wrong aligned equations that no required decision pointed at, per 100 gold equations.

**Review load (what a person is asked to decide)**

| Measure | Value |
|---|---|
| PDF pages converted | 484 |
| Required decisions | 853 |
| Required fixes (several can share one decision) | 1,025 |
| **Required decisions per PDF page** | **1.7624** |
| Optional notes per PDF page | 0.5537 |
| Equation flags: precision | 0.7891 |
| Equation flags: recall | 0.3441 |
| Heading flags: precision | 0.0000 |
| Heading flags: recall | 0.0000 |
| Wrong items not asked about | 748 |

Flag precision is the share of required decisions that point at a real error; recall is the share of real errors a person is asked about. Both are measured against the gold set only; figures and equations with no gold partner have no answer key here.

**Reasons behind the required fixes**

| Reason | Fixes |
|---|---|
| below_accept | 351 |
| conversion_failed | 1 |
| heading.all_caps | 6 |
| heading.repeated_label | 230 |
| heading.very_short | 8 |
| heading.weak_evidence | 216 |
| invalid_latex | 6 |
| low_confidence | 95 |
| not_equation | 46 |
| sign_mismatch | 70 |
| style_mismatch | 8 |
| symbol_mismatch | 296 |
| table_not_rebuilt | 36 |
| too_long | 10 |
| unbalanced_braces | 1 |
| unknown_command | 4 |

Counted per fix, and a fix can have several reasons, so the counts add up to more than the number of required decisions.

**Predicted equations with no gold partner**

| Kind | Count |
|---|---|
| prose_math | 352 |
| fragment | 182 |
| other | 344 |

`prose_math`: maths the source wrote as plain text, so the gold has no MathML for it; `fragment`: a piece of a gold equation or of a broken formula.

**Per section (runs pooled)**

| Section | PDF pages | Gold equations | Exact | Rate | Required | Calls | Cost |
|---|---|---|---|---|---|---|---|
| college-physics/29-3-photon-energies-and-the-electromagnetic-spectrum | 10 | 94 | 44 | 0.4681 | 32 | 152 | $0.2313 |
| college-physics/5-3-elasticity-stress-and-strain | 14 | 242 | 161 | 0.6653 | 26 | 292 | $0.3527 |
| college-physics/28-4-relativistic-addition-of-velocities | 9 | 136 | 60 | 0.4412 | 28 | 193 | $0.2587 |
| chemistry/19-3-spectroscopic-and-magnetic-properties-of-coordination-compounds | 8 | 18 | 4 | 0.2222 | 24 | 230 | $0.3024 |
| chemistry/7-3-lewis-symbols-and-structures | 9 | 50 | 0 | 0.0000 | 33 | 161 | $0.4096 |
| chemistry/14-7-acid-base-titrations | 11 | 126 | 4 | 0.0317 | 39 | 220 | $0.4828 |
| precalculus/5-4-right-triangle-trigonometry | 29 | 324 | 131 | 0.4043 | 188 | 681 | $1.1994 |
| precalculus/1-6-absolute-value-functions | 28 | 410 | 271 | 0.6610 | 75 | 521 | $0.6057 |
| precalculus/3-1-complex-numbers | 20 | 412 | 296 | 0.7184 | 59 | 477 | $0.5090 |
| introductory-statistics/2-4-box-plots | 9 | 0 | 0 | n/a | 4 | 48 | $0.0487 |
| introductory-statistics/4-5-hypergeometric-distribution | 6 | 8 | 2 | 0.2500 | 16 | 54 | $0.0600 |
| introductory-statistics/3-1-terminology | 8 | 62 | 8 | 0.1290 | 22 | 179 | $0.0896 |
| intermediate-algebra/4-3-solve-mixture-applications-with-systems-of-equations | 28 | 130 | 65 | 0.5000 | 25 | 234 | $0.4001 |
| intermediate-algebra/4-6-solve-systems-of-equations-using-determinants | 27 | 366 | 132 | 0.3607 | 156 | 579 | $1.0687 |
| intermediate-algebra/1-5-properties-of-real-numbers | 26 | 360 | 201 | 0.5583 | 126 | 514 | $0.6053 |

Counts are summed over the runs. Cost includes the second automated check.

**Per book (point estimates; each rests on a few sections)**

| Book | Sections | End-to-end exact (lenient) | Count | Heading F1 |
|---|---|---|---|---|
| College Physics | 3 | 0.5614 | 265/472 | 1.0000 |
| Chemistry | 3 | 0.0412 | 8/194 | 1.0000 |
| Precalculus | 3 | 0.6091 | 698/1,146 | 0.9524 |
| Introductory Statistics | 3 | 0.1429 | 10/70 | 0.9398 |
| Intermediate Algebra | 3 | 0.4650 | 398/856 | 0.9434 |

### Equations as pictures (equations task)

**Each gold equation as a picture (equations task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| exact_lenient | 0.6841 | 1,873/2,738 | [0.5527, 0.7828] | [0.6664, 0.7012] | 0.6844 / 0.6837 |
| exact_lenient, without G1 equations | 0.7051 | 1,863/2,642 | [0.5807, 0.8009] | [0.6875, 0.7222] | 0.7055 / 0.7048 |
| exact_strict | 0.4503 | 1,233/2,738 | [0.3681, 0.5323] | [0.4318, 0.4690] | 0.4507 / 0.4500 |
| mean_teds | 0.8086 | 2,213.98/2,738 | [0.7268, 0.8716] | n/a | 0.8097 / 0.8075 |
| proposal_rate | 0.9701 | 2,656/2,738 | [0.9192, 1.0000] | [0.9630, 0.9758] | 0.9708 / 0.9693 |
| unflagged wrong per 100 equations | 22.28 | 610/2,738 | [16.51, 29.70] | n/a | 22.28 / 22.28 |

Each equation is rendered to an image with its text layer and its surrounding text, as the whole-document run would give it. The picture comes from a browser print of the gold MathML, so it is cleaner than a crop from a real PDF.

**By status the pipeline gave the answer**

| Status | n | Share | exact_lenient | mean TEDS |
|---|---|---|---|---|
| accepted | 2,527 | 0.9229 | 0.7333 | 0.8417 |
| needs_review | 129 | 0.0471 | 0.1550 | 0.6736 |
| abstained | 7 | 0.0026 | 0.0000 | 0.0000 |
| failed | 75 | 0.0274 | 0.0000 | 0.0000 |

Accepted answers should be more accurate than answers sent to review.

**By display**

| Display | n | Share | exact_lenient | mean TEDS |
|---|---|---|---|---|
| block | 326 | 0.1191 | 0.2853 | 0.7718 |
| inline | 2,412 | 0.8809 | 0.7380 | 0.8136 |

### Headings (headings task)

**Headings found in the printed sections (headings task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| **heading F1** | 0.9613 | n/a | [0.9474, 0.9772] | n/a | 0.9613 |
| heading precision | 0.9467 | 497/525 | [0.9272, 0.9703] | [0.9240, 0.9628] | 0.9467 |
| heading recall | 0.9764 | 497/509 | [0.9564, 0.9935] | [0.9592, 0.9865] | 0.9764 |
| level_accuracy (among matched headings) | 1.0000 | 497/497 | [1.0000, 1.0000] | [0.9923, 1.0000] | 1.0000 |

A PDF has no heading levels, so level accuracy compares each heading's level relative to the top level on its page. The headings task makes no model call and runs once. Most gold headings sit inside boxes (examples, notes, exercises); see recall by context.

**Recall by where the heading sits**

| Context | Gold headings | Recall |
|---|---|---|
| be-prepared | 3 | 1.0000 |
| body | 110 | 0.9636 |
| chemistry | 3 | 1.0000 |
| commentary | 5 | 1.0000 |
| example | 140 | 0.9929 |
| how-to-notitle | 16 | 1.0000 |
| howto | 2 | 1.0000 |
| interactive | 4 | 1.0000 |
| media-2 | 2 | 1.0000 |
| note | 44 | 1.0000 |
| precalculus | 28 | 1.0000 |
| problem | 30 | 0.9667 |
| solution | 58 | 0.9310 |
| statistics | 9 | 1.0000 |
| try | 54 | 0.9630 |
| unnumbered | 1 | 1.0000 |

### Figure descriptions (figures task)

**Alt text for each figure with an OpenStax reference (figures task)**

| Metric | Pooled | Count | Cluster 95% (by section) | Wilson 95% (items independent) | Per run |
|---|---|---|---|---|---|
| judge_pass_rate (agreement with a model judge) | 0.9710 | 134/138 | [0.9333, 1.0000] | [0.9278, 0.9887] | 0.9565 / 0.9855 |
| judge_factual_error_rate (among judged) | 0.0000 | 0/138 | [0.0000, 0.0000] | [0.0000, 0.0271] | 0.0000 / 0.0000 |
| proposal_rate | 1.0000 | 138/138 | [1.0000, 1.0000] | [0.9729, 1.0000] | 1.0000 / 1.0000 |
| check_pass_rate (the step's own deterministic checks) | 1.0000 | 138/138 | [1.0000, 1.0000] | [0.9729, 1.0000] | 1.0000 / 1.0000 |

The judge is a model (claude-opus-5-5) that sees the image, the candidate, the OpenStax alt text and the caption, never which system wrote the candidate. Its pass rate is agreement with that judge, not accuracy; it is not yet calibrated against human labels. A figure with no proposal counts as a fail. Judge cost is reported apart.

**Figures by status**

| Status | n |
|---|---|
| accepted | 121 |
| needs_review | 17 |

### Cost and time

**Spend by run (list prices; models.pricing)**

| Run | Items | Model calls | Pipeline cost | Judge cost | Pipeline cost per PDF page |
|---|---|---|---|---|---|
| document run 1 | 15 | 2,267 | $3.3480 | n/a | $0.01383 |
| document run 2 | 15 | 2,268 | $3.2760 | n/a | $0.01354 |
| headings run 1 | 15 | 0 | $0.0000 | n/a | n/a |
| figures run 1 | 69 | 138 | $0.3013 | $0.3427 | n/a |
| figures run 2 | 69 | 138 | $0.3024 | $0.3455 | n/a |
| equations run 1 | 1,369 | 1,579 | $1.4797 | n/a | n/a |
| equations run 2 | 1,369 | 1,563 | $1.4819 | n/a | n/a |

Pipeline cost includes the second automated check. Judge cost is never counted as the pipeline's. The per-page figure is for the document task only.

**Cost per PDF page across sections and runs (document task)**

| Median | p95 | Min | Max |
|---|---|---|---|
| 0.01252 | 0.02249 | 0.00270 | 0.02316 |

**Seconds per PDF page, as measured in the recorded live run (document task)**

| Median | p95 | Min | Max |
|---|---|---|---|
| 4.28087 | 5.94030 | 1.19000 | 6.18687 |

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
| document run 2 | yes | none | none |
| headings run 1 | yes | none | none |
| figures run 1 | yes | none | none |
| figures run 2 | yes | none | none |
| equations run 1 | yes | none | none |
| equations run 2 | yes | none | none |

## Invented-content rate

The invented-content rate of a type of output unit is the share of the system's units of that type that the source PDF does not support (preregistration 7.5). The reference is the PDF's own text layer, footer and attribution included.

Rules: `a11y_stem.eval.invented`, code fingerprint `672e19fc97e14b7daa97015833b0dbcd913725598f8e6cdaa2b5e4326720d295`. Units: prose sentences of 7 or more words (unsupported when under 25% of their word 4-grams are in the PDF); numbers (not among the PDF's numbers); headings (no line of the PDF within difflib ratio 0.85); equations aligned to a gold equation (hold a letter, digit or Greek letter the gold equation does not); equations with no gold partner and 3 or more symbols (no matching run of the PDF's symbols, ratio 0.8); table cells.

**Primary rows** are prose sentences and numbers. a11y-stem copies prose from the PDF's text layer, so for it those rows measure the copying, plus the sentences the tool writes itself (the "About this version" section and notices), which are counted in the first table and left out in the second. The types where a11y-stem can invent are equations and figure descriptions.

Intervals: cluster bootstrap by section (the number to quote), shown as `rate (unsupported/units) [95% interval]`. With few sections that have units of a type the interval is not given.

**What the rule cannot see.** A changed word inside a sentence that keeps a quarter of its 4-grams passes the sentence rule (a changed number does not pass the number rule); a faithful paraphrase or a reordering counts as unsupported (a false positive); the PDF holds what is printed, so a correct description of a figure is never "supported" by it, which is why figures use the judge. **The audit of the rule's precision and recall (blind labels by the author, section 7.5) has not been done**: until it is, the raw rates are the only rates, and a rate for a system that re-types the page includes paraphrase the rule cannot tell from invention.

**Unsupported units / units, all text (the rate to quote)**

| Unit | a11y-stem | chatbot-haiku-purdue | chatbot-opus-purdue | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|---|---|
| prose sentences (7+ words) | 0.079 (353/4457) [0.049, 0.122] | 0.037 (152/4139) [0.021, 0.058] | 0.040 (167/4146) [0.018, 0.070] | 0.009 (18/2090) [0.001, 0.021] | 0.008 (16/2046) [0.001, 0.019] | 0.030 (73/2439) [0.015, 0.047] |
| numbers in prose and headings | 0.000 (0/3672) [0.000, 0.000] | 0.002 (5/3058) [0.000, 0.003] | 0.002 (5/3088) [0.000, 0.004] | 0.014 (42/3054) [0.004, 0.024] | 0.001 (2/2160) [0.000, 0.002] | 0.022 (116/5179) [0.007, 0.042] |
| headings | 0.028 (30/1080) [0.022, 0.038] | 0.022 (23/1028) [0.009, 0.042] | 0.018 (18/998) [0.006, 0.034] | 0.000 (0/563) [0.000, 0.000] | 0.000 (0/475) [0.000, 0.000] | 0.000 (0/513) [0.000, 0.000] |
| equations aligned to gold (added symbol) | 0.087 (208/2392) [0.048, 0.124] | 0.017 (40/2413) [0.005, 0.045] | 0.020 (49/2429) [0.006, 0.049] | 0/0 | 0.264 (171/647) [0.205, 0.321] | 0/0 |
| equations with no gold partner | 0.027 (14/521) [0.000, 0.079] | 0.226 (218/964) [0.035, 0.479] | 0.294 (280/953) [0.049, 0.543] | 0/0 | 0.140 (57/408) [0.039, 0.259] | 0/0 |
| table cells | 0/0 | 0.117 (216/1847) [0.062, 0.172] | 0.167 (388/2326) [0.116, 0.222] | 0.024 (10/409) [0.007, 0.046] | 0.083 (52/623) [0.043, 0.162] | 0.061 (27/443) [0.007, 0.115] |

**The same without the text a11y-stem wrote itself**

| Unit | a11y-stem |
|---|---|
| prose sentences (7+ words) | 0.000 (0/4104) [0.000, 0.000] |
| numbers in prose and headings | 0.000 (0/3672) [0.000, 0.000] |
| headings | 0.000 (0/1050) [0.000, 0.000] |
| equations aligned to gold (added symbol) | 0.087 (208/2392) [0.048, 0.124] |
| equations with no gold partner | 0.027 (14/521) [0.000, 0.079] |
| table cells | 0/0 |

**Symbols added to aligned equations**

| System | Added / output symbols | Per 1,000 | Per 1,000, less a kept equation number | Aligned equations |
|---|---|---|---|---|
| a11y-stem | 464/17,213 | 26.96 | 26.67 | 2,392 |
| chatbot-haiku-purdue | 120/14,565 | 8.24 | 8.24 | 2,413 |
| chatbot-opus-purdue | 284/14,949 | 19.00 | 19.00 | 2,429 |
| docling | 0/0 | n/a | n/a | 0 |
| mineru-basic | 474/5,386 | 88.01 | 83.55 | 647 |
| pymupdf4llm | 0/0 | n/a | n/a | 0 |

**Equations the rule could not check**

| System | TeX that did not convert | No gold partner and under 3 symbols |
|---|---|---|
| a11y-stem | 0 | 252 |
| chatbot-haiku-purdue | 197 | 1,045 |
| chatbot-opus-purdue | 312 | 679 |
| docling | 0 | 0 |
| mineru-basic | 31 | 93 |
| pymupdf4llm | 0 | 0 |

These are counted, never scored as correct.

**Figure descriptions: judged as stating something the image contradicts**

| System | Judge: factual error / descriptions judged | Rate [cluster 95%] |
|---|---|---|
| a11y-stem | 0/138 | 0.0000 [0.0000, 0.0000] |
| chatbot-haiku-purdue | 12/133 | 0.0902 [0.0385, 0.1532] |
| chatbot-opus-purdue | 3/134 | 0.0224 [0.0000, 0.0612] |
| docling | 0/0 | n/a |
| mineru-basic | 0/0 | n/a |
| pymupdf4llm | 0/0 | n/a |

Agreement with a model judge reading the image (claude-opus-5-5), not accuracy; the judge is not calibrated against human labels in this run. A figure with no description is not judged here (it is a fail in the figure tables).

Units counted per system and section, with the text of every flagged unit, are not part of this file; `a11y-stem eval invented score --units` writes them for one output.

## Comparison with the chatbot recipe

The chatbot recipe is what an instructor does today: upload the PDF to a general model and type one sentence. Prompt of the primary arm, from Purdue Innovative Learning ("Accessibility in Mathematics", section "PDF to HTML / Using Gemini", https://www.purdue.edu/innovativelearning/tools-resources/accessibility/digital-instructional-materials-accessibility-checklist/digital-math/, page last modified 2026-07-28, read 2026-10-09), verbatim, and quoted as the input it is, not as a claim this project makes:

<!-- quote -->
> Convert to accessible HTML compliant with WCAG 2.2 AA with complete MathJax headers and remove [cite].
<!-- /quote -->

Each arm sends the PDF as a document block and a 150 dpi image of every page, then the prompt; no system prompt, no tools, the model's default reasoning effort, at most 64,000 output tokens, streamed (`proof.chatbot`). The HTML in the reply is scored as it comes: a reply that was cut off or empty is a failure of the section, not asked again. Equations are read from the reply's MathJax TeX or MathML, converted the way a11y-stem converts its own model's LaTeX, and aligned to the gold equations by the document task's code, so the same scorer reads both systems.

Arms: `chatbot-opus-purdue` is the primary arm (claude-opus-5-5, two runs); `chatbot-haiku-purdue` is the same Purdue prompt on the cheaper model (claude-haiku-5-5, two runs). The cheaper arm is descriptive. The registration's expert-prompt arm is not run on Claude (Addendum 6).

**Family A: a11y-stem against the Purdue-prompt chatbot on claude-opus-5-5**

| Measure | Against | a11y-stem | Other | Difference (a11y-stem minus other) | Cluster 99.000% (k = 5): the claim | Cluster 95% | Sign-flip p | Result |
|---|---|---|---|---|---|---|---|---|
| end_to_end_exact_lenient | chatbot-opus-purdue | 0.5037 | 0.6216 | -0.1180 | [-0.1992, -0.0254] | [-0.1796, -0.0490] | 0.0234 | **chatbot-opus-purdue is higher by point estimate: the registered interval excludes 0, the sign-flip test does not (p = 0.0234)** |
| unflagged_wrong_per_100 | chatbot-opus-purdue | 23.30 | 24.00 | -0.69 | [-11.31, 13.19] | [-8.74, 8.90] | 0.5542 | **no difference this set can detect** |
| invented prose sentence rate | chatbot-opus-purdue | 0.0792 | 0.0403 | 0.0389 | [-0.0135, 0.0949] | [-0.0012, 0.0803] | 0.0275 | **no difference this set can detect** |
| invented number rate | chatbot-opus-purdue | 0.0000 | 0.0016 | -0.0016 | [-0.0044, 0.0000] | [-0.0037, 0.0000] | 0.5000 | **no difference this set can detect** |
| heading F1 | chatbot-opus-purdue | 0.9613 | 0.9315 | 0.0298 | [-0.0119, 0.1022] | [-0.0036, 0.0803] | 0.0332 | **no difference this set can detect** |

Paired cluster bootstrap by section: the same resampled sections for both systems, B = 10,000, seed 0. "X is better" is written only when the 99% interval (Bonferroni, k = 5) of the difference excludes 0; otherwise "no difference this set can detect". With 15 sections a percentile bootstrap is approximate and tends to be a little narrow. The chatbot's two runs are pooled; a11y-stem's document task also has two runs, and its headings task one. Every number here is a rate over all items, and a section that failed counts as a failure. Sign-flip p: two-sided p-value of a paired sign-flip permutation test on the per-section differences, a robustness check that is not part of the registered rule; the Bonferroni threshold for it is 0.01000. With fewer than 10 sections no verdict is written (a bootstrap over so few sections only returns the sections' own extremes), and a "better" that the sign-flip test does not support is written as "higher by point estimate".

**All chatbot arms: equations (preregistration 7.1)**

| Metric | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 0.5037 [0.3755, 0.5985] | 0.6216 [0.4640, 0.7274] | 0.5997 [0.4368, 0.7078] |
| end_to_end_exact_lenient, without G1 equations | 0.5204 [0.3949, 0.6141] | 0.6404 [0.4898, 0.7431] | 0.6200 [0.4605, 0.7270] |
| unflagged_wrong_per_100 | 23.30 [18.97, 29.46] | 24.00 [16.13, 33.43] | 25.24 [18.19, 34.56] |
| matched_exact_lenient (aligned pairs only) | 0.5666 [0.4361, 0.6619] | 0.7007 [0.5788, 0.7865] | 0.6805 [0.5443, 0.7699] |
| detection_recall | 0.8890 [0.8454, 0.9176] | 0.8871 [0.7436, 0.9648] | 0.8813 [0.7689, 0.9468] |
| detection_precision | 0.7349 [0.5974, 0.8295] | 0.5555 [0.3672, 0.7141] | 0.5224 [0.3407, 0.6814] |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 0.6183 [0.4625, 0.7231] | 0.5913 [0.4292, 0.6980] |

`value [cluster 95% interval by section]`. A system that asks no questions has every wrong equation unflagged. G1 equations (operators packed into OpenStax text) can fail exact match when the answer is right. The allowlist-off row converts a system's TeX without a11y-stem's LaTeX allowlist (descriptive; a11y-stem's own allowlist is a product rule, so it has no such row).

**All chatbot arms: headings (7.2)**

| Metric | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| heading F1 | 0.9613 [0.9474, 0.9772] | 0.9315 [0.8914, 0.9580] | 0.9140 [0.8724, 0.9449] |
| heading precision | 0.9467 [0.9272, 0.9703] | 0.9409 [0.9059, 0.9682] | 0.9095 [0.8649, 0.9418] |
| heading recall | 0.9764 [0.9564, 0.9935] | 0.9224 [0.8663, 0.9614] | 0.9185 [0.8707, 0.9558] |
| heading level_accuracy (among matched) | 1.0000 [1.0000, 1.0000] | 0.3408 [0.2640, 0.4672] | 0.5080 [0.3602, 0.7143] |

Levels are compared relative to each side's top level in the section. A system that writes an `h1` document title the PDF does not have gets a false positive for it.

**All chatbot arms: figures (7.3)**

| Metric | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| figure judge_pass_rate (no description = fail) | 0.9710 [0.9333, 1.0000] | 0.8043 [0.6797, 0.9048] | 0.8333 [0.7422, 0.9071] |
| figure judge_factual_error_rate (among judged) | 0.0000 [0.0000, 0.0000] | 0.0224 [0.0000, 0.0612] | 0.0902 [0.0385, 0.1532] |
| figures with no description | 0.0000 [0.0000, 0.0000] | 0.0290 [0.0000, 0.0727] | 0.0362 [0.0000, 0.0865] |

Over the figures with an OpenStax alt text. Agreement with a model judge (claude-opus-5-5) that sees the image, the candidate, the OpenStax alt and the caption, never which system wrote the candidate; not accuracy, and not calibrated against human labels in this run. A figure with no description is a fail. Judge cost is reported apart.

**All chatbot arms: invented content (7.5; the units are in the invented-content section)**

| Metric | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| invented prose sentences (text-layer mismatch, rule unaudited) | 0.0792 [0.0493, 0.1224] | 0.0403 [0.0178, 0.0697] | 0.0367 [0.0214, 0.0583] |
| invented numbers (text-layer mismatch, rule unaudited) | 0.0000 [0.0000, 0.0000] | 0.0016 [0.0000, 0.0037] | 0.0016 [0.0004, 0.0029] |
| headings the PDF does not support | 0.0278 [0.0216, 0.0378] | 0.0180 [0.0063, 0.0340] | 0.0224 [0.0093, 0.0424] |
| aligned equations with an added symbol | 0.0870 [0.0481, 0.1236] | 0.0202 [0.0065, 0.0489] | 0.0166 [0.0054, 0.0449] |
| equations with no gold partner and no match in the PDF | 0.0269 [0.0000, 0.0787] | 0.2938 [0.0491, 0.5431] | 0.2261 [0.0351, 0.4791] |

All text, including text a11y-stem wrote itself. A section a system failed on has no units, so it adds nothing here; the raw rates include paraphrase the rule counts as unsupported, and the audit that measures that has not been done. A rate of exactly 0 has a zero-width bootstrap interval (every resample is 0): the Wilson upper bound in the last table is the bound to read.

**All chatbot arms: coverage (7.6)**

| Metric | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| coverage: PDF word 6-grams found in the output | 0.6526 [0.5723, 0.7259] | 0.6461 [0.5576, 0.7260] | 0.6205 [0.5393, 0.6928] |

**All chatbot arms: counts behind the rates**

| Count | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 1,379/2,738 | 1,702/2,738 | 1,642/2,738 |
| end_to_end_exact_lenient, without G1 equations | 1,375/2,642 | 1,692/2,642 | 1,638/2,642 |
| unflagged_wrong_per_100 | 638/2,738 | 657/2,738 | 691/2,738 |
| matched_exact_lenient (aligned pairs only) | 1,379/2,434 | 1,702/2,429 | 1,642/2,413 |
| detection_recall | 2,434/2,738 | 2,429/2,738 | 2,413/2,738 |
| detection_precision | 2,434/3,312 | 2,429/4,373 | 2,413/4,619 |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 1,693/2,738 | 1,619/2,738 |
| heading F1 | 497/525 pred, 497/509 gold | 939/998 pred, 939/1,018 gold | 935/1,028 pred, 935/1,018 gold |
| heading precision | 497/525 | 939/998 | 935/1,028 |
| heading recall | 497/509 | 939/1,018 | 935/1,018 |
| heading level_accuracy (among matched) | 497/497 | 320/939 | 475/935 |
| figure judge_pass_rate (no description = fail) | 134/138 | 111/138 | 115/138 |
| figure judge_factual_error_rate (among judged) | 0/138 | 3/134 | 12/133 |
| figures with no description | 0/138 | 4/138 | 5/138 |
| invented prose sentences (text-layer mismatch, rule unaudited) | 353/4,457 | 167/4,146 | 152/4,139 |
| invented numbers (text-layer mismatch, rule unaudited) | 0/3,672 | 5/3,088 | 5/3,058 |
| headings the PDF does not support | 30/1,080 | 18/998 | 23/1,028 |
| aligned equations with an added symbol | 208/2,392 | 49/2,429 | 40/2,413 |
| equations with no gold partner and no match in the PDF | 14/521 | 280/953 | 218/964 |
| coverage: PDF word 6-grams found in the output | 59,342/90,934 | 58,757/90,934 | 56,425/90,934 |

**All chatbot arms: Wilson 95% intervals (items treated as independent; narrower than the cluster intervals)**

| Wilson 95% (items treated as independent) | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | [0.4849, 0.5224] | [0.6033, 0.6396] | [0.5812, 0.6179] |
| end_to_end_exact_lenient, without G1 equations | [0.5014, 0.5394] | [0.6219, 0.6585] | [0.6013, 0.6383] |
| matched_exact_lenient (aligned pairs only) | [0.5468, 0.5861] | [0.6822, 0.7186] | [0.6616, 0.6988] |
| detection_recall | [0.8767, 0.9002] | [0.8747, 0.8985] | [0.8686, 0.8929] |
| detection_precision | [0.7196, 0.7497] | [0.5407, 0.5701] | [0.5080, 0.5368] |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | [0.6000, 0.6364] | [0.5728, 0.6096] |
| heading precision | [0.9240, 0.9628] | [0.9245, 0.9539] | [0.8904, 0.9256] |
| heading recall | [0.9592, 0.9865] | [0.9043, 0.9373] | [0.9000, 0.9337] |
| figure judge_pass_rate (no description = fail) | [0.9278, 0.9887] | [0.7303, 0.8619] | [0.7623, 0.8863] |
| figure judge_factual_error_rate (among judged) | [0.0000, 0.0271] | [0.0076, 0.0638] | [0.0524, 0.1511] |
| figures with no description | [0.0000, 0.0271] | [0.0113, 0.0722] | [0.0156, 0.0820] |
| invented prose sentences (text-layer mismatch, rule unaudited) | [0.0716, 0.0875] | [0.0347, 0.0467] | [0.0314, 0.0429] |
| invented numbers (text-layer mismatch, rule unaudited) | [0.0000, 0.0010] | [0.0007, 0.0038] | [0.0007, 0.0038] |
| headings the PDF does not support | [0.0195, 0.0394] | [0.0114, 0.0283] | [0.0150, 0.0333] |
| aligned equations with an added symbol | [0.0763, 0.0989] | [0.0153, 0.0266] | [0.0122, 0.0225] |
| equations with no gold partner and no match in the PDF | [0.0161, 0.0446] | [0.2658, 0.3235] | [0.2009, 0.2536] |

**Each run on its own**

| Run | end_to_end_exact_lenient | unflagged_wrong_per_100 | heading F1 | invented sentences | invented numbers |
|---|---|---|---|---|---|
| chatbot-opus-purdue run 1 | 0.6253 | 23.67 | 0.9296 | 0.0274 | 0.0013 |
| chatbot-opus-purdue run 2 | 0.6180 | 24.32 | 0.9335 | 0.0528 | 0.0019 |
| chatbot-haiku-purdue run 1 | 0.6026 | 24.76 | 0.9237 | 0.0361 | 0.0019 |
| chatbot-haiku-purdue run 2 | 0.5968 | 25.71 | 0.9043 | 0.0373 | 0.0013 |

**End-to-end exact match by section**

| Section | Gold equations | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|---|
| college-physics/29-3-photon-energies-and-the-electromagnetic-spectrum | 47 | 0.4681 | 0.4681 | 0.4468 |
| college-physics/5-3-elasticity-stress-and-strain | 121 | 0.6653 | 0.8264 | 0.8099 |
| college-physics/28-4-relativistic-addition-of-velocities | 68 | 0.4412 | 0.4853 | 0.4853 |
| chemistry/19-3-spectroscopic-and-magnetic-properties-of-coordination-compounds | 9 | 0.2222 | 0.2222 | 0.2222 |
| chemistry/7-3-lewis-symbols-and-structures | 25 | 0.0000 | 0.0000 | 0.0000 |
| chemistry/14-7-acid-base-titrations | 63 | 0.0317 | 0.0317 | 0.0238 |
| precalculus/5-4-right-triangle-trigonometry | 162 | 0.4043 | 0.7191 | 0.6759 |
| precalculus/1-6-absolute-value-functions | 205 | 0.6610 | 0.8659 | 0.8488 |
| precalculus/3-1-complex-numbers | 206 | 0.7184 | 0.7694 | 0.7524 |
| introductory-statistics/2-4-box-plots | 0 | n/a | n/a | n/a |
| introductory-statistics/4-5-hypergeometric-distribution | 4 | 0.2500 | 1.0000 | 1.0000 |
| introductory-statistics/3-1-terminology | 31 | 0.1290 | 0.0968 | 0.0968 |
| intermediate-algebra/4-3-solve-mixture-applications-with-systems-of-equations | 65 | 0.5000 | 0.3923 | 0.2154 |
| intermediate-algebra/4-6-solve-systems-of-equations-using-determinants | 183 | 0.3607 | 0.4699 | 0.4590 |
| intermediate-algebra/1-5-properties-of-real-numbers | 180 | 0.5583 | 0.6722 | 0.6778 |

**End-to-end exact match by book**

| Book (point estimates; three sections each) | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| College Physics | 0.5614 | 0.6568 | 0.6441 |
| Chemistry | 0.0412 | 0.0412 | 0.0361 |
| Precalculus | 0.6091 | 0.7897 | 0.7653 |
| Introductory Statistics | 0.1429 | 0.2000 | 0.2000 |
| Intermediate Algebra | 0.4650 | 0.5432 | 0.5140 |

**Heading F1 by book**

| Book (point estimates; three sections each) | a11y-stem | chatbot-opus-purdue | chatbot-haiku-purdue |
|---|---|---|---|
| College Physics | 1.0000 | 0.8867 | 0.9068 |
| Chemistry | 1.0000 | 0.8037 | 0.7489 |
| Precalculus | 0.9524 | 0.9701 | 0.9499 |
| Introductory Statistics | 0.9398 | 0.9405 | 0.9240 |
| Intermediate Algebra | 0.9434 | 0.9531 | 0.9370 |

**Cost and time per PDF page**

| System | Total API cost | Cost/page median | p95 | min | max | Seconds/page median | p95 | min | max | Peak memory (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| a11y-stem (document task) | $6.6240 | 0.01252 | 0.02249 | 0.00270 | 0.02316 | 4.28087 | 5.94030 | 1.19000 | 6.18687 | n/a |
| chatbot-opus-purdue | $22.3407 | 0.04765 | 0.07367 | 0.03413 | 0.12942 | 10.70750 | 20.00340 | 5.72857 | 44.74444 | n/a |
| chatbot-haiku-purdue | $1.4878 | 0.00147 | 0.00460 | 0.00097 | 0.00480 | 4.50079 | 7.73566 | 2.40357 | 9.03333 | n/a |

Chatbot: list-price cost of each call from its usage, and the call's wall clock, per PDF page of the section. a11y-stem: pipeline plus second check per page, and the document task's wall clock at concurrency 4 on the recording machine. Judge cost is not included for any system.

**Sections that did not convert**

| System | Outputs | Expected | ok | truncated | empty | timeout | crashed | not run |
|---|---|---|---|---|---|---|---|---|
| chatbot-opus-purdue | 30 | 30 | 30 | 0 | 0 | 0 | 0 | 0 |
| chatbot-haiku-purdue | 30 | 30 | 30 | 0 | 0 | 0 | 0 | 0 |

A section that is not ok scores as found nothing: its gold items stay in every denominator.

**Not measured in this recording:** whether a wrong equation changes the meaning (the blind labels of preregistration 7.8 are the author's, and none exist yet), and the agreement of the figure judge with human labels.

## Comparison with open PDF parsers

The parsers solve a different problem: getting a PDF's text into an LLM. They are here because they are what an engineer reaches for first, and because equations, headings and reading order are the hard part of both problems. The comparison is on the measures they share, and there is no overall score and no winner. Every parser runs on the same PDFs, byte for byte, with the tool's defaults on CPU, once per section, in a fresh process; a crash, an empty output or a 30-minute limit scores the section as found nothing. Each parser's Markdown export is read by the same reader as the chatbot's HTML (`eval.invented.read`), its maths converted and aligned to gold by the same code as a11y-stem's.

**What each system is built for (not failures: design differences)**

| System | Built for | Not built for |
|---|---|---|
| a11y-stem | accessible HTML from STEM course PDFs, with a review screen | re-typing prose (it keeps the PDF's text); tables in production (behind a flag); scans |
| Docling | lossless document conversion for gen-AI pipelines (Markdown, JSON; HTML is one export) | accessibility remediation: no alt text on pictures, one heading level in the export, formulas left undecoded unless the opt-in enrichment is on |
| MinerU | extraction for LLM corpora and RAG (Markdown, JSON) | accessible HTML: empty alt text on images, one heading level in its Markdown |
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
| docling | docling 2.138.0 | 0 | default settings: a formula is not decoded, so no LaTeX is written (its Markdown holds `<!-- formula-not-decoded -->`); the registered arm with formula recognition on was not carried out (Addendum 4) |
| mineru-basic | mineru 4.0.12 | 1,179 | the basic tier on CPU; MinerU's documentation names the standard tier as its default, and that tier was not run, so this is not MinerU at its default settings |
| pymupdf4llm | pymupdf4llm 1.28.2 | 0 | has no formula recognition |

**Family B: a11y-stem against each parser**

| Measure | Against | a11y-stem | Other | Difference (a11y-stem minus other) | Cluster 99.375% (k = 8): the claim | Cluster 95% | Sign-flip p | Result |
|---|---|---|---|---|---|---|---|---|
| end_to_end_exact_lenient | docling | 0.5037 | 0.0000 | 0.5037 | [0.3180, 0.6336] | [0.3755, 0.5985] | 0.0002 | **a11y-stem is better** |
| heading F1 | docling | 0.9613 | 0.9104 | 0.0509 | [-0.0052, 0.1481] | [0.0043, 0.1173] | 0.0186 | **no difference this set can detect** |
| end_to_end_exact_lenient | mineru-basic | 0.5037 | 0.1738 | 0.3298 | [0.1760, 0.4365] | [0.2225, 0.4148] | 0.0002 | **a11y-stem is better** |
| heading F1 | mineru-basic | 0.9613 | 0.9228 | 0.0386 | [0.0047, 0.0789] | [0.0120, 0.0670] | 0.0073 | **a11y-stem is higher by point estimate: the registered interval excludes 0, the sign-flip test does not (p = 0.0073)** |
| end_to_end_exact_lenient | pymupdf4llm | 0.5037 | 0.0000 | 0.5037 | [0.3180, 0.6336] | [0.3755, 0.5985] | 0.0002 | **a11y-stem is better** |
| heading F1 | pymupdf4llm | 0.9613 | 0.8297 | 0.1316 | [0.0292, 0.2706] | [0.0509, 0.2229] | 0.0088 | **a11y-stem is higher by point estimate: the registered interval excludes 0, the sign-flip test does not (p = 0.0088)** |

Preregistration 8.3 as written: 2 comparisons per parser, k = 8 (3 parsers ran; the registered four are counted, Addendum 1 D3), the 99.375% paired cluster-bootstrap interval of the difference decides. For a parser that writes no LaTeX by design (`docling`, `pymupdf4llm`) the equation row compares a11y-stem with a tool that does not attempt equations; read it as a description of what the tool is built for, not as evidence that a11y-stem transcribes better. Flat heading levels in a parser's export are a property of that export. Sign-flip p: two-sided p-value of a paired sign-flip permutation test on the per-section differences, a robustness check that is not part of the registered rule; the Bonferroni threshold for it is 0.00625. With fewer than 10 sections no verdict is written (a bootstrap over so few sections only returns the sections' own extremes), and a "better" that the sign-flip test does not support is written as "higher by point estimate".

**All parsers: equations (preregistration 7.1)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 0.5037 [0.3755, 0.5985] | 0.0000 [0.0000, 0.0000] | 0.1738 [0.1081, 0.2177] | 0.0000 [0.0000, 0.0000] |
| end_to_end_exact_lenient, without G1 equations | 0.5204 [0.3949, 0.6141] | 0.0000 [0.0000, 0.0000] | 0.1802 [0.1148, 0.2235] | 0.0000 [0.0000, 0.0000] |
| unflagged_wrong_per_100 | 23.30 [18.97, 29.46] | 0.00 [0.00, 0.00] | 27.61 [22.15, 34.74] | 0.00 [0.00, 0.00] |
| matched_exact_lenient (aligned pairs only) | 0.5666 [0.4361, 0.6619] | n/a | 0.3679 [0.2400, 0.4420] | n/a |
| detection_recall | 0.8890 [0.8454, 0.9176] | 0.0000 [0.0000, 0.0000] | 0.4726 [0.3810, 0.5529] | 0.0000 [0.0000, 0.0000] |
| detection_precision | 0.7349 [0.5974, 0.8295] | n/a | 0.5488 [0.3798, 0.6914] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 0.0000 [0.0000, 0.0000] | 0.1709 [0.1070, 0.2132] | 0.0000 [0.0000, 0.0000] |

`value [cluster 95% interval by section]`. A system that asks no questions has every wrong equation unflagged. G1 equations (operators packed into OpenStax text) can fail exact match when the answer is right. The allowlist-off row converts a system's TeX without a11y-stem's LaTeX allowlist (descriptive; a11y-stem's own allowlist is a product rule, so it has no such row).

**All parsers: headings (7.2)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| heading F1 | 0.9613 [0.9474, 0.9772] | 0.9104 [0.8418, 0.9576] | 0.9228 [0.8937, 0.9547] | 0.8297 [0.7384, 0.9185] |
| heading precision | 0.9467 [0.9272, 0.9703] | 0.8668 [0.7599, 0.9444] | 0.9558 [0.9359, 0.9741] | 0.8265 [0.7284, 0.9258] |
| heading recall | 0.9764 [0.9564, 0.9935] | 0.9587 [0.9338, 0.9805] | 0.8919 [0.8480, 0.9452] | 0.8330 [0.6753, 0.9706] |
| heading level_accuracy (among matched) | 1.0000 [1.0000, 1.0000] | 0.0307 [0.0238, 0.0421] | 0.1652 [0.0522, 0.3262] | 1.0000 [1.0000, 1.0000] |

Levels are compared relative to each side's top level in the section. A system that writes an `h1` document title the PDF does not have gets a false positive for it.

**All parsers: figures (7.3)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| figure judge_pass_rate (no description = fail) | 0.9710 [0.9333, 1.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| figure judge_factual_error_rate (among judged) | 0.0000 [0.0000, 0.0000] | n/a | n/a | n/a |
| figures with no description | 0.0000 [0.0000, 0.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] |

Over the figures with an OpenStax alt text. Agreement with a model judge (claude-opus-5-5) that sees the image, the candidate, the OpenStax alt and the caption, never which system wrote the candidate; not accuracy, and not calibrated against human labels in this run. A figure with no description is a fail. Judge cost is reported apart.

**All parsers: invented content (7.5; the units are in the invented-content section)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| invented prose sentences (text-layer mismatch, rule unaudited) | 0.0792 [0.0493, 0.1224] | 0.0086 [0.0009, 0.0207] | 0.0078 [0.0005, 0.0188] | 0.0299 [0.0150, 0.0465] |
| invented numbers (text-layer mismatch, rule unaudited) | 0.0000 [0.0000, 0.0000] | 0.0138 [0.0038, 0.0237] | 0.0009 [0.0000, 0.0024] | 0.0224 [0.0067, 0.0424] |
| headings the PDF does not support | 0.0278 [0.0216, 0.0378] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] |
| aligned equations with an added symbol | 0.0870 [0.0481, 0.1236] | n/a | 0.2643 [0.2049, 0.3209] | n/a |
| equations with no gold partner and no match in the PDF | 0.0269 [0.0000, 0.0787] | n/a | 0.1397 [0.0386, 0.2593] | n/a |

All text, including text a11y-stem wrote itself. A section a system failed on has no units, so it adds nothing here; the raw rates include paraphrase the rule counts as unsupported, and the audit that measures that has not been done. A rate of exactly 0 has a zero-width bootstrap interval (every resample is 0): the Wilson upper bound in the last table is the bound to read.

**All parsers: coverage (7.6)**

| Metric | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| coverage: PDF word 6-grams found in the output | 0.6526 [0.5723, 0.7259] | 0.7237 [0.6487, 0.7897] | 0.6742 [0.6004, 0.7418] | 0.7999 [0.7610, 0.8368] |

**All parsers: counts behind the rates**

| Count | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | 1,379/2,738 | 0/1,369 | 238/1,369 | 0/1,369 |
| end_to_end_exact_lenient, without G1 equations | 1,375/2,642 | 0/1,321 | 238/1,321 | 0/1,321 |
| unflagged_wrong_per_100 | 638/2,738 | 0/1,369 | 378/1,369 | 0/1,369 |
| matched_exact_lenient (aligned pairs only) | 1,379/2,434 | 0/0 | 238/647 | 0/0 |
| detection_recall | 2,434/2,738 | 0/1,369 | 647/1,369 | 0/1,369 |
| detection_precision | 2,434/3,312 | 0/0 | 647/1,179 | 0/0 |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | 0/1,369 | 234/1,369 | 0/1,369 |
| heading F1 | 497/525 pred, 497/509 gold | 488/563 pred, 488/509 gold | 454/475 pred, 454/509 gold | 424/513 pred, 424/509 gold |
| heading precision | 497/525 | 488/563 | 454/475 | 424/513 |
| heading recall | 497/509 | 488/509 | 454/509 | 424/509 |
| heading level_accuracy (among matched) | 497/497 | 15/488 | 75/454 | 424/424 |
| figure judge_pass_rate (no description = fail) | 134/138 | 0/69 | 0/69 | 0/69 |
| figure judge_factual_error_rate (among judged) | 0/138 | 0/0 | 0/0 | 0/0 |
| figures with no description | 0/138 | 69/69 | 69/69 | 69/69 |
| invented prose sentences (text-layer mismatch, rule unaudited) | 353/4,457 | 18/2,090 | 16/2,046 | 73/2,439 |
| invented numbers (text-layer mismatch, rule unaudited) | 0/3,672 | 42/3,054 | 2/2,160 | 116/5,179 |
| headings the PDF does not support | 30/1,080 | 0/563 | 0/475 | 0/513 |
| aligned equations with an added symbol | 208/2,392 | 0/0 | 171/647 | 0/0 |
| equations with no gold partner and no match in the PDF | 14/521 | 0/0 | 57/408 | 0/0 |
| coverage: PDF word 6-grams found in the output | 59,342/90,934 | 32,906/45,467 | 30,656/45,467 | 36,369/45,467 |

**All parsers: Wilson 95% intervals (items treated as independent; narrower than the cluster intervals)**

| Wilson 95% (items treated as independent) | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| end_to_end_exact_lenient (all gold equations) | [0.4849, 0.5224] | [0.0000, 0.0028] | [0.1547, 0.1948] | [0.0000, 0.0028] |
| end_to_end_exact_lenient, without G1 equations | [0.5014, 0.5394] | [0.0000, 0.0029] | [0.1604, 0.2018] | [0.0000, 0.0029] |
| matched_exact_lenient (aligned pairs only) | [0.5468, 0.5861] | n/a | [0.3316, 0.4057] | n/a |
| detection_recall | [0.8767, 0.9002] | [0.0000, 0.0028] | [0.4463, 0.4991] | [0.0000, 0.0028] |
| detection_precision | [0.7196, 0.7497] | n/a | [0.5203, 0.5770] | n/a |
| end_to_end_exact_lenient, allowlist off (descriptive row) | n/a | [0.0000, 0.0028] | [0.1519, 0.1918] | [0.0000, 0.0028] |
| heading precision | [0.9240, 0.9628] | [0.8362, 0.8924] | [0.9334, 0.9709] | [0.7913, 0.8568] |
| heading recall | [0.9592, 0.9865] | [0.9378, 0.9729] | [0.8620, 0.9160] | [0.7981, 0.8629] |
| figure judge_pass_rate (no description = fail) | [0.9278, 0.9887] | [0.0000, 0.0527] | [0.0000, 0.0527] | [0.0000, 0.0527] |
| figure judge_factual_error_rate (among judged) | [0.0000, 0.0271] | n/a | n/a | n/a |
| figures with no description | [0.0000, 0.0271] | [0.9473, 1.0000] | [0.9473, 1.0000] | [0.9473, 1.0000] |
| invented prose sentences (text-layer mismatch, rule unaudited) | [0.0716, 0.0875] | [0.0055, 0.0136] | [0.0048, 0.0127] | [0.0239, 0.0375] |
| invented numbers (text-layer mismatch, rule unaudited) | [0.0000, 0.0010] | [0.0102, 0.0185] | [0.0003, 0.0034] | [0.0187, 0.0268] |
| headings the PDF does not support | [0.0195, 0.0394] | [0.0000, 0.0068] | [0.0000, 0.0080] | [0.0000, 0.0074] |
| aligned equations with an added symbol | [0.0763, 0.0989] | n/a | [0.2318, 0.2996] | n/a |
| equations with no gold partner and no match in the PDF | [0.0161, 0.0446] | n/a | [0.1094, 0.1767] | n/a |

**End-to-end exact match by section**

| Section | Gold equations | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|---|
| college-physics/29-3-photon-energies-and-the-electromagnetic-spectrum | 47 | 0.4681 | 0.0000 | 0.0000 | 0.0000 |
| college-physics/5-3-elasticity-stress-and-strain | 121 | 0.6653 | 0.0000 | 0.2893 | 0.0000 |
| college-physics/28-4-relativistic-addition-of-velocities | 68 | 0.4412 | 0.0000 | 0.0294 | 0.0000 |
| chemistry/19-3-spectroscopic-and-magnetic-properties-of-coordination-compounds | 9 | 0.2222 | 0.0000 | 0.1111 | 0.0000 |
| chemistry/7-3-lewis-symbols-and-structures | 25 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| chemistry/14-7-acid-base-titrations | 63 | 0.0317 | 0.0000 | 0.0000 | 0.0000 |
| precalculus/5-4-right-triangle-trigonometry | 162 | 0.4043 | 0.0000 | 0.2284 | 0.0000 |
| precalculus/1-6-absolute-value-functions | 205 | 0.6610 | 0.0000 | 0.2341 | 0.0000 |
| precalculus/3-1-complex-numbers | 206 | 0.7184 | 0.0000 | 0.2476 | 0.0000 |
| introductory-statistics/2-4-box-plots | 0 | n/a | n/a | n/a | n/a |
| introductory-statistics/4-5-hypergeometric-distribution | 4 | 0.2500 | 0.0000 | 0.0000 | 0.0000 |
| introductory-statistics/3-1-terminology | 31 | 0.1290 | 0.0000 | 0.0968 | 0.0000 |
| intermediate-algebra/4-3-solve-mixture-applications-with-systems-of-equations | 65 | 0.5000 | 0.0000 | 0.0308 | 0.0000 |
| intermediate-algebra/4-6-solve-systems-of-equations-using-determinants | 183 | 0.3607 | 0.0000 | 0.2295 | 0.0000 |
| intermediate-algebra/1-5-properties-of-real-numbers | 180 | 0.5583 | 0.0000 | 0.0944 | 0.0000 |

**Heading F1 by book**

| Book (point estimates; three sections each) | a11y-stem | docling | mineru-basic | pymupdf4llm |
|---|---|---|---|---|
| College Physics | 1.0000 | 0.9565 | 0.9625 | 0.9693 |
| Chemistry | 1.0000 | 0.9825 | 0.9913 | 0.9915 |
| Precalculus | 0.9524 | 0.9284 | 0.9198 | 0.8532 |
| Introductory Statistics | 0.9398 | 0.9286 | 0.9286 | 0.9647 |
| Intermediate Algebra | 0.9434 | 0.8462 | 0.8771 | 0.6216 |

**Time and memory per PDF page**

| System | Total API cost | Cost/page median | p95 | min | max | Seconds/page median | p95 | min | max | Peak memory (MB) |
|---|---|---|---|---|---|---|---|---|---|---|
| a11y-stem (document task) | $6.6240 | 0.01252 | 0.02249 | 0.00270 | 0.02316 | 4.28087 | 5.94030 | 1.19000 | 6.18687 | n/a |
| docling | $0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 3.67345 | 7.93241 | 2.19993 | 8.67304 | 3327 |
| mineru-basic | $0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 4.28371 | 13.79948 | 2.17400 | 15.34236 | 4235 |
| pymupdf4llm | $0 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.34993 | 0.44153 | 0.26852 | 0.45325 | 385 |

Parsers: wall clock of one fresh process per section divided by its PDF pages, model load included, CPU only, API cost $0 (hosting not estimated); peak memory is the process tree's maximum. a11y-stem: seconds of the document task at concurrency 4 (it waits on a hosted model, so its time is not CPU time). Timings are from one machine; the load average at each start is in the recorded run, and a busy machine makes a time an upper bound.

**Sections that did not convert**

| System | Outputs | Expected | ok | truncated | empty | timeout | crashed | not run |
|---|---|---|---|---|---|---|---|---|
| docling | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |
| mineru-basic | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |
| pymupdf4llm | 15 | 15 | 15 | 0 | 0 | 0 | 0 | 0 |

A section that is not ok scores as found nothing: its gold items stay in every denominator.

Parser timings: 0 runs started on a quiet machine (1-minute load under 1.5) and 0 did not. Host: AMD Ryzen 7 5800H with Radeon Graphics, 16 logical CPUs.

## Human labels and judge agreement

**Not done.** The labels are written by the author, blind to the system, after the run (preregistration 7.8). None exist, so this report shows no number from them. Until they do:

- whether a wrong equation changes the meaning is not known: every wrong equation is counted as wrong, and the scorer's ceiling on a hand-written sample is stated in the registered addendum;
- the figure judge (a model) is not calibrated against human labels, so its numbers are agreement with that judge and nothing more;
- the invented-content rule's precision and recall are not measured, so its rates include paraphrase it cannot tell from invention.

## What these numbers leave out

- Fifteen sections of five books: intervals come from resampling sections, which is approximate with this few clusters; per-book numbers are point estimates.
- The sections come from retired first editions, whose text may differ from the current editions.
- Any change to a11y-stem's prompts, rules, thresholds, models or scorers after the first held-out output exists means these numbers can no longer be quoted as held-out results for the changed system.
- Docling is run in its default settings (a formula is not decoded); the registered arm with formula recognition on was not carried out (Addendum 4). MinerU is run at its basic tier, not the standard tier its documentation names as the default.
- A paired verdict is written only when the registered Bonferroni interval excludes 0; where a sign-flip test on the section differences disagrees, the report says 'higher by point estimate'.
- a11y-stem's review screen asked about none of the 28 wrong headings in the interim headings run: that is a limit of the pipeline, documented here and not fixed on the held-out data.
- The section PDFs are printed from OpenStax's HTML by this project's own print stylesheet, not the publisher's PDFs, and a11y-stem's heading rules were developed on pages it printed: a heading result may not carry over to publisher PDFs.
- The model-dependent parts ran on Claude, not on the OpenAI models the registration names (Addendum 6): a11y-stem with claude-haiku-5-5 and claude-opus-5-5 and no second automated check, the Purdue-prompt chatbot arms on claude-opus-5-5 and claude-haiku-5-5, the figure judge on claude-opus-5-5. The expert-prompt chatbot arm was not run. Each arm's number of runs is the one this report states.
- The invented-content rows count units the PDF's text layer does not contain, by a rule whose precision and recall have not been audited; for a parser's Markdown they include comment markers and mathematics the reader cannot match. Read them as a text-layer mismatch.
- Exact match of MathML is strict: a correct answer can fail on markup (see G1), and a wrong digit in a long equation costs little tree-edit similarity. It is not a measure of how a screen reader would speak the result.
- With this few sections the bootstrap intervals are approximate and tend to be a little narrow. Per-book numbers are point estimates.
- The figure judge is a model and is not calibrated against human labels.
- A replay shows that the numbers follow from the recorded answers and this code. It cannot show that a new live run would give the same answers: model output varies from run to run, which is why tasks are repeated and each run's value is shown.

## Reproduce

```
uv sync --locked                      # Python 3.12+, uv; no key
make proof SET=heldout-2026-10      # replay: writes this file, no network needed
make proof-sources SET=heldout-2026-10   # also fetch the answer key from OpenStax and print the PDFs again (network, Chromium)
make proof-live SET=heldout-2026-10 MAX_USD=N   # new live run with OPENAI_API_KEY, capped
```
A replay writes byte-identical output on any machine with the same dependency versions. A live run calls the models again, so its numbers differ from the recorded ones by the usual run-to-run variation.

## Attribution

- Chemistry by Paul Flowers, William R. Robinson, PhD, Richard Langley, Klaus Theopold, OpenStax, version 741a7af, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original at https://openstax.org/details/books/chemistry.
- College Physics by Paul Peter Urone, Roger Hinrichs, OpenStax, version 1aa3e7a, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original at https://openstax.org/details/books/college-physics.
- Intermediate Algebra by Lynn Marecek, OpenStax, version eb2e29d, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original at https://openstax.org/details/books/intermediate-algebra.
- Introductory Statistics by Barbara Illowsky, Susan Dean, OpenStax, version 1d23970, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original at https://openstax.org/details/books/introductory-statistics.
- Precalculus by Jay Abramson, OpenStax, version ebc5beb, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original at https://openstax.org/details/books/precalculus.
- Changes: each section was reformatted as a printable page for evaluating a11y-stem, image alt text was left out of the print, and no wording was edited. a11y-stem is not affiliated with or endorsed by OpenStax.
