# Equations, round 5: the page witness on twelve development papers (2026-11-03)

Development part only (`--part dev`); nothing here is held-out and no default changed. One run per arm,
every model answer recorded under `~/.cache/readablestem/realworld/runs/dev/` (`e1-control`,
`e1-sonnet`, `e1-witness-opus`). Runner: `scripts/eq_dev_run.py`. Intervals are 95% cluster bootstraps
over papers (`scripts/realworld_report.py`), so with 12 papers they are wide.

**The short version.** The page witness (`FEATURE_PAGE_WITNESS`, one model call per maths page that reads
every formula, see docs/steps.md) did not change a single reading and found no equation the pipeline had
missed. Exact matches and detection are the same to the digit with it on and off. It did what the rule
for it says: disagreements became required decisions, and those caught more wrong equations. Silent
errors fell by 2.1 per 100 gold [1.3, 2.9], flag recall rose from 0.26 to 0.41, and it cost 2.4 cents
a page more (Sonnet) and one more decision a page. **The registered stop rule applies: the dev gain on
exact is 0.0 points against the +2 needed, so the experiment stops here.** The feature stays off.

## What was run

- **Papers.** The first twelve of the development part's registered chat order
  (`realworld_run.chat_order`): 57 PDF pages and 1,096 gold equations, 914 aligned. Six of them (546 gold)
  have a recorded Opus chatbot answer (the dev run of 2026-10-21), so that comparison is on those six.
- **Control.** ReadableSTEM as production has it for typed PDFs (tables, notation memory, line context on,
  no second check), witness off. The crop answers of the recorded `line1` run replayed (1,261 of 1,339
  requests); the 78 the code had changed since were asked live ($0.10).
- **Witness arm.** The same, with `FEATURE_PAGE_WITNESS` on and `PAGE_WITNESS_MODEL`
  `claude-sonnet-5-5`, effort medium, output limit 12,288, call timeout 240 s. Control answers replayed, so
  the new spend is the witness calls and the crop calls of anything they changed (nothing).
- **Scoring.** Reading B (the maths cells of a rebuilt table count as equations, as in a chatbot's HTML),
  as in docs/eval-equation-round3.md; the notation-neutral and spoken-equal readings are the secondaries.
  Only the pipeline's code was run: the chatbot arm is the recorded one, re-scored by today's converter
  (`--rescore-chat`).
- **Opus witness: not measured.** See "Spend". Two Opus witness calls were answered (n = 2 pages): $0.11 and
  $0.20 each, against $0.025 a call for Sonnet (n = 55). That is about six times the price, so an Opus
  witness over twelve papers would have cost about $8 against a $5 cap.

## Results (reading B, 12 papers, 1,096 gold)

| | control (witness off) | witness, Sonnet | difference (paired) |
|---|---|---|---|
| exact_lenient (primary) | 0.753 [0.710, 0.792] | 0.753 [0.710, 0.792] | 0.000 [0.000, 0.000] |
| notation-neutral | 0.783 [0.733, 0.830] | 0.783 [0.733, 0.830] | 0.000 |
| spoken-equal (exploratory) | 0.813 [0.761, 0.860] | 0.813 [0.761, 0.860] | 0.000 |
| detection recall | 0.891 [0.844, 0.927] | 0.891 [0.844, 0.927] | 0.000 |
| wrong, not asked, per 100 gold | 10.68 [6.95, 14.85] | 8.58 [5.01, 12.39] | -2.10 [-2.94, -1.31], sign-flip p 0.004 |
| flag recall (symbols-wrong aligned equations that were a decision) | 0.262 [0.141, 0.402] | 0.411 [0.278, 0.569] | +0.149 |
| flag precision | 0.329 [0.164, 0.525] | 0.328 [0.191, 0.463] | -0.001 |
| required decisions per PDF page, all kinds | 3.33 [2.12, 4.65] | 4.35 [3.02, 5.83] | +1.02 |
| cost per PDF page | $0.0315 [0.0232, 0.0406] | $0.0555 [0.0465, 0.0659] | +$0.0239 (the witness) |

Under reading A (the registered table reading) the control and witness exact are both 0.706 [0.623, 0.771].

Against the recorded dev Opus chatbot, on the 6 papers both ran on (n = 6 papers, 546 gold; either
ReadableSTEM arm, they are the same):

| reading B | ReadableSTEM | Opus chatbot | difference (95%) |
|---|---|---|---|
| exact_lenient | 0.764 | 0.784 | -0.020 [-0.049, +0.033], sign-flip p 0.63 |
| notation-neutral | 0.788 | 0.810 | -0.022 [-0.047, +0.020] |
| spoken-equal | 0.811 | 0.813 | -0.002 [-0.039, +0.044] |

Silent errors on the same six: control 7.3, witness 5.7, Opus chatbot 9.5 per 100 gold (the intervals
overlap; n = 6).

## What the witness did, pair by pair

- **It asked for 55 pages and got 55 answers** (827 formulas proposed, 818 with their quotes holding, 9
  dropped for a quote that held nowhere or an overlap).
- **Where it stood against the elements** (formulas): 643 had the same extent as an element (within one
  glyph), 83 overlapped one with a different extent, 21 lay inside another element, 71 were witness-only
  without maths glyphs (left out by the glyph gate), and none was witness-only with glyph evidence. So
  detection additions: **0**. Whether the 71 without glyph evidence contain missed equations was not
  measured.
- **Readings changed: 0 of 914 aligned equations.** In the 18 disagreements the glyph adjudication kept
  the crop's reading every time (a tie or a crop win), so a better witness reading never reached the
  output and a worse one never did either.
- **Decisions it added:** 18 `witness_mismatch` (11 on an equation that was wrong) and 39
  `witness_extent_differs` (19 wrong). In all 49 equations became required that were not before, and 23 of
  them (47%) were wrong equations: these are the 2.1 per 100 fewer silent errors. The other 26 were
  decisions on equations that were already right.
- **Agreement is not safety.** Of the 857 aligned equations with no witness code (the witness agreed or
  had nothing to say), 747 were exactly right: 87.2%. That is not the oracle's P(correct | agree) of
  94.8% (docs/eval-equation-diagnostics.md, D2), since it includes equations the witness did not
  settle, but it points the same way: agreement is no reason to lift or skip anything, which is how the
  integration was built.

## Decision against the registered stop rule

Rule: stop if the dev gain is under +2 points or silent errors rise by more than 0.5 per 100. The gain on
exact is 0.0 points (under +2) and silent errors fell. **Stop.** E2 and E3 do not follow from this on the
rule as written. What the data supports is narrower: the witness works as a flagging layer (recall +0.15 at
+1 decision a page and +2.4 cents a page, Sonnet), not as a way to read more equations right. The part of
the plan that expected +2 to +4 points from the witness's own readings is what the result does not
support: its readings agree with the crop's where the crop is right, and where they disagree the glyph
rule keeps the crop's. A witness that was allowed to win a disagreement was not tried (the integration
notes ruled it out), and was not tested here.

## Spend

Cap $5.00 for the experiment; **about $4.38 spent** (by the job budget; every start is in
`data/realworld/spend-log.jsonl`):

| start | what | spent |
|---|---|---|
| control | the crop requests the recording could not answer | $0.10 |
| Opus witness, first start | 2 answered calls ($0.31); 2 timed out and were charged their whole reservation | $2.92 |
| Sonnet witness | 9-page probe, the full run, then the 12 pages whose answers were not kept | $1.36 |

The Opus start is a measurement problem found by the run, and the largest cost of the experiment.
`page_witness.py` sent its calls with the client's default 60 s timeout. A dense page with thinking takes
longer (the answered calls took 30 s and 58 s), a timed-out call is charged its whole worst-case
reservation (about $1.30 for Opus because of its refusal-fallback ceiling), and the reservations of four
pages in flight are held against `--max-usd` too, so the cap stopped the run after four calls. What was
changed: `PAGE_WITNESS_TIMEOUT_S` (default 240 s, never below the client's own) is set on the witness
request. The real bill for the two timed-out calls may be lower than the budget's figure; the ledger uses
the budget's. With about $0.62 left under the cap and one Opus call reserving $1.30, the Opus arm could not
be run again inside the cap, so **Opus against Sonnet as the witness model is not answered**: only the
cost ratio above (n = 2 against n = 55) is.

A second finding, fixed here: `eval pipeline`'s per-item cost did not include the witness's calls (it
did for the page math and image page passes), so the first Sonnet items showed the control's cost. Items
now carry `page_witness` (counts) and `page_witness_cost_usd`, and the report's `cost` block has
`page_witness_usd` and `page_witness_per_pdf_page_usd`.

## Limits

- 12 papers from one development set, 6 for the chatbot comparison; the dev set was used to tune the
  pipeline before, so absolute numbers are not an estimate for new papers. Fresh sets were not touched.
- One run per arm; the models are not deterministic. The control and the witness arm share every crop
  answer, which is why their readings are identical: a witness arm with its own crop answers would differ by
  noise.
- Sonnet only. A stronger witness might read more formulas right and so disagree on different ones; its
  readings could still not win a disagreement in this integration.
- Detection additions are 0 because of the glyph gate; the 71 witness formulas it left out are checked
  against the gold in "Diagnosis" below.
- Flag precision under 0.33 means two of three new decisions are on equations that are right.

## Reproduce

```
R="uv run python scripts/eq_dev_run.py"
$R papers
$R run control --offline                  # replay only, spends nothing
$R report --reading cells                 # add --neutral or --spoken for the secondaries
$R witness                                # what the witness changed against the control
$R run witness-sonnet-r2 --offline        # the recorded Sonnet answers settled by today's rule
$R witness --tag e1-sonnet-r2
```

A paid start needs a key (`railway run -s web -- $R run witness-sonnet --max-usd 1 --concurrency 2`) and
a cap sized for the reservations in flight, not for the expected cost.

## Diagnosis (2026-11-03): why the witness changed nothing

$0: the recorded Sonnet answers replayed offline (`run witness-sonnet-r2 --offline`; the replay reproduces
the arm's 914 of 914 final readings), with each witness reading converted by the pipeline's own path and scored
against the gold as the crop's is. Development papers only (12 papers, 1,096 gold, 914 aligned): nothing here
is an estimate for new papers.

**Adjudication.** 24 elements had a witness reading that differed from the crop's (18 aligned to a gold
equation). Against the gold, of the 18: witness right and crop wrong 7, crop right and witness wrong 2, both
right (a difference the scorer sees and the glyphs do not, such as `\ldots` for `...` or `\left\langle` for
`\langle`) 5, both wrong 4. The rule kept the crop's reading 24 times: 23 of the 24 were exact ties and one a
crop win (0.881 against 0.595); the witness won none. Cause: the glyph distance is the Dice similarity of the
bags of letters, digits and Greek letters, and two readings of one formula nearly always have the same ones,
so it is 1.0 against 1.0 whatever the operators, relations, slashes or structure say; the 0.05 near-tie rule then
keeps the crop. It is not a conversion or extent bug (a `match` extent is the element's within one glyph, the
witness reading goes through the same conversion and clean-up as the crop's, the text it is compared with is
the element's), and not the normalisation: the metric cannot see what the readings differ on. Whether the
witness is right in such a disagreement is mixed (7 against 2 among the aligned ones, plus 5 notation-only
cases), so letting it win every disagreement would be worth +5 equations of 914, and the
"extent differs" cases (65 aligned: crop right 37, witness right 21, witness only 4, crop only 20) show the witness
quotes the wrong stretch more often than the layout does.

**What the glyphs can separate.** The one case the text layer settles is a written slash against a drawn
fraction (`-2/3` read as `-\frac{2}{3}`: the text layer has the slash, a fraction bar is never text); in 2 of the
7 witness-right cases the crop made that mistake. Brackets do not separate: the text of an element often holds the
bracket of its neighbour (`(R_{*,1})` against gold `R_{*,1}`), which the crop's clean-up drops on purpose; a
bracket mark picked the witness there and was removed again. The other witness-right cases (a relation read as
`\succsim` for `\asymp`, `\ldots`, `\left` delimiters) are not told apart by the bag or the slash.

**The change.** `witness_reconcile.adjudicate`: when the letters tie (within `TIE_BELOW`), the slashes in the
glyphs decide (`marks_dice`, `MARKS = "/"`). The readings are shown and the formula is a required decision as
before. Re-scored on the same recorded answers, with the same crop answers:

| 12 dev papers (1,096 gold) | witness, as run | witness, slash tie-break |
|---|---|---|
| exact_lenient (reading B) | 0.753 [0.710, 0.792] | 0.755 [0.712, 0.793] |
| readings changed from the control | 0 of 914 | 2 of 914 (both wrong to right) |
| silent wrong per 100 gold | 8.58 [5.01, 12.39] | 8.58 [5.01, 12.39] |
| required decisions per PDF page | 4.35 [3.02, 5.83] | 4.35 [3.02, 5.83] |
| flag recall / precision | 0.411 / 0.328 | 0.400 / 0.313 |

Two equations, +0.2 points: the two readings were already decisions, so decisions and silent errors do not
move (the flag recall falls because two flagged equations are no longer wrong). Agreement stays a note: of the
581 aligned equations where the witness's extent matched and it agreed, 57 (9.8%) were wrong in both.

**Detection.** 818 verified formulas: 726 were taken by an element, 71 had no glyph evidence and 21 had it
but lay inside another element (paragraph, table, figure; left to that element). Checked against the gold on
the same page (exact under the lenient comparison, or tree similarity of 0.6 or more for "near"):

| the witness-only formula | no glyph evidence (71) | glyph evidence, inside text (21) |
|---|---|---|
| equals a gold equation the run missed | 11 (`95\%` x2, `3\%`, `10\%`, `20\%`, three decimals, `(1)`, `(2)`, `g`) | 2 |
| resembles a missed one (units, subscripts) | 6 | 6 (`\text{EPR}_{ex}` x6, one paper) |
| equals a gold equation already read | 21 | 1 |
| no gold equation | 33 (21 of them a number and a unit) | 11 (block formulas the run reads elsewhere) |

So 17 to 25 of 182 missed gold equations (1.6 to 2.3 points of recall) lie in the 92 formulas, at about a quarter
precision. Nothing in the data separates them: the maths-glyph counts are the same (0 to 3 characters of 3 to
11) on the right and wrong ones, since a plain number, a percentage or a unit is typeset in the text font
whether or not the author put it in `$...$`; whether a quantity is gold is the author's convention, and "a number
and a unit" is on both sides (`9.8 m/s^2` is gold, `0.5 s` is not). The D3 evidence (docs/eval-equation-diagnostics.md:
45 to 70% of undetected running-text equations carry some, against 4% background) does not reach these, which are
the ones with none. The gate ("maths glyphs and clear of every other element") is therefore not buggy and
loosening it would add about 90 required decisions (1.6 a page) for 17 to 25 true ones; it was left as it is.

**Sonnet against Opus, at $0** (6 papers with the recorded Opus chatbot, 546 gold). On the 269 equations that
have a witness reading: ours right 241 (89.6%), Sonnet witness 233 (86.6%), Opus chatbot 238 (88.5%). Where ours is
wrong (28 of them) the chatbot is right on 8 and the Sonnet witness on 3; where ours is right the chatbot is
wrong on 11 and the witness on 11. So an Opus witness would read about 5 more equations right than Sonnet in
these papers (1.9 points of the witnessed ones, 0.9 of all 546), and a perfect chooser between ours and it
would gain 8 (1.5 points), under the +2 the plan needed; an Opus witness that always won would lose more than it
gains. The chatbot's lead on these six papers (426 right against ours 370 of 546) is mostly detection: 53 of the
126 gold equations we did not align are read right by it (the witness's formulas equal 12 of them), plus 15
readings that were wrong. How much of those 53 is alignment (the chatbot is aligned by sequence, ours by
position, and repeated equations such as `n = 10^4` are counted once per gold) was not separated. The
oracle's +9.3 points is that: detection and alignment of a whole-page reading, not better readings of the
equations we find.

Not answered: the Opus witness itself (it needs a paid run), and whether a different witness prompt (asked
for every formula with no row anchors) would find the 53; the witness is offered rows of the text layer and can
only quote them.
