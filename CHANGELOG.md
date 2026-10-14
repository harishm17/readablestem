# Changelog

## Rename to ReadableSTEM (2026-10-14)

The product is now called ReadableSTEM (readablestem.com). It was called a11y-stem. No
pipeline, prompt, model or scoring change: every recorded model answer, eval baseline,
proof bundle and gold file is untouched.

- Python package `readablestem` (was `a11y_stem`); command `readablestem`. The
  `a11y-stem` command stays as an alias, and `import a11y_stem` still works (an alias of the
  same modules), because the registered proof files quote and import the old names.
- Page titles, headers, footer, emails, help, privacy, terms and limits pages, the student
  file's "About this version" note and the `generator` meta tag say ReadableSTEM. The class
  on the tool's own words in the HTML is `readablestem` (it was `a11y-stem`; the
  invented-content reader and the HTML checks accept both).
- Session cookie `readablestem_session` (`__Host-readablestem_session` in production): the
  one-time effect is that everyone is signed out. The review screen's saved drafts and
  shortcuts (browser localStorage) start over.
- Cache folders `~/.cache/readablestem/{heldout,dev-v2,models}` and `readablestem-proof`. A
  folder under the old `a11y-stem` name is still read while it is the only one; rename the
  folder to switch.
- Default SQLite file `.local/readablestem.db` (was `.local/a11y-stem.db`; rename the file,
  or set `DATABASE_URL`).
- Deploy defaults: Cloud Run service, Artifact Registry repository `readablestem`, Cloud SQL
  instance `readablestem-db`, log-based metrics and alert policies `readablestem-*`. If you
  provisioned under the old names, set `SERVICE`, `AR_REPO` and `SQL_INSTANCE` to them.
- URLs: `BASE_URL` is `https://readablestem.com`; the Google redirect URI is
  `https://readablestem.com/auth/callback`.
- Left alone on purpose: the GitHub repository URL (a11y-stem until the repository is
  renamed), the registered proof files and `PROOF.md` ("a11y-stem" is the name the run
  was made under), the page prints the evaluation set is made from (their footer names
  a11y-stem; the page images are part of the recorded answers' request keys), and sampling
  seeds, fingerprint keys and `generated_by` lines of gold data.

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
