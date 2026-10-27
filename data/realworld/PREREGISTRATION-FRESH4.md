# Registration of the fourth fresh real-world split: inline equations read with their line

Written 2026-10-26, before any system was run on any page of this split. It follows
`PREREGISTRATION-FRESH3.md` (used); it is hashed in `REGISTRATION-fresh4.sha256`
(`scripts/realworld_register.py --set fresh4`), and `scripts/realworld_run.py` refuses a run on
this part unless every file listed there still has its recorded hash. What the set is and how its
gold is made: `docs/eval-realworld.md`. What is tested: `docs/eval-equation-round3.md`.

## 1. The question

On real LaTeX PDFs that no earlier run has seen, does reading each inline equation with its line
(`EQUATION_LINE_CONTEXT`: the span's crop plus the whole line with the span boxed, prompt
`equation_line.md`) write more equations exactly right than ReadableSTEM without it, without
more wrong equations that nobody is asked about; and where does ReadableSTEM with it stand
against the chatbot recipe (Haiku and Opus)?

Why this change: on fresh3 the largest mechanism by which ReadableSTEM lost to Opus was the span
of an inline equation cut at the wrong place by the text layer (34 equations against 6), and the
page math pass, which places spans by quoting the same text layer, did not move it (+0.2 points).
On the 25 development papers the line context moved the score by +2.0 points [+0.5, +3.7]
(table-cell maths counted), with the silent errors unchanged (-0.04 per 100 gold).

## 2. The papers

15 arXiv papers, 71 PDF pages, 1,600 gold equations (87 display), CC BY 4.0 (14) or CC0 (1), all
research style, drawn from the pool of `PREREGISTRATION.md` by **the order of the third fresh split
continued**: `order-realworld-fresh3-2026-10-22.json` was visited for its first 72 candidates
(`candidates-fresh3.json`); this split starts at its index 72 (`order-realworld-fresh4-2026-10-26.json`:
the same list from there on, nothing re-ordered). The same gates and page rule as before. 37 candidates
were visited: 15 accepted, 15 fewer than 4 usable pages, 2 more than 10% unconvertible equations,
2 no equations, 1 no main `.tex`, 1 download too large, 1 a source that is a PDF
(`candidates-fresh4.json`). No paper was swapped, added or dropped by choice.

Papers (ids; SHA-256 of the sorted ids joined by newlines
`397cf65fcf897af2b34ed94c7ff4ffec93d217dde05fae7ec16454bbf92f3c19`): 2201.12936, 2309.04589,
2309.13554, 2312.01530, 2402.06415, 2406.17446, 2407.06788, 2409.05598, 2409.12662, 2410.22388,
2501.15793, 2502.21009, 2503.18982, 2504.10283, 2505.23940. `manifest-fresh4.json` has per paper the
licence, the chosen pages and the hashes of the PDF, the source, the cut PDF and the gold.

What has been looked at: the build's log and the manifest's counts. No gold file was read and no
system output of any kind exists for these pages. The line context was built and tuned on the 25
development papers only.

## 3. Arms

All Claude, one run each, one machine, every model answer recorded.

| Arm | What runs |
|---|---|
| **Chatbot, Haiku** | `chatbot-haiku-purdue`, `proof.chatbot.record_arms` unchanged: `claude-haiku-5-5`, the PDF, a 150 dpi PNG of every page, the Purdue sentence verbatim, no system prompt |
| **Chatbot, Opus** | the same on `claude-opus-5-5` |
| **ReadableSTEM before** (tag `before`) | the code at the commit that holds this registration, `scripts/realworld_run.py rs`: document task, provider anthropic, main `claude-opus-5-5` (effort medium), cheap `claude-haiku-5-5` (low), `FEATURE_TABLES=true` and `FEATURE_NOTATION_MEMORY=true` (production), the page math pass off, the Claude second check off, `EQUATION_LINE_CONTEXT` off |
| **ReadableSTEM after** (tag `after`) | the same code and settings with `EQUATION_LINE_CONTEXT=true`; it starts from the `before` run's recorded answers, so a request the two share is answered once |

No `src/` file changes between the first request of the first arm and the end of the last. The chatbots
run first; their output is not read until both ReadableSTEM arms are recorded.

## 4. Metrics

Per paper, by the document task's scorer (`eval/pipeline_eval.document_page_item`; the chatbots' output
by `proof/systems.equation_item`), over every gold equation, aligner and `exact_lenient` unchanged.

- **Primary: `exact_lenient`, end to end, reading B** (each formula a rebuilt table's cells hold in the
  output HTML counts as one inline equation, `pipeline_eval.with_table_maths`). Reading B is primary
  this time because it is the one that reads every arm's output the same way: the chatbots' scorer
  counts the formulas inside their HTML tables, and reading A did not count ReadableSTEM's (on fresh3
  that difference alone was 3.9 of the 5.8 points between ReadableSTEM and Opus,
  `docs/eval-equation-round2.md`). Reading A is reported beside it.
- **Notation-neutral secondary** (`scripts/rw_notation_neutral.py`, unchanged and hashed here).
- Secondary otherwise: symbols credited; detection recall and precision; **wrong equations nobody is
  asked about, per 100 gold** (the silent errors); decisions asked per page; cost per page; the
  failures by mechanism (`scripts/rw_gap_table.py`), and the span bucket in particular.

## 5. Analysis and what the report may say

95% cluster bootstraps over the 15 papers (`proof/stats.cluster_bootstrap`, 10,000 resamples, seed 0),
paired for differences, and the sign-flip test on per-paper differences.

1. **The change (decides the default).** After minus before, primary: the setting is turned on in
   production only if the difference is positive and its 95% interval excludes 0, **and** the
   silent-error rule (2) holds. Otherwise it stays off and the report says so.
2. **Silent-error limit.** The point estimate of (after minus before) in wrong equations nobody is asked
   about per 100 gold, reading B, is at most **+0.5**.
3. **Against the chatbots** (reported, not a gate for the setting). After minus Haiku and after minus
   Opus on the primary, with 97.5% intervals (k = 2). "Beats" is written for a pair only if the difference
   is positive, its 97.5% interval excludes 0 and the sign-flip p is below 0.025; otherwise "higher by
   point estimate", "no difference this set can detect" or "lower". The same for the silent errors, where
   fewer is better.

A result that fails is reported as it is; no setting, scorer or gold changes on this split's account.

## 6. Budget

Caps on this split: Haiku $0.5, Opus $7.5, before $3.5, after $3.0 (about $11 expected: Opus $0.088 a page,
ReadableSTEM $0.04 a page, the `after` arm only its new inline requests). Kept in `spend-log.jsonl`. A run
stopped by its cap is reported as partial and the cap is not raised without asking. Nothing is run twice
to confirm a number; a crash is repeated only for the jobs that did not finish, and named.

## 7. What it will not say

Whether a wrong equation changes the meaning, whether a person would accept the page, anything about
scans, Word files or textbooks (all 15 papers are research articles), or about maths inside embedded
figures. One run per arm gives no estimate of run-to-run variation; 15 papers give wide intervals.
