# Changelog

## Pipeline 0.5.0 (2026-10-14)

Results and eval reports from 0.4.0 load unchanged; their numbers were made under the
older rules. The held-out recording (`proof-heldout-2026-10`) is a 0.4.0 run and stays one.

### Tier 1 (before the beta)

- Spend: reservations are sized for Claude (a one-page PDF can make an Opus call); model
  concurrency is a setting (`MODEL_CONCURRENCY`, held to what the caps can hold) and a 429
  pauses every request.
- Equations: leading exercise numbers are stripped from block-equation regions; bold Greek
  is written as Unicode math bold; an equation that could not be converted gets the PDF's
  own text as its alt text, marked not checked; a large display formula on a slide goes to
  the equation step, not to the headings.
- Escalation: the main model is not asked again when the only trigger is a confidence just
  under the bar with no failed check.
- Text and structure: hyphens in compounds at line ends are kept unless the joined word is
  a dictionary word; a heading that repeats the one above becomes bold text; exam mode for
  figure alt text.

### Tier 2 (accuracy)

- Layout: inline stacked fractions and binomials are one inline equation; braced systems,
  determinants and cases are one equation; a middle dot is an operator; half a fraction is
  held under the accept bar with a required decision; equations that are pictures are
  classified and need a decision.
- Prose: raised and lowered text is written as `sup` and `sub` (units, ions, nuclides);
  code blocks as `pre`/`code`, glossaries as definition lists, footnote marks linked,
  contents pages as lists of links, tagged-PDF headings and alt text as proposals.
- Checks: a second automated check on Claude (shadow by default, enforcement off) with a
  daily canary; number and letter-case checks against the PDF's text layer; speech
  readings for matrices, cases, nuclides and units; MathML alttext (setting, off).
- Heading triage: bold lines ending in `.`, `?` or `:` are required decisions; repeated
  labels in a group count once.
- Model cost (Claude): the untrusted-text notice rides in the cached system block
  (`ANTHROPIC_NOTICE_IN_SYSTEM`, on; the second check and the figure judge keep their own
  prompts). Off by default: lower effort for the equation and figure steps, downscaled
  figure crops, Haiku first for display equations, context only on escalation, per-model
  accept bars, Sonnet as the main model. `eval pipeline --answers` records and replays
  model answers; `--batch` uses Message Batches at half price (docs/eval.md "Model cost").
- Runtime: layout overlaps the model calls, the table detector runs only on pages that
  could hold a table, fewer writes in the spend guard and worker poll, WOFF2 maths font.
