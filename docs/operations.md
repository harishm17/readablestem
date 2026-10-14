# Operations

How the deployed app looks after itself, what it records, and what to do when something
goes wrong. First deploy, rollback and secret rotation: [deploy.md](deploy.md).

## What runs on a schedule

| What | When | Where |
|---|---|---|
| Aggregate, then purge | Hourly, Cloud Scheduler job `a11y-stem-cron` | `POST /internal/cron/aggregate-and-purge` (needs the `X-Cron-Token` header); logs `cron_done` |
| Decisions canary | At most once per UTC day, inside the hourly cron, after the purge | `ops.canary.maybe_run_daily`: 20 fixed requests to the Decisions API, compared with committed answers; `decisions_canary_drift` (ERROR) on any move over 0.02. See "Decisions canary" below |
| Claude canary | At most once per UTC day, inside the hourly cron, after the Decisions canary | `ops.claude_canary.maybe_run_daily`: the Claude second check asked about 12 synthetic cases with known answers; `claude_canary_drift` (ERROR) when more than `CLAUDE_CANARY_MAX_MISSES` are wrong. See docs/steps.md "Second check on Claude" |
| Purge backstop | Hourly, the worker thread (first run 10 minutes after start) | `purge_expired` again, logged `purge_done source=worker`; `purge_backstop_deleted` (WARNING) if it found overdue jobs, i.e. the scheduler is late |
| Uptime check of `/readyz` | Every 5 minutes, Cloud Monitoring | Emails `ALERT_EMAIL` when the database, the worker thread or the model provider setting is not ok for 10 minutes |
| Cron alerts | Cloud Monitoring (log-based metrics) | Emails `ALERT_EMAIL` when there is no `cron_done` for 2 hours, or on any `cron_refused`, `aggregate_failed`, `purge_backstop_deleted`, `purge_backstop_failed` or failed Cloud Scheduler attempt |
| Outside-in check of `/readyz` and `/demo` | Weekly, GitHub Actions `uptime.yml` | Skips unless the repo variable `LIVE_URL` is set |
| Dependency updates | Weekly, Dependabot | One grouped PR per ecosystem (uv, actions, Docker) |

The same tasks run by hand:

```sh
gcloud scheduler jobs run a11y-stem-cron --location="$REGION" --project="$PROJECT_ID"

# or from a machine that can reach the database (prints counts only):
DATABASE_URL=postgresql+psycopg://... uv run a11y-stem ops cron
uv run a11y-stem ops aggregate
uv run a11y-stem ops purge
uv run --env-file .env a11y-stem ops decisions-canary --max-usd 0.02   # no database needed
uv run a11y-stem ops launch-check      # unfinished copy, demo, baseline, settings and spend limits
uv run a11y-stem ops backup-check      # DATABASE_URL could run the app? ("Backups and restore")
uv run a11y-stem ops restore-drill     # pg_dump, restore into a scratch database, check, drop
```

`ops cron` exits 1 if aggregation failed, but only after the purge has run.

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

What survives deletion: users and the allowlist, the daily spend and quota counters, the
content-free aggregates below, the usage events (next section), and database backups. A
backup holds every document that existed when it ran, so a deleted document stays in the
backups taken before its deletion until they are dropped. The deploy keeps one daily
backup and no point-in-time-recovery logs, so that is up to about a day after the purge
([deploy.md](deploy.md), "Backups and deleted documents"); the privacy page has to say so
(or backups have to be off).

### Large files waiting for their pages

A PDF over the page or size limit is kept as a `pending_uploads` row (`web/pending.py`, docs/web.md
"Large files in parts") while its owner chooses which pages to convert. It is a copy of the
upload, so it follows the same promise: `retain_until` is `RETENTION_HOURS` after it was
made, the same hourly purge deletes it (`ops.retention.purge_pending`, logged as
`pending_deleted` in `purge_done`), "Discard this file" deletes it at once, and so does
`erase_user`. Past its time it answers 404 before the purge runs. The sample document
(`web/sample.py`) is an ordinary finished job with `is_sample` set, deleted by the same clock.

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
- **With the user.** `a11y-stem users delete` (`ops.retention.erase_user`) deletes all of
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
| same | `tier:<tier>` | fixes by review tier as the pipeline left them (`required`, `optional`, `none`; `Fix.review`, see docs/design/beta-architecture.md "Review tiers") |
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
| `page` | `page_type_reason:<code>` | their content-free reason codes (`invisible_text`, `text_covered`, `ocr_font`, `ink_writing`, `hidden_ratio`, `ink_not_analysed`, ...) |
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

Reading them (Cloud SQL Studio in the console, or `psql` through the Cloud SQL Auth
Proxy):

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

`a11y-stem ops report --days 7` prints the weekly error-analysis tables from these rows
(through the Cloud SQL Auth Proxy, like the other `ops` commands), including the review
load (required decisions and optional notes per page, by step) and, per step (equation,
figure, table, heading: every reviewable kind), the tier mix and the most frequent
reasons for each tier. The opted-in export's labels carry the table proposal and the
reviewed table too.

## Logs

Every line on stdout is one JSON object. Cloud Logging reads `severity`, `message` and
`time` (UTC) from it; everything else lands in `jsonPayload`.

```json
{"method": "POST", "route": "/jobs/{job_id}/review", "status": 303, "latency_ms": 41,
 "logger": "a11y_stem.http", "time": "2026-10-07T18:02:11.204518Z",
 "severity": "INFO", "message": "request"}
```

Useful Logs Explorer queries (add `resource.type="cloud_run_revision" AND
resource.labels.service_name="a11y-stem"`):

| Question | Query |
|---|---|
| Anything wrong? | `severity>=WARNING` |
| Server errors | `jsonPayload.message="request" AND jsonPayload.status>=500` |
| Slow pages | `jsonPayload.message="request" AND jsonPayload.latency_ms>5000` |
| Hourly cleanup | `jsonPayload.message=("aggregate_done" OR "purge_done" OR "aggregate_failed" OR "aggregate_on_delete_failed")` |
| Tracing problems | `jsonPayload.message=("tracing_disabled" OR "trace_failed")` |
| Handwritten and scanned PDFs refused at upload (they never become jobs, so the `page_type` aggregates cannot count them) | `jsonPayload.message="upload_rejected" AND jsonPayload.code=("handwritten" OR "handwritten_no_text" OR "scanned" OR "scanned_ocr")` |
| Decisions API | `jsonPayload.message=("decision_call" OR "verifier_unavailable")` (failures are WARNING; `outcome`, `status`, `code`, `request_id`) |
| Claude canary | `jsonPayload.message=("claude_canary_ok" OR "claude_canary_drift" OR "claude_canary_incomplete" OR "claude_canary_failed")`, and `jsonPayload.claude_canary` on `cron_done` |
| Decisions canary | `jsonPayload.message=("decisions_canary_ok" OR "decisions_canary_drift" OR "decisions_canary_incomplete" OR "decisions_canary_failed")`, and `jsonPayload.decisions_canary` on `cron_done` |

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
- Below WARNING, only `a11y_stem.*`, `uvicorn.error` and alembic's migration logger are
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
from a11y_stem.observability.logging import get_logger

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
`jsonPayload.message="job_failed" AND jsonPayload.error_code="internal_error"` or
`jsonPayload.message="model_call" AND jsonPayload.code="project_spend_limit_exceeded"`
work as written. `log_exception` adds `error_type` and `stack`, as the chain does for
`exc_info`.

## Decisions canary

The second automated check (ADR 0005, docs/steps.md) compares the Decisions API's
probabilities with fixed thresholds. Those thresholds mean nothing once the answers
under them move, and the API is in beta: one model, no version pinning. Its answers are
deterministic, so a fixed set of requests answered differently on one day is a change
of the model or the service, seen the day it happens.

- **Requests:** `src/a11y_stem/ops/canary_requests.json`: 20 synthetic cases (6 figure
  checks on a bar chart and a force diagram drawn with PyMuPDF, with right, wrong and
  generic alt texts; 6 equation checks on three drawn equations, right and wrong
  LaTeX; 8 plain true/false statements). The figure and equation cases are built with
  the production question builders, so they ask what the pipeline asks. No document
  content.
- **Expected answers:** `src/a11y_stem/ops/canary_expected.json`, recorded live on
  2026-10-08 and checked on a second run the same day (20 of 20 identical). Each case
  stores the SHA-256 of its request as sent; `tests/test_ops_second_check.py` fails when
  the question wording, the framing or an image changes without a re-recording.
- **Alert:** an answer that moved by more than 0.02 (or a refusal that appeared or went
  away) logs one ERROR line `decisions_canary_drift` with the counts, the largest move
  and the case ids, and the command exits 1. A case that got no answer is
  `decisions_canary_incomplete` (WARNING), not drift.
- **In the hourly cron** (`POST /internal/cron/aggregate-and-purge`), after the purge:
  at most once per UTC day. The first cron run of the day claims it by inserting the
  aggregate row (day, `canary`, `decisions:run`) if it is missing, so two instances or
  two runs cannot both send it; a claimed day is not retried, even if the run failed.
  Fail-open: nothing about the canary changes the cron's response or its status code.
  Off with `DECISIONS_CANARY=false`, and never with `ENV=test`, `FAKE_MODELS` or no
  key. It reserves through a `JobBudget` of $0.02 on the daily spend cap (dollars only:
  Decisions requests are not model calls, and the budget allows none); a run costs
  about $0.00075 and takes about 3 s; cases still unsent after 60 s count as failed
  (`deadline`), so a hanging API cannot hold the cron request. `ops cron` (the by-hand cron) does not run it; use the command below.
- **By hand:** `uv run --env-file .env a11y-stem ops decisions-canary --max-usd 0.02`
  (exit 0 no drift, 1 drift, 2 could not compare). After a deliberate change (new
  question wording, new canary cases) or a drift you have looked at and accept:
  `... ops decisions-canary --max-usd 0.02 --record`, review the diff of
  `canary_expected.json`, commit it with the change, and re-check the thresholds on
  labelled data (docs/eval.md).

TODO(Harish): the drift line is an ERROR in Cloud Logging, but no alert policy emails it
yet. To be told by email, add `decisions_canary_drift` to the
`$SERVICE-cron-problems` log metric in `deploy/deploy.sh` (it then reports as a "cron or
purge problem"), or give it a policy of its own.

## Health, readiness and shutdown

| Endpoint | Answers | Used by |
|---|---|---|
| `GET /healthz` | `{"status":"ok"}` when the process answers and a `SELECT 1` works, else 503 | Cloud Run's startup probe; the Dockerfile's HEALTHCHECK |
| `GET /readyz` | 200 `{"status":"ready","checks":{...}}`, or 503 `not_ready` when any check is not ok | The Cloud Monitoring uptime check; the weekly GitHub `uptime.yml`; you, with `curl` |

`/readyz` has three checks, each one word and never a setting's value (so no key, URL or
model name can leak): `database` (`ok`, `unreachable`), `worker` (`ok`; `disabled` when
`RUN_WORKER=false`; `stopping` after SIGTERM; `stopped` if the thread died; `stalled` if
neither the loop nor a running conversion has shown life for six minutes; `not_started`),
`model_provider` (`ok`; `no_key` if the selected provider's key is empty; `unpriced_model`;
`fake` with `FAKE_MODELS`). A spent daily cap is not "not ready": the service is working
and says so on `/status`.

**Shutdown.** Cloud Run sends SIGTERM and kills the process 10 seconds later (a new
revision, a scale-down, a deploy). The app stops the worker at once: no new job is
claimed, the running conversion is cancelled (no more model calls are sent), and its lease
ends now so another instance requeues it immediately instead of after two minutes. Then
uvicorn drains open connections for at most 5 seconds (`GRACEFUL_SHUTDOWN_S`) and the
app's shutdown makes sure the worker is stopped (3 seconds). The requeued job restarts
from the beginning on another attempt (three attempts end in `lease_expired`). Look for
`sigterm_received`, `job_cancelled reason=shutdown` and `worker_stopped` in the logs.
`tests/test_web_health.py` starts a real server, sends SIGTERM, and checks it exits
well inside 10 seconds.

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
a backup mostly protects what is not temporary: the allowlist and admins, courses and
notation memory, the daily spend and quota counters, the content-free aggregates and usage
events, and opted-in documents (30 days). Losing the lot would cost you the allowlist
(re-add the instructors) and some history; instructors would upload again.

**What the deploy sets up.** Cloud SQL's automated backup, daily at 08:00 UTC, of which
`SQL_RETAINED_BACKUPS` (1) is kept, and no point-in-time recovery. That is deliberate:
every backup holds the documents present when it ran, so keeping seven would stretch
"deleted after 24 hours" to a week, and the transaction logs of point-in-time recovery
would do the same (deploy.md, "Backups and deleted documents"). The cost is a restore
point up to a day old and no recovery to a chosen minute. If you need that, enable it on
purpose and change the privacy page: `gcloud sql instances patch a11y-stem-db
--enable-point-in-time-recovery --retained-transaction-log-days=3`.

**Check it weekly** (a calendar reminder is enough; exits 1 when something needs you):

```sh
set -a; . deploy/.env.deploy; set +a
./deploy/backups.sh status
```

It reports: automated backups on; the newest successful automated backup is under 36 hours
old; and no manual backup older than 7 days (a manual backup is kept until deleted, so it
outlives the retention promise). `./deploy/backups.sh list` shows every backup.

**Before a risky change** (a migration that drops or renames something, a restore):

```sh
./deploy/backups.sh now                      # one on-demand backup
# ... make the change, check it ...
gcloud sql backups delete BACKUP_ID --instance=a11y-stem-db --project="$PROJECT_ID"
```

**Prove a restore works (the drill; do it once before real use and after any big schema
change; it costs a few cents).** This restores the newest backup into a throwaway
instance, so the live database is never touched:

```sh
./deploy/backups.sh list                     # note the newest BACKUP_ID (status SUCCESSFUL)
gcloud sql instances create a11y-stem-db-drill --database-version=POSTGRES_16 \
  --edition=enterprise --tier=db-f1-micro --region="$REGION" --storage-size=10 \
  --project="$PROJECT_ID"
gcloud sql backups restore BACKUP_ID --restore-instance=a11y-stem-db-drill \
  --backup-instance=a11y-stem-db --project="$PROJECT_ID"
gcloud sql users set-password postgres --instance=a11y-stem-db-drill \
  --prompt-for-password --project="$PROJECT_ID"
# in a second terminal: the Cloud SQL Auth Proxy to the drill instance
cloud-sql-proxy "$PROJECT_ID:$REGION:a11y-stem-db-drill" --port 5433
```

then, from the checkout:

```sh
read -rs PGPW; export DATABASE_URL="postgresql+psycopg://postgres:${PGPW}@127.0.0.1:5433/a11y"; unset PGPW
uv run a11y-stem ops backup-check
gcloud sql instances delete a11y-stem-db-drill --project="$PROJECT_ID"   # when done
```

`backup-check` reads the copy and prints table names and row counts only. It exits 1 unless
the schema is at this release's migration head (`schema=head`; `ahead` means a newer
release migrated it, fine for a rollback), every table exists, and at least one admin can
sign in. It also prints the age of the newest job and the newest spend day, which should
match the day the backup was taken.

**Restoring for real.** To go back to a backup in place (this overwrites the instance):

```sh
gcloud sql backups restore BACKUP_ID --restore-instance=a11y-stem-db --project="$PROJECT_ID"
gcloud scheduler jobs run a11y-stem-cron --location="$REGION" --project="$PROJECT_ID"
```

Run the cron right after: the restore brings back documents that retention had already
deleted, and the purge removes them again. Then `uv run a11y-stem ops backup-check`
against the instance (through the proxy) and a sign-in.

**Any Postgres: the pg_dump drill.** `a11y-stem ops restore-drill` takes the database in
`DATABASE_URL`, runs `pg_dump` (custom format) into a private temporary directory, runs
`pg_restore` into a scratch database on the same server, runs the same checks as
`backup-check` on the scratch copy, compares the row counts with the source and drops the
scratch database and the dump file whatever happens. It needs the PostgreSQL client tools
on the machine (`--pg-bin DIR` if they are not on PATH) and a user allowed to create
databases (on Cloud SQL, `postgres` through the proxy, not the app's `a11y` user). The
output is counts only. It is also the way to test a `pg_dump` schedule for `DB_MODE=external`:
a cron job running `pg_dump --format=custom --file=a11y-$(date +%F).dump` to storage you
control, with the file deleted after a day for the same reason as the Cloud SQL setting.
`tests/test_ops_backup.py` runs both commands against a real Postgres.

## Tracing (Langfuse)

Off unless `TRACING_ENABLED=true` and both Langfuse keys are set (`deploy.sh` stores the
secret key in Secret Manager when tracing is enabled). If the `tracing` extra is missing
or the client cannot start, the app logs `tracing_disabled` with a `reason` and carries
on without traces. Keep it off for any run on `private-inputs/`.

Where the tracer comes from: `create_app` calls `make_tracer(settings)` once and the
worker passes it to every `convert_pdf` (`app_started` logs `tracing=true|false`); it is
flushed when the app shuts down. `a11y-stem convert` does the same for one run. With
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

## When something goes wrong

| Symptom | Look at | Usual fix |
|---|---|---|
| Uptime alert email | `severity>=WARNING` logs; `gcloud run revisions list` | Roll back traffic to the last good revision (deploy.md, "Roll back") |
| `/healthz` answers 503, or `/readyz` shows `database: unreachable` | The database: Cloud SQL instance state, connections. Users meanwhile see a calm "Not available right now" page (503 with `Retry-After`) | See "Too many connections" below |
| `/readyz` shows `worker: stalled` or `stopped`, or documents wait for ever | `worker_loop_error`, `lease_refresh_failed` and `conversion_abandoned` in the logs | The worker thread is stuck or dead; documents are queued but nothing converts. Deploy a new revision (`./deploy/deploy.sh service`) or `gcloud run services update a11y-stem --region="$REGION" --project="$PROJECT_ID" --update-env-vars=RESTARTED_AT=$(date +%s)` to replace the instance; the running job's lease expires and it is requeued |
| `/readyz` shows `model_provider: no_key` or `unpriced_model` | The `anthropic-api-key` secret has a version and the service reads `:latest`; `ANTHROPIC_MAIN_MODEL` names a model in `models/pricing.py` | Add the secret version and redeploy, or fix the model name |
| Conversions fail with `reason:budget_daily`, jobs end `error:budget_daily`, or `claims_paused` in the logs | Spend today vs `DAILY_SPEND_CAP_USD` | Working as designed: users see "Stopped early" or "Waiting", and queued jobs start after 00:00 UTC. Raise the cap only on purpose |
| Conversions fail with provider errors | `call:<model>:outcome:api_error` counts; OpenAI or Anthropic dashboard | The OpenAI project limit may be reached (HTTP 429 `project_spend_limit_exceeded`); with Claude, the workspace limit or rate limit (HTTP 429 `rate_limit_error`), or a key not scoped to a workspace (HTTP 400, set `ANTHROPIC_WORKSPACE_ID`) |
| "cron not running" or "cron or purge problem" alert | `jsonPayload.message=("cron_done" OR "cron_refused" OR "aggregate_failed" OR "purge_backstop_deleted")`; Cloud Scheduler > job history | See the next rows. The worker's backstop keeps purging meanwhile, so documents are still deleted on time; fix the scheduler anyway |
| Cron returns 403 | `cron_refused` in logs | The scheduler's token differs from the secret, usually after a rotation: `./deploy/deploy.sh cron` |
| Cron returns 500 | `aggregate_failed` with `error_type` and `stack` | Purge still ran. Fix the bug; jobs stay unaggregated until then. A job that expires first is aggregated as it is deleted, or, if that fails too (`aggregate_on_delete_failed`), counted in `unaggregated_deleted` |
| `unaggregated_deleted` above 0 | The cron history | Some statistics are lost for those jobs; nothing else to do |
| Purge itself failing | `purge_done` missing for over an hour (from both `source=cron` and `source=worker`), or `purge_backstop_failed` | Treat as a privacy incident: run `a11y-stem ops purge` by hand, then fix the cause. Job pages already answer 404 once `retain_until` has passed, purged or not |
| Too many connections | Cloud SQL connection count (25 on db-f1-micro) | Each instance pools up to 10 connections; keep `--max-instances=2` or move to `db-g1-small` (50) |
| Weekly GitHub uptime run failed | The run's log | Same as the uptime alert; also check the custom domain and certificate |
| `decisions_canary_drift` | The line's `drifted_cases` and `max_delta`; `a11y-stem ops decisions-canary --max-usd 0.02` to see each moved answer | The Decisions API's answers changed under the thresholds. Conversions are unaffected (the check is a note or a shadow and fails open); to stop the figure notes meanwhile set `DECISIONS_FIGURE_VERIFY=false`. Re-measure the thresholds (docs/eval.md), then re-record the canary |
| Many `decision_call` WARNINGs or `verify:*:outcome:timeout` counts | `outcome`, `status`, `code` on the lines | Decisions API trouble. Nothing to do for conversions (fail-open); the check's answers are missing for those fixes |

## CI and dependency updates

`ci.yml` runs on every push to `main` and every pull request:

| Job | What it checks |
|---|---|
| `lint-test` (3.12, 3.14) | `ruff check`, `ruff format --check`, `ty check src`, `pytest` (offline; Postgres tests use the embedded server on 3.12) |
| `postgres` | `pytest -m postgres` against a Postgres 16 service container |
| `browser` | `pytest -m browser` with Playwright Chromium (axe-core checks) |
| `docker` | Builds the image (no push) with BuildKit and again with the legacy builder (`DOCKER_BUILDKIT=0`, as Cloud Build's `--tag` build may use), and checks the entry point and non-root user |
| `eval-gate` | `a11y-stem eval gate --require-current` for every committed `data/eval/baseline-<task>.json` against its `latest-<task>.json` (skips while none is committed). Fails on a regression, a changed judge, or a latest report the current prompts did not produce |
| `secret-scan` | gitleaks over the full history |

Every action is pinned to a commit SHA with its tag in a comment; Dependabot proposes
updates weekly. `eval-live.yml` (manual, costs money) runs the equations eval against
the live models with a `max_usd` cap and uploads the report as an artifact.
