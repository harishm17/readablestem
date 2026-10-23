# Registration of the fresh real-world split: page math pass on or off

Written 2026-10-21, before any system was run on any page of this split. It follows
`PREREGISTRATION.md` (the first real-world split, 40 papers, now used up for tuning) and is hashed in
`REGISTRATION-fresh.sha256` (`scripts/realworld_register.py --set fresh`); `scripts/realworld_run.py`
refuses a run on this part unless every file listed there still has its recorded hash.

## 1. The question

Does ReadableSTEM with the page math pass on (ADR 0006), with the settings the development runs
pointed to, do at least as well as with it off on real LaTeX PDFs, without more wrong equations that
nobody is asked about? The answer decides whether those settings become the production defaults
(section 7).

## 2. The papers

18 arXiv papers, 86 PDF pages, 1,836 gold equations (142 display), all CC BY 4.0, drawn from the same pool
(`pool.json`, 10,527 records with an accepted licence) by a new seed, `realworld-fresh-2026-10-21`, with
the same rules as the first split (candidate order by SHA-256 of seed, set and id; sets in turn; tutorial and
research alternating; the same gates: download, main `.tex`, 8 to 120 pages and a text layer, at most 10%
unconvertible equations, at least 4 usable pages; 4 to 6 pages per paper by hash; the arXiv PDF cut to those pages;
gold from the author's LaTeX by the same extractor, `docs/eval-realworld.md`). Left out of the order: the 129 candidates the first
build visited (the 40 used and the 89 that failed a gate, which would fail again): `excluded` 129 in
`order-realworld-fresh-2026-10-21.json`. 54 candidates were visited: 18 accepted, 19 fewer than 4 usable pages, 6
unconvertible equations, 5 a source that is a PDF, 4 a page count outside the range, 2 no equations
(`candidates-fresh.json`). No papers were swapped, added or dropped by choice.

Papers (ids; SHA-256 of the sorted ids joined by newlines `aec6707bc926c15ddcea2747d0e8059815a32739198022766c80aa063e05b867`):
2210.13562, 2212.05955, 2303.02529, 2307.16839, 2309.01229, 2402.10836, 2406.12157, 2406.19702, 2407.08602,
2407.10832, 2410.04138, 2411.04352, 2411.07613, 2501.00140, 2502.04553, 2502.08443, 2504.19306, 2506.01997.
`manifest-fresh.json` has per paper the licence, the chosen pages and the SHA-256 of the PDF, the source archive, the cut PDF and
the gold. Corpus, gold and PDFs stay outside the repository (`~/.cache/readablestem/realworld`).
`uv run python scripts/realworld_build.py verify` rebuilds all three manifests' gold from the cached downloads and finds the same hashes
(run after the allowlist change below).

What has been looked at: the candidates' abstracts, the source and PDF of the 54 candidates (gold extraction, counts only),
the manifest's counts. No gold file was read paper by paper and no system output exists for these pages.

## 3. Arms

Both arms are ReadableSTEM at the code of the registration commit's parent (branch `feat/realworld-eval`), document task through
`scripts/realworld_run.py rs`, provider anthropic, `claude-opus-5-5` (effort medium) main and `claude-haiku-5-5` (low) cheap model, the Claude
second check off, every other setting at its default, **and the converter allowlist change** (`arg`, `substack`, `succsim`, `colon`, `coloneqq`,
`triangleq`, `trianglelefteq`, `gtrapprox`, `intercal`, `rightsquigarrow`, `leadsto` accepted by `pipeline/validate.py`; commit "Converter allowlist ...";
replayed on the first real-world development set with no regression: `docs/eval-realworld.md`). They differ in:

| Arm | Settings |
|---|---|
| **off** (tag `fresh-off`) | `FEATURE_PAGE_MATH=false` |
| **on** (tag `fresh-on`) | `FEATURE_PAGE_MATH=true`, `PAGE_MATH_MAX_OUTPUT_TOKENS=12288`, `PAGE_MATH_AGREEMENT_LIFT=false` (the page pass with Haiku at effort medium, the reconciliation policy as committed) |

Each arm is run **once**; the model answers are recorded. The `on` run starts from the `off` run's answers (`--seed-answers-from`), so a
request the two runs share (the same crop, the same instructions) is answered once and replays in the other; every other request is asked and
recorded. Both arms therefore see the same answer to the same question, which makes the paired comparison about the pass and not about sampling
noise; `cost per page` of each arm is the cost of all its requests at list price, shared ones included. Code identity at registration (git tree hashes):
see the commit; `src/readablestem/pipeline`, `models`, `core` and `config.py` change in no way between registration and the runs.

## 4. Metrics

Per paper, by the document task's scorers (`eval/pipeline_eval.document_page_item`), over every gold equation:
- **Primary: `exact_lenient`**, end to end, on minus off, paired by paper.
- Secondary: symbols credited (`eval/equations_symbols.py`); **wrong equations nobody is asked about, per 100 gold** (aligned equations that are not
  `exact_lenient` and not a required decision); **flag recall** (symbol-wrong aligned equations that were a required decision) and flag precision;
  **required decisions per PDF page** (all kinds); **cost per PDF page**; detection recall and precision.
- Descriptive: the buckets and the strata of `docs/eval-realworld.md`, pages where the page pass answered nothing, the page pass's cost.

## 5. Analysis

95% cluster bootstraps over the 18 papers (10,000 resamples, seed 0, `proof/stats`), paired for differences, and the sign-flip test
on per-paper differences. The decision of section 7 uses the **point estimates** of the paired differences, as set by the person who owns the product; the intervals and p are
reported beside them and the report says in words what they do and do not show. Single run per arm, 18 papers: the intervals are approximate and a
little narrow, and an effect of two points or less will not be separated from none.

## 6. Budget

Hard cap **$8** of live model spend for this split (all arms; the first split's $15.39 is separate). Estimated from the first split's $0.0235 a page: off about $2.0, on about $1.2 beyond what it shares; caps
$3.5 and $3.5 per start. Spend is logged in `spend-log.jsonl`. A run stopped by its cap or by a crash is repeated only for the jobs that did not finish.

## 7. The decision rule

If, on this split, **(a) the primary paired difference (on minus off) is at least 0** and **(b) the difference in wrong equations nobody is asked about per
100 gold does not rise by more than 0.5**, then the production defaults become `FEATURE_PAGE_MATH=on`, `PAGE_MATH_MAX_OUTPUT_TOKENS=12288` and
`PAGE_MATH_AGREEMENT_LIFT=off` (settings, docs, ADR 0006 status, README copy that describes the pipeline). Otherwise the defaults stay as they are (off) and the report says why.

## 8. What it will not say

Whether either arm beats a chatbot (none is run); anything about tables and figures beyond the strata the report prints; whether a person would accept
the pages; results beyond 18 papers of one year of arXiv and one extractor's conventions (`docs/eval-realworld.md`, "Limits").
