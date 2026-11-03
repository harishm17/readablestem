# A calibrated flag policy for equations, replayed on recorded runs (2026-11-03)

`FLAG_POLICY` (docs/steps.md, "Flag policy") asks for a decision on an accepted equation that looks
like the readings that turned out wrong. This page says how it was fitted and what it does, measured on
recorded runs only: no model call, no PDF, $0. The setting is off.

## Method

- **Data.** `src/readablestem/eval/flag_features.py` reads the items of recorded runs (the registered
  "cells" reading of `scripts/realworld_report.py`: table maths cells count as equations) and makes a row
  per aligned equation with the features of `pipeline/flag_policy.py`, computed from the recorded pair by
  the same function the step calls live. `uv run python -m readablestem.eval.flag_features dump OUT.jsonl`
  writes the rows (features, no equation text); `fit` fits; `report` prints the tables below.
- **Fit on development only.** Two splits: `dev` (25 arXiv papers, the line-context run `dev/line1`, 2,350
  gold) and `dev-v2` (28 OpenStax, authored and chemistry documents, the flags-on run of 2026-10-26,
  1,809 gold). The weights come from the 3,285 accepted equations that were not a decision (497 wrong). The
  penalty (L2) was chosen among 0.3 to 30 by out-of-fold log loss over papers (5 folds, a paper never split);
  it is 10. The threshold was chosen on `dev` alone, scored out of fold. Nothing was chosen on any other split.
- **Validation.** `fresh2`, `fresh3`, `fresh4` (the recorded `after` runs, 15 papers each) are scored with
  the shipped constants as they are.
- **Label.** "Wrong" is `not exact_lenient`, the registered score. On dev-v2 that scorer calls a tree that
  differs from the gold's wrong while speech and symbols agree (410 of its 649 silent "wrong" have the
  symbols right), so the fit label of that split alone is wrong under both `exact_lenient` and
  `exact_symbols`. Its reported numbers below use the registered one.
- **The rule.** The policy only adds decisions. Threshold: the fewest added decisions with silent wrong at most
  4 per 100 gold at no more than 4 decisions a page; when the budget cannot buy that, the most decisions the
  budget allows. Equal probabilities are flagged together or not at all.
- **Intervals** are 95% cluster bootstraps over papers (`proof.stats`, 10,000 resamples). "Decisions per
  page" counts every required decision of the document (equations, figures, headings), plus the added ones.
  Flag recall and precision are the registered ones: recall is the share of the aligned equations whose
  symbols are wrong (`exact_symbols` false) that are a decision, precision the share of the decisions on
  aligned equations that are such.

## The policy

`flag-policy-1`: bias -2.78, threshold 0.4541, 22 features. Strongest weights: no text layer +1.41, upright
text (`\mathrm`, `\text`, `\operatorname`) +1.39, size (log of the symbol count) +0.76, big operators +0.66,
scripts +0.65, delimiters +0.57, bold +0.54, calligraphic +0.35; a single symbol -0.58, fractions -0.29,
accents -0.25. A second reading that changed the cheap answer +0.20; every other weight is under 0.11 in size.
Out-of-fold AUROC on the fit rows 0.727 (dev alone 0.763); on the validation splits 0.742 (fresh2), 0.768
(fresh3), 0.732 (fresh4), 0.747 pooled (1,117, 1,254, 1,044 and 3,415 accepted equations not asked about).

## Result (shipped constants; before, after)

Silent wrong is the wrong aligned equations with no required decision, per 100 gold.

| split (gold, papers, pages) | role | flag recall | flag precision | silent wrong per 100 | decisions per page | added (wrong) |
|---|---|---|---|---|---|---|
| dev (2,350, 25, 114) | fit | 0.28 [0.19, 0.38] to 0.33 [0.23, 0.44] | 0.32 [0.20, 0.47] to 0.33 [0.20, 0.47] | 10.13 [7.84, 12.66] to 9.32 [7.12, 11.74] | 3.68 [2.50, 5.05] to 3.98 [2.73, 5.47] | 35 (19) |
| dev-v2 (1,809, 28, 338) | fit | 0.23 [0.10, 0.45] to 0.48 [0.32, 0.62] | 0.44 [0.20, 0.70] to 0.49 [0.24, 0.70] | 37.15 [25.95, 49.55] to 29.35 [20.17, 38.42] | 1.27 [0.74, 2.03] to 1.72 [1.10, 2.60] | 153 (141) |
| fresh2 (1,639, 15, 70) | validation | 0.42 [0.31, 0.55] to 0.47 [0.34, 0.61] | 0.45 [0.36, 0.54] to 0.45 [0.36, 0.52] | 8.79 [6.16, 11.09] to 8.05 [5.77, 10.07] | 4.29 [2.73, 6.17] to 4.57 [2.81, 6.75] | 20 (12) |
| fresh3 (1,779, 15, 73) | validation | 0.25 [0.15, 0.38] to 0.38 [0.26, 0.52] | 0.35 [0.20, 0.61] to 0.41 [0.23, 0.64] | 10.29 [5.91, 15.31] to 8.43 [4.82, 12.80] | 3.64 [2.28, 5.32] to 4.29 [2.83, 6.14] | 47 (33) |
| fresh4 (1,600, 15, 71) | validation | 0.17 [0.10, 0.28] to 0.24 [0.11, 0.40] | 0.27 [0.11, 0.43] to 0.32 [0.11, 0.52] | 7.25 [3.93, 11.91] to 6.75 [3.36, 11.27] | 2.73 [1.94, 3.76] to 2.90 [2.04, 4.04] | 12 (8) |
| fresh2-4 pooled (5,018, 45, 214) | validation | 0.29 [0.22, 0.37] to 0.38 [0.29, 0.47] | 0.38 [0.28, 0.48] to 0.41 [0.30, 0.51] | 8.83 [6.62, 11.26] to 7.77 [5.79, 9.97] | 3.55 [2.74, 4.46] to 3.92 [3.03, 4.95] | 79 (53) |

What this says.

- **The policy does what it was fitted for and no more.** On the three validation splits together it adds 79
  decisions, 53 of them on a wrong equation (67%, against 13.0% wrong among the 3,415 it chose from), and
  takes silent wrong from 8.83 to 7.77 per 100 gold (-1.06) at +0.37 decisions a page. The gain is the same
  direction on every validation split (-0.74, -1.86, -0.50), and the fit splits show it too. The intervals
  overlap before and after: these are the same papers, so the paired change is more certain than two
  separate intervals, but with 45 papers it is a small effect, not a settled one.
- **The goal is not reachable with these features.** Silent wrong 4 per 100 on dev is 94 silent errors of the
  238 it has, so the policy would have to find 60% of them, and dev already spends 3.68 decisions a page. The
  4-a-page budget leaves 37 added decisions (0.32 a page), which buy 10.13 to about 9.3. Ignoring the
  budget it takes 501 added decisions (4.4 a page, 8.07 in all) on dev, most of them on equations that are
  right.
- **Flag recall stays low.** 0.29 to 0.38 pooled on fresh2-4 (0.17 to 0.24 on fresh4,
  the line-context set).
- **The added decisions are worth more than the late ones.** Precision of the added decisions by the number
  of added decisions per page (most likely wrong first; shipped weights; n per row in the "added" column):

| added per page | dev: added, silent per 100, precision | fresh2-4: added, silent per 100, precision |
|---|---|---|
| 0.1 | 11, 9.91, 0.45 | 21, 8.49, 0.81 |
| 0.25 | 28, 9.45, 0.57 | 53, 8.07, 0.72 |
| 0.5 | 57, 8.64, 0.61 | 104, 7.51, 0.63 |
| 1.0 | 114, 7.28, 0.59 | 214, 6.10, 0.64 |
| 2.0 | 224, 5.62, 0.47 | 421, 5.06, 0.45 |

  Each added decision is one a person must look at, and about half to two thirds of them are a wrong equation up
  to 1 a page; past that the share falls under half. Whether that is worth the load is the owner's call.
  Spending 2 added decisions a page would bring silent wrong to 5 to 6 per 100, still over 4.

## What a $0 policy cannot see

- **No confidence number.** The recorded items keep the review tier and its reasons, not the model's
  confidence. The nearest recorded things (`below_accept`, `low_confidence`) occur only on equations that were
  already a decision, so they say nothing about the ones the policy may add. The cheap/main disagreement is
  `escalation` (changed, same, skipped); the glyph style check and the extent are in the features only
  through the symbol bags (`ext_extra`, `ext_missing`): the runs used here were made without the glyph
  checks (docs/eval-equation-glyph-checks.md), so no recorded `glyph_style_mismatch` flag is among them. The
  items could carry the confidence from the next recording.
- **Disagreement with an independent page reading is a stronger signal, and the product has none at run
  time.** Where the recorded Opus chatbot (the whole PDF in, LaTeX out) read the page, its reading disagreed
  with ours (canonical forms, `math_canonical`, notation level) on 523 of the 3,270 accepted equations we did
  not ask about (fresh2-4); 256 of those 523 are wrong (49%), which is 256 of the 404 silent errors the chatbot
  also read (63%, the D2 figure of docs/eval-equation-diagnostics.md). On `dev` the chatbot ran on 6 papers
  (418 equations; 15 disagreements, 6 wrong), too few to say anything. That feature is in the dump
  (`chat_agree`) and not in the policy: it needs a second reading at run time, which is what the page
  witness (`FEATURE_PAGE_WITNESS`) is for. Its weight can be fitted once a dev run has recorded witness
  readings.
- **Line context.** The `dev` and `fresh4` runs asked with the line context (`EQUATION_LINE_CONTEXT`); the
  `fresh2` and `fresh3` runs predate it (their `after` run is the registered one of its day). The policy
  carries no feature for it, and the validation numbers mix the two: fresh4 (line context on) gains
  least (-0.50), the two older runs more (-0.74, -1.86), with intervals that overlap.
- **The same papers.** fresh2 to fresh4 are validation for this fit, but they are not new to the project: they
  were used for earlier rounds (docs/eval-equation-round3.md). Nothing in this fit was chosen on them. A new
  set (fresh5) is the test of whether the gain survives.
- **Decisions on pages that already ask a lot.** fresh2 asks 4.29 decisions a page before the policy; the
  policy has no per-page cap, so the page budget is a property of the threshold on dev, not a guarantee for
  every document.

## Reproduce

```
uv run python -m readablestem.eval.flag_features fit --json fit.json
uv run python -m readablestem.eval.flag_features report --json report.json
uv run python -m readablestem.eval.flag_features dump rows.jsonl
```

Needs the cached runs (`~/.cache/readablestem/realworld/runs`, `~/.cache/a11y-stem/round4/on.jsonl`), which are
not in the repository. `report` says whether the constants in `flag_policy.py` equal the fit it just made.
`scripts/realworld_run.py rs --flag-policy` runs the pipeline with the setting on (a live run: replays
only through recorded answers). Tests: `tests/test_flag_policy.py` (features, add-only, the step, the fit on
a fixture).
