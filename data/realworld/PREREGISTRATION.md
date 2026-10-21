# Registration of the real-world held-out split (arXiv, author LaTeX)

Written 2026-10-21, before any system was run on any part of this set, development or held-out. It
follows the style of `proof/PREREGISTRATION.md` and is hashed in `REGISTRATION.sha256` (made by
`scripts/realworld_register.py`); `scripts/realworld_run.py` refuses a held-out run if a file
listed there has changed. What the set is and how its gold is made: `docs/eval-realworld.md`.

## 1. What is registered

A held-out split of **15 arXiv papers** (71 PDF pages, 1,826 gold equations), the rule that made it,
the arms that will be run on it, the metrics and the analysis, and the budget rules. Nothing in it
may change after a system has seen a held-out page, except by an addendum written before the
affected number is read.

What has been looked at before this text: the candidates' abstracts (to rank them by style), the
source and PDF of every candidate the build visited (129, to debug the gold extractor; the
extractor was changed twice during the build, to expand author macros the converter does not know
and to place pictures and algorithms like floats), the gold lists and page images of three papers
of the development part. No system output of any kind exists. The held-out gold was never read
paper by paper; its counts per paper are in `manifest-heldout.json`.

## 2. The corpus and the draw

| Item | Value |
|---|---|
| Source of records | arXiv OAI-PMH `ListRecords`, format `arXiv`, the sets and datestamp windows of `src/readablestem/gold/arxiv_corpus.py` (15 sets: math.PR, stat.TH, stat.ME, stat.ML, cs.LG, cs.DS, econ.EM, q-bio.QM, physics.class-ph, ed-ph, flu-dyn, data-an, cond-mat.stat-mech, math.NA, math.HO) |
| Licences accepted | CC BY 3.0, CC BY 4.0, CC0 1.0 (read from the record). Every other licence is skipped. |
| Pool | 10,527 records with an accepted licence, 10,165 after the page range (8 to 120 pages where the comments give one); `pool.json` (SHA-256 in `REGISTRATION.sha256`) |
| Style | `tutorial` when the title or comments say lecture notes, tutorial, introduction to, primer, review of ...; or the abstract narrowly does (`arxiv_corpus.TUTORIAL_*`). A heuristic, not a judgement: some "tutorial" papers are research papers. |
| Seed | `realworld-2026-10-21`. Candidate order: inside each (style, set) stratum by SHA-256 of seed, set and id; sets in turn; styles alternating T, R. `order-realworld-2026-10-21.json` |
| Gates, in the order of the candidate list, no replacement by choice | download works; the source has a main `.tex`; the PDF has 8 to 120 pages and a text layer; at most 10% of the source's equations are unconvertible; at least 4 usable pages (below) |
| Papers | the first 40 candidates that passed (129 visited: 40 accepted, 59 fewer than 4 usable pages, 12 unconvertible, 9 no source files, 6 page count, 2 source is a PDF, 1 http 404; `candidates.json`) |
| Pages | a page is usable when every equation printed on it is known: each is placed on that page by text anchors, none may be on it by interval, at least 90% of the page's words are in the source's prose, at least 5 equations, all convertible, not page 1. 4 to 6 usable pages per paper, drawn by SHA-256 of seed, id and page. |
| Split | 15 held-out, 25 development: from each style in proportion, the papers with the smallest SHA-256 of seed, "split" and id. |
| Document | the arXiv-provided PDF cut to the chosen pages (a new PDF, the chosen pages in order); source only for gold |

Held-out papers (ids; SHA-256 of the sorted ids joined by newlines
`de2d68b44a27216646b1366735cd7da824103f902a10b9fc4300ceb28bcb4429`): 2301.06637, 2301.08958,
2310.04390, 2311.10204, 2403.05373, 2404.01071, 2405.01155, 2405.03801, 2407.03929, 2407.04881,
2410.10911, 2411.03234, 2503.17379, 2504.05959, 2504.14081. All CC BY 4.0. Development papers: 25, ids hash
`05b71b0e759d54af7590b09badfdeef7bbd56da966436774d877befe48f5f005`. `manifest-heldout.json` records, per paper,
the licence, the chosen pages and the SHA-256 of the full PDF, the source archive, the cut PDF and the
gold file. The corpus, the gold and the PDFs are kept outside the repository
(`~/.cache/readablestem/realworld`); only scripts, manifests and results are committed.

## 3. Gold

Equations are those of the author's source: display (`equation`, `align`, `gather`, `multline`,
`eqnarray`, `flalign`, `alignat`, `displaymath`, `\[ \]`, `$$ $$`) and inline (`$ $`, `\( \)`,
`math`), after expanding the author's macros, converted to MathML by the pipeline's own path
(`equation_step.clean_latex`, `validate.safe_latex_to_mathml`). An `align` with several numbered rows
is one gold equation per numbered row (unnumbered rows join the next numbered one), a starred `align`
is one equation, `gather` is one per row, `multline` one. Known limits (`docs/eval-realworld.md`):
pages with an equation the converter cannot convert, or that text anchors cannot place, are not
used, so the pages are easier than average; mathematics inside figures (axis labels in an embedded
figure) is not in the gold, and a pipeline that reads it is counted as writing an equation with no
gold partner. Corrections of the answer key found on development pages are allowed before the
held-out run, by an addendum that names them and re-hashes the manifests; the held-out run never
uses system output on held-out pages to correct gold.

## 4. Arms

All Claude, on one machine, one run each (the OpenStax proof made two; the budget does not).

| Arm | What runs |
|---|---|
| ReadableSTEM | The repository at the registration commit's parent (branch `feat/page-math` at `c190b3e`): document task (`scripts/realworld_run.py rs`, through `eval_cli._run_pipeline`), provider anthropic, main `claude-opus-5-5` (effort medium), cheap `claude-haiku-5-5` (low), `FEATURE_PAGE_MATH` off, all other flags at their defaults, the Claude second check off (`CLAUDE_SECOND_CHECK=off`; it is shadow-only and changes no output). Code identity (git tree hashes at registration): `src/readablestem/pipeline` 8efc77da, `models` e9e7481d, `core` bae2451d, `config.py` f8ee287c. |
| Chatbot, Haiku | `chatbot-haiku-purdue`: `claude-haiku-5-5`, the PDF as a document block, a 150 dpi PNG of every page, the Purdue sentence verbatim as the only text (`proof/chat_prompts.json`, SHA-256 of the sentence `2e507512...`): "Convert to accessible HTML compliant with WCAG 2.2 AA with complete MathJax headers and remove [cite]." No system prompt, no tools, default effort, 64,000 output tokens, streamed. One run. |
| Chatbot, Opus | `chatbot-opus-purdue`: the same on `claude-opus-5-5`. One run. |

The registered expert-prompt arm of the proof and the parsers are not run here.

## 5. Metrics

Scored by the document task's code (`eval/pipeline_eval.document_page_item`; the chatbots'
output is read by `proof/systems.equation_item`, as in the proof), per paper, end to end, over
every gold equation.

- **Primary**: `exact_lenient`, exactly right equations / gold equations (detection and
  transcription together; an undetected equation is wrong).
- **Secondary**: symbols credited (`eval/equations_symbols.py`; split and merged gold credited as
  in `docs/eval-secondary-metrics.md`) / gold equations.
- **Detection**: recall (gold equations with an aligned prediction / gold) and precision
  (aligned predictions / predictions).
- **Unflagged wrong per 100 gold**: aligned equations that are not `exact_lenient` and are not
  a required decision, per 100 gold equations (a chatbot asks nobody, so all its wrong ones count).
- **Unsupported / invented**: predictions with no gold partner whose letters, digits and Greek
  letters the PDF's own text layer does not support (`text_layer_supported` false, not a piece of a
  gold equation), per prediction.
- **Cost per PDF page**: the arm's recorded model spend (list price) / pages of the PDF; ReadableSTEM's
  includes everything it called.
- Descriptive, not tested: the buckets of `docs/eval-page-math.md` (correct, scorer, span,
  recognition, no proposal, undetected), the score by display or inline, by single symbol or longer,
  by style of paper, by alignment environment, required decisions per page, failure examples.

## 6. Analysis

Intervals are 95% cluster bootstraps over papers (`proof/stats.cluster_bootstrap`: 10,000
resamples, `default_rng(0)`, pooled sums). The two registered tests are the paired differences
**ReadableSTEM minus chatbot-Opus** and **ReadableSTEM minus chatbot-Haiku** on the primary metric,
on the papers both arms were run on, with a 97.5% interval (Bonferroni, k = 2) and the sign-flip
test on per-paper differences (exact). The report writes "better" or "worse" only when the 97.5%
interval excludes 0 and the sign-flip p is below 0.025; otherwise "higher by point estimate" or
"no difference this set can detect". Every other number is descriptive, with its 95% interval. With
15 papers the intervals are approximate and a little narrow, and one run per arm gives no estimate
of run-to-run variation (on the OpenStax set it was 0.007 on the primary score).

## 7. Budget and rules of the run

- **Hard cap: $20 of live model spend for the whole work** (development and held-out), kept in
  `spend-log.jsonl` (the estimate each run was given, its cap, what it cost from its recorded
  answers). Planned caps: development ReadableSTEM $4.0, Haiku chatbot $0.8, Opus chatbot $4.0 (on
  the first N development papers of the registered order, `chat_order`, N chosen from a measured
  cost per page); held-out ReadableSTEM $3.0, Haiku chatbot $0.6, Opus chatbot $5.5.
- **Opus on the held-out part**: all 15 papers when its measured cost per page (from development)
  puts the arm under its cap; otherwise the first N papers of `chat_order(heldout)` that fit, and the
  paired comparison is on those papers (ReadableSTEM and Haiku always run on all 15).
- Every model answer is recorded (`--answers`, the chatbots' files) and replays for free; no run is
  repeated to confirm a number; a run stopped by its cap is reported as partial, not repeated.
- The held-out part is run **once per arm** (`scripts/realworld_run.py` writes a marker and refuses
  a second live run); nothing on it is read before the three arms are recorded, and no setting,
  prompt, threshold, scorer or gold is changed on its account. A crash or infrastructure failure is
  repeated only for the jobs that did not finish, and named in the report.
- Development runs come first and are for reading and for sizing; nothing in this registration
  depends on their results except the Opus sizing above.

## 8. What it will not say

Whether a wrong equation changes the meaning, whether a person would accept the page, how the
systems do on scans, on Word files, or on pages whose equations the gold cannot place; the
results are for 15 papers of 10 fields in one year of arXiv and one extractor's convention for
alignments.
