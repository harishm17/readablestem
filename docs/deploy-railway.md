# Deploying ReadableSTEM on Railway (current)

This is the current deploy guide. The Google Cloud Run guide ([deploy.md](deploy.md)) still
works and still explains the settings, spend limits and sizing in detail; this page covers
what is different on Railway. Day-to-day running is in [operations.md](operations.md).

Railway documentation was read on 2026-10-14 (docs.railway.com: config as code, healthchecks,
pre-deploy command, cron jobs, Dockerfiles, backups, networking limits, pricing). Anything that
could not be confirmed from it is marked **confirm**.

```
Instructors ── https://readablestem.com ──> Railway edge ──> web service "web"
                                                              (Dockerfile; web app + worker thread)
                                                                 │  private network
                                                                 ▼
                                                              Postgres service
Cron service "cron" (same repo and Dockerfile, hourly) ── `readablestem ops cron` ──> Postgres
GitHub uptime.yml (weekly) ── GET /readyz and /demo ──> readablestem.com
```

## What is in the repository

| File | Used for |
|---|---|
| `railway.json` | The web service: Dockerfile build, pre-deploy migration, `/healthz` deploy check, restart policy. Railway reads this file at the repository root for every service that does not name another file. |
| `deploy/railway.cron.json` | The cron service only: start command `readablestem ops cron`, hourly schedule. Point that service at this file (below). |
| `tests/test_railway_config.py` | Fails if either file stops parsing, the health-check path stops being a route, or the pre-deploy and start commands stop being real commands. |

Railway marks config as code as deprecated: "Existing files keep working for legacy services
until 2026-12-01" and then are not guaranteed, with Infrastructure as Code
(`.railway/railway.ts`, `railway config apply`) as the replacement. **Confirm** whether a service
created now counts as legacy. Nothing breaks if the file stops being read, because every value in it
is also a dashboard setting; the table in step 5 lists them so you can copy them over.

## Decisions for Harish

1. **Backup window and the privacy page (blocking-beta, B2 in [launch.md](launch.md)).** The privacy page
   says one daily backup is kept and a deleted document can stay in it "up to about one more day"
   (the Cloud SQL setting). Railway's backups are different: daily backups are kept 6 days, weekly
   27 days, monthly 89 days, and you choose which schedules to switch on. A deleted document stays in
   any backup taken before it was deleted. Pick one and make the privacy sentence match:
   - recommended: **daily only, kept 6 days**; change the sentence to "up to about a week";
   - or no Railway backups for the beta and the current retention wording stays true, at the cost of
     having no restore if the volume is lost (the data is small and mostly rebuildable, see
     deploy.md "Backups and deleted documents").
   Weekly and monthly schedules stretch the promise to 27 and 89 days: do not switch them on.
2. **Off-site nightly dump to Cloudflare R2 (optional, needs your Cloudflare account).** See
   "Backups". Railway's own backups live in the same Railway account as the database; an R2 copy
   survives losing the account or the project. It also adds a second place deleted documents live, so
   it needs a short lifetime (R2 object lifecycle rule: delete after 3 days) and a sentence on the
   privacy page. Recommended for the beta: skip it, use Railway's daily backup, and revisit if the
   service gets real use.
3. **Cron: Railway cron service or an HTTP call.** Recommended: the cron service below. The
   HTTP route (`POST /internal/cron/aggregate-and-purge`) also runs the daily Claude canary
   (about $0.04 a run); `ops cron` does not. Decide whether you want the canary (step 4, option B).
4. **LibreOffice in the image (Word and PowerPoint uploads).** Off by default. To turn it on:
   variable `INSTALL_LIBREOFFICE=true` on the web service and a memory allowance of 2 GB (below).
   Read launch.md W1 to W3 first.
5. **Google sign-in client.** It is in Testing with harishmanoharan17@gmail.com as the only test user.
   Publishing it (so other instructors can sign in without being added as test users) needs the final
   privacy and terms wording and the launch.md items B1 to B6; `readablestem ops launch-check` lists
   what is still unfinished.

## Variables

Already set by hand on the web service: `ENV=prod`, `PORT=8080`, `BASE_URL=https://readablestem.com`,
`DATABASE_URL`, `MODEL_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `SESSION_SECRET`, `CRON_TOKEN`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `CONTACT_EMAIL`, `ADMIN_EMAILS`,
`DAILY_SPEND_CAP_USD=10`, `PER_USER_DAILY_PAGE_QUOTA=90`, `TRUSTED_PROXY_HOPS=1`, the `FEATURE_*` and
the `DECISIONS_*` variables.

`PORT` must stay `8080`: the Dockerfile's start command is fixed to that port, and Railway
sends its health check to `$PORT`. `railway.json` leaves the start command to the image.

Still to decide or set (none is required for the service to start; the app refuses to start in
production if a required one is missing, and logs the reason):

| Variable | Why | Suggested |
|---|---|---|
| `SOURCE_URL` | The footer's link to the source (AGPL, launch.md B12). The default points at the old `harishm17/a11y-stem` repository. | The public repository's URL. |
| `MAX_USD_PER_JOB`, `MAX_USD_PER_PAGE` | Per-conversion dollar caps. | Leave unset: the defaults for Claude are $3.00 and $1.00, and the start-up check refuses numbers that cannot work. |
| `FEEDBACK_URL` | The footer's Feedback link. | Leave unset: it becomes an email to `CONTACT_EMAIL`. A form service is a new data processor to name on the privacy page. |
| `ALERT_EMAIL` | Read only by `deploy/deploy.sh` (Google Cloud alert policies). The app ignores it, so setting it on Railway does nothing. | Skip. Use Railway's project notifications and the weekly GitHub uptime check (below). |
| `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | Defaults 24 hours and 30 days. | Leave unset. If you set them, the cron service needs the same values (reference the web service's). |
| `CLAUDE_CANARY` | Daily drift check, only run by the HTTP cron route. | `false` if you use the cron service and do not want option B. |
| `INSTALL_LIBREOFFICE` | Build argument, see decision 4. | Unset. |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | How long Railway waits between SIGTERM and killing the old container on a deploy. The worker stops claiming at once on SIGTERM and gives its running job back; uvicorn waits 5 s for open connections. **Confirm** the name and default on Railway's deployment page. | `10` |
| `TRACING_ENABLED` and the Langfuse keys | Off. | Leave off (launch.md L4). |

The Google client needs `https://readablestem.com/auth/callback` as an authorized redirect URI
(exact match; the app sends `BASE_URL + /auth/callback`). Sessions are per host: sign in again at
the domain.

`TRUSTED_PROXY_HOPS=1` is right when the only thing in front of the app is Railway's edge. If the
domain's DNS goes through Cloudflare's proxy ("orange cloud"), there are two proxies and the value
is 2; with the wrong value the rate limits are shared by everyone or can be dodged with a forged
`X-Forwarded-For`. After the first deploy, check that two people on different networks hit separate
limits, or that the log's limiter key is not Railway's address (**confirm**: Railway's edge adds the
client address to `X-Forwarded-For`).

## First deploy: checklist

1. **Project.** One Railway project with the web service (from this GitHub repository, branch
   `feat/launch-integration` until it is merged to the branch you deploy from), the Postgres
   service, and the custom domain `readablestem.com` on the web service. (Done.)
2. **Variables** as above. `DATABASE_URL` must use the `postgresql+psycopg://` scheme and Railway's
   private hostname, as a reference to the Postgres service (for example
   `postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`).
   Production refuses SQLite, a non-https `BASE_URL`, and any missing key.
3. **Web service settings.** Source: the repository; builder Dockerfile; no start command; no cron
   schedule; replicas 1. `railway.json` sets the rest. Do not scale beyond one replica: rate limits
   and the upload gate are per process, and a second worker would only take pages.
4. **Migrations run before each deploy** through `preDeployCommand`:
   `readablestem db upgrade` (`alembic upgrade head`) in a separate container on the private
   network with the service's variables. The app does not migrate itself: in production it refuses to
   start against a schema that is missing or behind its code (`require_migrated`), and it accepts one
   that is ahead, so a failed pre-deploy command stops the deploy and the old version keeps serving. Write migrations additively (deploy.md, "Roll back").
5. **If the config file is not read,** set these in the web service's settings instead:

   | Setting | Value |
   |---|---|
   | Builder | Dockerfile (`Dockerfile`) |
   | Pre-deploy command | `readablestem db upgrade` |
   | Healthcheck path | `/healthz` (timeout 300 s) |
   | Restart policy | On failure, 10 retries |
   | Start command | empty |

   Why `/healthz` and not `/readyz`: Railway uses the health check only to decide whether a new
   deployment may take traffic. `/healthz` needs the database and nothing else. `/readyz` also needs
   the worker thread and the model provider to be set up, so a provider problem would block a deploy
   that is otherwise fine and a worker starting late would be read as a failed deploy. `/readyz` is
   what the weekly GitHub uptime check and your own monitoring should read. Railway's health check
   sends its requests from the host name `healthcheck.railway.app`; the app does not restrict host
   names, so nothing needs adding.
6. **Deploy,** watch the build and deploy logs. The first deploy creates the schema.
7. **Create the first admin.** `ADMIN_EMAILS` makes admins at startup; check with
   `railway ssh -s web -- readablestem users list` (**confirm** the `ssh` flags for your CLI
   version with `railway ssh --help`). Add instructors with `readablestem users allow EMAIL`.
8. **Cron service** (next section).
9. **Backups** (below).
10. **Check it:** `curl https://readablestem.com/healthz`, `/readyz` (each worker, database, provider
    word should read ok), sign in at the domain, convert one small PDF, download, delete.
11. **Run the launch check** against production settings (below) and clear its list.
12. **GitHub:** repository variable `LIVE_URL=https://readablestem.com` turns on the weekly
    `uptime.yml` check of `/readyz` and `/demo`.

## Cron

The hourly task aggregates finished jobs into content-free counts, then deletes everything past
its retention time (aggregation first, so the counts survive; the purge runs even if aggregation
fails). The web app's worker thread also runs the purge hourly as a backstop, so a stopped cron
never breaks the 24-hour deletion promise; it only stops the counts and logs
`purge_backstop_deleted`.

### Option A (recommended): a Railway cron service running the CLI

The CLI command exists: `readablestem ops cron` (exit 1 if aggregation failed, after the purge ran).
It needs only the database, so no token travels over the network.

1. In the project, **New > GitHub repo**, the same repository and branch. Name it `cron`.
2. Settings > **Config-as-code** (named "Railway Config File" in the dashboard; **confirm**): set the
   path to `/deploy/railway.cron.json`. This is essential: without it the service reads the root
   `railway.json`, would run the migration too, and two services migrating at once can race.
   If that setting does not exist, do not create the cron service from this repository until you have
   found another way to keep it off `railway.json`; the HTTP route (option B) needs no second service.
   With the file read, nothing else needs setting: start command `readablestem ops cron`, cron schedule
   `7 * * * *`, no pre-deploy command, no healthcheck, restart policy Never.
3. Variables on the cron service (minimum):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{web.DATABASE_URL}}` (use the web service's real name) |
   | `ENV` | `prod` |
   | `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | `${{web.RETENTION_HOURS}}` etc. only if set on web |

   The CLI reads settings without the production start checks, so the model keys, session secret and
   OAuth settings are not needed here.
4. Schedule `7 * * * *` (UTC, every hour at minute 7). Railway allows at least 5 minutes between runs and skips
   a run if the previous one is still active; the task takes seconds and exits.
5. Check: after the first run, the cron service's logs show a line like
   `aggregated_jobs=0 jobs_deleted=0 blobs_deleted=0 unaggregated_deleted=0`. To run it once by hand,
   open the cron service's latest deployment and redeploy it (**confirm** the dashboard offers it); the
   private database hostname does not resolve from your own machine, so `railway run` cannot do it.
6. The Cloud Run alerts that watched `cron_done` do not exist here. The cron service's failed runs show
   in its deployments list; `purge_backstop_deleted` in the web logs means the cron is late.

The cron service runs the same image as the web service and is billed only for the seconds it runs.
Its build is not charged.

### Option B: the HTTP route

`POST https://readablestem.com/internal/cron/aggregate-and-purge` with the header `X-Cron-Token: <CRON_TOKEN>`.
It does the same work, logs `cron_done`, returns 200 or 500, and also runs the daily Claude canary
(about $0.04 a run, at most once a day; set `CLAUDE_CANARY=false` to stop that). It is limited to 10 calls per 5 minutes per address.
Railway has no HTTP scheduler, so a scheduler of your own has to call it. Do not put the token in a
workflow file; use a repository secret:

```sh
curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://readablestem.com/internal/cron/aggregate-and-purge
```

A scheduled GitHub Actions workflow can be late by tens of minutes and is switched off after 60 days
without repository activity, which is why option A is the recommendation.

## Backups

The Postgres service stores its data on a Railway volume. Railway's volume backups cover it
(docs.railway.com/volumes/backups): open the **Postgres service > Backups**, switch on
**Daily** (every 24 hours, kept 6 days) and nothing else (decision 1). Plans: the page does not
say whether Hobby includes backups; **confirm** the tab is there, and do not rely on backups
until you have restored one. Restore: Backups tab > the backup > **Restore**; Railway mounts a
new volume named for the backup's date and stages the change; review it under Details at the top
of the canvas, then **Deploy**. The old volume stays unmounted in the project. A restore also brings
back documents that retention had already deleted: run the cron right after.

Prove a restore before real users. With a scratch database: `readablestem ops restore-drill`
(pg_dump into a scratch database and back, counts only) and `readablestem ops backup-check` on a
restored copy (schema at head, tables present, an admin). Both need the Postgres client tools and a
database URL reachable from where you run them; use Railway's public TCP proxy URL for a one-off,
then remove the proxy.

**Optional off-site copy (decision 2).** `deploy/backups.sh` is for Cloud SQL only (it calls
`gcloud sql backups`) and does nothing on Railway. For R2 nothing is built yet, because it needs an
R2 bucket and an API token from your Cloudflare account and an image with `pg_dump` and an S3 client,
which the app image does not have. The shape, if you choose it: a second cron service
(`0 3 * * *`) from a small Dockerfile with `postgresql-client` and `awscli`, running
`pg_dump --format=custom "$DATABASE_URL_PLAIN" | aws s3 cp - s3://BUCKET/readablestem-$(date +%F).dump --endpoint-url https://ACCOUNT.r2.cloudflarestorage.com`,
an R2 lifecycle rule deleting objects after 3 days, bucket access for that one token only, and the
privacy sentence from decision 1 covering the new longest window. (`DATABASE_URL_PLAIN` is the
connection string without the `+psycopg` driver suffix, which `pg_dump` does not accept.)

## Memory, CPU and request size

**Memory.** Measured on the development box, one conversion peaks near 0.5 GB including the web app
(deploy.md "Sizing": 437-504 MB for 30 dense pages). The layout work on a lecture PDF with the new
limits (merged from `feat/perf-layout`) measured up to about 413 MB for the layout step alone, and a
page over the drawing limits is no longer read at all. The probe that checks an upload before layout
has its own address-space cap of 768 MB plus twice the PDF. The table model is loaded at start
(`PRELOAD_TABLE_MODELS`), so the idle process is already a few hundred MB. Recommendation:
**allow 1 GB, expect 0.4 to 0.6 GB resident, and do not set a limit below 1 GB**; with
`INSTALL_LIBREOFFICE=true` allow 2 GB (LibreOffice adds about 300 MB while a Word or PowerPoint file
converts). Railway bills memory by use, not by what is allowed, so a higher allowance costs nothing
until it is used. Railway's Hobby plan lists up to 48 GB per service in its pricing page;
a service memory limit is a setting only if you want to cap it (Settings > Resource limits, **confirm**).
Watch the Metrics tab during the first real conversions and the first 300-page upload.

**CPU.** One vCPU is enough (deploy.md: about 20 CPU-seconds for a 30-page document). Replicas: 1.

**Request size.** `MAX_LARGE_UPLOAD_MB=30` (and `MAX_UPLOAD_MB=20`) were sized for Cloud Run's
32 MiB request cap. The app enforces its own limits: it refuses a request whose declared length or
streamed size is over `MAX_LARGE_UPLOAD_MB` (middleware in `web/security.py`, `web/uploads.py`) and
the form tells the person. Railway's documentation (networking specs and limits) states no maximum
request body size; it states that a request body must finish uploading within 5 minutes, a request
may run up to 15 minutes while data moves (5 minutes idle), headers are limited to 32 KB, and there
are 10,000 concurrent connections. So the app's 30 MB cap should be the effective one, but that is an absence of a documented limit and not a
guarantee: **confirm after the first deploy by uploading a 29 MB PDF at the domain**, and if Railway
refuses it, lower `MAX_LARGE_UPLOAD_MB`. The 5-minute upload window matters for slow
connections: 30 MB in 5 minutes needs about 0.8 Mbit/s.

**LibreOffice.** The Dockerfile declares `ARG INSTALL_LIBREOFFICE=false` in its final stage, and
Railway makes a service's variables available to Dockerfile builds, which a Dockerfile must pick up
with `ARG`. So setting the service variable `INSTALL_LIBREOFFICE=true` and redeploying installs
LibreOffice Writer and Impress. Check the build log for the `apt-get install` step, and that the
upload form then offers Word and PowerPoint. Unset it to go back. (On Cloud Run this needed a code
change because `gcloud builds submit --tag` cannot pass build arguments.)

## Ship a new version

Two ways; choose one per service so they do not both deploy the same commit.

- **GitHub-connected deploys (recommended).** The web and cron services watch a branch; a push to it
  builds, runs the pre-deploy migration, checks `/healthz`, then switches traffic. Turn on "Wait for CI"
  in the service settings if you want the GitHub checks to pass first (**confirm**).
- **From a checkout:** `railway up --service web` from this branch's checkout uploads the working tree
  (`.dockerignore` applies) and deploys it. It deploys uncommitted files too, so run it from a clean tree.
  Deploy the cron service the same way (`--service cron`) after changes that touch `ops`.

Nothing in this repository runs `railway up` for you, and none of the checks in this guide need it.

**Roll back.** In the web service's **Deployments** list, open the menu of the last good deployment
and choose **Rollback** (Railway keeps images for 72 hours after removal; **confirm** the menu name).
This switches the code only. The database is not rolled back and the pre-deploy migration does not run
backwards: an older version starts against a newer schema (it logs `schema_ahead` and carries on), which
works while migrations are additive. A release whose migration removed or renamed something needs a
forward fix or a backup restore (deploy.md "Roll back" explains this and the 0.4.0 stored-results limit).
Then fix on a branch and ship forward.

## Logs and monitoring

- Dashboard: service > **Logs** (Deploy logs for the running service, Build logs, and Pre-deploy logs
  for the migration).
- CLI (read-only): `railway logs --service web` (add `--build` for the build log; `railway logs --help`
  for the flags in your version), `railway status`.
- Logs are JSON, one event per line, with no document content. Useful messages: `cron_done` (HTTP cron),
  `purge_done`, `purge_backstop_deleted` (the cron is late), `rate_limited`, `launch_blockers` (count at start),
  `sigterm_received`, `schema_ahead`.
- Railway keeps logs for a limited time (**confirm** for Hobby), so they are not a record of anything.
- Alerts: the Cloud Monitoring policies in `deploy.sh` have no Railway equivalent. Use Railway's project
  notifications (Settings > Notifications, for failed deploys and crashes; **confirm**), the weekly GitHub
  uptime workflow, and the model provider's own spend alert (the hard stops are the provider's workspace limit
  and `DAILY_SPEND_CAP_USD`).

## Running the launch check against production

`readablestem ops launch-check` needs no database. It reads the settings from the environment and the public
templates from the checkout, and prints what is unfinished (draft pages, the demo, production settings, spend
limits). Run it from a checkout of the deployed commit, with the web service's variables:

```sh
railway link            # once: pick the project, the production environment and the web service
railway run -s web -- uv run readablestem ops launch-check
```

`railway run` runs the command on your machine with the service's variables in its environment; nothing is
changed on Railway, and the command prints item names only, never a setting's value. Do not use a command that
echoes the environment. Alternatively `railway ssh -s web -- readablestem ops launch-check` runs it in the
container (the image has the templates, but not `tests/`, `proof/` or the eval baseline, so it can list
repository items that exist in the checkout). Exit code 1 means items remain; `launch_blockers=0` is the goal.
Before the Google client is published, launch.md items B1 to B6 and P1 to P7 are what clear it.

## What it costs

Railway Hobby is $5 a month and includes $5 of usage. Usage is billed per use: memory $10 per GB-month,
CPU $20 per vCPU-month, volume storage $0.15 per GB-month, network egress $0.05 per GB; builds are free.
Rough estimate, to be replaced by the Usage page after the first week:

| Item | Assumption | About per month |
|---|---|---|
| Web service memory | 0.4 GB resident all month | $4 |
| Web service CPU | idle polling plus a few conversions, about 0.05 vCPU on average | $1 |
| Postgres service (memory, CPU, volume) | small database, about 0.15 GB resident | $2 |
| Cron service | seconds an hour | under $0.20 |
| Egress | pages and downloads | under $0.50 |
| **Usage** | | **about $7** |

The bill is the $5 subscription plus any usage above the $5 included: about $7 on these numbers.

So expect $5 to $8 a month for the app and database, not counting the model: Claude is paid separately and is
capped at `DAILY_SPEND_CAP_USD=10` a day by the app (at most about $300 in a 30-day month if the cap were hit
every day; measured costs are about $0.004 to $0.014 per page, so a few instructors converting a few hundred
pages a week cost a few dollars). Set the provider's own monthly workspace limit as the hard stop. Railway has
a usage limit setting under billing (**confirm** on Hobby): set it a little above the estimate so a runaway
service cannot bill without bound.

## The Cloud Run files

`deploy/deploy.sh`, `deploy/backups.sh` and `deploy/deploy.env.example` are the Google Cloud path and are not used
on Railway. They are kept, with deploy.md, as the documented alternative.
