# Registration of the fifth fresh real-world split (fresh5): the candidate against the chatbot recipe

**DRAFT. UNSIGNED. NOT HASHED.** Nothing in this file is registered until Harish signs it off. Until
then `REGISTRATION-fresh5.sha256` does not exist, the sections marked "to settle before signing" in
section 8 are open, and no model has been run on any unit of this split. On signing, section 8 is
deleted, the candidate commit and settings are written into section 3, the file is hashed with
`scripts/realworld_register.py --set fresh5`, and that is committed before the first request of
any arm. It follows `PREREGISTRATION-FRESH4.md` (used). What the set is and how its gold is made:
`docs/eval-realworld.md`.

## 1. The question

On documents that no earlier run, tuning step or registration has seen (arXiv papers, OpenStax
sections and course-style LaTeX including Beamer decks), does the candidate write more equations
exactly right than the Opus chatbot recipe, with fewer wrong equations that nobody is asked about?
The candidate is ReadableSTEM with the equation changes built since fresh4; the control is ReadableSTEM
as it runs in production today. The set can detect a difference of about 3 points of
`exact_lenient` (per-unit SD 0.048 from fresh4, 26 units, 80% power, alpha 0.025 with two comparisons);
a smaller one is not claimed either way.

## 2. The units

Selected by rule and seed (`scripts/fresh5_select.py`, seed `realworld-fresh5-2026-11-02`), never by
choice: a unit that fails an automatic gate is skipped and logged, and nothing is swapped, added or dropped
afterwards. Gold, downloads and compiled PDFs are in the cache outside the repository; no gold file has
been opened or read, only the gates' counts.

**A. arXiv LaTeX: 12 papers, 52 PDF pages, 943 gold equations (103 display), all research style, CC BY 4.0.**
The order of the fourth fresh split continued: `order-realworld-fresh4-2026-10-26.json` was visited for
its first 37 candidates (`candidates-fresh4.json`); this stratum starts at the next one
(`order-realworld-fresh5-2026-11-02-A.json`, nothing re-ordered) with the same gates and page rule as
before (`scripts/realworld_build.py`). 28 candidates were visited: 12 accepted, 10 fewer than 4 usable
pages, 2 more than 10% unconvertible equations, 1 page count outside 8 to 120, 1 source that is a PDF, 1 no
main `.tex`, 1 no equations (`candidates-fresh5-A.json`). Papers (SHA-256 of the sorted ids joined by
newlines `cbb1c30208ab1d7b23c40b1f669faa7428bfa58403fa7a4b0a342ddfeed49732`): 2303.13230,
2306.03073, 2401.11352, 2405.05818, 2406.12076, 2406.15398, 2407.08468, 2408.10825, 2410.14431, 2501.16660,
2503.17870, 2505.04942 (`manifest-fresh5a.json`).

**B. OpenStax: 6 sections, CC BY (plain CC BY in both the archive and the CMS).** Books with a CC BY licence
in both places; retired editions are allowed as for the held-out set
(`proof/DECISION-retired-editions-2026-10-13.md`). A section is out if any earlier set used it (the held-out
registration, dev-v2, chem-heldout, the tables and handwriting sets, `proof/`, the earlier real-world splits:
every `openstax/<book>/<section>` or `<book>__<section>` they name), if a section with the same title was used
(the same text in another book), or if its book family is a sealed one (the books of dev-v2, whose check part is
sealed). Sibling editions and shared-text books are one family. Sections are ranked by SHA-256 of seed, book and
section; books are taken in turn in the seeded order, at most 2 per family, and a section needs at least 10
equations in its answer key. 294 section gates were run (`selection-fresh5-B.json`, which also lists the
refused books and every gate result). The pages are pinned to archive release 20260604.144757.

| Book | State | Section | Equations in the key |
|---|---|---|---|
| Introductory Business Statistics | retired | 5-2 The Uniform Distribution | 12 |
| Principles of Microeconomics for AP Courses | retired | 5-1 Price Elasticity of Demand and Price Elasticity of Supply | 14 |
| Principles of Economics 2e | retired | 5-4 Elasticity in Areas Other Than Price | 11 |
| Statistics | live | 12-2 The Regression Equation | 20 |
| College Physics | retired | 31-4 Nuclear Decay and Conservation Laws | 183 |
| Biology | retired | 39-2 Gas Exchange across Respiratory Surfaces | 30 |

All six are CC BY 4.0. The sections are printed to PDF with the renderer of the held-out set
(`readablestem eval render`); their page counts and the hashes of the printed PDFs go into the manifest
before signing (to settle, section 8).

**C. Course-style LaTeX: 5 documents of the 8 planned, 26 PDF pages, 372 gold equations (38 display), no
Beamer deck.** The pool is the result of a fixed set of GitHub repository searches
(`pool-fresh5-course.json`: six topic phrases, each against `license:cc-by-4.0`, `license:cc-by-sa-4.0` and
`license:mit`, `language:TeX`, not a fork, not archived, at most 50 results each, by stars; 255
repositories after the licence gate). The licence is GitHub's SPDX id and is read again when the repository
is fetched; only CC BY 3.0/4.0, CC BY-SA 3.0/4.0 and MIT pass, and anything else (non-commercial,
no-derivatives, copyleft software licences, none, "other") is refused. The repositories are taken in the
seeded order (SHA-256 of seed and name), their documents in the seeded order, at most 2 accepted and 6 tried per
repository, non-Beamer documents capped at 5 so that 3 Beamer slots stay open. Each document is compiled from
its source with Tectonic 0.17.0 (`--untrusted`, no shell escape), so the PDF and the answer key come from the
same source, and the gold is made by the arXiv path (`arxiv_gold`) with its gates; for a Beamer deck the floor
is 100 characters of text per slide (300 for notes) and 3 equations on a usable slide (5 for notes). 579 visits
(`candidates-fresh5-C.json`): 5 accepted; the pool was exhausted. Accepted (`manifest-fresh5c.json`, with the
commit each was read from):

| Document | Licence | Pages | Gold equations |
|---|---|---|---|
| agormp/evolintro `evolintro` | CC BY-SA 4.0 | 11, 14, 18, 29 | 46 |
| gmarciani/research `computer-security/main` | MIT | 9, 16, 21, 22 | 87 |
| qubd/SN1_Notes `201-SN1_Notes` | CC BY-SA 4.0 | 5, 11, 13, 16, 82, 119 | 95 |
| robmelfi/PatternRecognitionLectureNotes `book` | CC BY 4.0 | 20, 21, 23, 39, 94, 102 | 105 |
| sim1-99/medical-physics-notes `main` | CC BY-SA 4.0 | 25, 51, 60, 69, 102, 104 | 39 |

Of the 164 Beamer documents visited, none accepted, 103 did not compile with Tectonic (a package or a theme it cannot load), 24 had
fewer than 4 usable pages, 17 a page count outside 6 to 200, 11 no equations, 5 too little text and 4 too
many unconvertible equations. The stratum is therefore short by 3 documents and by the Beamer requirement
(to settle, section 8). The licence of a repository does not cover third-party material inside a
document; the gold is equations only.

As the pool stands: 23 units (12 + 6 + 5), 78 PDF pages in A and C plus the pages of B.

## 3. Arms

All Claude, one run each, one machine, every model answer recorded.

| Arm | What runs |
|---|---|
| **Chatbot, Haiku** | `chatbot-haiku-purdue`, `proof.chatbot.record_arms` unchanged: `claude-haiku-5-5`, the PDF, a 150 dpi PNG of every page, the Purdue sentence verbatim, no system prompt |
| **Chatbot, Opus** | the same on `claude-opus-5-5` |
| **Control** | ReadableSTEM at the commit that holds this registration with the production settings (document task, provider anthropic, main `claude-opus-5-5` effort medium, cheap `claude-haiku-5-5` low, `FEATURE_TABLES=true`, `FEATURE_NOTATION_MEMORY=true`) and every new equation setting off |
| **Candidate** | the same code with the new equation settings on: **(to settle before signing: commit SHA and the exact settings, fixed by the development runs; no setting changes after the first request)** |
| **Chatbot, Opus, strong prompt** (optional) | the Opus chatbot with a prompt that asks for every formula, tables and captions included: **only if Harish decides to include it (about +$9)** |

The control and the candidate share recorded answers, so a request they have in common is answered once.
No `src/` file changes between the first request of the first arm and the end of the last. The chatbots run
first; their output is not read until both ReadableSTEM arms are recorded.

## 4. Metrics

Per unit, by the document task's scorer (`eval/pipeline_eval.document_page_item`; the chatbots' output by
`proof/systems.equation_item`), over every gold equation, aligner and `exact_lenient` unchanged, reading B
(each formula in a rebuilt table's cells counts as one inline equation).

- **Primary: `exact_lenient`, end to end, reading B, pooled over units.**
- **Silent errors:** wrong equations nobody is asked about, per 100 gold.
- **Flag recall:** of the aligned equations whose symbols are wrong, the share that were required decisions;
  and required decisions per page.
- **Unseen loss:** silent wrong equations plus undetected ones, per 100 gold.
- **Cost per page** from the recorded usage.
- Secondary, reported and not the verdict: notation-neutral (`scripts/rw_notation_neutral.py`), spoken-equal,
  symbols credited, each per stratum and for display, inline and table maths, and invented equations.

## 5. Analysis and success criteria

Cluster bootstraps over the units (`proof/stats.cluster_bootstrap`, 10,000 resamples, seed 0), paired for
differences, and the sign-flip test on per-unit differences. Intervals are 97.5% (k = 2 comparisons).

The claim "ReadableSTEM beats the chatbot baseline on its equation evaluation" is made only if all five hold:

1. **Primary.** Candidate minus Opus chatbot on the primary metric is positive, its 97.5% paired cluster-bootstrap
   interval excludes 0, the sign-flip p is below 0.025, and no stratum's point estimate is below -2 points.
2. **Silent errors.** At most 4.0 per 100 gold, at most half the Opus chatbot's, and not above the control's by
   more than 0.5.
3. **Flag recall.** At least 0.60, with at most 4.0 required decisions per page.
4. **Unseen loss.** At most the Opus chatbot's, so the claim cannot rest on missing equations.
5. **Cost.** At most $0.07 per page on this split.

Against Haiku the same primary comparison is reported. For a pair "beats" is written only on the rule in
criterion 1; otherwise "higher by point estimate", "no difference this set can detect" or "lower". A result that
fails is reported as it is, and the launch rule stands; no setting, scorer, gold or unit changes on this split's
account.

## 6. Budget

Caps on this split, kept in `spend-log.jsonl`: control and candidate $7.5 together, Opus chatbot $10,
Haiku chatbot $0.3, strong-prompt arm $9 if included. Each arm stops at its cap and is reported as partial; a cap is
not raised without asking. The API credit balance is checked before each paid step. Nothing is run twice to
confirm a number; a crash is repeated only for the jobs that did not finish, and named.

## 7. What is published, and what it will not say

Published whichever way the result falls: this registration (final text and hash), the manifests and candidate
logs, every recorded answer, the report in `docs/` and the README and `PROOF.md` rows. The Opus chatbot runs the
Purdue recipe as it is, which critics may call a weak baseline; the optional strong-prompt arm exists for that
reason. It will not say whether a wrong equation changes the meaning, whether a person would accept the page,
anything about scans, Word files or handwriting, or about maths in embedded figures. One run per arm gives no
run-to-run variation. arXiv gold is dominated by table cells and the authors' `$` boundaries, so a win there may
not carry to course materials, which is why the strata have a floor and are reported apart. The Beamer
requirement and the OpenStax pages are open (section 8); with fewer than the planned units the detectable
difference is larger than 3 points, and the report says so.

## 8. To settle before signing (delete on signing)

1. **Stratum C is short.** 5 documents, no Beamer deck, the pool exhausted. Options for Harish: widen the pool with
   more queries; compile with a pinned older Tectonic bundle, since 103 of the Beamer sources failed in the current
   one; relax the slide gates further; or run fresh5 with 5 course documents and no Beamer, adding Beamer decks as
   fixtures with gold instead. Whatever is chosen is decided before any system runs and is written here.
2. **Stratum B:** print the six sections, record their page counts and PDF hashes, add them to a manifest.
3. **Candidate commit and settings;** whether the strong-prompt arm is included; the cap split.
4. **The hash file** (`REGISTRATION-fresh5.sha256`) is written only after 1 to 3, by Harish's decision.
