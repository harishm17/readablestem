# Pre-registration: held-out OpenStax evaluation of a11y-stem

Written 2026-10-13, before any system under test (a11y-stem, the chatbot recipe, the open
parsers) has processed any of the sections below. The author's sign-off items are in
section 12.

This file is read-only once written. Its SHA-256 is in `PREREG.sha256`, with the hashes of
the selection files and of the held-out gold manifest. To change anything before the run,
write `PREREGISTRATION-v2.md` saying what changed and why, and register it the same way;
this file stays as it is. Changes after the run starts are deviations and are published
as such.

## 1. What this run answers

Reviewers asked for four things:

1. a11y-stem's results on OpenStax sections it was never developed on;
2. an invented-content rate: how much of an output says something the PDF does not;
3. a comparison with open PDF parsers (and with the chatbot recipe instructors copy);
4. one command that reproduces every number.

a11y-stem assists a person who makes course material accessible. Nothing here measures
conformance with WCAG or any other standard, and no number from this run may be reported
as "compliant", "verified" or "accessible guaranteed".

## 2. What was used in development (none of it is held out)

Found by `selection/scan_used.py` (run 2026-10-13 over `a11y-stem`, `a11y-stem-int`, every
worktree under `a11y-stem/.claude/worktrees`, `a11y-research` including its tarballs,
`~/.cache/a11y-fixer`, `~/.cache/final-verify-pt` and `/tmp`): cached OpenStax archive
pages, section slugs in text files and file names, and the contents of the research
tarballs; plus a grep for `openstax.org/books/...` URLs in `a11y-research`.

| Where | OpenStax sections |
|---|---|
| `a11y-stem/data/gold` (equations, figures, headings gold; demo; chatbot baseline; `tests/`) | Physics 2.1, 2.2, 2.3 |
| `a11y-stem/data/gold/tables` and table-bench (`pdf-eval-2026-10-09.tgz`) | Physics 1.3, 5.4, 6.3, 11.3, 14.1, 16.3, 22.2, 23.1, 23.2; Statistics 1.3, 2.2, 3.4, 4.2. Table-bench also scanned every page of both books for tables (`table-bench/data/scan.jsonl`) |
| `a11y-stem/.cache/openstax` (fetched for runs, demo look-ups and tests) | Physics 1.3, 2.1-2.4, 3.1, 3.2, 4.1-4.4, 5.1-5.5; College Physics 1.1 |
| `a11y-stem-int/.cache` and the tables-eval worktree | every page of Physics and Statistics (whole-book caches); the "42 cached OpenStax renders" of the layout regression sweeps (by their count, the 13 table pages in three print styles plus Physics 2.1-2.3; all inside the two excluded books either way) |
| `a11y-research/niche/holdout-2026-10-13/fresh` | Physics 3.1, 3.2, 4.2, 4.3, 4.4, 5.2, 5.3, 5.4, 5.5 |
| `a11y-research/niche/hybrid-heldout` | College Physics 10.2, 18.3; Chemistry 3.3, 12.4; Precalculus 8.8, 12.4; Algebra and Trigonometry 13.6; Introductory Statistics 7.2; Statistics 11.6 |
| `a11y-research/chatbot-baseline`, `niche/chatbot-runs` (incl. the 27-page "long" file) | Physics 2.1, 2.2, 2.3 |
| `/tmp` gold-fetch test folders and an old cache copy | College Physics 1.1; Physics 1.1, 1.2 |

Synthetic slugs in tests (`1-1-first-section`, `9-1-rolling-balls`, ...) are not OpenStax
sections.

Excluded as a result:

- **Every section of Physics and Statistics** (the two live CC BY books): both were
  cached and scanned whole, and their sections fill the development gold.
- **The 38 sections listed above** by name.
- **Near-copies of them in other books.** The high-school Physics book was adapted from
  College Physics, and Statistics from Introductory Statistics, so a section can repeat a
  development section's text. A candidate is excluded when 5% or more of its word 8-grams
  (visible text, MathML removed) occur in the 38 development sections' text. The highest
  value in the three candidate books with no shared lineage (Chemistry, Precalculus,
  Intermediate Algebra) is 0.021; College Physics sections mirroring the high-school
  Physics development sections go up to 0.43 (36 College Physics sections excluded),
  Introductory Statistics' copies of the Statistics gold pages reach 0.52-0.94 (7
  excluded), and Precalculus 11.6 (the same text as Algebra and Trigonometry 13.6) 0.99.
- **Sibling books not chosen:** Algebra and Trigonometry and College Algebra share their
  text with Precalculus; Chemistry: Atoms First shares its text with Chemistry.

## 3. Books and licences

Only CC BY material may be published. On 2026-10-13 (`a11y-stem gold licenses`, saved as
`selection/licenses-2026-10-13.csv`) every current edition of the books first suggested is
CC BY-NC-SA 4.0: College Physics 2e, University Physics Volume 1, Chemistry 2e, Calculus
Volume 1 (every calculus volume), Introductory Statistics 2e, Algebra and Trigonometry 2e,
Precalculus 2e, Intermediate Algebra 2e. The live CC BY STEM books are Physics and
Statistics, both used in development. So the held-out set comes from **retired first
editions**, which are CC BY 4.0 in both the OpenStax archive and the CMS and are still
served by the archive:

| Book | Slug | OpenStax state | Version (archive 20260604.144757) | Licence, archive / CMS | Senior authors | Source |
|---|---|---|---|---|---|---|
| College Physics | `college-physics` | retired | 1aa3e7a | CC BY 4.0 / CC BY 4.0 | Paul Peter Urone, Roger Hinrichs | https://openstax.org/details/books/college-physics |
| Chemistry | `chemistry` | retired | 741a7af | CC BY 4.0 / CC BY 4.0 | Paul Flowers, William R. Robinson, PhD, Richard Langley, Klaus Theopold | https://openstax.org/details/books/chemistry |
| Precalculus | `precalculus` | retired | ebc5beb | CC BY 4.0 / CC BY 4.0 | Jay Abramson | https://openstax.org/details/books/precalculus |
| Introductory Statistics | `introductory-statistics` | retired | 1d23970 | CC BY 4.0 / CC BY 4.0 | Barbara Illowsky, Susan Dean | https://openstax.org/details/books/introductory-statistics |
| Intermediate Algebra | `intermediate-algebra` | retired | eb2e29d | CC BY 4.0 / CC BY 4.0 | Lynn Marecek | https://openstax.org/details/books/intermediate-algebra |

Licence URLs as served: archive `http://creativecommons.org/licenses/by/4.0/`, CMS
`https://creativecommons.org/licenses/by/4.0/`; `gold fetch` checks both
(`gold.openstax.check_public_license`).

Fetching them needs `a11y-stem gold fetch --allow-retired`. Whether a retired edition is
acceptable ground truth is open in ADR 0001 (question 2); Harish decides before the run
(section 12).

Attribution for anything published from this set (the form `eval/render.py` prints in
each PDF): "<section title>" from <book title> by <senior authors>, OpenStax, licensed
under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); original page
https://openstax.org/books/<book>/pages/<section>; access for free at openstax.org;
changes: reformatted as a printable page for evaluating a11y-stem, image alt text left out
of the print, no wording edited. a11y-stem is not affiliated with or endorsed by
OpenStax. `proof/heldout-gold/ATTRIBUTION.md` is generated by `gold fetch`.

## 4. The held-out set and how it was chosen

### 4.1 Procedure (code in `selection/`, hashes in `selection/rules-before-draw.sha256`)

1. **Pool.** Every numbered section (slug `N-M-...`) of the five books.
2. **Eligibility** (`features.py`). Out: sections used in development (section 2); lab worksheets (an element with the class token `lab`; Introductory Statistics wraps each Stats Lab page in one); sections under 300 visible words; near-copies of the development sections (5% or more of their word 8-grams in the development text).
3. **Features**, from the archive XHTML with the extractor `gold fetch` uses (`gold.openstax.build_page_records`): equations (block, inline), figures, tables, headings, visible words (head, style, script and MathML removed). Maths density = equations per 1,000 visible words.
4. **Strata and draw** (`draw.py`). Per book, eligible sections sorted by maths density (then slug) are cut into terciles by position (low, mid, high). One `random.Random`, seeded with the first 16 hex digits of SHA-256("a11y-stem held-out OpenStax set, pre-registered 2026-10-13") = 7691389569267961419, shuffles each tercile in a fixed order (book order as in section 3, then low, mid, high). The first section of each shuffled tercile is drawn; the rest of the order is that tercile's replacement queue. 5 books x 3 terciles = 15 sections.
5. **Page rules** (`budget.py`), on Letter prints made by `a11y-stem eval render` after `a11y-stem gold fetch --allow-retired`: (A) a section printing to more than 30 pages (the production `max_pages_per_job`; `convert_pdf` refuses more with `too_many_pages`) is replaced by the next in its queue; (B) while the total is above 250 pages, the longest selected section is replaced the same way, and while it is below 150 the shortest; (C) at most 15 replacements.

Strata are within each book: Precalculus's "low" tercile is denser than Introductory Statistics' "high" one, so the set spans 0 to 112 equations per 1,000 words overall.

Only input properties were looked at to choose: the counts above, the overlap, and page counts. No system has produced any output for these sections.

### 4.2 Deviations during selection (all before any page count was read)

- `features.py` first counted words with the page `<head>`, `<style>` and `<script>` text included (OpenStax's developer scripts), which made unrelated sections overlap by 5-10%. Fixed to visible text only; the threshold, 10% until then, was set to 5% from the distribution of the fixed overlap (more than twice the 0.021 maximum of the books with no shared lineage). The first lab rule looked only at the page's top element and missed the Introductory Statistics labs; fixed to any element with the class token `lab`. Both before the draw.
- `budget.py` as hashed before the draw capped a section at 40 pages. The first print run failed (Playwright is not in the default dependency group); the second was stopped part-way, its log unread (kept as `budget-stopped-unread.log`), and the cap set to 30 to match the production page limit. The CLI calls were changed to `uv run --isolated --group browser` so the shared virtual environment was not modified. Each change is logged with a time and hash in `rules-before-draw.sha256`.

### 4.3 Replacements made by the page rules

- replace precalculus/5-1-angles (low) by 5-4-right-triangle-trigonometry: rule A, more than 30 pages
- replace precalculus/3-3-power-functions-and-polynomial-functions (mid) by 3-4-graphs-of-polynomial-functions: rule A, more than 30 pages
- replace precalculus/3-4-graphs-of-polynomial-functions (mid) by 1-6-absolute-value-functions: rule A, more than 30 pages
- replace precalculus/4-2-graphs-of-exponential-functions (high) by 7-2-sum-and-difference-identities: rule A, more than 30 pages
- replace intermediate-algebra/3-2-slope-of-a-line (low) by 4-3-solve-mixture-applications-with-systems-of-equations: rule A, more than 30 pages
- replace precalculus/7-2-sum-and-difference-identities (high) by 3-1-complex-numbers: rule B, total 252 above 250

Rules met: True; replacements: 6 of 15 allowed; total 242 Letter pages.

### 4.4 The 15 sections

Counts are from the held-out gold (`proof/heldout-gold`, fetched 2026-10-13). G1 = equations matching the known scorer-gap pattern (7.1). Alt = figures with a non-empty OpenStax alt text (the judge's reference). Box = headings inside a note, example or other box.

| # | Book | Section | Title | Stratum | Eq./1,000 words | Pages | Equations (block / inline) | G1 | Figures (alt) | Tables | Headings (box) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | College Physics | `29-3-photon-energies-and-the-electromagnetic-spectrum` | 29.3 Photon Energies and the Electromagnetic Spectrum | low | 15.09 | 10 | 47 (10 / 37) | 4 | 6 (5) | 1 | 23 (19) |
| 2 | College Physics | `5-3-elasticity-stress-and-strain` | 5.3 Elasticity: Stress and Strain | mid | 32.83 | 14 | 121 (22 / 99) | 10 | 8 (8) | 1 | 31 (27) |
| 3 | College Physics | `28-4-relativistic-addition-of-velocities` | 28.4 Relativistic Addition of Velocities | high | 42.55 | 9 | 68 (9 / 59) | 13 | 5 (5) | 0 | 26 (22) |
| 4 | Chemistry | `19-3-spectroscopic-and-magnetic-properties-of-coordination-compounds` | 19.3 Spectroscopic and Magnetic Properties of Coordination Compounds | low | 2.96 | 8 | 9 (3 / 6) | 2 | 8 (8) | 0 | 21 (16) |
| 5 | Chemistry | `7-3-lewis-symbols-and-structures` | 7.3 Lewis Symbols and Structures | mid | 8.34 | 9 | 25 (4 / 21) | 4 | 4 (4) | 0 | 23 (12) |
| 6 | Chemistry | `14-7-acid-base-titrations` | 14.7 Acid-Base Titrations | high | 20.45 | 11 | 63 (36 / 27) | 10 | 3 (3) | 1 | 14 (10) |
| 7 | Precalculus | `5-4-right-triangle-trigonometry` | 5.4 Right Triangle Trigonometry | low | 53.64 | 29 | 162 (21 / 141) | 1 | 15 (15) | 1 | 48 (32) |
| 8 | Precalculus | `1-6-absolute-value-functions` | 1.6 Absolute Value Functions | mid | 72.41 | 28 | 205 (19 / 186) | 1 | 12 (12) | 2 | 55 (42) |
| 9 | Precalculus | `3-1-complex-numbers` | 3.1 Complex Numbers | high | 100.39 | 20 | 206 (24 / 182) | 0 | 3 (3) | 1 | 68 (51) |
| 10 | Introductory Statistics | `2-4-box-plots` | 2.4 Box Plots | low | 0.0 | 9 | 0 (0 / 0) | 0 | 5 (5) | 0 | 13 (12) |
| 11 | Introductory Statistics | `4-5-hypergeometric-distribution` | 4.5 Hypergeometric Distribution | mid | 3.76 | 6 | 4 (0 / 4) | 0 | 1 (1) | 0 | 17 (15) |
| 12 | Introductory Statistics | `3-1-terminology` | 3.1 Terminology | high | 16.23 | 8 | 31 (0 / 31) | 0 | 0 (0) | 1 | 11 (10) |
| 13 | Intermediate Algebra | `4-3-solve-mixture-applications-with-systems-of-equations` | 4.3 Solve Mixture Applications with Systems of Equations | low | 12.53 | 28 | 65 (4 / 61) | 1 | 0 (0) | 6 | 40 (31) |
| 14 | Intermediate Algebra | `4-6-solve-systems-of-equations-using-determinants` | 4.6 Solve Systems of Equations Using Determinants | mid | 92.28 | 27 | 183 (5 / 178) | 0 | 0 (0) | 9 | 55 (45) |
| 15 | Intermediate Algebra | `1-5-properties-of-real-numbers` | 1.5 Properties of Real Numbers | high | 111.59 | 26 | 180 (6 / 174) | 2 | 0 (0) | 4 | 64 (55) |
| | **Total** | | | | | **242** | **1369** (163 / 1206) | **48** | **70** (69) | **27** | **509** (399) |

Per book:

| Book | Sections | Pages | Equations | Figures | Tables | Headings |
|---|---|---|---|---|---|---|
| College Physics | 3 | 33 | 236 | 19 | 2 | 80 |
| Chemistry | 3 | 28 | 97 | 15 | 1 | 58 |
| Precalculus | 3 | 77 | 573 | 30 | 4 | 171 |
| Introductory Statistics | 3 | 23 | 35 | 6 | 1 | 41 |
| Intermediate Algebra | 3 | 81 | 428 | 0 | 19 | 159 |

## 5. Can the existing pipeline fetch and print these? (checked 2026-10-13)

| Step | Result | What it needs |
|---|---|---|
| `a11y-stem gold licenses` | all five books CC BY 4.0 in the archive and the CMS, state `retired`, in the archive release | network only |
| `a11y-stem gold fetch --book B --page S ... --out DIR --allow-retired --date 2026-10-13` | worked for every section drawn, all five books, records written with `book_state: retired`, `_book.json`, `manifest.json` and `ATTRIBUTION.md` | `--allow-retired` (the state gate refuses retired books by default) |
| `a11y-stem eval render --book B --page S --gold DIR --out F.pdf` | printed every drawn and replacement section (Letter, Chromium m153); the running footer names the book and "CC BY 4.0", the closing block the senior authors from `_book.json`; figure images downloaded from openstax.org | Playwright, which is not in the default dependency group: `uv run --group browser` or `uv sync --all-groups`; Chromium 1243 is already in `~/.cache/ms-playwright`. For this registration it ran as `uv run --isolated --group browser`, leaving the shared virtual environment as it was |
| `a11y-stem eval equations --gold proof/heldout-gold --self-check` | 1,369 gold equations parsed and scored against themselves: 1.0, 0 errors | nothing |
| OpenStax cache `a11y-stem/.cache/openstax` | holds the archive JSON of every section of the five books (fetched for the features, 0.5 s apart) | nothing |
| `eval pipeline --task equations/headings/figures/document --gold DIR` | takes any gold directory; not run on this set (section 10) | `--cache` outside the repository (section 6.1) |
| Scorer gaps in reports | `scorer_gap_ids` reads `tests/fixtures/sample_latex_predictions.jsonl`, which holds the physics sample's ids only, so `..._excluding_scorer_gaps` will equal the plain numbers on this set | the G1 rule (7.1), applied by the bootstrap script (section 12) |
| `demo build`, `render.page_source_from_openstax` | refuse retired books (live-only state gate) | not needed for the eval; the demo stays a live CC BY page |

Two leftovers of this check sit in the repository's git-ignored caches: the archive JSON
above and the figure images of the printed sections in `a11y-stem/.cache/eval/images`
(keyed by URL hash). No held-out PDF was written to `.cache/eval/pages`. The prints made
here are in `~/.cache/a11y-proof/prereg/pdfs/`, outside both repositories.

## 6. Systems compared

Every system gets the same 15 PDFs, byte for byte: the ones a11y-stem's document task
prints in its first run (`eval/render.py`, Letter, kept in the run's cache outside the
repository). Their SHA-256 values and page counts go in the run report, and the page
counts must equal section 4's (a re-print differs in bytes, since Chromium stamps a
creation date, but not in pages). The prints made for this registration only counted
pages. Each system is run with settings fixed before the run. None of them is tuned on
these sections (section 10).

### 6.1 a11y-stem, production defaults

- Code: the `a11y-stem` commit checked out when the run starts, recorded in every report
  (`pipeline_version`, prompt versions, model ids). At registration time that is
  `feat/beta-app` at 293160e: pipeline 0.4.0, prompts `equation-df0e12f1`,
  `figure-ac51f42d`, judge `judge_figure-137e2607`. A later commit is allowed only if it
  was not informed by these sections (section 10); the report lists the diff of prompts,
  rules and thresholds against 293160e.
- Settings: `Settings()` defaults with only `OPENAI_API_KEY` from `.env` (run with
  `uv run --env-file .env` from the repository root, which today holds no other
  variable). That means: main model gpt-6.1-sol, cheap model gpt-6-luna,
  `auto_accept_confidence` 0.85, `abstain_confidence` 0.4, every content flag off
  (`FEATURE_TABLES`, `FEATURE_LONG_DESCRIPTIONS`, `FEATURE_COLOUR`,
  `FEATURE_NOTATION_MEMORY`), the second automated check on (`DECISIONS_FIGURE_VERIFY`,
  `DECISIONS_EQUATION_SHADOW`), `max_pages_per_job` 30, `convert_pdf` concurrency 4,
  `store=False` on every OpenAI call. No course notation list.
- Tasks: `eval pipeline` with `--gold proof/heldout-gold` and `--cache` pointing OUTSIDE
  the repository (for example `~/.cache/a11y-proof/heldout-eval-cache`). Reason:
  `a11y-research/handwriting/detect/build_corpus.py` and
  `a11y-research/decisions/headings/prep.py` read every PDF in the repository's
  `.cache/eval/pages/`, so a held-out print left there would flow into development
  sweeps. Tasks run: `document` (primary: the whole section PDF through `convert_pdf`),
  `equations`, `headings` and `figures`. `tables` is not run as a task (the flag is off
  in production); tables only enter through the document task's review load.
- Repeats: the document, equations and figures tasks run twice (R = 2, independent runs,
  same code and settings), because model answers differ from run to run (docs/eval.md
  shows 0.733 and 0.767 on the same 30 equations). The headings task makes no model call
  and runs once.
- Per-job limits. The eval spends from one run-level budget (`--max-usd`, `--max-calls`
  5000) and does not apply the app's per-job limits (`web/worker.py`: the smaller of
  $2.00 and $0.30 per page, and of 400 calls and 80 calls per page). After the run, each
  section's calls and cost (second check included) are compared with those limits for its
  page count; a section over them would have stopped part-way in the app, and is reported
  as a production-configuration failure (section 9) next to its eval numbers.

### 6.2 The chatbot recipe (Purdue's prompt on gpt-6.1-sol)

- Script: `a11y-research/niche/chatbot-runs/scripts/run_chat.py`, mode `pages` (the PDF as
  an `input_file` plus a 150 dpi PNG of every page, then the prompt), no system or
  developer instructions, no tools, `store=False`, API-default reasoning effort,
  `max_output_tokens` 64,000.
- Prompt (Purdue Innovative Learning, "Accessibility in Mathematics", verbatim):
  "Convert to accessible HTML compliant with WCAG 2.2 AA with complete MathJax headers and
  remove [cite]." The prompt's own wording is quoted as the input it is; it is not a claim
  this project makes.
- Model gpt-6.1-sol. R = 2 runs per section (primary arm).
- Secondary arm: the research's careful "expert" prompt (`niche/chatbot-runs/prompts.json`,
  key `expert`), gpt-6.1-sol, R = 1. It is there so the comparison is not only against
  the weakest prompt an instructor might copy. No other prompt is added after the run.
- The output is the HTML in the reply, taken as it comes (one file per section). A reply
  that is cut off (`status` incomplete, for example at the 64,000-token ceiling, which the
  26-29 page sections may reach) or refused is scored as it is, not re-asked, and counted
  as a section failure (section 9).

### 6.3 Open parsers

The parsers, their versions and their settings are the ones named in the parser plan
(being written by a separate task; no plan file existed when this was written). The plan
must be frozen, with its path and SHA-256 appended to `PREREG.sha256` as an addendum
line, before any held-out output from any system exists. If it is not frozen by then, the
parser comparison is not run on this set. Constraints on the plan, fixed here:

- Settings are chosen on development material only (OpenStax Physics 2.1-2.3 and the
  repository's own fixtures), never on these sections.
- Every parser runs once per section (they are deterministic; if one is not, R = 2).
- Output (Markdown, HTML or JSON) is converted to the same intermediate form for scoring:
  headings with levels, text blocks, equations as LaTeX or MathML, tables as grids,
  images with any text alternative they carry.
- A parser that fails on a section (crash, timeout of 30 minutes, empty output) scores that
  section as "found nothing". No reruns with other settings.

## 7. Metrics and how they are computed

Unless stated otherwise: a **rate** has every item in the run as its denominator and an
item with no proposal counts as wrong (docs/eval.md "Conventions"). Point estimates pool
all items over all sections and runs (micro-average). Intervals are in section 8.
**Primary** metrics are the ones the comparison claims in section 8.3 rest on; the rest
are **secondary**: reported, never used to claim a difference.

### 7.1 Equations

Gold: every `<math>` element in the 15 sections (`proof/heldout-gold`).

| Metric | How | Systems | Primary? |
|---|---|---|---|
| `end_to_end_exact_lenient` (document task) | gold equations whose aligned prediction is exactly right after lenient normalisation (`eval/equations.py`), divided by all gold equations. Alignment as the document task does it: weighted LCS in reading order, threshold 0.5 on token signatures (docs/eval.md "document") | all | yes |
| `matched_exact_lenient`, `matched_mean_teds` | the same over aligned pairs only; TEDS = 1 - TED / max(nodes), Zhang-Shasha on normalised trees, 0 with no proposal | all | no |
| `detection_recall`, `detection_precision` (+ excluding `prose_math`) | as in the document task | all | no |
| `exact_lenient`, `exact_strict`, `mean_teds` (equations task: each gold equation rendered to a PNG with its text layer and context) | `eval pipeline --task equations` | a11y-stem | no |
| `by_display`, `by_route`, `by_status`, `selective` | as in docs/eval.md | a11y-stem | no |
| `unflagged_wrong_per_100` | wrong aligned equations (not exact_lenient, and not G1, see below) that were not inside a required decision, per 100 gold equations. The chatbot and the parsers ask nothing, so every wrong equation of theirs is unflagged | all | yes |
| `meaning_level_unflagged` | the subset of the above that Harish labels meaning-changing (wrong symbol, sign, relation, digit, vector made scalar, dropped or added term) rather than cosmetic, labelling blind to the system (section 7.8) | all | no |

How the chatbot's and the parsers' equations are read: every MathJax TeX span and
`<math>` element in reading order (`chatbot-baseline/extract.py`), Markdown `$...$` and
`$$...$$` for parsers, then the same clean-up and LaTeX-to-MathML converter a11y-stem uses
on its own model output (`equation_step.clean_latex`, `_convert`), then the document
alignment with the MathML signature only (they have no PDF region, so no text-layer
"second chance"). This is what `chatbot-baseline/score_baseline.py` does today on the
physics gold; it must be generalised to take a gold directory (section 12).

**Known scorer gaps** (a correct answer can still fail exact match), listed before any
output exists:

1. **G1, operators packed into text.** OpenStax sometimes writes `<mtext>time =</mtext>`
   or `<mtext>m/s)(46 s)</mtext>`; latex2mathml writes text plus `<mo>`. 5 of the 30
   development equations. Detected on gold alone: a gold `<mtext>` whose text holds one of
   `= + − - × · ÷ / ( ) [ ] < > ≤ ≥ ≈ ±` next to other characters
   (`selection/gold_summary.py`, function `g1`). Count on this set: section 4.4.
   `end_to_end_exact_lenient` is reported over all gold equations (headline) and over
   gold equations without G1 (secondary).
2. **Upright letters (chemistry, units).** Checked on hand-made pairs on 2026-10-13
   (scorer only): `\mathrm{H}_2\mathrm{O}` and `\text{H}_2\text{O}` both match a gold
   written with `<mtext>` leniently, and `\text{m}` matches a gold `<mi>m</mi>`; but
   `\mathrm{...}` fails against a gold `<mi mathvariant="normal">` (lenient false, TEDS
   0.6), and strict match fails for `\mathrm` against `<mtext>`. No held-out gold equation
   uses `<mi mathvariant="normal">` (0 of 1,369; 278 hold an `<mtext>`), so the lenient
   numbers should not be hit; the strict ones are. Other chemistry notation (charges,
   reaction arrows, states) is untested until the sample in (b).
3. **Prose maths.** Maths OpenStax writes as plain HTML text has no gold MathML, so an
   equation found there counts against detection precision (`prose_math`, reported
   apart).
4. **Exact match is not speech.** Two different trees can read the same, and one wrong
   digit in a long equation costs little TEDS.
5. **Multi-line layouts.** OpenStax uses a three-column `mtable`; `array{rcl}` matches it,
   other alignments may not.
6. **Alignment.** With repeated equations (worked examples often restate one), the weighted
   LCS can pair an output with the wrong gold copy; both are the same maths, so exactness
   is unaffected, but detection counts can shift by a few.

**Scorer readiness check (gold only, before any system output):**
(a) `a11y-stem eval equations --gold proof/heldout-gold --self-check` must parse every
gold equation and score 1.0. Done 2026-10-13: n 1,369, exact_strict, exact_lenient and
mean TEDS 1.0, 0 errors (plumbing only: it does not test normalisation).
(b) A seeded sample of 40 gold equations (8 per book;
`random.Random` seeded with the SHA-256 of "a11y-stem held-out scorer sample 2026-10-13",
first 16 hex digits, drawn from each book's equations in gold order) gets known-correct
LaTeX, written from the gold MathML (not by any system under test) and checked by Harish
against the rendered gold. Scored with `a11y-stem eval equations --pred`, it gives the scorer's
ceiling on this set (exact_lenient with a Wilson interval). Every failure is classified
into a gap class (above, or a new one) in an addendum written before the run. The scorer
is not changed for this run. If it is changed later, the change is applied to every
system, and both versions of every equation number are published.

### 7.2 Headings

Gold: every `h1`-`h6` with visible text in the sections, in document order
(docs/eval.md "Heading records"). Prediction: a11y-stem's heading fixes with status
accepted, needs_review or edited; for the chatbot and the parsers, `h1`-`h6` in the HTML,
or Markdown `#` levels.

| Metric | How | Primary? |
|---|---|---|
| `precision`, `recall`, `f1` | normalised text (NFKC, case-folded, punctuation to spaces), aligned in reading order by weighted LCS with difflib ratio >= 0.85; micro-averaged over sections | F1: yes |
| `level_accuracy` | among matched pairs, relative level (gold level minus the section's top gold level, against the same for the prediction) agrees | no |
| `recall_by_context` | recall in the body and in each box class | no |

For the chatbot and parsers the same code (`eval/pipeline_eval.py` heading matching) is
fed their headings. The document title the chatbot invents (an `h1` before the section
title) is not removed: if it does not match gold, it is a false positive.

### 7.3 Figures

Gold: every OpenStax figure with a non-empty alt text (the judge needs a reference): 69
of the 70 figures. The three Intermediate Algebra sections and Introductory Statistics
3.1 have none, so figure numbers rest on 10 sections.

- a11y-stem: `eval pipeline --task figures` (each figure's image file through
  `figure_step.describe` with its caption), and the document task's figure fixes.
- Chatbot: its HTML has no images in earlier runs (0 of 45 outputs). The text it wrote
  for a figure is found by rule: a `<figure>`, `<figcaption>` or `<details>` block, or the
  paragraph right after an `<img>`, whose text contains the gold figure's number
  ("Figure 12.3") or at least half of the gold caption's word 4-grams; the description is
  that block's text minus the caption. No such block: "no description".
- Parsers: the text alternative they emit for the image (alt, or a picture description if
  the parser plan turns one on); none: "no description".

| Metric | How | Primary? |
|---|---|---|
| `judge_pass_rate` | passes / figures measured; pass = `conveys_key_information` and not `factual_error`; "no description" counts as a fail (docs/eval.md "figures") | no (the judge is uncalibrated) |
| `judge_factual_error_rate` | judged descriptions where the judge found a factual error | no |
| `no_description_rate` | figures with nothing a screen reader would announce as their description | no |
| `check_pass_rate`, `decorative_rate` | a11y-stem only, as in docs/eval.md | no |

The judge: `eval/judge.py`, prompt `judge_figure` (version recorded), gpt-6.1-sol, no
fallback, with the image; it sees the candidate, the OpenStax alt and the caption, never
which system wrote the candidate. Judge cost is reported apart and never counted as
system cost.

### 7.4 Review load (a11y-stem)

From the document task (docs/eval.md "Review load"): `required_decisions` (units: a group
of repeated headings counts once), `required_per_pdf_page` (**primary**, single-system),
`optional_per_pdf_page`, `required_by_reason`, `flag_precision` and `flag_recall` (wrong
= not exact_lenient for an aligned equation, not a gold heading or the wrong relative
level for a heading), and `wrong_not_required`. With the tables flag off, every data
table layout detects is kept as an image with a required decision; those decisions are
counted and listed under their own reason. The chatbot and the parsers ask no questions:
their review load is reported as "none asked", next to their unflagged errors, never as
zero work.

### 7.5 Invented content

The **invented-content rate** of an element type is the share of a system's output units
of that type that the source PDF does not support. Reference text R: the PDF's own text
layer (PyMuPDF words, every page, including the footer and attribution the print adds),
normalised as `niche/chatbot-runs/scripts/common.py` `norm_words` does (NFKC, ligatures
and quotes folded, lower case, runs of `[a-z0-9]`). Units and support rules:

| Type | Unit | Unsupported when | Primary? |
|---|---|---|---|
| Prose sentence | a sentence of 7 or more words in output text outside maths, headings, tables and figure descriptions (split after `.`, `!` or `?` followed by space) | fewer than 25% of its word 4-grams occur in R (the `invented_sentences` rule, `niche/chatbot-runs/scripts/analyze.py`) | yes |
| Number | each number in output prose and headings, outside maths, read the way `figure_details` reads numbers (minus variants, thousands separators, 0.50 = 0.5) | it is not among the numbers of R read the same way | yes |
| Heading | each output heading | no line of R matches its normalised text with difflib ratio >= 0.85 | no |
| Equation, aligned | each output equation aligned to a gold equation (7.1) | its multiset of letters, digits and Greek letters (NFKC, case-folded, as `equation_step` builds it) holds a symbol more often than the gold equation does: an added symbol | no |
| Equation, unaligned | each output equation with no gold partner, of 3 or more such symbols | no window of R's symbol stream (letters, digits, Greek in reading order) of the same length matches its symbol sequence with difflib ratio >= 0.8 | no |
| Figure description | each alt text or description block (7.3) | the image-aware judge sets `factual_error` | no |
| Table cell | each non-empty cell of an output table | a number in it is not among R's numbers, or it has 3 or more words and fewer than half of its word bigrams occur in R | no |

Also reported: added symbols per 1,000 output symbols (aligned equations), and per type
the count per PDF page. Unaligned equations shorter than 3 symbols are counted as "too
short to check". a11y-stem copies prose from the text layer, so its prose and number
rates measure the copying; the types where it can invent are equations and figure
descriptions.

What the rule cannot see, said up front: a changed word inside a sentence that keeps a
quarter of its 4-grams passes the sentence rule (a changed number does not pass the
number rule); faithful paraphrase and reordering count as unsupported (false positives);
R holds what is printed, so a correct description of a figure is never "supported" by R
(figures use the judge instead). So the rule's precision and recall are measured: a
seeded sample of 50 units the rule flags and 50 it does not, per system and per type
where that many exist, labelled by Harish blind to the system (7.8); both the raw rate and
the audited precision and recall are published.

The invented-content builder (being written by a separate task) implements exactly this
table. Its code is frozen and its SHA-256 recorded in the run report before it scores any
held-out output; it is tested on development outputs (the Physics 2.1-2.3 chatbot runs,
with inventions planted by hand) only. Where the builder and this table differ, this
table wins unless a v2 of this file is written before the run.

### 7.6 Coverage (secondary)

How much of the PDF's text reaches the output: R's word 6-gram recall in the output, and
per paragraph "kept" (4-gram recall >= 0.6), "partial" (>= 0.2) or "missing"
(`analyze.coverage`). Reported for every system; a dropped Teacher-Support-style box or a
skipped exercise set shows here.

### 7.7 Cost and time per page

| System | Cost per PDF page | Time per PDF page |
|---|---|---|
| a11y-stem | (pipeline + second check) / PDF pages converted, from `ModelCall.cost_usd` at `models/pricing.py` list prices (docs/eval.md "Cost per page"); judge reported apart | per section, `DocumentStats.latency_ms` / its PDF pages (wall clock, concurrency 4, on the box); median and p95 over the 15 sections and both runs |
| Chatbot | `usage.jsonl` cost (same list prices) / PDF pages | the call's latency / PDF pages; median and p95 |
| Parsers | $0 in API; hosting not estimated | wall clock per page on the box, CPU only unless the plan says otherwise; threads, CPU model and memory recorded |

Cost and time are reported without intervals (they are measurements, not estimates of a
rate), with the min and max over sections.

### 7.8 Human labels (after the run, before publishing)

Three label sets, each on a sheet with no system name, items in a seeded random order
(seed: SHA-256 of "a11y-stem held-out labels 2026-10-13"):

1. Wrong equations (7.1): gold rendering next to the output rendering; meaning-changing or
   cosmetic.
2. Figures: a seeded sample of 30 judged descriptions across systems; pass or fail and
   factual error yes or no, with the image, the caption and the OpenStax alt. Gives
   Cohen's kappa against the judge with a bootstrap interval (`eval calibrate`). If the
   lower end of kappa's interval is below 0.4, judge-based numbers are published as "not
   reliable on this set" next to the human-labelled sample's numbers.
3. Invented content: the audit samples of 7.5.

Labels are written once. Harish labels; no model labels for him.

## 8. Statistics

### 8.1 Intervals

- **Cluster bootstrap by section** for every headline number and every paired
  difference: resample the 15 sections with replacement, B = 10,000 resamples, NumPy
  `default_rng(0)`, recompute the pooled metric (sum of numerators over sum of
  denominators over the resampled sections), and take the 2.5th and 97.5th percentiles.
  Resamples where a denominator is 0 are skipped and counted.
- **Wilson score interval** for every rate as well, labelled "Wilson (treats items as
  independent)". It is narrower than the cluster interval when items in a section are
  alike; the cluster interval is the one quoted.
- With 15 clusters a percentile bootstrap is approximate and tends to be a little narrow;
  this is said next to the numbers.
- **Repeats.** With R = 2, a section's numerators and denominators are summed over both
  runs before resampling (the run is not a resampling unit). Each run's own point estimate
  is also published, and the run-to-run difference.
- **Per book** (3 sections each): point estimates only, described as such.

### 8.2 Paired comparisons

A difference A - B uses the same resampled sections for both systems in each bootstrap
draw. Systems are compared only on the sections both completed; a section one system
failed on counts as a failure of that system (section 9), not as missing.

### 8.3 Claims

Two families of comparisons, each Bonferroni-corrected within itself:

- **Family A, a11y-stem against the chatbot recipe (Purdue prompt), 5 comparisons:**
  `end_to_end_exact_lenient`, `unflagged_wrong_per_100`, invented prose sentence rate,
  invented number rate, heading F1.
- **Family B, a11y-stem against each parser in the frozen parser plan:**
  `end_to_end_exact_lenient` and heading F1 per parser (2 x number of parsers).

"A is better than B on X" may be said only when the (1 - 0.05 / k) cluster-bootstrap
interval of the difference excludes 0, k being the family's number of comparisons
(k = 5 for family A: the 99% interval). Otherwise the wording is "no difference this set
can detect", with the interval. The 95% intervals are published for everything. The
expert-prompt arm, the single-system numbers (required decisions per page, cost, time)
and every secondary metric are descriptive.

## 9. What counts as a failure

- **Equation:** not exact_lenient against its gold, or no aligned output (end to end).
  Reported with and without G1. An unflagged failure is one not inside a required
  decision.
- **Heading:** a gold heading not found (recall), an output heading with no gold partner
  (precision), a matched heading at the wrong relative level (level accuracy).
- **Figure:** a judge fail, or no description.
- **Invented content:** an unsupported unit (7.5).
- **Section:** the system could not convert it (exception, `LayoutError` such as
  `too_many_pages`, refusal, empty or cut-off output, a parser crash or a 30-minute
  timeout). Every gold item of that section then counts as missed. It is not dropped from
  any denominator.
- **Infrastructure, not the system:** provider 5xx or 429, a lost network, the machine
  going down. Such a section may be re-run, at most twice, with every attempt logged; a
  budget stop (`--max-usd`) is infrastructure too, and the section is re-run whole, never
  scored half-converted (the eval already drops a half-converted page).
- **Run:** a run is complete when every section of every arm has a scored outcome. An
  incomplete run is published as incomplete, with what is missing.

All failures are published: counts by type and by book, and every unflagged
meaning-level equation error with its gold and output (CC BY content only).

## 10. Nothing is tuned on these sections

1. From the time `PREREG.sha256` was written, nobody (person or agent) runs any system
   under test on these 15 sections, opens their outputs, or uses them to choose a prompt,
   rule, threshold, model, normalisation or scorer change, until the frozen run. The
   gold-only steps of this file (readiness check, label sheets) are the exception.
   `--fake` runs count as runs (layout and headings are real code) and are not allowed on
   this set; plumbing is checked on the development gold.
2. The held-out gold and PDFs are not added to `data/gold`, to `.cache/eval/pages`, to any
   test fixture, to any layout or page-type sweep or corpus, or to the demo.
3. After the first held-out output of any system exists, any change to a11y-stem's
   prompts, rules, thresholds, models, layout heuristics or scorers means the numbers of
   this run can no longer be quoted as held-out results for the changed system. Held-out
   numbers for the changed system need a NEW held-out set, chosen by this same procedure
   with a new seed text, excluding these 15 sections and everything used in development by
   then, registered the same way before its run.
4. Harness bugs found during the run (a crash in a scorer or a runner) may be fixed only if
   the fix does not change what any system does or how any output is scored. A fix that
   changes a number is labelled post hoc; the numbers before and after are both
   published.
5. Error analysis on the outputs is encouraged after the run. Once it informs a change,
   these sections are development material for the next held-out set.

## 11. Reporting rules

- Publish every metric in section 7, including the ones that go against a11y-stem, with
  n, the interval, the per-section table, and the deviations from this file.
- Wording: a11y-stem "assists"; never "compliant", "verified", "accessible guaranteed",
  or any claim of conformance with WCAG, ADA or another standard. Judge numbers are
  "agreement with a model judge", not accuracy. Automated HTML checks (static, axe-core)
  are reported as what a machine can detect, not as accessibility.
- Inputs in anything published: these OpenStax CC BY sections (with the attribution in
  section 3) and MIT material from the repositories. Never the Purdue samples, the lab
  manuals, lec14 or any user upload.
- Logs from the run are content-free (codes, counts, costs, ids); outputs live in files
  next to the report, not in logs.
- Outputs published for inspection (a11y-stem HTML, chatbot HTML, parser output) are
  derived from CC BY sections and carry the attribution.

## 12. Before the run (each item frozen and hashed in the run report)

1. Harish decides ADR 0001 question 2 (retired editions as ground truth). If the answer
   is no, this registration is void as a whole; a new one is written with live CC BY
   books only.
2. Spend: Harish approves a cap of $50 for the whole run (estimate below).
3. Scorer readiness check (7.1), with its addendum.
4. A cluster-bootstrap and paired-difference script over the eval's per-item JSONL
   (section 8), tested on the development reports.
5. `score_baseline.py` generalised to any gold directory, for the chatbot and the parsers.
6. The invented-content builder (7.5), frozen.
7. The figure-description rule for chatbot output (7.3), implemented and tested on the
   development chatbot runs.
8. The parser plan, frozen (6.3).
9. One command that reproduces every number: `make heldout-score` (from saved outputs; no
   key, no spend; exactly the published numbers) and `make heldout-run MAX_USD=...` (calls
   the models again; numbers will differ by run-to-run variation). Both verify
   `PREREG.sha256` and the held-out gold manifest's SHA-256 first and stop on a mismatch.

Spend estimate (list prices of 2026-10-07; the cap stops the run, it does not pause it).
This set has 5.7 gold equations per PDF page against 0.7 on the development sample, so
the development's $0.0035 per page does not carry over; the estimate scales by element:

| Arm | Basis | Estimate |
|---|---|---|
| a11y-stem document task, 2 runs | about $0.002 per equation or figure element (development document run: $0.145 for about 71 elements); about 1,450 elements | about $6 |
| a11y-stem equations task, 2 runs | $0.0017 per equation (development) x 1,369 | about $5 |
| a11y-stem figures task with the judge, 2 runs | about $0.007 per figure x 70 | about $1 |
| Chatbot, Purdue prompt, 2 runs | $0.36-0.43 per 27 pages in earlier sol runs; at most about $0.8 per section (about 80,000 input tokens with page images, plus 64,000 output tokens) | about $8 expected, $24 at most |
| Chatbot, expert prompt, 1 run | longer outputs | about $6 expected, $12 at most |
| Judge on chatbot descriptions | 69 figures | under $1 |
| Parsers | | $0 in API |
| **Total** | | **about $27 expected; cap $50** |

## 13. Limits of this design

- Clean inputs: every PDF is an OpenStax web page printed by one Chromium, one font
  family, single column, untagged, with a text layer. Real course PDFs (LaTeX, Word,
  scans, slides, handwriting) are harder; this set says nothing about them.
- Retired first editions (published 2012-2017) are older than what OpenStax serves
  today; their MathML and alt text may be older in style. The licence leaves no newer
  choice.
- Rule A (at most 30 pages) favours shorter sections: 4 of the 15 first draws (3 in
  Precalculus, 1 in Intermediate Algebra) printed to 33-45 pages and were replaced, as was
  one replacement (53 pages); rule B then swapped a 30-page Precalculus section for a
  20-page one (section 4.3). Sections with long exercise sets are under-represented, and
  documents over 30 pages are not tested at all (the app refuses them).
- Uneven content: Intermediate Algebra contributes no figures, Introductory Statistics
  2.4 no equations; Precalculus and Intermediate Algebra hold 1,001 of the 1,369
  equations and 159 + 171 of the 509 headings, so pooled equation and heading numbers
  lean on two books. Per-book numbers are published for that reason.
- 399 of the 509 gold headings sit inside boxes (examples, notes, "Try It" exercises);
  heading numbers are mostly about box headings. `recall_by_context` is published.
- No calculus book: every OpenStax calculus volume is CC BY-NC-SA. Precalculus chapter 12
  (limits, continuity) is the nearest, and it was not drawn.
- One reader (Harish) labels everything; no second labeller, no screen-reader users.
- 15 sections: intervals will be wide; per-book numbers rest on 3 sections each.
- The judge is a model and is uncalibrated until the labels of 7.8 exist.
- The chatbot is run through the API, not a consumer chat app; app behaviour can differ.

## 14. Files

All under the `proof/` folder of this repository. `PREREG.sha256` holds the SHA-256 of
this file and of every file below (check with `sha256sum -c PREREG.sha256` from this
folder).

| Path | What |
|---|---|
| `PREREGISTRATION.md` | this file (read-only) |
| `heldout-gold/` | the answer key for the 15 sections, written by `a11y-stem gold fetch --allow-retired` on 2026-10-13: one JSONL per section, `_book.json` per book, `manifest.json` (per-file SHA-256), `ATTRIBUTION.md`. CC BY 4.0 OpenStax content |
| `selection/scan_used.py` | the development-use scan (section 2) |
| `selection/features.py`, `features.json` | eligibility and features of every numbered section of the five books (section 4.1, steps 1-3) |
| `selection/draw.py`, `draw.json` | the seeded draw and every tercile's queue (step 4) |
| `selection/budget.py`, `budget.json`, `budget.log`, `budget-stopped-unread.log` | the page rules, their result and log (step 5, sections 4.2-4.3) |
| `selection/rules-before-draw.sha256` | hashes of the rules taken before the draw ran, and each later change with its time |
| `selection/gold_summary.py`, `gold-summary.json` | the counts and the G1 rule of section 4.4 |
| `selection/section4.py` | writes section 4 from the files above |
| `selection/licenses-2026-10-13.csv` | `a11y-stem gold licenses` on 2026-10-13 |

The selection scripts read OpenStax content only through the archive cache and print or
store counts, slugs and titles; no page text. The held-out PDFs printed to count pages
are in `~/.cache/a11y-proof/prereg/pdfs/` (scratch; the run prints its own).
