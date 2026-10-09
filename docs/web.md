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

A verbatim copy of `.env.example` loads (blank values mean "not set"). Minimal `.env`
for local work (SQLite under `.local/`, no Google, and no OpenAI key: `FAKE_MODELS`
answers every model request offline with placeholder output, so the whole flow runs
for free; drop it and set `OPENAI_API_KEY` for real conversions):

```
ENV=dev
DEV_LOGIN_ENABLED=true
FAKE_MODELS=true
ADMIN_EMAILS=you@example.org
CONTACT_EMAIL=you@example.org
```

Without `FAKE_MODELS` and without a key, jobs fail with `model_not_configured` ("Conversions
are not set up on this server yet") rather than a generic error.

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
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_ALLOWED_TENANTS` | Microsoft (Entra ID) sign-in, on only with all three set; tenants are directory ids, comma-separated. Half of them set refuses to start (`check_production`, any environment) |
| `FEATURE_TABLES`, `FEATURE_LONG_DESCRIPTIONS`, `FEATURE_COLOUR`, `FEATURE_NOTATION_MEMORY` | Content features, all off by default (docs/design/content-features.md "Flags") |
| `FEEDBACK_QUESTIONS_ENABLED`, `EVENTS_ENABLED` | The two questions after a download, and content-free usage events (`events` table). Both on by default |
| `BASE_URL` | Builds the OAuth redirect URI, `${BASE_URL}/auth/callback` |
| `ADMIN_EMAILS` | Comma-separated; made allowed admins at every start-up |
| `DEV_LOGIN_ENABLED` | Enables `/dev-login` (only with `ENV=dev` or `test`) |
| `DAILY_SPEND_CAP_USD` | Global model spend per UTC day (`spend_days`) |
| `PER_USER_DAILY_PAGE_QUOTA` | Pages per user per UTC day (`quota_days`) |
| `MAX_UPLOAD_MB`, `MAX_PAGES_PER_JOB`, `MAX_CONCURRENT_UPLOADS` | Upload limits (the last: uploads taken in at once per instance, one per user) |
| `MAX_USD_PER_JOB`, `MAX_MODEL_CALLS_PER_JOB` | Per-job budget given to the model client (all attempts together). The second automated check's requests count in dollars, not as model calls |
| `MAX_USD_PER_PAGE`, `MAX_MODEL_CALLS_PER_PAGE` | Lower the per-job caps for short documents: a job may use at most this much per page of its upload (defaults $0.30 and 80), so one dense page cannot use a long document's allowance |
| `DECISIONS_FIGURE_VERIFY`, `DECISIONS_EQUATION_SHADOW`, `DECISIONS_CANARY` (and thresholds) | The second automated check (ADR 0005): an optional note on accepted figures, a shadow on equations, a daily drift canary in the cron (docs/steps.md, docs/operations.md). All on by default |
| `FAKE_MODELS` | Dev and test only: every model request is answered by the offline client (placeholder output), so the app runs end to end without an OpenAI key. Refused in production |
| `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | When a job is deleted (24 h, or 30 days if the user opts in) |
| `CRON_TOKEN` | Shared secret for `/internal/cron/aggregate-and-purge`. Required (non-empty) in production, or the purge would never run |
| `RUN_WORKER`, `WORKER_POLL_S` | Run the worker thread in this process; queue poll interval |
| `SOURCE_URL`, `CONTACT_EMAIL` | Footer source link (AGPL notice); contact on the 403, privacy and terms pages |

## Routes

| Method and path | Who | What |
|---|---|---|
| GET `/` | anyone | What the tool does, sign-in, link to the demo |
| GET `/demo` | anyone | `web/static/demo/index.html` if present (served with the sandbox CSP), else a "being prepared" page |
| GET `/privacy`, `/terms` | anyone | Drafts describing the real behaviour (hourly purge, one daily backup, Langfuse only when tracing is on), marked "Draft, to be reviewed by Harish". `a11y-stem ops launch-check` lists what is still unfinished on the public pages |
| GET `/limits` | anyone | Known limits: a `TODO(Harish)` placeholder until Harish writes it |
| GET `/status` | anyone | Service status: up, conversions open or paused for today, documents waiting and converting (numbers only; 503 when the database does not answer) |
| GET `/login` | anyone | Sign-in page |
| POST `/login` | anyone | Redirects to Google (CSRF-checked form) |
| GET `/auth/callback` | Google | Finishes sign-in; 403 page if not allowlisted or email not verified |
| GET, POST `/dev-login` | dev/test only | Any email, no password; the allowlist still applies |
| POST `/logout` | signed in | Clears the session |
| GET `/app` | signed in | Upload form and the user's documents |
| POST `/jobs` | signed in | Upload: checks, quota, queue; 303 to `/jobs/{id}` |
| GET `/jobs/{id}` | owner | The job page: status, live progress, what is left to do, the before/after card, notices, results, language (below) |
| GET `/jobs/{id}/status?v=` | owner | Status fragment for htmx polling (204 while unchanged); changes per step |
| GET `/jobs/{id}/progress?v=` | owner | The list of steps with their counts, polled on its own outside the live region (204 while unchanged) |
| GET `/jobs/{id}/draft` | owner | Asked before a download while decisions are open: what students would get, "Review N decisions first" or "Download the draft anyway"; with none open, 303 to the finish line |
| POST `/jobs/{id}/language` | owner | Confirm or change the document language (`lang`, one the control offers); lang_source becomes "user" and the HTML is made again |
| GET `/jobs/{id}/download` | owner | The HTML as an attachment |
| GET `/jobs/{id}/view` | owner | The HTML inline, sandboxed (see below) |
| GET `/jobs/{id}/crops/{element_id}.png` | owner | Original image of one equation, figure or table |
| GET `/jobs/{id}/review?item=&page=&accepted=1&cell=` | owner | Review screen: one decision at a time beside its page (below); `cell` (`r1c2`) opens that cell's form on a table |
| GET `/jobs/{id}/review/list` | owner | "All items as a list": every fix, grouped by kind, with bulk accept |
| GET `/jobs/{id}/review/marks.css` | owner | Where each mark and its frame go on its page image, and the compare sizes (element ids and numbers only) |
| GET `/jobs/{id}/pages/{n}.png` | owner | Page `n` (1-based) of the upload, rendered now at 144 dpi, never stored; 429 beyond two renders in progress for one person, 503 when the instance's two render slots stay busy |
| GET `/jobs/{id}/regions/{element_id}.png` | owner | An item with the page around it: an equation with the words on each side (its differing symbol outlined), a heading with the whole lines around it (outlined), a figure, a table (outlined, its caption above it in whole); 429 and 503 as for pages |
| POST `/jobs/{id}/review/{element_id}` | owner | Accept, save edit or reject one fix (a table's edit says what it changes: `change` is `cell`, `headers` or `use_local`); with `equations`, `figures`, `tables` or `headings` in place of the id, bulk accept in that group; with a heading group's key (`hg-p1-e7`), one decision for every heading of the group still waiting (with `scope=all`, for every heading of the group: a decided group decided again). A hidden `view` field (`split` or `list`) says which view to go back to, and `version` (the job's `result_version` the page was made from) refuses a form from a stale tab |
| GET, POST `/jobs/{id}/delete` | owner | A "Delete <file>? This cannot be undone" confirmation page; only a POST with `confirm=yes` from that page deletes the job and everything made from it (WCAG 3.3.4, works without JavaScript) |
| GET, POST `/courses` | signed in | Courses (with `FEATURE_NOTATION_MEMORY` only, else 404): the user's courses; add one ("Courses and notation" below) |
| GET `/courses/{id}`; POST `/courses/{id}/rename`, `/courses/{id}/entries` | owner | A course's notation; rename; add a symbol |
| GET, POST `/courses/{id}/entries/{entry}`; POST `.../delete` | owner | Edit or remove one symbol |
| GET, POST `/courses/{id}/delete` | owner | "Delete <course>?" confirmation; only a POST with `confirm=yes` deletes |
| POST `/courses/{id}/remember` | owner of the course and the job | "Remember for <course>" from the review screen; back to the review screen |
| GET `/admin` | admins | Allowlist, today's spend against the cap, queue depth, aggregate counts |
| POST `/admin/allowlist`, `/admin/allowlist/remove` | admins | Add or remove someone |
| POST `/internal/cron/aggregate-and-purge` | Cloud Scheduler | `X-Cron-Token` header; aggregates, then purges |
| GET `/healthz` | anyone | `{"status": "ok"}` after a database ping, else 503 |

Every `/jobs/*` route answers 404 for a job that does not exist or belongs to someone
else, admins included, so job ids cannot be probed and admins never see documents. The
`/courses/*` routes do the same for courses.

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
  CSRF token issued at sign-in. It holds the user id, the user's session version, the
  CSRF token, one-time notices (never document content) and Authlib's sign-in state.
- **Signing out.** The cookie is signed, not stored, so clearing it in one browser
  would leave any copy of it valid for its 7 days. Signing out therefore bumps
  `users.session_version`, and a session signed in with an older version no longer
  signs anyone in: signing out ends every session of the account (other browsers and
  copied cookies too). Removal from the allowlist bumps it as well, so old sessions stay
  ended if the person is allowed back. Sessions from before the column existed carry no
  version and count as version 0.
- **CSRF.** Every form that changes something carries a hidden `csrf_token`, compared in
  constant time with the session's. `tests/test_web_auth.py` lists every POST route and
  checks each refuses a forged token, so a new route cannot skip the check unnoticed.

## Uploads and limits

`POST /jobs` checks, in order:

1. the daily spend cap: if not even the largest single model call the pipeline makes
   fits under `DAILY_SPEND_CAP_USD` today (`spend.largest_reservation_usd`, about $0.031:
   a display equation on the main model), a friendly 503 page ("Conversions are paused
   for today"). A smaller threshold admitted uploads that could convert nothing;
2. uploads in progress: at most `MAX_CONCURRENT_UPLOADS` (3) per instance and one per
   user (`uploads.UploadGate`). Each upload costs about three times its size in memory
   while it is read, copied and probed, so this keeps uploads near 200 MB however many
   requests Cloud Run sends at once (`--concurrency=20`, 1 GiB). Over the limit: a 503
   (busy) or 429 (your own upload is still in progress) page with `Retry-After`, before
   the body is read;
3. size: a declared `Content-Length` over the limit is refused before reading, and the
   body is counted while it streams in, so an endless upload is cut off just past
   `MAX_UPLOAD_MB` (413). The file stays in memory; it never touches the server's disk;
4. the CSRF token;
5. the PDF: magic bytes (`%PDF-` in the first 1024 bytes, else 415), then opened in a
   child process (`python -m a11y_stem.web.pdf_probe`) with a 30 s timeout. A crash or
   hang in MuPDF *while opening the file and counting pages* kills the child, not the
   server. Password-protected, unreadable or empty files and more than
   `MAX_PAGES_PER_JOB` pages are refused (422), and so are, before any quota is spent:
   a page larger than 5000 pt on a side (`page_too_large`; MuPDF accepts page boxes far
   past the PDF maximum, and the conversion's crash probe renders whole pages, about
   1 GB for an 18000 pt page), a PDF whose every page is a scanned image with no text
   (`scanned`, with advice to run OCR first) and one whose every page is blank
   (`no_content`). The child caps its own address space at 768 MB. The conversion
   itself is not isolated this way; see "Limits of the worker" below;
6. the page quota: one conditional `UPDATE quota_days SET pages = pages + n WHERE ... AND
   pages + n <= quota`, in the same transaction as the job insert (429 if over).

Validation errors re-show the upload page with an error summary linked to the file input.
The opt-in box "Keep my document and results for 30 days to help improve the tool" is off
by default; it sets `retain_until` to 30 days instead of 24 hours.

## Courses and notation (`web/routes/courses.py`, `web/notation.py`)

Only with `FEATURE_NOTATION_MEMORY` on. Off, every `/courses` route is a 404, the upload
form shows no course choice and ignores a `course` field, and the job page and review
screen show nothing about courses. The pipeline side is in docs/steps.md "Course
notation" and docs/layout.md "Bold letters in prose".

- **Courses** (`/courses`): a table of the user's courses with how many symbols each has,
  and a form to add one. A name is one line of up to 200 characters, not the same as
  another of the user's courses (ignoring case); up to 50 courses. A course page lists
  its symbols (the styled letter, its words, what it means, the note, "By you" or "From a
  review", and an "Edit or remove" link), and has forms to add a symbol, rename the
  course and delete it.
- **Symbols.** Typed in any form `core.notation.canonical_symbol` understands: "bold d",
  `\mathbf{d}`, the styled letter `𝐝`, "bold omega", "double-struck R", "v with an
  arrow". All of them are one entry (one per course for each symbol). A form it cannot
  read gets "This tool cannot tell which symbol that is. Write one symbol another way,
  for example “bold d” or “\mathbf{d}”." The meaning is one of vector, matrix, set,
  operator, constant, unit or something else; the note (up to 200 characters) is for the
  instructor and never sent to a model. Up to 200 symbols per course. The add form says
  to add both forms when a course writes a letter both ways (a bold v for velocity and a
  plain v for speed): a letter listed plain too is no evidence of a lost bold.
- **Delete.** "Delete course" asks first on its own page (WCAG 3.3.4); only a POST with
  `confirm=yes` deletes the course and its symbols. Documents converted for it keep their
  results and lose only the link (`jobs.course_id` set to NULL). Removing one symbol does
  not ask: the notice after it says how to add it back.
- **Upload.** When the user has courses, the upload form has an optional "Course" choice
  ("No course" first); without any, one line links to `/courses`. The choice must be one
  of the user's courses, checked after the CSRF token and before the PDF is opened;
  otherwise the form comes back with an error and nothing is uploaded or counted. The job
  stores `course_id`, and the job page says "Course: <name>. This document is read with
  its notation."
- **Worker.** Before converting, `notation.conversion_options` loads the course's symbols
  into the run's `ConversionOptions` (the settings' flags plus `notation`). The list is
  read when the job runs, so a symbol added while it waits is used. A job without a
  course, or a course without symbols, runs with the settings' flags as before.
- **Review screen.** For an equation whose PDF text or proposal has a letter the course
  lists, the card has one line: "Your course notation: bold d is a vector." When the
  course was the reason for the decision (the PDF's text cannot show bold), a second line
  says so. After a decision that settles a styled letter, the decision's notice has one
  button, "Remember for <course>: bold ω is a vector" (bold means vector, double-struck
  means set): an edit that adds a bold or double-struck letter the proposal did not have
  (the screen's "Use bold ω" is one), or accepting a proposal the style check asked
  about whose styled letter the PDF's text does not show. Only for a job with a course,
  and a letter the course does not have yet. It is optional: nothing waits on it. The
  POST checks that the job and the course are the user's and that the course is the
  job's, adds the symbol with source "review", and goes back to the same decision with
  "Remembered for <course>: ...".

Course names, symbols and notes are user content: shown only to their owner, logged only
as ids and counts (`course_created`, `course_renamed`, `course_deleted`,
`notation_added` with its source, `notation_changed`, `notation_removed`), sent to a
model only as the fenced course notation block, and erased with the user
(`ops.retention.erase_user`). Every form is a plain POST with the CSRF token, with an
error summary linked to the field, so it all works without JavaScript.

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

- **Claiming.** Fewest attempts first, then oldest (then id), so a retried job goes
  behind fresh ones. The queue position on the job page ("2 documents ahead of yours",
  `views.queue_ahead`) counts in the same order, also skipping the jobs of people no
  longer allowed. Postgres: `SELECT ... FOR UPDATE SKIP LOCKED` then an UPDATE in the same
  transaction. SQLite: a conditional UPDATE (`WHERE status = 'queued'`) whose rowcount
  says who won. Jobs of people no longer on the allowlist are not claimed; removing
  someone also fails their queued and running jobs with `access_removed`.
- **Daily budget spent.** While less than the largest single model call is left under
  the daily cap (the same threshold as the upload gate), the worker claims nothing and
  logs `claims_paused` once (`claims_resumed` when it starts again). Queued jobs wait,
  and their page says so ("Today's conversion budget has been used up ... starts then"),
  instead of running into a document with every element "not converted".
- **Leases.** A claimed job gets `lease_until = now + 120 s`. The conversion runs on its
  own thread; the worker thread waits for it and extends the lease every 40 s, and each
  progress callback extends it too. Before each claim the worker requeues RUNNING jobs
  whose lease ran out; after 3 attempts a job is failed with `lease_expired` instead.
  On shutdown (`Worker.stop`) the running conversion is cancelled and its lease ended at
  once, so another instance requeues the job straight away.
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
  budget), progress=...)` (plus `tracer=` when tracing is on), where `budget` is a
  `JobBudget` over the daily `DbSpendGuard` with what is left of the job's caps
  (`worker.job_caps`: `MAX_USD_PER_JOB` and `MAX_MODEL_CALLS_PER_JOB`, lowered to the
  per-page allowances times the pages). The caps cover the job, not one attempt, and are
  enforced on the job's row: each reservation is one conditional UPDATE

  ```sql
  UPDATE jobs SET cost_usd = cost_usd + :a, model_calls = model_calls + 1
  WHERE id = :id AND status = 'running' AND attempts = :n
    AND cost_usd + :a <= :max_usd AND model_calls + 1 <= :max_calls
  ```

  which also checks ownership; settling replaces the hold with the actual cost. So
  `Job.cost_usd` is always current, and an attempt that dies mid-call leaves its holds
  counted rather than handing its spend to the retry. A request of the second automated
  check (ADR 0005) holds dollars only: its UPDATE adds 0 to `model_calls` and does not
  test the call cap, so the checks never use the calls the steps need (the call caps
  were sized for those); they still count against `MAX_USD_PER_JOB`, the per-page
  dollar allowance and the daily cap. The defaults are
  `pipeline.run.convert_pdf` and `models.make_vision_client`; tests pass fakes to
  `create_app`.
- **Storing.** `result_json` (the `DocumentResult` without `html`), `html`, crops as
  `Blob(kind="crop")`, status `done` and an outcome code when the result is incomplete
  (below). Only if this worker still owns the job (same attempt, still running), so a
  job deleted or requeued meanwhile is not brought back. NUL characters (they occur in
  text extracted from PDFs, and Postgres refuses them) are removed first. If the write
  fails, a transient error (`OperationalError`) is retried once; after that the job is
  failed with `store_failed` instead of being left running, which would convert it
  again. The static HTML checks of the output travel in `result_json` as
  `html_checks` and are counted by `ops.aggregate` (`document check:<name>:<code>`).
  axe-core does not run in the hosted app (there is no browser in the image); it runs in
  CI and in the eval harness only.
- **Failure codes** (`Job.error_code`, shown on the job page with a plain explanation):

  | Code | Cause |
  |---|---|
  | `LayoutError.code` (e.g. `encrypted`, `unreadable`) | The layout step refused the PDF |
  | `budget_daily`, `budget_job_usd`, `budget_job_calls` | `BudgetExceeded` escaped the pipeline |
  | `scanned` | Every page was only a scanned image (the upload probe normally refuses these first) |
  | `no_content` | The result had no elements at all |
  | `model_not_configured` | The model client is misconfigured (no OpenAI key): every job would fail the same way |
  | `access_removed` | The owner was removed from the allowlist |
  | `timeout` | The conversion ran past its time limit |
  | `store_failed` | The result could not be written to the database |
  | `lease_expired` | The job was interrupted 3 times |
  | `upload_missing` | The upload blob was gone |
  | `internal_error` | Anything else |

- **Live progress.** The converter's `ProgressEvent`s (`convert_pdf(stage_progress=)`:
  "layout", then "equations", "figures", "tables" with done and total, then "assemble"
  and "checks"; content-free) are kept per stage and written to `jobs.progress_json` as
  `{"stages": {stage: event}}`: a stage seen for the first time or just finished at
  once, other updates at most every 2 s (`worker.PROGRESS_WRITE_S`), and only while
  this attempt owns the job (otherwise the next event raises `JobCancelled`). A failed
  write is logged (`progress_write_failed`) and skipped. A requeued job starts again with
  none. Only a converter that takes `stage_progress` gets it.
- **Outcome codes on finished jobs** (`worker.result_outcome`; status `done`, results
  available, but incomplete). The job page and the documents list show them instead of
  "Finished" (`web/outcome.py`), and `ops.aggregate` counts them as `job error:<code>`:

  | Code | Cause | What the user is told |
  |---|---|---|
  | `budget_daily` | The daily cap ran out part way | "Stopped early": N of M equations and figures not converted, originals kept; upload again after 00:00 UTC |
  | `budget_job_usd`, `budget_job_calls` | The job's own cap ran out | "Stopped early": split the PDF |
  | `scanned_pages` | Some pages were only scanned images (kept as flagged images) | Which pages; run OCR and upload again |

  A finished job with no code whose equations, figures and tables mostly failed with
  `model_api_error` or `model_timeout` is shown as "Stopped: the AI service did not
  answer" (`outcome.MODEL_OUTAGE`, read from the stored result on the job page; nothing
  is stored on the job, and it goes away once enough of them are filled in). Retrying
  without a new upload is not built yet.

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

## The job page

Code: `web/routes/jobs.py` (routes), `web/job_page.py` (every rule and sentence),
`web/views.py` (what is read from the database), `web/outcome.py` (stops and outages),
`pipeline/source_facts.py` and `pipeline/notices.py` (what the pipeline measures and
derives), templates `job.html`, `_job_status.html`, `_job_progress.html`,
`_job_ready.html`, `_job_notices.html`, `_job_card.html`, `_job_language.html`,
`job_draft.html`. Tests: `tests/test_job_page_rules.py` (the rules on their own),
`tests/test_web_job_page.py` and `tests/test_web_job_page_states.py` (the page in every
state, the routes), `tests/test_web_job_page_browser.py` (axe at 1280 and 320 CSS px,
reflow, htmx, no JavaScript), `tests/test_job_source_facts.py`,
`tests/test_job_notices.py`, `tests/test_job_progress.py`.

From top to bottom:

1. **The status box**, a polite live region, with a headline that says what is left
   to do (MF5 of the UX review; `job_page.headline`):

   | State | Label | Sentence |
   |---|---|---|
   | queued | Waiting | "2 documents ahead of yours (1 is yours), about 3 minutes to go. You can leave this page; it keeps its place." |
   | queued, today's budget spent | Waiting | the `waiting_budget` outcome |
   | converting | Converting | the step it is on: "Reading the equations and checking each against your PDF's text." |
   | stopped by a spend cap | Stopped early | the outcome's message (and the open decisions) |
   | AI service outage | Stopped: the AI service did not answer | "... Nothing is wrong with your PDF ..." |
   | scanned pages | Finished, with pages not converted | which pages, OCR |
   | decisions open | Converted | "3 decisions need you before you share it." |
   | no decision open, parts shown as images | Ready | "1 equation is shown as an image that screen readers cannot read." |
   | nothing left | Ready to share | "Everything was converted." |
   | failed | Stopped | why (the failure codes above) |

   The tab title follows the label. The estimate while waiting is the pages ahead (in
   the worker's claim order), what is left of the jobs converting and the job's own
   pages, at the pace of the last 20 finished jobs (start to finish, per page; 10 s a
   page with none to go by: `views.seconds_per_page`). The box changes per step, not
   per element, so a screen reader hears each step once.
2. **The steps**, outside the live region and polled on their own: "Reading the layout:
   16 pages", "Found 21 equations, 7 figures and 52 headings", "Equations: 14 of 21 read
   and checked against your PDF's text", "Figures: 5 of 7 described", "Tables: ...",
   "Building the HTML file", "Running the structure checks", each "(done)" or "(in
   progress)". Without JavaScript the page shows them as they were when it loaded, with
   the "refresh the status" link and the opt-in reload.
3. **What to do next.** With decisions open: "Review 3 decisions" (the main button,
   straight to the first open decision), "Preview the student version" and "Download a
   draft (3 decisions open)", which asks first on its own page what students would get
   ("For 1 equation and 1 figure, students will get the tool's suggestion as it is. 1
   equation is an image marked "Not converted"."). The polled status links to the first
   decision as soon as the job finishes ("Start the review"), so it is one click from
   finished to deciding. With none open, **Ready to share** (BW1, MF9): "You made 3
   decisions on 9 October." (or "Nothing in this file needed your decision."), the
   download as the main button, the student version, "Before you share" (read it
   through; the notes, optional; what is not handled) and "Put it in your course"
   (upload it as a file next to the PDF, do not paste it into a page editor; the tested
   steps per LMS are `TODO(Harish)`). Deleting is a quiet button at the end of the page.
4. **Not handled in this file** and **Worth checking**: `pipeline.notices.current(result)`
   (notices derived again from the fixes as they are now, plus the stored ones of other
   codes), one sentence each from `messages.NOTICE_MESSAGES`, with the page ("Page 3:",
   "The whole document:"), "(2 times)" for a count, and, when the notice has a decision,
   "Decide it (one of your decisions)" or "You have decided it". A notice never adds a
   decision: the table kept as an image is counted once, as its fix.
5. **What changed for a screen-reader user** (Beta 1 of the feature review), shown for
   results with `source_facts` (older ones have no card): up to 7 rows "Can a
   screen-reader user ... | In your PDF | In the HTML" (equations with their structure,
   bold vectors, figures, headings, lists, tables, links, the title, the language),
   chosen from what the PDF has. The PDF side says what is lost ("The symbols are there,
   on one line. In 9 of 19, the fraction bars and subscripts are gone."; the rows of an
   aligned derivation are lines of the text layer too, so only a matrix or cases loses
   "rows and columns"), says "Perhaps ... (we did not check)" for a tagged PDF, and
   never calls the text layer unreadable; there is no score. A detected or assumed
   language is not a "Yes" but "check it under Language below", and that row then comes
   before the title's. A method note says how it was measured and that the checks catch
   some equation errors, not all. **Hear the difference** shows up to four equations, picked by the
   structure their text layer loses (the most first, display ones first, each kind of
   loss once before any twice): the crop, "From your PDF a screen reader gets:" (for a
   tagged PDF "Your PDF's text layer gives:", since its tags may give more) the text
   layer read out flat (`job_page.pdf_reading`: Greek letters and symbols named, "/" as
   "slash", a styled maths letter as its plain letter), what is lost ("The power is
   gone."), and "In the HTML:" the spoken reading. That text is document content: it is
   rendered for the owner only and never logged. On the committed demo PDF (13 pages,
   one real run) the card has 7 rows and its first example is Equation 2.4: "capital
   delta d capital delta t period" against "capital delta bold d over capital delta t".
6. **Results**: what was found, and one breakdown everywhere (MF6): "Need your decision"
   (decisions, as on the review screen: a group of repeated headings is one), "Accepted
   for you" (with how many have a note, and how many of those the second automated
   check calls worth a look) and "Decided by you" add up to the items, and a sentence
   says so ("Of the 62 items found, 17 wait for your decision, 45 were accepted for you
   and 0 were decided by you. The 17 waiting make 3 decisions: repeated headings are
   decided together."). Then the links per kind to their first decision, and the
   automatic checks of the file.
7. **Language**: "Language: English (detected; your PDF did not say)" ("from your PDF",
   "assumed; ...", "you chose it"; a result made before detection existed counts as
   "from your PDF" when the PDF has /Lang) and a select of 25 languages with "Confirm
   this language" (when it was detected or assumed) or "Use this language". The POST
   (CSRF-checked, owner only, finished jobs only, only a listed language or the
   document's own) sets `lang` and `lang_source="user"`, drops the `lang_detected` and
   `lang_assumed` notices, unmarks passages now in the document's language
   (`job_page.set_language`), makes the HTML again (`review.rerender_html`, with the day
   of the last decision for the student note) and bumps `jobs.result_version`. The
   language tag is not logged. The text work has the same function with passage
   detection (`text_pass.set_document_language`) and its own list of languages
   (`language.LANGUAGE_NAMES`): once both are merged, the route uses those.

## Review screen

Code: `web/routes/split.py` (the review screen, page images, marks), `web/queue.py` (the
decisions, numbered, in order), `web/compare.py` (what differs in an equation, crop
geometry), `web/page_images.py` and `web/pdf_probe.py` (rendering in a child process),
`web/routes/review.py` (the decision POST and the list view), `web/review.py` (rules:
order, eligibility, validation, table edits, the stale-tab guard), `web/table_card.py`
and `templates/_table_card.html` (the table card), `web/messages.py` (what each code
means). Tests:
`tests/test_web_review_split.py` (the screen, deciding, older results),
`tests/test_web_review_tables.py` (the table card in every tier, its decisions and
edits, the stale-tab guard) and `tests/test_web_review_tables_browser.py` (axe at 1280
and 320 px in every state of the card, a wide table scrolling in its own box, keyboard
order, a cell changed from the keyboard, no JavaScript; `browser` marker),
`tests/test_web_page_images.py` (routes, ownership, retention, headers, a failing child),
`tests/test_web_compare_queue.py`, `tests/test_web_marks.py` (where the marks go),
`tests/test_web_review_split_browser.py` (axe at 1440 and 390 px, also on a dense page
with accepted items shown, 320 px reflow, target sizes, the selected mark inside the page
region on narrow and short screens, no JavaScript, Accept after typing, focus, shortcuts,
forced colours, an image retried; `browser` marker), and for the list view `tests/test_web_review.py` (one fix),
`tests/test_web_review_bulk.py` (order, landing, bulk accept),
`tests/test_web_review_messages.py` (every code has a sentence),
`tests/test_web_review_triage.py` (decisions, optional checks, heading groups),
`tests/test_web_review_browser.py` and `tests/test_web_review_triage_browser.py`.

Real documents need only one to three decisions under the review policy
(docs/design/beta-architecture.md "Review tiers": OpenStax 2.1 three heading groups, the
LaTeX handout one equation), so the screen asks them one at a time beside the page they
are on. It is the Split View design Harish chose on 2026-10-08, with ideas taken from the
queue and outline prototypes.

### The review screen (`/jobs/{id}/review`)

**Top.** One sentence, counting decisions (a heading group is one): "3 things need your
decision. Everything else was accepted; optional checks are below." ("2 of 3 decisions
left. ...", "All decisions made. ..."). No instruction paragraph: the buttons say what
they do. Beside it, the decisions as numbered steps (made, current, waiting; a bar when
there are more than 12, nothing when there is one), "All items as a list" and "Back to
the results".

**The decision** (right on a wide screen, first on a phone). "Decision 1 of 3", what it is
("Equation 5, page 1, in a sentence"), its state in words ("Needs your decision",
"Decided: Edited by you"), then one sentence that says what to look at, and the item:

- *Equation*: the PDF and the proposal at the same size. The PDF row is a crop a little
  wider than the equation (`compare.context_clip`: four font sizes of words on each side
  of an inline one), so the weight of a letter can be judged against the body text (a
  word the crop's side edges cut is painted over in the child, `--trim`, so only whole
  words show and the crop keeps its size); the proposal's MathML is drawn under it at
  the crop's scale (font size and left edge in
  `cqw` from `marks.css`). The differing symbol is outlined in both: in the crop by the
  child process (`--mark`: the glyphs of those characters inside the equation's box), in
  the MathML by a `class="flagged"` the sanitiser adds to the tokens holding it. A short
  table "What differs" gives the same in text (symbol, in the PDF, proposal, changes the
  meaning: yes or no), from the equation step's own rules (`equation_step._layer_letters`,
  `_mathml_letters`, `_letter_verdict`, `_symbol_bag`, imported, not copied): letter
  styles ("The ω is bold in the PDF (a vector) but plain in the proposal."), symbols
  ("The PDF has 8 where the proposal has 3.") and signs (minus, plus, plus-minus counted
  on both sides). Nothing of this is stored: it is worked out again from the element's
  text layer when the screen is shown. A style the text layer cannot carry is not a
  difference: a double-struck R from an MSBM font (likewise script and fraktur) is a
  plain "R" there, so for `\mathbb{R}` the screen says "The proposal has a double-struck
  ℝ. The PDF's text cannot show that style, so check it in the image." instead of
  calling the PDF's R plain (unless the R is known to come from a regular text font).
  The proposal is drawn in Latin Modern Math (served from `static/vendor/fonts`, see
  its README), the OpenType math font of the family LaTeX PDFs are set in: in the
  browser's default math font a plain omega is as heavy as a bold Computer Modern one.
  Accept and Reject, with "Edit the LaTeX" folded (open when there is no proposal or an
  error). When the proposal *changes the meaning* (a "Yes" in the table), the fix comes
  first: a one-click **Use bold ω** (`compare.suggest_fix`: the proposal's LaTeX with
  only that letter's style changed, `\boldsymbol` for Greek, `\mathbf` for an upright
  Latin letter, `\mathbb`/`\mathcal`/`\mathfrak` for a lost letter style, or the bold
  taken off; posted as an edit, so it goes through the same checks, and offered only
  when those checks pass and the same comparison then finds no meaning-level
  difference), the edit box open, and Accept as the secondary **Accept anyway**.
- *Figure*: its image (the stored crop), the caption, and the alt text field with
  Accept, Save edit and Reject. With long descriptions on (docs/steps.md), a chart's
  data table comes right under the image, as students will get it (`table_html`, in a
  box that scrolls sideways and takes focus, captioned "Data read from Figure 1; the
  values are approximate" when its values were read from the image, which is why such
  a figure asks for a decision), then the long description in its own field ("Longer
  description for figure 1"; empty to remove it) and a **Remove the data table (keep
  the descriptions)** box, both saved with Save edit. The long description is cleaned
  and checked like the model's (`figure_details`: no URL or markup, 40-2,000
  characters, at most 12 lines); the table cannot be edited, only removed. Accept with
  either changed is refused like a changed alt text ("You changed the longer
  description. Choose Save edit ..."). The long description field is shown when the
  figure has a long description or a table, and, empty, for a chart or diagram described
  with long descriptions on but left without one (the notice `long_description_missing`),
  so the reviewer can write it; the table box only with a table. A form without these
  fields (the list view) keeps both, and marking a figure decorative drops both. The
  list view does not show them yet.
- *Heading group*: one card. "“Teacher Support” appears 11 times as a bold line in the
  body text size.", the first one in its band of the page (`compare.band_clip`, the
  heading outlined), the pages it is on, **Keep as headings at level 3** and **Make bold
  text**, "Choose another level" folded (open when keeping them at the proposed level
  would skip a level), and "Decide them one by one" in the list view. The band is
  snapped to whole lines in the render child (`pdf_probe.snap_band`, from the page's own
  text): the line across its top comes in whole (a box title right above a heading is
  the context that matters), a later line across its bottom is left out, the words its
  right edge would cut are painted over (each line ends on a whole word), and with
  nothing above the heading it starts just above it. Once decided, the card says what
  the headings are now ("All 11 were made bold text.") and offers the other choice again
  for all of them ("Change all 11: Keep as headings at level 3"; `scope=all`, so the
  group decision acts on every heading of the group, decided or not, and leaves alone
  the ones already that way).
- *Single heading*: its band of the page, text and level, a level select.
- *Table* (below, "The table card").

"Details for this item" (folded) keeps the confidence, every failed check with its
sentence and code. Previous and next decision links close the card. After Accept, Save
edit or Reject the form redirects (303) to the next decision still open after this one
(`queue.next_open`, wrapping round), or to the done state: "All decisions made" with
**Download the accessible HTML**. There the decision's heading takes focus (`autofocus`
on the `tabindex="-1"` heading, which works without JavaScript; no URL fragment, which
would cancel autofocus) and is described by a status line above it: "Equation 1:
Accepted. 2 decisions left." A refused edit comes back on the same decision (422) with the
error summary and the field open.

**Optional checks (M)**, folded, below the decision: each a link ("Equation 2, page 1")
and a short note ("A letter's style differs slightly; the meaning is the same."; for a
style the PDF's text cannot show, "The proposal has a double-struck ℝ, a style the PDF's
text cannot show."). Opening
one shows it in the decision's place ("Optional check", "Accepted, with a note"), still
editable, with a link back to the decision at hand.

**The page** (left on a wide screen, sticky, below on a phone). "Page 1 of 16", the pages
as numbered links with "N to decide" only where a decision is open (an open decision
counts on the page of its first heading still waiting, so the pages add up to the total),
a legend (only the kinds of mark on the page), "Show accepted (N)", and the page image
in its own named, focusable scroll region ("Page 1 image with 5 marks"). Every fix on
the page is marked by a link to it:

| Mark | Shape | Text |
|---|---|---|
| A decision still open | solid frame, numbered disc beside it (the decision's number) | "1: Equation 5, needs your decision (1 of 3)" |
| A heading group's heading on another page than the one its decision counts on ("Same decision, other page") | thinner frame, the number in an outlined disc | "1: Heading 24, “Teacher Support”, repeated heading, part of decision 1 of 3 (first on page 1)" |
| The selected one | thicker blue frame, blue disc, `aria-current` | the same |
| An optional check | dashed frame; "Note" label on hover, focus or selection | "Note: Equation 2, optional check" |
| Accepted or decided | dotted frame (a tick on hover, focus or selection), only with "Show accepted" | "Heading 1, accepted automatically" |

So a page whose chip has no "N to decide" never shows a solid decision mark: a group
counts on the page of its first heading still waiting, and its headings elsewhere get
the lighter mark.

A mark is two boxes (`web.marks`, positions from `marks.css`; the CSP allows no inline
styles): an invisible link, the clickable area, at least 24 x 24 CSS px (WCAG 2.5.8) at
the smallest size the page is drawn (the page frame is at least 36rem wide at every
window width, about 1 CSS px per point for a Letter page, and scrolls inside its region
when narrower), and inside it a frame that hugs the item, so frames never cross the
lines above and below. On a dense page (an inline equation on every line, accepted ones
shown) links that size would overlap, and a click in the overlap would go to the wrong
item: overlapping links are pushed apart, each by part of the overlap, never so far that
a link stops covering the middle of its own item (pairs squeezed along one axis are
tried along the other). The frames stay put. The layout covers every fix of the page,
shown or not, so no subset a view shows overlaps. A mark's number sits left of its
frame, or for an equation in a line of text in the page margin on the same line (its
"Note" label and tick too), so it covers no word. The marks keep their own colours in forced-colours mode
(`forced-color-adjust: none` on the page frame): the page image stays white whatever the
theme, and system colours would make them white on white. The page itself never scrolls
sideways (320 CSS px included).

**Page images.** Rendered when the browser asks, from the job's own upload, in the
upload probe's child process (`python -m a11y_stem.web.pdf_probe render ...`, 768 MB
address space cap, 20 s timeout; `web.page_images`), never in the web process: a page
that crashes MuPDF kills a child. Whole pages at 144 dpi (long side at most 2000 px),
regions at most 1600 px, never more than 2400 px whatever is asked. A render needs a
slot (`page_images.render_slot`), taken *before* the upload is loaded, so no waiting
request holds a copy of the PDF: two per person (a review page asks for two images at a
time; one more is refused at once with a 429 and `Retry-After: 2`, so one signed-in
user's burst cannot tie up the instance), two children at once per instance (a request
waits at most 3 s for one, then 503). A burst of 150 requests from one user now renders
two images and refuses the rest at once, and other pages stay fast. Nothing is stored
and nothing is cached: the responses carry `Cache-Control: private, no-store`
like the crops, a job past its retention time or deleted answers 404 (`owned_job`), and
someone else's job 404 too. A failed render answers 503 (logged as `page_image_failed`
with a code, no content); the decision still works, and the screen says the image could
not be shown (with JavaScript) and points to the list view.

**Page sizes.** The marks need each page's size in points: `DocumentResult.page_sizes`
(0-based page -> width, height, as layout measured it) is stored with every result from
this release on. A result stored before it has none, so the screen asks the PDF once
(`sizes` in the same child, in a render slot) and stores the answer in the job's
`result_json` (only that key is added; the result still loads as before), so the next
view, its `marks.css` and its region images start no child for it; if that fails too,
the page is shown without marks ("The marks could not be placed on this page") and every
item is still in the list view.
Results stored before review tiers load as before (`default_review`), each waiting heading
then its own decision.

**The table card** (`web/table_card.py`, `templates/_table_card.html`). Tables are a
review group like the others (equations, figures, tables, headings), so a table kept as
an image is a decision the screen shows and the job can be done. The card has:

- the table in the PDF (`/regions/{id}.png`: the box layout found, outlined, with a
  little page round it snapped to whole lines like a heading's band, so the caption
  above it comes in whole) and, under it, the proposal as the student would get it:
  `pipeline.table_html.render_table`, the output's own serialiser (cell text escaped,
  maths cells as MathML rebuilt from an allowlisted tree), in its own named, focusable
  box that scrolls sideways when the table is wider than the card. Its cells keep whole
  words, and every cell has a rule round it, so a cell spanning the wrong rows or
  columns shows. Its label says whether the document has it now ("Proposal, as students
  would get it", "Rebuilt, as students get it", "Proposal (the document has the image
  instead)");
- **What the checks found**: the step's failed checks as sentences from
  `web/messages.py`, and any structural problem of the proposal's grid
  (`core.types.grid_problems`, in plain English: `web.review.GRID_PROBLEM_SENTENCES`)
  that no check already reported. The codes stay in "Details for this item";
- **Change the table** (folded): "Cell to change", a list of every cell ("Row 4,
  columns 2 to 3: v = d / t") with **Change this cell**, a GET that comes back with that
  cell's form open and its field focused (`?item=<id>&cell=r3c1`); and **Headers**, two
  boxes, "The first row is a header" and "The first column is a header", with **Save
  headers**;
- **The grid read from the PDF** (folded), when layout built one (`Element.table`) that
  has no structural problem and differs from the proposal: the grid, and **Use this grid
  instead**;
- **Accept** and **Reject** ("Reject keeps the image of the table, marked “not
  converted”."). A table kept as an image (`table_not_rebuilt`, no proposal) has **Use
  the grid read from the PDF**, when there is one (shown in the proposal's place, "Read
  from the PDF's own text and lines, without a model"), and **Keep the image**, which
  rejects it: the document keeps the flagged image with its "not converted" note.

Every change to a table goes through the same route as other decisions (`change` is
`cell`, `headers` or `use_local`), is stored as `edited` with a `ReviewEdit` and
re-renders the HTML. After one the reviewer stays on the table, which is shown as it is
now, with "Go on to decision N" when it was the last change; Accept and Reject move on
as for other kinds. The rules (`web.review`, "tables"):

- *Headers.* Ticking a box makes the first row's cells column headers (`header_rows`
  1), or the first column's body cells row headers (`header_cols` 1); unticking makes
  them data cells again (a full-width section heading stays one). A model may mark the
  first column's cells without the count; the box is then ticked. The same boxes as
  before are refused ("The headers are already like that").
- *A cell.* Its text is cleaned as alt text is (invisible format characters removed,
  whitespace collapsed), at most 1,000 characters, and escaped when written; a maths
  cell's LaTeX goes through what an equation edit does (`web.review.checked_latex`:
  `clean_latex`, `check_latex`, `safe_latex_to_mathml` inline, `check_mathml`), and its
  plain text stays as the serialiser's fallback. Text typed into an empty cell of the
  header row or column makes it a header. The same text again is refused.
- *Structure.* An edit that would give the grid a structural problem it did not have is
  refused with what it would break ("The headers were not saved. The headers would
  cover the whole table, leaving no row or column for data."): the first row of a
  one-row table, a header row whose cell spans into the body. A problem the proposal
  already had is left for the reviewer to judge and does not block a cell.
- *The grid read from the PDF* replaces the proposal with a copy of `Element.table`; the
  fix keeps its reason (`table_not_rebuilt`), so ops still counts why it was asked, but
  the card and the list no longer show it: the table is converted now.

After a table edit the fix's checks are the ones the review ran, as after an equation
edit: a maths cell's LaTeX and MathML checks, and the table step's own checks of the
table as it is now (`web.review.table_checks`, which runs
`pipeline.table_step.table_checks(grid, element)`: structure and headers, agreement
with the grid read from the PDF, the cells' words, the maths cells). So a finding stays
on the card until the cell is put right, and a failed one is a warning on the change
("1 check gives a warning"), never a refusal. Until the table step has that function
there are none. Nothing about a table
is logged but codes: the `review` event gets `change=cell|headers|use_local`.

With keyboard shortcuts on, A accepts a proposed table and E opens "Change the table" at
the cell list. In the list view a table card shows its crop, the proposal (or the grid
read from the PDF) and Accept, Reject or Keep the image, Use the grid read from the PDF;
headers and cells are changed on the review screen ("open it on the review screen").

**Stale tabs.** Every review write moves `jobs.result_version` on, in the same
transaction, with the job row locked (`web.review.guard_result_version`, called by every
write path in `routes/review.py`: one fix, a heading group, bulk accept). The table
card's forms send the version their page was made from (`version`, read from
`JobView.result_version`); one made before another tab or window changed the review is
refused with a 422 and nothing saved: "Your change was not saved: this item was changed
in another tab or window after this page was opened, and what you see now is the latest
version." The page that says so is the latest version, and offers **Reload table 1**.
A form without the field (older pages, scripts) is not checked. Without it a cell
changed by its place in the grid could land on another cell once a tab elsewhere had
changed the table. The other cards will send it too (the review-screen UX work), with
the same helper.

**Accept never drops an edit.** Accept and Save edit share a form; Accept keeps the
proposal and never used the fields, so a correction typed and then accepted out of habit
was lost without a word. Accept with the LaTeX, the alt text, "Decorative" or the level
changed from the proposal is refused on that field ("You changed the LaTeX. Choose Save
edit to use your change, or undo it to accept the proposal."), the change kept in the
field and Save edit made the main button; nothing is saved. Fields the form did not send
are not compared (both views).

**JavaScript** (`static/app.js`) only enhances: it scrolls the page region so the
selected mark is in its visible box, working inside the region only (stacked, the region
starts below the window, and a window term used to scroll it to its end; side by side it
also moves the window when the mark is near the foot of a page, never so far that the
decision's heading leaves the window), asks once more after 1.5 s for an image that was
refused for a moment and only then says the page image could not be shown, makes "Save
edit" the main button once a field of the decision is changed, and offers keyboard
shortcuts: N next decision, P previous, A accept, E go to the edit field. They are off
until turned on ("Keyboard shortcuts", stored in `localStorage`; WCAG 2.1.4), ignore keys
with Ctrl, Alt or Meta, and never act while focus is in a field.

### The list view (`/jobs/{id}/review/list`)

Every fix of the document, grouped by kind, as the review screen was before the split
view; it keeps bulk accept. Its forms send `view=list`, so a decision there comes back to
the list's next card.

**Order.** Only meaning-level problems ask for a decision (docs/design/beta-architecture.md
"Review tiers"), so the page has three sections:

1. **Needs your decision (N)**: equations, then figures, then tables, then headings (a
   mistake there costs a student most), each with the fixes still waiting (needs review, failed and not
   converted alike) in page and reading order. N counts decisions: a group of repeated
   headings is one.
2. **Optional checks (M)**: fixes accepted automatically with a note (`Fix.review ==
   "optional"`: a bold subscript, a double-struck R the PDF cannot show, a heading with
   weak evidence, a long alt text), folded into a `<details>`. Each item's summary line is
   its note in plain English (`web/messages.py`); unfolding it shows the full card, which
   can still be edited or rejected. Nothing here counts toward "done". The second
   automated check's note on a figure (ADR 0005: a possible mismatch between the alt
   text and the image) is listed here by policy, but it is not something small, so the
   page does not call it that: when there is one, the line at the top says "... optional
   checks do not hold anything up, but 1 of them is worth a look" instead of "can be
   looked at if you want", the intro says such figures are worth a look (a wrong alt text
   changes what a screen reader user hears; the check is new and can be wrong, so it does
   not hold up the document), the section's lead counts them apart from the small notes,
   and their card says "Optional check (worth a look; it does not hold up the document)"
   instead of "nothing to do unless you disagree" (`core.triage.second_check_note`;
   tests in `tests/test_web_second_check_review.py`).
3. **Accepted or decided**: everything else, folded.

A table of contents at the top links to each section and kind group with its counts.
Every fix is numbered within its kind in reading order ("Equation 3"), so a number stays
the same while the list reorders.

**Heading groups.** Repeated headings with weak evidence (the same label and style two or
more times, `pipeline/heading_step.py`) share a `Fix.group_key` and are one card: "“Teacher
Support” appears 11 times as a bold line in the body text size. Keep them as headings at
level 3, or make them bold text?", with **Apply to all 11** (accept each at its proposed
level), **Make all 11 bold text** (reject each: the output writes `<p><strong>`) and a
level select with **Set the level for all 11**. The buttons' accessible names include the
label ("Apply to all 11 headings “Teacher Support” at level 3"). The group's own cards
are folded inside it ("Decide them one by one"), for the odd one out; once one is decided
the reviewer lands back on the card, which asks about the rest ("2 still waiting") with
their cards unfolded (also whenever one of them has an error, so the error summary link
reaches the field). Apply to all leaves out a heading whose
level the output would move (it says how many); one level for all is refused as a whole,
with nothing saved, if the output would move it for any of them. Each changed heading gets
its own `ReviewEdit`, the HTML is re-rendered once, and the reviewer lands on the next
decision. A group decision posts to the same route as a single one, with the group key
(`hg-` + the first member's element id) in place of the element id.

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
clean and validate edits the way model output is (`equation_step.clean_latex`, then
`pipeline.validate`'s `check_latex`, `safe_latex_to_mathml`, `check_mathml`; alt text
through `validate.clean_alt`, then `check_alt`), store a `ReviewEdit`, set the fix status
(`accepted`, `edited`, `rejected`) and `Fix.reviewed` in `result_json`, re-render the
job's HTML with `pipeline.assemble.render_html` and re-run its static checks
(`result_json.html_checks`, listed on the job page). A failed check re-shows the page
(422) with the reviewer's input as typed and an error summary linked to the field;
nothing is saved.

- **Accept by a reviewer.** It keeps the status `accepted` (the proposal is used as it
  is) and sets `Fix.reviewed`, so the job page counts it under "Reviewed by you", not
  "Accepted automatically", the card says "Accepted by you" and the output's About
  section says "accepted by a reviewer". `reviewed` defaults to false, so results
  stored before it existed still load (their edited and rejected fixes still count as
  reviewed).
- **Cleaning.** A reviewer who pastes `$v_f = v_0 + at$` gets `v_f = v_0 + at`, and
  `50% of g` is stored as `50\% of g` (a bare `%` would make the converter drop the rest
  of the line). Alt text loses invisible format characters (bidi controls, zero-width
  spaces), so what is stored is exactly what `check_alt` judged.
- **Heading levels.** Only `1` to `6` (ASCII digits; `²` used to be a 500). The output
  never skips a level (`assemble.Outline`, see docs/output.md "Headings"), so a level
  more than one below the heading before it would be stored as one level and shown as
  another. It is refused with the level it would be shown at and the levels that fit
  ("Choose a level from 1 to 3, or change the heading before it first"); the form's
  hint says the same before saving. The first heading may take any level. Rejecting or
  changing a heading can move later ones (the children of a rejected heading move up
  together), so Accept, as well as Save edit, refuses a proposed level the output would
  move, and the card has no Accept button for it. The card shows the level the document
  writes ("In the document: level 2."; for a rejected heading, "If accepted, it would be
  level 2.") and its select offers that level. A decision that moves other headings says
  which ("Heading 3 is now shown at a different level."). Stored levels are not
  rewritten: restoring a rejected heading puts its children back where they were.
  `web.review.heading_places` is the one pass that gives every heading's written level;
  a test checks it against the assembler's output for every outline of up to four
  headings.

After a decision on the list view the browser is redirected (303) to
`/jobs/{id}/review/list#item-<id>` of the next fix still waiting, after this one in review
order and wrapping round to the first, or to `#review-progress` when none are left. A one-time notice there links back to the fix
that changed ("Equation 1 on page 1: Accepted."), which is unfolded so the link works.
The landing item has `tabindex="-1"`, so the next Tab reaches that link.

**Bulk accept.** Each group offers to accept several proposals at once:

- "Accept every equation proposal at or above X%" (X from 90, 80, 70, 60, 50; each
  option says how many it would take), for every group;
- "Accept all N remaining headings", for headings only: a wrong heading level is cheap
  to spot and fix later, a wrong equation or alt text is not.

Only fixes a single "Accept" could take are included: status `needs_review`, a proposal,
no failed error-level check and, for a heading, a level the output writes as it is
("Not included: 1 heading the document shows at another level than the one proposed").
Failed and not-converted fixes, and fixes the reviewer already decided, are never
touched. The threshold compares the whole percentage the page
shows (0.795 is shown as 80%, so it is "at or above 80%"). Bulk accept posts to the same
route as a single decision, with the group (`equations`, `figures`, `tables`, `headings`) in
place of the element id (element ids look like `p3-e12`, so the two cannot collide).
That keeps one CSRF- and ownership-checked way to change a review, and the POST-route
list in `tests/test_web_auth.py` unchanged. It is one transaction: one `ReviewEdit` per
fix, `result_json` updated, the HTML re-rendered once, then a redirect to
`#group-<kind>` with the count ("Accepted 3 headings."). Repeating it changes nothing
and re-renders nothing. A choice the page never offers (an unknown threshold, "accept
all" for equations) is refused with a 422 and an error linked to the threshold select.

**Job page.** "Need your decision" counts the decisions still waiting (a heading group
once; failed and not-converted items too, since each needs a decision) and is broken down
by kind, each linking to the first decision of that kind on the review screen
(`?item=<key>`; "3 need your decision: 1 equation, 1 figure, 1 group of repeated
headings."). "Optional checks (accepted; nothing to do)" counts the notes
nobody has opened; they never count toward done. When some of them are the second
automated check's, the label says so: "Optional checks (accepted; nothing required, 1
worth a look)". The summary also counts
list items, gives the model cost of the whole job (`Job.cost_usd`: every attempt,
including one interrupted before it stored a result) and lists the static checks of the
HTML file that did not pass, each as a sentence from `web/messages.py` with its code
("All 11 checks passed" otherwise). An incomplete or failed job's outcome
(`web/outcome.py`: "Stopped early", "Finished, with pages not converted", a scanned or
empty PDF) replaces the plain status in the page title, the status box and the
"Why the conversion stopped" section, and a finished but incomplete job gets a note at
the top of its results.

**Messages.** `web/messages.py` maps every `(Check.name, Check.code)` pair and every
`Fix.reason` the pipeline can emit to one plain sentence that says what is wrong and
what to look at. `tests/test_web_review_messages.py` collects the codes from the
pipeline's source with `ast` (not from a hand-kept list), so a new code fails the test
until it has a sentence, and a sentence whose code no longer exists fails it too. An
unknown code still gets a neutral sentence plus the code.

**Narrow screens.** Long file names, codes and document text wrap
(`overflow-wrap: anywhere` on headings, ledes, summaries, error text, table row headers
and the review cards; `break-word` everywhere else), so no page scrolls sideways at 320
CSS px. A table wider than the screen scrolls inside its own `.table-scroll` box, which
is `position: relative` so the visually hidden button text in it cannot widen the page.

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
Cache-Control: no-store            (everything except /static/; page images and
                                    marks.css: private, no-store)
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
Our own script, `web/static/app.js`, places error summary links (below) and adds the
review screen's enhancements (scrolling the page image to the selected mark, opt-in
keyboard shortcuts; see "Review screen"). The review screen's per-job geometry comes as
a stylesheet from our origin (`marks.css`), not as inline styles.

## Accessibility of the app itself

Server-rendered pages that work without JavaScript: htmx only polls the job status
and, separately, its list of steps (every 3 s, 204 while unchanged). The status box is
a polite live region that changes per step, not per element; the counts are in the list
of steps outside it. When the job finishes the status box says so and links to the first
decision and to the results ("Start the review or show the results", "Show the results",
or "Show why it stopped"); the page is not reloaded under the reader, which would send a
screen reader back to the top. Without
JavaScript the status page does not reload by itself, because a reload the user did not
choose sends a screen reader back to the top each time (WCAG 2.2.1, failure F41; axe
rule `meta-refresh`, critical). It has a "refresh the status" link and, inside
`<noscript>`, an opt-in link to `/jobs/{id}?auto=1`, which adds a 15 s `<noscript>` meta
refresh until the job finishes and shows a "Stop reloading this page" link. Every page has a skip link, one
`<main>`, a labelled navigation, unique titles (prefixed "Error:" when a form has errors),
labels on every control, error summaries linked to fields, visible focus rings, and
status written in words, not colour alone. After a failed form the error summary takes
focus (`autofocus` on a `tabindex="-1"` box, so it works without JavaScript), and so does
the first one-time notice after a redirect ("deleted"), because a live region present at
page load is not announced. While htmx polls, the status fragment carries a `<title>`
that htmx moves into the document title, so the tab title follows the status.

Following an error summary link would scroll the control to the top of the window and
cut off its label, hint and error message. `app.js` (as the GOV.UK error summary does)
scrolls the start of the field's `.field` wrapper to the top instead and focuses the
control without scrolling again; when the field is taller than the window (320 x 256,
which is 400% zoom, where a heading-level error alone wraps to 170 px) the focused
control stays in view with as much of the field as fits above it. Without JavaScript,
`scroll-margin-top: 8rem` on form controls keeps the label in view on wider screens.

`tests/test_web_browser.py` runs axe-core on every page (zero serious or critical
violations), including an unfinished job page with JavaScript on and as a no-JS browser
parses it; runs the WCAG 2.2 AA and best-practice rules at moderate and above; checks
every page at 320 CSS px with a long underscored file name, with and without the WCAG
1.4.12 text-spacing override; checks where focus goes after errors and after deleting,
and that following an error summary link leaves the field's label and error message in
view, with and without JavaScript (`test_web_review_browser.py` does the same for the
heading-level and LaTeX errors at 320 CSS px); and walks the upload-to-review flow with
JavaScript off. `test_web_review_split_browser.py` runs the WCAG 2.2 AA and
best-practice rules (target size included) at moderate and above on every state of the
review screen at 1440 and 390 CSS px, checks 320 CSS px reflow with and without text
spacing, measures every mark (at least 24 x 24 CSS px), completes a whole review with
JavaScript off, checks that focus lands on the next decision's heading after each
decision, that shortcuts are opt-in and never fire while typing, and that the marks keep
their colours in forced-colours mode.

Contrast of the review screen's own colours (`tests/test_web_review_split.py` computes
them from `app.css`): mark outlines on the white page image at least 3:1 (decision
#7a4b00 7.4:1, selected #1a4fa0 7.9:1, optional #4b5a75 7.0:1, accepted #1d6b35 6.5:1),
white text on the numbered discs at least 4.5:1.

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

Besides jobs and their children, the schema holds a user's courses and their notation
entries (user content), content-free usage events, sign-in identities and the daily
counters (docs/design/content-features.md "Database"). `a11y-stem users delete` erases a
person through `ops.retention.erase_user`: every job (counted first), then every row of
every table that holds their user id, then the account; a test fails when a table with
a user id is added without it.

- Dev and test create tables directly on start-up. Production refuses to start when the
  schema is missing or behind this release's migrations: run `uv run a11y-stem db
  upgrade` (same as `uv run alembic upgrade head`) before deploying. A schema at a
  revision this release does not know (a newer release migrated it) is accepted and
  logged as `schema_ahead`, so traffic can roll back to an older revision; see
  deploy.md, "Roll back".
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
| `uv run pytest -m browser tests/test_web_review_split_browser.py` | Chromium: the review screen (axe at 1440 and 390 px, reflow, target size, no-JS completion, focus, shortcuts, forced colours) |
| `uv run pytest -m browser tests/test_web_review_figures_browser.py` | Chromium: the figure card with a long description and a data table (axe at 1440 and 390 px, as shown and after a refused edit; no sideways scrolling at 320 px; "Remove the data table" without JavaScript) |
| `uv run pytest tests/test_web_courses.py` | Courses and notation: the flag off, ownership (404), CSRF, every accepted symbol form and the errors, the upload's course, the worker's options, the review card and "remember", logs without course content, erasure |
| `uv run pytest -m browser tests/test_web_courses_browser.py` | Chromium: axe on every courses page and error state, the upload choice, the job page and the remember offer; 320 px reflow with text spacing; the whole flow without JavaScript |
| `uv run pytest -m browser tests/test_web_job_page_browser.py` | Chromium: the job page in each state (axe at 1280 and 320 px, reflow with text spacing, htmx steps outside the live region, draft and language without JavaScript, the one-click download) |

Postgres tests use `TEST_POSTGRES_URL` if set (CI service container), else start an
embedded server from the `pgserver` dev dependency (Python 3.12), else skip. Browser tests
need `uv run playwright install chromium`; axe-core is downloaded once from the npm
registry, checked against its published integrity hash and cached in `.cache/axe-core/`.

Most of these tests monkeypatch the other work packages' functions the web app calls
(`pipeline.validate`, `pipeline.speech`, `pipeline.assemble`, `ops.aggregate`,
`ops.retention`) and pass a fake converter and model client to `create_app`. Two files
run the real thing with the offline model client: `tests/test_web_pipeline.py` drives
`tests/fixtures/layout/worked_example.pdf` through upload, worker, review, aggregation and
deletion (the stored HTML equals a review re-render, crops are keyed by element id, an
inline equation edited in review stays in its paragraph), and `tests/test_web_outcomes.py`
covers budget stops, scanned pages and removed users with `pipeline.run.convert_pdf`.
