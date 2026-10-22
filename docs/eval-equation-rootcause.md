# Equations on real papers: why a chatbot did better, what was fixed, and a fresh test (2026-10-21)

[docs/eval-realworld.md](eval-realworld.md) found that ReadableSTEM writes about as many equations exactly right on
arXiv PDFs as a free chatbot (held-out 0.583 against 0.641 for both Haiku and Opus; development 0.562 against Haiku's
0.687) and named symptoms: maths in tables and figures, the edges of inline spans, short inline expressions not
found. This page is the root-cause work on those symptoms, done as debugging: evidence from the recorded outputs first,
at no cost; then fixes for the mechanisms that carry the gap; then a test on a fresh set of papers registered before any
system ran on it.

**The short version.** On the 25 development papers ReadableSTEM was 12.4 points behind Haiku; five mechanisms carried 12.1 of them: short variables set in an italic text font (3.9), mathematics in table cells (2.9), in captions (1.7),
the edges of inline spans (3.6) and `\bm` read as `\mathbf` (0.6). Reading the equations it found is not where it loses (its wrong readings are 6.6% of the gold against Haiku's 9.1%), and a render-and-compare repair loop
could see 1 of its 282 silent errors, so it was not built. The fixes are deterministic, from the text layer and the glyph boxes, with no prompt change. On development they take ReadableSTEM from 0.553 to 0.680 (+0.129 [+0.079, +0.181]),
level with Haiku (0.677) and behind Opus on its 6 papers, and the silent errors fall from 12.0 to 8.8 per 100 gold. **On 15 fresh papers, registered before any system ran, the fixes are worth +0.068 [+0.031, +0.116] (0.558 to 0.626),
the silent errors do not rise (9.76 to 8.42 per 100; the limit was +0.5), and ReadableSTEM is level with Haiku (0.626 against 0.622, +0.004, nothing detectable) and 5.6 points below Opus (0.682; the 97.5% interval -0.109 to +0.007).
The registered claim "clearly beats the chatbot recipe" is not met.** It leaves clearly fewer silent errors than either chatbot (8.4 against 16.3 and 13.7 per 100 gold). What stays: maths in table cells, formulas that wrap or are typed
apart, and a notation convention (`\text` against the authors' `\mathrm`) the scorer counts as 4 points for the chatbots and 1.5 for ReadableSTEM on this set. Spend $13.55 of $18.


## 1. Method, and a correction to the comparison

**Evidence.** For every gold equation of the 25 development papers (2,350) the outcome of ReadableSTEM and of each chatbot is
taken from the recorded runs (`runs/dev/...`; nothing is re-run) and every failure gets one mechanism, the first rule that fits
(`scripts/rw_gap_table.py`): not detected (by where it is typed: a table cell, a caption, an algorithm, running text of one to
three tokens, longer or display), the span cut at an edge (by what differs there: parentheses of the sentence, a unit after
a number, a word of the sentence, a citation, other), read wrongly (by what the author typed: bold italic `\bm`, upright
text, script letters, other), the scorer's tree differs while the symbols agree, no proposal. "Points" below are
ReadableSTEM's failures with the chatbot right, minus the chatbot's failures with ReadableSTEM right, over the 2,350 gold.

**The comparison is fair on pages, input and scoring, and unfair in one place that is now corrected.**

- *Pages and input.* The three arms get the same cut PDF (4 to 6 pages of each paper); the chatbots get the PDF and a 150 dpi PNG of
  every page. No development paper was refused (Haiku 25 of 25 `ok`, Opus 6 of 6). On the held-out part Opus returned a refusal for
  one paper (176 equations scored as found nothing, as registered); the sensitivity without it is in `eval-realworld.md`.
- *Scoring.* One aligner and one `exact_lenient` for every arm (`document_page_item`; the chatbots' HTML read by `proof/systems.equation_item`).
  Nothing is lenient to one arm only.
- *The converter changed under the gold.* The gold's MathML was made with the converter of 2026-10-21 morning; the chemistry work then
  changed it (a script after a bracket groups the bracket: `(X^\top X)^{-1}`; docs/chemistry.md "Side effect"). `scripts/realworld_build.py verify`
  now finds 28 papers whose gold rebuilds differently. A ReadableSTEM run made today is scored by the new converter against the old gold
  (its primary fell by 1.1 points, 0.5634 to 0.5528, on the same answers and a new prompt), while the chatbots' item files were made before and are scored by the
  old one. Read again from the recorded chatbot answers with today's code (`realworld_report.py --rescore-chat`), Haiku scores 0.6766 (stored 0.6868) and Opus on its 6 papers 0.7875
  (0.7930). **Every development comparison below uses the rescored chatbot items**, so one converter scores every arm; the registered numbers of
  `eval-realworld.md` are untouched. The fresh set's gold is built by the current converter, so it needs no correction.
- *The equation prompt changed too* (chemistry added to it: hash `equation-df0e12f1` to `equation-68ebbbf9`), so no recorded ReadableSTEM answer replays on main;
  the development baseline was recorded again ($2.33, `base-main`): primary 0.5528, 12.0 wrong equations nobody is asked about per 100 gold.

## 2. Where the points went (development, before any fix)

ReadableSTEM `base-main` 0.5528 against Haiku 0.6766 (rescored): 12.4 points. Per mechanism, from `scripts/rw_gap_table.py --part dev --rs base-main --rescore-chat`:

| Mechanism | ReadableSTEM wrong, Haiku right | Haiku wrong, ReadableSTEM right | Net points |
|---|---|---|---|
| not detected, 1 to 3 tokens in running text | 112 | 21 | +3.87 |
| not detected, in a table cell | 68 | 1 | +2.85 |
| not detected, in a caption (figure or table) | 49 | 8 | +1.74 |
| span: neither parentheses, units, words nor citations ("other") | 53 | 13 | +1.70 |
| span: parentheses of the sentence | 30 | 0 | +1.28 |
| read wrongly: bold italic (`\bm`, `\boldsymbol` read as `\mathbf`) | 28 | 14 | +0.60 |
| span: a unit after the number | 8 | 0 | +0.34 |
| span: a word of the sentence (`Let`, `for all`) | 6 | 0 | +0.26 |
| read wrongly, upright text / script letters | 14 | 10 | +0.17 |
| span: a citation | 1 | 0 | +0.04 |
| undetected: longer inline or display, algorithms | 16 | 19 | -0.13 |
| read wrongly, other | 17 | 19 | -0.09 |
| no proposal, scorer | 8 | 14 | -0.26 |
| **all** | | | **+12.4** |

Reading it:

1. **Reading is not where ReadableSTEM loses.** The equations it found but wrote wrongly are 6.6% of the gold against Haiku's 9.1%
   (recognition bucket); net, ReadableSTEM is ahead there. A render-and-compare repair loop (suggested during the work) could help only this class, and only
   where the symbols are right and the structure is not: of the 282 wrong equations nobody is asked about, **1** has the same symbols in another
   arrangement (the structural error such a check could see); 105 have other symbols (the text-layer check already runs there), 119 are a span cut at an edge
   (the render matches the crop, which is itself wrong), 57 differ only in notation (the same print). That is 0.04 points against the 1.5-point or one-third threshold set for building it. Not built.
2. **Five mechanisms carry 12.1 of the 12.4 points**: short inline variables (3.9), tables (2.9), captions (1.7), the edge of the span (3.6 over four kinds), bold italic (0.6).
3. Not mechanisms: the scorer bucket (1.8% of gold for ReadableSTEM, 2.0% for Haiku), the rest of recognition, algorithms and displays.

## 3. The mechanisms, with their evidence

**M1. A variable set in an italic text font is not seen as maths (3.9 points; the largest).** `mathptmx`, `mathpazo`, Charter and `newtx` set the
Latin letters of a formula in the text family's italic and only Greek letters and symbols in a maths font. The layout (`pipeline/layout.py`,
`_inline_evidence`) kept a run only with a maths-font glyph, an operator, a script or a relation, so a lone `S`, `m`, `K(t)` or `N_i` in a sentence was text.
Example (2503.02792 p1, "Let us consider *S* species"): gold `S`; ReadableSTEM nothing; Haiku `S`; the PDF's font for the letter is
`CharterBT-Italic`. The same on 2408.09889 p2 (Palatino: `m`, `a`), 2404.15495 p1 to 3 (Times: `K(t)`, `t`) and 2501.09629 (MathDesign). Of the
ReadableSTEM misses in running text with one to three tokens, Haiku found 112; what it did not find and ReadableSTEM did not either is gold convention (below).

**M2. Mathematics typed in a table cell (2.9 points).** 196 of the 223 table equations are not found: a cell's maths is the table step's text, not an
equation element, and a table the detector does not propose is not read at all (docs/tables.md measured 65 of 223 found, 49 exact, with `FEATURE_TABLES`
on: the rest is detection, the work of the table branch). Not changed here; the arms of the fresh test run with `FEATURE_TABLES` on (the deployed setting).

**M3. Mathematics in captions (1.7 points).** All 177 gold equations the report calls "figure" are in a `\caption{...}` (none is inside an embedded picture:
the gold has none), and 19 more are in table captions. The layout kept a caption as one plain string, `Element.caption`, and never cut it into text and
formulas. Example (2404.15495 p2, Figure 5's caption): gold `K(t)` and `\langle N_{\Delta t=1h}\rangle`; before nothing, Haiku right; after exact.

**M4. Where an inline formula starts and ends (3.6 points over four kinds).** The author's maths stops at the dollar sign, and ReadableSTEM's span ran on.
- *Things around the formula that are the sentence's* (parentheses, a unit, a citation, 69 of the 204 span failures; gold stops before them in 27 of 31 formulas
  cut inside a pair of parentheses; 37 failures on an upright unit against 2 exact): `(\gamma = -0.9)` for `\gamma=-0.9`, `t = 915\ \text{s}` for `t=915` (2505.11658 p2).
- *The crop shows the neighbour.* The padded crop (2 pt) takes in half of the parenthesis beside a formula and the model writes it: 2401.11209 p2, run `βr` (the text layer
  has no bracket), reading `(\beta_r)`; 2405.03801, `(k-1)-regular` read as `(k-1)\text{-}`.
- *Words and operands taken in.* A theorem set in italics with `log(n)` upright read "for all" and "and" as variables (2305.06353 p3: `n_k \succsim n_l \text{ for all } 1\le k,l\le K`
  for gold `1 \le k,l \le K`); two formulas with a word space between them read as one (`\varepsilon^{(i)}(k)\ (i=1,...,I)`, 2404.15495 p3); a `:=` and a map
  `H : R^d \to R^p` cut at the colon (22 failures of one held-out paper); a list with an ellipsis cut at the comma.
- *A formula that wraps across a line* is two inline equations by design (`f(n) \leq C g(n)` printed as "f(n) ≤" and "C g(n)": 19 failures); not changed.

**M5. Bold italic (0.6 points; 2.6% of all gold in three papers).** `\bm x_t` is set in CMMIB10 (bold math italic); the crop shows a bold letter and every reading says `\mathbf{x}_t`
(2504.03499 p1: gold `\bm x_t`; ReadableSTEM `\mathbf{x}_{t}`; Haiku `\mathbf{x}_t`). 62 of the development equations; Haiku fails the same 66. The face is in the font name.

**What is not a mechanism.** (a) *The scorer.* Equations the scorer calls wrong that print the same: the styled letters Unicode drew in Letterlike Symbols (`\mathcal{R}` is U+211B, `\mathcal R`
without braces is a plain R with a variant) and upright words (`\text{in}` against `\mathrm{in}`); `scripts/rw_scorer_sensitivity.py` counts them for every arm: development
ReadableSTEM +0.77 and +0.72 points, Haiku +0.85 and +0.72; held-out ReadableSTEM +0.0 and +1.64, Haiku +0.0 and +0.99. Symmetric to within a point, so the scorer is left as it is
(`exact_lenient` of the registration), and the sensitivity is reported beside the results. (b) *Gold boundaries no arm can match*: bare scripts typed as `m$^{-1}$` (gold `^{-1}`: 40 equations, 1.7%, Haiku
right on 7), numbers typed in math mode (`$20$`, `91\%`: 32 equations, Haiku found 2), `\theta` and `= 0` typed apart. They stay in every arm's score.
(c) *A wrong reading of the symbols* is where ReadableSTEM is already ahead (above).



## 4. The fixes

Each was written test first (`tests/test_layout_italic_variables.py`, `test_layout_run_edges.py`, `test_layout_captions.py`, `test_equation_bold_italic.py`,
`test_equation_neighbour_brackets.py`, `test_equation_neighbour_marks.py`) from the evidence above, tuned and measured on the development papers only, with no prompt change
(the equation prompt hash is `equation-68ebbbf9`, as on main, so the committed `latest-*` reports and the eval gate are untouched). Everything is deterministic and from
the text layer and the glyph boxes; a recorded model answer replays to the same result. Details: docs/layout.md "Inline formulas in LaTeX papers", docs/steps.md "Edges and faces".

| Fix | Mechanism | Where |
|---|---|---|
| An italic variable in upright prose is maths: a short italic group (not an English word, not an italic phrase, not a label, not a quantity's unit) whose neighbours are not italic letters | M1 | `layout._italic_variables`, `_word_lengths` |
| A bold letter with an index is a vector, not the count of `H2O` | M1 | `layout._bold_variable` |
| A figure's caption is cut like a paragraph; its formulas are inline equations of the figure (`Element.caption_marked`, written into the `<figcaption>`) | M3 | `layout._attach_captions`, `_to_elements`, `assemble.figure`, `run.context_texts` |
| The parentheses of the sentence around a whole formula (a tuple keeps its pair), an upright unit after a number, a citation after a space and a trailing opening bracket leave the formula | M4 | `layout._trim_run`, `_sentence_parentheses`, `_tail_outside_maths` |
| A visible word space between two operands ends the formula; an ellipsis list, `:=` and a map `H : A -> B` are one | M4 | `layout._word_space_between_operands`, `_ellipsis_comma`, `_join_signatures` |
| A parenthesis, hyphen or dot at an edge of the reading that the run's own text layer lacks is the neighbour's | M4 | `equation_step.drop_neighbour_brackets`, `drop_neighbour_marks` |
| `\mathbf` becomes `\boldsymbol` for a letter drawn in a bold italic font | M5 | `layout._Char.styled`, `equation_step.restyle_bold_italic` |

Layout changes the OpenStax pages too; the layout of the 121 PDFs of `scripts/layout_identity.py` is compared before and after in section 7.

**Considered and not done.** A render-and-compare repair loop (section 2, point 1: it can see 1 of 282 silent errors). A whole-page model reading as a second witness for detection only (the page math pass,
ADR 0006): its measured gain on development, +1.7 to +2.5 points, came with 0.6 to 1.1 more silent errors per 100 gold against a limit of 0.5; the fixes above take the mechanisms it was
built for (span edges, short variables, captions) from the text layer instead, and what is left undetected in running text is mostly numbers and bare scripts the answer key counts and the chatbots do not find either.
The scorer is not changed (section 3). The table cells are the table branch's. A formula that wraps across a line stays two equations.


## 5. Development results (25 papers, 2,350 gold equations; tuned on these, so a reading of the effect and not a test)

ReadableSTEM at the head of the branch, merged with main `a1b33bd`, against `base-main` (main before the fixes, recorded again, flags off) and the chatbots read again with
today's code (`--rescore-chat`). `flags off` is the registered configuration (page math, tables and notation memory off); `flags on` is the deployed one
(`FEATURE_TABLES`, `FEATURE_NOTATION_MEMORY`). Intervals are 95% cluster bootstraps over papers.

| | base-main, flags off | after, flags off | after, flags on | Haiku | Opus (6 papers) |
|---|---|---|---|---|---|
| **exact_lenient, registered reading** | 0.553 [0.486, 0.616] | 0.680 [0.622, 0.727] | 0.682 [0.623, 0.728] | 0.677 [0.607, 0.739] | 0.788 [0.670, 0.852] |
| exact_lenient, table cells counted | 0.553 | 0.680 | **0.701** [0.647, 0.744] | 0.677 | 0.788 |
| symbols, credited | 0.590 | 0.718 | 0.720 (0.743 cells counted) | 0.714 | 0.821 |
| detection recall / precision | 0.737 / 0.862 | 0.816 / 0.855 | 0.819 / 0.855 (0.851 / 0.849) | 0.840 / 0.876 | 0.879 / 0.876 |
| **wrong, not asked, per 100 gold** | 12.00 [8.40, 15.79] | **8.81** [6.69, 11.16] | **8.98** [6.82, 11.35] (9.96 cells counted) | 16.30 [11.76, 21.41] | 9.16 [6.10, 13.59] |
| required decisions per PDF page | 4.54 | 4.38 | 4.22 | 0 | 0 |
| cost per PDF page | $0.0242 | $0.0260 | $0.0296 | $0.0022 | $0.0878 |

Paired, 25 papers, 95% intervals (97.5% in the registered tests of the fresh set): **after minus base-main +0.129 [+0.079, +0.181]** on the registered reading (+0.148
[+0.100, +0.198] with table cells counted); after minus Haiku +0.005 [-0.053, +0.061] (+0.024 [-0.027, +0.074] with cells counted); after minus Opus on its 6 papers -0.112 [-0.246, -0.031]
(-0.075 [-0.149, -0.027] with cells counted). Tables on adds +0.002 on the registered reading (the scorer cannot see a cell) and +0.021 [+0.005, +0.041] with cells counted.
**On development ReadableSTEM went from 12.4 points behind Haiku to level with it, and stays behind Opus.** The three arms are not yet apart on the evidence of this set.

**Silent errors fell, they did not rise**: 12.00 to 8.81 per 100 gold (paired -3.02 [-5.39, -0.97] on the registered reading), because the span fixes turn silent span errors into exact equations (span errors that
nobody was asked about: 119 to 73) and the detections added are read as well as the rest. By kind, the remaining 207: 74 other symbols (36%), 73 spans (35%), 59 notation-only (29%),
1 structural. The per-page cost rose by $0.0018 (the extra equations) and by $0.0036 more with tables on (a table is $0.033).

**Where the points went after the fixes** (flags off, `rw_gap_table.py --rs v6off --rescore-chat`; net points against Haiku):

| Mechanism | before | after |
|---|---|---|
| not detected, 1 to 3 tokens in running text | +3.87 | +0.89 |
| not detected, in a table cell | +2.85 | +2.85 (flags on: +0.72 with cells counted) |
| not detected, in a caption | +1.74 | -2.04 |
| span, all four kinds | +3.58 | +0.94 |
| read wrongly: bold italic | +0.60 | -1.57 |
| everything else | +0.28 | -0.19 |
| **total** | **+12.4** | **+0.6** |

What is left against Haiku: table cells (68 equations, +2.85; the 25 of them that the tables-on pipeline still misses are the table branch's, and 31 of the 126 undetected table equations
are lone `\checkmark` and `\times` marks neither arm finds), span "other" (+1.15: formulas that wrap across a line, `\theta` typed apart from `= 0`) and
short variables (+0.89, mostly numbers typed in math mode and bare scripts). Against Opus on its 6 papers the same table cells are 4.2 of the 7.5 points.

**Other splits, layout only** (no model, so no new number on the held-out set; `scripts/realworld_report.py` was not run on them): the layout's detection recall and its span fidelity
(the text layer within 0.99 of the gold equation) before and after, from the document task's aligner with no model reading: held-out 0.770 to 0.788 and 0.512 to 0.546; first fresh split 0.818 to 0.872 and
0.553 to 0.616; development 0.728 to 0.805 and 0.471 to 0.573. The gain is smaller on the held-out set (a fifth of its gold is a table cell, a twentieth a caption) and not specific to the papers the fixes were tuned on.

## 6. The fresh set, registered before any system ran

`data/realworld/PREREGISTRATION-FRESH2.md`, hashed in `REGISTRATION-fresh2.sha256` and committed before any system ran: 15 papers (70 pages, 1,639 gold equations, 84 display, 145 typed in a table,
101 in a caption), the first 15 candidates that passed the gates when the first fresh split's seeded order is continued from the first candidate it did not visit (none of the 183 papers visited before is in it), the arms, the metrics,
the tests and the limit. Each arm was run once and is reported whatever it shows. Arms: the two chatbot recipes unchanged (`chatbot-haiku-purdue`, `chatbot-opus-purdue`); **before** = main `a1b33bd` (`src` tree `33e73a5f`, plus the measurement file `eval/pipeline_eval.py`, which
adds the table-cells reading and does not change what the pipeline writes); **after** = this branch at `d4846d1` (`src` tree `4f84a5dfd`; main `9082de9` merged: its handwriting change touches image pages, none of which is in these papers), both with the deployed
`FEATURE_TABLES` and `FEATURE_NOTATION_MEMORY` on, page math off; `after` started from `before`'s recorded answers (a shared request answered once). One start of each chatbot arm stopped on an `APIConnectionError` and was continued for the jobs that had not finished (named in the registration's rules).

| 15 fresh papers, 1,639 gold equations | ReadableSTEM after | ReadableSTEM before | Chatbot, Haiku | Chatbot, Opus |
|---|---|---|---|---|
| **exact_lenient, registered reading (the verdict)** | **0.626** [0.520, 0.741] | 0.558 [0.454, 0.672] | 0.622 [0.486, 0.751] | 0.682 [0.590, 0.782] |
| exact_lenient, table cells counted | 0.642 [0.537, 0.753] | 0.574 [0.472, 0.685] | 0.622 | 0.682 |
| symbols, credited | 0.664 [0.566, 0.765] | 0.596 | 0.658 | 0.727 |
| detection recall / precision | 0.763 / 0.866 | 0.736 / 0.880 | 0.785 / 0.904 | 0.818 / 0.848 |
| **wrong, not asked, per 100 gold (silent errors)** | **8.42** [5.84, 10.83] (8.79 cells counted) | 9.76 [5.94, 14.20] | 16.29 [12.13, 20.30] | 13.67 [8.92, 18.01] |
| required decisions per PDF page | 4.29 | 4.93 | 0 | 0 |
| cost per PDF page | $0.0390 | $0.0389 | $0.0023 | $0.0862 |

**The registered tests** (paired, 15 papers, 97.5% intervals, Bonferroni k = 2; sign-flip p):

| | Estimate | 97.5% | p | Verdict by the registered rule |
|---|---|---|---|---|
| after minus Opus, primary | -0.056 | [-0.109, +0.007] | 0.115 | no difference this set can detect; lower by point estimate |
| after minus Haiku, primary | +0.004 | [-0.066, +0.102] | 0.776 | no difference this set can detect |
| (cells counted) after minus Opus / Haiku | -0.040 / +0.020 | [-0.096, +0.027] / [-0.047, +0.114] | 0.371 / 0.492 | the same |
| **silent-error limit** after minus before | **-1.34** per 100 | [-4.91, +1.51] | 0.634 | **met** (limit +0.5) |
| after minus before, primary | **+0.068** | [+0.031, +0.116] | 0.002 | better, by 6.8 points |

**The registered claim, "clearly beats the chatbot recipe", is not met**: ReadableSTEM after the fixes equals Haiku (+0.4 points, nothing detectable) and is lower than Opus by 5.6 points by point estimate, an interval that includes zero.
What the set does show, at the registered 97.5% level: the fixes are worth **+6.8 points** of exact equations on papers they were never tuned on (before 0.558, after 0.626), the silent errors did not rise (they fell, 9.76 to 8.42 per 100, not detectably), and
ReadableSTEM leaves clearly fewer wrong equations nobody is asked about than either chatbot (paired minus Haiku -7.9 per 100 [-11.2, -4.3], p < 0.001; minus Opus -5.2 [-9.4, -0.1], p = 0.049), because it asks a person about
2.3 equations on a page, which a chatbot never does. The ordering to Opus is the one of the development set and of the held-out set (Opus ahead by 5 to 11 points on the papers it was run on).

**Where the points are** (`rw_gap_table.py --part fresh2 --rs after --reading cells`; net points against Haiku, negative: ReadableSTEM ahead): table cells +1.83, longer inline expressions and displays not found +1.40, span "other" +1.16,
algorithms +0.49, bold italic +0.24, against short inline expressions in running text **-3.17**, captions -1.34, upright text -1.04, other readings -1.34. On this set the mechanisms of the
analysis hold in sign and, for short variables and captions, in size: ReadableSTEM finds 74 short inline equations that Haiku does not and Haiku finds 22 that ReadableSTEM does not.

**What the scorer hides, and it is not in ReadableSTEM's favour here.** Undoing the two notation artefacts of section 3 (`rw_scorer_sensitivity.py`) adds +1.46 points to ReadableSTEM but **+4.64 to Haiku and +4.03 to Opus**:
the authors of this set write upright words in maths as `\mathrm{...}` and the chatbots follow them, while ReadableSTEM writes `\text{...}` (the equation prompt's rule), and the scorer reads the two as different trees. The registered primary is unchanged and is what the verdict uses;
under the variant, ReadableSTEM (0.641) would be below Haiku (0.668) and Opus (0.722). That is a measured disadvantage of the one convention ReadableSTEM writes upright words in, not an error in what a reader sees; it is not fixed here because it needs a choice the evidence does not make (the authors split: in subscripts `\text` 55 against `\mathrm` 36 on the development set, and for words outside subscripts `\mathrm` 111 against `\text` 31 on the held-out set), and a prompt change would invalidate every recorded answer.

**Unresolved** (not tuned on the fresh set, which is now used): the 30 longer inline expressions Haiku found and ReadableSTEM did not (`v : \mathcal{S} \to \mathbb{R}`, `(1-\gamma)`, `0_k 0_l`, a display with `aligned`), concentrated in 3 papers; table cells; upright words.


## 7. What the fixes did to other documents

`scripts/layout_identity.py` (the layout of 126 PDFs: the fixtures, the development set, the authored documents and the OpenStax pages cached for the eval, before and after, no model): 111 identical. The 15 that
differ: the two LaTeX fixtures and `real-tables` papers (TeX: expected), and OpenStax pages where a figure caption's formulas (`H2` and `O2` in a caption, as in a paragraph) are now inline
equations of the figure, and where a set with an ellipsis (`{a_1, a_2, ..., a_n}`) is one formula, not a formula and a loose "..." (chemistry and algebra pages; the largest has 11 more elements). Layout-only detection and fidelity on the development
set of OpenStax pages and authored documents (`data/dev-v2/dev`, 24 pages, 1,809 gold equations): recall 0.870 to 0.870, precision 0.710 to 0.709, span fidelity 0.568 to 0.574. The TeX conventions are not applied to a Chromium
print: with them applied to it, precision fell from 0.710 to 0.672 (OpenStax italic letters are emphasis, not variables) and the parenthesis rule cost 21 equations on one page (binomial coefficients drawn as parentheses round two stacked entries); both are why the TeX conventions are gated on the producer.
The eval gate (`latest-*` against `baseline-*`) is untouched: no prompt, model or scorer changed, so `--require-current` still passes, and the committed reports are not regenerated.

## 8. Spend

`scripts/realworld_run.py tally`: development $3.91 of the track's $18 (`base-main` $2.33, five runs of changes $1.58); the second fresh set $9.64 (Haiku $0.16, Opus $6.03, ReadableSTEM before $2.72 and after $0.73 on shared answers); **$13.55 of the track's $18**, The Opus arm cost $0.0862 a page, as on the held-out set.


## 9. What this does not say

One run per arm, 15 fresh papers of one year of arXiv: the intervals are wide (a paired difference of 5 points is at the edge of what 15 papers separate), and the fresh set has now been used (its failures above were read, not tuned on).
The development numbers are those of a system tuned on them. The two readings of the primary differ by 1.6 to 2.1 points and agree on every verdict. The silent errors are "wrong and nobody asked", per 100 gold equations, as
`eval-realworld.md` defines them: an equation the system never detected is not one (a missed equation is a silent loss of another kind that this number does not count, and ReadableSTEM misses 24% of the gold against the chatbots' 18% to 21%).
Whether a wrong equation changes the meaning, scans, Word files, chemistry (see docs/chemistry.md) and handwriting (docs/eval-handwriting.md) are not measured here. The fixes change nothing in how agreement between a crop reading and a page or text-layer reading lifts confidence
(`PAGE_MATH_AGREEMENT_LIFT` and the page pass are untouched and off), so they cannot create silent errors on image pages; the layout rules that add equations apply only to text-layer pages typeset by TeX.

**Handed to the owner.** The table branch's work decides the largest remaining piece (table cells: +1.8 points against Haiku on the fresh set, 4.2 of 7.5 against Opus on development); the cell reading of `pipeline_eval.with_table_maths` is how its
result will show in this metric. The equation prompt's `\text` for upright words, against authors who write `\mathrm`, is a prompt decision with a cost (every recorded answer re-asked) and no clear majority convention; it is not made here. Nothing in this work needed a chemistry-specific change of the equation prompt.

## Files and how to rerun

| What | Where |
|---|---|
| Failure table by mechanism, silent errors by kind | `scripts/rw_gap_table.py --part dev --rs v6off --rescore-chat [--reading cells]` |
| Reports (intervals, paired tests; `--rescore-chat`, `--reading`, `--extra-arm`) | `scripts/realworld_report.py` |
| Scorer artefacts by arm | `scripts/rw_scorer_sensitivity.py` |
| Registration, manifest, order, candidates of the fresh set; its hash | `data/realworld/PREREGISTRATION-FRESH2.md`, `manifest-fresh2.json`, `order-realworld-fresh2-2026-10-21.json`, `candidates-fresh2.json`, `REGISTRATION-fresh2.sha256` |
| Build, runs | `scripts/realworld_build.py build --order data/realworld/order-realworld-fresh2-2026-10-21.json --target 15 --single-part fresh2 --candidates-out candidates-fresh2.json`; `scripts/realworld_run.py chat|rs --part fresh2` |
| Results (the report's JSON, both readings) | `data/realworld/results-fresh2.json`, `results-fresh2-cells.json`, `results-dev-rootcause.json`, `results-dev-rootcause-cells.json` |
| Spend and the code each arm ran | `data/realworld/spend-log.jsonl` (`git_head`, `src_tree`) |
| Tests | `tests/test_layout_italic_variables.py`, `test_layout_run_edges.py`, `test_layout_captions.py`, `test_equation_bold_italic.py`, `test_equation_neighbour_brackets.py`, `test_equation_neighbour_marks.py`, `test_eval_table_maths_reading.py` |

