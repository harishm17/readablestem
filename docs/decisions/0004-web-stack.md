# ADR 0004: One FastAPI service

| Field | Value |
|---|---|
| Status | **Accepted** (Harish, 2026-10-07); the single-service design is current, the Cloud Run hosting is not (see the update below) |
| Date | 2026-10-07 |
| Decider | Harish |

## Decision

One Python service on Cloud Run (min-instances=1, CPU always allocated):

- FastAPI with server-rendered, accessible Jinja templates; htmx only enhances (pages
  work without JavaScript).
- A worker thread in the same process claims queued jobs from Postgres
  (`FOR UPDATE SKIP LOCKED`, with a lease so a crashed job is retried).
- Postgres holds users/allowlist, jobs, blobs (uploads and crops), review edits, the
  daily spend counter, per-user quotas and content-free daily aggregates. No separate
  object store: deleting a job deletes everything derived from the document.
- Google sign-in (OIDC via Authlib) plus an allowlist in the database.
- Cloud Scheduler calls `/internal/cron/aggregate-and-purge` (shared-secret header).

Rejected: Next.js front end + separate FastAPI workers + Cloud Tasks (two deploys, more
surface for a 3-5 instructor beta).

**Update (2026-10-29).** Since 2026-10-14 the service runs on Railway, not Cloud Run. It is
still one FastAPI service with the worker thread in the same process and one replica, and
Postgres is Railway Postgres. The hourly cron is a separate Railway `cron` service running
`readablestem ops cron`; nothing calls `/internal/cron/aggregate-and-purge`, so the daily
canaries that only that route runs are off in production. A `backup` service takes the daily
database dump. See [deploy-railway.md](../deploy-railway.md). The rest of the decision stands.

**Update (2026-11-04).** Sign-in also offers Microsoft accounts for universities listed in `MS_ALLOWED_TENANTS` ([deploy-railway.md](../deploy-railway.md), "Microsoft sign-in on Railway"); the allowlist or `SIGNUP_MODE=open` decides who may then use the app.

**Update (2026-11-04), queue and conversion.** The claim order in the bullet above is now fewest attempts first, then the owner served longest ago (round-robin by person, `web/fair_queue.py`), then oldest; the lease and the three attempts are unchanged. The whole conversion runs in a child process the worker can kill (`web/isolated_convert.py`: a 3072 MB address cap, a CPU limit, the job's wall-clock limit, and no keys or database URL in the child), so a crash, an out-of-memory or a hang fails one job and not the web process. Model requests, spend reservations and database writes stay in the web process.

## Consequences

Drafted from what the repository shows; the author confirms or edits.

- **Easier.** One deploy, one process and no queue service. Deleting a job deletes
  everything made from it, because uploads and crops live in the same database rather than
  in a second store. Server-rendered pages work without JavaScript, which suits an
  accessibility tool.
- **Harder.** The worker shares a process and a CPU allowance with the web pages, and
  min-instances=1 with CPU always allocated is a standing cost. Postgres holds the
  uploaded documents, so its backups hold them too; the retention window and the privacy
  page have to agree (`docs/deploy.md`). Scaling past a few instructors would mean moving
  the worker out of the web process.
- **When to revisit.** When conversions slow the pages down, or use outgrows a handful of
  instructors.
