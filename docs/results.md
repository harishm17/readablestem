# Results

Every measured result of ReadableSTEM, with what was run, on what, and what the number leaves
out. The [README](../README.md) has the short version; [eval.md](eval.md) has the method and
the definitions; each result's own report is linked from its section.

| Result set | Run on | Pipeline | Seen before the run? | Report |
|---|---|---|---|---|
| [fresh5](#latest-registered-result-fresh5-2026-11-04): 26 documents, 1,715 equations | 2026-11-04 | 0.5.9 | No: registered, run once; strata A to C read again at 0.5.23 (2026-11-27) to decide the flag policy ([eval-flag-policy.md](eval-flag-policy.md)) | [eval-equation-fresh5.md](eval-equation-fresh5.md) |
| [First registered held-out run](#first-registered-held-out-result-2026-10-14): 15 OpenStax sections, 242 pages | 2026-10-14 | 0.4.0 | No: registered | [PROOF.md](../PROOF.md) |
| [Re-measurement on the same 15 sections](#re-measurement-on-the-same-sections-2026-10-21-not-a-blind-result) | 2026-10-21 | 0.5.0 and later | Yes: not a blind result | [remeasurement-2026-10-21.md](remeasurement-2026-10-21.md) |
| [Scanned and handwritten pages](#scanned-and-handwritten-pages-converted-on-request-2026-10-21) | 2026-10-21 and later | 0.5.0 and later | Partly: said in the section | [eval-image-pages.md](eval-image-pages.md), [eval-handwriting.md](eval-handwriting.md) |
| [Development sample](#development-sample-pipeline-040-2026-10-12): 3 OpenStax sections | 2026-10-12 | 0.4.0 | Yes: the rules were tuned on it | [eval.md](eval.md) |

Other reports (real papers from arXiv, chemistry, tables, the parsing benchmark) are listed in
the [documentation index](README.md).

There are two kinds of number here, and they must not be mixed. The first kind is measured
on documents the system had never been run on, with the protocol written down before the
run. The second kind comes from the sample on which the checks were developed. The scan
numbers and the re-measurement below are neither, and say so where they appear.

## Latest registered result: fresh5 (2026-11-04)

The newest held-out result is the fifth fresh split, run once on 2026-11-04 under
[`data/realworld/PREREGISTRATION-FRESH5.md`](../data/realworld/PREREGISTRATION-FRESH5.md) (hash in
`data/realworld/REGISTRATION-fresh5.sha256`, merged to `main` before the first request). It tests
equations only, on ReadableSTEM pipeline 0.5.9 (the result file did not record its settings, and
some of the live service's settings have changed since): 26
documents (12 arXiv papers, 6 OpenStax sections, 8 course LaTeX documents, 3 of them Beamer
decks), 131 pages, 1,715 gold equations. Report: [docs/eval-equation-fresh5.md](eval-equation-fresh5.md);
[PROOF.md](../PROOF.md) is the replayed report of the first registered result, below.

| Strata A to C, 26 documents, 1,715 gold equations | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku |
|---|---|---|---|
| Spoken-equal, end to end (primary) | 0.840 [0.791, 0.876] | 0.836 [0.778, 0.878] | 0.816 [0.750, 0.860] |
| Exact match (secondary) | 0.793 [0.736, 0.834] | 0.770 [0.704, 0.824] | 0.732 [0.657, 0.793] |
| Cost per page | $0.0222 | $0.0681 | $0.0017 |

**Verdict, as registered:** on the primary score ReadableSTEM and the Opus chatbot show **no
difference this set can detect** (+0.004, 97.5% interval [-0.023, +0.036]); the set was powered
for about 3 points. The registered claim that ReadableSTEM beats the chatbot baseline on
equations is **not made**. How many wrong equations pass without a decision is being scored
again: the first scoring counted differences in notation that do not change what is read (a hand
check of the 86 errors found 55 of that kind), so this page states no figure for it; the report
keeps the first scoring as a record. Stratum D (3 OpenStax
chemistry sections, 123 gold equations) is not a success (0.333 chemically right against 0.80),
and its comparison with the chatbots is not measured. One run per arm; the speech is
ReadableSTEM's own rules, not a particular screen reader's; nothing here covers scans,
handwriting, Word files or math inside figures. Recorded spend $21.98.

## First registered held-out result (2026-10-14)

> **Superseded for the current system, not for this run.** The table and text below are the
> registered held-out results of the system as it was on 2026-10-14 (pipeline 0.4.0) and are kept as
> they were. The system has changed since; a re-measurement on the same sections, which is **not a
> blind result**, follows ("Re-measurement on the same sections, 2026-10-21").

Fifteen sections of five OpenStax books (242 Letter pages, 1,369 equations, 509 headings,
69 figures with an OpenStax alt text), chosen by a rule and registered before any system ran.
The full report, with every table, interval and per-section number, is [PROOF.md](../PROOF.md);
it is replayed from the recorded model answers committed in
[`proof/bundles/heldout-2026-10/`](../proof/bundles/heldout-2026-10/) (`git checkout proof-heldout-2026-10 && make proof`, no key, no
network, the same bytes every time). Intervals are 95% cluster bootstraps over the 15 sections,
which are approximate with this few and tend to be a little narrow.

**What was run, and how it differs from the registration.** The OpenAI account ran out of
credit during the first attempt (Addendum 3), and every model call of this run went to Claude
instead ([Addendum 6](../proof/PREREGISTRATION-ADDENDUM-6.md), written before any model saw a
held-out section; [Addendum 7](../proof/PREREGISTRATION-ADDENDUM-7.md) records what happened
during the run). ReadableSTEM ran with `claude-haiku-5-5` (cheap) and `claude-opus-5-5` (main), the
production settings otherwise, and no second automated check (that is OpenAI's). The chatbot
recipe is Purdue's one-sentence prompt, verbatim, on `claude-opus-5-5` (two runs) and on
`claude-haiku-5-5` (two runs); the registration's expert-prompt arm was not run. Figures are
judged by `claude-opus-5-5`. The open parsers are the ones recorded earlier, with no model
(Docling in its default settings, MinerU at its basic tier, PyMuPDF4LLM; the registered Docling
arm with formula recognition was not carried out). The configuration was chosen on the
development sample and frozen before the run. Nothing was tuned on these sections.

Recorded model spend: ReadableSTEM $10.88 (its figure judge included), the opus chatbot arm
$22.34 and its judge $0.72, the haiku arm $1.49 and its judge $0.78: $36.21, under the $45
ceiling. About $1.3 more went to an interrupted run that was made again, and $1.08 to the
development-sample checks that priced the run (Addendum 7).

**The first registered run in full (2026-10-14, version 0.4.0).** These numbers are the record of that run. The current system is measured in fresh5, above.

| Held-out, 15 sections | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku | Docling | MinerU (basic) | PyMuPDF4LLM |
|---|---|---|---|---|---|---|
| Equations right, end to end (document task) | 0.504 [0.376, 0.599] | 0.622 [0.464, 0.727] | 0.600 [0.437, 0.708] | 0 | 0.174 [0.108, 0.218] | 0 |
| Wrong equations nobody is asked about, per 100 | 23.3 [19.0, 29.5] | 24.0 [16.1, 33.4] | 25.2 [18.2, 34.6] | 0 (writes none) | 27.6 [22.2, 34.7] | 0 (writes none) |
| Heading F1 | **0.961** [0.947, 0.977] | 0.932 [0.891, 0.958] | 0.914 [0.872, 0.945] | 0.910 [0.842, 0.958] | 0.923 [0.894, 0.955] | 0.830 [0.738, 0.919] |
| Heading level right (among matched) | **1.000** | 0.341 | 0.508 | 0.031 | 0.165 | 1.000 |
| Figure descriptions the judge passes (69 figures, two runs) | **0.971** [0.933, 1.000] | 0.804 [0.680, 0.905] | 0.833 [0.742, 0.907] | none written | none written | none written |
| Figure descriptions the judge finds a factual error in | 0 of 138 | 3 of 134 | 12 of 133 | | | |
| Cost per PDF page (median) | $0.0125 | $0.0477 | $0.0015 | $0 | $0 | $0 |
| Seconds per PDF page (median) | 4.3 | 10.7 | 4.5 | 3.7 | 4.3 | 0.35 |

- **Equations.** ReadableSTEM gets 1,379 of 2,738 equations (two runs) exactly right through the whole
  pipeline. The registered comparison with the Opus chatbot did not pass its sign-flip test on the 15
  section differences (p = 0.023 against a threshold of 0.01), so the report makes no claim about it in
  either direction, and the claim that ReadableSTEM beats the chatbot baseline was not made. Chemistry is
  near zero for every system (0.04 for all three), and the scorer's own ceiling on a hand-written sample
  is 0.70, so absolute numbers read low for everyone. ReadableSTEM's equations task, which hands it each
  equation as a picture with its text layer, scores 0.684 [0.553, 0.783]: the equations missed on a whole
  page come from finding and cutting out equations (it finds 88.9% of them and writes 26.5% extra pieces
  with no gold partner), not only from reading them. Against
  MinerU, the only parser that writes LaTeX, ReadableSTEM is better (0.504 against 0.174, registered
  99.4% interval 0.18 to 0.44). Docling and PyMuPDF4LLM write no equations by design, so their 0
  describes what they are built for.
- **The review screen misses most wrong equations and every wrong heading.** 23.3 of every 100
  gold equations are wrong and not inside a decision the instructor is asked to make, as many
  as the chatbot, which asks nothing. The screen's equation flags have precision 0.79 and
  recall 0.34. Its heading flags have precision 0 and recall 0 (none of the wrong headings
  is asked about), as the interim headings run had already shown. I have not yet labeled
  which wrong equations change the meaning, so the development-sample statement that no
  meaning-level error was accepted silently cannot be repeated for these sections.
- **Review load: 1.76 required decisions per PDF page** (853 decisions over 484 page-runs), against
  0.23 on the development sample, plus 0.55 optional notes per page. Of the required decisions,
  230 are repeated heading labels and 216 headings with weak evidence, 351 below the
  acceptance threshold and 296 symbol mismatches (a fix can have several reasons). At that rate a
  100-page document, the most the app takes at once, comes with about 176 decisions, the number
  to weigh the others against.
- **Headings: ReadableSTEM leads, by less than the intervals need.** F1 0.961 against 0.910 to
  0.932 for the others. The paired comparisons at the registered level (k = 8, 99.4%)
  exclude 0 against MinerU and PyMuPDF4LLM, and against neither does the sign-flip test agree
  (p = 0.0073 and 0.0088, thresholds 0.00625), so the report writes "higher by point estimate";
  they do not exclude 0 against Docling or the Opus chatbot. The clearest lead is in the levels:
  ReadableSTEM gets the relative level of every matched heading right, the chatbots 34% and 51%,
  Docling and MinerU write one level.
- **Figures: ReadableSTEM is ahead on the judge,** which is a model and is not calibrated against
  human labels (that pass is agreement with it, not accuracy). Parsers write no description.
- **Invented content** (units the PDF's text layer does not hold; the rule's precision and recall
  are not audited, so these are text-layer mismatches, not proven inventions):

  | Per unit | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku | Docling | MinerU | PyMuPDF4LLM |
  |---|---|---|---|---|---|---|
  | Prose sentences | 0.079 | 0.040 | 0.037 | 0.009 | 0.008 | 0.030 |
  | Numbers | 0 of 3,672 | 5 of 3,088 | 5 of 3,058 | 42 of 3,054 | 2 of 2,160 | 116 of 5,179 |
  | Equations, an added symbol (aligned to gold) | 0.087 | 0.020 | 0.017 | | 0.264 | |
  | Equations with no gold partner | 0.027 | 0.294 | 0.226 | | 0.140 | |

  ReadableSTEM's prose figure counts the sentences it writes itself ("About this version" and
  notices); without them it is 0 of 4,104. It copies prose, so its numbers there measure the
  copying. On equations it adds a symbol to 8.7% of the ones it writes, against 2% for the
  chatbots; the chatbots write many more equations the PDF has no match for.
- **Coverage** of the PDF's text (word 6-grams): ReadableSTEM 0.653, the chatbots 0.646 and 0.621,
  Docling 0.724, MinerU 0.674, PyMuPDF4LLM 0.800.
- **Cost and time.** ReadableSTEM's document task cost $6.62 for the 15 sections twice ($0.0137 per
  page on average); the Opus chatbot cost $22.34 for two runs, 3.8 times as much per page; the
  Haiku chatbot $1.49. Parsers cost no API money. The time is wall clock on one machine, at
  concurrency 4 for ReadableSTEM.

**Not measured.** Whether a wrong equation changes the meaning, whether the figure judge agrees
with a person, and the audit of the invented-content rule: those labels are mine to make and
do not exist yet (registration 7.8). The sections are OpenStax pages printed by this project's
stylesheet, clean inputs, from retired first editions; real course PDFs are harder. The runs
are one recording on Claude: model output varies run to run (the two document runs differ by
0.007 on equations, the two Opus chatbot runs by 0.007), and the OpenAI models the
registration names were not used.

The interim report from before the model run, with the headings and parser inputs and outputs,
is [`proof/interim/INTERIM-heldout-2026-10.md`](../proof/interim/INTERIM-heldout-2026-10.md); its
numbers are the same code's and are unchanged.

What was fixed before the run, and can be checked now:

| What | Value |
|---|---|
| Sections | 15 sections of 5 OpenStax books (College Physics, Chemistry, Precalculus, Introductory Statistics, Intermediate Algebra), 242 Letter pages |
| Answer key | 1,369 equations, 509 headings, 70 figures (69 with an OpenStax alt text) |
| License | the retired first editions, which are CC BY 4.0; the current editions are CC BY-NC-SA and were not used |
| Choice | a seeded rule on input properties only, with every section used in development excluded; the rule's code, its inputs and its output are in [`proof/selection/`](../proof/selection/), and `tests/test_proof_registered.py` re-runs the draw and compares it with the committed result |
| Registration | [`proof/PREREGISTRATION.md`](../proof/PREREGISTRATION.md), SHA-256 `d4d3af030b403a84da2c1a6787dc9773775a826cfa4f86edf9b48f7ec3f288ec` (version 2), and the answer key's manifest `9be8c5650fe492d0c5312199b3040aa6ea741595d5ba97df4b68e816455c9aa3`, checked before a run. This is version 2 of the registration: it differs from version 1 only in wording (the names of the people who asked for this evidence, an authorship line and paths on my machine were removed), and version 1's hashes are kept. Addendum 6, which moved the model-dependent parts to Claude, has SHA-256 `50fd995223017609e247bc9a2dc943f4fa9dd345d606cd075b851b32979e7c8a`, and Addendum 7, written after the run, `b38825ab165728273220e8685358328879db36437f1d1c3baafaaa6b817810d2`. The frozen files and how to check them are in [`proof/REGISTERED.md`](../proof/REGISTERED.md); the only timestamps are the files' own and this repository's commits, there is no outside timestamp yet |
| Scorer ceiling (answer key only, no model) | known-correct LaTeX written by hand for 40 of the 1,369 equations scores 28 of 40 under the lenient scorer (0.70, Wilson 95% 0.55 to 0.82), because OpenStax writes signs, degrees and units inside number or text tokens; a perfect transcriber is not scored 1.0, and a tool that writes LaTeX in its own style pays more (normalising markup alone moves MinerU from 0.174 to 0.197). I have not checked the 40 against the rendered gold yet |
| Compared | ReadableSTEM against the chatbot recipe instructors copy (Purdue's one-sentence prompt, [quoted from its page](https://www.purdue.edu/innovativelearning/tools-resources/accessibility/digital-instructional-materials-accessibility-checklist/digital-math/) as of 2026-10-09, on claude-opus-5-5 and claude-haiku-5-5) and against Docling, MinerU and PyMuPDF4LLM, on equations, headings, figures and invented content |
| Not measured | whether a wrong equation changes the meaning, and whether the figure judge agrees with a person: those labels are mine to make and do not exist yet |

To make the recording again, `PROVIDER=anthropic ENV_FILE=/path/to/.env proof/record-heldout.sh`
(the caps are in the script; this run's were $26 for ReadableSTEM and $13, $2.50 and $3.50
for the chatbot arms and their judge, within a $45 ceiling), or `PROVIDER=openai` for the
registration's OpenAI models. The recording code, the caps and the reproduction path are
described in [docs/proof.md](proof.md). `make proof` replays the committed recording and
writes [PROOF.md](../PROOF.md) (about ten minutes, no key) with the pipeline as it was when the
run was recorded, so reproduce it from the tag: `git checkout proof-heldout-2026-10 && make
proof` (later commits change the product, and a recording cannot be replayed through code that
asks the models something else; `make proof-check` still checks its hashes anywhere). The report of the development sample is
[`proof/PROOF-development-sample.md`](../proof/PROOF-development-sample.md) (`make proof
SET=smoke-physics-2 PROOF_EXTRA="--out proof/PROOF-development-sample.md"`).

## Re-measurement on the same sections, 2026-10-21 (not a blind result)

The current system (pipeline 0.5.0 and later changes, `docs/remeasurement-2026-10-21.md`) was run
again, through the same held-out path, on the same 15 sections: two runs, production settings, the
page math pass off. **These sections have been examined since the registered run** (the errors of the
registered run were read, and the 0.5.0 rules were written to fix classes of them), so these numbers
are optimistic and are not held-out results of the current system; nothing was tuned after the first
number was seen. Registered numbers stay as above. The chatbot arms are the registered arms, not run
again (the PDFs were printed again, so their bytes differ; text and gold are the same).

| 15 sections, 2 runs | ReadableSTEM, registered (0.4.0) | ReadableSTEM, re-measured (**not blind**) | Chatbot, Opus (registered) | Chatbot, Haiku (registered) |
|---|---|---|---|---|
| Equations right, end to end (primary) | 0.504 [0.376, 0.599] | **0.596** [0.448, 0.697] | 0.622 [0.464, 0.727] | 0.600 [0.437, 0.708] |
| Same, symbols scorer, split/merge credited (secondary) | 0.722 | **0.827** | 0.835 | 0.801 |
| Wrong equations nobody is asked about, per 100 | 23.3 [19.0, 29.5] | **22.4** [16.2, 31.0] | 24.0 | 25.2 |
| Heading F1 | 0.961 | 0.963 | 0.932 | 0.914 |
| Figure descriptions the judge passes | 0.971 | 0.957 | 0.804 | 0.833 |
| Equations with an added symbol (aligned) | 0.087 | 0.013 | 0.020 | 0.017 |
| Equations with no gold partner and no match in the PDF (registered rule) | 0.027 | **0.234** | 0.294 | 0.226 |
| Same, rows checked one at a time (secondary) | 0.011 | 0.208 | 0.288 | 0.224 |
| Same, only equations with a text layer in the PDF (rule / rows) | not split | 0.035 / 0.015 | not split | not split |
| Required decisions per PDF page | 1.76 | 1.33 | 0 | 0 |
| Cost per PDF page (median) | $0.0125 | $0.0104 | $0.0477 | $0.0015 |

By the registered method, ReadableSTEM minus the Opus chatbot on the primary score is -0.025 (99%
interval -0.069 to +0.020, sign-flip p = 0.39): no difference this set can detect, where the
registered run had -0.118 (-0.199 to -0.025). The change of ReadableSTEM itself on these sections is
+0.093 (95% +0.039 to +0.144, sign-flip p = 0.023). What got worse: equations with no gold partner and no match in the PDF went from 0.027 to 0.234 by the registered rule (0.011 to 0.208 with the rows of a derivation checked one at a time). An audit of 30 of them (seeded, by section) found no wrong or invented reading: 25 were correct readings the check cannot support (22 are pictures of equations, which the 0.5.0 layout now reads and 0.4.0 left as figures, so the PDF holds no text for them; 3 are derivations whose rows are not one run of the text layer) and 5 were spans that swallowed neighboring text. The comparable number is the equations that have a text layer: 0.035 (0.015 by rows) against 0.027 (0.011) registered. The check asks whether an equation without an answer-key partner can be found in the PDF's text; it cannot tell a right reading of a picture from a wrong one. The share of wrong equations a person is asked about fell (flag recall 0.34 to
0.21, precision 0.79 to 0.89). Chemistry is as before (0.04). Recorded spend $9.23. The same recipe
on the development sets (one run each, `docs/eval-page-math.md`): Haiku 0.514 against 0.486 on 114
pages, Opus 0.586 and ReadableSTEM 0.597 on 49, a tie on math sections; the symbols score is in
`docs/eval-page-math.md`. A replay of this run reproduces every number but one axe-core count
(`docs/remeasurement-2026-10-21.md`, deviation 1). The recording is not committed
(`~/.cache/a11y-proof/heldout-remeasure-2026-10-21`).

## Scanned and handwritten pages, converted on request (2026-10-21)

Behind an option on the upload form ("Convert scanned or handwritten pages", offered when the
server sets `FEATURE_IMAGE_PAGES`), each scanned or handwritten page is read from its picture by
one call to the main model (`claude-opus-5-5`); its equations are read again from a cut of their
place on the page, every equation and table is a decision for the instructor, and the original page
is kept in the file for checking. The numbers, which are not the ones above (different documents and
a different job; [docs/eval-image-pages.md](eval-image-pages.md), [ADR 0007](decisions/0007-image-pages.md)):

- **Development pages made into scans** (36 pages of six development documents, degraded
  by [`eval/scan.py`](../src/readablestem/eval/scan.py)): 0.59 of the equations exactly right end to
  end (0.83 on the symbols score), the same as the same pages as typed PDFs; words of the prose
  wrong 3 to 5 % of the time; $0.106 a page. Handwriting was looked at on four public-domain pages
  by eye only: the main model read them well, and a cheaper model misread two section numbers with
  full confidence. Five photographed notebook pages were looked at by eye later (2026-11-03): the
  pass read the two physics pages well and the three linear-algebra pages mostly right; so nine
  handwritten pages in all, none with an answer key. A held-out answer key of 24 pages of real
  handwriting (205 expressions) was measured afterwards: 0.566 exactly right end to end, no wrong
  equation without a decision, 5.7 decisions a page ([docs/eval-handwriting.md](eval-handwriting.md)).
- **Real papers as scans, never used to build the feature** (the real-world held-out set, 9 of 15
  papers because the $9 spending cap for the run was reached; 42 pages, 1,147 equations, registered
  before the run): 0.66 exactly right (0.69 on the symbols score) against 0.62 (0.66) for the typed
  PDFs of the same papers, a difference inside the noise, with 5 wrong equations in every 100 not
  asked about when it was measured (10 for the typed PDFs); since 2026-10-22 every converted block
  with math is a decision, and a replay of the same recorded answers left none unasked
  (docs/eval-image-pages.md, "Update: math always required"); $0.136 a page.
- It is slower (about 20 to 30 seconds a page), it costs four to six times a typed page, and a
  model can misread handwriting and small symbols; the measurements are on synthetic scans, nine
  papers and 24 held-out handwritten pages, with no measurement of how long checking takes.

## Development sample (pipeline 0.4.0, 2026-10-12)

Three sections of OpenStax *Physics* chapter 2 (CC BY 4.0). The detection and review
rules were tuned on these pages, so these figures flatter the system. They are listed
because they are committed and reproducible, and because the regression gate compares
CI runs with them (`data/eval/baseline-*.json`).

| Task | n | Result | Cost |
|---|---|---|---|
| Equations | 30 | `exact_lenient` 0.767 (95% CI 0.591 to 0.882); a perfect transcriber scores 0.833 here, because of known gaps in the scorer; mean TEDS 0.864 | $0.0017 per equation |
| Headings | 121 | recall 0.793 (CI 0.713 to 0.856), level accuracy 0.740, precision 0.914 (105 predicted against 121 gold; the 8 least certain went to review; of the wrong headings, the share a decision asks about fell from 0.33 to 0.18 because the 8 repeated labels it used to ask about are no longer headings, 6 to 4 decisions). The gold counts the label and the title of an OpenStax box as two headings; the outline keeps one (docs/eval.md, "Doubled box headings") | no model call |
| Figures | 13 | judge pass 1.000 (CI 0.772 to 1.000); the judge is a model and is not yet calibrated against human labels | $0.0039 per figure |
| Whole pages | 3 sections, 43 PDF pages | equation recall 1.000, precision 0.545 (0.968 without prose math); 0 static and 0 axe-core violations; 0.233 required decisions per PDF page, of which 6 of 10 pointed at a real error (recorded 2026-10-21, before the 2026-11-02 heading changes, which remove 2 of its heading decisions) | $0.0034 per PDF page; median 33 s per section (n = 3) |
| Tables (local mode, no model; off by default) | 197 tables | recall 0.909 (CI 0.857 to 0.958), precision 0.984 (CI 0.953 to 0.994), TEDS 0.866 | no model call |

The numbers leave several things out.

- **Small samples.** One equation is 3.3 points of `exact_lenient`. One figure is 7.7 points.
- **Clean inputs.** Rendered equations and printed pages are tidier than real course PDFs:
  no scans, no handwriting, no slides, one set of fonts. Expect real documents to score lower.
- **The review screen does not catch everything.** In the whole-page run, 3 of the 25
  scorable equations were wrong once the scorer's known gaps are set aside; the review
  screen asked about 1 of them. The other two are cosmetic by the project's policy (a bold
  subscript, a comma). No meaning-level error was accepted silently in this sample, but 30
  equations cannot rule it out.
- **Automated HTML checks find only what a machine can find.** Zero violations does not
  mean a page is usable with a screen reader, and these numbers do not come from testing with
  screen-reader users.

The CI baselines in `data/eval/` were recorded again on 2026-10-14 (pipeline 0.5.0, Claude):
equations `exact_lenient` 0.733 (22 of 30; the table above has the earlier run's 0.767, and model output varies
between runs), mean TEDS 0.870; figures judge pass 1.000; whole pages end to end 0.767.

Method, definitions and every table: [docs/eval.md](eval.md).
