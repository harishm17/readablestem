# Equations on the fifth fresh split (fresh5): registered result

Run 2026-11-04, once, under `data/realworld/PREREGISTRATION-FRESH5.md` (signed 2026-11-04, hashed in
`data/realworld/REGISTRATION-fresh5.sha256`, merged to `main` in PR #149 before the first request). Every number
below comes from `scripts/fresh5_report.py` over the recorded answers; the machine-readable copy is
`data/realworld/results-fresh5.json`. Intervals are cluster bootstraps over units (10,000 resamples, seed 0);
paired differences carry the registered 97.5% interval (k = 2) and the sign-flip p.

**Verdict, as registered (section 5): the claim "ReadableSTEM beats the chatbot baseline on its equation
evaluation" is not made.** On the primary score the candidate and the Opus chatbot show **no difference this set
can detect** (candidate higher by point estimate, +0.4 points). Criteria 2 (silent errors) and 3 (flag recall)
fail; criteria 4 (unseen loss) and 5 (cost) are met. Stratum D is **not a success** under its own rule.

## What ran

| Arm | What | Units answered |
|---|---|---|
| Candidate | ReadableSTEM at `949cb1a` (pipeline 0.5.9, `src` tree `6a22753`), production settings: glyph checks, flag policy, glyph reconcile, line context, tables, notation memory on; Opus 5.5 medium, Haiku 5.5 low | 29 of 29 |
| Control | the same code with glyph checks, flag policy and glyph reconcile off | 29 of 29 |
| Chatbot, Opus | Purdue sentence, verbatim, on `claude-opus-5-5` | 29 of 29 |
| Chatbot, Opus, strong prompt | the `expert` prompt on `claude-opus-5-5` | 17 of 29 (stopped at its $9 cap) |
| Chatbot, Haiku | Purdue sentence on `claude-haiku-5-5` | 29 of 29 |

Units: strata A (12 arXiv papers, 52 pages), B (6 OpenStax sections, 43 pages), C (8 course LaTeX documents,
3 of them Beamer decks, 36 pages); 26 units, 131 pages, 1,715 gold equations in the primary. Stratum D (3 OpenStax
chemistry sections, 24 pages, 123 gold equations) is reported apart. The Beamer slide gates were relaxed while the
registration was a draft (100 characters and 3 equations on a slide instead of 300 and 5), and the owner kept that
rule at signing.

## Primary and secondaries, strata A to C pooled

| metric (95% interval) | Candidate | Control | Chatbot, Opus | Opus, strong prompt (17 units) | Chatbot, Haiku |
|---|---|---|---|---|---|
| units / gold / pages | 26 / 1,715 / 131 | 26 / 1,715 / 131 | 26 / 1,715 / 131 | 17 / 1,149 / 81 | 26 / 1,715 / 131 |
| **spoken-equal, end to end (primary)** | **0.840** [0.791, 0.876] | 0.838 [0.793, 0.874] | 0.836 [0.778, 0.878] | 0.817 [0.732, 0.869] | 0.816 [0.750, 0.860] |
| exact_lenient (secondary) | 0.793 [0.736, 0.834] | 0.783 [0.726, 0.824] | 0.770 [0.704, 0.824] | 0.674 [0.561, 0.742] | 0.732 [0.657, 0.793] |
| silent errors per 100 gold | 5.02 [3.55, 6.39] | 6.59 [4.11, 9.68] | 9.56 [5.86, 14.55] | 14.27 [8.89, 21.18] | 11.90 [8.36, 16.07] |
| flag recall | 0.461 [0.309, 0.613] | 0.330 [0.186, 0.506] | 0 (asks nothing) | 0 | 0 |
| required decisions per page | 2.87 [1.74, 4.57] | 2.50 [1.37, 4.26] | 0 | 0 | 0 |
| unseen loss per 100 gold | 11.02 [8.34, 14.22] | 12.60 [9.35, 16.50] | 16.44 [12.20, 22.20] | 18.28 [13.10, 26.78] | 18.43 [13.96, 25.02] |
| cost per page (USD) | 0.0222 | 0.0219 | 0.0681 | 0.0980 | 0.0017 |
| detection recall | 0.940 | 0.940 | 0.931 | 0.960 | 0.935 |
| symbols, credited | 0.840 | 0.829 | 0.827 | 0.782 | 0.776 |

Silent errors are aligned equations wrong under the primary that were not a required decision; unseen loss adds
the undetected gold. Cost per page over all 29 units: candidate $0.0209, control $0.0206, Opus chatbot $0.0679,
strong prompt $0.0980 (17 units), Haiku $0.0017.

## Paired differences (candidate minus each arm, units both answered)

| minus | metric | units | estimate | 97.5% interval | sign-flip p | by stratum A / B / C |
|---|---|---|---|---|---|---|
| Opus chatbot | primary | 26 | +0.004 | [-0.023, +0.036] | 0.44 | -0.012 / +0.037 / +0.016 |
| Opus chatbot | exact_lenient | 26 | +0.023 | [-0.016, +0.068] | 0.19 | -0.004 / +0.122 / +0.020 |
| Opus chatbot | silent errors per 100 | 26 | -4.55 | [-9.91, -0.59] | 0.006 | -3.39 / -19.26 / +1.20 |
| Opus chatbot | unseen loss per 100 | 26 | -5.42 | [-11.07, -1.17] | 0.008 | -3.29 / -18.15 / -2.59 |
| Control | primary | 26 | +0.001 | [-0.005, +0.005] | 1.00 | +0.003 / -0.007 / +0.002 |
| Control | exact_lenient | 26 | +0.010 | [-0.002, +0.032] | 0.27 | +0.019 / -0.007 / +0.004 |
| Control | silent errors per 100 | 26 | -1.57 | [-4.01, -0.07] | 0.06 | -0.32 / -8.89 / 0.00 |
| Opus, strong prompt | primary | 17 | +0.007 | [-0.038, +0.056] | 0.57 | -0.049 / +0.056 / +0.054 |
| Opus, strong prompt | silent errors per 100 | 17 | -8.96 | [-15.95, -3.49] | 0.0002 | -6.46 / -22.00 / -3.75 |
| Haiku chatbot | primary | 26 | +0.024 | [-0.024, +0.079] | 0.026 | 0.000 / +0.078 / +0.040 |
| Haiku chatbot | silent errors per 100 | 26 | -6.88 | [-11.56, -3.04] | 0.0001 | -5.09 / -15.93 / -5.38 |

Registered wording on the primary: against the Opus chatbot, the strong-prompt Opus arm, the Haiku chatbot and
the control, **no difference this set can detect** (each higher by point estimate). The set was powered for about
3 points; every primary difference is smaller than that.

## Primary by stratum (point estimates)

| stratum | Candidate | Control | Chatbot, Opus | Opus, strong prompt | Chatbot, Haiku |
|---|---|---|---|---|---|
| A, arXiv (12 units) | 0.848 | 0.845 | 0.860 | 0.878 (6) | 0.848 |
| B, OpenStax (6) | 0.704 | 0.711 | 0.667 | 0.636 (5) | 0.626 |
| C, course LaTeX (8) | 0.896 | 0.894 | 0.880 | 0.853 (6) | 0.857 |

Silent errors per 100 gold by stratum, candidate / Opus chatbot: A 4.35 / 7.74, B 9.26 / 28.52, C 3.98 / 2.79.

## Criteria (section 5)

| criterion | met | value |
|---|---|---|
| 1. primary: candidate minus Opus chatbot positive, 97.5% interval excludes 0, p < 0.025, no stratum below -2 points | no | +0.004 [-0.023, +0.036], p 0.44 |
| 2. silent errors at most 4.0 per 100, at most half the Opus chatbot's, at most control + 0.5 | no | 5.02 (Opus 9.56, control 6.59): the first part fails, the other two hold |
| 3. flag recall at least 0.60 at at most 4.0 required decisions a page | no | 0.461 at 2.87 a page |
| 4. unseen loss at most the Opus chatbot's | yes | 11.02 against 16.44 |
| 5. cost at most $0.07 a page | yes | $0.0209 a page (29 units) |

## Stratum D: chemistry (3 sections, 123 gold equations; aggregates only)

Source: OpenStax, Rice University. One section is CC BY 4.0 and two are CC BY-NC-SA 4.0, used privately: their
answer keys and model answers stay outside the repository, and only these aggregates are published.

| arm | chemically right, lone `×` left out (primary) | chemically right, all | silent harm per 100 gold (n) | spoken-equal | exact_lenient |
|---|---|---|---|---|---|
| Candidate | 0.333 (41/123) | 0.333 | 18.70 (23) | 0.187 | 0.008 |
| Control | 0.333 (41/123) | 0.333 | 24.39 (30) | 0.187 | 0.008 |
| Chatbot, Opus | 0.024 (3/123) | 0.024 | 3.25 (4) | 0.008 | 0.008 |
| Chatbot, Haiku | 0.016 (2/123) | 0.016 | 4.07 (5) | 0.008 | 0.008 |

The sections have no lone `×`, so the two "chemically right" columns are the same. The strong-prompt arm stopped
before stratum D.

D criteria: D1 (at least 0.80) **not met**, 0.333; D2 (at least 5 points above the control) **not met**, equal;
D3 (at least the Opus chatbot) met on the scorer's numbers, **but the chatbot numbers are not a reading of the
chatbots' chemistry**: both chatbots wrote their chemistry as mhchem `\ce{...}` (289 and 213 of them), and the
chatbot scorer's reader (`proof/systems.equation_item`) does not convert `\ce`, so the Opus chatbot had only 7 of the 123 gold
equations aligned at all (0 of 35 in the public section), against 61 for the candidate. No chatbot used `\ce` in strata A to C, so the primary is not touched. The scorer is
registered and is not changed on this split's account; D3 should be read as "not measured". D4 (silent harm at
most 1 per 100) **not met**, 18.7. **Stratum D is not a success.** With 123 equations in 3 sections, a rate near
0.33 has a 95% interval of about plus or minus 0.08.

## Spend

| arm | cap | spent | note |
|---|---|---|---|
| Control and candidate | $7.50 together | $3.2661 | control $3.1876, candidate's new answers $0.0785 (the rest shared) |
| Chatbot, Opus | $10, raised to $12.50 | $10.5189 | the $10 cap stopped it after the 26 units of A to C; the owner approved $12.50 to finish the registered list (below) |
| Chatbot, Opus, strong prompt | $9 | $7.9367 | stopped at its cap: 17 of 29 units; what was left ($1.06) was less than one unit's worst case |
| Chatbot, Haiku | $0.30 | $0.2596 | complete |
| **Total** | $26.80 registered, $29.30 after the raise | **$21.9813** | |

These totals include the trial runs: offline runs of both ReadableSTEM arms on one unit per folder ($0), free token
counts for the chatbots ($0), then a paid smoke test on one 4-page Beamer deck for every arm ($0.3504), whose
answers were kept and not asked again. Before fresh5 the phase had spent $13.1090 (`spend-log.jsonl` since
2026-11-03), so the phase total is $35.0903.

**Cap change during the run.** On 2026-11-04 the owner approved raising the Opus chatbot's cap from $10 to $12.50
so that it answered all 29 registered units. The $10 cap had stopped it after the 26 units of strata A to C,
before stratum D. The raise was decided before any ReadableSTEM fresh5 result was seen, and no other cap, setting,
scorer, gold or unit changed. The change is logged in `spend-log.jsonl` (`event: cap_change`). The registration
allows a raise with the owner's approval.

## What went wrong, and what it changes

- The Opus and Haiku chatbots' caps were set from a per-page estimate ($0.06) that was low. The registration said
  both would probably stop early. Haiku finished under its cap. Opus finished after the owner's raise.
- The strong-prompt arm is partial (17 units: 6 of A, 5 of B, 6 of C, none of D). Its comparison is on those 17 units.
- The first start of the control's smoke run was stopped by its own per-start budget ($0.25, too small for the
  worst-case holds). Its 9 answers were kept, and the rerun replayed them.
- Several starts ended on an `APIConnectionError` while tokens were being counted. The runner started again,
  skipped the recorded jobs, and recomputed what was left of the cap. No job was asked twice.
- Stratum D's chatbot scores are an artefact of the chatbot reader (above).

## Reproduce

```
R="uv run python scripts/realworld_run.py"
$R chat --part fresh5 --arm chatbot-haiku-purdue --max-usd 0.3          # replays: recorded jobs are skipped
$R rs --part fresh5a --tag candidate --settings fresh5-candidate --seed-answers-from control --group-cap 7.5 --max-usd 7.5 --offline
uv run python scripts/fresh5_report.py --out data/realworld/results-fresh5.json
```

The recordings are in `~/.cache/readablestem/realworld/runs/fresh5*` (stratum D's private folder never enters the
repository). The runner reads the registered settings into a clean environment (`--settings`), holds the two
ReadableSTEM arms to one cap (`--group-cap`), and runs the chatbots over the split in the registered order
(`--part fresh5`). `--dry` builds each chatbot message and counts its tokens without asking a model.

## What it does not say

This is one run per arm on 26 units (and 3 in D), so it shows no run-to-run variation. A difference under about
3 points of the primary is not claimed either way. The speech is ReadableSTEM's own (`pipeline.speech`), not a
particular screen reader's. The result says nothing about scans, handwriting, Word files or maths inside figures.
The arXiv gold follows the authors' `$` boundaries. Two of the three Beamer decks come from one repository.
