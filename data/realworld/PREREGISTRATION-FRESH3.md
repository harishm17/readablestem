# Registration of the third fresh real-world split: the equation round-2 fixes against the chatbot recipe

Written 2026-10-22, before any system was run on any page of this split. It follows
`PREREGISTRATION.md` (40 papers, development and held-out, used up for tuning),
`PREREGISTRATION-FRESH.md` and `PREREGISTRATION-FRESH2.md` (used); it is hashed in
`REGISTRATION-fresh3.sha256` (`scripts/realworld_register.py --set fresh3`), and
`scripts/realworld_run.py` refuses a run on this part unless every file listed there still has its
recorded hash. What the set is and how its gold is made: `docs/eval-realworld.md`. What the fixes
are: `docs/eval-equation-round2.md`.

## 1. The question

On real LaTeX PDFs that neither the fixes nor any earlier run has seen, does ReadableSTEM after the
fixes of the branch `fix/equations-round2` write more equations exactly right than the chatbot
recipe (Haiku and Opus), both by the registered score and by a notation-neutral score that does not
count `\mathrm`, `\text` and `\operatorname` as different, and does it do so without more wrong
equations that nobody is asked about (silent errors) than ReadableSTEM before the fixes?

## 2. The papers

15 arXiv papers, 73 PDF pages, 1,779 gold equations (142 display), CC BY 4.0 (14) or CC0 (1), read
from the record, drawn from the pool of `PREREGISTRATION.md` (`pool.json`) by **the order of the
second fresh split continued**: `order-realworld-fresh2-2026-10-21.json` (itself the continuation of
`order-realworld-fresh-2026-10-21.json`, seed `realworld-fresh-2026-10-21`) was visited for its first 49
candidates (`candidates-fresh2.json`); this split starts at its index 49
(`order-realworld-fresh3-2026-10-22.json`: the same list from there on, nothing re-ordered). The same gates
as before (a download that works, a main `.tex`, 8 to 120 pages and a text layer, at most 10%
unconvertible equations, at least 4 usable pages) and the same page rule (4 to 6 usable pages per paper by
hash; the arXiv PDF cut to them). A candidate that fails a gate is skipped, never replaced by choice. 72
candidates were visited: 15 accepted, 34 fewer than 4 usable pages, 8 more than 10% unconvertible
equations, 7 a page count outside the range, 7 a source that is a PDF, 1 no equations
(`candidates-fresh3.json`). No paper was swapped, added or dropped by choice, and the set is not split.
None of the 232 papers visited before is in it.

Papers (ids; SHA-256 of the sorted ids joined by newlines
`fb2c91dcf9e3846283b1be662a72fbf0af10d265bee57b8caefe1364ba685225`): 2201.10048, 2303.08046, 2305.00609,
2405.13060, 2406.01324, 2406.03321, 2408.05847, 2501.19367, 2503.00504, 2503.22999, 2504.06207, 2505.13042,
2506.15579, 2506.15876, 2506.20341. Styles: 11 research, 4 tutorial. `manifest-fresh3.json` has per paper the
licence, the chosen pages and the SHA-256 of the PDF, the source archive, the cut PDF and the gold. The gold
is made by the converter as it is on the branch's base (the converter did not change in this round); the corpus,
the gold and the PDFs stay outside the repository (`~/.cache/readablestem/realworld`).

What has been looked at: the candidates' abstracts (the pool's style label), the source and PDF of the 72
candidates for the gold extractor (counts only), the manifest's counts. No gold file was read paper by paper and
no system output of any kind exists for these pages. The fixes were tuned on the 25 development papers
only (`docs/eval-equation-round2.md`).

## 3. Arms

All Claude, one run each, one machine, every model answer recorded.

| Arm | What runs |
|---|---|
| **Chatbot, Haiku** | `chatbot-haiku-purdue`, `proof.chatbot.record_arms` unchanged: `claude-haiku-5-5`, the PDF, a 150 dpi PNG of every page, the Purdue sentence verbatim, no system prompt |
| **Chatbot, Opus** | the same on `claude-opus-5-5` |
| **ReadableSTEM before** (tag `before`) | `src` of main `8555f6c` (`src` tree `230aa3ccb55373fecdc42933dc8c6c98e61fb114`, extracted with `git archive` and put first on `PYTHONPATH`) driven by this branch's scripts (`scripts/realworld_run.py rs`): document task, provider anthropic, main `claude-opus-5-5` (effort medium), cheap `claude-haiku-5-5` (low), **`FEATURE_TABLES=true` and `FEATURE_NOTATION_MEMORY=true`**, the page math pass off, the Claude second check off, everything else at its default |
| **ReadableSTEM after** (tag `after`) | the same settings with `src` at the head of the branch when the run starts (its commit and `src` tree hash are written to `spend-log.jsonl`); it starts from the `before` run's recorded answers (`--seed-answers-from`), so a request the two runs share is answered once and both arms see the same answer to the same question |

`FEATURE_TABLES=true` in both ReadableSTEM arms is the production setting (with `MAX_USD_PER_PAGE=2.20` and
`MAX_USD_PER_JOB=4.70`, which the pipeline's caps do not reach on these pages). The equation prompt is unchanged
by the round (`equation-68ebbbf9`), so a request the two arms share replays. The `after` arm's code is frozen at the
commit named in the log: no `src/` file changes between the first request of the `before` run and the end of the `after`
run. The chatbots run first; their output is not read until both ReadableSTEM arms are recorded.

## 4. Metrics

Per paper, by the document task's scorer (`eval/pipeline_eval.document_page_item`; the chatbots' output by
`proof/systems.equation_item`), over every gold equation, with the aligner and `exact_lenient` as they are on the
branch's base; they are not changed.

- **Primary: `exact_lenient`, end to end**, pooled over gold equations, **reading A** (equations that are elements of
  their own; the maths of a table cell a rebuilt table holds is not one), as in `PREREGISTRATION-FRESH2.md`.
  **Reading B** (each formula a rebuilt table's cells have in the output HTML counts as one inline equation,
  `pipeline_eval.with_table_maths`, stored beside A in every ReadableSTEM item) is reported beside it. In B a cell
  of words with a formula in it counts the formula, as the `after` HTML writes it; in `before` the cell is one formula,
  as its HTML has it. The chatbots are scored the same way under both.
- **Notation-neutral secondary** (`scripts/rw_notation_neutral.py`, hashed with this registration, applied by
  `scripts/realworld_report.py --neutral`): an aligned equation is exact when it is exact under the primary, **or** when it is
  exact once `\mathrm{w}`, `\textrm{w}`, `\mbox{w}`, `\textnormal{w}`, `\operatorname{w}`, `\operatorname*{w}` and
  `{\rm w}` (no brace inside `w`) are written `\text{w}` in the prediction's LaTeX and in the gold's LaTeX. The same rule for
  every arm; nothing else is folded; detection, spans and silent errors are those of the primary. Read the same way
  under A and under B.
- Secondary otherwise: symbols credited; detection recall and precision; **wrong equations nobody is asked about, per 100
  gold** (aligned, not `exact_lenient`, not a required decision: the silent errors); the decisions asked per PDF page; cost per PDF
  page; the failures by mechanism (`scripts/rw_gap_table.py`).
- Not registered as tests: the misses (gold equations with no aligned prediction) per arm, reported with the table.

## 5. Analysis and what the report may say

95% cluster bootstraps over the 15 papers (`proof/stats.cluster_bootstrap`, 10,000 resamples, seed 0), paired for
differences, and the sign-flip test on per-paper differences.

1. **The claim.** ReadableSTEM after beats each chatbot, on the primary (reading A) and on the notation-neutral secondary
   (reading A). Four paired differences (after minus Haiku and after minus Opus, each under both scores) with a 97.5% interval
   (Bonferroni, k = 2 per score). "Beats" is written for a pair only if the difference is positive, the 97.5% interval excludes 0 and
   the sign-flip p is below 0.025; the claim is met only if all four pairs beat. Otherwise the report says for each pair "higher by
   point estimate", "no difference this set can detect", or "lower", and that the claim is not met. Reading B is stated beside
   it and the report says in words if its verdict differs.
2. **Silent-error limit.** After must not have more silent errors than before: the point estimate of (after minus before) in
   wrong equations nobody is asked about per 100 gold, reading A, is at most **+0.5**. The report gives the interval beside it.
3. **After against before**, primary and neutral, paired, for the size of the effect of the fixes.

A result that fails (1) or (2) is reported as it is; no setting, scorer or gold changes on this split's account.

## 6. Budget

Hard cap of **$16 of live model spend in total** for the round (the development re-recordings, $0.30 spent before this
registration, and this split), kept in `spend-log.jsonl`. Caps on this split: Haiku $0.5, Opus $7.5, before $4.5, after $3.0. Estimated from the
previous split: Opus $0.086 a page ($6.3), Haiku $0.0023 ($0.17), ReadableSTEM $0.039 a page ($2.8), the second ReadableSTEM arm a
fraction of that after sharing answers. A run stopped by its cap is reported as partial and the cap is not raised without asking. Nothing is
run twice to confirm a number; a crash is repeated only for the jobs that did not finish, and named.

## 7. What it will not say

Whether a wrong equation changes the meaning, whether a person would accept the page, anything about scans or Word files, or about the
mathematics inside embedded figures (not in the gold). The results are for 15 papers of one year of arXiv and one extractor's conventions
(`docs/eval-realworld.md`, "Limits"); one run per arm gives no estimate of run-to-run variation.
