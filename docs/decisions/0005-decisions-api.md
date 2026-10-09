# ADR 0005: The OpenAI Decisions API as a second automated check

| Field | Value |
|---|---|
| Status | **Accepted** (Harish, 2026-10-08) |
| Date | 2026-10-08 |
| Decider | Harish |

## Context

The pipeline's own confidence does not tell a right proposal from a wrong one: every
real figure was accepted at 0.95 to 0.99, and the equation model's confidence ranked
errors at AUROC 0.35. The deterministic checks (`check_alt`, the text-layer symbol and
style checks) catch structure and style, not a wrong number in an alt text or a wrong
sign in an equation.

The OpenAI Decisions API (`POST /v1/decisions`, public beta since 2026-10-06) answers
fixed questions about an input with probabilities instead of text. Measured on
2026-10-08 (931 API probes, then three task studies; research notes outside the repo):
gpt-6-luna only; $0.10 per 1M input tokens and no output charge; about 0.14 s per
request; deterministic (the same request gets the same answers); probabilities that rank
errors well but are not calibrated; choice answers that favour the first value listed
(+0.11 on average); undocumented limits of 200 questions and 10 score levels; image
`detail` defaulting to "auto", which costs about 4.8x "high" on large images; and the
request parameters `store`, `prompt_cache_key`, `temperature`, `seed`, `reasoning` and
`service_tier` refused.

## Decision

Use it only as a second automated check on proposals the pipeline already made, never to
generate anything and never in place of the deterministic checks:

1. **Figures: an optional note, behind a setting** (`DECISIONS_FIGURE_VERIFY`, on by
   default). For an accepted, non-decorative figure, two predicates on the image, the
   caption and the alt text: P(ok) and P(factual error). When min(P(ok), 1 - P(factual
   error)) is below 0.5 (pre-registered, `DECISIONS_FIGURE_FLAG_BELOW`) the figure stays
   accepted and gets the optional note "A second automated check found a possible
   mismatch between the alt text and the image; worth a look". Never a required decision
   in this version.
2. **Equations: shadow mode only** (`DECISIONS_EQUATION_SHADOW`, on). One choice in a
   fixed order (match, cosmetic, meaning_changing) on the original crop and the
   proposal's LaTeX as text (no renderer), for the final proposal and for the cheap
   answer before escalation. P(meaning_changing) is recorded and aggregated; it never
   changes a status, a confidence, a tier or escalation.
3. **A daily drift canary**: 20 fixed synthetic requests compared with committed answers;
   any probability that moves by more than 0.02 logs an ERROR (`ops/canary.py`).
4. **Eval metrics**: the figure flag rate and agreement with the gpt-6.1-sol judge; the
   equation shadow AUROC against exact_lenient.

Rules that come with it:

- Steps depend only on `models.verifier.Verifier` and its content-free
  `VerifierVerdict(probabilities, reason_code)`, so the provider can be replaced.
- Every request reserves spend first through the job's `JobBudget`; `detail="high"`
  always; none of the refused parameters are sent.
- The checks share the job's dollar caps and the daily cap, not its model-call cap: a
  request reserves dollars only (`JobBudget.reserve(..., count_call=False)`; in the web
  worker the hold on the job row adds nothing to `jobs.model_calls`). The call caps
  (`MAX_MODEL_CALLS_PER_JOB` 400, `MAX_MODEL_CALLS_PER_PAGE` 80) were sized for the
  steps' calls, and the checks about double a job's requests (figures eval: 26 calls
  and 13 checks; equations eval: 36 and 36), so counting them would make an
  equation-heavy document that fits today fail its last elements with
  `budget_job_calls` (reproduced offline: 14 problem-set pages, 62 elements failed), and
  the equation shadow would change other elements' status. In dollars they add 2-4% of
  a job's spend (about $0.0001 per PDF page, against `MAX_USD_PER_PAGE` $0.30), so only
  a document that ends within that margin of its dollar cap could have its last element
  refused because of them.
- Fail-open: an error, a timeout, a refusal or no budget left leaves the fix exactly as
  it would be without the check; the outcome code is recorded.
- Wording: "a second automated check found a possible mismatch" or "found no
  contradiction"; never "verified" or "checked for accuracy".
- Not used for headings, routing, detection filtering, upload screening or as the eval
  judge (the research found no fit, or evidence against).

Where it lives: `models/decisions_client.py`, `models/verifier.py`, `models/pricing.py`
(`DECISIONS_PRICES`), `pipeline/figure_step.py` (`second_check`),
`pipeline/equation_shadow.py`, `core/types.py` (`Verification`, `Fix.verifications`),
`ops/aggregate.py`, `ops/report.py`, `ops/canary.py`, `eval/verifier_metrics.py`. Details:
docs/steps.md "Second automated check", docs/eval.md, docs/operations.md.

## Measured on this branch (2026-10-08)

| Run | Result | Added cost |
|---|---|---|
| figures eval (13 figures, all accepted) | 13 answered, 1 flagged (figure 2.9, a dense vector diagram the judge passed); the judge's one failure (2.13) scored 0.55, second lowest; AUROC of the score against judge failures 0.92 | $0.00008 per figure (2% of describing it) |
| equations eval (30) | all answered; AUROC against "not exact_lenient" 0.45 (no real meaning-level error in the sample; the 7 wrong ones are scorer gaps, a bold subscript and a comma); every equation with a bold vector scored P(meaning_changing) 0.20-0.59, every plain one 0.01-0.16 | $0.00004 per equation |
| document eval (43 PDF pages) | 71 answers, none failed; 2 figure notes (optional notes 23 -> 25; required decisions unchanged at 9) | $0.00010 per PDF page (4% of the run's $0.0026) |
| canary | 20 of 20 answers identical on a second run the same day | $0.00075 per run |

## Consequences

TODO(Harish)
