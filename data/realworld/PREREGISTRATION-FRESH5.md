# Registration of the fifth fresh real-world split (fresh5): the candidate against the chatbot recipe

**Signed off by the owner on 2026-11-04.** The owner's decisions at signing are written into the
sections they change: the primary score (section 4), stratum D's private use (section 2 D), the slide
gates (section 2 C), the candidate commit and settings and the strong-prompt arm (section 3), and the
caps (section 6). This file and what it names are hashed with
`scripts/realworld_register.py --set fresh5` (`REGISTRATION-fresh5.sha256`), and that is merged to
`main` before the first request of any arm. No model had been run on any unit of this split when it
was signed. It follows `PREREGISTRATION-FRESH4.md` (used). What the set is and how its gold is made:
`docs/eval-realworld.md`.

## 1. The question

On documents that no earlier run, tuning step or registration has seen (arXiv papers, OpenStax
sections and course-style LaTeX including Beamer decks), does the candidate write more equations
right (as a screen-reader user hears them, section 4) than the Opus chatbot recipe, with fewer
wrong equations that nobody is asked about?
The candidate is ReadableSTEM with the equation changes built since fresh4, as it runs in production
since 2026-11-04; the control is the same code with those changes off, which is how production ran
before them (section 3). The set can detect a difference of about 3 points of
the primary (the per-unit SD of 0.048 was measured on fresh4's `exact_lenient`; 26 units, 80% power, alpha 0.025 with two comparisons);
a smaller one is not claimed either way. Stratum D (OpenStax chemistry, section 2 D) is outside that count and
is judged on its own chemistry measure (section 5, stratum D).

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

| Book | State | Section | Equations in the key | Letter pages |
|---|---|---|---|---|
| Introductory Business Statistics | retired | 5-2 The Uniform Distribution | 12 | 5 |
| Principles of Microeconomics for AP Courses | retired | 5-1 Price Elasticity of Demand and Price Elasticity of Supply | 14 | 5 |
| Principles of Economics 2e | retired | 5-4 Elasticity in Areas Other Than Price | 11 | 5 |
| Statistics | live | 12-2 The Regression Equation | 20 | 12 |
| College Physics | retired | 31-4 Nuclear Decay and Conservation Laws | 183 | 10 |
| Biology | retired | 39-2 Gas Exchange across Respiratory Surfaces | 30 | 6 |

All six are CC BY 4.0, 43 pages. They are printed to Letter PDF with the renderer of the held-out set
(`eval.render`, `scripts/fresh5_select.py openstax-render`); the SHA-256 of each printed PDF is in
`selection-fresh5-B.json`.

**C. Course-style LaTeX: 8 documents (3 Beamer decks), 36 PDF pages, 502 gold equations (49 display), CC BY (3),
CC BY-SA (2) or MIT (3).** The pool is the result of a fixed set of GitHub repository searches
(`pool-fresh5-course.json`, 446 repositories after the licence gate): for each of `license:cc-by-4.0`,
`license:cc-by-sa-4.0` and `license:mit` (GitHub has no 3.0 qualifier), `language:TeX`, not a fork, not
archived, at most 100 results by stars, the seven phrases "lecture notes", "lecture slides", "course slides",
"beamer slides", "beamer lecture", "beamer presentation" and "problem set", and the three topics `beamer`,
`beamer-slides` and `lecture-notes` (30 queries). The licence is GitHub's SPDX id and is read again when the
repository is fetched; only CC BY 3.0/4.0, CC BY-SA 3.0/4.0 and MIT pass, and anything else (non-commercial,
no-derivatives, copyleft software licences, none, "other") is refused. The repositories are taken in the seeded
order (SHA-256 of seed and name), their documents in the seeded order, at most 2 accepted and 6 tried per
repository, non-Beamer documents capped at 5 so that 3 Beamer slots stay open. Each document is compiled from its
source and the gold is made by the arXiv path (`arxiv_gold`) from the same source, so the PDF and the answer key
agree. Engines, in order: pdflatex and xelatex from a user-level TeX Live 2026 (latexmk), then
Tectonic 0.17.0 (`--untrusted`, cached packages only); every compile runs in a bubblewrap sandbox (no network, a minimal read-only root, the source as a discarded overlay,
no rc file, shell escape off, no environment from the parent, a mount namespace that shows it only the source, the TeX
Live tree and an output folder: `safe_tex.py`), and the first engine that
compiles the document wins; each document's engine and
version are in the manifest (`tex_engine`). Gates are those of the arXiv path (at most 10% unconvertible
equations, at least 4 usable pages, 4 to 6 pages drawn by hash) with a text floor of 300 characters per page (100
per Beamer slide) and 5 equations on a usable page (3 on a slide).

Changes to the rule while this file was a draft, all made before any system ran and before signing, each
recorded so the signed text can be read against them:

1. The first pool (180 repositories, 4 phrases) and Tectonic alone produced 5 documents and no Beamer deck, because
   103 of the Beamer sources did not compile in it (a package or a theme it cannot load); a compile failure is a
   limit of the tool, not a property of the document, so a TeX Live engine is tried first.
2. The pool was widened with the phrases "lecture slides", "course slides", "beamer lecture", "beamer
   presentation", the three topics, and 100 results per query instead of 50, and regenerated before signing. The
   pool changes with time on GitHub, so `pool-fresh5-course.json` is the registered pool and is not harvested again.
3. With TeX Live and the wider pool, the slide gates as first written (300 characters of text and 5 equations on a
   slide) were tried first. They gave 7 documents with 2 Beamer decks, both from one repository, and the pool was
   exhausted; 98 Beamer documents failed the text floor, because a slide carries little text (this observation is from a
   pass made before the compile sandbox below was added, and its log is not kept). The slide floors were then set to
   100 characters and 3 equations, which is the rule of the final draw (`candidates-fresh5-C.json`, 513 visits, all
   compiled in the sandbox: 8 accepted; the Beamer sources that still failed: 77 compile, 31 too few usable pages, 18
   no equations, 13 page count outside 6 to 200, 3 text floor, 2 unconvertible). The notes' gates did not change. The
   relaxation is a choice made to fill the Beamer slots, and the report states it. At signing the owner
   kept the relaxed rule (100 characters and 3 equations on a slide) rather than a strict redraw.
4. A review found that compiling downloaded TeX unsandboxed could run code (an rc file) or read local files (an absolute
   `\input`), so every unpacking and compile now goes through `safe_tex.py` (archives with links, unsafe paths or
   more than 20,000 files or 400 MB are refused, which skipped 1 repository; engines run in a bubblewrap sandbox
   with no network, `latexmk -norc`, shell escape off, a clean environment; lualatex was dropped from the
   engine list). The whole draw was repeated from fresh downloads under it; it gave the same 8 documents.

Accepted (`manifest-fresh5c.json`, with the commit each was read from and the engine):

| Document | Licence | Style | Pages | Gold equations | Engine |
|---|---|---|---|---|---|
| AlbertEMC2Stein/ComputationalGeometry `main` | MIT | notes | 17, 27, 41, 50 | 158 | pdflatex |
| agormp/evolintro `evolintro` | CC BY-SA 4.0 | notes | 11, 14, 18, 29 | 46 | pdflatex |
| fairyflies9/Fluid-Dynamics-and-IBM-Theory `1-3-Integral-Transform` | CC BY 4.0 | Beamer | 19, 26, 32, 36 | 12 | pdflatex |
| fairyflies9/Fluid-Dynamics-and-IBM-Theory `5-2-Intro-RANS-Part2` | CC BY 4.0 | Beamer | 14, 15, 16, 23 | 37 | pdflatex |
| gmarciani/research `computer-security/main` | MIT | notes | 9, 16, 21, 22 | 87 | Tectonic |
| ondrejch/PHANES-course `mod/04/lecture04` | MIT | Beamer | 7, 10, 16, 18 | 15 | pdflatex |
| robmelfi/PatternRecognitionLectureNotes `book` | CC BY 4.0 | notes | 20, 21, 23, 39, 94, 102 | 105 | xelatex |
| sim1-99/medical-physics-notes `main` | CC BY-SA 4.0 | notes | 12, 25, 47, 68, 82, 102 | 42 | pdflatex |

Two Beamer decks come from one repository (the per-repository cap of 2). The licence of a repository does not cover
third-party material inside a document; the gold is equations only.

**D. OpenStax chemistry: 3 sections (1 CC BY, 2 CC BY-NC-SA used privately), 24 Letter pages, 115 chemistry gold equations, reported apart and not pooled.**

*Licence handling.* The service is free and non-commercial. The pool has three tiers, and the licence of every book
whose slug has "chemistry" is read live, in the archive and in the CMS:

- Tier 0, public: the CC BY first editions (Chemistry, Chemistry: Atoms First; both retired, allowed as for stratum B).
- Tier 1, private: Chemistry 2e and Chemistry: Atoms First 2e (CC BY-NC-SA 4.0 in both places).
- Tier 2, private, only if the first two do not give enough: Organic Chemistry (CC BY-NC-SA 4.0).

A CC BY-NC-SA section is a private measurement. Its printed PDF, its answer key and the model answers about it stay in
the cache outside the repository (`gold/fresh5d-private`) and are never committed or published; the repository holds
only section ids, titles, counts and hashes (`selection-fresh5-D.json`). What is published about those sections is
aggregate numbers (rates over the stratum, with their sample sizes), with the source named (OpenStax, Rice University)
and the licence named (CC BY-NC-SA 4.0). No book outside the three tiers, and no other non-commercial or
no-derivatives licence, is touched. At signing the owner accepted this private use of the two CC BY-NC-SA
sections, and kept 17.3 (the topic rule matches section titles, as written).

*Rule.* A section is out if any earlier set used it (the same rule as stratum B, with the 2e editions folded into the
first editions they are the second edition of), if a section with its title was used, if it could be in the sealed check
part of dev-v2 (the names are not recorded, so every section of that set's chemistry book under the registered hash
rule counts, a superset), or if its title is on a topic the development and held-out chemistry sets cover
(balancing, precipitation, galvanic cells, decay, Nernst, rate laws, Brønsted acids, nuclear equations, measurement,
thermodynamics, noble gases, representative metals, Lewis structures, titrations, coordination compounds; the
topic test is a match on the section title). Of 762 sections in the 5 books, 410 were out as used (by name or by
title) or possibly sealed and 9 on topic, leaving 343. Those are ranked by tier and then by SHA-256 of
`fresh5-D|<book>|<section>` and walked in that order, taking each that passes four automatic gates: at least 25
gold equations, at least 40% of them chemistry (a reaction arrow, a state symbol, a formula of two or more element symbols
or an ion; a lone multiplication sign in the key is counted in neither number), a word 8-gram overlap of at most 20% with
the text of every used or sealed section and of the sections already taken (so a section of a 2e edition that repeats a
used first-edition section is measured against it), and at most 15 printed Letter pages. The walk stops at 4 sections
holding at least 100 chemistry gold equations, and never takes more than 8. The gates were fixed before the draw and are
not changed after it. All 343 candidates were visited and the walk ran out before it reached 4 sections: 3 passed, 327 had
too few equations, 4 too small a chemistry share, 9 too much overlap (`selection-fresh5-D.json`, with every gate result).
**The rule filled 3 of its 4 places** and reached 115 chemistry gold equations; no gate is loosened to add another. Pages are
pinned to archive release 20260604.144757, printed to Letter PDF with the renderer of stratum B, and the SHA-256 of each PDF and answer key is
recorded. The answer keys are in the cache outside the repository; no gold file has been opened or read, only the gates' counts.

| Book | Licence | Section | Equations in the key | Letter pages |
|---|---|---|---|---|
| Chemistry | CC BY 4.0 | 2.6 Molecular and Ionic Compounds | 35 (33 chemistry) | 11 |
| Chemistry 2e (private) | CC BY-NC-SA 4.0 | 17.3 Electrode and Cell Potentials | 51 (46 chemistry) | 7 |
| Chemistry 2e (private) | CC BY-NC-SA 4.0 | 17.1 Review of Redox Chemistry | 37 (36 chemistry) | 6 |

Totals of strata A to C: 26 units (12 + 6 + 8), 131 PDF pages (52 + 43 + 36). Stratum D adds 3 units and 24 pages, and
every arm runs on it exactly as on the others (the arms table below, the same recipes, settings and caps rules).

## 3. Arms

All Claude, one run each, one machine, every model answer recorded.

| Arm | What runs |
|---|---|
| **Chatbot, Haiku** | `chatbot-haiku-purdue`, `proof.chatbot.record_arms` unchanged: `claude-haiku-5-5`, the PDF, a 150 dpi PNG of every page, the Purdue sentence verbatim, no system prompt |
| **Chatbot, Opus** | `chatbot-opus-purdue`: the same on `claude-opus-5-5` |
| **Chatbot, Opus, strong prompt** | `chatbot-opus-expert`: the same inputs on `claude-opus-5-5` with the `expert` prompt of `src/readablestem/proof/chat_prompts.json` verbatim instead of the Purdue sentence (the file's SHA-256 is `9b7a20b88889c2401b5c76c2ca41e95419e29107ea57ce7aeb61ad0c80c87d54`; it asks for one HTML file with presentation MathML for a screen-reader user), one reply per unit, no system prompt. A reply that stops at the output limit or ends with "CONTINUED IN NEXT REPLY" is scored as it is (status `truncated`) and not continued, as for the other arms. Added at signing on the owner's approval (2026-11-03) so that the baseline is not only the one-sentence recipe |
| **Control** | ReadableSTEM at commit `949cb1a677ea85517dab80ae1675153a614b437e` (pipeline 0.5.9, `src` tree `6a22753803dd772c7177d5406a09fd479685aa97`) through the document task: provider anthropic, main `claude-opus-5-5` effort medium, cheap `claude-haiku-5-5` effort low, `FEATURE_TABLES=true`, `FEATURE_NOTATION_MEMORY=true`, `EQUATION_LINE_CONTEXT=true` (on in production since fresh4); the equation settings built since fresh4 off: `EQUATION_GLYPH_CHECKS=false`, `FLAG_POLICY=false`, `EQUATION_GLYPH_RECONCILE=false` |
| **Candidate** | the same commit and `src` tree with the production settings live since 2026-11-04 (deployed at `949cb1a`): as the control plus `EQUATION_GLYPH_CHECKS=true`, `FLAG_POLICY=true`, `EQUATION_GLYPH_RECONCILE=true` |

Both ReadableSTEM arms, every other setting at the code's default and as in production: `FEATURE_PAGE_WITNESS`
off, `LAYOUT_DISPLAY_GROUPING` off, `FEATURE_PAGE_MATH` off, the second automated check off (it is shadow-only
and changes no output, as in every earlier registered run). `FEATURE_IMAGE_PAGES` and `FEATURE_LONG_DESCRIPTIONS`
are on in production but act only when a person asks for them on a job (image pages, long figure
descriptions), which the document task does not; they are left at their defaults. The settings are passed to
each run in a clean environment that holds only them and the API key, so no deployed variable reaches it.
The production values were read from the deployment on 2026-11-04 and checked against `config.py`'s defaults
and `docs/eval-dev-baseline-2026-11-03.md` ("Reading push").

The control and the candidate share recorded answers, so a request they have in common is answered once
(the candidate's run starts from the control's answers). No `src/` file changes between the first request
of the first arm and the end of the last: every arm runs from the `src` tree above; the runner and report
scripts (`scripts/realworld_run.py`, the report) gain only what reads this split's four strata and the
strong-prompt arm, and change no scoring. No setting changes after the first request. The chatbots run
first; their output is not read until both ReadableSTEM arms are recorded.

## 4. Metrics

Per unit, by the document task's scorer (`eval/pipeline_eval.document_page_item`; the chatbots' output by
`proof/systems.equation_item`), over every gold equation, aligner unchanged, reading B (each formula in a
rebuilt table's cells counts as one inline equation).

- **Primary: spoken-equal, end to end, reading B, pooled over units.** An aligned equation counts as right when
  its tree equals the gold's (`exact_lenient`) or when the speech rules say the prediction and the gold in the
  same words (`scripts/rw_spoken.py`, run as `realworld_report.py --spoken`; the speech is
  `pipeline.speech.mathml_to_speech` with no course readings, and both sides are written one way for prints that
  look alike by `pipeline/math_canonical.py`: `repair_converter_gaps` and `speakable_latex`). The same scorer,
  aligner and gold apply to every arm. Reason, the owner's: the product is for people who read mathematics
  through a screen reader, so the score that matters is what such a reader hears, and two spellings that sound
  and print alike are not an error. `exact_lenient` is reported beside it as a secondary.
  OpenStax gold (strata B and D) is MathML with no LaTeX, so it has nothing to fold: there the gold is spoken
  from its MathML as it is and compared with the prediction as written. A prediction written as MathML
  (a chatbot that answers in `<math>`, as the strong prompt asks) is spoken from its MathML and compared with
  the gold as written. Neither is folded then (`spoken_exact_mathml_gold` and the markup case of
  `spoken_exact` in `scripts/rw_spoken.py`, added before signing; the rest of the scorer is unchanged).
- **Silent errors:** wrong equations nobody is asked about, per 100 gold, under the primary.
- **Flag recall:** of the aligned equations whose symbols are wrong, the share that were required decisions;
  and required decisions per page.
- **Unseen loss:** silent wrong equations plus undetected ones, per 100 gold.
- **Cost per page** from the recorded usage.
- Secondary, reported and not the verdict: `exact_lenient`, notation-neutral (`scripts/rw_notation_neutral.py`),
  symbols credited, each per stratum and for display, inline and table maths, and invented equations.

The spoken scorer is fixed before signing: `scripts/rw_spoken.py`, `pipeline/math_canonical.py` and the speech
files it asks (`pipeline/speech.py`, `speech_chem.py`, `speech_nuclides.py`, `speech_units.py`) are hashed with
this registration (`FRESH5_FILES` in `scripts/realworld_register.py`), with `gold/arxiv_gold.py`, the gold's
converter step. The fix is measured on the development papers
(`docs/eval-realworld.md`, "The spoken-equal scorer"): 68 of 71 equations that print as the gold does but were
spoken differently became equal, every arm's score moved by about 3 points, and no pair that prints differently became equal.
The scorer's converter (`equation_step.clean_latex`, `validate.safe_latex_to_mathml` and the modules that shape
its MathML: `core/latex_groups.py`, `core/mathchars.py`, `pipeline/chem.py`, `chem_mathml.py`,
`mathml_shape.py`, and the speech helpers `core/mathml_intent.py`, `core/notation.py`, `eval/equations.py`) is
the one at the signed candidate commit and is hashed with the rest, so gold and every arm are converted and
spoken by the same code. Neither the scorer, the converter nor the speech rules change after the first request.

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

Against Haiku and against the strong-prompt Opus arm the same primary comparison is reported, with the
wording rule below. A paired comparison is over the units both arms answered; when a cap stopped an arm
(section 6), the units it did not reach are named and the comparison is made on the rest. For a pair "beats" is written only on the rule in
criterion 1; otherwise "higher by point estimate", "no difference this set can detect" or "lower". A result that
fails is reported as it is, and the launch rule stands; no setting, scorer, gold or unit changes on this split's
account.

### Stratum D: chemistry

Chemistry gold is scored by what the chemistry says, not by the shape of its markup (`eval/equations_chem.py`, the scorer
`chem`), so `exact_lenient` does not decide D and D is not part of criterion 1 above. The numbers published for D, each with
its sample size (gold equations and sections):

- **Primary: chemically right**, end to end (`exact_chem` or an earlier rung), over the chemistry gold **excluding the lone
  multiplication sign** (`chem_right_excl_times`). The lone `×` is a layout mark that the answer keys carry as an equation;
  it is reported separately, and the number with it included (`chem_right`) is printed beside the primary one, so neither
  replaces the other. The scorer counts every gold equation of the three chemistry sections (123; the 115 above are the
  ones the selection gate counted as chemistry, and the sections have no lone `×`).
- **Silent harm:** a chemistry equation that is wrong, was not asked about and is not "input" (`silent_harm_summary`), per 100 gold.
- Secondary, reported and not the verdict: `exact_lenient`, the symbols scorer, table cells found, flag recall, the notation
  scorer, each per section.

Stratum D is called a success only if every one of these holds, all set before any model runs on it:

1. The candidate is chemically right on at least **0.80** of the chemistry gold (excluding the lone `×`).
2. The candidate is at least **5 points** above the control (current production) on the same measure.
3. The candidate is at least the Opus chatbot as a point estimate.
4. Silent harm is at most **1 per 100** gold equations.

Otherwise D is reported as it is, with the same wording rules as section 5 ("higher by point estimate", "no difference this
set can detect", "lower"). D is published whichever way it falls.

**What D can and cannot show.** With 115 chemistry gold equations in 3 sections, the 95% interval around a rate of 0.80 is about
plus or minus 0.07, and one silent harm in the stratum is about 0.9 per 100. D is evidence about these sections; it is not an
estimate for chemistry teaching material, and criterion 2 is a point-estimate rule (a paired interval over 3 sections
is too coarse to be a test). Two of the three sections are CC BY-NC-SA and are used privately, so their answers and
answer keys cannot be published; a reader can check the stratum's aggregates and the selection by the rule but not the
per-section content of those two.

## 6. Budget

Caps on this split (stratum D included; it adds no cap of its own), kept in `spend-log.jsonl`, set by the
owner at signing: control and candidate $7.5 together, Opus chatbot $10, Haiku chatbot $0.3, strong-prompt Opus
chatbot $9. **Total $26.80.** Spend on the dev runs of this phase before signing (`spend-log.jsonl` since
2026-11-03) was $13.11. Each arm stops at its cap and is reported as partial; a cap is not raised without asking.
Small trial runs come first and count toward the caps: offline runs that spend nothing, then one short unit per
arm, whose answers are kept and not asked again.

The chatbot arms take the units in one registered order, so a cap cuts them the same way: the 26 units of
strata A to C first, ordered by SHA-256 of `realworld-fresh5-2026-11-02|chat-order|<unit id>` (the unit id is
`<book>/<page>` of its gold, for example `arxiv/2303.13230`), then the 3 units of stratum D in the same kind of
order; a unit whose worst case (its counted input plus the full output limit) no longer fits what is left of the
cap is not run and is named. The ReadableSTEM arms run stratum by stratum (A, B, C, D), the control before the
candidate. A unit the trial run answered is not asked again. The recorded per-page cost of the Purdue recipe on
earlier splits (about $0.087 a page on Opus, $0.0023 on Haiku) puts the Opus chatbot's 155 pages near $13.5 and
Haiku's near $0.36, above their caps, so both are expected to stop before the end of the order; the ReadableSTEM
arms are expected near $5. The API credit balance is checked before each paid step. Nothing is run twice to
confirm a number; a crash is repeated only for the jobs that did not finish, and named.

## 7. What is published, and what it will not say

Published whichever way the result falls: this registration (final text and hash), the manifests and candidate
logs, every recorded answer, the report in `docs/` and the README and `PROOF.md` rows. The Opus chatbot runs the
Purdue recipe as it is, which critics may call a weak baseline. It will not say whether a wrong equation changes the meaning, whether a person would accept the page,
anything about scans, Word files or handwriting, or about maths in embedded figures. One run per arm gives no
run-to-run variation. arXiv gold is dominated by table cells and the authors' `$` boundaries, so a win there may
not carry to course materials, which is why the strata have a floor and are reported apart. Stratum D is three sections of one textbook family
(two of them second-edition text used privately), so it says nothing about chemistry courses in general. The Beamer
slide gates were relaxed during the draft to fill the Beamer slots (section 2 C), which the report states. Two of
the three Beamer decks share a repository, and a deck's maths letters sit in a text font, so glyph evidence is weak
there.
