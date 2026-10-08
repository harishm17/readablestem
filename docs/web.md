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
| `ENV` | `dev`, `test` or `prod`. Production refuses to start without its limits and secrets (`Settings.check_production`) |
| `DATABASE_URL` | SQLite locally, Postgres (`postgresql+psycopg://...`) in production |
| `SESSION_SECRET` | Signs the session cookie. Required in production |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Without them the "Sign in with Google" button is hidden |
| `BASE_URL` | Builds the OAuth redirect URI, `${BASE_URL}/auth/callback` |
| `ADMIN_EMAILS` | Comma-separated; made allowed admins at every start-up |
| `DEV_LOGIN_ENABLED` | Enables `/dev-login` (only with `ENV=dev` or `test`) |
| `DAILY_SPEND_CAP_USD` | Global model spend per UTC day (`spend_days`) |
| `PER_USER_DAILY_PAGE_QUOTA` | Pages per user per UTC day (`quota_days`) |
| `MAX_UPLOAD_MB`, `MAX_PAGES_PER_JOB` | Upload limits |
| `MAX_USD_PER_JOB`, `MAX_MODEL_CALLS_PER_JOB` | Per-job budget given to the model client |
| `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | When a job is deleted (24 h, or 30 days if the user opts in) |
| `CRON_TOKEN` | Shared secret for `/internal/cron/aggregate-and-purge` |
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
| POST `/jobs/{id}/review/{element_id}` | owner | Accept, save edit or reject one fix |
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
   hang in MuPDF kills the child, not the server. Password-protected, unreadable or empty
   files and more than `MAX_PAGES_PER_JOB` pages are refused (422);
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

- **Claiming.** Oldest queued job first. Postgres: `SELECT ... FOR UPDATE SKIP LOCKED`
  then an UPDATE in the same transaction. SQLite: a conditional UPDATE
  (`WHERE status = 'queued'`) whose rowcount says who won.
- **Leases.** A claimed job gets `lease_until = now + 120 s`, extended by a heartbeat
  thread every 40 s and on each progress callback. Before each claim the worker requeues
  RUNNING jobs whose lease ran out; after 3 attempts a job is failed with
  `lease_expired` instead.
- **Running.** `converter(pdf_bytes, settings=..., client=client_factory(settings,
  JobBudget(DbSpendGuard, MAX_USD_PER_JOB, MAX_MODEL_CALLS_PER_JOB)), progress=...)`.
  The defaults are `pipeline.run.convert_pdf` and `models.make_vision_client`; tests pass
  fakes to `create_app`.
- **Storing.** `result_json` (the `DocumentResult` without `html`), `html`, crops as
  `Blob(kind="crop")`, `cost_usd`, `model_calls`, status `done`. Only if this worker still
  owns the job (same attempt, still running), so a job deleted or requeued meanwhile is
  not brought back.
- **Failure codes** (`Job.error_code`, shown on the job page with a plain explanation):

  | Code | Cause |
  |---|---|
  | `LayoutError.code` (e.g. `encrypted`, `unreadable`) | The layout step refused the PDF |
  | `budget_daily`, `budget_job_usd`, `budget_job_calls` | `BudgetExceeded` escaped the pipeline |
  | `lease_expired` | The job was interrupted 3 times |
  | `upload_missing` | The upload blob was gone |
  | `internal_error` | Anything else |

`Worker.run_once()` and `process_next_job(...)` process one job synchronously, for tests
and scripts.

## Review screen

`/jobs/{id}/review` lists every fix: those needing review first, then those not converted,
then the rest, each in document order. Each card shows the original crop, the proposed
equation as MathML (re-serialised from an allowlisted tree by `web/mathml.py`, never
pasted as a string), the speech text, an editable LaTeX textarea, alt text input (with a
"decorative" box) or heading level, the confidence and the failed check codes, with
"Save edit", "Accept" and "Reject" buttons.

A decision is one transaction: lock the job row, apply the decision (`web/review.py`),
validate edits with `pipeline.validate` (`check_latex`, `safe_latex_to_mathml`,
`check_mathml`, `check_alt`), store a `ReviewEdit`, set the fix status (`accepted`,
`edited`, `rejected`) in `result_json`, and re-render the job's HTML with
`pipeline.assemble.render_html`. A failed check re-shows the page (422) with the
reviewer's input and an error summary linked to the field; nothing is saved.

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
JavaScript a `<noscript>` meta refresh reloads the status page every 5 s until the job
finishes, and there is a "refresh the status" link. Every page has a skip link, one
`<main>`, a labelled navigation, unique titles (prefixed "Error:" when a form has errors),
labels on every control, error summaries linked to fields, visible focus rings, and
status written in words, not colour alone. `tests/test_web_browser.py` runs axe-core on
every page (zero serious or critical violations) and walks the upload-to-review flow
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
