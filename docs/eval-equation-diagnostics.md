# Equations on real papers: three diagnostics on the recorded runs (2026-11-02)

Three measurements on what is already recorded: why maths typed in a table cell is missed, whether the equations
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

`scripts/eq_diag_common.py` holds what they share. Tests: `tests/test_eq_diag.py` (one fixture paper,
`tests/fixtures/layout/latex/calculus_notes.pdf`, and small generated PDFs).

## Method

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

## Limits

- Counts describe the recorded runs with the code at the time they were made (D1's layout and D4's replay use the code in
  this branch). The gold's equation boundaries are the authors' `$`, so some "missed" formulas are the authors' typing.
- D3's windows come from matching text, not coordinates; a text found in several places gives two bounds, and a hit in a
  line of a page can be another occurrence of the same text.
- A lone `\pm` is a symbol in a table cell; whether the gold should count it as an equation is a scoring question this
  report does not settle.
- Nothing here was tuned: no threshold was chosen on a split, and none of the numbers is a claim about a system.
