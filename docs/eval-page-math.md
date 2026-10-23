# The page math pass on the development sets (2026-10-21)

What [ADR 0006](decisions/0006-page-math-pass.md) built, what it did on the development sets,
what it cost, and what that says about a held-out run. Development sets only (`data/gold` and
dev-v2's dev part); the held-out set was not run, opened or used to tune anything, and the sealed
check part of dev-v2 was not touched. Every number is from this branch's pipeline with
`FEATURE_PAGE_MATH` off (baseline) or on, with every model answer recorded once and replayed.

**The short version.** On 114 development PDF pages (724 gold equations) the pass does not move
the primary `exact_lenient` (0.486 off, 0.482 on; the change is -0.4 points, interval -1.9 to
+0.9). The symbols-correct score moves by +0.5 points. Detection recall by +0.6 points, span
fidelity by +0.4, and detection precision falls by 7.8 points because the pass finds many more
inline pieces of mathematics than the answer key counts. What does move is the review screen:
of the equations that are wrong in the symbols, the share sent to a person goes from 41% to 58%
(precision of those flags from 44% to 56%), and the wrong equations nobody is asked about fall
from 4.8 to 3.5 per 100 gold, at the same number of required decisions per page (1.67 to
1.61) and $0.0016 more per page. The cause is the second reading, not the spans. The page pass
alone cuts the answer key's spans no better than layout's own rules at this commit (0.32 against
0.34 faithful on one slice), because at this commit the rules already cut what a text-grounded
pass can fix (below), and the expressions it cannot fix are the ones the text layer cannot
anchor.

## What was built

| Part | Where |
|---|---|
| The pass: prompt, strict output schema, verification of every quote against the text layer, the call | `src/readablestem/pipeline/page_math.py`, `src/readablestem/models/prompts/page_math.md` |
| Reconciliation with layout (a hook in layout's page analysis; layout runs twice with the flag on) | `src/readablestem/pipeline/page_math_reconcile.py`; hook points in `pipeline/layout.py` (`analyze(page_math=...)`, `_page_blocks`, `_paragraph_pieces`, `render_page`) |
| Two readings (agreement lifts confidence, disagreement asks the main model once, a reading nobody shares is a required decision) | `pipeline/equation_step.py` (`_compare_readings`, `PAGE_READING*`), `web/messages.py` |
| Flag and settings | `FEATURE_PAGE_MATH`, `PAGE_MATH_MODEL`, `ANTHROPIC_EFFORT_PAGE_MATH`, `PAGE_MATH_UNCONFIRMED_FACTOR`; `core/types.py` (`ConversionOptions.page_math`, `Element.page_latex`, `Element.page_status`, `PageMathSummary`, `Step`) |
| Longer fenced context for one step | `models/base.py` (`VisionRequest.context_limit`), both clients |
| Eval: cost split, per-pair page-pass fields, `--sections` | `eval/pipeline_eval.py`, `cli/eval_cli.py` |
| Comparison scripts | `scripts/dev_buckets.py` (headline numbers and buckets per run), `scripts/dev_spans.py` (span-level effect with no equation model, about $0.002 a page) |
| Tests | `tests/test_page_math.py` (verification, reconciliation, the call, two recorded answers on dev pages in `tests/fixtures/page_math`), `tests/test_steps_equation_page.py` |

How a page goes through it: layout reads the page (first run, rows are collected); one claude-haiku-5-5
call (effort medium) gets the page at 150 dpi and the rows as `L12 | text`, and returns expressions
with anchors that quote the rows; each quote is checked against the row (`verify`), a block
span's LaTeX is checked against the text it quotes, and layout runs again with the verified
plan; the equation step then reads every crop as before and compares its reading with the page's.

## How it was measured

Two slices of the 28 dev-v2 documents (338 pages would have cost about $5 for a baseline alone), chosen
for the classes the held-out analysis named before any run, not by looking at results:

| Slice | Sections | PDF pages | Gold equations |
|---|---|---|---|
| A (tuned on) | Elementary Algebra 5.2 (systems, numbered exercises), authored LaTeX matrices, Chemistry 2.3 (nuclides), 17.3 (rate laws), 1.4 (measurements) | 65 | 272 |
| B (looked at less) | Elementary Algebra 8.5 (complex fractions), College Algebra 9.6 (binomials), authored LaTeX exercises, Chemistry 16.3, 14.1 | 49 | 452 |

Slice A is where the prompt and the reconciliation were iterated; B was run after the first policy
was fixed and then used for two more fixes (below), so neither is a clean confirmation set. The
original development set (`data/gold`, 30 equations on 43 pages) is the regression check; its
baseline is the one recorded on 2026-10-14 (replays at no cost against this commit). The baseline of
each slice is this branch with the flag off, run live once (`--answers`), replayed to rescore.
The second automated check was off in every run (shadow only; it changes no output).

## Results

Baseline (flag off) against the pass on, pooled over A and B (724 gold equations, 114 PDF pages),
and for the two slices and `data/gold`:

| | A off | A on | B off | B on | **A+B off** | **A+B on** | gold off | gold on |
|---|---|---|---|---|---|---|---|---|
| **primary `exact_lenient`** | 0.302 | 0.305 | 0.597 | 0.589 | **0.486** | **0.482** | 0.767 | 0.800 |
| secondary: stream v2, credit | 0.445 | 0.449 | 0.639 | 0.631 | 0.566 | 0.562 | 0.967 | 1.000 |
| secondary: symbols, credit | 0.585 | 0.588 | 0.799 | 0.805 | 0.718 | 0.724 | 0.967 | 1.000 |
| detection recall | 0.673 | 0.688 | 0.872 | 0.872 | 0.797 | 0.803 | 1.000 | 1.000 |
| detection precision | 0.688 | 0.517 | 0.793 | 0.796 | 0.756 | 0.678 | 0.750 | 0.283 |
| span fidelity (span text within 0.99 of the gold) | 0.338 | 0.342 | 0.624 | 0.628 | 0.517 | 0.521 | 0.600 | 0.733 |
| unmatched predictions (unsupported by the PDF's text) | 83 (31) | 175 (32) | 103 (45) | 101 (41) | 186 (76) | 276 (73) | 10 (0) | 76 (2) |
| required decisions per PDF page (equations) | 1.31 (0.72) | 1.40 (0.83) | 2.14 (1.82) | 1.90 (1.57) | 1.67 (1.19) | 1.61 (1.15) | 0.14 (0) | 0.21 (0.07) |
| wrong, not asked, per 100 gold (primary) | 33.5 | 30.1 | 22.8 | 23.7 | 26.8 | 26.1 | 23.3 | 20.0 |
| wrong in the symbols, not asked, per 100 gold | 5.9 | 2.9 | 4.2 | 3.8 | **4.8** | **3.5** | 3.3 | 0.0 |
| flag precision (flagged equations wrong in the symbols) | 0.53 | 0.76 | 0.41 | 0.42 | **0.44** | **0.56** | | |
| flag recall (symbols-wrong equations that were flagged) | 0.33 | 0.70 | 0.46 | 0.47 | **0.41** | **0.58** | | |
| cost per PDF page | $0.0116 | $0.0142 | $0.0194 | $0.0196 | **$0.0149** | **$0.0165** | $0.0038 | $0.0054 |

"Flag" is a required decision on an aligned equation. "Unmatched predictions" are the equations the
pipeline wrote that no gold equation aligns to (the answer key writes quantities and lone variables
in the text as plain text; "unsupported" are those the PDF's own text does not support). Precision of
detection fell because the pass marks quantities with units, lone variables and bold vectors in
sentences as inline mathematics, which is what it was told to do and what a screen reader
benefits from, and the answer key does not count. On `data/gold` it writes 76 equations the answer key has no partner for, against 10, 60 of them
"other" (a quantity or a lone symbol); the gold's own 30 equations are all symbols-correct with
the pass on (29 off), nothing is worse.

The pooled change in `exact_lenient` is -0.4 points; a cluster bootstrap over the 10 sections gives
-1.9 to +0.9, and the symbols score +0.5 (0.0 to +1.0). Losses and gains are single equations:
on slice B the pass lost seven binomial coefficients to representation (`\binom` against the
answer key's brackets around a table; the symbols scorer now treats the two as equal, the primary
cannot) and gained two complex fractions the layout alone had missed.

### Buckets

Per gold equation (pooled A and B; the bucket definitions are those of the held-out analysis of
2026-10-20, `scripts/dev_buckets.py`):

| Bucket | off | on |
|---|---|---|
| correct (`exact_lenient`) | 0.486 | 0.482 |
| scorer: symbols right, primary says no | 0.229 | 0.239 |
| span: aligned, symbols differ at an end | 0.051 | 0.053 |
| recognition: aligned, symbols differ inside | 0.017 | 0.017 |
| no proposal | 0.014 | 0.012 |
| undetected: no aligned prediction | 0.203 | 0.198 |

The undetected 143 gold equations (of 724) of the pass-on run are: 55 single glyphs (an answer key
equation that is only "x", "+" or "="), 40 chemical formulas and equations, 21 inline fractions
(most of them the nested fractions of "complex rational expressions"), 20 nuclide symbols
(prefix mass and atomic numbers, a stack the text layer gives in no usable order) and 7 others.
The pass recovered about four of them.

### Span level: what the model's own cut is worth

`scripts/dev_spans.py` aligns what layout finds, what layout finds with the verified plan, and
the model's spans alone to the gold by text, with no equation model (the answer is the
fidelity: the span's text within 0.9 of the gold equation's):

| | Recall | Precision | Faithful (0.99) |
|---|---|---|---|
| Slice A, layout alone | 0.629 | 0.643 | 0.342 |
| Slice A, layout with the plan | 0.647 | 0.486 | 0.346 |
| Slice A, the pass alone (medium) | 0.570 | 0.502 | 0.316 |
| Slice A, the pass alone (low, prompt without examples) | 0.577 | 0.549 | 0.210 |
| Slice B, layout alone | 0.872 | 0.793 | 0.624 |
| Slice B, layout with the plan | 0.872 | 0.796 | 0.628 |
| Slice B, the pass alone (medium) | 0.449 | 0.935 | 0.281 |

By class (gold equations right at fidelity 0.9, A and B together, `layout alone / with the plan /
the pass alone`): systems with a brace 85 / 86 / 86 of 87; numbered exercises 119 / 121 / 108 of
131; inline fractions 125 / 127 / 78 of 184; chemical formulas 37 / 37 / 18 of 91; matrices
31 / 31 / 18 of 50; nuclides 2 / 2 / 1 of 24. At this commit layout already gets the exercise
numbers and the systems the held-out run showed it cutting wrongly (the 0.5.0 rules; docs/layout.md);
what is left is mostly not reachable from the text layer.

## Cost

Per PDF page, pooled A and B, flag off then on:

| | off | on |
|---|---|---|
| the page pass (claude-haiku-5-5, medium) | 0 | $0.0018 |
| cheap reads of crops | $0.0002 | $0.0002 |
| main model, first read (display equations) | $0.0103 | $0.0105 |
| main model, escalation after a cheap read | $0.0018 | $0.0016 |
| figures and the rest | $0.0026 | $0.0025 |
| **all** | **$0.0149** | **$0.0165** |

The target was about $0.017 per page: slice A is $0.0142, slice B $0.0196 (its baseline is already
$0.0194), `data/gold` $0.0054, pooled $0.0165. Escalations do not rise: where the two readings
agree a cheap answer is accepted without the second read, and where they disagree the second
read is one the pipeline would often have made anyway. At effort low the pass costs $0.0010 a
page but cuts the answer key's spans right 0.21 of the time (0.32 at medium on the same slice).

## What was iterated, and why anything was run again

| Step | Why | What changed | Result |
|---|---|---|---|
| First end-to-end run on slice A (page pass v1 prompt, effort low, replace-on-verify) | the first full measurement | | primary 0.302 to 0.305, detection precision 0.688 to 0.519, required decisions 1.31 to 2.11 per page: more flags and no gain |
| Reconciliation: a span must agree with its quote; bracket rows are absorbed; a span that would split a layout region is refused | the first run cut matrices and derivations into pieces layout had right (3 symbols-exact equations lost on the matrices document) | `_consistent` (Dice 0.8 of the symbol bags of the LaTeX and the quote), `_absorb_brackets`, `_screen_blocks` | splits stopped; `refused` counts them (18 on the six pages of the matrices document) |
| Prompt v2 (examples) and effort medium | the pass alone cut systems and fractions worse than layout (fidelity 0.21 on slice A) | four worked examples in the prompt, effort low to medium | fidelity 0.21 (low, v1) / 0.20 (low, v2) / 0.32 (medium, v2): the effort did it, the examples did not |
| `PAGE_MATH_UNCONFIRMED_FACTOR` 0.9 to 1.0 | measured on slice A: of 16 aligned equations that fell under the accept bar for being unconfirmed, 14 were right | default 1.0 | below-accept decisions on slice A fell from 38 to 8 (with the other changes of that step) |
| Slice B run (first policy) | | two losses were the pass quoting "n" for "nth" and "(r+1)" for "(r+1)th" | `_with_scripts`: a span takes up to four letters or digits glued to its end, and script glyphs at either end |
| The symbols scorer: `\binom` and brackets around a one-column table | seven binomials counted as lost | `mfrac` with no bar is a stack | the held-out tables above moved by less than 0.001 |
| Agreement of the readings is by symbols without letter weight | on `data/gold` the pass flagged bold vectors it read as plain letters (8 false flags) | `page_math._unstyled` | required decisions on `data/gold` 0.49 to 0.21 per page, pooled A+B 1.67 to 1.61 |

The last two were measured by replaying the recorded answers (every request they changed had
been asked already), so they cost nothing; the reruns of the full pipeline at the end only paid for
crops whose box changed.

## Spend

Live model spend of this work, under the $8 cap, from the answer files (list price, each answer paid once):

| What | USD |
|---|---|
| Slice A baseline (flag off) | 0.7536 |
| Probe: 3 pages of the matrices document, prompt v1 | 0.0064 |
| Slice A, flag on, first policy (prompt v1, effort low) | 0.3325 |
| Page pass prompt and effort iteration (A low v2, A medium, B medium; page pass only) | 0.2635 |
| Slice A, flag on, medium (crops that changed) | 0.1927 |
| Slice B baseline (flag off) | 0.9460 |
| Slice B, flag on (crops that changed) | 0.1230 |
| Slice B, final code (2 crops) | 0.0027 |
| `data/gold`, flag on (page pass $0.056, crops $0.112) | 0.1680 |
| Recorded answers for the two test fixtures | 0.0050 |
| **Total** | **2.7934** |

Replays of the 2026-10-14 `data/gold` baseline and of every baseline rescore cost nothing.

## What still fails

Development examples (gold id, what the pipeline wrote):

- **Nested complex fractions** (`8-5-simplify-complex-rational-expressions` eq-003, eq-013, eq-021): the
  text layer is a bag of numerators and denominators with no order, so neither layout nor a
  quote can cut them, and the crop is cut where the rows are.
- **Braces drawn as paths** (`5-2-solve-systems`, eq-014, 016, 023, 028, 029, 037, 040, 041: 12 of the 38 `span`
  bucket equations): the brace is not in the text layer, the crop is cut inside it, the equation
  step reads the two lines and writes no brace, the page pass writes `\left\{` and the two readings
  disagree, which is the flag that works (they are required decisions), but nothing repairs the span.
- **Nuclide prefix scripts** (`2-3-atomic-structure`, `_1^1 H` to `_9^19 F`): 20 undetected; the
  pass found four, as `2H` without the stack.
- **Chemical formulas and equations in running text** (`17-3-rate-laws`, `16-3-standard-reduction-potentials`):
  40 undetected, many of them formulas the answer key writes as MathML and the PDF prints as plain
  text with raised digits.
- **Single glyphs** (55): the answer key has an equation that is a lone `×`; not a defect of the pipeline.
- **Both readings agree and are wrong** (10 of 280 aligned equations the pass placed and both
  readings agreed on, A and B): a lone variable cut without the "=" the answer key keeps with it
  (`5-2-solve-systems` eq-046, 055, 056: `n =` against `n`), a nuclide without its atomic number
  (`2-3-atomic-structure` eq-012, 013), the first half of a complex fraction (`8-5` eq-002).

## What this says about a held-out run

The held-out gap to the Opus chatbot is -0.118 on the primary scorer
(docs/eval-secondary-metrics.md), and it is made of segmentation and the scorer, in about equal
parts. The scorer part does not change. Of the segmentation part, the 0.5.0 layout rules (exercise
numbers and systems) were estimated at about +10 points before this work; they are independent of it.
On the development sets the pass adds nothing measurable beyond them to the primary score. My
estimate for the held-out effect of turning it on, with the 0.5.0 layout as the baseline:

- primary `exact_lenient`: **0 points, plausible range -1.5 to +1** (the dev interval is -1.9 to
  +0.9 and dev is where it was tuned); it does not close the gap to the chatbots;
- symbols-correct end to end: **+0.5 points** (0.0 to +1.0 on dev);
- equations wrong in the symbols that nobody is asked about, per 100 gold: **down about 1.4**
  (4.8 to 3.5 on dev, interval -4.6 to +0.9 per 100 over 10 sections), with equation flag recall
  up from about 0.4 to 0.6;
- more inline equations, which a screen reader benefits from and the answer key does not count:
  detection precision falls by about 8 points (47 points on `data/gold`, whose text is full of
  quantities with units and bold vectors) and `unmatched predictions` rise by about half;
- cost $0.0016 to $0.0018 per page on top of $0.015.

### Sections a fresh held-out set needs

The comparison is the same sections with the flag on and off, so the registered paired design
applies (PROOF.md: cluster bootstrap over sections, with the sign-flip test for small
numbers of clusters). The standard deviation of the per-section difference on the dev sections is
what sets the number; at 80% power and a two-sided 5% level, `n = (1.96 + 0.84)^2 sd^2 / effect^2`:

| Effect to detect | Per-section sd on dev | Sections |
|---|---|---|
| +1 point of primary `exact_lenient` | 1.45 points | about 17 |
| +0.5 points of symbols-correct | 0.68 points | about 12 |
| -1.4 per 100 in symbols-wrong equations nobody is asked about | 4.1 per 100 | about 68 |
| Equation flag recall +17 points | equation-level (McNemar), not section-level | the registered sections (14 with equations) have about 270 symbols-wrong aligned equations per run, enough for a standard error of about 3 points |

The first row is what it takes to tell a +1 point effect from none, so confirming "no effect on the
primary score" needs at least that many sections, more than the 15 of the registered set. Two
caveats raise the number: the dev sections are ones the rules were tuned against, so their
differences are probably smaller and less variable than a fresh set's; and the two recorded model
runs of one system differ by 0.007 on equations (README), which is as large as the effect on the
symbols score, so a real comparison needs replicates (two runs on each side) as well as sections.

## Against the chatbot recipe, on the same development pages (2026-10-21)

The held-out run had the Opus chatbot (Purdue's sentence, the PDF and a 150 dpi image of every
page) ahead of ReadableSTEM 0.622 to 0.504, with the 0.4.0 pipeline. To see where that stands at
this commit, the same recipe was run on the same dev-v2 sections with the proof's own code
(`proof/chatbot.py`, `record_arms`, unchanged; `scripts/dev_chatbot.py` only chooses the sections
and scores with `proof.systems.equation_item`, so the held-out path is untouched): the Haiku arm on
both slices (10 sections, 114 pages), the Opus arm on slice B (5 sections, 49 pages), one run each
(the proof made two), answers recorded under `.local/chat-dev`. Spend: Haiku $0.256, Opus $2.657;
total live spend of this work **$5.71** of the $8 cap.

| Dev, flag off / on and the chatbot arms | A+B off | A+B on | A+B Haiku | B off | B on | B Haiku | B Opus |
|---|---|---|---|---|---|---|---|
| gold equations / PDF pages | 724 / 114 | 724 / 114 | 724 / 114 | 452 / 49 | 452 / 49 | 452 / 49 | 452 / 49 |
| primary `exact_lenient` | 0.486 | 0.482 | 0.514 | 0.597 | 0.589 | 0.580 | 0.586 |
| symbols, credited | 0.718 | 0.724 | 0.718 | 0.799 | 0.805 | 0.781 | 0.783 |
| detection recall | 0.797 | 0.803 | 0.855 | 0.872 | 0.872 | 0.945 | 0.801 |
| detection precision | 0.756 | 0.678 | 0.565 | 0.793 | 0.796 | 0.771 | 0.557 |
| equations with no gold partner | 186 | 276 | 477 | 103 | 101 | 127 | 288 |
| wrong in the symbols, nobody asked, per 100 gold | 4.8 | 3.5 | 14.1 | 4.2 | 3.8 | 16.8 | 2.4 |
| wrong on the primary, nobody asked, per 100 gold | 26.8 | 26.1 | 34.1 | 22.8 | 23.7 | 36.5 | 21.5 |
| required decisions per PDF page | 1.67 | 1.61 | 0 | 2.14 | 1.90 | 0 | 0 |
| cost per PDF page | $0.0149 | $0.0165 | $0.0022 | $0.0194 | $0.0196 | $0.0014 | $0.0542 |

The chatbots ask nobody about anything, so every wrong aligned equation is unasked. Their "no gold
partner" count is every equation the reader found beyond the gold, with no text-layer check (the
pipeline's own `unsupported` column cannot be computed for them). Chemistry: the Opus arm wrote
every formula as `\ce{...}` (mhchem), which the proof's converter, the one it uses for
ReadableSTEM's own LaTeX, cannot read, so its chemistry sections score near 0 (0 of 36 and 5 of 57
aligned); the held-out run had the same convention (all three systems near 0.04 on chemistry).
Without the two chemistry sections the comparison on the maths sections of slice B (3 sections,
359 gold equations, 37 pages) is:

| Slice B, maths sections only | off | on | Haiku | Opus |
|---|---|---|---|---|
| primary `exact_lenient` | 0.749 | 0.738 | 0.730 | 0.738 |
| symbols, credited | 0.919 | 0.928 | 0.969 | 0.975 |
| detection recall / precision | 0.969 / 0.804 | 0.969 / 0.817 | 0.992 / 0.743 | 0.994 / 0.736 |
| wrong in the symbols, nobody asked, per 100 | 2.5 | 2.2 | 2.8 | 2.8 |

Paired differences (same sections, cluster bootstrap over sections; with 5 to 10 sections the
intervals are wide and only indicative; `scripts/dev_paired.py`): Haiku minus flag off on the primary
score +2.8 points (-6.7 to +13.2, ten sections), on the symbols score 0.0 (-12.9 to +9.8), on
unasked symbols-wrong +9.3 per 100 (+0.1 to +27.1); Opus minus flag off on slice B: primary -1.1
(-11.1 to +12.6), symbols -1.5 (-19.4 to +9.1), unasked symbols-wrong -1.8 per 100 (-7.2 to +0.5).
The flag on against off: primary -0.4 (-1.9 to +0.9), symbols +0.6 (0.0 to +1.0), unasked
symbols-wrong -1.4 per 100 (-4.6 to +0.9).

**What this says.** At this commit, on these pages, the pipeline and the chatbot recipe are level on
the primary score (Opus 0.586, flag off 0.597, flag on 0.589 on slice B; Haiku 0.514 against 0.486
on both slices), where the held-out run at 0.4.0 had a 12-point gap. This is consistent with the
0.5.0 layout rules having closed most of it, as the analysis projected, but dev is not held-out
and these pages are rendered clean; it is evidence that the gap is much smaller, not a
measurement of it. Where the chatbots lead is the symbols-correct score on the maths sections (+5
points: they find and read the nested fractions and stacked nuclides the text layer cannot give)
and detection recall; where the pipeline leads is chemistry under the mhchem convention, wrong
equations nobody is asked about (Haiku 14 to 17 per 100 against 4 for the pipeline; Opus 2.4, below
the pipeline's 3.8 and 4.2) and cost (Opus is 2.8 times the pipeline's cost per page).

### Equations the chatbot gets right and the pipeline does not

Pooled A+B against the Haiku arm: the chatbot is exactly right on 372 of 724, the pipeline on 352
(flag off) or 349 (on); both on 312 or 314; only the chatbot on 60 (58 with the flag on), only the
pipeline on 40 (35). Slice B against the Opus arm: 265 against 270 (266 on), both 243 (245), only
the chatbot 22 (20), only the pipeline 27 (21).

The 60 (flag off) / 58 (on) equations the Haiku arm gets and the pipeline does not, by what the
pipeline did (`scripts/dev_gap.py`):

| What the pipeline did | off | on |
|---|---|---|
| no aligned prediction (undetected) | 27 | 25 |
| symbols right, the primary scorer says no (array against `cases`/`aligned`, `\left(`) | 15 | 15 |
| span cut short or extended | 10 | 10 |
| aligned, symbols differ inside | 4 | 5 |
| aligned, no proposal (the model answer is missing, a fraction cut in half) | 4 | 3 |

Against Opus on slice B (22 / 20): 10 undetected, 4 recognition, 4 no proposal, 3 scorer, 1 span.
With the flag on, 2 of the 60 become right; one is a glued exercise number the pass cut off
(`8-5 ... eq-046`: `258.\ \frac{...}` off, `\frac{...}` on).

Ten concrete cases (cases 1, 7, 8 and 10 are from the Haiku arm, the others from the Opus arm; gold is the symbol stream of the answer key; "span" is the text layer of the
region the pipeline cut):

| # | Section, gold | Pipeline (flag off) | Chatbot | Bucket |
|---|---|---|---|---|
| 1 | Chemistry 2.3 eq-011, `_1^1 H` | nothing aligned | `{}^{1}_{1}\mathrm{H}` | undetected (nuclide prefix scripts: the text layer has "H" and no atomic number in order; the model reads the picture) |
| 2 | Elementary Algebra 8.5 eq-015, `((1/3)+(1/6)) / ((1/2)-(1/3))` | nothing aligned | `\dfrac{\frac13+\frac16}{\frac12-\frac13}` | undetected (nested fraction, the rows are a bag) |
| 3 | 8.5 eq-013, `(2/(x^2-1)) / (3/(x+1))` | no proposal; the span is `𝑥−1 322` | `\dfrac{\frac{2}{x^2-1}}{\frac{3}{x+1}}` | no proposal (half of the fraction) |
| 4 | 8.5 eq-038 | `\frac{\frac{4y}{y+5}+\frac{2}{y+6}}{3y}` (denominator cut) | the full denominator | recognition (span cut) |
| 5 | 8.5 eq-046 | `258.\ \frac{\frac{8}{d^{2}+9d+18}}{\frac{12}{d+6}}` | the fraction | span: the exercise number glued on (fixed by the pass) |
| 6 | College Algebra 9.6 eq-160, `(3x^2-sqrt(2y^3))^12` | `(3x^{2}-\sqrt{2y^{3}})^{1}` | `\left(3x^2-\sqrt{2y^3}\right)^{12}` | recognition (the exponent 12 is outside the region's text) |
| 7 | authored matrices eq-019, `3[..]+7[..]=[..]` | the matrices without the leading 3 | `3\begin{bmatrix}...` | span (the 3 is outside the region) |
| 8 | Elementary Algebra 5.2 eq-024, `(1/2) x` | `\frac{1}{2}x=-2x+5` | `\tfrac{1}{2}x` | span extended over the next equation |
| 9 | authored exercises eq-044, braced system | `\left\{\begin{array}{rcl}...` | `\left\{\begin{array}{l}...` | scorer (three columns against one; same symbols) |
| 10 | authored matrices eq-038, three-line derivation | `\begin{array}{rcl}...` | `\begin{aligned}...` | scorer (same symbols) |

## Recommendation

**(a) Production.** Not yet. The case for the flag is the second reading: of the equations wrong in
the symbols, 58% instead of 41% reach a person, at the same decisions per page, for $0.0016 a page
(about 11% of the pipeline's cost). Against it: the gain is concentrated in slice A, the slice it was
tuned on (flag recall 0.33 to 0.70 there, 0.46 to 0.47 on B); every interval on the review numbers
includes 0; the primary score does not move; it adds about half as many equations again that the
answer key has no partner for (186 to 276 on 114 pages), each one a change to the output the page
pass decided alone and the review screen then may ask about; and it runs layout twice. If the
review-flag gain is the goal, it is worth trying only after a held-out check on fresh sections (c),
or after a variant that compares readings on layout's own spans and never changes them is built
and measured (not done here); the extra inline equations are the part to weigh.

**(b) What would close the rest of the gap**, by expected points on the dev pages and cost
(points are of gold equations on the 724 pooled; the held-out effect scales by the classes present):

| Rank | Change | Expected | Cost |
|---|---|---|---|
| 1 | Include the vector-drawn brace in a braced system's region (a layout rule, no model): 12 of the 38 span-bucket equations on dev | +1.7 points | days of work, $0 |
| 2 | Nuclide prefix scripts as one inline run (stacked mass and atomic number; `_1^1 H`): 20 undetected, all in one chemistry section | +2.8 points on chemistry sections, little elsewhere | days of work, $0 |
| 3 | Nested and inline fractions the text layer cannot order (21 undetected plus cut spans such as cases 3 and 4): propose a box from the picture, not a quote from the text layer, and let the equation step read that crop | +2 to +3 points | a design change away from "grounded in the text layer"; about $0.003 a page for the proposals |
| 4 | Write systems and derivations as `cases`/`aligned` as the answer key does: closes the 15 "scorer" cases | up to +2 points on the primary only; same symbols, so no real gain; the analysis already judged it gaming the scorer | $0 |
| 5 | Chemistry formulas in running text (40 undetected): inline detection for sub/superscripted formulas | +3 to +5 points on chemistry sections | hard; depends on how many chemistry texts the beta has |
| not worth it | the 55 single-glyph gold equations (a lone `×`); the page pass with a bigger model | 0 | the pass at medium already reads as well as the layout cuts, and a bigger model does not anchor what the text layer lacks |

**(c) A fresh held-out run.** Two different questions. (1) Has the 0.5.0 layout closed the
held-out gap? The dev result says the pipeline is level with the chatbots on maths, so the
registered comparison (ReadableSTEM -0.118 against Opus) is probably out of date. Re-recording
ReadableSTEM at this commit on the 15 registered sections costs about $1 to $2 per run (only the
crops that changed need new answers; the rest replay), $3 for two runs; those sections are no longer
blind (the analysis of 2026-10-20 read their errors), so it is a re-measurement, not a test. That is
worth doing now. (2) Does the page pass help? That needs fresh sections: the paired on/off design
needs about 17 sections for +1 point of the primary score and about 12 for +0.5 of the symbols score
(above), and a chatbot comparison needs far more, because the section-level spread of the
difference between systems is 0.21 on the primary score (held-out run): a 5-point difference needs
about 140 sections, a 10-point one about 35. So a fresh set of 20 sections (about 320 pages) would
settle the page pass: ReadableSTEM off and on, two runs each, about $20 (about $10 for one run of both settings), and the chatbots (Haiku
about $0.7, Opus about $17 per run) only if a head-to-head is wanted, which at that size can still
only detect a gap of 10 points or more.

## Two deterministic layout fixes (2026-10-21)

The page pass stays off (decision of 2026-10-21; this document, the ADR and the code are the record
of the experiment). Two of the failures it could not repair were fixed in layout, with no model:

1. **A stray brace.** OpenStax prints "Solve the system by substitution. {" with the brace at the
   end of the sentence's row and the equations in rows of their own to its right, so the system's
   crop started at its first equation and the model wrote no brace (12 of the 38 `span`-bucket
   equations on dev). `layout._stray_brace_cut` cuts the brace off the sentence and
   `_math_neighbours` takes it into the system (docs/layout.md, "A stray opening brace").
2. **Nuclides in running text.** The mass number raised and the atomic number lowered before an
   element's symbol (`14C`, `235U`, `4 2 He`) were raised text; they are one inline run now, so the
   equation step reads them and the speech says "carbon 14, atomic number 6"
   (`layout._nuclide_runs`). Two tests that pinned the old raised-text behaviour were rewritten.

Layout alone on all 28 dev-v2 documents (`--fake`, no model): matched gold equations 1,524 to 1,535
of 1,809; equations whose span text is within 0.99 of the gold 978 to 1,002 (Elementary Algebra
5.2: 53 to 80, the brace). The 121 PDFs of `scripts/layout_identity.py` (fixtures, development
material, development pages): 102 identical, 19 different, and the differences are in paragraphs and
equations only: no heading, figure, table, list item, kept page or page type changes, the
"scripts flattened" notice goes where a nuclide was raised text, and the ids of inline equations
shift (`make reproduce-rerun` passes).

With the model, flag off, recorded answers for what did not change and live calls for the new crops
(slice B has none: nothing there changes; spend $0.098):

| Dev, flag off | A before | A after | B before / after | A+B before | A+B after |
|---|---|---|---|---|---|
| primary `exact_lenient` | 0.302 | 0.313 | 0.597 / 0.597 | 0.486 | 0.490 |
| symbols, credited | 0.585 | 0.618 | 0.799 / 0.799 | 0.718 | 0.731 |
| span fidelity (0.99) | 0.338 | 0.430 | 0.624 / 0.624 | 0.517 | 0.551 |
| detection recall / precision | .673 / .688 | .691 / .627 | .872 / .793 | .797 / .756 | .804 / .730 |
| wrong in symbols, unasked, per 100 | 5.9 | 4.4 | 4.2 / 4.2 | 4.8 | 4.3 |
| cost per PDF page | $0.0116 | $0.0125 | $0.0194 | $0.0149 | $0.0155 |

Paired over the ten sections: primary +0.4 points (0.0 to +1.3), symbols +1.2 (-0.9 to +4.4), unasked
symbols-wrong -0.6 per 100 (-4.1 to +3.4). The estimate of +1.7 points for the brace alone was
about right for what it fixes (the brace equations are in the symbols score, +9 on slice A), but
most of them are still wrong on the primary scorer, which also wants `cases` and not
`array`; the nuclide fix moves chemistry detection (the authored chemistry document 64 to 70 of
108; Chemistry 2.3 7 to 12 of 38, the rest of its nuclides are cells of tables). Dev spend of the
whole task: $5.80 of $8.
