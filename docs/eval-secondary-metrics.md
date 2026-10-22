# Secondary equation metrics, and what they say about the recorded held-out run

Written 2026-10-21. No model was called: every number below is computed from the recorded
answers of the held-out run (`proof/bundles/heldout-2026-10`, pipeline 0.4.0, recorded
2026-10-14), read for reporting only. Nothing here was used to change a rule, a prompt or a
threshold, and the pre-registered primary number, `exact_lenient`, is unchanged and still the
headline (PROOF.md, `proof/PREREGISTRATION.md`; neither is edited).

## The metrics

`eval pipeline` (equations and document tasks) reports three equation scores. The first is the
primary one; the other two are secondary, labelled as such in the report, and never replace it.

| Scorer | Where | What it forgives (beyond the primary scorer) |
|---|---|---|
| `exact_lenient` (primary) | `eval/equations.py` | `mi`/`mn`/`mtext` as one token type, trailing sentence punctuation |
| stream v2 (secondary) | `eval/equations_v2.py`, `secondary_stream_v2` | every token split into atoms, so a signed number (`-5` in one `<mn>`, or `-` and `5`), operators and units packed into `<mtext>`, and `mo`/`mtext`/`mi`/`mn` are one thing; look-alike dashes, mu, primes, degree signs and decimal points; split and merged equations credited (`credit_split`, `credit_merged`) |
| symbols (secondary, new) | `eval/equations_symbols.py`, `secondary_symbols` | all of the above, plus: tables flattened (row-major stream of the non-empty cells, so spacer columns and one-cell-per-row systems equal the same lines in an array); a script is a marker not a container (`[H3O+]_0` with the subscript on the whole bracket equals the subscript on the closing bracket); under and over are scripts; a fraction with no bar (`\binom`) is a stack, as the answer key's brackets around a one-column table; bars and angle brackets with other code points |
| chem (secondary, chemistry gold only) | `eval/equations_chem.py`, `exact_chem` and `pred_mathml` in the document task's pairs | the symbols stream, plus: upright, italic and bold one letter (`mtext` `H`, `<mi mathvariant="normal">H</mi>`, `H`), the forward arrows one arrow and the equilibrium arrows one arrow, box-drawing bars as bars, `∆` as delta, a number the gold cut in two (`0.2` `57`) or written `96{,}485` as one number, a bracket at the very end or start that nothing in the equation matches (the answer key took the sentence's parenthesis). Never applied to non-chemistry gold; the primary is unchanged (docs/chemistry.md, "Accuracy") |

What the new scorer keeps wrong: a dropped or added sign, a wrong digit or letter, a different
order, `a/b` against `a b`, `x^{ab}` against `x^a b`, a subscript against a superscript, a
root over one factor against over two. The price of flattening tables is stated in the
module: a 2x3 and a 3x2 matrix with the same entries in row-major order are equal here, which
is why it is a secondary measure and why a determinant's brackets and rows are not checked.

(c) in the request, the mo/mtext/mi/mn unification and signed-number packing, was already
what stream v2 does (every token is split into atoms and the token type is not in the
stream), so no new rule was needed; the new tests state it for the concrete cases
(`-5`, `time =`, `[H3O+]`, `\text{OH}^-`).

(d) split and merge credit existed (`credit_split`, `credit_merged`); it is now also
reported for the symbols scorer (`symbols_credited`), and a split or merge is still a layout
error in `split_gold_equations` and the detection numbers.

Per aligned pair the item rows carry `exact_symbols` and `symbols_similarity`, per document
`symbols_credited`, and the reports `metrics.secondary_symbols`.

## The recorded held-out run, rescored

Document task, 14 sections with equations (15 registered; one has none), 1,369 gold equations,
mean of the two recorded runs per arm. Reproduce (no key, no network, no model):

```
git worktree add /tmp/heldout-tag proof-heldout-2026-10
uv run python scripts/heldout_replay_dump.py /tmp/heldout-tag /tmp/dump      # in the tag's environment
uv run python scripts/heldout_secondary_scores.py /tmp/dump --out scores.json
```

`heldout_secondary_scores.py` stops unless its reconstruction of ReadableSTEM's two runs gives
the recorded primary 0.5004 and 0.5069.

| End to end, right / gold | ReadableSTEM | Chatbot, Opus | Chatbot, Haiku |
|---|---|---|---|
| primary `exact_lenient` | 0.504 | 0.622 | 0.600 |
| stream v2 | 0.630 | 0.776 | 0.736 |
| stream v2, split and merged credited | 0.646 | 0.778 | 0.740 |
| symbols | 0.695 | 0.833 | 0.788 |
| symbols, split and merged credited | **0.722** | **0.835** | **0.801** |
| matched pairs exact, primary / symbols | 0.567 / 0.781 | 0.701 / 0.939 | 0.680 / 0.894 |
| detection recall / precision | 0.889 / 0.743 | 0.887 / 0.561 | 0.881 / 0.532 |

Credited split / merged (symbols): ReadableSTEM 16 / 22 per run, Opus 2 / 0, Haiku 17.5 / 0.

Intervals for the differences (cluster bootstrap over the 14 sections, 10,000 resamples,
ratio estimator, seed 7; approximate and narrow with so few clusters, as in PROOF.md):

| ReadableSTEM minus Opus chatbot | Difference | 95% interval |
|---|---|---|
| primary | -0.118 | -0.180 to -0.050 |
| stream v2 with credit | -0.131 | -0.203 to -0.032 |
| symbols with credit | -0.112 | -0.197 to +0.023 |

## What this says

- The scorer artefacts are real and large (+0.22 for ReadableSTEM, +0.21 for the Opus
  chatbot between primary and symbols-with-credit) but they are almost the same size for both,
  so the scorer does not explain the gap. The gap between ReadableSTEM and the Opus chatbot is
  -0.118 under the primary scorer and -0.112 under the loosest one. Its interval now includes
  0, which the primary one did not, so the gap is not established under this scorer, but the
  point estimate has not moved.
- The analysis of 2026-10-20 reported 0.708 for ReadableSTEM and 0.840 for the Opus chatbot
  with a looser comparator (it also ignored all grouping and the gold's trailing `?`, `:`
  and `=`). The scorer here is a rule written down and tested, so it is a little stricter:
  0.722 and 0.835.
- Under the symbols scorer ReadableSTEM's pairs that were aligned are still right less often
  (0.781, against 0.939 for the Opus chatbot). That is not recognition alone: a pair counts
  as aligned when the text matches by half, so a mis-cut span is in it.
- The chatbots write many equations the answer key has no partner for (precision 0.56 and
  0.53 against 0.74); see docs/eval-invented.md.
