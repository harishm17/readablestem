# Addendum 1 to the held-out pre-registration: run decisions and the freeze

Written 2026-10-13, before any system under test (ReadableSTEM, the chatbot recipe, the open
parsers) has produced any output for the 15 held-out sections. `PREREGISTRATION.md`
(SHA-256 `8127b143cdbf278f9a14650e086c70b4af7063d789b16ffdfc266399e5d4c5d9`) stands unchanged;
this file adds what its section 6.3 and section 12 left open, and records what was frozen.
It is read-only once written, and its SHA-256 and that of `PARSERS-PLAN.md` are appended to
`PREREG.sha256` before the run.

Who wrote it: the run's integrator, working from the brief. The decisions of
`PARSERS-PLAN.md` section 11 (Marker, the Docling formula arm, the shape of family B, the
luna arm, the label samples) had not been answered by the author when this was written. The
defaults below are the ones that need no licence risk and no change to the registration.
The author can reject any of them; the parts they touch are then published as deviations.

## 1. Decisions taken for the run

| # | Decision | Why |
|---|---|---|
| D1 | **Marker is not run** (plan 6.1, option a). | Its weights' licence counts evaluating as use and bars use by a competing service, with no research exception. |
| D2 | **Docling with formula recognition (`docling-formula`) runs under the 30-minute rule** of registration section 9, one section at a time, last, on a quiet machine. Sections it does not finish in 30 minutes score as found nothing, and the report says how many. | It is the registration's rule; a longer limit would need a v2 written before the run. |
| D3 | **Family B is the registration's literal rule**: 2 comparisons per parser that ran, so k = 8 with four parser arms (`docling`, `docling-formula`, `mineru-basic`, `pymupdf4llm`), each judged at the 99.375% paired cluster interval. The plan's section 4.6 proposal (equations only against parsers that write LaTeX) is **not** adopted; instead the report states beside the two parsers that write no LaTeX by design that the equation row describes what they are built for. | 4.6 needs the author's amendment. |
| D4 | **`chatbot-luna-purdue` (gpt-6-luna, Purdue prompt, 2 runs) is a descriptive arm**, paid from the chatbot cap. | Plan arm C2. |
| D5 | **Spend caps from the brief: $12 for ReadableSTEM (all four tasks, judge included) and $10 for the chatbot arms (the figure judge on their descriptions included).** The registration's $50 cap is not used. Arm order of spending: `chatbot-sol-purdue` (2 runs), `chatbot-luna-purdue` (2), `chatbot-sol-expert` (1) last, which runs only if the cap still holds it; if the cap does not, it is reported as not run, never half scored. | The cap is the brief's. |
| D6 | ReadableSTEM's tasks run in the order document (x2), headings, figures (x2), equations (x2), with `--keep-finished`: if the $12 cap stops the run, the runs that finished are kept and the report opens with an **INCOMPLETE RUN** banner. | The registration's estimate for these tasks is about $12, which is the cap; the primary task goes first. |
| D7 | No human labels exist in this run: the meaning-level labels (7.8 set 1), the figure judge's agreement with the author (set 2) and the invented-content audit (set 3) are **not done**. PROOF.md says so. The scorer-readiness check (12.3) was done by the integrator only (section 3 below). | Labels are the author's. |

## 2. Scoring choices the registration leaves open

- **One reader for every system but ReadableSTEM's own output model.** The chatbot's HTML and each
  parser's Markdown export are read with `readablestem.eval.invented.read` (MathJax TeX,
  `<math>`, Markdown `$...$`), maths converted by `equation_step.clean_latex` and the allowlisted
  `latex2mathml` path, aligned to gold by `pipeline_eval.document_page_item` (MathML signature
  only), headings by `pipeline_eval.heading_page_item`. ReadableSTEM's own HTML (rebuilt by the
  replay) goes through the same reader for the invented-content and coverage rows; its equation
  and heading numbers come from its tasks as in registration 7.1 and 7.2.
- **The parser plan's adapters (`proof/parsers/src/parsers_bench/adapters`) are not used for the
  numbers in PROOF.md.** The harness converts, and writes each tool's Markdown export (images
  cut to a marker); the shared reader scores it. Plan rows that need the adapters (normalised
  rows, the MathJax converter row, equation content retained in any form, reading order,
  images kept, table TEDS, automated HTML checks, the pre-flag row) are **not computed**.
  Computed from the plan: the allowlist-free row.
- **Invented content** is `readablestem.eval.invented`, code fingerprint
  `b2f7596bdf9acae5b19e2ce3d284f7076f73c1a9f9df8896b79755f80a08f7e0`, frozen here. The headline
  for ReadableSTEM is **all text**, including the sentences and headings the tool writes itself
  ("About this version" and notices); the same table without them is printed beside it.
  The figure-description row is the figure judge's `factual_error` over **all** gold figures with
  an OpenStax alt (not a sample), as 7.3 defines it; the same verdicts feed the figure pass
  rate.
- **Coverage** (7.6) is the share of the PDF's word 6-grams found in the output. The
  per-paragraph kept/partial/missing count is not computed.
- **Chatbot figures** (7.3): the description is found by `eval.invented.figures.bind` (figure
  number, or at least half of the gold caption's word 4-grams in the description or its
  caption); the judge (`judge_figure`, gpt-6.1-sol, the gold image) sees the candidate, the
  OpenStax alt and the caption. No description found: no call, a fail.
- **A run's failure statuses**: `truncated` (the API said incomplete, or the reply ends with
  `CONTINUED IN NEXT REPLY`), `empty`, `crashed`; for parsers `timeout` (30 minutes) and
  `empty`. Infrastructure errors (connection, 5xx, 429, timeout) are retried at most twice.
- **Pooling** is by sums over sections and runs; cluster bootstrap B = 10,000, seed 0; Wilson
  beside it; claims at the Bonferroni level of section 8.3.

## 3. Scorer readiness check (registration 7.1 b), done on gold only

Seed: first 16 hex digits of SHA-256 of "a11y-stem held-out scorer sample 2026-10-13" =
6664324825138640087; `random.Random(seed).sample(...)`, 8 equations per book in gold order
(`scorer-readiness/draw.py`, `sample.json`). Known-correct LaTeX was written by hand from the
gold MathML by the integrator (`make_pred.py`, `pred.jsonl`), not by any system under test.
**It has not been checked by the author against the rendered gold**, which the registration
asks for; read the result with that in mind. Scored with `readablestem eval equations --pred`
(`report.json`):

**exact_lenient 28 of 40 = 0.700 (Wilson 95% 0.546 to 0.819)**; by book: College Physics 6/8,
Chemistry 4/8, Precalculus 5/8, Introductory Statistics 8/8, Intermediate Algebra 5/8.

The 12 failures, by gap class (gold only):

- **G3 (new): a sign or a unit inside `<mn>`**: `<mn>−2</mn>`, `<mn>−7</mn>`, `<mn>−3</mn>`,
  `<mn>60°</mn>`, `<mn>43°</mn>` (5 of the 12). LaTeX gives `<mo>-</mo><mn>2</mn>` or a number and
  a degree sign as separate tokens.
- **G1 and the like: quantities with their unit in one `<mtext>`**: `0.002500 mol`, `J` `·` `s`,
  `Joules/ion` (2).
- **G4 (new): a chemistry formula or an ion written with an empty-base superscript or a text
  minus/arrow** (`<msup><mrow/><mtext>−</mtext></msup>`, `⟶`, `<mtext>−</mtext>` inside a
  table) (3).
- **G5 (new): gold markup that LaTeX cannot reproduce**: `<mi fontstyle="italic">` wrapping
  several symbols (`v+u`) (2).

A perfect transcriber is therefore scored 0.70 on this sample, not 1.0, and every system pays
the same price. The scorer is not changed for this run (registration 7.1).

## 4. What was frozen, and where

| Item | Value |
|---|---|
| Registration | `PREREGISTRATION.md`, sha256 `8127b143cdbf278f9a14650e086c70b4af7063d789b16ffdfc266399e5d4c5d9` |
| Held-out gold manifest | sha256 `9be8c5650fe492d0c5312199b3040aa6ea741595d5ba97df4b68e816455c9aa3` |
| Code (branch `feat/public-proof` of `ReadableSTEM`) | commit `501b5a2865fa1e5fda1c45006ffb51287f0bf7a6`; pipeline 0.4.0, equation prompt `equation-df0e12f1`, figure prompt `figure-ac51f42d`, judge `judge_figure-137e2607` as at registration (checked in the run's manifest) |
| Differences from `293160e` in prompts, rules, thresholds, models, scorers | none (`git diff 293160e..501b5a2 -- src/readablestem/models src/readablestem/pipeline src/readablestem/config.py src/readablestem/core src/readablestem/eval/pipeline_eval.py src/readablestem/eval/equations.py src/readablestem/eval/judge.py` shows comment edits in `pipeline/page_type.py` only); the rest of the diff is the proof harness, the held-out selector, the invented-content scorer, `eval/reproduce.py` and public-readiness text |
| Invented-content code | fingerprint `b2f7596bdf9acae5b19e2ce3d284f7076f73c1a9f9df8896b79755f80a08f7e0` |
| Parser plan | `PARSERS-PLAN.md`, sha256 `3ab0e878244d1b90f84e4128a1a27350a6fd00c7923beadaad8bbdf710d84ecf` (frozen by this addendum, with D1 to D4) |
| Parser environments | `proof/parsers/uv.lock` `c42dc21f...`, `arms/docling/uv.lock` `8edbca5e...`, `arms/mineru/uv.lock` `9fabeeb6...`, `arms/pymupdf4llm/uv.lock` `c637d1a4...` |
| Chatbot prompts | `src/readablestem/proof/chat_prompts.json` sha256 `9b7a20b88889c2401b5c76c2ca41e95419e29107ea57ce7aeb61ad0c80c87d54` (Purdue sentence verbatim; the research's `expert` prompt) |
| Dependencies | `uv.lock` sha256 `10c8a2c8144be83767d510aa2d17144ba2977ca66444e533a6621701725ac66f` |

## 5. Not done before the run (so the reader knows)

The author's sign-off of the retired editions is recorded in
`DECISION-retired-editions-2026-10-13.md`. Not done: the author's check of the 40 hand-written
equations (section 3), the audit labels, the figure labels, the answer to plan section 11. The
run proceeds on the integrator's reading of the brief; none of these can change a number
already measured, but each can change what the numbers may be called.
