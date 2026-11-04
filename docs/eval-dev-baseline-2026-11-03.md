# Equations: where ReadableSTEM stands on the 25 real-world development papers, against the recorded chatbots (2026-11-03)

Development part only (`--part dev`); the held-out and fresh parts were not touched and no default
changed. One run of ReadableSTEM at `main` 91d0046 (pipeline 0.5.3, 2026-11-03), scored with the
recorded Opus and Haiku chatbot answers of 2026-10-21, which were re-scored, not re-asked. Intervals
are 95% cluster bootstraps over papers (`scripts/realworld_report.py`, 10,000 resamples), so with 25
papers, and 6 for Opus, they are wide. The development papers were used to build the extractor and to
tune the pipeline before: these numbers are a baseline for this set, not an estimate for new papers.

*Update: the Opus chatbot arm now covers all 25 papers (see "Opus arm on all 25 development papers" at
the end). The Opus figures in this first part are for the 6 papers that had it then.*

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

## Opus arm on all 25 development papers

Added 2026-11-03 (the last call ended 2026-11-04 02:07 UTC). The baseline above had a recorded Opus
chatbot answer for 6 of the 25 development papers. This section records the Opus arm on the other 19, so
both chatbot arms and ReadableSTEM cover all 25 (114 PDF pages, 2,350 gold equations). Development part
only; no held-out or fresh paper was touched, and ReadableSTEM was not run again (its answers are the
`base-0503` recording, scored as above). Intervals are 95% cluster bootstraps over papers (10,000
resamples); n = 25 papers throughout unless stated.

**The short version.** With Opus on all 25 papers, ReadableSTEM writes 0.735 of the equations exactly
right (reading B, 0.677 to 0.783), the Opus chatbot 0.708 (0.613 to 0.787) and the Haiku chatbot 0.674
(0.605 to 0.738). The paired difference against Opus is +0.027 (95% -0.029 to +0.093, sign-flip
p = 0.654, n = 25 papers); against Haiku +0.060 (+0.004 to +0.120, p = 0.080, n = 25). No difference
against Opus is detected under any reading, and the interval is wide enough to include a gap of about
3 points either way. On the 6 papers of the baseline the Opus chatbot had scored 0.784; on the 19 new
papers it scores about 0.685 (1,235 of 1,804 gold, derived from the totals), so those 6 were an easy
draw and the "ReadableSTEM -0.020 against Opus" of the baseline was a 6-paper figure, not a 25-paper
one. ReadableSTEM leaves 9.5 wrong equations in 100 unasked, Opus 11.1, Haiku 16.5. Opus's detection
precision is lower (0.767 against 0.866 for ReadableSTEM): it writes more equation text that is not in
the gold.

### How the arm was recorded

- Arm `chatbot-opus-purdue` (`claude-opus-5-5`), `scripts/realworld_run.py chat --part dev --papers
  <19 ids> --max-usd 12 --workers 2`, which is `proof.chatbot.record_arms` with the settings of the 6
  existing recordings: the PDF as a document block, a 150 dpi PNG of every page, the Purdue sentence
  verbatim, no system prompt, default effort, 64,000 maximum output tokens, streamed. Recorded next to
  the existing ones (`~/.cache/readablestem/realworld/runs/dev/chat/systems/chatbot-opus-purdue/`, same
  file format). The 19 papers are the 25 of `manifest-dev.json` minus the 6 already recorded.
- All 25 recordings end with `response_status end_turn`: none cut off, none empty; no cut-off answer was
  asked again. One paper (2412.08797) was re-asked once by the runner after an `APIConnectionError`
  (the runner's existing retry for connection errors; its cost is estimated from usage and flagged
  `cost_estimated` in its file).
- The key came from the environment through `railway run`; every other setting was removed from the
  process before the run (`env -i`, key only).

### Results (reading B)

| | ReadableSTEM (25) | Chatbot, Opus (25) | Chatbot, Haiku (25) |
|---|---|---|---|
| gold equations / PDF pages | 2,350 / 114 | 2,350 / 114 | 2,350 / 114 |
| exact_lenient, end to end | 0.735 [0.677, 0.783] | 0.708 [0.613, 0.787] | 0.674 [0.605, 0.738] |
| notation-neutral | 0.763 [0.704, 0.812] | 0.728 [0.631, 0.811] | 0.693 [0.619, 0.759] |
| spoken-equal (exploratory) | 0.783 [0.721, 0.833] | 0.728 [0.633, 0.808] | 0.723 [0.655, 0.782] |
| symbols, credited (secondary) | 0.777 [0.718, 0.826] | 0.744 [0.644, 0.826] | 0.712 [0.639, 0.778] |
| detection recall | 0.871 [0.820, 0.911] | 0.819 [0.730, 0.891] | 0.840 [0.786, 0.884] |
| detection precision | 0.866 [0.838, 0.894] | 0.767 [0.686, 0.846] | 0.876 [0.829, 0.918] |
| wrong, not asked, per 100 gold (exact_lenient) | 9.53 [7.41, 11.91] | 11.15 [8.25, 14.65] | 16.51 [11.91, 21.65] |
| wrong, not asked, per 100 gold (notation-neutral) | 7.40 [5.52, 9.58] | 9.11 [6.16, 12.64] | 14.64 [10.15, 19.96] |
| wrong, not asked, per 100 gold (spoken-equal) | 6.26 [4.04, 8.86] | 9.11 [6.14, 12.75] | 11.66 [8.49, 15.22] |
| required decisions per PDF page, all kinds | 3.87 [2.69, 5.25] | 0 | 0 |
| cost per PDF page | $0.0313 [0.0238, 0.0402] | $0.0865 [0.0809, 0.0924] | $0.0022 [0.0021, 0.0024] |

Under reading A (the registered table reading) exact_lenient is 0.703 [0.636, 0.759] for ReadableSTEM,
0.708 [0.613, 0.787] for Opus and 0.674 [0.605, 0.738] for Haiku (the chatbots are the same under both
readings). The ReadableSTEM and Haiku columns equal the baseline's; only the Opus column is new.

### Paired differences (ReadableSTEM minus the chatbot, reading B, 95% cluster interval, n = 25 papers)

| metric | minus Opus | sign-flip p | minus Haiku | sign-flip p |
|---|---|---|---|---|
| exact_lenient | +0.027 [-0.029, +0.093] | 0.654 | +0.060 [+0.004, +0.120] | 0.080 |
| notation-neutral | +0.035 [-0.024, +0.106] | 0.488 | +0.070 [+0.016, +0.129] | 0.016 |
| spoken-equal | +0.055 [-0.013, +0.138] | 0.318 | +0.060 [+0.013, +0.112] | 0.023 |
| symbols, credited | +0.033 [-0.023, +0.100] | 0.548 | +0.065 [+0.012, +0.123] | 0.035 |
| detection recall | +0.052 [-0.012, +0.135] | 0.297 | +0.032 [-0.007, +0.080] | 0.154 |
| wrong, not asked, per 100 gold (exact_lenient) | -1.62 [-4.01, +0.54] | 0.306 | -6.98 [-12.00, -2.60] | 0.015 |
| wrong, not asked, per 100 gold (notation-neutral) | -1.70 [-4.03, +0.33] | 0.223 | -7.23 [-12.12, -3.06] | 0.004 |
| wrong, not asked, per 100 gold (spoken-equal) | -2.85 [-5.05, -0.75] | 0.087 | -5.40 [-8.36, -2.45] | 0.007 |

Under reading A the differences are -0.004 (-0.073 to +0.069, p = 0.557) against Opus and +0.029
(-0.040 to +0.096, p = 0.676) against Haiku; wrong, not asked, against Opus is -2.89 (-5.95 to -0.47,
p = 0.022). No interval for exact_lenient against Opus excludes zero under any reading. The p-values are
descriptive; none of the comparisons is registered, and the set was used to tune the pipeline.

### Where the misses are (reading B, exact_lenient, n = 25 papers, 2,350 gold each)

The same buckets as above (`scripts/dev_miss_places.py`, which reproduces the baseline's ReadableSTEM and
Haiku counts exactly). Counts of gold equations, per 100 gold in brackets.

| miss bucket | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku |
|---|---|---|---|
| right | 1,727 (73.5) | 1,663 (70.8) | 1,585 (67.4) |
| in tables and figures (any failure) | 180 (7.7) | 173 (7.4) | 252 (10.7) |
| inline span edges (running text) | 90 (3.8) | 65 (2.8) | 71 (3.0) |
| short inline expressions in running text, not found (1 to 3 symbols) | 125 (5.3) | 153 (6.5) | 121 (5.1) |
| other | 228 (9.7) | 296 (12.6) | 321 (13.7) |
| misses, total | 623 (26.5) | 687 (29.2) | 765 (32.6) |

| Opus chatbot, 25 papers | gold | right | scorer | span | recognition | no proposal | not found |
|---|---|---|---|---|---|---|---|
| running text and algorithm | 1,950 | 1,436 | 68 | 65 | 78 | 9 | 294 |
| table | 223 | 97 | 7 | 6 | 17 | 0 | 96 |
| figure (caption or panel) | 177 | 130 | 3 | 8 | 1 | 0 | 35 |

Opus gets more table equations right than ReadableSTEM (97 of 223 against 86) and more of the aligned
display equations (align-like 0.449 against 0.410, 78 gold). It does not find 294 of the 1,950
running-text equations, against 171 for ReadableSTEM, which is where its larger "other" comes from (42
display equations and 99 longer inline expressions not found, against 12 and 34). Its spans are cut too
long 66 times and too short 13.

### Spend

The Opus arm cost **$7.4938** at list price, measured from the usage of the 19 recorded answers (87
pages; estimate $5.22 at the script's per-page figure, cap $12.00, nothing refused by the cap). One line in
`data/realworld/spend-log.jsonl` (arm `chatbot-opus-purdue`, part `dev`). The run was cut twice by an
`APIConnectionError` and restarted by the script; a call lost that way is not recorded, so a partial answer
billed by the provider and not recorded is possible and is not counted here. The re-scoring (three
readings, reading A, the miss places) cost nothing. ReadableSTEM was not run again.

### Limits of this section

- 25 development papers the pipeline was tuned against: a baseline for this set, not an estimate for new
  papers. One run of each arm; the models are not deterministic.
- The Opus 6-paper figures in the baseline above come from the same recordings and are unchanged; this
  section adds the other 19.
- The interval for Opus (0.613 to 0.787) is wide because of papers where it drops whole running-text
  equations; with 25 papers a difference of 3 points either way is within the interval.

### Reproduce

```
R="uv run python scripts/realworld_run.py"
$R chat --part dev --arm chatbot-opus-purdue --papers <the 19 ids> --max-usd 12 --workers 2
uv run python scripts/realworld_report.py --part dev --tag base-0503 --rescore-chat --reading cells
# add --neutral or --spoken, or drop --reading cells for reading A
uv run python scripts/dev_miss_places.py --part dev --tag base-0503
```

## Pipeline 0.5.7

Added 2026-11-04. ReadableSTEM re-run on the 25 development papers at commit `c0b5b69` (the deploy of
2026-11-03; pipeline 0.5.7: glyph spans #116, table-cell maths #120, short inline maths #124, TeX
producer detection #129), tag `base-0507`, against the `base-0503` run of pipeline 0.5.3 and the recorded
Opus and Haiku chatbot answers (all 25 papers, 114 pages, 2,350 gold; development part only, no held-out
or fresh paper touched, no chatbot asked). Same flags, models, tooling, scorer and reading B as the
baseline above; intervals are 95% cluster bootstraps over the 25 papers (10,000 resamples), p-values
sign-flip, descriptive and not registered. The set was used to tune the pipeline.

**The short version.** Pipeline 0.5.7 writes 0.736 of the equations exactly right (reading B, 0.679 to
0.783), pipeline 0.5.3 0.735, the Opus chatbot 0.708 (0.613 to 0.787), the Haiku chatbot 0.674. The paired
difference against Opus is +0.029 (95% -0.029 to +0.096, p = 0.626): ReadableSTEM is ahead of Opus on
the point estimate, and the interval does not exclude zero, so these papers do not show a difference.
The same holds under the notation-neutral reading (+0.037, -0.023 to +0.107) and the spoken-equal
reading (+0.056, -0.013 to +0.139). Against Haiku the difference is +0.062 (+0.005 to +0.122, p = 0.077),
as before. The change from 0.5.3 to 0.5.7 is +0.001 (-0.009 to +0.010, p = 0.852): the four fixes did
not move the end-to-end score on this set. They moved table equations (86 to 95 right of 223) and
slightly raised detection recall; they did not move short inline maths (125 to 122 missed), span edges
got a little worse (90 to 96 in running text), and equations wrong without being asked went from 9.5 to
10.2 per 100 gold (+0.68, -0.09 to +1.84, p = 0.297).

### How it was run

- `scripts/realworld_run.py rs --part dev --tag base-0507` with the flags of the base-0503 run (`--tables
  --notation-memory --line-context --glyph-checks --flag-policy`; page math and page witness off;
  `--section-workers 1 --concurrency 2`), from a detached worktree at `c0b5b69`. The key came from the
  environment through `railway run`; the process was started with `env -i`, the key only, so no
  application setting reached a request. Pipeline version recorded in the report: 0.5.7; models
  `claude-opus-5-5` (main) and `claude-haiku-5-5` (cheap), as in the baseline.
- Answers: the run started from the recorded answers of `base-0503`. A request whose text matches a
  recorded answer is replayed; one that the code changes since had altered is asked live. The first offline
  pass reported 570 requests without a recorded answer; the first live pass finished with 377 still
  unrecorded (a request's text depends on earlier answers, so the count changes as answers are asked), the
  second with 57, and the third pass reported 2,718 hits and 0 misses. Cost per page is the list
  price of all 2,709 recorded calls (replayed ones included), as in the baseline.
- Recordings in `~/.cache/readablestem/realworld/runs/dev/base-0507/` next to `base-0503`.

### Results (reading B, n = 25 papers, 2,350 gold, 114 pages)

| | ReadableSTEM 0.5.7 | ReadableSTEM 0.5.3 | Chatbot, Opus | Chatbot, Haiku |
|---|---|---|---|---|
| exact_lenient, end to end | 0.736 [0.679, 0.783] | 0.735 [0.677, 0.783] | 0.708 [0.613, 0.787] | 0.674 [0.605, 0.738] |
| notation-neutral | 0.765 [0.707, 0.812] | 0.763 [0.704, 0.812] | 0.728 [0.631, 0.811] | 0.693 [0.619, 0.759] |
| spoken-equal (exploratory) | 0.784 [0.723, 0.833] | 0.783 [0.721, 0.833] | 0.728 [0.633, 0.808] | 0.723 [0.655, 0.782] |
| symbols, credited (secondary) | 0.779 [0.720, 0.829] | 0.777 [0.718, 0.826] | 0.744 [0.644, 0.826] | 0.712 [0.639, 0.778] |
| detection recall | 0.879 [0.830, 0.915] | 0.871 [0.820, 0.911] | 0.819 [0.730, 0.891] | 0.840 [0.786, 0.884] |
| detection precision | 0.861 [0.831, 0.889] | 0.866 [0.838, 0.894] | 0.767 [0.686, 0.846] | 0.876 [0.829, 0.918] |
| wrong, not asked, per 100 gold (exact_lenient) | 10.21 [7.83, 12.94] | 9.53 [7.41, 11.91] | 11.15 [8.25, 14.65] | 16.51 [11.91, 21.65] |
| wrong, not asked, per 100 gold (notation-neutral) | 8.04 [5.85, 10.64] | 7.40 [5.52, 9.58] | 9.11 [6.16, 12.64] | 14.64 [10.15, 19.96] |
| wrong, not asked, per 100 gold (spoken-equal) | 6.89 [4.45, 9.92] | 6.26 [4.04, 8.86] | 9.11 [6.14, 12.75] | 11.66 [8.50, 15.22] |
| equation flags, recall | 0.300 [0.191, 0.409] | 0.328 [0.213, 0.439] | 0 | 0 |
| equation flags, precision | 0.330 [0.199, 0.474] | 0.338 [0.204, 0.482] | n/a | n/a |
| required decisions per PDF page, all kinds | 3.62 [2.61, 4.75] | 3.87 [2.69, 5.25] | 0 | 0 |
| of which on an aligned equation | 1.97 [1.26, 2.77] | 2.00 [1.29, 2.83] | 0 | 0 |
| cost per PDF page | $0.0304 [0.0239, 0.0373] | $0.0313 [0.0238, 0.0402] | $0.0865 [0.0809, 0.0924] | $0.0022 [0.0021, 0.0024] |

Under reading A (the registered table reading) exact_lenient is 0.716 [0.657, 0.764] for 0.5.7 and
0.703 [0.636, 0.759] for 0.5.3 (difference +0.012, -0.006 to +0.041, p = 0.434); against Opus +0.008
(-0.058 to +0.080, p = 0.866), against Haiku +0.041 (-0.026 to +0.108, p = 0.373). The 0.5.3, Opus and
Haiku columns reproduce the earlier sections of this document.

### Paired differences (reading B, 95% cluster interval, sign-flip p, n = 25 papers)

| metric | 0.5.7 minus Opus | p | 0.5.7 minus Haiku | p | 0.5.7 minus 0.5.3 | p |
|---|---|---|---|---|---|---|
| exact_lenient | +0.029 [-0.029, +0.096] | 0.626 | +0.062 [+0.005, +0.122] | 0.077 | +0.001 [-0.009, +0.010] | 0.852 |
| notation-neutral | +0.037 [-0.023, +0.107] | 0.464 | +0.071 [+0.016, +0.131] | 0.016 | +0.001 [-0.009, +0.010] | 0.852 |
| spoken-equal | +0.056 [-0.013, +0.139] | 0.303 | +0.061 [+0.014, +0.113] | 0.023 | +0.001 [-0.009, +0.010] | 0.844 |
| symbols, credited | +0.035 [-0.022, +0.103] | 0.511 | +0.067 [+0.013, +0.127] | 0.033 | +0.002 [-0.008, +0.011] | 0.781 |
| detection recall | +0.060 [-0.005, +0.142] | 0.200 | +0.039 [-0.002, +0.088] | 0.096 | +0.007 [0.000, +0.018] | 0.125 |
| wrong, not asked, per 100 gold (exact_lenient) | -0.94 [-2.99, +0.95] | 0.563 | -6.30 [-11.25, -1.96] | 0.031 | +0.68 [-0.09, +1.84] | 0.297 |
| wrong, not asked, per 100 gold (notation-neutral) | -1.06 [-3.12, +0.75] | 0.425 | -6.60 [-11.41, -2.51] | 0.008 | +0.64 [-0.13, +1.81] | 0.406 |
| wrong, not asked, per 100 gold (spoken-equal) | -2.21 [-4.11, -0.38] | 0.170 | -4.77 [-7.61, -1.99] | 0.016 | +0.64 [-0.13, +1.81] | 0.344 |

Is ReadableSTEM ahead of Opus on these 25 papers? On the point estimate, yes, by 3 points (exact), 4
(neutral) and 6 (spoken); on detection precision by 9 points and on cost by 2.8 times. But no interval
for exact_lenient, notation-neutral or spoken-equal excludes zero (lower ends -0.029, -0.023, -0.013),
and the sign-flip p-values are 0.30 to 0.63, so this set does not show a difference against Opus; a gap
of about 3 points either way is inside the interval. The size of the lead is what it was with 0.5.3
(+0.027). Against Haiku the interval excludes zero under the notation-neutral and spoken-equal readings
and just excludes it under exact (+0.005).

### Where the misses are (reading B, exact_lenient, counts of gold equations, per 100 gold in brackets)

`scripts/dev_miss_places.py --part dev --tag base-0507` (and `base-0503`), same buckets as above.

| miss bucket | 0.5.7 | 0.5.3 | Opus | Haiku |
|---|---|---|---|---|
| right | 1,730 (73.6) | 1,727 (73.5) | 1,663 (70.8) | 1,585 (67.4) |
| in tables and figures (any failure) | 173 (7.4) | 180 (7.7) | 173 (7.4) | 252 (10.7) |
| inline span edges (running text) | 96 (4.1) | 90 (3.8) | 65 (2.8) | 71 (3.0) |
| short inline expressions in running text, not found (1 to 3 symbols) | 122 (5.2) | 125 (5.3) | 153 (6.5) | 121 (5.1) |
| other | 229 (9.7) | 228 (9.7) | 296 (12.6) | 321 (13.7) |
| misses, total | 620 (26.4) | 623 (26.5) | 687 (29.2) | 765 (32.6) |

| ReadableSTEM by place | gold | right 0.5.7 (0.5.3) | scorer | span | recognition | no proposal | not found 0.5.7 (0.5.3) |
|---|---|---|---|---|---|---|---|
| running text and algorithm | 1,950 | 1,503 (1,507) | 81 | 96 | 99 | 3 | 168 (171) |
| table | 223 | 95 (86) | 11 | 6 | 26 | 0 | 85 (99) |
| figure (caption or panel) | 177 | 132 (134) | 4 | 8 | 0 | 1 | 32 (32) |

Table equations are the one place the four changes show: 9 more right, 14 fewer not found, and 4 more
found and read wrong. Short inline maths barely moved (125 to 122), running-text spans got 6 worse (90 to
96), and the running-text right count is 4 lower. These are single-run differences of a few equations in
2,350; one run each, so run-to-run variation of the models is not measured here.

### Spend

Cap $8.00 for the measurement; **$0.4142 spent**, all of it ReadableSTEM's own pipeline calls: the first
live pass $0.3455 (cap $8.00), the second $0.0687 (cap $4.00); the third and a repeat of it asked
nothing ($0.00). Four lines in `data/realworld/spend-log.jsonl`, tag `base-0507`. Re-scores, miss places
and the paired differences cost nothing. No chatbot arm was run; the full run's recorded calls are $3.47
at list price ($0.0304 per page, 2,709 calls).

### Reproduce

```
R="uv run python scripts/realworld_run.py"
$R rs --part dev --tag base-0507 --tables --notation-memory --line-context --glyph-checks \
  --flag-policy --seed-answers-from base-0503 --max-usd 8 --section-workers 1 --concurrency 2
# repeat until the offline pass (--offline) reports 0 misses; run at c0b5b69 with the key only in the environment
uv run python scripts/realworld_report.py --part dev --tag base-0507 --rescore-chat --extra-arm rs0503=base-0503 --reading cells
# add --neutral or --spoken, or drop --reading cells for reading A
uv run python scripts/dev_miss_places.py --part dev --tag base-0507
```
