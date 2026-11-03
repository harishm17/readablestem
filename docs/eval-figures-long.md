# Figure gold v2 and the long-description pre-registration (draft)

Status: **DRAFT, awaiting owner sign-off.** The gold set is built and checked offline. No model has
run on it, and none will until section 3 is signed (the date and the hash of this file are then
written at the bottom, as the other pre-registrations do).

## 1. Why a second figure set

The v1 figure gold ([eval.md](eval.md), "figures") is 13 OpenStax Physics figures. The
long-description feature (long descriptions and chart data tables,
[steps.md](steps.md#long-descriptions-data-tables-and-colour-feature_long_descriptions)) was
judged on them once (2026-10-09): 9 figures got a judged long description, all 9 passed, and the
Wilson 95% interval for 9 of 9 runs from 0.70 to 1.00. The long judge is not calibrated against
human labels. Three data tables were judged. That cannot support a bar, and 4 charts in 13 is too
few for chart tables.

Figure gold v2 is 50 figures, in four strata, from CC BY sources, drawn by a written seeded rule
(section 2). It lives in `data/gold-figures-v2/`, apart from `data/gold/` so that `gold fetch`
and its manifest are untouched.

## 2. The set

### The rule (code: `gold/figures_v2.py`; the draw is a pure function, tested)

1. **Licence gate.** OpenStax: the book's licence is plain CC BY in both the archive and the CMS
   (`gold licenses`, `data/openstax-licenses.csv`). arXiv: CC BY 3.0 or 4.0 in the OAI record.
   NC, SA, ND, CC0 and "arXiv non-exclusive" are out; a missing licence is out.
2. **Exclusion gate.** No figure from a page whose slug is a registered held-out section (any
   book, so a later edition of a held-out section is out too), or that another gold, development
   or held-out set under `data/` already uses (54 slugs). 47 pages were dropped this way.
3. **Credit gate.** The book's CC BY does not override a figure's own credit line. A caption
   credit that names another licence, permission, copyright, Wikimedia Commons or Flickr takes
   the figure out, and so does a credit to a person or a body not on a short free list (US
   government bodies, whose works are public domain, and ESO, CC BY): its terms are not stated.
   Every credit of a kept figure is recorded in `ATTRIBUTION.md` as the caption prints it
   (`third_party_licence`; 7 of the 50 carry one). 3 figures drawn earlier were redrawn in
   order after this gate.
4. **Reference gate.** One image; the OpenStax alt text (the reference long description) at least
   120 characters; for charts at least four numbers of two digits or with a decimal point in it
   (readable values: a proxy, not a check); no maths in the caption.
5. **Stratum** from the first 90-200 characters of the alt text, by a fixed keyword list
   (`classify_openstax`): chart (graph, plot, histogram, pie chart, ...), chemistry (structures,
   Lewis symbols, ball-and-stick and space-filling models, structural formulas), photo or
   illustration, diagram (circuit, schematic, cycle, cross section, flow chart, ...). Anything
   else is not a candidate. The same image in two editions counts once.
6. **Draw.** Inside each stratum, order by SHA-256 of `seed|stratum|id` (seed
   `figures-v2-2026-11-02`); charts from arXiv with plotted data would go first, since only they
   have an exact reference table. Walk the order; keep a figure unless its page already gave one or
   its book already gave 3 to the stratum (6 for chemistry, which has few books). Stop at the quota.
   A figure that fails to download, or that a look at the image shows to be wrong, is skipped in
   order, never replaced by choice (`selection.json` lists them).

### What was drawn (50 figures; `selection.json`)

| Stratum | Quota | Drawn | Pool after the gates | Analysis / held half |
|---|---|---|---|---|
| Data charts and graphs | 14 | 14 | 159 | 7 / 7 |
| Diagrams | 14 | 14 | 740 | 7 / 7 |
| Photos and illustrations | 10 | 10 | 628 | 5 / 5 |
| Chemistry structures | 12 | 12 | 126 | 6 / 6 |
| **Total** | 50 | 50 | 1,653 | 25 / 25 |

By book: Chemistry: Atoms First 12, Biology 11, Astronomy 8, College Physics 7, Statistics 4,
Anatomy and Physiology 4, Principles of Macroeconomics 2e 3, Physics 1. **45 of the 50 come from
retired editions** (CC BY, but whether a retired edition may serve as ground truth is still open:
[decisions/0001-scope.md](decisions/0001-scope.md), question 2). Only Physics and Statistics are
live CC BY STEM books, and their pages were mostly used in development; no live CC BY book has
chemistry structures. 49 images are JPEG, one PNG.

### What each record holds (`figures.jsonl`, one line per figure)

`id`, `stratum`, `split`, `source`, `book`, `book_state`, `page_key`, `source_url`,
`licence_name`, `licence_url`, `attribution`, `image` (`url`, `sha256`, `bytes`, `media_type`,
size), `caption`, `reference_alt`, `reference_long_description`, `reference_long_source`,
`reference_table` (or null). `ATTRIBUTION.md` lists every figure with its licence and source.
`scripts/figures_v2_build.py check` and `tests/test_gold_figures_v2.py` re-run the licence gate on
the committed file.

- **OpenStax images are not committed.** A run downloads each one and checks its sha256 (as the v1
  gold does). The record has the URL in the pinned archive release `20260604.144757`.
- **The reference long description is the OpenStax alt text, unchanged** (median 485 characters,
  123 to 1,868; 9 are over 1,000). The long judge sees the first 250 characters of it, as a hint;
  the image is the ground truth, so the judge is not scored against the reference.
- **The reference can be wrong.** One figure drawn first (Astronomy 5-2) had an alt text that
  describes curves at 3000 to 6000 K while the image labels 2500 to 5500 K; it was skipped in order
  (`exclusions.json`). Only the 14 charts were looked at; the other 36 references are unchecked.
- **Reference tables, 4 of 14 charts** (`tables-from-alt.json`): where the alt text states the
  values and the figure prints them (an ideal-gas table, bar labels of the Kepler planet
  counts, water shares, two production frontiers' end points), a table was written from the alt text and
  checked against the image by the set builder. They are not yet checked by a person other than the builder
  (`verified_by_person: false`). The other 10 charts have no reference table: their values are
  curves with no printed numbers.

### The arXiv strand found nothing

The plan was to take plotted data from the TeX source of CC BY arXiv papers. The parser is built
and tested (`gold/figures_v2_arxiv.py`: inline `coordinates {...}` or inline `table {...}` of a
single `axis`, 3 to 40 points, up to 5 series; it writes the reference table and a description
built only from those numbers, and `render` compiles the figure with tectonic). Run over the first
100 papers of the seeded order of the 10,165-paper CC BY pool (`data/realworld/pool.json`), it
found **0** charts that pass; 1 of 368 papers already cached for the real-world set had one.
pgfplots with inline numbers is rare, and most plots read a data file or hold hundreds of points.
So the set has no arXiv figures and no exact source-data table, and the table cell-accuracy
measure (section 3.3) has only the 4 alt-text tables behind it. Open for a later round: a larger
scan (cost: one polite download every 3 seconds, about 1 hit per 400 papers), or charts from
repositories that publish their plot scripts and data under CC BY.

### Rebuild

```bash
uv run python scripts/figures_v2_pool.py openstax      # pool-openstax.json (cached HTTP, outside the repo)
uv run python scripts/figures_v2_arxiv.py scan --limit 100   # pool-arxiv.json (optional)
uv run python scripts/figures_v2_build.py build        # figures.jsonl, selection.json, ATTRIBUTION.md
uv run python scripts/figures_v2_build.py check
```

None of these calls a model. A rebuild on a later archive release can change the pool; the
committed `pool-openstax.json` and the hashes in `selection.json` are what the draw used.

## 3. Pre-registration (draft)

### 3.1 Question

On 50 figures that no earlier step run has seen, does the figure step with long descriptions on
write long descriptions a student could use in place of the figure, without invented numbers, and
data tables whose values are right?

### 3.2 Arms

| Arm | What runs |
|---|---|
| **A0, baseline** | `figure_step.describe` with `FEATURE_LONG_DESCRIPTIONS=true` at the commit that holds the signed registration: the provider, main model, prompt version and judge (provider, model, prompt version) are written into the report. One run on all 50. |
| **A1, one revision** | At most one revised long-description prompt (`figure_long.md`), made after A0 from the **analysis half only** (25 figures; their judge failures may be read). Run on all 50; the claim is on the held half. |
| **C, confirmatory** | The arm that is final after A0 and A1, run once more on all 50, so run-to-run variation is visible. Its numbers are the reported ones. If A0 meets every bar in 3.4, A1 is not run and C is a second run of A0. |

Re-judging (the same outputs, a revised judge, section 3.6) is not a new arm.

### 3.3 What is measured

Per run, over figures the judge answered, with n beside every rate and a Wilson 95% interval:

- **Judge pass rate** of long descriptions: `accurate` and `complete` and not `invented_values`
  (`judge.judge_long`). A figure with no long description counts as a fail (a chart or diagram
  left without one gives the reader nothing); a failed judge call is excluded and counted.
- **Factual errors:** the share with `accurate = false`, and the share with `invented_values =
  true` (separately).
- **Table values OK** (judge): the share of proposed data tables with `table_values_ok = true`,
  over charts that got a table; also `table_wrong_values` summed.
- **Table cell accuracy** (exact, no model): for the charts with a reference table, `score_table`
  (`gold/figures_v2_table.py`) matches rows by their first column and counts a cell right when it is
  within 5% of the reference column's range. Reported as cells right / cells, with rows missing
  and extra. n is 4 charts, 32 cells, so this is reported, not tested against a bar.
- Per stratum and per half, the same counts. Cost per run (`cost.per_item_usd`), from the report.

### 3.4 Success criteria (set before any run)

All on C, over the 50 figures unless the held half is named; **a bar is met only if the judge is
accepted under 3.6**:

1. Long pass rate at least 0.85 and its Wilson lower bound at least 0.70 (43 of 50 passes; 42 of
   50 gives a lower bound of 0.715 but a rate of 0.84, which misses).
2. Invented values at most 0.05 (at most 2 of 50). The upper Wilson bound is reported: 2 of 50
   still allows 0.135.
3. Judge table values OK at least 0.90 over charts with a table, n reported (the set has
   at most 14).
4. On the held half alone (25), a pass of 22 of 25 gives a lower bound of exactly 0.70: the
   bar can be met there only at 22 or more, and it is reported with that stated. If A1 is
   the final arm, this is the only half that counts for it.

Missing any bar is a finding, not a reason to re-run: the report says which one, by stratum.

### 3.5 Budget

The 2026-10-09 run on 13 figures cost $0.124 with its judge ($0.0095 a figure), so about $0.50
for 50; $0.70 is allowed per run. Planned: A0, A1, C and two re-judgings, about $3. **Hard cap $8 for everything**, `--max-usd
1.50` on each run, and the report keeps a running total. No other paid call: no probe runs, no
extra arms. Reaching the cap stops the work and is reported.

### 3.6 Judge calibration (human labels)

The long judge has never been compared with a person. Before a bar is claimed:

1. After A0, draw **25 items** from A0's outputs by a seeded order: up to 10 judge fails first
   (all of them when there are 10 or fewer), then judge passes, with the strata as even as the
   draw allows. Oversampling fails is deliberate (a sample of all passes cannot show the judge
   missing an error); the report gives sensitivity and specificity next to kappa, because kappa
   depends on how many fails the sample holds.
2. A person labels them **blind**: per item they see the image, the long description and the
   table (no judge verdict, no arm, no reference text) and answers three questions: accurate,
   complete, invented values (yes or no). About 90 seconds each, 40 minutes in all.
3. Report, per field and for pass/fail: raw agreement, Cohen's kappa with the seeded bootstrap
   interval (`eval calibrate`, [eval.md](eval.md#judge-calibration-eval-calibrate)), and the
   confusion matrix. 25 items give a wide interval; it is printed.
4. **Accepted** when kappa for pass/fail is at least 0.6 and the judge missed no more than a
   quarter of the fails the person found. Otherwise revise the judge prompt once and re-judge the same
   outputs (no new figure runs); the person re-labels only the items the revision changed. If kappa
   is still under 0.6, the numbers are published as "judge not agreed with a person" and no bar is
   claimed.
5. The judge is the run's main model unless the registration names another; the report records
   it. A judge from the same model family as the writer can favour its own wording; the human
   labels are the check on that.

After the confirmatory run C, a short spot check reads what the judge decided on that run:
`eval figures-spotcheck` ([eval.md](eval.md#figure-spot-check-eval-figures-spotcheck)) writes a
blind sheet of every judge fail and 10 sampled passes, offline and free, and reports how many fails
the person confirms and how many of the passes they would fail, each with n and an interval. It
adds no bar and does not replace the kappa calibration above.

### 3.7 Not claimed

- No claim that a long description is *useful to a student* beyond the judge and the 25 human labels
  (no user study).
- No claim about figures unlike these strata (screenshots, tables as images, handwriting).
- The references are alt text that varies in quality (section 2); a low pass rate may be the
  judge or the reference, which the labels separate only on the 25 sampled items.
- Retired editions: if they are ruled out (decision 0001, question 2), 45 of the 50 figures
  go and the set must be redrawn before any run.

### 3.8 Open for sign-off (owner decisions)

1. May retired CC BY OpenStax editions serve as references (45 of the 50)?
2. The bars in 3.4, and the cell-accuracy measure being reported without a bar (n = 4 charts).
3. The 4 alt-text tables: a check of them against the images (a few minutes).
4. Provider and model for the arms and the judge (3.2); the cap in 3.5.
5. Whether to spend a round on a larger arXiv or repository scan first, to get more charts with
   an exact table.

Decisions on the points above are recorded here, with a date and the hash of this file, before any run.
