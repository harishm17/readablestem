# Equations: where ReadableSTEM stands on the 25 real-world development papers, against the recorded chatbots (2026-11-03)

Development part only (`--part dev`); the held-out and fresh parts were not touched and no default
changed. One run of ReadableSTEM at `main` 91d0046 (pipeline 0.5.3, 2026-11-03), scored with the
recorded Opus and Haiku chatbot answers of 2026-10-21, which were re-scored, not re-asked. Intervals
are 95% cluster bootstraps over papers (`scripts/realworld_report.py`, 10,000 resamples), so with 25
papers, and 6 for Opus, they are wide. The development papers were used to build the extractor and to
tune the pipeline before: these numbers are a baseline for this set, not an estimate for new papers.

**The short version.** On 25 development papers (114 pages, 2,350 gold equations), ReadableSTEM writes
0.735 of the equations exactly right end to end (reading B, 95% interval 0.677 to 0.783), the Haiku
chatbot 0.675 (0.605 to 0.738), and on the 6 papers that have an Opus answer ReadableSTEM writes 0.764
(0.681 to 0.808) against Opus 0.784 (0.660 to 0.852). The paired difference against Haiku is +0.060
(95% +0.004 to +0.120, sign-flip p = 0.080, n = 25 papers); against Opus -0.020 (-0.049 to +0.033,
p = 0.625, n = 6 papers). Read through the notation-neutral and the spoken-equal readings, the
difference against Opus is -0.022 and -0.002, and against Haiku +0.070 and +0.060 (both intervals above
zero). ReadableSTEM leaves 9.5 wrong equations in 100 unasked (7.4 and 6.3 under the two readings), the
Haiku chatbot 16.5, Opus 9.5 on its 6 papers. The remaining misses are
mostly where ReadableSTEM has no equation element: 180 of its 623 misses (7.7 per 100 gold) are
equations typed in a table or a figure, 125 (5.3 per 100) are one to three symbols in running text
that were not found, and 90 (3.8 per 100) are inline spans cut at the wrong edge.

## What was run

- **Papers.** The 25 development papers of `data/realworld/manifest-dev.json`: 114 PDF pages, 2,350 gold
  equations (197 display, 2,153 inline; 1,908 typed in running text, 223 in a table, 177 in a figure, 42
  in an algorithm). Six of them (27 pages, 546 gold) have a recorded Opus chatbot answer; all 25 have a
  recorded Haiku answer.
- **ReadableSTEM.** `scripts/realworld_run.py rs --part dev --tag base-0503` through the document task,
  provider anthropic, `claude-opus-5-5` (effort medium) as main model and `claude-haiku-5-5` (low) as
  cheap model, second check off. Production's flags for a typed PDF, from `docs/eval-realworld.md`,
  `docs/remeasurement-2026-10-21.md`, docs/eval-equation-round5.md and the deploy notes of 2026-11-03
  (17:27 and 19:17 UTC): `FEATURE_TABLES`, `FEATURE_NOTATION_MEMORY`, `EQUATION_LINE_CONTEXT`,
  `EQUATION_GLYPH_CHECKS` and `FLAG_POLICY` on; the page math pass and the page witness off (both are
  off in production). Every other setting was its default: the environment's application settings
  were removed before the run (`eq_dev_run.scrub_environment`) so no deployed value reached a request.
  Production's variables themselves were not read.
- **Answers.** The run started from the recorded crop answers of the control of docs/eval-equation-round5.md
  (`e1-control`, which includes the `line1` run). A request whose text matches a recorded answer is
  replayed; one that code changes since had altered is asked live. The first offline pass reported 68
  requests without a recorded answer; the live start (cap $4.00, 1 paper at a time, 2 workers) asked 42
  and spent $0.1186; a second offline pass reported 2,715 hits and 0 misses. The cost per page below is
  the list price of every recorded call in the 2,715 (replayed ones included), so it is the cost of the
  whole run, not of the live start. Recorded under `~/.cache/readablestem/realworld/runs/dev/base-0503/`
  (`answers.jsonl`, `items.jsonl`, `report.json`).
- **Chatbots.** `chatbot-opus-purdue` (6 papers) and `chatbot-haiku-purdue` (25 papers), recorded
  2026-10-21 and re-scored from their recorded answers by today's converter (`--rescore-chat`), so every
  arm is scored by one converter. No chatbot was asked anything.
- **Readings.** Reading B (`--reading cells`): the maths cells of a rebuilt table count as equations, as
  a chatbot's HTML is read; this is the primary here, as in docs/eval-equation-round5.md. Reading A is
  the registered table reading (the table is one element; ReadableSTEM does not get credit for cell
  maths). Notation-neutral (`--neutral`, upright-word commands folded on both sides) and spoken-equal
  (`--spoken`, exploratory: an aligned equation is also right when ReadableSTEM's speech says prediction
  and gold alike; docs/eval-equation-round5.md) are applied to every arm.

## Results (reading B)

All 25 papers (2,350 gold, 114 pages); Opus is 6 papers (546 gold, 27 pages).

| | ReadableSTEM (25) | Chatbot, Haiku (25) | Chatbot, Opus (6) |
|---|---|---|---|
| exact_lenient, end to end | 0.735 [0.677, 0.783] | 0.675 [0.605, 0.738] | 0.784 [0.660, 0.852] |
| notation-neutral | 0.763 [0.704, 0.812] | 0.693 [0.619, 0.759] | 0.810 [0.681, 0.885] |
| spoken-equal (exploratory) | 0.783 [0.721, 0.833] | 0.723 [0.655, 0.782] | 0.813 [0.684, 0.887] |
| symbols, credited (secondary) | 0.777 [0.718, 0.826] | 0.712 [0.639, 0.778] | 0.822 [0.693, 0.899] |
| detection recall | 0.871 [0.820, 0.911] | 0.840 [0.786, 0.884] | 0.879 [0.784, 0.942] |
| detection precision | 0.866 [0.838, 0.894] | 0.876 [0.829, 0.918] | 0.876 [0.753, 0.952] |
| wrong, not asked, per 100 gold (exact_lenient) | 9.53 [7.41, 11.91] | 16.51 [11.91, 21.65] | 9.52 [6.18, 14.14] |
| wrong, not asked, per 100 gold (notation-neutral) | 7.40 [5.52, 9.58] | 14.64 [10.15, 19.96] | 6.96 [4.20, 12.50] |
| wrong, not asked, per 100 gold (spoken-equal) | 6.26 [4.04, 8.86] | 11.66 [8.49, 15.22] | 6.59 [3.85, 12.23] |
| required decisions per PDF page, all kinds | 3.87 [2.69, 5.25] | 0 | 0 |
| of which on an aligned equation | 2.00 [1.29, 2.83] | 0 | 0 |
| equation flags, recall (symbols-wrong aligned equations that were a decision) | 0.328 [0.213, 0.439] | 0 | 0 |
| equation flags, precision | 0.338 [0.204, 0.482] | n/a | n/a |
| cost per PDF page | $0.0313 [0.0238, 0.0402] | $0.0022 [0.0021, 0.0024] | $0.0878 [0.0756, 0.0973] |

"Wrong, not asked" counts an aligned equation that is wrong under the stated reading and was not a
required decision; the chatbots ask nobody about anything, so every wrong aligned equation of theirs is
unasked. Detection and the cost rows do not depend on the reading. Under reading A, ReadableSTEM's
exact_lenient is 0.703 [0.636, 0.759] and detection recall 0.825 [0.751, 0.884], because a table's cell
maths is not credited; the chatbots' numbers are the same under both readings.

### On the 6 papers all three arms ran on (546 gold, 27 pages)

| reading B | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku |
|---|---|---|---|
| exact_lenient | 0.764 [0.681, 0.808] | 0.784 [0.660, 0.852] | 0.755 [0.603, 0.829] |
| notation-neutral | 0.788 [0.690, 0.847] | 0.810 [0.681, 0.885] | 0.778 [0.614, 0.860] |
| spoken-equal | 0.811 [0.704, 0.877] | 0.813 [0.684, 0.887] | 0.797 [0.620, 0.887] |
| detection recall | 0.863 [0.782, 0.917] | 0.879 [0.784, 0.942] | 0.866 [0.724, 0.943] |
| detection precision | 0.895 [0.835, 0.930] | 0.876 [0.753, 0.952] | 0.915 [0.814, 0.962] |
| wrong, not asked, per 100 gold (exact_lenient) | 7.14 [3.69, 9.62] | 9.52 [6.18, 14.14] | 11.17 [6.55, 15.69] |
| required decisions per PDF page | 2.82 [1.35, 4.64] | 0 | 0 |
| cost per PDF page | $0.0350 [0.0231, 0.0470] | $0.0878 [0.0756, 0.0973] | $0.0021 [0.0018, 0.0025] |

## Paired differences (ReadableSTEM minus the chatbot, reading B, 95% cluster interval over papers)

| metric | minus Opus (n = 6 papers) | sign-flip p | minus Haiku (n = 25 papers) | sign-flip p |
|---|---|---|---|---|
| exact_lenient | -0.020 [-0.049, +0.033] | 0.625 | +0.060 [+0.004, +0.120] | 0.080 |
| notation-neutral | -0.022 [-0.047, +0.020] | 0.500 | +0.070 [+0.016, +0.129] | 0.016 |
| spoken-equal | -0.002 [-0.039, +0.044] | 0.875 | +0.060 [+0.013, +0.112] | 0.023 |
| symbols, credited | -0.020 [-0.054, +0.033] | 0.500 | +0.065 [+0.012, +0.123] | 0.035 |
| detection recall | -0.016 [-0.067, +0.015] | 0.313 | +0.032 [-0.007, +0.080] | 0.154 |
| wrong, not asked, per 100 gold (exact_lenient) | -2.38 [-9.07, +1.22] | 0.188 | -6.98 [-12.00, -2.59] | 0.015 |
| wrong, not asked, per 100 gold (notation-neutral) | -1.83 [-8.03, +1.56] | 0.188 | -7.23 [-12.12, -3.06] | 0.004 |
| wrong, not asked, per 100 gold (spoken-equal) | -3.30 [-9.07, -0.21] | 0.125 | -5.40 [-8.36, -2.45] | 0.007 |

Under reading A (the registered table reading) the differences are -0.106 (-0.244 to -0.028, p = 0.063,
n = 6) against Opus and +0.029 (-0.040 to +0.096, p = 0.676, n = 25) against Haiku. The reading
decides whether the interval against Haiku excludes zero; the weight it moves is the table cells (223 gold
equations, 9.5% of the set). The p-values are descriptive; none of these comparisons is registered. With 6 papers the
interval against Opus is as wide as the whole spread of the arms, so "no difference detected" is
what it says and not evidence that there is none.

## Where the misses are (reading B, exact_lenient)

Every gold equation that is not exactly right, placed by where it was typed (from the source, as in
docs/eval-realworld.md) and by the first rule that fits. Per 100 gold equations of that arm's papers.

| miss bucket | ReadableSTEM (25 papers, 2,350 gold) | Haiku (25) | ReadableSTEM (6 papers, 546) | Haiku (6) | Opus (6) |
|---|---|---|---|---|---|
| right | 73.5 | 67.4 | 76.4 | 75.5 | 78.4 |
| in tables and figures (any failure) | 7.7 (180) | 10.7 (252) | 7.9 (43) | 9.2 (50) | 8.1 (44) |
| inline span edges (running text; aligned, symbols differ at an end only) | 3.8 (90) | 3.0 (71) | 2.0 (11) | 2.7 (15) | 2.4 (13) |
| short inline expressions in running text, not found (1 to 3 symbols) | 5.3 (125) | 5.1 (121) | 7.1 (39) | 4.6 (25) | 5.3 (29) |
| other | 9.7 (228) | 13.7 (321) | 6.6 (36) | 8.1 (44) | 5.9 (32) |
| misses, total | 26.5 (623) | 32.6 (765) | 23.6 (129) | 24.5 (134) | 21.6 (118) |

"Other" for ReadableSTEM's 25 papers is 100 aligned equations whose symbols differ inside (recognition),
80 where the symbols are right and the primary scorer says no, 2 with no proposal, and 46 not found
that are a display equation (12) or a longer inline expression (34). Where each failure falls, in running
text (which includes algorithms), tables and figures (counts of gold equations):

| ReadableSTEM, 25 papers | gold | right | scorer | span | recognition | no proposal | not found |
|---|---|---|---|---|---|---|---|
| running text and algorithm | 1,950 | 1,507 | 80 | 90 | 100 | 2 | 171 |
| table | 223 | 86 | 11 | 5 | 22 | 0 | 99 |
| figure (caption or panel) | 177 | 134 | 3 | 7 | 0 | 1 | 32 |

| Haiku chatbot, 25 papers | gold | right | scorer | span | recognition | no proposal | not found |
|---|---|---|---|---|---|---|---|
| running text and algorithm | 1,950 | 1,437 | 75 | 71 | 167 | 13 | 187 |
| table | 223 | 80 | 3 | 7 | 33 | 0 | 100 |
| figure (caption or panel) | 177 | 68 | 1 | 5 | 13 | 0 | 90 |

By where the equation was typed, `exact_lenient` (ReadableSTEM, 25 papers): running text 1,908 gold,
0.776; table 223, 0.386 (detected 0.556); figure 172, 0.756 (detected 0.814); algorithm 42, 0.643.
Display 197, 0.503; inline 2,153, 0.756; single symbol 608, 0.715; research papers 976, 0.700; tutorial
1,374, 0.760.

- **Span.** ReadableSTEM's 102 span failures (90 in running text, 12 in tables and figures) are 65 cut too
  long and 37 cut too short. The chatbots' are almost all too long (Opus 18 of 19 on its 6 papers).
- **Short inline maths.** ReadableSTEM does not find 70 single symbols and 55 two-or-three-symbol
  expressions in running text. The Haiku chatbot misses 81 and 40.
- **Tables and figures.** 99 of the 223 table equations and 32 of the 177 figure equations are not found
  at all; 22 table equations are found and read wrong. Opus on its 6 papers misses 20 of 82 table
  equations and 10 of 37 in figures; ReadableSTEM on the same 6 misses 22 and 11.

## The converter

`scripts/dev_converter_effect.py` on the run's items (reading A, aligned pairs, stored gold: the gold
file's MathML is the old converter's shape; "re-converted" makes both sides alike): the converter's
repairs and shape step move the primary 0.7153 to 0.6996 (-1.57 points, 1,681 to 1,644 of 2,350) against the stored
gold and 0.7183 to 0.7204 (+0.21 points) against the re-converted gold; the symbols scorer moves -0.34
and -0.04. The sign of the net effect depends on which gold is used, so it is a size of at most 1.6 points on the
primary. (The script's
own scoring path gives 1,644 where the report's reading A has 1,653; the report is the number used here.)

## Spend

Cap $8.00 for the measurement; **$0.1186 spent**, all of it the one live start of ReadableSTEM's
pipeline (cap $4.00 for that start, which was sized with the reservations of two workers in flight, not
the expected cost; 42 requests asked; line in `data/realworld/spend-log.jsonl`, tag `base-0503`). The
two offline passes, every re-score, the chatbot re-scores and the converter script cost nothing. No
chatbot arm was run. No model was called for anything else.

## Limits

- 25 development papers from one set that the pipeline was tuned against; 6 papers for the Opus
  comparison, chosen by the registered chat order, not at random from the 25. One run of each arm; the
  models are not deterministic, and the crop answers of ReadableSTEM were mostly asked on earlier days
  (2,673 of 2,715 requests replayed) at earlier code; only the 42 that code changes had altered
  were asked on 2026-11-03.
- The Opus chatbot's recorded answers were re-scored by today's converter, so its 6-paper numbers differ
  from the 0.793 of docs/eval-realworld.md (0.784 under reading A here); the Haiku number moves from
  0.687 to 0.675 the same way.
- The spoken-equal reading is exploratory (docs/eval-equation-round5.md); the exact and the
  notation-neutral rows are the ones to read first.
- The one run is of the pipeline with line context, glyph checks and the flag policy on. The 12-paper control of
  docs/eval-equation-round5.md had glyph checks and the flag policy off and scored 0.753 under reading B (12
  papers); this run's 0.735 is on 25 papers, so the two are not a before and after.
- Cost per page is list price of the recorded calls. A page of a research paper costs more than a
  page of a textbook; Opus's 6 papers are 27 pages.

## Reproduce

```
R="uv run python scripts/realworld_run.py"
$R rs --part dev --tag base-0503 --tables --notation-memory --line-context --glyph-checks \
  --flag-policy --seed-answers-from e1-control --max-usd 4 --section-workers 1 --concurrency 2
# add --offline to replay the recorded answers for nothing
uv run python scripts/realworld_report.py --part dev --tag base-0503 --rescore-chat --reading cells
uv run python scripts/realworld_report.py --part dev --tag base-0503 --rescore-chat --reading cells --neutral
uv run python scripts/realworld_report.py --part dev --tag base-0503 --rescore-chat --reading cells --spoken
uv run python scripts/realworld_report.py --part dev --tag base-0503 --rescore-chat   # reading A
uv run python scripts/dev_converter_effect.py --gold ~/.cache/readablestem/realworld/gold/dev \
  base0503=~/.cache/readablestem/realworld/runs/dev/base-0503/items.jsonl
```

A paid start needs a key and the application's own settings removed from the environment
(`scripts/eq_dev_run.py`, `scrub_environment`), so that a deployed value cannot change a request.
