# Changelog

## Page math pass, secondary equation scores, `\right.` (2026-10-21, branch feat/page-math)

- Page math pass (off: `FEATURE_PAGE_MATH`, [ADR 0006](docs/decisions/0006-page-math-pass.md)):
  one cheap-model call per page proposes where the equations are, quoting the PDF's text layer;
  every quote is verified against the text layer, verified spans are laid over layout's own
  decisions (layout stays the fallback), and the page's LaTeX is a second reading that the
  equation step compares with its own. Development-set results and cost:
  [docs/eval-page-math.md](docs/eval-page-math.md).
- A second labelled secondary equation score, `symbols` (tables flattened, scripts as markers),
  beside stream v2; what both say about the recorded held-out run:
  [docs/eval-secondary-metrics.md](docs/eval-secondary-metrics.md). The primary
  `exact_lenient` and every committed report are unchanged.
- `eval pipeline --sections` runs a slice of a set; `scripts/dev_buckets.py` and
  `scripts/dev_spans.py` compare runs on a development set.
- The "." of `\right.` and `\left.` is no longer taken for sentence punctuation and stripped
  from an inline equation.

## Open sign-up (2026-10-21)

- `SIGNUP_MODE=open` lets anyone with a verified Google account (or an allowed Microsoft one)
  sign in; the first sign-in makes the account. The default, `allowlist`, is unchanged. A person an
  admin blocked stays refused and is never made again. Accounts have a tier, `invited` (added by an
  admin) or `open`; the admin page shows it and can block and unblock.
- New accounts have lower daily limits: `OPEN_TIER_DAILY_PAGE_QUOTA` pages (20) and
  `OPEN_TIER_DAILY_REREADS` model reads from review (30); the service takes at most
  `MAX_NEW_ACCOUNTS_PER_DAY` (100) new accounts a day and 5 an hour per address; and their documents
  draw on `OPEN_TIER_DAILY_SPEND_CAP_USD` ($4), a pool inside the daily cap, so strangers cannot use
  up the instructors' budget. Invited people are unaffected when it is spent.
- People can delete their own account (Account page, a confirmation first). The public pages (sign-in,
  landing, privacy, terms, known limits) say what is true for the mode the server runs in, and
  `ops launch-check` checks the privacy notice says the first sign-in makes an account and how to
  delete it. Migration `a6c2e9f4b713` (additive). Details: docs/web.md, "Sign-up modes and access
  tiers".

## One heading for a doubled box title (2026-10-14)

- OpenStax prints a box's label and its title as two lines ("Teacher Support" twice), and a
  screen-reader user navigating by headings heard each twice. A heading with the same text as the
  heading directly above it, at any level, is now written as bold text (it was only the same
  level). The same text with content between the two copies stays a heading. The public demo and
  the site examples were written again from their stored results with `readablestem demo rerender`
  (no model call; it now decides the headings again too). Heading F1 on the development set goes
  from 0.934 to 0.821 because the gold counts both lines; the CI baseline was replaced on purpose
  (docs/eval.md, "Doubled box headings").

## Beta wording and repository link (2026-10-14)

- The draft wording of the privacy, terms, known-limits, landing and Canvas help pages is the beta
  wording: draft banners and `TODO(Harish)` markers are gone and `readablestem ops launch-check`
  reports no unfinished copy. Railway is named as host, daily backups are kept about 6 days (a
  deleted document can remain in a backup for up to about a week), governing law is Texas, and
  Word and PowerPoint are described only when the server can convert them. Details in
  [docs/launch.md](docs/launch.md).
- The source link (`SOURCE_URL` default), the README and the site point at
  https://github.com/harishm17/readablestem. The repository has to be public before the first
  sign-in (AGPL, launch.md B12).

## Railway deploy and layout limits (2026-10-14)

- Deploy on Railway: `railway.json` (Dockerfile build, `readablestem db upgrade` before each deploy,
  `/healthz` deploy check), `deploy/railway.cron.json` (hourly `readablestem ops cron`) and
  [docs/deploy-railway.md](docs/deploy-railway.md). The Cloud Run guide stays as the alternative.
- Layout analysis no longer takes minutes and gigabytes on pages of tens of thousands of tiny
  drawings: banded clustering (same groups), a drawing cap per page (`page_type.PAGE_MAX_PATHS`,
  `PAGE_MAX_ITEMS`; such a page is kept as its image with its text, reason `unread_page`), a limit on
  the table detector, and the error code `too_complex` for an upload whose pages need too much.
  Output is unchanged on every repository fixture and development page
  (`scripts/layout_identity.py`); details in docs/layout.md.

## Name: ReadableSTEM (2026-10-14)

The product is called ReadableSTEM (readablestem.com). No pipeline, prompt, model or scoring
change.

- Python package `readablestem`; command `readablestem`.
- Page titles, headers, footer, emails, help, privacy, terms and limits pages, the student
  file's "About this version" note and the `generator` meta tag say ReadableSTEM. The class
  on the tool's own words in the HTML is `readablestem`.
- Session cookie `readablestem_session` (`__Host-readablestem_session` in production). The
  review screen's saved drafts and shortcuts (browser localStorage) start over.
- Cache folders `~/.cache/readablestem/{heldout,dev-v2,models}` and `readablestem-proof`.
- Default SQLite file `.local/readablestem.db` (or set `DATABASE_URL`).
- Deploy defaults: Cloud Run service, Artifact Registry repository `readablestem`, Cloud SQL
  instance `readablestem-db`, log-based metrics and alert policies `readablestem-*`.
- URLs: `BASE_URL` is `https://readablestem.com`; the Google redirect URI is
  `https://readablestem.com/auth/callback`.

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
