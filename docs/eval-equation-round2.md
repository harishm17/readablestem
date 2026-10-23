# Equations on real papers, round 2: two more fixes and a third registered test (2026-10-23)

[eval-equation-rootcause.md](eval-equation-rootcause.md) ended level with Haiku (0.626 against 0.622) and 5.6 points
below Opus on 15 fresh papers, with maths in table cells and a notation convention (`\mathrm` against `\text`) named
as what stays. This round fixed what could be fixed without a prompt change and tested it once on a third set of 15
papers, registered before any system ran (`data/realworld/PREREGISTRATION-FRESH3.md`, hashed in
`REGISTRATION-fresh3.sha256`).

**The short version.** The fixes are worth +0.6 points on the registered score (0.614 to 0.620) and +1.8 points when
the maths in table cells counts (0.636 to 0.654). **The registered claim, that ReadableSTEM beats each chatbot on
both scores, is not met.** On the registered reading ReadableSTEM is 5.8 points below Opus (0.620 against 0.678; 97.5%
interval -0.155 to -0.003, sign-flip p 0.081) and level with Haiku (0.620 against 0.648, no difference this set can
detect). Counting the maths in table cells, it is level with Haiku (0.654 against 0.648) and 2.4 points below Opus
(97.5% interval -0.053 to 0.000). The silent-error limit is met: +0.056 per 100 gold against the limit of +0.5. On
this set ReadableSTEM and the chatbots write about the same number of wrong equations nobody is asked about (10.2
against 11.0 for Opus and 12.3 for Haiku per 100 gold), and ReadableSTEM is the only arm that flags any (24.5% of its
symbols-wrong equations are a required decision; the chatbots flag none). Spend $9.73 of the $16 cap.

## 1. What was changed

- **Column order** (`pipeline/layout.py`): a column's full-width line is no longer a section break, and a caption held
  with its column fills the column. Text that was read in the wrong order across columns is read in the right one.
- **Table cells of words with maths** (`pipeline/table_cells.py`, `pipeline/table_html.py`): a cell of words with a
  formula in it is read as words and maths, not as one formula.
- **Evaluation**: `scripts/rw_notation_neutral.py`, a notation-neutral secondary score that does not count
  `\mathrm`, `\textrm`, `\mbox`, `\textnormal`, `\operatorname` and `{\rm }` as different from `\text`, applied to the
  prediction and the gold alike and to every arm; reading B (`pipeline_eval.with_table_maths`), which counts each
  formula in a rebuilt table's cells as one inline equation, as the chatbots' HTML is read; and
  `scripts/realworld_refresh_gold.py`, which rebuilds the gold with the current converter, because the converter had
  changed under the stored gold.

The page math pass stays off, the equation prompt is unchanged (`equation-68ebbbf9`), and nothing was tuned on the third
set. A render-and-compare repair loop was considered and not built (it could see 1 of 282 silent errors; see the
previous page).

## 2. The third registered set

15 arXiv papers, 73 pages, 1,779 gold equations (142 display), CC BY 4.0 (14) or CC0 (1), drawn by the order of the
earlier splits continued. One run per arm, every answer recorded (`runs/fresh3`): ReadableSTEM before (the source of
main at 8555f6c, tables and notation memory on, as in production), ReadableSTEM after (this branch, seeded with the
before run's answers so a request the two share is answered once), and the chatbot recipe on Haiku and Opus.

| exact_lenient, 15 papers | ReadableSTEM | before | Haiku | Opus |
|---|---|---|---|---|
| registered (reading A) | 0.620 [0.431, 0.782] | 0.614 | 0.648 [0.453, 0.803] | 0.678 [0.472, 0.842] |
| notation-neutral (A) | 0.633 [0.439, 0.799] | 0.627 | 0.657 | 0.686 |
| table-cell maths counted (B) | 0.654 [0.454, 0.807] | 0.636 | 0.648 | 0.678 |
| notation-neutral and B | 0.667 [0.464, 0.826] | 0.649 | 0.657 | 0.686 |
| detection recall (A) | 0.752 | 0.745 | 0.771 | 0.788 |
| wrong, not asked, per 100 gold (A) | 10.2 | 10.1 | 12.3 | 11.0 |
| cost per PDF page (USD) | 0.038 | 0.037 | 0.0023 | 0.088 |

Paired differences (reading A, registered score, 97.5% intervals with k = 2): ReadableSTEM minus Opus -0.058
[-0.155, -0.003], p 0.081; minus Haiku -0.028 [-0.125, +0.035], p 0.417; after minus before +0.006 [0.000, 0.013],
p 0.25. On the notation-neutral score: minus Opus -0.053 [-0.148, 0.000], p 0.132; minus Haiku -0.024. Under reading B
the registered score gives minus Opus -0.024 [-0.053, 0.000], p 0.153 and minus Haiku +0.006 [-0.029, +0.043].
The intervals are wide: 15 papers, one extractor's conventions, one run per arm (nothing is run twice to confirm a number).

## 3. Where ReadableSTEM still loses

- **Maths typed in a table** (100 gold equations in `table` environments): 0.010 right as elements of their own (0.04 found), 0.61
  right once table-cell maths counts (notation-neutral), against 0.73 for Opus and 0.68 for Haiku. This is the larger part of the
  registered gap: with the tables feature on, the maths of a cell is part of the table, not an equation of its own.
  The 257 equations in `sidewaystable` are found by no arm (all four score 0.000 there), so they weigh equally on all four.
- **Equations typed in text**, the largest group (1,306): 0.809 against 0.822 (Opus) and 0.802 (Haiku), on the
  notation-neutral score; level.
- **Display equations**: 0.542 against 0.528 and 0.486, with 97% of them found (Opus 89%).
- **The edges of inline spans** are cut wrong more often (59 too short and 15 too long, against 9 and 7 for Opus),
  and short inline expressions are found less often than by Opus (detection recall 0.752 against 0.788, 3.6 points).

## 4. What follows

The fixes are small. On the 25 development papers they moved the score by +0.4 points (0.730 to 0.734, table-cell
maths counted, notation-neutral), and here by +0.6 points registered and +1.8 with table-cell maths counted. The remaining gap on running text is the edge of inline spans, which is deterministic and the
next thing to fix. The gap on maths in table cells is partly a reading choice: the registered reading counts a cell's
maths as no equation, so detection cannot close it there; which reading to publish is a decision, not a measurement. No
claim of beating the chatbot recipe on equations is made. What the data support is: about level with Haiku, behind
Opus by 2 to 6 points depending on the reading, no more wrong equations nobody is asked about than before the fixes, and
flags on a quarter of its symbols-wrong equations where the chatbots flag none.

Commands: `scripts/realworld_report.py --part fresh3 --tag after --extra-arm before=before [--reading cells]
[--neutral]`; results in `data/realworld/results-fresh3*.json`.
