# Web app

The hosted beta: an instructor signs in with Google, uploads a PDF, watches it convert,
reviews the uncertain fixes and downloads one accessible HTML file. One FastAPI process
serves the pages and runs the background worker (ADR 0004).

Code: `src/a11y_stem/web/`. Tests: `tests/test_web_*.py`.

## Run it locally

```
uv sync --all-groups
cp .env.example .env            # then set the values below
uv run a11y-stem users allow you@example.org --admin
uv run a11y-stem serve --reload # http://127.0.0.1:8000
```

Minimal `.env` for local work (SQLite under `.local/`, no Google, no real model calls
until WP1's client is in place):

```
ENV=dev
DEV_LOGIN_ENABLED=true
ADMIN_EMAILS=you@example.org
CONTACT_EMAIL=you@example.org
```

`/dev-login` then signs you in with any allowlisted email and no password. In dev and
test the tables are created on start-up; there is no session secret, so a random one is
made per process and a restart (or `--reload`) signs everyone out.

`a11y-stem serve` runs `uvicorn a11y_stem.web.app:create_app --factory`. Options:
`--host` (default 127.0.0.1; use 0.0.0.0 in a container), `--port` (default `$PORT` or
8000), `--reload`.

## Settings the web app uses

All live in `a11y_stem.config.Settings` (environment or `.env`).

| Setting | Used for |
|---|---|
| `ENV` | `dev`, `test` or `prod`. Production refuses to start without its limits and secrets (`Settings.check_production`, plus `web.app.check_web_secrets`, which also refuses empty secrets) |
| `DATABASE_URL` | SQLite locally, Postgres (`postgresql+psycopg://...`) in production |
| `SESSION_SECRET` | Signs the session cookie. Required in production, at least 32 characters (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Without them the "Sign in with Google" button is hidden |
| `BASE_URL` | Builds the OAuth redirect URI, `${BASE_URL}/auth/callback` |
| `ADMIN_EMAILS` | Comma-separated; made allowed admins at every start-up |
| `DEV_LOGIN_ENABLED` | Enables `/dev-login` (only with `ENV=dev` or `test`) |
| `DAILY_SPEND_CAP_USD` | Global model spend per UTC day (`spend_days`) |
| `PER_USER_DAILY_PAGE_QUOTA` | Pages per user per UTC day (`quota_days`) |
| `MAX_UPLOAD_MB`, `MAX_PAGES_PER_JOB` | Upload limits |
| `MAX_USD_PER_JOB`, `MAX_MODEL_CALLS_PER_JOB` | Per-job budget given to the model client |
| `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | When a job is deleted (24 h, or 30 days if the user opts in) |
| `CRON_TOKEN` | Shared secret for `/internal/cron/aggregate-and-purge`. Required (non-empty) in production, or the purge would never run |
| `RUN_WORKER`, `WORKER_POLL_S` | Run the worker thread in this process; queue poll interval |
| `SOURCE_URL`, `CONTACT_EMAIL` | Footer source link (AGPL notice); contact on the 403, privacy and terms pages |

## Routes

| Method and path | Who | What |
|---|---|---|
| GET `/` | anyone | What the tool does, sign-in, link to the demo |
| GET `/demo` | anyone | `web/static/demo/index.html` if present (served with the sandbox CSP), else a "being prepared" page |
| GET `/privacy`, `/terms` | anyone | Drafts describing the real behaviour, marked "Draft, to be reviewed by Harish" |
| GET `/limits` | anyone | Known limits: a `TODO(Harish)` placeholder until Harish writes it |
| GET `/login` | anyone | Sign-in page |
| POST `/login` | anyone | Redirects to Google (CSRF-checked form) |
| GET `/auth/callback` | Google | Finishes sign-in; 403 page if not allowlisted or email not verified |
| GET, POST `/dev-login` | dev/test only | Any email, no password; the allowlist still applies |
| POST `/logout` | signed in | Clears the session |
| GET `/app` | signed in | Upload form and the user's documents |
| POST `/jobs` | signed in | Upload: checks, quota, queue; 303 to `/jobs/{id}` |
| GET `/jobs/{id}` | owner | Status and progress; results summary when done |
| GET `/jobs/{id}/status?v=` | owner | Status fragment for htmx polling (204 while unchanged) |
| GET `/jobs/{id}/download` | owner | The HTML as an attachment |
| GET `/jobs/{id}/view` | owner | The HTML inline, sandboxed (see below) |
| GET `/jobs/{id}/crops/{element_id}.png` | owner | Original image of one equation or figure |
| GET `/jobs/{id}/review` | owner | Review screen |
| POST `/jobs/{id}/review/{element_id}` | owner | Accept, save edit or reject one fix; with `equations`, `figures` or `headings` in place of the id, bulk accept in that group |
| POST `/jobs/{id}/delete` | owner | Delete the job and everything made from it now |
| GET `/admin` | admins | Allowlist, today's spend against the cap, queue depth, aggregate counts |
| POST `/admin/allowlist`, `/admin/allowlist/remove` | admins | Add or remove someone |
| POST `/internal/cron/aggregate-and-purge` | Cloud Scheduler | `X-Cron-Token` header; aggregates, then purges |
| GET `/healthz` | anyone | `{"status": "ok"}` after a database ping, else 503 |

Every `/jobs/*` route answers 404 for a job that does not exist or belongs to someone
else, admins included, so job ids cannot be probed and admins never see documents.

## Sign-in, sessions and CSRF

- **Google.** Authlib's Starlette client with the discovery document
  `https://accounts.google.com/.well-known/openid-configuration` and scope
  `openid email profile`. In the Google Cloud console, add
  `${BASE_URL}/auth/callback` as an authorised redirect URI. The ID token's nonce is
  checked by Authlib; we also require `email_verified`.
- **Allowlist.** `users.is_allowed`, managed with `a11y-stem users allow|deny|list` or on
  `/admin`. It is checked on every request, so removing someone ends their access at
  once. People who are not on it see a polite 403 page with `CONTACT_EMAIL`; no row is
  created for them.
- **Sessions.** Starlette `SessionMiddleware`: a signed cookie `a11y_stem_session`,
  HttpOnly, SameSite=Lax, 7 days, Secure in production. The session is cleared and a new
  CSRF token issued at sign-in. It holds the user id, the CSRF token, one-time notices
  (never document content) and Authlib's sign-in state.
- **CSRF.** Every form that changes something carries a hidden `csrf_token`, compared in
  constant time with the session's. `tests/test_web_auth.py` lists every POST route and
  checks each refuses a forged token, so a new route cannot skip the check unnoticed.

## Uploads and limits

`POST /jobs` checks, in order:

1. the daily spend cap: if not even one cheap model call fits under
   `DAILY_SPEND_CAP_USD` today, a friendly 503 page ("Conversions are paused for today");
2. size: a declared `Content-Length` over the limit is refused before reading, and the
   body is counted while it streams in, so an endless upload is cut off just past
   `MAX_UPLOAD_MB` (413). The file stays in memory; it never touches the server's disk;
3. the CSRF token;
4. the PDF: magic bytes (`%PDF-` in the first 1024 bytes, else 415), then opened in a
   child process (`python -m a11y_stem.web.pdf_probe`) with a 30 s timeout. A crash or
   hang in MuPDF *while opening the file and counting pages* kills the child, not the
   server. Password-protected, unreadable or empty files and more than
   `MAX_PAGES_PER_JOB` pages are refused (422). The conversion itself is not isolated
   this way; see "Limits of the worker" below;
5. the page quota: one conditional `UPDATE quota_days SET pages = pages + n WHERE ... AND
   pages + n <= quota`, in the same transaction as the job insert (429 if over).

Validation errors re-show the upload page with an error summary linked to the file input.
The opt-in box "Keep my document and results for 30 days to help improve the tool" is off
by default; it sets `retain_until` to 30 days instead of 24 hours.

## Spend cap (`web/spend.py`)

`DbSpendGuard(factory, cap_usd)` implements `core.budget.SpendGuard` on `spend_days`
(one row per UTC day). A reservation is a single conditional UPDATE:

```sql
UPDATE spend_days SET held_usd = held_usd + :a
WHERE day = :d AND spent_usd + held_usd + :a <= :cap
```

No row updated means `BudgetExceeded("daily")`. The day row is created first with an
insert-ignore (`ON CONFLICT DO NOTHING`, on SQLite and Postgres). Settling moves the hold
to `spent_usd` on the day it was reserved. The Postgres test runs 20 threads against the
cap and checks it is never exceeded. If a process dies between reserve and settle, its
hold stays until the day ends: the cap errs on the side of spending less.

## Worker (`web/worker.py`)

A thread started and stopped with the app's lifespan when `RUN_WORKER=true`.

- **Claiming.** Fewest attempts first, then oldest, so a retried job goes behind fresh
  ones. Postgres: `SELECT ... FOR UPDATE SKIP LOCKED` then an UPDATE in the same
  transaction. SQLite: a conditional UPDATE (`WHERE status = 'queued'`) whose rowcount
  says who won.
- **Leases.** A claimed job gets `lease_until = now + 120 s`. The conversion runs on its
  own thread; the worker thread waits for it and extends the lease every 40 s, and each
  progress callback extends it too. Before each claim the worker requeues RUNNING jobs
  whose lease ran out; after 3 attempts a job is failed with `lease_expired` instead.
- **Time limit.** `JOB_BASE_S + JOB_PER_PAGE_S * pages` (300 s + 90 s per page, so
  50 minutes for 30 pages; constants in `web/worker.py`, not settings yet). Past it the
  worker stops extending the lease, cancels the conversion, fails the job with `timeout`
  and goes on to the next job.
- **Cancellation.** When the job stops being this worker's (the user pressed "Delete
  now", retention purged it, or another worker requeued it after a lost lease) the
  conversion is cancelled. The job budget checks ownership in the database before every
  spend reservation, so no further crop or nearby text is sent to the model; the
  heartbeat and the progress callback check it too. Cancellation raises
  `worker.JobCancelled`, a `BudgetExceeded` with scope `cancelled`, so the model client
  sends nothing and `convert_pdf` cancels its queued steps; the progress callback's
  exception aborts the run. Model calls already in flight finish. The outcome is
  "discarded", not a failure.
- **Running.** `converter(pdf_bytes, settings=..., client=client_factory(settings,
  budget), progress=...)`, where `budget` is a `JobBudget` over the daily `DbSpendGuard`
  with what is left of `MAX_USD_PER_JOB` and `MAX_MODEL_CALLS_PER_JOB` for this job. The
  caps are per job, not per attempt: spend and calls so far are written to
  `Job.cost_usd` and `Job.model_calls` on every heartbeat and progress callback, and a
  retried attempt starts from them. The defaults are `pipeline.run.convert_pdf` and
  `models.make_vision_client`; tests pass fakes to `create_app`.
- **Storing.** `result_json` (the `DocumentResult` without `html`), `html`, crops as
  `Blob(kind="crop")`, `cost_usd` and `model_calls` (all attempts), status `done`. Only
  if this worker still owns the job (same attempt, still running), so a job deleted or
  requeued meanwhile is not brought back. NUL characters (they occur in text extracted
  from PDFs, and Postgres refuses them) are removed first. If the write fails, a
  transient error (`OperationalError`) is retried once; after that the job is failed
  with `store_failed` instead of being left running, which would convert it again.
- **Failure codes** (`Job.error_code`, shown on the job page with a plain explanation):

  | Code | Cause |
  |---|---|
  | `LayoutError.code` (e.g. `encrypted`, `unreadable`) | The layout step refused the PDF |
  | `budget_daily`, `budget_job_usd`, `budget_job_calls` | `BudgetExceeded` escaped the pipeline |
  | `timeout` | The conversion ran past its time limit |
  | `store_failed` | The result could not be written to the database |
  | `lease_expired` | The job was interrupted 3 times |
  | `upload_missing` | The upload blob was gone |
  | `internal_error` | Anything else |

**Limits of the worker.** The conversion (`layout.analyze`, crops) runs MuPDF inside the
web process, not in a child process like the upload probe. A conversion stuck in native
code cannot be stopped from Python: after its time limit the job is failed and the queue
moves on, but the stuck thread stays (logged as `conversion_abandoned`) until the
process restarts. A PDF that crashes MuPDF during conversion takes the whole process
down; the job is retried after its lease expires, behind fresh jobs, and failed with
`lease_expired` after 3 attempts. Running the conversion in a killable child process is
the fix; it is not built yet.

`Worker.run_once()` and `process_next_job(...)` process one job synchronously, for tests
and scripts.

## Review screen

Code: `web/routes/review.py` (page and form handling), `web/review.py` (rules: order,
eligibility, validation), `web/messages.py` (what each code means). Tests:
`tests/test_web_review.py` (one fix), `tests/test_web_review_bulk.py` (order, landing,
bulk accept), `tests/test_web_review_messages.py` (every code has a sentence) and
`tests/test_web_review_browser.py` (axe, reflow, keyboard, no JavaScript; `browser`
marker).

**Order.** A reviewer's time should go where a mistake costs a student most, so fixes
are grouped by kind: equations, then figures, then headings. Within a group, the fixes
still waiting for a decision come first (needs review, failed and not converted alike),
in page and reading order; the ones already decided (accepted, edited, rejected) are
folded into a `<details>` below them. A table of contents at the top links to each group
and gives its counts. Every fix is numbered within its kind in reading order ("Equation
3"), so a number stays the same while the list reorders after each decision.

**One fix.** Each card shows:

- the status in words ("Needs review", "Not converted (error)"; the coloured border
  only repeats it) and the confidence as a word and a number, "Low (45%)" (High from
  85%, the default auto-accept bar; Medium from 60%);
- why it is here: each failed check and `Fix.reason` as a sentence from
  `web/messages.py`, its severity in words ("Must be fixed", "Worth checking"), and the
  raw code as a small secondary line for bug reports;
- for equations and figures, the original crop; for equations, the proposed MathML
  (re-serialised from an allowlisted tree by `web/mathml.py`, never pasted as a string)
  in a box that scrolls sideways when the equation is wide (a named tab stop, so it can
  be scrolled from the keyboard), the speech text and an editable LaTeX box; for figures,
  the proposed alt text, the caption, an alt text box with the 150-character guideline
  and the current count written out (no JavaScript), and a "decorative" box; for
  headings, the text and a level select;
- "Save edit", "Accept" (only when the proposal passed every error-level check) and
  "Reject" buttons whose accessible names include the fix ("Reject figure 2").

A decision is one transaction: lock the job row, apply the decision (`web/review.py`),
validate edits with `pipeline.validate` (`check_latex`, `safe_latex_to_mathml`,
`check_mathml`, `check_alt`), store a `ReviewEdit`, set the fix status (`accepted`,
`edited`, `rejected`) in `result_json`, and re-render the job's HTML with
`pipeline.assemble.render_html`. A failed check re-shows the page (422) with the
reviewer's input and an error summary linked to the field; nothing is saved.

After a decision the browser is redirected (303) to `#item-<id>` of the next fix still
waiting, after this one in review order and wrapping round to the first, or to
`#review-progress` when none are left. A one-time notice there links back to the fix
that changed ("Equation 1 on page 1: Accepted."), which is unfolded so the link works.
The landing item has `tabindex="-1"`, so the next Tab reaches that link.

**Bulk accept.** Each group offers to accept several proposals at once:

- "Accept every equation proposal at or above X%" (X from 90, 80, 70, 60, 50; each
  option says how many it would take), for every group;
- "Accept all N remaining headings", for headings only: a wrong heading level is cheap
  to spot and fix later, a wrong equation or alt text is not.

Only fixes a single "Accept" could take are included: status `needs_review`, a proposal,
and no failed error-level check. Failed and not-converted fixes, and fixes the reviewer
already decided, are never touched. The threshold compares the whole percentage the page
shows (0.795 is shown as 80%, so it is "at or above 80%"). Bulk accept posts to the same
route as a single decision, with the group (`equations`, `figures`, `headings`) in
place of the element id (element ids look like `p3-e12`, so the two cannot collide).
That keeps one CSRF- and ownership-checked way to change a review, and the POST-route
list in `tests/test_web_auth.py` unchanged. It is one transaction: one `ReviewEdit` per
fix, `result_json` updated, the HTML re-rendered once, then a redirect to
`#group-<kind>` with the count ("Accepted 3 headings."). Repeating it changes nothing
and re-renders nothing. A choice the page never offers (an unknown threshold, "accept
all" for equations) is refused with a 422 and an error linked to the threshold select.

**Job page.** "Need your review" is broken down by kind, each linking to its group
("7 need your review: 3 equations, 1 figure, 3 headings.").

**Messages.** `web/messages.py` maps every `(Check.name, Check.code)` pair and every
`Fix.reason` the pipeline can emit to one plain sentence that says what is wrong and
what to look at. `tests/test_web_review_messages.py` collects the codes from the
pipeline's source with `ast` (not from a hand-kept list), so a new code fails the test
until it has a sentence, and a sentence whose code no longer exists fails it too. An
unknown code still gets a neutral sentence plus the code.

**Narrow screens.** Long file names, codes and document text wrap
(`overflow-wrap: anywhere` on headings, ledes, summaries, error text and the review
cards), so neither the job page nor the review page scrolls sideways at 320 CSS px.

## Security headers

Every response (`web/security.py`):

```
Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self';
  script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none';
  form-action 'self' https://accounts.google.com
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Cache-Control: no-store            (everything except /static/)
Strict-Transport-Security: max-age=31536000; includeSubDomains   (production only)
```

Request bodies are capped at 1 MiB while they stream in (`RequestSizeLimitMiddleware`),
before any form is parsed: Starlette's multipart parser has no limit on file parts, and
CSRF can only be checked after parsing. The upload route is exempt because it enforces
its own `MAX_UPLOAD_MB` limit the same way.

The converted document (`/view`, `/download`, `/demo`) gets its own policy instead:
`sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'`. It may carry
inline styles and data: images, and the sandbox stops it running scripts, submitting
forms or reading our cookies.

The app pages have no inline scripts, styles or event handlers (a test scans the
templates). htmx is vendored at `web/static/vendor/` (version, hashes and licence in its
README) and configured to need no `eval`, inject no `<style>` and keep no history cache.

## Accessibility of the app itself

Server-rendered pages that work without JavaScript: htmx only polls the job status
(every 3 s, 204 while unchanged, a full reload when the job finishes). Without
JavaScript the status page does not reload by itself, because a reload the user did not
choose sends a screen reader back to the top each time (WCAG 2.2.1, failure F41; axe
rule `meta-refresh`, critical). It has a "refresh the status" link and, inside
`<noscript>`, an opt-in link to `/jobs/{id}?auto=1`, which adds a 15 s `<noscript>` meta
refresh until the job finishes and shows a "Stop reloading this page" link. Every page has a skip link, one
`<main>`, a labelled navigation, unique titles (prefixed "Error:" when a form has errors),
labels on every control, error summaries linked to fields, visible focus rings, and
status written in words, not colour alone. `tests/test_web_browser.py` runs axe-core on
every page (zero serious or critical violations), including an unfinished job page with
JavaScript on and as a no-JS browser parses it, and walks the upload-to-review flow
with JavaScript off.

## Privacy

- Logs carry event names, ids, counts, costs and category codes only
  (`web/logsafe.py`). Exceptions are logged as their type and code location, never their
  message, which can quote document text. `SafeErrorMiddleware` turns any unhandled
  error into a plain 500 page so the server never logs the message either. A test runs
  upload, conversion, review and failure with marker strings in every kind of content and
  checks none reach the logs.
- File names, emails and document content are not written to logs or error codes; the
  file name is shown only to its owner and deleted with the job.
- Not-allowlisted sign-ins are not stored.

## Database and migrations

Schema: `web/db.py`. Migrations: `migrations/` with `alembic.ini` at the repo root. The
URL comes from `DATABASE_URL` (or the settings, which read `.env`).

- Dev and test create tables directly on start-up. Production refuses to start unless the
  schema is at the latest migration: run `uv run a11y-stem db upgrade` (same as
  `uv run alembic upgrade head`) before deploying.
- A local SQLite database made by start-up cannot then be upgraded with Alembic (its
  tables already exist). Delete `.local/a11y-stem.db` if you need to test migrations.
- New migration: change `web/db.py`, then
  `DATABASE_URL=sqlite:////tmp/empty.db uv run alembic upgrade head` followed by
  `DATABASE_URL=sqlite:////tmp/empty.db uv run alembic revision --autogenerate -m "..."`,
  and review the file. `tests/test_web_migrations.py` and `tests/test_web_postgres.py`
  check that the migrated schema matches the models on SQLite and Postgres.

## Tests

| Command | What runs |
|---|---|
| `uv run pytest tests/test_web_*.py` | Offline web tests (SQLite) plus the Postgres ones when a server is available |
| `uv run pytest -m postgres` | Postgres only: spend and quota under 20 threads, SKIP LOCKED with two workers, migration check |
| `uv run pytest -m browser tests/test_web_browser.py` | Chromium: axe-core on every page, CSP, htmx polling, no-JS flow |

Postgres tests use `TEST_POSTGRES_URL` if set (CI service container), else start an
embedded server from the `pgserver` dev dependency (Python 3.12), else skip. Browser tests
need `uv run playwright install chromium`; axe-core is downloaded once from the npm
registry, checked against its published integrity hash and cached in `.cache/axe-core/`.

The other work packages' functions the web app calls (`pipeline.validate`,
`pipeline.speech`, `pipeline.assemble`, `ops.aggregate`, `ops.retention`) are
monkeypatched in these tests; the converter and model client are fakes passed to
`create_app`.
