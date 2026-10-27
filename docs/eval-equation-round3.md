# Equations on real papers, round 3: why ReadableSTEM trailed a chatbot, and inline equations read with their line (2026-10-26)

After round 2 ([eval-equation-round2.md](eval-equation-round2.md)) ReadableSTEM wrote fewer equations exactly right than
the chatbot recipe on Opus. This round asked why, and whether the pipeline was over-built or tuned to its own
development papers; it then tested the one change the answer pointed to on a fourth set of 15 papers registered
before any system ran (`data/realworld/PREREGISTRATION-FRESH4.md`).

**The short version.** Most of the registered gap on fresh3 was the scorer, not the product: reading A counted the
formulas in the chatbots' HTML tables and not those in ReadableSTEM's rebuilt tables (3.9 of 5.8 points). Read the same
way for every arm (reading B), the gap to Opus was 2.4 points, and its largest part was where an inline formula
starts and ends: the text layer splits a formula at a radical, an integral sign, a stacked fraction or the left side of
a relation, and the model only read inside that span. The model now also sees the whole line with the span boxed and
writes the formula the box is part of (`EQUATION_LINE_CONTEXT`). **On fresh4 that is +2.6 points [+1.7, +3.6]
(0.586 to 0.613, p < 0.001) with fewer silent errors (7.9 to 7.3 per 100 gold): the registered rule for turning it on
is met.** Against the chatbots on fresh4: Haiku 0.563 (ReadableSTEM higher by point estimate, +4.9), Opus 0.641
(ReadableSTEM lower, -2.8, 97.5% interval -5.5 to 0.0), and ReadableSTEM leaves the fewest wrong equations nobody is
asked about (7.3 against 9.8 for Opus and 11.3 for Haiku per 100 gold) at a third of Opus's cost per page. It does not
beat Opus yet. Spend this round: $12.00 (exploratory $0.97, development $1.60, fresh4 $9.44).

## 1. Why it trailed (fresh3, recorded runs, no new spend)

- **The scorer read the arms differently.** `pipeline_eval.with_table_maths` says it: the chatbots' HTML is read with
  the formulas in its tables, ReadableSTEM's rebuilt tables were not, under the registered reading A. On fresh3 that
  was 70 table equations ReadableSTEM wrote and was not credited for, 3.9 of the 5.8 points. Reading B, which reads
  every arm's output the same way, is the primary score from fresh4 on.
- **17% of the gold is out of every arm's reach.** 257 equations in `sidewaystable` were found by no arm: the same
  loss for all four, so it narrows every difference.
- **What was left, by mechanism** (reading B, ReadableSTEM wrong and Opus right, minus the reverse): span edges +1.6
  points (34 against 6), upright text +0.5, table cells +0.4, longer inline or display +0.3, captions +0.3. Recognising
  symbols inside a correct span was not where it lost.
- **Span edges, case by case.** `T = 2\pi` for `T = 2\pi\sqrt{m/k}` (the radical is a line object of its own);
  `0.077` for `Ca^*=0.077`; `f\,d\mu = 0` for `\int f\,d\mu = 0`; `j!` for `\dfrac{n!}{i!\,j!}`; and junk kept at
  the edge: `Ca^* \ [-]`, `t \in \mathcal{T}_1.^{8}` (a footnote mark), `ATT(c,t)\text{s}` (a plural s). Most were
  accepted without a decision: silent errors.
- **The page math pass does not fix it.** Run on fresh3 over today's main (exploratory, $0.97): +0.2 points. It
  places spans by quoting the text layer, so it inherits the same gaps.

## 2. Over-built, or tuned to its own papers?

Partly both. `pipeline/layout.py` is about 9,000 lines of rules; the model that reads an equation is the same Opus
the chatbot uses, so the rules that decide what it is shown are the bottleneck. Rules tuned on the development papers
shrink on fresh ones: equation round 1 was +12.9 points on development and +6.8 on fresh2; table round 1 took
recall from 0.56 to 0.92 on development and 0.74 on fresh documents. So no further rules for single cases; the change
below gives the model the context it lacked and lets it decide the boundary, which is what a chatbot does.

## 3. What changed

`layout.crop_line` renders the line an inline equation sits in (the paragraph's width, 0.8 span heights above and
below, so a raised radical or a stacked fraction is in it) with a two-pixel red box round the span. The equation step
sends it after the usual crop, with `prompts/equation_line.md` appended to the equation prompt: transcribe the whole
formula the box is part of, extend past the box while the maths goes on, leave out words of the sentence, footnote
and citation marks, item numbers and the sentence's punctuation. Display equations, figures and tables are unchanged.
Setting `EQUATION_LINE_CONTEXT` (default off in the code; on in production after this test).

Development (25 papers, reading B): 0.716 to 0.735 (+2.0 [+0.5, +3.7], p = 0.016); silent errors 10.17 to 10.13; span
failures 5.6% to 4.6% of the gold.

## 4. The fourth registered set

15 arXiv research papers, 71 pages, 1,600 gold equations (87 display), CC BY 4.0 or CC0, the seeded order continued
from fresh3. One run per arm, every answer recorded (`runs/fresh4`).

| fresh4, 15 papers | ReadableSTEM (line) | before | Haiku | Opus |
|---|---|---|---|---|
| **exact_lenient, reading B (primary)** | **0.613** | 0.586 | 0.563 | 0.641 |
| notation-neutral, reading B | 0.618 | 0.592 | 0.569 | 0.650 |
| exact_lenient, reading A | 0.593 | 0.567 | 0.563 | 0.641 |
| detection recall (B) | 0.706 | 0.705 | 0.676 | 0.738 |
| wrong, not asked, per 100 gold (B) | **7.3** | 7.9 | 11.3 | 9.8 |
| decisions asked per page | 2.7 | 3.6 | 0 | 0 |
| cost per PDF page | $0.030 | $0.030 | $0.002 | $0.090 |

Registered decisions (section 5):

1. **The change: met.** After minus before +0.026 [+0.017, +0.036], sign-flip p < 0.001; positive under every reading
   (A +0.026, notation-neutral +0.026). The span bucket fell from 5.1% to 3.3% of the gold.
2. **Silent-error limit: met.** -0.69 per 100 gold [-1.80, +0.47] against a limit of +0.5.
3. **Against the chatbots.** Haiku: higher by point estimate (+0.049, 97.5% interval -0.026 to +0.187, p 0.16), and fewer
   silent errors (-4.0, p 0.03). Opus: lower (-0.028, 97.5% interval -0.055 to 0.000, p 0.07), fewer silent errors by point
   estimate (-2.5, no difference this set can detect).

A cost of the change: the review screen asks about fewer of the wrong equations (flag recall 0.33 to 0.19, decisions per
page 3.6 to 2.7). The extended formula no longer matches its text-layer span, and a symbol mismatch against the text
layer was one of the reasons a decision was asked. The silent errors still fell, so fewer questions are asked and fewer
wrong equations go through unasked; the flags that remain are less often on a wrong equation.

## 5. Where it still loses to Opus (fresh4, reading B)

ReadableSTEM wrong and Opus right, minus the reverse, of 1,600 gold: maths in table cells not found +1.1 points (23
against 5); **bold italic letters (`\bm`, `\boldsymbol`) +1.0 (20 against 4)**; span edges +0.7 (23 against 12, was 34
against 6 on fresh3); captions +0.3. The bold-italic loss is self-inflicted: the equation prompt says "bold symbols
(vectors) use `\mathbf{...}`", so an author's bold italic `\bm{x}` is written upright bold. That is the next change to
test, with the table-cell detection gap.

Commands: `scripts/realworld_report.py --part fresh4 --tag after --extra-arm before=before --reading cells [--neutral]`;
`scripts/rw_gap_table.py --part fresh4 --rs after --reading cells --vs Opus`; results in
`data/realworld/results-fresh4*.json`.

## 6. Checks for over-fitting (2026-10-26, after the switch-on)

- **The gain is broad.** On fresh4, line context raised 14 of the 15 papers and lowered none (+0.0 to +4.8 points per
  paper). It was +2.0 on the development papers and +2.6 on fresh4: no shrinkage from tuned to untuned papers.
- **Course material.** On the 14 OpenStax algebra and trigonometry sections of dev-v2 (1,114 gold, production flags) it
  changed 46 transcriptions and the score not at all: 3 equations right that were wrong, 3 wrong that were right
  (0.5395 either way). Textbook maths is mostly display equations, which line context does not touch. The chemistry
  sections were not compared: the API account ran out of credit during the run, and the calls after that failed.
- **What a student hears.** `scripts/rw_spoken.py` (`--spoken` on `realworld_report.py` and `rw_gap_table.py`;
  exploratory, not registered) also counts an equation right when ReadableSTEM's speech reads prediction and gold
  alike. `\mathbf{x}`, `\boldsymbol{x}` and `\bm{x}` are all "bold x", so the bold-italic loss of section 5 is not a loss
  a screen-reader user hears, and it is dropped as a next change: fixing it would tune to the scorer. Read this way,
  fresh3 is a tie with Opus (0.707 against 0.706) and fresh4 is 0.632 against 0.669.
- **One paper carries most of the rest.** Of the 58 table-cell equations ReadableSTEM missed on fresh4 and Opus read
  right (spoken), 42 are in one paper, arXiv 2409.12662, whose two 28-row tables on pages 14 and 15 are not found as
  tables and whose floor brackets have no character in the text layer. Leaving that paper out (a sensitivity check
  chosen after seeing the data, not a result): spoken 0.671 against Opus 0.684 (-1.2, p 0.45); primary -2.8. No rule
  for that paper is added.
