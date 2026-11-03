# Glyph checks for equations, replayed on recorded runs (2026-11-03)

What `EQUATION_GLYPH_CHECKS` does (docs/steps.md, "Glyph checks"), measured with no model call: the recorded answers of the
fresh4 and development runs of [eval-equation-round3.md](eval-equation-round3.md) are replayed through the pipeline
(`scripts/realworld_run.py rs --offline --line-context [--glyph-checks]`, the recorded `after` and `line1` answers as the
seed). The setting is the only difference between a pair of runs.

## Method

- **Baseline reproduces the record.** The code of the recorded runs (commit `44d55da`, `FEATURE_TABLES`,
  `FEATURE_NOTATION_MEMORY` and `EQUATION_LINE_CONTEXT` on) answers every request from the recorded answers (fresh4: 1,460
  hits, 0 misses; development: 2,729 hits, 0 misses) and gives the recorded numbers (fresh4 flag recall 0.192, precision
  0.294, 2.73 decisions a page). On today's main the same replay misses 78 requests of fresh4 (layout and crop drift since
  then), which would show up as failed fixes, so the replays above are made with this change applied to that commit.
- **A new flag on a cheap answer asks the main model again**, a request nobody recorded: those come back "refused" and the
  cheap answer stands. The replay therefore shows the flags the change adds, not what a second reading would then do. Such
  requests: 2 on fresh4 and 6 on development (about $0.004 each at the recorded price, so under $0.03 for a development set).
- Numbers are over the aligned equations of the set (fresh4: 1,090 of 1,600 gold; development: 1,940 of 2,350), the
  registered metrics of `scripts/realworld_report.py` (reading B, "cells"). "Wrong" is not `exact_lenient`.

## Result

| | fresh4 before | fresh4 after | development before | development after |
|---|---|---|---|---|
| aligned equations wrong | 141 | 128 | 287 | 282 |
| `exact_lenient`, reading B | 0.613 | 0.621 | 0.735 | 0.737 |
| wrong, not asked, per 100 gold | 7.25 | 6.38 | 10.13 | 10.09 |
| flag recall (symbols-wrong aligned) | 0.192 | 0.214 | 0.287 | 0.280 |
| flag precision | 0.294 | 0.301 | 0.329 | 0.327 |
| decisions a page, all kinds | 2.73 | 2.68 | 3.68 | 3.59 |
| right equations that became wrong | | 0 | | 0 |
| wrong equations that became right | | 13 | | 4 |

**The target of intervention 1 (flag recall 0.33 or more from the extent) is not met, and the replay says why.** Of the 18
fresh4 equations that the 2026-10 "before" run flagged and got wrong, and the "after" run (line context) did not flag, 16
were flagged by `below_accept` (the model's confidence), not by a symbol mismatch: the line context made the model more sure
of itself, so fewer low-confidence flags. The bag check cannot add flags for them. On the recorded line-context run no
silent error has a differing bag (diagnostic D4: 0 of 63 on fresh4), so comparing with other glyphs cannot find a new one.

What the extent does:

- It removes false symbol flags. A reading that reaches past the box now meets the glyphs it covers: `symbol_mismatch`
  reasons 40 to 28 on fresh4 and 96 to 65 on development, and `sign_mismatch` 12 to 11 and 10 to 6. Four fresh4 and
  six development equations that were right and were a decision are not any more.
- It costs recall where a glyph beside the box explains a wrong reading. On development 32 readings were extended; 16 were
  wrong (gold), against 15% wrong over all aligned equations. Five wrong equations that were flagged by a symbol mismatch
  are not any more (a unit `s` taken in after `915`, the letters of the next display line explaining a hallucinated sum).
  So "the glyphs beside the box make up the reading" is weak evidence of a right reading, and an extended reading is a
  useful feature for the calibrated policy of the plan (intervention 4), not a reason to stop asking.

What the style check does:

- `glyph_style_mismatch` fired 2 times on fresh4 (both wrong equations) and 6 on development. Of the 4 that were not
  a decision already, 2 were wrong equations and 2 are readings the lenient scorer calls right (an upright `c` in a
  subscript that the PDF draws in italic). A first version flagged a calligraphic `A` of Latin Modern's symbols font,
  whose flags say "italic": symbol and extension fonts are now left out of `glyph_style` (`_MATH_SYMBOL_FONT`).
- The larger effect is in what it writes. The 13 fresh4 equations are bold italic letters (`\bm`, `\boldsymbol`) that the
  text layer calls plain bold (`LMMathItalic10-Bold`: its flags say bold and italic, the code point says bold):
  `restyle_bold_italic` and the bold-letter fix of running text now write `\boldsymbol` for them, so they are right and no
  decision is asked.
- Silent errors of the "same letters in another style" kind (`scripts/eq_diag_unflagged.py`): fresh4 44 before, 31
  after; development 55 before, 50 after. Of the 31 left on fresh4, 15 are a `\mathcal` letter that the scorer reads as a
  different token from the gold's script one, 14 are `\text{RE}` against `\mathrm{RE}` (upright in both, so the reading
  is right) and 3 are script against bold italic; 5 are a bold Greek letter the gold writes `\boldsymbol` (not compared:
  Greek letters are left out, their style depends on case and package). On development 21 are a bold letter in the gold
  that the reading has plain, which the text layer does not show as bold. The pooled sets without a line-context
  recording were not replayed, so the pooled 363 style errors of the diagnostics are not all reached.

## What was checked and what was not

- Right equations never became wrong; the 13 and 4 equations that became right are the bold italic ones.
- Not measured: the effect of the extra main-model readings the new flags ask for (the cost above, and whether the second
  reading agrees), the other registered sets (fresh2 and fresh3 have no line-context recording), and production traffic.
  The setting stays off until someone decides the decision load is worth it.
- The 2026-10 runs recorded the span's text only, so a replay needs the PDFs (`~/.cache/readablestem/realworld`) and the
  replay commands above; neither is in the repository.
