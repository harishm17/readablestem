# Re-measurement of the held-out sections, 2026-10-21 (not a blind result)

**What this is.** ReadableSTEM as it is now (pipeline 0.5.0 and the changes after it, below) was run
again, through the repository's official held-out path, on the 15 sections of the registered set
(`heldout-2026-10`). **It is not a held-out result.** Those sections have been examined since the
registered run: the held-out errors were read in the analysis of 2026-10-20, and the rules of
pipeline 0.5.0 were written after the registered run to fix classes of error seen there (the
development set dev-v2 was built to contain them). The numbers below are therefore optimistic for
documents in general, and are not to be quoted as held-out results of the system. The registered
numbers (README, `PROOF.md`, the recorded run `proof/bundles/heldout-2026-10/`) are unchanged and
remain the held-out results of the 0.4.0 system; nothing in `proof/` or `PROOF.md` was edited.

**Nothing was tuned after the first held-out number of this run was seen.** The code that ran is
commit `f87ac84` of branch `feat/page-math` (clean checkout; the two layout fixes had been made
and measured on the development sets only, `docs/eval-page-math.md`); the page math pass was off
(its default); no setting, prompt, threshold or scorer was changed on this run's account.

## What changed since the registered run

The registered run was pipeline 0.4.0 (commit `297126f`, as the registration's Addendum 7 says).
The re-measured system is `f87ac84`, pipeline 0.5.0, with the equation prompt (`equation-df0e12f1`),
the models (claude-opus-5-5 at effort medium, claude-haiku-5-5 at low) and the thresholds as they
were. Changed between them (CHANGELOG.md, "Pipeline 0.5.0" and after):

- layout: leading exercise numbers stripped from block-equation regions; inline stacked
  fractions and binomials as one inline equation; braced systems, determinants and cases as one
  equation; a middle dot is an operator; half a fraction is held under the accept bar with a
  required decision; equations that are pictures are classified and need a decision; hyphens at
  line ends; a heading that repeats the one above becomes bold text; large slide formulas go to
  the equation step; tagged-PDF structure;
- review: number and letter-case checks against the text layer, speech readings, "single reading"
  acceptance, escalation not asked again when only the confidence is just under the bar;
- after 0.5.0 on this branch: the "." of `\right.` is no longer stripped (a text-only change to
  inline LaTeX), a stray opening brace at the end of a sentence goes with its system, and nuclides
  in running text (`14C`, `235U`) are inline equations instead of raised text; the page math pass
  exists and is off;
- not in the proof path: the Claude second check (shadow) did not run (no second check, as in the
  registered run); the equations task, the figures task and the figure judge ran as registered.

## What was run

```
readablestem proof --set heldout-2026-10 --live --resume --provider anthropic --max-usd 13 \
  --keep-finished --confirm-signoff --section-workers 3 --work W --record B --out R
```

(`--section-workers 3`, which the proof documents as changing timing only, because the registered
two-hour harness limit was a risk; the sections' wall-clock times are therefore not recorded.) All
seven runs of the registered design completed (document x2, headings x1, figures x2, equations
x2), none stopped by the cap, no failed model call. Recorded spend **$9.23** (figure judge
included), under the $14 hard stop. The recording is at
`~/.cache/a11y-proof/heldout-remeasure-2026-10-21/bundle` (outside the repository; manifest
SHA-256 `0d01a3da...`), its replay report `PROOF-replay.md` and, with the registered chatbot arms
added, `PROOF-cmp.md` beside it.

Deviations, stated as they are:

1. **The replay differs from the recording in two numbers**: `document.run1.axe_checked_pages`
   15 recorded, 14 replayed (and one page counted as skipped). One page's HTML differed between
   the live conversion and the replay, so axe-core's recorded answer for it was not found. Every
   model request, every equation metric and every cost replays identically (7,162 requests, one
   without an answer: that axe result). The report is `--allow-misses --allow-drift` and says
   DRIFT. A full pytest run was in progress on the same machine during the live run (CPU load); the
   ink analysis has a time budget that a loaded machine can exhaust, which is the likely cause, but
   it was not confirmed. No axe numbers are quoted here.
2. **The PDFs were printed again** (Chromium) from the same archive pages: 242 pages as registered,
   but not byte-identical to the registered PDFs (their SHA-256 differ), which is why the chatbot
   arms, recorded on the first printing, were **not** re-run (instructed: no new chatbot spend).
   They are the recorded arms of the registered run, scored against the same gold by the same code,
   on the same text.
3. **The comparison with the chatbot arms** was made by adding those recorded arms (copied from
   `proof/bundles/heldout-2026-10/systems/`, no model call) to a copy of the new bundle and replaying it
   (`PROOF-cmp.md`). The comparison tables are the proof's own, with its registered method
   (family A, k = 5, paired cluster bootstrap by section, 99% Bonferroni intervals, sign-flip test).

## Results

Registered (0.4.0, the held-out run) beside re-measured (`f87ac84`, **not blind**) and the recorded
chatbot arms (registered run). Intervals are the registered ones (cluster bootstrap over sections,
95% unless said; approximate with 15 sections, a little narrow).

| 15 sections, 2 runs | ReadableSTEM registered (0.4.0) | ReadableSTEM re-measured (not blind) | Chatbot, Opus (registered) | Chatbot, Haiku (registered) |
|---|---|---|---|---|
| **Equations right, end to end (primary `exact_lenient`)** | 0.504 [0.376, 0.599] | **0.596** [0.448, 0.697] | 0.622 [0.464, 0.727] | 0.600 [0.437, 0.708] |
| Equations right, symbols scorer with split/merge credit (secondary) | 0.722 | **0.827** | 0.835 | 0.801 |
| Wrong equations nobody is asked about, per 100 gold | 23.3 [19.0, 29.5] | **22.4** [16.2, 31.0] | 24.0 [16.1, 33.4] | 25.2 [18.2, 34.6] |
| Detection recall / precision | 0.889 / 0.735 | 0.905 / 0.767 | 0.887 / 0.556 | 0.881 / 0.522 |
| Invented: prose sentences (includes text the tool writes) | 0.079 | 0.084 [0.057, 0.122] | 0.040 | 0.037 |
| Invented: numbers | 0 of 3,672 | 0 of 3,906 | 5 of 3,088 | 5 of 3,058 |
| Invented: aligned equations with an added symbol | 0.087 | **0.013** (31/2,447) | 0.020 | 0.017 |
| Invented: equations with no gold partner and no match in the PDF | 0.027 (14/521) | **0.234** (124/529) [0.035, 0.442] | 0.294 | 0.226 |
| Heading F1 / level right | 0.961 / 1.000 | 0.963 / 1.000 | 0.932 / 0.341 | 0.914 / 0.508 |
| Figure judge pass / factual errors | 0.971 / 0 of 138 | 0.957 [0.868, 1.0] / 0 of 138 | 0.804 / 3 of 134 | 0.833 / 12 of 133 |
| Required decisions per PDF page | 1.76 | 1.33 | 0 | 0 |
| Equation flags, precision / recall | 0.79 / 0.34 | 0.89 / 0.21 | n/a | n/a |
| Cost per PDF page, median (mean of the document task) | $0.0125 ($0.0137) | $0.0104 ($0.0115) | $0.0477 | $0.0015 |

The re-measured rates per run are 0.5975 and 0.5953 on the primary score (the registered run's were
0.5004 and 0.5069).

**Against the chatbot, by the registered method** (family A, `PROOF-cmp.md`): ReadableSTEM minus
the Opus chatbot on the primary score is **-0.025, 99% interval [-0.069, +0.020] (k = 5)**, 95%
[-0.057, +0.008], sign-flip p = 0.39: "no difference this set can detect". The registered run
had -0.118 [-0.199, -0.025]. Unflagged wrong per 100: -1.64 [-8.7, +12.8], no difference. Invented
prose sentences: the chatbot is better (0.040 against 0.084, which includes the sentences the tool
writes itself; without them ReadableSTEM's is 0 of 4,104, as registered). Heading F1: no
difference detectable at the registered level (+0.032, interval [-0.009, +0.104]; ReadableSTEM's
levels are right where the chatbots' are not).
On the secondary scores (14 sections with equations; 95%, not registered): symbols-credited
-0.008 [-0.074, +0.097] against Opus and +0.026 [-0.042, +0.143] against Haiku; primary against
Haiku -0.003 [-0.039, +0.042]. The change of ReadableSTEM itself, same sections, re-measured minus
registered: primary +0.093 [+0.039, +0.144], sign-flip p = 0.023; symbols-credited +0.104
[+0.047, +0.152] (an improvement on sections that were looked at).

By book (primary, pooled; registered in brackets): College Physics 0.572 (0.561), Chemistry 0.041
(0.041), Precalculus 0.760 (0.609), Introductory Statistics 0.200 (0.143), Intermediate Algebra
0.549 (0.465). Opus on the same books: 0.657, 0.041, 0.790, 0.200, 0.543. On the symbols score
Chemistry is 0.649 for ReadableSTEM and 0.134 for the Opus chatbot (its formulas are not read
by the converter, as on the development set); College Physics is 0.831 against 0.966.

### What got worse, and what the numbers do not say

- **Equations with no gold partner and no match in the PDF: 0.027 to 0.234** (14 to 124 of about
  525). This is the invented-content check, and it moved the wrong way by a lot. Looking at the
  flagged units of three sections (`readablestem eval invented score --units`), most are multi-row
  derivations that the 0.5.0 rules now read as one equation: the model writes the rows one after
  another, while the PDF's text layer orders their symbols differently, so no run of the PDF's
  symbols matches ("18 p + 6 q + 15 p + 5 q 18 p + 15 p + 6 q + 5 q"; "5 13 + ( 4 4 ) 5 13 + 1 1 ..."),
  which the rule counts as unsupported. That reading is from three sections and is not an audit;
  the rule's precision is unaudited, as the report says. What it does show is that "invented" for
  equations with no partner is not comparable between the 0.4.0 run (which cut those derivations into
  pieces) and this one. The rate for aligned equations, the one that checks a symbol added to
  an answer, improved from 0.087 to 0.013.
- **Equation flag recall fell** (0.34 to 0.21) while precision rose (0.79 to 0.89): there are
  fewer required decisions per page (1.76 to 1.33), and `below_accept` is the biggest reason
  (204 fixes). The share of wrong equations that nobody is asked about is about the same (22.4 per
  100 against 23.3).
- **Chemistry is unchanged** (0.041 for ReadableSTEM and for the chatbot, symbols 0.65 against 0.13): the
  primary scorer cannot give a perfect transcriber credit on the answer key's chemistry markup.
- **Not measured again**: seconds per page (`--section-workers 3`), axe-core (deviation 1), the open
  parsers, the chatbot arms, the invented-content audit, whether a wrong equation changes the meaning.

## The same chatbot comparison on the development sets

(`docs/eval-page-math.md`, "Against the chatbot recipe, on the same development pages"; development
sets, one run each of the registered recipe through the proof's own code, spend $2.91.) On 114 dev-v2
pages: Haiku arm 0.514 on the primary score against ReadableSTEM's 0.486; on slice B (49 pages):
Opus 0.586, Haiku 0.580, ReadableSTEM 0.597 (flag off). On the maths sections of B the primary is a tie
(0.749, 0.730, 0.738) and the chatbots lead on the symbols score (0.97 against 0.92) and recall
(0.99 against 0.97); on the two chemistry sections the Opus arm wrote `\ce{...}` formulas the
converter cannot read. The development calibration and the held-out re-measurement agree: with the
0.5.0 layout the primary-score gap to the chatbots is small and not detectable on 15 sections.
Neither is a blind measurement of documents in general.

## What would make this a test

A held-out result needs sections nobody has examined, with the system frozen before they are
first run. At the section-to-section spread of the difference between systems seen here (0.21 on
the primary score), a gap of 5 points needs about 140 sections and a gap of 10 about 35, so 15 more
sections would settle nothing about the chatbots. The change of ReadableSTEM itself between the two
runs of these sections has the same spread (0.20 per section, equal weights), so confirming a
+5 point gain on unexamined sections needs on the order of 130 sections by that formula, and the
+9 points seen here about 40; with 14 sections the registered-style sign-flip test gave p = 0.023
for it, which is the reason to believe it is real on these sections and not a reason to believe the
size on others. A fresh set of 20 sections would settle only the larger questions (whether the gain
over 0.4.0 is of the order of 10 points on sections nobody looked at, and, for the page math pass
(off), a +1 point effect needs about 17 sections in a paired design, `docs/eval-page-math.md`).
Cost of one ReadableSTEM run on 20 sections: about $10, with two runs $20.
