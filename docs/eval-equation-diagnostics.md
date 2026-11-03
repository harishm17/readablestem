# Equations on real papers: three diagnostics on the recorded runs (2026-11-02)

Four measurements on what is already recorded (the fourth, D2, is the last section): why maths typed in a table cell is missed, whether the equations
the pipeline misses or cuts wrongly carry glyph evidence its rules do not use, and why wrong equations with other
symbols were not flagged for review. They read the recorded items of the real-world runs
([eval-realworld.md](eval-realworld.md), [eval-equation-round3.md](eval-equation-round3.md)), the cached gold and the
cached PDFs; nothing calls a model and nothing is spent. They explain the runs; they do not change any code path or number
of the product.

| Script | Question |
|---|---|
| `scripts/eq_diag_tables.py` | A gold equation typed in a table was not aligned to a prediction: was the table not found, rotated, its cell kept as text, or the equation placed elsewhere in the order? |
| `scripts/eq_diag_glyphs.py` | Do undetected and span-wrong equations carry maths-font, italic-text-font or raised/lowered glyphs, and how common is each in the rest of the page? |
| `scripts/eq_diag_unflagged.py` | A wrong equation with other symbols was not a required decision: no text layer, no Unicode, or symbols the bag check does not count? |
| `scripts/eq_oracle_agreement.py` | Would a second, independent whole-page reading (the recorded Opus chatbot answers) fix, flag or find our errors? See [D2](#d2-an-independent-whole-page-reading-the-recorded-chatbot-answers). |

`scripts/eq_diag_common.py` holds what they share. Tests: `tests/test_eq_diag.py` (one fixture paper,
`tests/fixtures/layout/latex/calculus_notes.pdf`, and small generated PDFs).

## Method

- **Inputs.** The scripts need the real-world cache at `~/.cache/readablestem/realworld` (recorded runs under `runs/`,
  gold and cut PDFs under `gold/`, source papers under `papers/`). It is not in the repository; without it they stop.

- **Splits and runs** (gold equations): dev 2,350 (`rs-allow`), dev2 2,350 (`main0`), held-out 1,826 (`rs`), fresh 1,836
  (`fresh-on`), fresh2 1,639, fresh3 1,779, fresh4 1,600 (`after`). dev and dev2 are the same 25 papers with the gold made
  again, so sums leave dev out (11,030 gold in the sums). The table reading is reading B (the maths cells of a rebuilt
  table are equations). dev, held-out and fresh were run before the table step read maths cells, so their "no maths cell
  read" counts describe those runs, not the current code. `--run PART=NAME` picks another run.
- **Where a gold equation sits.** A gold record has its page and no coordinates, so each equation is found in the page's
  characters (PyMuPDF `rawdict`) by its text, after making a text layer and the gold write the same thing (asterisk
  operator, dashes, accents). A text found in several places cannot be told apart (a lone plus-or-minus sign): the
  diagnostics look at every place and say how many there were. A text found only in part (a floor bracket drawn as a rule,
  a missing star) is a partial match. 11,030 gold: 1,100 missed table-typed equations located in 972 cases, 914 exactly.
- **Layout** for D1 is `layout.analyze` with the table step's detection on, as deployed, run again on the cached PDFs
  with the code in this branch (no model). It found 39 of the 40 tables that had a maths cell in the recorded runs.

## D1: table-cell maths (1,504 table-typed gold; 1,100 missed in the sums)

Pooled over dev2, held-out, fresh, fresh2, fresh3, fresh4: of 1,504 gold equations typed in a table, 404 were aligned (275
exactly right) and 1,100 were not. Each missed one has the first cause that fits:

| Cause | All splits (n = 1,100) | dev2, fresh2-4 only (n = 870) | fresh4 (n = 344) |
|---|---|---|---|
| Predicted, but not where the gold's order expects it (an unmatched prediction with its text) | 121 | 107 | 70 |
| Not in the text layer | 7 | 6 | 0 |
| Rotated (a rotated page, or text not running left to right) | 273 | 273 | 0 |
| Table not detected (the text is in the page, no table region holds it) | 99 | 94 | 42 |
| Table detected, no cell of it became a maths cell | 282 | 72 | 10 |
| Table detected, other cells of it read as maths, this one stayed text | 318 | 318 | 222 |

What the 1,100 are:

- A lone `\pm` is 401 of them (225 rotated, 176 in detected tables), `^{**}`/`^{*}` 38, `\checkmark` 31, `<0.001` 29. By
  the gold's length: 64 bare numbers, 575 single symbols, 278 of two or three symbols, 183 longer.
- Four papers hold 658 of the 1,100: 2305.00609 (257, all rotated: `sidewaystable`), 2309.04589 (180, 176 of them `\pm`),
  2405.01155 (124, a held-out paper, run before cells were read), 2409.12662 (97: 51 predicted out of order, 42 not detected,
  all floor-bracket formulas such as `\lfloor T^{1/2}\rfloor`).
- fresh4 (424 table-typed gold, 80 aligned, 59 exact, 344 missed): 222 are in detected tables whose other cells were read;
  176 of those are `\pm` in one paper. 42 are in one paper whose tables were not found.
- Where the text sits when no table is found (99): inside an equation element 39, a paragraph 25, no element 23, a figure 9,
  a list item 3.

## D3: glyph evidence of misses (11,030 gold)

Located equations with at least one character carrying the kind of evidence. "Some" is the upper bound (at some place the
text is found), "every" the lower bound (at every place).

| Group | n located | Any evidence, some / every | Maths font, some / every | Italic text font, some / every | Raised, some / every | Lowered, some / every |
|---|---|---|---|---|---|---|
| Undetected, running text | 802 | 70% / 45% | 57% / 41% | 7.7% / 3.7% | 13% / 7.7% | 26% / 15% |
| Undetected, table cell | 811 | 92% / 87% | 91% / 86% | 1.1% / 0.2% | 15% / 14% | 8.9% / 6.5% |
| Undetected, figure or algorithm | 533 | 94% / 85% | 89% / 82% | 3.0% / 1.7% | 6.8% / 2.8% | 20% / 13% |
| Span wrong, cut short | 281 | 98% / 98% | 97% / 97% | 6.8% / 6.8% | 32% / 32% | 45% / 45% |
| Span wrong, ran on | 294 | 97% / 73% | 90% / 68% | 10% / 3.7% | 21% / 13% | 38% / 26% |
| Detected and right (control) | 6,833 | 96% / 72% | 92% / 70% | 12% / 4.0% | 21% / 15% | 44% / 31% |

By font family, undetected running text (some / every): CMMI 38% / 31%, CMSY 18% / 16%, CMEX 3.0% / 0.6%, MSBM 0.9% / 0.9%,
another maths font 14% / 5.7%.

Background: of the 1,146,894 characters of the same pages outside every gold equation, 4.1% carry some evidence (maths
font 1.5%, italic text font 2.3%, raised 0.3%, lowered 0.4%).

Span-wrong equations, looking only at the characters of the wrong edge (equations whose edge was found): of 251 cut short,
in 182 (73%) the characters the span lacks carry maths-font, italic or script evidence; of 207 that ran on, in 103 (50%)
the extra characters carry no evidence at all (plain upright text).

Located counts: 802 of 863 undetected running-text, 811 of 818 table cell, 533 of 543 figure or algorithm, 281 of 313 cut
short, 294 of 310 ran on, 6,833 of 7,078 control. Windows of a text found in several places are the main uncertainty;
equations located at one place only: 260, 325, 135, 241, 112 and 2,689 of those groups.

Reading the table: an undetected equation with a character in a font `parse.MATH_FONT` already matches is one whose
glyphs the layout saw, and which was still not detected. The rates say how many carry some evidence, not that a rule built
on it would be precise; the background rate is the number to set against them.

## D4: silent errors with other symbols (11,030 gold)

A silent error is an aligned, wrong equation that is not a required decision. Of 1,101 silent errors in the sums, 532 have
other letters, digits or symbols than the gold (the "other symbols" bucket of
[eval-equation-rootcause.md](eval-equation-rootcause.md); dev's count here is 106 against that report's 105, with the
current scorer), 349 are a span cut at an edge, 216 are scorer differences, 4 are the same symbols arranged differently.
The `text_layer_symbols_differ` check was replayed on each of the 532 with the recorded span text (at most 300 characters)
and MathML:

| Reason it was not flagged | n | Share |
|---|---|---|
| No text layer on the span | 0 | 0% |
| No Unicode: private-use or `(cid:N)` glyphs, or no letter, digit or Greek letter | 66 | 12.4% |
| The bag differs and the check fires on replay | 0 | 0% |
| The bag agrees; the same letters in another style (bold against bold italic, script, upright against italic) | 363 | 68.2% |
| The bag agrees; wrong symbols are brackets | 8 | 1.5% |
| The bag agrees; primes or accents | 2 | 0.4% |
| The bag agrees; arrows or relations | 11 | 2.1% |
| The bag agrees; operators or punctuation | 14 | 2.6% |
| The bag agrees; structure (scripts, fractions, groups) | 20 | 3.8% |
| The bag agrees; letters or digits (the text layer shares the error) | 43 | 8.1% |
| The bag agrees; other | 5 | 0.9% |

By split (gold, silent errors, other symbols): dev 2,350, 266, 106; dev2 2,350, 200, 88; held-out 1,826, 183, 52; fresh 1,836,
275, 164; fresh2 1,639, 144, 56; fresh3 1,779, 183, 109; fresh4 1,600, 116, 63. The style differences, token by token
(the prediction's style against the gold's): plain against upright text 173, bold against bold italic 74, a script glyph
against script style 56, bold against plain 28, a script glyph against double-struck 27.

## D2: an independent whole-page reading (the recorded chatbot answers)

The Opus chatbot arm of the real-world reports (the whole PDF in, LaTeX out, no checks; [eval-realworld.md](eval-realworld.md))
is a second reading of every page it was run on. Scored by the document task's scorer like our runs, its answers align to
the same gold, so for each gold equation both readings can be compared with `pipeline/math_canonical.py`. Chatbot answers
were recorded for 6 of dev's 25 papers (the same 6 for dev2) and for 15 papers each of held-out, fresh2, fresh3 and fresh4;
the numbers below are on those papers only, with our run as in the other sections, and the chatbot items made again from
the recorded answers with the current converter. Pooled: dev2, held-out, fresh2-4, 66 papers, 7,390 gold equations (dev is not
added up with dev2). Intervals are 95% cluster bootstraps over papers; with 6 to 15 papers a split, the split columns are wide.

`uv run python scripts/eq_oracle_agreement.py [--at tree|notation|spoken] [--json OUT.json]`

Definitions. Right is `exact_lenient`. **Agree**: the two readings are the same under canonical comparison
(`Kind.NOTATION`: canonical LaTeX or one MathML tree) and the spans are within one glyph. The chatbot gives no extent, so the
span test is a stand-in: our span's text layer and the chatbot's reading, reduced to symbols, differ in at most one symbol;
a span with no text layer cannot be checked and does not pass. Both readings aligning to one gold equation already means
their extents overlap it.

| | dev | dev2 | held-out | fresh2 | fresh3 | fresh4 | pooled |
|---|---|---|---|---|---|---|---|
| papers | 6 | 6 | 15 | 15 | 15 | 15 | 66 |
| gold equations | 546 | 546 | 1,826 | 1,639 | 1,779 | 1,600 | 7,390 |
| (a) ours right | 59.5% | 74.9% | 58.3% | 64.2% | 65.4% | 61.3% | 63.2% [55.7, 70.2] |
| chatbot right | 78.4% | 79.9% | 63.6% | 68.1% | 67.8% | 63.6% | 66.8% [58.5, 74.4] |
| union (right in either) | 80.4% | 82.4% | 70.9% | 76.1% | 71.5% | 68.1% | 72.4% [64.3, 79.8] |
| union minus ours, points | +20.9 | +7.5 | +12.5 | +11.9 | +6.1 | +6.9 | +9.3 [+7.4, +11.2] |
| (b) agree and span (n of ours read) | 318 of 394 | 394 of 455 | 903 of 1,425 | 913 of 1,293 | 1,004 of 1,407 | 877 of 1,129 | 4,091 of 5,709 |
| P(ours right given agree) | 96.5% | 96.4% | 93.8% | 93.4% | 94.9% | 96.5% | 94.8% [92.9, 96.5] |
| same, canonical agreement only | 96.0% | 95.4% | 93.2% | 93.0% | 94.3% | 95.4% | 94.1% [92.1, 95.9] |
| (c) our wrong readings (n) | 69 | 46 | 360 | 241 | 244 | 149 | 1,040 |
| disagreements, both read (n) | 69 | 44 | 346 | 267 | 353 | 207 | 1,217 |
| recall of "disagree" | 73.9% | 50.0% | 60.0% | 61.8% | 66.4% | 69.1% | 62.8% [54.5, 71.0] |
| precision of "disagree" | 73.9% | 52.3% | 62.4% | 55.8% | 45.9% | 49.8% | 53.7% [47.2, 60.7] |
| our silent errors (n) | 52 | 32 | 183 | 144 | 183 | 116 | 658 |
| silent errors a disagreement would flag | 73.1% | 50.0% | 57.4% | 54.2% | 65.6% | 65.5% | 60.0% [51.2, 69.1] |
| (d) our undetected gold (n) | 152 | 91 | 401 | 346 | 372 | 471 | 1,681 |
| of those, found by the chatbot | 61.2% | 46.2% | 32.7% | 46.5% | 12.1% | 20.6% | 28.3% [17.5, 44.2] |
| of those, found and right | 47.4% | 29.7% | 22.7% | 31.2% | 9.4% | 11.3% | 18.7% [11.4, 29.9] |

- **(a)** Picking the right one of the two readings for every equation (an oracle: nobody knows which that is) would
  add 9.3 points on the pooled papers (95% interval 7.4 to 11.2), and at least 6 points on every split. The chatbot alone
  reads 66.8% right to our 63.2% on these papers (the intervals overlap widely; they are the same papers read by both).
  About a third (35.6%) of our wrong readings are read right by the chatbot.
- **(b)** When the two agree, ours is right 94.8% of the time (92.9 to 96.5), 93.4% to 96.5% on each split. The same with
  agreement at `Kind.TREE` only (one MathML tree, no folding of LaTeX): 96.0% (94.3 to 97.5), 4,022 of 5,709 read; 94.7% to
  97.4% on the splits other than dev. The 4 to 5 in 100 agreed readings that are wrong are errors both readers make (the
  same wrong reading on the same page), so agreement is not independent evidence for every equation. The chatbot's own
  readings are right 94.8% of the time under the same agreement.
- **(c)** "The two readings differ, so ask" catches 62.8% of our wrong readings (54.5 to 71.0), and 53.7% of the
  disagreements are our wrong readings (47.2 to 60.7); counting a missing chatbot reading as a disagreement raises recall
  to 79.6% and lowers precision to 51.2%. Of our 658 silent errors (wrong, no required decision), 60.0% would have been
  flagged (70.5% with a missing reading counted).
- **(d)** Of the 1,681 gold equations we did not detect, the chatbot found 28.3% (17.5 to 44.2) and read 18.7% right. This
  varies from 61% (dev) to 12% (fresh3): the cause (pages the chatbot skipped, equations it wrote as prose, or
  alignment) is not split here.

Limits. The chatbot's page-level answers are one model with no span of their own, so "within one glyph" is tested against
our text layer and is the weakest part: it removes few cases (4,309 canonical agreements, 4,091 with the span test). The
6 dev papers are 24% of the 25 papers of dev. A disagreement is worth as much as the chatbot is independent of our
readers; an equation both get wrong the same way shows no sign. Whether a page witness read with the span anchored by a
quote would do better is not measured here. The union and the flag numbers are ceilings of what the recorded chatbot
answers allow, not what any product reads.

## Inline span edges from glyphs (T11, 2026-11-03)

`scripts/eq_span_edges.py --part PART [--out F] [--compare F]` runs the layout alone (no model, no cost) over the cut PDFs
of a real-world part, aligns the equations it finds to the gold as the document task does with no model answers, and
counts for the inline gold equations: detection recall by gold length, precision (aligned predictions over predicted
inline equations), and span edges. An **edge error** is an aligned pair whose text layer differs from the gold in letters
or digits (after folding the code points a text layer and a gold spell differently): letters or digits only the span has
are "ran on", only the gold has "cut short". The 390 aligned pairs of dev2 below text-layer fidelity 0.99 are mostly a
different print of the same maths (an accent the text layer has no glyph for, scripts in another order): 289 of the 390 are
"inside" and are not counted as edge errors. The rules are in docs/layout.md ("Inline span edges from glyphs"); the dev2
errors were read to find them, the other four parts were only measured.

| part | inline gold | recall (found) | edge error, of gold | precision (predicted) |
|---|---|---|---|---|
| dev2 (tuned on) | 2,153 | 0.808 to 0.814 (1,739 to 1,752) | 4.7% to 3.8% (101 to 82) | 0.879 to 0.902 (1,978 to 1,943) |
| held-out | 1,739 | 0.779 to 0.780 (1,355 to 1,357) | 7.5% to 6.8% (130 to 119) | 0.875 to 0.880 (1,549 to 1,542) |
| fresh2 | 1,555 | 0.748 to 0.767 (1,163 to 1,193) | 4.2% to 3.3% (66 to 51) | 0.883 to 0.910 (1,317 to 1,311) |
| fresh3 | 1,637 | 0.725 to 0.725 | 2.4% to 2.3% (40 to 37) | 0.892 to 0.898 |
| fresh4 | 1,513 | 0.658 to 0.658 | 3.6% to 3.5% (54 to 53) | 0.930 to 0.931 |
| held-out and fresh2 to 4 (replay only) | 6,444 | 0.730 to 0.734 (4,701 to 4,733) | 4.5% to 4.0% (290 to 260) | 0.893 to 0.902 (5,267 to 5,245) |

On dev2, 14 gold equations are found that were not and none is lost; 31 aligned spans changed. The papers whose inline predictions change: dev2 11 of 25, held-out 2 of 15, fresh2 7, fresh3 3, fresh4 5.
Gold equations found that were not, in the four replay parts: 39 (held-out 2, fresh2 35, fresh3 0, fresh4 2); lost 7
(fresh2 5, fresh4 2): a name that touches a bracket now takes in the words around the formula the gold set apart
(`ARFIMA(1, d(m), 1)` where the gold has `d(m)`), so the prediction no longer aligns to the shorter gold formula although
it holds all of it. Exact and spoken-equal scores need a model's reading and are not computed here; the formulas read
are the same glyphs, so only the crop's edges move.

Other layout, 148 PDFs of `scripts/layout_identity.py` (the fixtures, `data/`, the development pages cached for the eval;
before and after, no model): 146 identical, 2 different, both TeX papers of the table sets. `real-tables/2407.13734`: 197
to 193 elements, because `x_{1:T}` is one formula (its `T` was an equation element of its own, with two more fragments, a
paragraph's marker numbers shift); `real-tables-heldout/2311.16525`: `\sim 28%` keeps its percent sign. The letters and
digits of every page are the same before and after, inline maths included (0 lost, 0 gained).

What this does not show: no model reads the changed spans here, so whether the crop reading improves is the next run's
question; the gold's edges are the authors' `$`, and about a dozen of the 82 remaining dev2 errors are an author setting a relation as text
(`G = $6.6 \times 10^{-11}$`), which glyphs cannot tell from one formula; the nuclide rule (`^{20}\mathrm{Mg}` against
`20Mg`) is a gold convention left as it is (about a dozen dev2 pairs).

## Limits

- Counts describe the recorded runs with the code at the time they were made (D1's layout and D4's replay use the code in
  this branch). The gold's equation boundaries are the authors' `$`, so some "missed" formulas are the authors' typing.
- D3's windows come from matching text, not coordinates; a text found in several places gives two bounds, and a hit in a
  line of a page can be another occurrence of the same text.
- A lone `\pm` is a symbol in a table cell; whether the gold should count it as an equation is a scoring question this
  report does not settle.
- Any rule built from these tables cannot be validated on these same splits: they were looked at here, so only a fresh,
  never-used set can show whether a rule works.
- Nothing here was tuned: no threshold was chosen on a split, and none of the numbers is a claim about a system.
- The calibrated flag policy that D6 asked for (features of the recorded runs, a fit on development, a replay on fresh2 to fresh4) is in [eval-flag-policy.md](eval-flag-policy.md).
