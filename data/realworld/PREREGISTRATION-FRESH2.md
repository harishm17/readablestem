# Registration of the second fresh real-world split: the equation root-cause fixes against the chatbot recipe

Written 2026-10-21, before any system was run on any page of this split. It follows
`PREREGISTRATION.md` (40 papers, development and held-out, used up for tuning) and
`PREREGISTRATION-FRESH.md` (18 papers, used for the page math pass); it is hashed in
`REGISTRATION-fresh2.sha256` (`scripts/realworld_register.py --set fresh2`), and
`scripts/realworld_run.py` refuses a run on this part unless every file listed there still has its
recorded hash. What the set is and how its gold is made: `docs/eval-realworld.md`. What the fixes
are: `docs/eval-equation-rootcause.md`.

## 1. The question

On real LaTeX PDFs that neither the fixes nor the previous runs have seen, does ReadableSTEM after
the fixes of the branch `fix/equation-rootcause` write more equations exactly right than the chatbot
recipe (Haiku and Opus), and does it do so without more equations that are wrong and that nobody is
asked about (silent errors) than the code before the fixes?

## 2. The papers

15 arXiv papers, 70 PDF pages, 1,639 gold equations (84 display), CC BY 4.0, CC BY 3.0 or CC0 (read from
the record), drawn from the pool of `PREREGISTRATION.md` (`pool.json`, 10,527 records) by **the order of
the first fresh split continued**: `order-realworld-fresh-2026-10-21.json` (seed
`realworld-fresh-2026-10-21`; the 129 candidates the first build visited are left out of it) holds the
candidates in their seeded order; the first split visited the first 54 of them (`candidates-fresh.json`), and
this split starts at index 54, `order-realworld-fresh2-2026-10-21.json` (the same list from there on, nothing
re-ordered). The same gates as before (a download that works, a main `.tex`, 8 to 120 pages and a text
layer, at most 10% unconvertible equations, at least 4 usable pages) and the same page rule (4 to 6
usable pages per paper by hash; the arXiv PDF cut to them). A candidate that fails a gate is skipped, never
replaced by choice. 49 candidates were visited: 15 accepted, 17 fewer than 4 usable pages, 6 a source that is a
PDF, 5 a page count outside the range, 4 more than 10% unconvertible equations, 2 no equations
(`candidates-fresh2.json`). No paper was swapped, added or dropped by choice, and the set is not split:
every accepted paper is in it. None of the 183 papers visited before is in it.

Papers (ids; SHA-256 of the sorted ids joined by newlines
`45ad9d9c8cdb23b592c700075985ae0c327dcc6b3f07700b7459cde7c2540d5c`): 1812.06627, 2212.03448, 2401.05368,
2403.17966, 2405.15050, 2405.20573, 2407.04005, 2408.13659, 2412.05104, 2412.09655, 2501.03732, 2503.02921,
2503.08264, 2504.00755, 2504.12173. Styles: 8 tutorial, 7 research. `manifest-fresh2.json` has per paper the
licence, the chosen pages and the SHA-256 of the PDF, the source archive, the cut PDF and the gold. The corpus,
the gold and the PDFs stay outside the repository (`~/.cache/readablestem/realworld`).

What has been looked at: the candidates' abstracts (the pool's style label), the source and PDF of the 49
candidates for the gold extractor (counts only), the manifest's counts. No gold file was read paper by paper and
no system output of any kind exists for these pages. The fixes were tuned on the 25 development papers
only; the other two splits were looked at to see which mechanisms were common (`docs/eval-equation-rootcause.md`)
and are not part of this test.

## 3. Arms

All Claude, one run each, one machine, every model answer recorded.

| Arm | What runs |
|---|---|
| **Chatbot, Haiku** | `chatbot-haiku-purdue`, `proof.chatbot.record_arms` unchanged: `claude-haiku-5-5`, the PDF, a 150 dpi PNG of every page, the Purdue sentence verbatim, no system prompt |
| **Chatbot, Opus** | the same on `claude-opus-5-5` |
| **ReadableSTEM before** (tag `before`) | the repository at `a1b33bd` (main before the fixes, with the chemistry accuracy work merged; `src` tree `33e73a5fe9fc13ce169de1bacbb9f4eb7c17386d`) with this branch's scripts and data and, as the only change to `src`, this branch's `src/readablestem/eval/pipeline_eval.py` (the measurement: it adds the table-cells reading B of section 4 and does not touch what the pipeline produces; its SHA-256 is written to the spend log's note): document task (`scripts/realworld_run.py rs`), provider anthropic, main `claude-opus-5-5` (effort medium), cheap `claude-haiku-5-5` (low), **`FEATURE_TABLES=true` and `FEATURE_NOTATION_MEMORY=true` (the deployed settings, `deploy/deploy.env.example`)**, the page math pass off, the Claude second check off, everything else at its default |
| **ReadableSTEM after** (tag `after`) | the same settings at the head of the branch when the run starts (its commit and `src` tree hash are written to `spend-log.jsonl`); it starts from the `before` run's recorded answers (`--seed-answers-from`), so a request the two runs share is answered once, and both arms see the same answer to the same question |

The `after` arm's code is frozen at the commit named in the log: no `src/` file changes between the
first request of the `before` run and the end of the `after` run. The chatbots run first; their output is not
read until both ReadableSTEM arms are recorded.

## 4. Metrics

Per paper, by the document task's scorer (`eval/pipeline_eval.document_page_item`; the chatbots'
output by `proof/systems.equation_item`), over every gold equation, **with the scorer as it is on
the branch's base; it is not changed.**

- **Primary: `exact_lenient`, end to end** (detection and transcription together; an undetected equation is wrong), pooled over
  gold equations, in two readings of what ReadableSTEM wrote, **both registered and both reported**:
  **A, as registered before** (equations that are elements of their own, `document_page_item`): the maths in a cell of a table the
  table step rebuilt is not one of them. **B, cells counted** (`pipeline_eval.with_table_maths`, stored beside A in every
  ReadableSTEM item): each maths cell of a rebuilt table is one inline equation, as a reader of the output HTML counts it, and as the chatbots' HTML is
  read (every `<math>` in it). Reading A cannot credit ReadableSTEM for a formula it wrote correctly in a table, which the report of the analysis shows is
  a measurement artefact, not a product defect; B is the like-for-like reading. The chatbots are scored the same way under both. **Reading A is the
  headline of the verdict (section 5); B is stated beside it, and the report says in words if the two verdicts differ.**
- Secondary: symbols credited; detection recall and precision; **wrong equations nobody is asked about, per 100 gold**
  (aligned, not `exact_lenient`, not a required decision: the silent errors); the decisions asked per PDF page; cost
  per PDF page; the failures by mechanism (`scripts/rw_gap_table.py`).
- Sensitivity, not registered as a test: the same scores with the two notation artefacts of the scorer undone
  (`scripts/rw_scorer_sensitivity.py`: styled letters, upright words), for every arm; and without a paper whose arm
  was refused, if one is.

## 5. Analysis and what the report may say

95% cluster bootstraps over the 15 papers (`proof/stats.cluster_bootstrap`, 10,000 resamples, seed 0), paired for
differences, and the sign-flip test on per-paper differences.

1. **Registered tests** (primary, reading A and reading B): ReadableSTEM after minus chatbot-Haiku, and minus chatbot-Opus, with a 97.5% interval
   (Bonferroni, k = 2). "Beats" is written only if both differences are positive, both 97.5% intervals exclude 0 and
   both sign-flip p are below 0.025. Otherwise the report says "higher by point estimate" or "no difference this set can detect", or
   "lower".
2. **Silent-error limit.** After must not have more silent errors than before: the point estimate of (after minus before)
   in wrong equations nobody is asked about per 100 gold is at most **+0.5**, the limit of
   `PREREGISTRATION-FRESH.md`. The report gives the interval beside it.
3. **After against before**, primary and secondary, paired, for the size of the effect of the fixes.

A result that fails (1) or (2) is reported as it is; no setting, scorer or gold changes on this split's account.

## 6. Budget

Hard cap of **$18 of live model spend for the whole track** (the development runs, which are recorded, and this
split), kept in `spend-log.jsonl`. Planned caps on this split: Haiku $0.5, Opus $7.0, before $4.0, after $3.0. Estimated
from the previous split: Opus $0.083 a page ($5.8), Haiku $0.0023 ($0.16), ReadableSTEM $0.035 with tables on ($2.5),
the second ReadableSTEM arm about half of that after sharing answers. A run stopped by its cap is reported as partial.
Nothing is run twice to confirm a number; a crash is repeated only for the jobs that did not finish, and named.

## 7. What it will not say

Whether a wrong equation changes the meaning, whether a person would accept the page, anything about scans or Word
files, or about the mathematics inside embedded figures (not in the gold). The results are for 15 papers of one year
of arXiv and one extractor's conventions (`docs/eval-realworld.md`, "Limits"); one run per arm gives no estimate of
run-to-run variation.
