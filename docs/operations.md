# Operations

How the deployed app looks after itself, what it records, and what to do when something
goes wrong. It describes production, which runs on Railway. First deploy, rollback and
variables: [deploy-railway.md](deploy-railway.md). The Google Cloud Run alternative
([deploy.md](deploy.md)) has its own scheduler, alerts and backups, which this page does not
cover.

## What runs on a schedule

| What | When | Where |
|---|---|---|
| Aggregate, then purge | Hourly at minute 7, the Railway `cron` service | `readablestem ops cron`: needs only the database, prints `aggregated_jobs=... jobs_deleted=...`, exits 1 if aggregation failed (after the purge ran). The HTTP route `POST /internal/cron/aggregate-and-purge` (header `X-Cron-Token`, logs `cron_done`) does the same work, but nothing in production calls it |
| Decisions canary | Not run in production | Only the HTTP cron route runs it (`ops.canary.maybe_run_daily`), and it is OpenAI's Decisions API, which is off under `MODEL_PROVIDER=anthropic`. By hand only. See "Decisions canary" below |
| Claude canary | Not run in production | Only the HTTP cron route runs it (`ops.claude_canary.maybe_run_daily`); `ops cron` does not. By hand: `readablestem ops claude-canary --max-usd 0.25`. See docs/steps.md "Second check on Claude" |
| Purge backstop | Hourly, the worker thread (first run 10 minutes after start) | `purge_expired` again, logged `purge_done source=worker`; `purge_backstop_deleted` (WARNING) if it found overdue jobs, i.e. the cron is late |
| Database backup | Daily at 08:17 UTC, the Railway `backup` service | `pg_dump` to a `.part` file, checked (gzip, completion line, restore into a scratch database, every table and the schema version present), then moved into the `/backups` volume; dumps older than 5 days are deleted (the last 6 are kept); logs `backup_ok file=... tables=... alembic=...`, or `backup_failed reason=...` and exit 1. Records the `backup` heartbeat. See "Backups and restore" |
| Failed cron or backup run | `/healthz/ops`, checked from outside by GitHub Actions `uptime.yml` every 3 hours | The backup and the cron each write a row of `ops_heartbeat` when they finish well; `/healthz/ops` answers 503 when the backup is older than 26 hours or the cron older than 150 minutes, and the failed workflow run is emailed by GitHub. See "Health, readiness and shutdown". Railway's own deployments list still shows the failed run, and its project notifications can mail it (deploy-railway.md, "Logs and monitoring") |
| Deploy health check of `/healthz` | On every deploy, Railway | A new deployment takes traffic only once `/healthz` answers (timeout 300 s) |
| Outside-in check of `/readyz` | Every 5 minutes, GitHub Actions `uptime.yml` | Skips unless the repo variable `LIVE_URL` is set; a failed run is emailed by GitHub. This is the only check of `/readyz` |
| Outside-in check of `/healthz/ops` and `/demo` | Every 3 hours, GitHub Actions `uptime.yml` | Same variable and the same email. GitHub turns scheduled workflows off after 60 days with no commit to a public repository ("Health, readiness and shutdown") |
| Dependency updates | Weekly, Dependabot | One grouped PR per ecosystem (uv, actions, Docker) |

The same tasks run by hand:

```sh
# On Railway: redeploy the `cron` service from its latest deployment (deploy-railway.md, "Cron"),
# or from a machine that can reach the database (prints counts only):
DATABASE_URL=postgresql+psycopg://... uv run readablestem ops cron
uv run readablestem ops aggregate
uv run readablestem ops purge
uv run --env-file .env readablestem ops decisions-canary --max-usd 0.02   # OpenAI key; no database needed
uv run --env-file .env readablestem ops claude-canary --max-usd 0.25       # Anthropic key; no database needed
uv run readablestem ops launch-check      # unfinished copy, demo, baseline, settings and spend limits
uv run readablestem ops backup-check      # DATABASE_URL could run the app? ("Backups and restore")
uv run readablestem ops restore-drill     # pg_dump, restore into a scratch database, check, drop
```

`ops cron` exits 1 if aggregation failed, but only after the purge has run. After a run in which
both worked it records the `cron` heartbeat (`ops_heartbeat`); a run that failed records nothing,
so `/healthz/ops` goes stale 150 minutes after the last good one.
`readablestem ops replay-erasures` is for restoring a backup ("Backups and restore").

## Retention

Each job gets `retain_until` when it is uploaded: `RETENTION_HOURS` (24) later, or
`OPT_IN_RETENTION_DAYS` (30) when the instructor opted in. The hourly purge deletes every
job past that time together with everything derived from the document: the uploaded PDF,
the crops, the result JSON, the HTML, the filename and the review edits. It deletes the
child rows itself and does not rely on the database cascade alone.

Every deletion goes through `ops.retention.delete_jobs(session, job_ids)`: the hourly
purge, and the web app's "delete now" (`POST /jobs/{job_id}/delete` must call
`delete_jobs(session, [job.id])` in its transaction rather than `session.delete(job)`).
In the same transaction, before deleting, it aggregates a finished job that was not
aggregated yet (a user can review, download and delete within the hour, before the cron
runs) and counts the job's review actions. Deleting a row any other way loses those
counts without a trace.

Deletion does not wait for statistics. If counting fails at that point (an aggregation
bug, say), the job is deleted anyway and counted in `unaggregated_deleted`. A non-zero
value there means some counts are missing; it never means a document was kept.

What survives deletion: accounts (the allowlist, and with open sign-up the people who signed
themselves up, until they delete them or an admin does), the daily spend and quota counters, the
content-free aggregates below, the usage events (next section), and database backups. A
backup holds every document that existed when it ran, so a deleted document stays in the
backups taken before its deletion until they are dropped. The `backup` service takes one
dump a day and keeps the last 6, and there are no point-in-time-recovery logs, so that is up
to about a week after the purge (deploy-railway.md, "As deployed"); the privacy page says so
("about 6 days", "up to about a week") and has to keep saying so if the schedule changes.

### Large files waiting for their pages

A PDF over the page or size limit is kept as a `pending_uploads` row (`web/pending.py`, docs/web.md
"Large files in parts") while its owner chooses which pages to convert. It is a copy of the
upload, so it follows the same promise: `retain_until` is `RETENTION_HOURS` after it was
made, the same hourly purge deletes it (`ops.retention.purge_pending`, logged as
`pending_deleted` in `purge_done`), "Discard this file" deletes it at once, and so does
`erase_user`. Past its time it answers 404 before the purge runs. The sample document
(`web/sample.py`) is an ordinary finished job with `is_sample` set, deleted by the same clock.

### Answers to uploads of several files

The page that says what became of the files of one upload (`web/upload_result.py`, docs/web.md
"Several files in one upload") is a row of `upload_results`: file names and numbers, no document
content. It is document-derived all the same, so `retain_until` is an hour after it was made, the
hourly purge deletes it (`ops.retention.purge_upload_results`, logged as `results_deleted` in
`purge_done`), `erase_user` deletes the person's, and past its time it answers 404.

### Usage events

`events` (`web/events.py`, docs/web.md "Usage events") holds content-free, pseudonymous
usage events: a user id, an event name, a job id, a category code and a number. They
outlive the jobs they mention on purpose (the beta's measures span weeks, and the
deleted-document page uses them to tell an owner what happened), but not for ever:

- **400 days** (`web.events.EVENT_RETENTION_DAYS`). The same hourly purge, after the jobs,
  deletes every event older than that (`ops.retention.purge_old_events`, one DELETE on
  the indexed `created_at`) and logs the number as `events_deleted` in `purge_done`.
  Why 400: a whole academic year and the first weeks of the next, so a course taught
  again can be compared with its first run and the four-week return rate measured all
  year. Changing it means changing the privacy page, which names the number.
- **With the user.** `readablestem users delete` (`ops.retention.erase_user`) deletes all of
  a person's events at once.
- **Off.** `EVENTS_ENABLED=false` writes no new events (and turns the two questions after
  a download off); events already kept follow the rules above.

The upload fingerprint in `uploaded` events is keyed with `SESSION_SECRET`: rotating the
secret makes the next upload of a file look new (a missed `reuploaded`, nothing else).
`/admin` shows the counts per day and per pseudonymous person.

## Error aggregates

`error_aggregates` has one row per (UTC day, step, category) with a count. The day is the
day the job finished. Fixes are counted as the pipeline left them, even if a reviewer
acted before the hourly run: a review rewrites the stored result, so each fix's status is
read from the `before` snapshot of its earliest review. Categories:

| Step | Category | Counts |
|---|---|---|
| `equation`, `figure`, `heading` | `status:<status>` | fixes by status (`accepted`, `needs_review`, `abstained`, `failed`) |
| same | `check:<check name>:<code>` | failed checks, e.g. `check:latex.allowed_commands:unknown_command` |
| same | `reason:<code>` | `Fix.reason`, e.g. `reason:low_confidence`, `reason:budget_daily` |
| same | `call:<model>:outcome:<outcome>` | every model call attempt, e.g. `call:gpt-6-luna:outcome:timeout` |
| same | `call:fallback` | calls made on the other model after a failure |
| same | `call:escalated` | fixes re-asked on a different model without a failure (e.g. a low-confidence `gpt-6-luna` answer sent to `gpt-6.1-sol`) |
| same | `review:<action>` | reviewer decisions (`accept`, `edit`, `reject`), counted when the job is deleted, because reviews can happen until then |
| same | `tier:<tier>` | fixes by review tier as the pipeline left them (`required`, `optional`, `none`; `Fix.review`) |
| same | `tier:<tier>:<reason>` | why each required or optional fix has its tier: each failed check code, its `Fix.reason`, `heading.repeated_label` for a member of a group of repeated headings, or `below_accept` when only the confidence held it back (`core.triage.tier_reasons`) |
| same | `decisions:required` | decisions the pipeline asked a person for: one per required fix, but one per group of repeated headings and one per group of pages kept as images for one reason (`sum(decisions:required) / sum(pages)` is the review load per page) |
| same | `aggregate:checks_edited` | fixes whose checks could not be counted: a reviewer's edit replaced them before aggregation ran (a heading edit keeps them; checks in the earliest `before` snapshot are counted when it has them) |
| `equation`, `figure` | `verify:<check>:<stage>:outcome:<outcome>` | answers of the second automated check (ADR 0005): `check` is `figure_alt` or `equation_match`, `stage` `final` or `cheap`, `outcome` `ok` or why there is none (`timeout`, `refused`, `budget_exceeded`, ...) |
| same | `verify:<check>:<stage>:flagged` | answers past the threshold: a figure's optional note; an equation's "would flag" (shadow, nothing changed) |
| same | `verify:<check>:<stage>:score:<tenth>` | the score in tenths (`0.0` to `0.9`): figures min(P(ok), 1 - P(factual error)), low = possible mismatch; equations P(meaning_changing), high = possible mismatch. Lets a new threshold be read off past days |
| same | `verify:cost_microusd` | the check's spend (a sum; also inside the job's `cost_microusd`) |
| `canary` | `decisions:run`, `decisions:<status>` | the day's canary claim and its result (`ok`, `drift`, `failed`, `stale`) |
| `document` | `check:<check name>:<code>` | failed static checks of the output HTML (`DocumentResult.html_checks`), e.g. `check:heading.order:skipped_level` |
| `job` | `status:done`, `status:failed` | finished jobs |
| `job` | `error:<code>` | `Job.error_code`, e.g. `error:encrypted`, `error:handwritten`; on done jobs the outcome code (`error:budget_daily`, `error:pages_not_converted`; `error:scanned_pages` before page types) |
| `page` | `page_type:<kind>` | analysed pages of a job by page type (`born_digital`, `handwritten_ocr`, `scan_ocr`, ...; `pipeline.page_type`, docs/layout.md "Hidden text and page types"): the signal for whether handwriting and scans are common enough to matter |
| `page` | `page_type_reason:<code>` | their content-free reason codes (`invisible_text`, `text_covered`, `ocr_font`, `ink_writing`, `hidden_ratio`, `text_not_covering_content`, `ink_not_analysed`, `drawings_over_cap`, ...) |
| `job` | `pages` | pages of done jobs (a sum, not a count of jobs) |
| `job` | `cost_microusd` | model spend of done jobs in millionths of a dollar (a sum): `sum(cost_microusd) / sum(pages) / 1e6` is the production cost per page |
| `job` | `latency:lt60s` ... `latency:ge600s` | done jobs by conversion wall time (buckets 60, 120, 300, 600 s) |
| `job` | `aggregate:invalid_result` | stored results that no longer parse (schema drift) |

Every part of a category must look like a code (lower case letters, digits, `_ . -`, at
most 48 characters). Anything else is stored as `other`, so even a bug that puts text in
a check code cannot write that text into this table. A rising `other` count means some
code upstream is emitting something that is not a category code.

Aggregation is idempotent: a job's counts and its `aggregated` flag are written in one
transaction, and on Postgres the job rows are locked with `FOR UPDATE SKIP LOCKED`, so two
overlapping cron runs never count a job twice.

Reading them (the Postgres service's Data tab in the Railway dashboard, or `psql` through
Railway's public TCP proxy, which you remove afterwards):

```sql
-- Most frequent failed checks in the last 7 days
SELECT step, category, sum(count) AS n
FROM error_aggregates
WHERE day >= current_date - 7 AND category LIKE 'check:%'
GROUP BY step, category ORDER BY n DESC LIMIT 20;

-- Share of equation fixes needing review, per day
SELECT day,
       sum(count) FILTER (WHERE category = 'status:needs_review')::float
         / nullif(sum(count) FILTER (WHERE category LIKE 'status:%'), 0) AS review_rate
FROM error_aggregates WHERE step = 'equation' GROUP BY day ORDER BY day;

-- Review load: required decisions and optional notes per converted page, last 30 days
SELECT sum(count) FILTER (WHERE category = 'decisions:required')::float
         / nullif((SELECT sum(count) FROM error_aggregates
                   WHERE step = 'job' AND category = 'pages' AND day >= current_date - 30), 0)
         AS decisions_per_page,
       sum(count) FILTER (WHERE category = 'tier:optional')::float
         / nullif((SELECT sum(count) FROM error_aggregates
                   WHERE step = 'job' AND category = 'pages' AND day >= current_date - 30), 0)
         AS optional_per_page
FROM error_aggregates WHERE day >= current_date - 30;

-- Model reliability: outcomes per model
SELECT category, sum(count) FROM error_aggregates
WHERE category LIKE 'call:%:outcome:%' GROUP BY category ORDER BY 2 DESC;

-- Second automated check: figure notes per checked figure, and its spend per page
SELECT sum(count) FILTER (WHERE category = 'verify:figure_alt:final:flagged')::float
         / nullif(sum(count) FILTER (WHERE category = 'verify:figure_alt:final:outcome:ok'), 0)
         AS figure_flag_rate,
       sum(count) FILTER (WHERE category = 'verify:cost_microusd') / 1e6
         / nullif((SELECT sum(count) FROM error_aggregates
                   WHERE step = 'job' AND category = 'pages' AND day >= current_date - 30), 0)
         AS check_usd_per_page
FROM error_aggregates WHERE day >= current_date - 30;

-- Production cost per converted page, last 30 days
SELECT sum(count) FILTER (WHERE category = 'cost_microusd') / 1e6
       / nullif(sum(count) FILTER (WHERE category = 'pages'), 0) AS usd_per_page
FROM error_aggregates WHERE step = 'job' AND day >= current_date - 30;
```

`readablestem ops report --days 7` prints the weekly error-analysis tables from these rows
(with `DATABASE_URL` pointing at a database you can reach, like the other `ops` commands),
including the review
load (required decisions and optional notes per page, by step) and, per step (equation,
figure, table, heading: every reviewable kind), the tier mix and the most frequent
reasons for each tier. The opted-in export's labels carry the table proposal and the
reviewed table too.

## Logs

Every line on stdout is one JSON object. Railway's log viewer and CLI treat each key as an
attribute you can filter on (`@key:value`); `severity`, `message` and `time` (UTC) are always
there, and Cloud Logging reads the same three on the Cloud Run path.

```json
{"method": "POST", "route": "/jobs/{job_id}/review", "status": 303, "latency_ms": 41,
 "logger": "readablestem.http", "time": "2026-10-07T18:02:11.204518Z",
 "severity": "INFO", "message": "request"}
```

Useful filters for the service's **Logs** panel or `railway logs --service web --filter '...'`
(syntax: docs.railway.com/observability/logs; `severity` is an ordinary attribute here):

| Question | Filter |
|---|---|
| Anything wrong? | `@severity:WARNING OR @severity:ERROR` |
| Server errors | `@message:request AND @status:>=500` |
| Slow pages | `@message:request AND @latency_ms:>5000` |
| Hourly cleanup (cron service) | `@message:aggregate_done OR @message:purge_done OR @message:aggregate_failed OR @message:aggregate_on_delete_failed` |
| Tracing problems | `@message:tracing_disabled OR @message:trace_failed` |
| Handwritten and scanned PDFs refused at upload (they never become jobs, so the `page_type` aggregates cannot count them) | `@message:upload_rejected AND (@code:handwritten OR @code:handwritten_no_text OR @code:scanned OR @code:scanned_ocr)` |
| Decisions API (OpenAI provider only) | `@message:decision_call OR @message:verifier_unavailable` (failures are WARNING; `outcome`, `status`, `code`, `request_id`) |
| Claude canary | `@message:claude_canary_ok OR @message:claude_canary_drift OR @message:claude_canary_incomplete OR @message:claude_canary_failed` |
| Decisions canary | `@message:decisions_canary_ok OR @message:decisions_canary_drift OR @message:decisions_canary_incomplete OR @message:decisions_canary_failed` |

### What can never be in a log line

Logs are about the system, not the documents. The chain in
`observability/logging.py` enforces it for every logger, ours or a library's:

- Keys that name content or personal data are dropped: `text`, `latex`, `mathml`, `alt`,
  `html`, `filename`, `email`, `content`, `caption`, `context`, `prompt`, `speech`,
  `title`, `name`, `body`, `path`, `url`, `query`. Also in the plural (`emails`), as a
  word inside a longer key (`alt_text`, `admin_emails`, `contextText`), at the end of a
  key without separators (`alttext`, `doctitle`), and in any spelling (`MathML`, `LaTeX`,
  `file_name`, `fileName`). Keys starting with `_` get no exemption. `prompt_version` is
  the one allowed exception.
- Values must be plain data. Objects become a type marker (`"<Element>"`), so a model or
  a path cannot leak through its `repr`. Strings are cut at 200 characters.
- Exceptions are logged as `error_type` plus a `stack` of `file:line:function` entries.
  The message is dropped: a pydantic error on model output quotes the LaTeX it rejected.
- Below WARNING, only `readablestem.*`, `uvicorn.error` and alembic's migration logger are
  written. Libraries that log URLs with query strings or request bodies at INFO or DEBUG
  (the OpenAI SDK's `httpx2`, `httpx`, SQLAlchemy) stay quiet even with `LOG_LEVEL=DEBUG`.
- Uvicorn's access log is off; `request_logging_middleware` logs the route template
  (`/jobs/{job_id}`), never the path, query string or headers.

Wiring it into the web app: call `configure_logging(settings)` in `create_app`, and
start uvicorn with `uvicorn.run(..., **uvicorn_log_options())` (that is,
`log_config=None, access_log=False`). Nothing else needs to call it in the server
process. Uvicorn's default `log_config` would install plain-text handlers that print the
full path and query string (the OAuth callback's `?code=...`) and tracebacks with their
messages; `configure_logging` routes uvicorn's records through the chain above and drops
its access lines whether uvicorn configures logging before or after it, but until
`configure_logging` has run at all, uvicorn's handlers are the only ones there.

Writing a log line:

```python
from readablestem.observability.logging import get_logger

log = get_logger(__name__)
log.info(
    "fix_done",
    job_id=job.id,
    element_id=fix.element_id,
    status=fix.status,
    reason=fix.reason,
    cost_usd=fix.cost_usd,
)
```

Pass ids, counts and category codes as keyword fields. Do not build a message string
out of values, and never pass document text under a neutral key name: the filter catches
names, not meaning.

Code on a standard-library logger (the web app and worker use `web.logsafe.log_event`
and `log_exception`; the OpenAI and Anthropic clients log `model_call`, `model_call_retry` and
`model_call_retry_skipped`) goes through `observability.logging.log_fields`: the message
is the event name alone and the fields become top-level keys, so
`@message:job_failed AND @error_code:internal_error` or
`@message:model_call AND @code:project_spend_limit_exceeded` work as written. `log_exception` adds `error_type` and `stack`, as the chain does for
`exc_info`.

## Decisions canary

This applies to the OpenAI provider only (the Decisions API is OpenAI's); production runs
Anthropic, so nothing here runs there. The second automated check (ADR 0005, docs/steps.md) compares the Decisions API's
probabilities with fixed thresholds. Those thresholds mean nothing once the answers
under them move, and the API is in beta: one model, no version pinning. Its answers are
deterministic, so a fixed set of requests answered differently on one day is a change
of the model or the service, seen the day it happens.

- **Requests:** `src/readablestem/ops/canary_requests.json`: 20 synthetic cases (6 figure
  checks on a bar chart and a force diagram drawn with PyMuPDF, with right, wrong and
  generic alt texts; 6 equation checks on three drawn equations, right and wrong
  LaTeX; 8 plain true/false statements). The figure and equation cases are built with
  the production question builders, so they ask what the pipeline asks. No document
  content.
- **Expected answers:** `src/readablestem/ops/canary_expected.json`, recorded live on
  2026-10-08 and checked on a second run the same day (20 of 20 identical). Each case
  stores the SHA-256 of its request as sent; `tests/test_ops_second_check.py` fails when
  the question wording, the framing or an image changes without a re-recording.
- **Alert:** an answer that moved by more than 0.02 (or a refusal that appeared or went
  away) logs one ERROR line `decisions_canary_drift` with the counts, the largest move
  and the case ids, and the command exits 1. A case that got no answer is
  `decisions_canary_incomplete` (WARNING), not drift.
- **In the HTTP cron route** (`POST /internal/cron/aggregate-and-purge`), after the purge:
  at most once per UTC day. The Railway cron service runs `ops cron` instead, which does
  not run it, so no canary runs in production unless something calls the route. The first cron run of the day claims it by inserting the
  aggregate row (day, `canary`, `decisions:run`) if it is missing, so two instances or
  two runs cannot both send it; a claimed day is not retried, even if the run failed.
  Fail-open: nothing about the canary changes the cron's response or its status code.
  Off with `DECISIONS_CANARY=false`, and never with `ENV=test`, `FAKE_MODELS` or no
  key. It reserves through a `JobBudget` of $0.02 on the daily spend cap (dollars only:
  Decisions requests are not model calls, and the budget allows none); a run costs
  about $0.00075 and takes about 3 s; cases still unsent after 60 s count as failed
  (`deadline`), so a hanging API cannot hold the cron request. `ops cron` does not run it; use the command below.
- **By hand:** `uv run --env-file .env readablestem ops decisions-canary --max-usd 0.02`
  (exit 0 no drift, 1 drift, 2 could not compare). After a deliberate change (new
  question wording, new canary cases) or a drift you have looked at and accept:
  `... ops decisions-canary --max-usd 0.02 --record`, review the diff of
  `canary_expected.json`, commit it with the change, and re-check the thresholds on
  labelled data (docs/eval.md).

No alert is wired to the drift line (the log-based alert policies in `deploy/deploy.sh` are
the Cloud Run path only). To get one on Railway, run the canary somewhere that can mail
failures, for example the weekly GitHub workflow.

## Health, readiness and shutdown

| Endpoint | Answers | Used by |
|---|---|---|
| `GET /healthz` | `{"status":"ok"}` when the process answers and a `SELECT 1` works, else 503 | Railway's deploy health check (`.railway/railway.ts`); the Dockerfile's HEALTHCHECK for local Docker |
| `GET /readyz` | 200 `{"status":"ready","checks":{...}}`, or 503 `not_ready` when any check is not ok | GitHub `uptime.yml` every 5 minutes; you, with `curl` (Cloud Monitoring's uptime check is the Cloud Run path) |
| `GET /healthz/ops` | 200 `{"status":"ok","stale":[]}`, or 503 `{"status":"stale","stale":["backup"]}` naming only the tasks that are late (`database` if the table could not be read) | GitHub `uptime.yml` every 3 hours; you, with `curl` |

`/readyz` has three checks, each one word and never a setting's value (so no key, URL or
model name can leak): `database` (`ok`, `unreachable`), `worker` (`ok`; `disabled` when
`RUN_WORKER=false`; `stopping` after SIGTERM; `stopped` if the thread died; `stalled` if
neither the loop nor a running conversion has shown life for six minutes; `not_started`),
`model_provider` (`ok`; `no_key` if the selected provider's key is empty; `unpriced_model`;
`fake` with `FAKE_MODELS`). A spent daily cap is not "not ready": the service is working
and says so on `/status`.

`/healthz/ops` says the scheduled tasks ran. The `backup` service and `readablestem ops cron`
each upsert one row of `ops_heartbeat` (name, `last_ok_at`, a short note of counts) when they
finish well; the endpoint answers 200 while the backup is fresher than 26 hours (a day and two
hours of slack) and the cron fresher than 150 minutes (two missed runs). The migration that makes the table
seeds both names with the time it ran, so the first deploy does not start with the check failing;
a task that never reports goes stale on schedule. A task with no row at all counts as late. It is public and content-free like `/readyz`, with no rate limit of its
own (a read of two rows), and it is not in the access log (`logging._QUIET_ROUTES`).

**Alerts without a vendor** (`.github/workflows/uptime.yml`, needs the repository variable
`LIVE_URL`). GitHub emails a failed scheduled run to the user who last edited the cron lines of
`uptime.yml` (not necessarily the repository owner; Settings > Notifications > Actions, for that
account). Whoever should get the mail should be the one who commits any change to those lines. Every 5 minutes one job calls `/readyz` (with two retries), so an outage
is mailed within about ten minutes, then once per failed run until it is fixed. Every 3 hours a
second job calls `/healthz/ops` and `/demo` and prints the 503 body, which names the stale task;
three hours keeps a late backup or cron to a few mails a day instead of one per five minutes, and
finds a missed cron within five hours and a missed backup within 29. Two limits to know:

- GitHub **disables scheduled workflows in a public repository after 60 days with no commit** (it
  mails a notice when it does). With no commits there are no checks and no alerts. After a long
  quiet spell open the Actions tab, choose "Uptime", and press "Enable workflow", or push any
  commit. Dependabot's weekly PRs normally keep it alive, but only once merged.
- Runs start late when GitHub is busy, so "every 5 minutes" is a target, not a promise.

When the 503 names `cron`: look at the `cron` service's deployments (`ops cron` failing, or the
service stopped). When it names `backup`: look at its latest deployment for `backup_failed
reason=...` (the table in "Backups and restore").

**Shutdown.** On a deploy Railway sends SIGTERM to the old deployment once the new one is
live, then SIGKILL after `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` (docs.railway.com, "Deployment
Teardown"). Production sets it to 10 seconds (`deploy.drainingSeconds` on `web` in `.railway/railway.ts`;
Railway's default is 0), the same window Cloud Run gave. The app does what it can in the time it is given: it stops the worker at once (no new
job is claimed, the running conversion is cancelled so no more model calls are sent, and its
lease ends now so another run requeues it immediately instead of after two minutes). Then uvicorn
drains open connections for at most 5 seconds (`GRACEFUL_SHUTDOWN_S`) and the app's shutdown
makes sure the worker is stopped (3 seconds). If the process is killed before the lease is
ended, the lease runs out and the job is requeued by the next worker. The requeued job restarts
from the beginning on another attempt (three attempts end in `lease_expired`). Look for
`sigterm_received`, `job_cancelled reason=shutdown` and `worker_stopped` in the logs. A conversion
runs in a child process (below): cancelling it kills the child within 0.2 seconds, so shutdown
leaves nothing running.
`tests/test_web_health.py` starts a real server, sends SIGTERM, and checks it exits well inside
10 seconds.

**The conversion child.** Each conversion runs in a child process (`python -m
readablestem.web.conversion_child`, started by `web/isolated_convert.py`), so a PDF that crashes,
exhausts memory or hangs MuPDF fails that one job and the web process, its sessions and the
worker go on. The child is limited to `CONVERSION_MEMORY_MB` of address space (default 3072, plus
twice the PDF; resident memory is far lower, 330-460 MB measured) and 4 times the job's time limit
in CPU seconds, and is killed at the job's time limit, when the job is deleted or taken over, and
at shutdown. Web service and child share the service's RAM: while a job converts, expect the web
process plus one child; a service limit below about 1 GB would not hold both (docs/deploy-railway.md,
"Memory"). Do not set `CONVERSION_MEMORY_MB` below 1536: the table detector's ONNX runtime then
hangs instead of failing, and the job ends as `timeout`. What to look for:

| Log line or job code | Meaning |
|---|---|
| `job_failed error_code=conversion_crashed`, preceded by `conversion_child_died signal=6` (abort), `11` (segfault) or `exit_code=N` | MuPDF or another native library killed the child. That file will do it again: nothing to retry (the job page does not offer "Try again"). Keep the document id; reproduce with `readablestem convert` on a copy only if the owner agrees |
| `error_code=conversion_oom` (after `conversion_child_retry` when it was `signal=9`) | The child ran out of memory (its cap), or was SIGKILLed from outside twice. Raise `CONVERSION_MEMORY_MB` only if documents that should fit are failing; a single large file is better split |
| `error_code=conversion_unavailable`, `conversion_child_unavailable` (ERROR, with `error_type` or `exit_code`), `conversion_child_check_failed` at start | The child itself cannot run (an import, a library, the table model file at `TABLE_MODEL_DIR`), not the PDF. Every job fails this way until it is fixed; the job page offers "Try again". Check the image and the volume, then redeploy |
| `conversion_child_retry` | The child was killed by a SIGKILL this process did not send; the conversion was run once more. `cgroup_oom_kills` on `conversion_child_died` says whether the kernel's OOM killer did it (cgroup v2). Frequent ones mean the service is short of memory: raise its allowance, not `CONVERSION_MEMORY_MB` |
| `conversion_child_frame_refused` | The child's result was over 128 MB: the job fails `conversion_crashed` |
| `conversion_child_timeout` | The converter's own backstop fired (the worker's limit normally does first: `job_timeout`, `error_code=timeout`). `conversion_child_died signal=24` is the CPU limit |
| `conversion_child_protocol` | The child sent something that is not the protocol (a bug, or a compromised child): the job is failed `conversion_crashed`. Treat repeated ones on one account as an incident |
| `conversion_child_unreaped` | A child did not die within 5 s of SIGKILL (a process stuck in the kernel); check the host |
| `conversion_abandoned` | No longer expected for a hung MuPDF; it would mean a callback in the web process (the database) hung |

A crashed or out-of-memory child fails the job at once; it is not requeued, so one poison PDF
costs one attempt and not three lease expiries. `PRELOAD_TABLE_MODELS` has no effect while
conversions run in a child: each child loads what it needs (a job pays about 1.4-2 s for the child's start and
imports on the development box, more on 1 vCPU, and with `FEATURE_TABLES` on the table models
load in about 4-5 s on 1 vCPU; docs/web.md, "The conversion runs in a child process"). Tracing records the web
process's spans only.

**Error pages.** Every page that fails answers in the app's plain style with a way home:
404 "Page not found", 500 "Something went wrong" (the same page whether the error was
raised or unhandled, with no document text), 503 "Not available right now". When the
database does not answer (connection refused, pool exhausted), the 503 page is a static
file, because the usual error page looks the signed-in user up in that database; it
carries `Retry-After: 30` and links to `/status`. Only the exception type is logged
(`database_unavailable`), never its message.

## Backups and restore

**What is in the database, and what a backup is for.** Documents and everything made from
them (uploads, crops, results, review edits) are deleted after `RETENTION_HOURS` (24), so
a backup mostly protects what is not temporary: accounts (the allowlist and, with open
sign-up, everyone who signed up) and admins, courses and
notation memory, the daily spend and quota counters, the content-free aggregates and usage
events, and opted-in documents (30 days). Losing the lot would cost you the allowlist
(re-add the instructors) and some history; instructors would upload again, and people who
signed themselves up would sign in again and get a new account.

An account deleted by its owner is gone from the database at once and stays in the backups
taken before, until they are dropped (the privacy notice says so for documents; the same holds
for the account row).

**What production runs.** The `backup` service (`.railway/railway.ts`): daily at 08:17 UTC it
runs `pg_dump` of the Postgres service into `/backups/readablestem-<date>.sql.gz` on its own
volume, restores that file into a scratch database `restore_check`, compares it with the live
database, drops the scratch database and logs `backup_ok file=... tables=... alembic=...`. The
command is a bash script (`set -euo pipefail`, kept readable as `backupScript` at the top of
`.railway/railway.ts` and tested by `tests/test_railway_backup_script.py`: `bash -n`, shellcheck,
and runs against stub `pg_dump` and `psql`). The dump is written to a `.part` file and moved into
place only after every check has passed: `gzip -t` reads it back, it ends with pg_dump's
`-- PostgreSQL database dump complete` line (a dump cut short does not), it restores with
`ON_ERROR_STOP`, every public table of the live database exists in the restore and
`alembic_version` matches. Any failure removes the `.part` file, prints `backup_failed
reason=...` and exits 1, so the run shows as failed and the heartbeat stays old. Reasons: `dump`,
`gzip_test`, `dump_read`, `dump_incomplete`, `restore_prepare`, `restore`, `read_live`,
`read_restore`, `no_tables`, `missing_tables`, `alembic_mismatch`, `restore_cleanup`, `move`,
`heartbeat` (the dump was kept but `ops_heartbeat` could not be written, usually because the
migration that makes the table has not run). Only after a good run does it upsert the `backup`
heartbeat, then delete dumps older than 5 days (and `.part` leftovers older than a day), so the
last 6 are kept. There is no
point-in-time recovery and Railway's own scheduled volume backups are not used (they need a Pro
workspace). That is deliberate: every dump holds the documents present when it ran, so keeping
more would stretch "deleted after 24 hours" past the "about a week" the privacy page promises
(deploy-railway.md, "As deployed"). The cost is a restore point up to a day old. If you want more,
change the privacy page first.

**Check it** when `/healthz/ops` names `backup`, and now and then by hand: open the `backup`
service's latest deployment in Railway and look for a `backup_ok` line from today, with `tables=`
and `alembic=` as expected (`ls -l /backups` at the end of the log lists the dumps kept). A run
that failed shows in the deployments list and as `backup_failed reason=...`.

**Restoring for real.** Restore into an empty database, never over the live one without a
decision (deploy-railway.md, "As deployed", has the commands: `railway ssh --service backup`, then
`gunzip -c /backups/readablestem-<date>.sql.gz | psql "$PGURL"`). Then run the cron right after
(redeploy the `cron` service): the restore brings back documents that retention had already
deleted, and the purge removes them again. Then replay the erasures (next paragraphs), run
`uv run readablestem ops backup-check` against the restored database and sign in.

**Accounts erased after the backup come back with it.** Erasing an account (the Account page,
`readablestem users delete`) writes a row to `erasure_ledger`: a SHA-256 of the user id and the
time, nothing that says who it was, kept for ever. The backup has the ledger as it was when it
ran, so its newer entries are missing. To erase the people who asked after the backup was made:

1. **If the current database can still be reached**, before restoring, export its ledger:
   `psql "$OLD_DATABASE_URL" -c "\copy erasure_ledger to 'ledger.csv' csv header"`.
2. Restore the dump (above).
3. Replay, against the restored database:
   `DATABASE_URL=... uv run readablestem ops replay-erasures --ledger-csv ledger.csv`
   (leave the option out when there is no export). It merges the file into the ledger, then erases
   every account whose hashed id is in it, with the same code as a deletion request, and prints
   `ledger_imported=N accounts_erased=M`. Running it twice does nothing the second time; it never
   touches an account the ledger does not name.

What this does not cover: if the database is lost entirely and the newest dump is the only copy,
the ledger entries written after that dump are gone with it, and the people who erased their
accounts in that window (up to a day, plus whatever the last good dump missed) are back with
their accounts, though not their documents (those expire after 24 hours anyway). Nothing in this
repository can know about them; if you have a record of such requests (email), run
`readablestem users delete EMAIL --yes` for each. Sign-in identities, courses and notation memory
return with the account until you do. Usage events, accounts and all other rows of the person
are removed by the replay in full, as by a normal deletion.

`backup-check` reads the copy and prints table names and row counts only. It exits 1 unless
the schema is at this release's migration head (`schema=head`; `ahead` means a newer
release migrated it, fine for a rollback), every table exists, and at least one admin can
sign in. It also prints the age of the newest job and the newest spend day, which should
match the day the backup was taken.

**Any Postgres: the pg_dump drill.** `readablestem ops restore-drill` takes the database in
`DATABASE_URL`, runs `pg_dump` (custom format) into a private temporary directory, runs
`pg_restore` into a scratch database on the same server, runs the same checks as
`backup-check` on the scratch copy, compares the row counts with the source and drops the
scratch database and the dump file whatever happens. It needs the PostgreSQL client tools
on the machine (`--pg-bin DIR` if they are not on PATH) and a user allowed to create
databases (the Postgres service's own user on Railway, reached through its public TCP proxy,
which you remove afterwards). The output is counts only. The `backup` service does the same
by shell commands every day. `tests/test_ops_backup.py` runs both commands against a real
Postgres.

## Open sign-up (`SIGNUP_MODE=open`)

What to look at, and what to do. Setting and design: docs/web.md, "Sign-up modes and access
tiers".

- **The Admin page** shows "New accounts today (UTC)" against `MAX_NEW_ACCOUNTS_PER_DAY`,
  accounts by tier and blocked, the open pool's spend, and every account with its tier. A day
  that fills the account count early, or the pool by mid-morning, is the thing to notice; the
  public `/status` page says when conversions are paused (it does not distinguish the pool).
- **The day's cap.** The first time in a UTC day that `MAX_NEW_ACCOUNTS_PER_DAY` turns someone
  away the app logs `signup_cap_reached` (a WARNING with `code=daily_cap`, nothing about the
  person), once per day however many sign-ins are refused (`signup_days.cap_warned`), and the Admin
  page shows a notice above the sign-up numbers until midnight UTC. Nothing mails you: look at the
  Admin page, or search the logs for `signup_cap_reached`.
- **Spend by person.** The Admin page lists the 20 biggest spenders today, and over all days kept,
  by the same pseudonym as the usage tables (`u-` and the start of the user id), never next to an
  email. The figure is `quota_days.spent_usd`, added when a model call settles, for the person who
  owns the job (conversions and reads from review; a hold that was given back and settled late
  still counts). It lives and dies with the person's `quota_days` rows (deleted when they are
  erased). To see who a pseudonym is, match the id prefix in the database. The global daily total
  stays in `spend_days`; spend recorded before this release has no person.
- **Logs.** `signup` (one per new account, provider only), `login_refused` with `reason` =
  `blocked`, `signups_full` or `not_allowlisted`, `rate_limited` with `rule=signup`,
  `upload_rejected` with `code=open_pool`, `reread_refused` with `scope=allowance` and
  `account_deleted`. No address or name in any of them.
- **Block someone** on the Admin page ("Block" next to an open-tier account, "Remove" next to an
  invited one) or with `readablestem users deny EMAIL`. They are signed out everywhere, their
  queued and running jobs are stopped with `access_removed`, and sign-ins are refused without
  making a new account. `readablestem users unblock EMAIL` or the Unblock button lets them back
  with the tier they had.
- **Stop new accounts now** by setting `MAX_NEW_ACCOUNTS_PER_DAY=0` (people who already have an
  account carry on) and redeploying or restarting. `SIGNUP_MODE=allowlist` does the same and
  more: nobody new can sign in at all. Neither signs anyone out.
- **Pause strangers' spending** by setting `OPEN_TIER_DAILY_SPEND_CAP_USD=0`: their uploads are
  refused, their queued jobs wait, invited people are unaffected.
- **Deletion requests.** A person can delete their own account (Account page). If they cannot sign
  in, `readablestem users delete EMAIL --yes` does the same. Backups keep the row until they roll
  over.
- **A day's accounts are counted once.** Deleting an account does not give its place back, and an
  address that deleted its account today comes back with the day's pages, reads and place already
  used (`deleted_accounts`, a keyed hash of the address, purged the next day). The per-address rule
  (5 an hour) and the pool bound what a determined person can still do with many addresses.
- **Spend holds.** `holds_released` (a warning, with a count and dollars) means a dead process's
  holds were given back, at start-up, by the hourly backstop or when a lease ran out; one after a
  deploy is normal. Holds are rows of `spend_holds`; a row older than a day with the worker running
  is a bug.

## Tracing (Langfuse)

Off unless `TRACING_ENABLED=true` and both Langfuse keys are set (the Langfuse keys are Railway variables). If the `tracing` extra is missing
or the client cannot start, the app logs `tracing_disabled` with a `reason` and carries
on without traces. Keep it off for any run on `private-inputs/`.

Where the tracer comes from: `create_app` calls `make_tracer(settings)` once and the
worker passes it to every `convert_pdf` (`app_started` logs `tracing=true|false`); it is
flushed when the app shuts down. `readablestem convert` does the same for one run. With
tracing off the converter gets no tracer at all.

Retention: traces hold ids, codes, token counts and costs, never document content, but
they live in Langfuse, outside the app's hourly purge. TODO(Harish): before turning
tracing on in production, set the Langfuse project's data retention (Project settings >
Data retention, if your plan has it) to match `RETENTION_HOURS` or as short as allowed,
and make the privacy page say where traces go and for how long (it does when
`TRACING_ENABLED=true`; check the wording).

What a span can carry is an allowlist (`ALLOWED_ATTRIBUTES` in
`observability/tracing.py`): ids, step, kind, model (or a comma-separated list of the
models a step used), prompt version, attempt, fallback flag, status, outcome, reason,
error type, confidence, token counts, `cost_usd`, `latency_ms`, page numbers, plus
counts (`calls`, `model_calls`, `elements`, `tasks`, `fixes`, `count`; numbers only).
String values must look like codes. It is enforced three times: when the span is written,
in the Langfuse `mask` hook (inputs and outputs become `[masked]`), and in the export
hook, which deletes any other attribute before anything leaves the process. For code
that calls the Langfuse SDK directly, the client also drops span status descriptions,
events and link attributes before export, renames spans whose name is not code-like, and
has Langfuse's media upload (which would send base64 images found in a payload straight
to Langfuse) switched off.

Span handles take attributes with `.set(...)` or `.update(...)` (the same method), also
after the span started. A span opened with `model=`, or with `step=` one of `equation`,
`figure`, `judge`, is a Langfuse generation, so the model, tokens and `cost_usd` set on
it later show up in Langfuse's cost views. Exceptions are recorded as level ERROR with
the exception type only.

Spans started in worker threads nest under the job's span only if each task runs in its
own copy of the caller's context (`pool.submit(contextvars.copy_context().run, work)`);
otherwise each step shows up as its own trace. `pipeline.run` does this.

## Sandbox probe

`readablestem ops sandbox-probe` says whether the sandbox for uploaded TeX (`core/sandbox.py`,
docs/security.md, "Running uploaded TeX") works on the machine it runs on. Platforms may forbid the
unprivileged user namespaces bubblewrap needs, so run it on the deployed image, not on a laptop:

```
railway ssh -s web -- readablestem ops sandbox-probe            # name=value lines, then PASS or FAIL
railway ssh -s web -- readablestem ops sandbox-probe --require-latexml
```

Exit status 0 is PASS, 1 is FAIL. The checks, one line each: `bwrap`, `prlimit`, `sandbox_start`;
inside the sandbox `sandbox_env` (empty environment), `sandbox_no_secrets` (a variable set in the
probe's own environment is absent), `sandbox_no_etc` (no `/etc/passwd`, `/app`, `/home`),
`sandbox_pid1_environ` (empty), `sandbox_no_network`, `sandbox_output_writable`,
`sandbox_root_readonly`; the caps `sandbox_memory_cap`, `sandbox_wall_cap`, `sandbox_output_cap`,
`sandbox_task_cap`, `sandbox_file_cap`; `sandbox_total_bound_mb` (the cgroup's memory limit, else tasks x address space of `source.latexml.LIMITS`: 2048) and `sandbox_total_bound` (`fail` when it is over `SOURCE_SANDBOX_MAX_TOTAL_MB`, which fails the probe with `--require-latexml`); `sandbox_seccomp` (the filter denies `ptrace` inside;
`unsupported` on a machine other than x86-64 and arm64, which does not fail the probe);
and `latexml` (`ok` with `latexml_seconds`, or `missing` when the image was built without
LaTeXML; `--require-latexml` turns `missing` into a failure). Information lines that never fail
the probe: `sandbox_cgroup` (`ok` when the container delegates a cgroup v2, so memory and tasks are also capped for the whole group; `unavailable`: the memory cap is then per process), `seccomp_mode`, `landlock_abi`, whether a same-UID process can read another's
`/proc/<pid>/environ` with and without `PR_SET_DUMPABLE=0`, and `fallback_unshare_net` (whether a
plain child could cut its own network; it would still see every file, so it is not used). If it
fails, keep `FEATURE_LATEX_SOURCE` off; the alternative is a separate service with no secrets.
Nothing it prints is document content.

## When something goes wrong

| Symptom | Look at | Usual fix |
|---|---|---|
| The GitHub uptime `/readyz` run failed, or a Railway deploy or crash notification | `@severity:WARNING` logs; the web service's deployments list | Roll back to the last good deployment (deploy-railway.md, "Ship a new version") |
| `/healthz` answers 503, or `/readyz` shows `database: unreachable` | The database: the Postgres service's state and connections. Users meanwhile see a calm "Not available right now" page (503 with `Retry-After`) | See "Too many connections" below |
| `/readyz` shows `worker: stalled` or `stopped`, or documents wait for ever | `worker_loop_error`, `lease_refresh_failed` and `conversion_abandoned` in the logs (a hung MuPDF no longer stalls the worker: its child is killed at the time limit) | The worker thread is stuck or dead; documents are queued but nothing converts. Redeploy the web service (`railway up --service web`, or Redeploy on its latest deployment) to replace the process; the running job's lease expires and it is requeued |
| `/readyz` shows `model_provider: no_key` or `unpriced_model` | `ANTHROPIC_API_KEY` is set on the web service; `ANTHROPIC_MAIN_MODEL` names a model in `models/pricing.py` | Set the variable and redeploy, or fix the model name |
| Conversions fail with `reason:budget_daily`, jobs end `error:budget_daily`, or `claims_paused` in the logs | Spend today vs `DAILY_SPEND_CAP_USD` | Working as designed: users see "Stopped early" or "Waiting", and queued jobs start after 00:00 UTC. Raise the cap only on purpose |
| Conversions fail with provider errors | `call:<model>:outcome:api_error` counts; OpenAI or Anthropic dashboard | The OpenAI project limit may be reached (HTTP 429 `project_spend_limit_exceeded`); with Claude, the workspace limit or rate limit (HTTP 429 `rate_limit_error`), or a key not scoped to a workspace (HTTP 400, set `ANTHROPIC_WORKSPACE_ID`) |
| The `cron` service's run failed, or `purge_backstop_deleted` in the web logs | The `cron` service's deployments list and logs; `@message:purge_backstop_deleted OR @message:aggregate_failed` | The worker's backstop keeps purging meanwhile, so documents are still deleted on time; fix the cron anyway. `aggregate_failed` (with `error_type` and `stack`): the purge still ran, so fix the bug; jobs stay unaggregated until then. A job that expires first is aggregated as it is deleted, or, if that fails too (`aggregate_on_delete_failed`), counted in `unaggregated_deleted` |
| `/healthz/ops` is 503 naming `backup`, or no `backup_ok` line today | The `backup` service's latest deployment log: `backup_failed reason=...` ("Backups and restore") | Read the reason (a full `/backups` volume, a database that cannot be reached, a dump that was cut short or did not restore, a missing table). The previous good dump is kept until it ages out; fix it before then |
| `/healthz/ops` is 503 naming `cron` | The `cron` service's latest deployment and logs | `ops cron` is failing (`aggregate_failed`, a database error) or the service has stopped; fix it and redeploy the service, or run `readablestem ops cron` once by hand |
| The GitHub "Uptime" workflow stopped running, or its last run is over 60 days old | The Actions tab: a "disabled" banner | GitHub turns off scheduled workflows in a public repository after 60 days with no commit. Press "Enable workflow" |
| `unaggregated_deleted` above 0 | The cron history | Some statistics are lost for those jobs; nothing else to do |
| Purge itself failing | `purge_done` missing for over an hour (from both `source=cron` and `source=worker`), or `purge_backstop_failed` | Treat as a privacy incident: run `readablestem ops purge` by hand, then fix the cause. Job pages already answer 404 once `retain_until` has passed, purged or not |
| Too many connections | The Postgres service's connection count (`max_connections`, `SHOW max_connections`) | The web process pools up to 10 connections (5 plus 5 overflow; the routes are synchronous, so each request in a query holds one) and the cron service opens its own for a few seconds an hour, the backup's `psql` a few more once a day; Railway's Postgres allows 100 by default (`max_connections`), so one web replica uses a tenth. Two replicas would still fit, but the rate limits and the upload gate are per process |
| The GitHub uptime run failed | The run's log (`/readyz` job or `/healthz/ops` job) | For `/readyz`: the rows above and the custom domain and certificate. For `/healthz/ops`: the two rows above for `backup` and `cron` |
| `decisions_canary_drift` (OpenAI provider, run by hand) | The line's `drifted_cases` and `max_delta`; `readablestem ops decisions-canary --max-usd 0.02` to see each moved answer | The Decisions API's answers changed under the thresholds. Conversions are unaffected (the check is a note or a shadow and fails open); to stop the figure notes meanwhile set `DECISIONS_FIGURE_VERIFY=false`. Re-measure the thresholds (docs/eval.md), then re-record the canary |
| Many `decision_call` WARNINGs or `verify:*:outcome:timeout` counts | `outcome`, `status`, `code` on the lines | Decisions API trouble. Nothing to do for conversions (fail-open); the check's answers are missing for those fixes |

## CI and dependency updates

`ci.yml` runs on every push to `main` and every pull request:

| Job | What it checks |
|---|---|
| `static` (3.12, 3.14) | `ruff check`, `ruff format --check`, `ty check src`, and an import of every module |
| `pytest` (shards) | `pytest -n auto --shard i/4` (offline, parallel; Postgres tests use the embedded server on 3.12). 3.12 always; 3.14 in full on main, nightly and dependency or workflow changes, otherwise one smoke shard. Rules and sharding: [development.md](development.md), "CI" |
| `shard-coverage` | Every collected test ran in exactly one shard |
| `ci-passed` | Fails if any other job failed or was cancelled; the one check to require in branch protection |
| `postgres` | `alembic upgrade head` and `alembic check` (models match migrations), then `pytest -m postgres`, against a Postgres 18 service container (the major the backup image uses). Zero collected tests fails the job |
| `browser` | `pytest -m browser -n 3 --dist loadfile --shard i/2` (two jobs) with Playwright Chromium (axe-core checks); the browsers and axe-core are cached; zero collected tests fails the job |
| `dependency-audit` | `pip-audit` over the exported lock (all groups and extras); any known vulnerability fails the job. An unfixable one needs a commented ignore entry in the workflow |
| `CodeQL` (own workflow) | CodeQL for Python and GitHub Actions on push to main, on pull requests and weekly |
| `docker` | Builds the image (no push) with BuildKit and again with the legacy builder (`DOCKER_BUILDKIT=0`, as the Cloud Run path's Cloud Build `--tag` build may use), and checks the entry point and non-root user |
| `eval-gate` | `readablestem eval gate --require-current` for every committed `data/eval/baseline-<task>.json` against its `latest-<task>.json` (skips while none is committed). Fails on a regression, a changed judge, or a latest report the current prompts did not produce |
| `secret-scan` | gitleaks over the full history |

Every action is pinned to a commit SHA with its tag in a comment; Dependabot proposes
updates weekly. `eval-guard.yml` fails a pull request that changes a model constant or a
prompt without the matching `data/eval/latest-*.json` (label `eval-unchanged` or trailer
`Eval-Unchanged: <reason>` to opt out). `eval-live.yml` (costs money; weekly and by hand)
runs every eval task against the production provider with a hard cap per task (at most
$2.95 a week), gates each report against its baseline and uploads it as an artifact; it
skips with a notice while `ANTHROPIC_API_KEY` is not set. Both: `docs/eval.md`.
