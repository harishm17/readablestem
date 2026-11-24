# Deploying ReadableSTEM on Railway (current)

This is the current deploy guide. The Google Cloud Run guide ([deploy.md](deploy.md)) still
works and still explains the settings, spend limits and sizing in detail; this page covers
what is different on Railway. Day-to-day running is in [operations.md](operations.md).

Railway documentation was read on 2026-10-14 (docs.railway.com: config as code, healthchecks,
pre-deploy command, cron jobs, Dockerfiles, backups, networking limits, pricing) and again on
2026-10-29 for the restart policy, deployment teardown (SIGTERM and draining) and log filters.
Anything that could not be confirmed from it is marked **confirm**.

```
Instructors ── https://readablestem.com ──> Railway edge ──> web service "web"
                                                              (Dockerfile; web app + worker thread)
                                                                 │  private network
                                                                 ▼
                                                              Postgres service
Cron service "cron" (same repo and Dockerfile, hourly) ── `readablestem ops cron` ──> Postgres
Backup service "backup" (postgres:18 image, daily) ── pg_dump ──> Postgres, dumps on its volume
GitHub uptime.yml (every 5 min: GET /readyz; every 3 h: GET /healthz/ops and /demo) ──> readablestem.com
```

## As deployed (first deploy 2026-10-14; settings read from `.railway/railway.ts` on 2026-11-04)

What the first deploy found, and how production is actually configured:

- **Railway ignores `railway.json` for new services.** `railway config migrate status` reported
  "No services in production read Config as Code", so the first deploy ran no migration and
  crash-looped on an empty database. The settings were applied through Railway's API instead
  (`railway api 'mutation { serviceInstanceUpdate(...) }'`). Since 2026-10-21 they are described in
  `.railway/railway.ts` (imported from the live project with `railway config pull`; the old
  `railway.json` and `deploy/railway.cron.json` were deleted because nothing read them). What
  production uses:

  | Service | Settings |
  |---|---|
  | `web` | Built from the repository Dockerfile (builder setting `RAILPACK`, see below); pre-deploy `readablestem db upgrade`; health check `/healthz` (300 s); restart policy not set in `railway.ts`, so Railway's default applies (on failure, 10 retries); one replica; domains readablestem.com and www.readablestem.com |
  | `cron` | Same image; start `readablestem ops cron`; schedule `7 * * * *`; restart policy `NEVER`; every variable a reference to `web` (`${{web.NAME}}`) |
  | `backup` | Image `postgres:18`; daily at 08:17 UTC `pg_dump` of the database (`PGURL=${{Postgres.DATABASE_URL}}`), written to a `.part` file on the private volume `/backups`, restored into a scratch database and compared with the live one (gzip reads back, completion line, every table, same `alembic_version`), and only then moved into place; on any failure it prints `backup_failed reason=...`, removes the file and exits 1; after a good run it records the `backup` heartbeat (`ops_heartbeat`); dumps older than 5 days are deleted, so the last 6 are kept; restart policy `NEVER`. The command is the bash script `backupScript` in `.railway/railway.ts` |

  A redeploy reuses the old deployment's settings; after changing a setting, deploy afresh with
  `railway up --service <name>`.
  The live builder setting reads `RAILPACK` (what `railway.ts` records, so the plan is empty), but
  the deployments of `web` and `cron` are built from the repository `Dockerfile`, which Railway
  picks up on its own. Keep the Dockerfile at the repository root.
- **Backups.** Railway's own scheduled backups need a Pro workspace, so the `backup` service
  above does the daily copy inside the same project. The privacy page's "kept for about 6 days"
  matches it. To restore: `railway ssh --service backup`, then
  `gunzip -c /backups/readablestem-<date>.sql.gz | psql "$PGURL"` into an empty database.
  Every run also restores the new dump into a scratch database `restore_check`, checks it against
  the live database (every table, the same `alembic_version`), drops it and logs
  `backup_ok file=... tables=... alembic=...`; a dump that is cut short, does not restore or lacks a
  table fails the run and is not kept (`backup_failed reason=...`, docs/operations.md). First
  verified run: 2026-10-14, 17 tables.
- **Google sign-in** is published (In production): any Google account can reach the sign-in.
  With `SIGNUP_MODE=allowlist` the app's own allowlist decides who gets in (`/admin`); with
  `SIGNUP_MODE=open` anyone with a verified Google account gets an account at the first
  sign-in, with the open-tier limits below.
- **Smoke test, 2026-10-14:** sign-in, the sample job, a 2-page LaTeX handout converted in 19 s
  (31 model calls, $0.087), and a 28.9 MB upload accepted into "choose pages".

## What is in the repository

| File | Used for |
|---|---|
| `.railway/railway.ts` | The whole Railway project as code: services `web`, `cron`, `backup`, the Postgres service, both volumes, domains, schedules, start and pre-deploy commands, health check. Every variable is listed by name with `preserve()`; values stay in Railway. |
| `.railway/package.json`, `.railway/package-lock.json` | Only the `railway` npm package (the TypeScript SDK that `railway.ts` imports). The app is Python; nothing else uses Node. |
| `tests/test_railway_config.py` | Fails if `railway.ts` stops naming the services, the health-check path stops being a route, the pre-deploy and start commands stop being real commands, a literal secret appears in it, or a `railway.json` comes back. |

`railway.json` and `deploy/railway.cron.json` (Config as Code) are gone. Railway stops reading
them on 2026-12-01 and names Infrastructure as Code as the replacement;
`railway config migrate status` reports that no service in production reads them, so deleting
them changes nothing live.

### Changing the infrastructure

Run these from a checkout linked to the project (`railway link -p readablestem -e production`),
using the global `railway` CLI directly (not through `npx` or `timeout`: the CLI's version check
reads `$_` and breaks).

```sh
(cd .railway && npm install)   # once per checkout: installs the SDK
# edit .railway/railway.ts
railway config plan            # what would change in production; read it
railway config apply           # makes the change, after the same review
```

Rules that keep the plan honest:

- **An empty plan is the resting state.** "Your Railway configuration is already up to date"
  means the file and production agree. Run the plan after any change made in the dashboard; if it
  shows a difference, either put the change in the file or undo it in the dashboard.
- **Variables are managed.** A variable that exists in Railway but is missing from `railway.ts`
  appears in the plan as a deletion. After `railway variable set NEW=...`, add the name to
  `appVariables` (or `preserve()` it on the service) so the plan stays empty. A non-secret value may
  be written as a literal only if it equals what production has; secrets and anything changed from
  the dashboard stay `preserve()`.
- **Do not set `build.watchPatterns`.** With them `railway up` skips builds whose files did not
  match. Leave the `builder` the file records (`RAILPACK`) as it is: it is the live value, and the plan
  compares against it. The Dockerfile is what gets built regardless.
- **Applying never deploys code.** It changes service settings and creates or removes the
  resources the file names or drops. A settings change reaches a running service at its next
  deployment. Deploy code with `railway up`, below.
- To bring dashboard changes into the file instead, `railway config pull --variables managed --force`
  rewrites it from production (it drops the comments and the `preserved` helper; review the diff).

## Standing decisions

1. **Backups and the privacy page. Decided: daily only, kept 6 days; the privacy page says a
   deleted document can remain in a backup for up to about a week.** The earlier sentence ("one
   daily backup", "up to about one more day") was the Cloud SQL setting. A deleted document stays
   in any dump taken before it was deleted. The `backup` service keeps the last 6 daily dumps and
   there are no weekly or monthly ones: more would stretch the promise, and the privacy page
   (`privacy.html`) would have to change first. Railway's own volume backups (daily kept 6 days,
   weekly 27, monthly 89) are not used; they need a Pro workspace.
2. **Off-site nightly dump to Cloudflare R2: not built.** See "Backups". The dumps live in the
   same Railway project as the database, so losing the project loses both. An R2 copy would add a
   second place deleted documents live, so it would need a short lifetime (an R2 lifecycle rule
   of 3 days) and a sentence on the privacy page.
3. **Cron: the Railway cron service (option A).** The HTTP route (`POST
   /internal/cron/aggregate-and-purge`) and `ops cron` both run the two daily canaries, each at most
   once per UTC day (the Claude canary, about $0.04 a run; the OpenAI Decisions canary, which is
   off under Anthropic). In production the Claude canary does nothing until
   `CLAUDE_SECOND_CHECK` is turned on (it is `off` in code), so no canary spends anything by
   default. Their status goes in the cron heartbeat's note and on `/admin` ("Daily checks").
4. **LibreOffice in the image (Word and PowerPoint uploads).** Off by default. To turn it on:
   variable `INSTALL_LIBREOFFICE=true` on the web service and a memory allowance of 2 GB (below).
   Check `readablestem ops launch-check` first.
5. **Google sign-in client.** Published (In production), see "As deployed".

## Variables

Already set by hand on the web service: `ENV=prod`, `PORT=8080`, `BASE_URL=https://readablestem.com`,
`DATABASE_URL`, `MODEL_PROVIDER`, `ANTHROPIC_API_KEY`, `SESSION_SECRET`, `CRON_TOKEN`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `CONTACT_EMAIL`, `ADMIN_EMAILS`,
`DAILY_SPEND_CAP_USD=10`, `PER_USER_DAILY_PAGE_QUOTA=300`, `TRUSTED_PROXY_HOPS=1`, the `FEATURE_*`, `EQUATION_*`, `FLAG_POLICY` and
the `DECISIONS_*` variables (their production values: "Feature flags in production" below). The `DECISIONS_*` ones (`DECISIONS_CANARY`, `DECISIONS_EQUATION_SHADOW`,
`DECISIONS_FIGURE_VERIFY`) have no effect under `MODEL_PROVIDER=anthropic`: they only control OpenAI's
Decisions API check, which is skipped with Claude (`models.verifier.any_check_enabled`). They stay in
`.railway/railway.ts` so the plan stays empty. `SIGNUP_MODE` and the open-tier variables in the next
table are also set on web; the app starts without them, in `allowlist` mode.

`PORT` must stay `8080`: the Dockerfile's start command is fixed to that port, and Railway
sends its health check to `$PORT`. `.railway/railway.ts` leaves the start command to the image.

Still to decide or set (none is required for the service to start; the app refuses to start in
production if a required one is missing, and logs the reason):

| Variable | Why | Suggested |
|---|---|---|
| `SIGNUP_MODE` | `allowlist` (the default) or `open`: whether anyone with a verified Google (or allowed Microsoft) account gets an account at the first sign-in (docs/web.md, "Sign-up modes and access tiers"). | **`open`** for the free beta anyone can try. Set it last, after the checks below. |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_ALLOWED_TENANTS` | Microsoft sign-in. All three or none. | Next section: "Microsoft sign-in on Railway". |
| `OPEN_TIER_DAILY_PAGE_QUOTA` | Pages a day for an account that signed itself up. Invited people keep `PER_USER_DAILY_PAGE_QUOTA` (300). | **`150`** (one full-size 100-page document and some handouts a day; the default). |
| `OPEN_TIER_DAILY_REREADS` | Model reads from the review screen (a marked equation, a table cell) a day for such an account. Each costs model spend that no page count bounds. | **`30`** (the default). |
| `STORAGE_PER_USER_MB`, `STORAGE_OPEN_TIER_MB`, `STORAGE_GLOBAL_MAX_MB` | Stored-bytes caps (docs/operations.md, "How much the uploads weigh"): per invited person, per open-tier person, and for all uploads together. The database volume is 5000 MB and the nightly backup check restores a second copy on it. | Leave unset: the defaults are **300**, **250** and **2000**. |
| `MAX_NEW_ACCOUNTS_PER_DAY` | Accounts open sign-up may make per UTC day, for the whole service. When it is used up, new sign-ins get "Sign-ups are full for today"; people with accounts are not affected. `0` pauses sign-up. | **`100`** (the default). |
| `OPEN_TIER_DAILY_SPEND_CAP_USD` | The part of `DAILY_SPEND_CAP_USD` that documents of such accounts may use together; invited people are not held back when it is spent. It is inside the daily cap, not added to it. | **`4`** (the default; $4 of the $10 day, so strangers can never take the instructors' whole budget). |
| `SOURCE_URL` | The footer's link to the source (AGPL). The default is the public repository, `https://github.com/harishm17/readablestem`, which has to exist before the first sign-in. | Leave unset, or set it to the deployed commit's URL. |
| `MAX_USD_PER_JOB`, `MAX_USD_PER_PAGE` | Per-conversion dollar caps. | Leave unset: the defaults for Claude are $6.00 and $1.00 (a 100-page document is $1.50 spent plus about $1.1 held at once; with tables on the job cap must be at least $5.71), and the start-up check refuses numbers that cannot work. A value set earlier (a job cap of $3.00 or $4.70) is too low for 100-page documents: unset it. |
| `FEEDBACK_URL` | The footer's Feedback link. | Leave unset: it becomes an email to `CONTACT_EMAIL`. A form service is a new data processor to name on the privacy page. |
| `ALERT_EMAIL` | Read only by `deploy/deploy.sh` (Google Cloud alert policies). The app ignores it, so setting it on Railway does nothing. | Skip. Use Railway's project notifications and the GitHub uptime checks (below). |
| `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | Defaults 24 hours after the last activity, and 30 days for a document whose owner also lets the developer look at the results; "Keep for 7 days" is a per-upload choice with a fixed length (`web/retention.py`). Neither is named in `railway.ts`, so production uses the defaults. | Leave unset. If you set them, the cron service needs the same values (reference the web service's). |
| `CLAUDE_SECOND_CHECK` | The Claude second check: `off` (the code default since WS10.5) skips it; `shadow` asks a second model about each equation and figure and only records its answer; `enforce` acts on it. Declared on `web` in `railway.ts`. | Production's value is in the production profile. It was set to `off` on 2026-11-09: on the first real-model walkthrough (4 OCW documents) the shadow check changed no output and cost about 16% of the spend. Turn it back on only for a measured run. |
| `CLAUDE_CANARY` | Daily drift check of the Claude second check. The cron service (`ops cron`) and the HTTP cron route run it, at most once per UTC day, and only while `CLAUDE_SECOND_CHECK` is not `off`. | Leave unset. |
| `INSTALL_LIBREOFFICE` | Build argument, see decision 4. | Unset. |
| `FEATURE_LATEX_SOURCE` | Takes `.tex` files and zips of a LaTeX project (docs/web.md, "LaTeX source"; ADR 0008). Declared on `web` in `railway.ts`. | Production's value is in the production profile (it was turned on 2026-11-04 13:53 UTC, once the start-up sandbox probe passed on Railway, see "LaTeXML" below). Turn it on only after the image has LaTeXML (`INSTALL_LATEXML`) and `railway ssh -s web -- readablestem ops sandbox-probe --require-latexml` prints PASS. |
| `FEATURE_BATCH_ZIP` | Takes a `.zip` of PDFs and LaTeX project folders as a batch of documents (docs/web.md, "Zip of documents and the batch page"). Declared on `web` in `railway.ts`. | Production's value is in the production profile; keep it off until real course sources (zips of projects) have been tried. Zips of LaTeX projects also need `FEATURE_LATEX_SOURCE` (on). |
| `FEATURE_WORD_SOURCE` | Reads a `.docx` from the file itself (pandoc in the sandbox, `source/docx_reader.py`) instead of from a LibreOffice PDF; needs pandoc, a passing sandbox probe, and takes only files the reader's gate accepts, the rest go the PDF route (docs/web.md, "Word files read from the file"). Not declared on `web` in `railway.ts` yet. | Production's value is in the production profile; off until real Word files have been read both ways. |
| `FEATURE_CLASS_LINK` | The class link: an owner's link that opens the published student version without a sign-in (docs/web.md, "The class link"). Declared on `web` in `railway.ts`. | Off, and stays off until the list in "Before `FEATURE_CLASS_LINK` is turned on" below is done. |
| `FEATURE_COURSE_GRANTS` | Course grants: an owner gives a person every document of a course (docs/web.md, "Course grants"). Needs `FEATURE_NOTATION_MEMORY`. Declared on `web` in `railway.ts`. | Off, and stays off until the list in "Before `FEATURE_COURSE_GRANTS` is turned on" below is done. |
| `FEATURE_BEAMER_SOURCE` | Takes Beamer decks within LaTeX source (needs `FEATURE_LATEX_SOURCE`; docs/web.md). Declared on `web` in `railway.ts`. | Production's value is in the production profile; keep it off until a few real decks have been converted and read. |
| `INSTALL_LATEXML` | Build argument: adds LaTeXML, TeX Live's base and ImageMagick (about 270 MB; see "LaTeXML" below). Needed for LaTeX source uploads, which also need the sandbox to pass `readablestem ops sandbox-probe` on this image. Declared on `web` in `.railway/railway.ts` so that `railway config apply` keeps it. | Leave as it is: the probe passes on Railway with it. |
| `INSTALL_MATH_READER` | Build argument: adds the local handwriting reader's model files (0.6 GB; `docs/math-readers.md`, "The local reader"). Only `IMAGE_PAGES_SECOND_READER=local` makes the pipeline call it, and both it and `MATH_READER_LOCAL_ENABLED` are off by default (production's values: the production profile). With it, allow 2 GB of memory. | Unset. |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | How long Railway waits between SIGTERM and SIGKILL for the old deployment (its documented default is 0). The worker stops claiming at once on SIGTERM and gives its running job back; uvicorn waits up to 5 s for open connections, then the worker stop up to 3 s. | `10`, set as `deploy.drainingSeconds` on `web` in `.railway/railway.ts` |
| `TRACING_ENABLED` and the Langfuse keys | Off. | Leave off. |

The Google client needs `https://readablestem.com/auth/callback` as an authorized redirect URI
(exact match; the app sends `BASE_URL + /auth/callback`). Sessions are per host: sign in again at
the domain.

`TRUSTED_PROXY_HOPS=1` is right when the only thing in front of the app is Railway's edge. If the
domain's DNS goes through Cloudflare's proxy ("orange cloud"), there are two proxies and the value
is 2; with the wrong value the rate limits are shared by everyone or can be dodged with a forged
`X-Forwarded-For`. After the first deploy, check that two people on different networks hit separate
limits, or that the log's limiter key is not Railway's address (**confirm**: Railway's edge adds the
client address to `X-Forwarded-For`). After a deploy, search the logs for `proxy_hops_unexpected`:
it is logged once per process when `X-Forwarded-For` has more than `TRUSTED_PROXY_HOPS + 1`
entries, which means a proxy is missing from the setting (or a client forged the header). It
carries counts, no addresses. `readablestem ops launch-check` cannot see the live header; it
only flags `TRUSTED_PROXY_HOPS=0`.

Every request line in the log also carries `forwarded_parts`, the number of entries the
`X-Forwarded-For` header held when it reached the app (a count, never an address; 0 with no
header). To settle how Railway treats the header, send one public GET to a page that is not a
health route (those log at debug level) with a made-up two-entry header and read the count on its
line: 3 means Railway appends the address it saw, so the limiter keys on the real address
(`TRUSTED_PROXY_HOPS=1` is right); 1 means it replaces the header; 2 means it passes the header
through, and `TRUSTED_PROXY_HOPS` must change before anything else. **Not yet read in production**:
write the date and the count here when it is.

`CRON_TOKEN` must be at least 32 characters (production refuses to start otherwise). To rotate
`SESSION_SECRET` without signing people out, use `SESSION_SECRET_PREVIOUS`
([security.md](security.md), "Rotating the session secret").

## Feature flags in production

The production value of every behavior setting (the feature flags, the equation checks, the
models and their effort, the thresholds) is in the production profile,
`src/readablestem/ops/production.profile.toml`, with the place each value was taken from. This page
does not repeat them; `railway.ts` names every one of them on `web` with `preserve()`, and the
defaults are in `src/readablestem/config.py`. Settings that are not about behavior (secrets,
addresses, accounts, spend and size limits) are named in the profile's `operations` list only.

The first profile was written from what the repository states, not from the dashboard, so it says
`confirmed = false`. While it does, a deploy whose settings differ from it starts, logs
`profile_mismatch` once per differing setting (the name, never the value) and lists the names on
/admin. To confirm it, read the variables (`railway variable list -s web`), correct the file's
values and their `source` lines, and set `confirmed = true`; from then on a difference refuses the
start. `readablestem ops profile-check` prints the same comparison for the environment it runs in
(`railway ssh -s web -- readablestem ops profile-check`). To change a setting in production, change
the profile and the variable in Railway in the same change. A release that changes a profile row
sets the variable first (`railway variable set -s web --skip-deploys NAME=value`, so the code
that is running is not deployed again under the new value), then deploys, then runs that check: the release is live when the
setting's name is not among the names that differ. `railway ssh` needs an SSH key registered
with the Railway account; where there is none, the start log of the new deployment says the
same (`railway logs -s web -d -n 200`: no `profile_mismatch` line before `app_started`; the
0.5.18 release was checked this way). The code defaults are not the profile's
values, so a deploy with a variable missing starts normally, stamps its results with the new
`PIPELINE_VERSION` and reads them the old way; only this check (and the names on /admin) shows
it. `/admin/health` and `/admin` show the profile's hash to an admin (the public `/healthz/ops`
gives the status word only); the image build writes the image facts
(`readablestem ops image-facts --write`, Dockerfile), which changes that hash with what the
image carries.

A row of the profile that switches a behavior or picks a model may carry `reason` (one sentence,
why production has that value) and `eval` (the repository path of the report that measured it,
or `none`); "not stated" means the repository gives no reason. /admin shows both next to each
setting that differs. They are not in the hash, and the file's header says one person owns every
switch.

`FONT_CODE_TABLES` is new: on by default in code, with no value set in Railway (the profile says
so). Set it to `false` on `web`, and in the profile, to go back to the old private-use bullet rules
without a deploy; element text changes on documents with symbol fonts, so their stored step answers
are asked again.

Limits in production (each equals the code default in `config.py`, except the per-user page quota and the daily
spend cap, which are set on `web`): 100 pages and 50 MB per upload (`MAX_PAGES_PER_JOB`, `MAX_UPLOAD_MB`); a file of up
to 500 pages and 100 MB is kept for choosing pages (`MAX_SOURCE_PAGES`, `MAX_LARGE_UPLOAD_MB`);
open tier 150 pages a day; invited 300 pages a day (`PER_USER_DAILY_PAGE_QUOTA`); stored-upload caps
of 250 MB per open-tier person, 300 MB per invited person and 2000 MB for everybody
(`STORAGE_*_MB`; these cap what is stored at a time, not what is uploaded per day); a spend cap of
$6 per job and $10 a day for the site, of which open-tier accounts may use $4.

## Before `FEATURE_COURSE_GRANTS` is turned on

Course grants (WS27.1, docs/web.md "Course grants") let an owner give a person every document of a
course. They are off in code and in the production profile, and with the switch off the public
pages are accurate. Do not set it to `true` on `web` (it needs `FEATURE_NOTATION_MEMORY` too)
until the sentences below are changed or made conditional; the code does not do that for them,
except the one in `privacy.html`, which already follows the switch (`course_grants_on`).

1. **These sentences are false or incomplete once it is on** (`src/readablestem/web/templates/`):
   - `security.html:122-125`: "A document belongs to the person who uploaded it ... The owner can
     share a document with named people": a course can be shared too, and the people reach
     documents added later.
   - `offices.html:80-82`: "A document can be shared with named colleagues, who must sign in with
     the exact email address entered": same.
   - `account.html:24` ("People you share a document with see this beside your address") and
     `account.html:51` ("A read started by someone a document is shared with counts here"): both
     should also name a course's people.
   - `help_review.html:262`: "A person you shared the document with to view only is never given
     the draft": true for a course grant too; add "or a course".
   - `privacy.html`: the access sentence is already conditional on `course_grants_on`.
   - Unchanged and still true: `_retention_table.html:9` and `privacy.html:58`, `67-68` (a document
     is deleted on its schedule whoever can open it).
2. **Tell the owners what a grant is.** A grant covers documents added later; taking a document out
   of the course ends it; nothing is emailed; the grantee must sign in with Google, Microsoft or an
   emailed link to that exact address. The People section says this; the help pages do not yet.
3. **Know what it does not do.** No teams, departments or offices, no hand-over of a course, no
   usage totals. A grant is a person's address, not a role in an organization. The grantee's reads
   count against the owner's allowance.

## Before `FEATURE_CLASS_LINK` is turned on

The class link (WS27.2, docs/web.md "The class link") is the one way a document's content reaches
someone who is not signed in: `GET /s/{token}`. It is off in code and in the production profile.
Do not set it to `true` on `web` until all of this is true; none of it is done by the code, and the
public pages were deliberately not changed with the feature, so with the switch off they stay
accurate.

1. **The service owner supplies terms for publishing.** The terms say nothing today about an
   instructor putting a converted document where a class can open it without signing in: whose
   material may be published, that the instructor answers for the rights to it, what the service
   may do about a link (pause or remove it), and that a link's address is a secret that anyone
   holding it can use. This is the service owner's text, not an engineering one.
2. **A takedown contact that is watched.** `terms.html:88` and `privacy.html:292-296` send
   takedown requests to `CONTACT_EMAIL`, or to "the contact address of this service" when it is
   unset. Public pages now carry owners' material, so `CONTACT_EMAIL` must be set, read daily, and
   someone must know how to remove a link by hand (delete the `job_links` row or set `revoked_at`;
   the published copy then goes with the next hourly purge).
3. **These sentences must change** (they are true only while there is no link; `src/readablestem/web/templates/`):
   - `privacy.html:246-251`: "Only you can open your documents, and the people you name." With a
     link, anyone holding the address can open the published student version, without signing in.
   - `privacy.html:56`: "Nobody else looks at it." (the 7-day choice) is no longer the whole
     truth for a document with a link.
   - `privacy.html:48-54` and `privacy.html:65-66`: the deletion times ("deleted automatically 24
     hours after you last worked on it", "a person you share with does not keep it longer") need
     the exception that a document with a class link is kept until the link's last day (at most 180
     days) and then for its usual time again; and `_upload_limits.html:38-40`, the limits page's
     "How long it is kept", says the same thing and needs the same exception.
   - `privacy.html:127-128`: "No one else. We do not use your uploads to train models, sell them or
     share them beyond the services named here." needs to say that the owner may publish a copy and
     that the page sets no cookie and keeps no record of who opened it, only a count.
   - `terms.html:14` ("Review the output before you post it") and `terms.html:53-55` ("process
     material you are not allowed to share") should name publishing by link next to sharing.
4. **Know what the link does not do** (so the pages above can say it): its address is in the URL, so
   a browser history, a proxy or a log that records URLs holds it (the app's own logs record the
   route template, not the address, and uvicorn's access log is off); `open_count` counts fetches,
   including a chat app's link preview; the page has no way to show who opened it.

Then set `FEATURE_CLASS_LINK=true` on `web` and in the production profile.

## Microsoft sign-in on Railway

Microsoft sign-in ("Sign in with your university account") is built (`web/microsoft.py`) and stays off
until three variables exist on the **web** service. `.railway/railway.ts` already names them
(`MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_ALLOWED_TENANTS`, all `preserve()`), so `railway config apply`
keeps whatever you set. Naming a variable that has no value yet changes nothing in the plan. Cron does
not need them. The portal labels below are explained in deploy.md, "3b. Optional: create the Microsoft
sign-in app (Entra ID)"; how the app checks the tokens is in web.md, "Sign-in, sessions and CSRF".

**1. Register the app** at entra.microsoft.com, in your own directory (a free one works), not a
university's.

- App registrations, **New registration**: name `ReadableSTEM`; supported account types **Multiple
  Entra ID tenants** (older wording: "Accounts in any organizational directory"); no personal
  accounts. Copy the **Application (client) ID**.
- Authentication, **Add Redirect URI**, platform **Web**: `https://readablestem.com/auth/microsoft/callback`
  (the app sends `BASE_URL` + `/auth/microsoft/callback`, and the match is exact), plus
  `http://localhost:8000/auth/microsoft/callback` for local work. Leave both implicit-grant boxes unchecked.
- API permissions: keep `openid`, `profile` and `email` and remove `User.Read`.
- Token configuration, **Add optional claim**, type **ID**: `email` and `xms_edov`.

**2. Create the client secret.** Certificates & secrets, **New client secret**, 12 months. Copy the
**Value** (not the Secret ID); Microsoft shows it once. Put the expiry date in your calendar a few weeks
ahead: on that day Microsoft sign-in stops working (Google keeps working). To rotate, create a second
secret, run the `MS_CLIENT_SECRET` command below with the new Value, redeploy, then delete the old secret.

**3. Allowed directories.** `MS_ALLOWED_TENANTS` lists the directory (tenant) ids that may sign in,
comma-separated, lower-case GUIDs (the app refuses to start on anything else, such as a domain name or
`common`). People from any other directory see "Your organization is not set up for this beta" with their
directory id. The ids read on 2026-11-03 (re-check one with
`https://login.microsoftonline.com/DOMAIN/v2.0/.well-known/openid-configuration`, the id in `issuer`):

| Domain | Directory id |
|---|---|
| utdallas.edu | `8d281d1d-9c4d-4bf7-b16e-032d15de9f6c` |
| illinois.edu | `44467e6f-462c-4ea2-823f-7800de5434e3` |
| purdue.edu | `4130bd39-7c53-419c-b1e5-8758d6d63f21` |
| tamu.edu | `68f381e3-46da-47b9-ba57-6f322b8f0da1` |
| gatech.edu | `482198bb-ae7b-4b25-8b7a-6d7f32faa083` |

Start with UTD only and add a university when its IT team has approved the app (step 6). Being in
the list is not enough to get in: the person's address must be on the allowlist
(`readablestem users allow EMAIL`), or `SIGNUP_MODE=open`.

**4. Set the variables** from a checkout linked to the project (`railway link -p readablestem -e
production`). The app refuses to start when a client id or a secret is set without the rest, so set all
three with `--skip-deploys` and deploy once, never in between. Without `--skip-deploys`, each
`railway variable set` starts a deployment at once. Apply this `railway.ts` first (plan, review, apply:
"Changing the infrastructure"), so the next apply does not delete the values.

```sh
railway variable set -s web MS_CLIENT_ID=<application-client-id> --skip-deploys
printf %s '<client-secret-value>' | railway variable set -s web MS_CLIENT_SECRET --stdin --skip-deploys
railway variable set -s web MS_ALLOWED_TENANTS=8d281d1d-9c4d-4bf7-b16e-032d15de9f6c --skip-deploys
# later, to add a university: the whole list again, comma-separated
railway variable set -s web MS_ALLOWED_TENANTS=<id-1>,<id-2> --skip-deploys
```

Type the real values only in your terminal. `printf` keeps the secret out of the process list; to
keep it out of the shell history too, run `read -rs S` first and use `printf %s "$S"`. Never put a real
value in a file of this repository.

**5. Deploy and check.** `railway redeploy -s web` (the same build with the new variables) or the next
`railway up --service web`. Then:

```sh
railway run -s web -- uv run readablestem ops launch-check
```

The launch check lists a half-set group (`settings: MS_...`); the app logs the same reason and does not
start. Open `https://readablestem.com/login`, choose the Microsoft button and sign in with your UTD account.

**6. Admin consent.** If Microsoft says "Need admin approval", a UTD IT administrator opens this once
(one line; use the directory id of that university and your client id):

```
https://login.microsoftonline.com/<directory-id>/v2.0/adminconsent?client_id=<application-client-id>&scope=openid%20profile%20email&redirect_uri=https%3A%2F%2Freadablestem.com%2Fauth%2Fmicrosoft%2Fcallback
```

Publisher verification (Branding & properties) is only needed if an IT team asks for it.

## First deploy: checklist

1. **Project.** One Railway project with the web service (deployed from a checkout of `main`
   with `railway up`, see "Ship a new version"), the Postgres service, and the custom domain
   `readablestem.com` on the web service. (Done.)
2. **Variables** as above. `DATABASE_URL` must use the `postgresql+psycopg://` scheme and Railway's
   private hostname, as a reference to the Postgres service (for example
   `postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`).
   Production refuses SQLite, a non-https `BASE_URL`, and any missing key.
3. **Web service settings.** Source: the repository (deployed with `railway up`); the repository Dockerfile is built; no start command; no cron
   schedule; replicas 1. `.railway/railway.ts` sets the rest. Do not scale beyond one replica: rate limits
   and the upload gate are per process, and a second worker would only take pages.
4. **Migrations run before each deploy** through `preDeployCommand`:
   `readablestem db upgrade` (`alembic upgrade head`) in a separate container on the private
   network with the service's variables. The app does not migrate itself: in production it refuses to
   start against a schema that is missing or behind its code (`require_migrated`), and it accepts one
   that is ahead, so a failed pre-deploy command stops the deploy and the old version keeps serving. Write migrations additively (deploy.md, "Roll back").
5. **Settings of the web service** (declared in `.railway/railway.ts`; set by hand only if the
   service is ever rebuilt from scratch):

   | Setting | Value |
   |---|---|
   | Builder setting | `RAILPACK` as `railway.ts` records it; the deployments are built from the repository `Dockerfile` |
   | Pre-deploy command | `readablestem db upgrade` |
   | Healthcheck path | `/healthz` (timeout 300 s) |
   | Restart policy | Not set in `railway.ts`: Railway's default, on failure with 10 retries |
   | Start command | empty |

   Why `/healthz` and not `/readyz`: Railway uses the health check only to decide whether a new
   deployment may take traffic. `/healthz` needs the database and nothing else. `/readyz` also needs
   the worker thread and the model provider to be set up, so a provider problem would block a deploy
   that is otherwise fine and a worker starting late would be read as a failed deploy. `/readyz` is
   what the GitHub uptime check (every 5 minutes) and your own monitoring should read. `/healthz/ops`
   says the backup and the cron ran lately, as a status word only (below); the admin page and
   `/admin/health` say which one is late. Railway's health check
   sends its requests from the host name `healthcheck.railway.app`; the app does not restrict host
   names, so nothing needs adding.
6. **Deploy,** watch the build and deploy logs. The first deploy creates the schema.
7. **Create the first admin.** `ADMIN_EMAILS` makes admins at startup; check with
   `railway ssh -s web -- readablestem users list` (**confirm** the `ssh` flags for your CLI
   version with `railway ssh --help`). Add instructors with `readablestem users allow EMAIL`
   (people who sign themselves up with `SIGNUP_MODE=open` need no step; allowing one of them makes
   them invited, with the instructors' limits).
8. **Cron service** (next section).
9. **Backups** (below).
10. **Check it:** `curl https://readablestem.com/healthz`, `/readyz` (each worker, database, provider
    word should read ok), sign in at the domain, convert one small PDF, download, delete.
11. **Run the launch check** against production settings (below) and clear its list.
12. **GitHub:** repository variable `LIVE_URL=https://readablestem.com` turns on the
    `uptime.yml` checks: `/readyz` every 5 minutes, `/healthz/ops` and `/demo` every 3 hours. Set it
    after the first `cron` run and the first `backup` run on a release that has `ops_heartbeat`
    (`/healthz/ops` is 503 until both have reported). GitHub emails a failed run to the repository
    owner, and turns scheduled workflows off after 60 days without a commit to a public
    repository (docs/operations.md, "Health, readiness and shutdown").

## Cron

The hourly task aggregates finished jobs into content-free counts, then deletes everything past
its retention time (aggregation first, so the counts survive; the purge runs even if aggregation
fails). The web app's worker thread also runs the purge hourly as a backstop, so a stopped cron
never breaks the 24-hour deletion promise; it only stops the counts and logs
`purge_backstop_deleted`.

### Option A (recommended): a Railway cron service running the CLI

The CLI command exists: `readablestem ops cron` (exit 1 if aggregation failed, after the purge ran).
It needs only the database, so no token travels over the network.

1. The `cron` service is declared in `.railway/railway.ts` (same repository and Dockerfile as the
   web service); `railway config apply` creates it from the file.
2. The file gives it start command `readablestem ops cron`, cron schedule `7 * * * *`, no
   pre-deploy command, no healthcheck, restart policy Never. Keep it that way: a pre-deploy
   command here would run the migration too, and two services migrating at once can race.
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

The Postgres service stores its data on a Railway volume. The `backup` service in
`.railway/railway.ts` is the backup: daily at 08:17 UTC it dumps the database with `pg_dump`
to `/backups/readablestem-<date>.sql.gz` on its own volume (`backup-volume`), restores the dump
into a scratch database, checks it against the live database and logs
`backup_ok file=... tables=... alembic=...` (or `backup_failed reason=...` and a failed run), and
records the heartbeat that `/healthz/ops` reads. It keeps the last 6 dumps. The privacy page's "about 6 days" matches that (decision 1). Railway's own
volume backups (docs.railway.com/volumes/backups) are not switched on.

Restore into an empty database with `railway ssh --service backup`, then
`gunzip -c /backups/readablestem-<date>.sql.gz | psql "$PGURL"`. A restore also brings back
documents that retention had already deleted: run the cron right after (redeploy the `cron`
service). Then `readablestem ops replay-erasures` erases again the accounts that were erased
after the dump was made (export the current `erasure_ledger` first if the old database can still
be reached; docs/operations.md, "Backups and restore"). Check the restored copy with
`readablestem ops backup-check`.

Check that it runs: `/healthz/ops` (and the GitHub uptime job that calls it), or the `backup`
service's latest deployment log, which should show today's `backup_ok` line. A run that fails
shows in the deployments list and as `backup_failed reason=...`.

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

**Memory.** Measured on the development box, a conversion of a document of 6 to 30 pages peaks near 0.5 GB including the web app
(deploy.md "Sizing": 437-504 MB for 30 dense pages; a 100-page document 494 MB and a 51 MB, 100-page one with a picture on every page 745 MB, resident, for the child alone, measured 2026-11-03). The layout work on a lecture PDF with the new
limits (merged from `feat/perf-layout`) measured up to about 413 MB for the layout step alone, and a
page over the drawing limits is no longer read at all. The probe that checks an upload before layout
has its own address-space cap of 768 MB (a 117 MB, 500-page PDF was probed in 8 s inside it). Each conversion runs in a child process of the web service
(docs/web.md, "The conversion runs in a child process"; it loads the table models itself, so
`PRELOAD_TABLE_MODELS` no longer preloads them in the web process): while a job converts expect the
web process plus a child of about 0.3-0.5 GB resident. Recommendation:
**allow 2 GB, expect 0.4 to 0.9 GB resident, and do not set a limit below 1 GB** (an upload is held once while it is read and twice more by the database driver when it is stored, measured with SQLite: so one of 100 MB adds about 300 MB, and `MAX_CONCURRENT_UPLOADS=3` of them 900 MB: 1 GB was enough for 20 MB uploads); with
`INSTALL_LIBREOFFICE=true` allow 2 GB (LibreOffice's own process holds about 190 MB while a Word file
converts and is not running when the child peaks, so the peak is the child's: 0.41 to 0.42 GB, measured
2026-11-19, below); with `INSTALL_MATH_READER=true` and the reader enabled, a child that has loaded it holds
about 1.1 GB resident (measured, 74 crops), so allow 2 GB there too. Railway bills memory by use, not by what is allowed, so a higher allowance costs nothing
until it is used. Railway's Hobby plan lists up to 48 GB per service in its pricing page;
a service memory limit is a setting only if you want to cap it (Settings > Resource limits, **confirm**). `.railway/railway.ts` configures no memory limit for `web` (nor CPU), so there is none to raise: leave it unset, or if you set one, set **2 GB** for `web` (three 100 MB uploads in memory at once are about 900 MB, plus the 0.5 to 0.9 GB of a conversion).
Watch the Metrics tab during the first real conversions and the first 500-page upload.

**Measured peaks, 2026-11-19.** On the development box (Python 3.12, 16 cores, load average 14 to 17 from
other jobs, so times are 3 to 5 times a quiet machine's; memory is not affected). Each figure is the peak
resident memory (`VmHWM`) of the conversion child, or of the process named, with the real conversion child,
fake model answers and production's behavior settings; the Word and two-parser runs pinned to one CPU
(`taskset -c 0`) as `docs/deploy.md` "Sizing" did.

| What was converted | Peak resident memory |
|---|---|
| **Word**: three files (a 41 KB handout, a 43 KB lecture, a 147 KB file with equations as pictures) through the app with LibreOffice 26.2 | the conversion child 424, 407 and 406 MB; `soffice.bin` 187 to 193 MB, a 58 MB sandbox helper beside it; everything running at one moment peaked at 411, 399 and 406 MB, less than the child and LibreOffice added together, so LibreOffice is not running when the child peaks; the web process 177 MB |
| **PowerPoint** | not measured: this box has no Impress (`libreoffice-impress-nogui`, which the Dockerfile installs). Do not read the Word line as PowerPoint's. |
| **MuPDF and the second parser**: a 28-page paper, layout alone, then layout with PDFium (pypdfium2 5.14, not a dependency yet) reading every character box (56,424 characters, 0.3 to 0.4 s) from the same bytes | 545 and 556 MB with MuPDF alone; 547 and 551 MB with both holding the document: no addition the process peak shows (run to run the figure moves about 10 MB) |
| **100 dense pages** (public papers: 928 equations, 95 figures, 31 tables, 14.5 MB), one conversion | the child 2.0 to 2.1 GB (three runs); a 28-page paper 0.55 to 0.63 GB |

These correct two figures above. "One conversion peaks near 0.5 GB" holds for 6 to 30 pages and not for a
dense 100-page document, which needs about 2 GB for the child alone (the 494 and 745 MB figures for 100 pages
above, measured 2026-11-03, were other documents: what a document holds sets the peak, not its page count). "LibreOffice
adds about 300 MB" was too high, and it does not add to the peak at all. If you set a memory limit on `web`,
make it 4 GB, so that a 100-page dense document and the web process fit. The web process does not grow
with the documents it has converted: twelve conversions of one 6-page document through the worker went from
209 to 232 MB in the first ten and then stayed there (what Python allocated stayed at 5.0 to 5.2 MB; the rest
is memory the allocator keeps, 15 to 18 MB of it given back by `malloc_trim`); with SQLite's default page
cache it grows more (247 to 340 MB over twelve conversions of a 28-page paper), which is the database
driver's cache, not the app's: Postgres keeps its pages in the server. A test that asks conversion 10 to be
within 5 % of conversion 1 failed in 2 of 9 runs on this box (one run went 215 MB after conversion 1, 232 MB after conversion 10), so none is kept.

**CPU.** One vCPU is enough (deploy.md: about 20 CPU-seconds for a 30-page document, about 25 for 100 pages and 35 for 100 pages with 51 MB of pictures, measured 2026-11-03 on one CPU with fake models: 31 s and 44 s of wall time). Replicas: 1.

**Request size.** `MAX_LARGE_UPLOAD_MB=100` (and `MAX_UPLOAD_MB=50`) apply on Railway; the old 30 and 20 were
sized for Cloud Run's 32 MiB request cap, which a Cloud Run deployment still has (set both to 30 or less there). The app enforces its own limits: it refuses a request whose declared length or
streamed size is over `MAX_LARGE_UPLOAD_MB` (middleware in `web/security.py`, `web/uploads.py`) and
the form tells the person. Railway's documentation (networking specs and limits) states no maximum
request body size; it states that a request body must finish uploading within 5 minutes, a request
may run up to 15 minutes while data moves (5 minutes idle), headers are limited to 32 KB, and there
are 10,000 concurrent connections. So the app's 100 MB cap should be the effective one, but that is an absence of a documented limit and not a
guarantee: **confirm after the first deploy by uploading a PDF of about 95 MB at the domain**, and if Railway
refuses it, lower `MAX_LARGE_UPLOAD_MB`. The 5-minute upload window matters for slow
connections: 100 MB in 5 minutes needs about 2.7 Mbit/s (30 MB needed 0.8). A slower upload fails with the browser's own error, before the app sees the file.

**LibreOffice.** The Dockerfile declares `ARG INSTALL_LIBREOFFICE=false` in its final stage, and
Railway makes a service's variables available to Dockerfile builds, which a Dockerfile must pick up
with `ARG`. So setting the service variable `INSTALL_LIBREOFFICE=true` and redeploying installs
LibreOffice Writer and Impress. Check the build log for the `apt-get install` step, and that the
upload form then offers Word and PowerPoint. Unset it to go back. (On Cloud Run this needed a code
change because `gcloud builds submit --tag` cannot pass build arguments.)

**LaTeXML.** The Dockerfile declares `ARG INSTALL_LATEXML=false` in its final stage. Setting the
service variable `INSTALL_LATEXML=true` (declared in `railway.ts`, so an apply keeps it) and
redeploying installs the distribution's `latexml` package (0.8.8) with `--no-install-recommends`; it
depends on TeX Live's base (`texlive-latex-base`), ImageMagick and their Perl bindings. The same step installs `bubblewrap` (the sandbox LaTeX source runs in) and `util-linux` (`prlimit`), and the build fails if `bwrap` or `prlimit` is missing, so `readablestem ops sandbox-probe` on Railway tests the sandbox of this image (it currently fails: see "Status today" below). Right after
the install the build replaces ImageMagick's `policy.xml` with `src/readablestem/source/imagemagick-policy.xml`:
nothing is allowed except PNG, JPEG and GIF, and PS, EPS, PDF, XPS, URL, HTTP(S), FTP, MVG, MSL, TEXT,
LABEL, SVG and `@file` paths are named as denied. The build fails if no policy file was replaced or
`identify -list policy` does not show the PDF denial. **Size:** measured with `podman build` of the
same `apt-get` steps on the pinned `python:3.12-slim-trixie` (pandoc already in it): 324.3 MB before,
594.4 MB after (+270.1 MB, one layer of 270 MB; the plan's estimate of 0.6 GB was too high for this
base). The first Railway build prints the real image size; write it here. Check the build log for the
two `LaTeXML version 0.8.8` lines and the `1` printed by the policy check. Nothing in the app uses
LaTeXML until the LaTeX-source switch (`FEATURE_LATEX_SOURCE`) is on, and that switch stays off
until `readablestem ops sandbox-probe --require-latexml` prints PASS on the deployed image
(docs/operations.md, "Sandbox probe"); the web process also runs that probe at start-up, shows it
on `/admin/health` under `sandbox` (admin only), and keeps the source features switched off in effect unless it
passed, so a flag set by mistake does nothing under a FAIL.

**Status on 2026-11-04.** `FEATURE_LATEX_SOURCE` was turned on in production at 13:53 UTC (its value
now: the production profile, "Feature flags in production" above): the start-up sandbox probe passes on Railway with the Landlock backend (`init_process=tini` as pid 1; the
isolation, memory, task, task-growth and real-LaTeXML checks all ok). `FEATURE_BEAMER_SOURCE` and
`FEATURE_BATCH_ZIP` exist in the code (their values: the profile). How the probe got there:

- **Bubblewrap cannot start on Railway.** The platform gives no unprivileged user namespaces (the
  probe reported `bwrap=ok` but `sandbox_start=fail`), and the container already runs under seccomp
  mode 2 as uid 10001 (`seccomp_mode=2`, `user=uid-10001`). So the sandbox is the Landlock backend
  (`core/landlock_exec.py`; docs/security.md, "Landlock backend"): the kernel reported Landlock
  ABI 7, and every call the launcher needs worked in a throwaway child under the container's own
  filter (`can_landlock_create_ruleset`, `can_landlock_restrict_self`, `can_no_new_privs`,
  `can_seccomp_syscall`, `can_seccomp_prctl`, `can_prlimit_self` all `yes`; `process_dumpable=0`).
- **The launcher had a bug there**, found by the probe's step codes and the container reproduction
  below: it exited silently when its parent was pid 1, and the web process is pid 1 in the
  container, so the Landlock backend never started (`landlock_step=no_report`, `landlock_exit=125`).
  Fixed in PR #153 (the launcher now checks its parent is the process that started it, and every
  launcher-side check has a step code), verified in the container reproduction and then on Railway.
- **Orphans and pid 1.** With the web app as pid 1 of the container, nothing reaps what the
  sandbox leaves when its caller is killed (a deadline kill of the conversion child, a crash): the
  program's orphans and the launcher itself are re-parented to pid 1 and stay as zombies, and a
  hostile document can cause that on purpose. The image therefore has `tini` as its ENTRYPOINT
  (`ENTRYPOINT ["tini", "--"]`, then the unchanged CMD), so tini is pid 1 and reaps every orphan.
  `web` has no `start` command in `railway.ts` (a start command replaces the image's CMD, and
  whether Railway keeps the ENTRYPOINT behind a custom start command is not something this
  repository has verified): it runs the image's CMD behind the ENTRYPOINT. `cron` and `backup` do
  have start commands; they run no sandbox. After a deploy, `init_process=tini` in the probe on
  `/admin/health` (`sandbox.checks`, admin only) shows the ENTRYPOINT is in force; any other name means the
  app is pid 1 again. In the container reproduction below, the cleanup probe leaves one zombie
  with the app as pid 1 and none with an init (`podman run --init`).
- **The task limit must be the kernel's count, not `/proc`'s** (found after #153 was deployed).
  With the launcher running, the probe passed every isolation check but failed
  `sandbox_group_memory_cap` (its three tasks could not be made) and `latexml` (120 s, the wall
  clock): the launcher worked out `RLIMIT_NPROC` as the user's tasks visible in `/proc` plus the
  allowance, but the kernel counts every task of uid 10001 in the user namespace, including ones
  this container's `/proc` does not show, so the limit was below the real count, every fork
  failed, and LaTeXML (which forks for `kpsewhich`'s stand-in) waited and retried. The launcher
  now asks the kernel (`landlock_exec.fork_fits`, `count_tasks`). Reproduced locally with 400 tasks
  of the same user in another pid namespace of one container (`tests/test_sandbox_steps.py`,
  `test_a_program_can_fork_and_latexml_converts_when_most_of_the_users_tasks_are_hidden`), also with
  `--cpus=0.5`. A failed probe now also prints `latexml_step=`, `latexml_exit=`,
  `latexml_timed_out=` and `latexml_log=` (the end of LaTeXML's own output for the probe's fixed
  document, paths removed), and `group_cap_forked=`, `group_cap_exit=`, `group_cap_timed_out=`.
- **When LaTeX source is switched on.** `FEATURE_LATEX_SOURCE` takes effect only while the web
  process's start-up probe reports PASS (`/admin/health`, `sandbox.result`); it was switched on after
  a deploy showed PASS there (2026-11-04), and a later FAIL switches it off in effect. `FEATURE_BEAMER_SOURCE` and `FEATURE_BATCH_ZIP` stay off even
  then, until real Beamer decks and real course sources (zips of projects) have been tried through
  the Landlock sandbox on the deployed service.

CI builds the image with the argument once and checks the
versions and the policy (job `docker build (no push)`); the sandbox itself cannot be probed inside CI's
Docker, which does not allow user namespaces.

**Reproducing the probe in a container like Railway's** (no shell on Railway; this is how a
failing probe is found without a deploy). Build the image with LaTeXML, then run the probe as the
service's user, with a read-only root, the runtime's default seccomp profile, `/proc` as the runtime
mounts it, and no extra privileges (the app's code is the process that starts the sandbox, and it
may be pid 1 there, as in a container):

```
docker build --build-arg INSTALL_LATEXML=true -t readablestem-probe .
docker run --rm --user 10001:10001 --read-only --tmpfs /tmp:rw,exec --cap-drop=ALL \
  readablestem-probe readablestem ops sandbox-probe --require-latexml
```

With podman the same flags work (`podman run`; add `--init` to put an init in front, as the
image's ENTRYPOINT does, and compare the zombies `ps -eo stat,pid,ppid,cmd` shows after the
sandbox's caller is killed). On a host where unprivileged user namespaces are
allowed, bubblewrap will start and the Landlock backend is not tried; to see the Landlock path
there, add `-e READABLESTEM_SANDBOX_BACKEND=landlock`. The output ends in `PASS` or `FAIL`; when the
Landlock backend does not start, `landlock_step=` names the setup step that failed
(docs/security.md, "Why it did not start") and `can_<call>=` says whether the container's seccomp
profile allows each call the launcher needs. This reproduction is how the pid-1 bug above was
found (the pre-fix image printed `landlock_step=no_report` in it, as Railway did).

**pandoc.** The Dockerfile installs pandoc from the distribution in every build (no build argument):
it makes the Word download (docs/output.md, "The Word export"). It adds about 200 MB to the image (the
installed size of the Ubuntu pandoc 3.7 package is 201,810 kB; the Debian trixie package should be of the
same order, so **read the real before and after sizes from the first Railway build**). One run on the
7-picture demo page (700 KB of HTML) took 0.7 s and 248 MB resident, measured on the development box with
pandoc 3.7; the web service lets two run at once and caps each at 2 GB of heap, so allow for about 0.5 GB
more while two Word files are made. Check the build log for the pandoc version line.

## The worker as its own service

By default (`WORKER_MODE=inline`) the worker is a thread of `web`: a conversion and a request
share one process. With `WORKER_MODE=service` a second Railway service, `worker`, runs
`readablestem worker` (the same image; no web server) and `web` starts no worker. Measured on the
development box with replayed 10-page documents (no model calls), four readers loading one
review page while four conversions ran: the web process held 289 MB before and 364 MB after
inline, and 220 MB before and 257 MB after in `service` mode; the review page's median went from
291 to 312 ms inline (95th percentile 334 to 380) and from 252 to 273 ms (284 to 320) with the
worker apart. The conversion itself was already a child process, so the gain is the web
process's memory and the worker's share of its CPU, not a faster page.

**Not in `railway.ts` yet.** Adding the service to the file would make the next `railway config
apply` create and start a second service whatever `WORKER_MODE` says, so it is written here, as
the step an operator takes, and not there. Two claimers are safe (a job is claimed by one
conditional update, docs/web.md "Worker"), so the order is: add the service, check it, then
switch `web`.

1. Add this to `.railway/railway.ts` (next to `cron`), and `worker` to the `resources` list:

   ```ts
   // Conversions, apart from the web process (WORKER_MODE=service on web). Same image; no
   // domain, no health check. SIGTERM to SIGKILL: the worker stops claiming and ends its
   // running jobs' leases within 20 s (web/worker_service.py DRAIN_S), so 30 here.
   const worker = service("worker", {
     build: { buildEnvironment: "V3", builder: "RAILPACK" },
     start: "readablestem worker",
     replicas: { iad: 1 },
     deploy: {
       drainingSeconds: 30,
       limitOverride: { containers: { memoryBytes: 2 * 1024 ** 3 } }, // one slot; +1 GB a slot
     },
     env: preserved(...appVariables, ...profileVariables, "WORKER_SLOTS"),
   });
   ```

   Its variables are references to `web`'s (`${{web.NAME}}`), as `cron`'s are; `WORKER_MODE` and
   the settings of `webOnlyVariables` that conversions read (the feature switches, the
   `MAX_USD_*` caps, `CONTENT_RUNS`, `EQUATION_*`, `IMAGE_PAGES_*`, `PAGE_WITNESS_MODEL`, ...) must
   reach it too, so reference every name of `webOnlyVariables` except `PORT` and `MS_*`. No
   pre-deploy command (two services migrating at once would race). Run `railway config plan`,
   review, `railway config apply`, then `railway up --service worker`.
2. With `web` still inline, the new service starts claiming jobs beside it. Look at its logs for
   `worker_service_started`, and at `/readyz` after step 3.
3. Set `WORKER_MODE=service` on `web` (`railway variable set WORKER_MODE=service --service
   web`) and redeploy it. `/readyz` should say `"worker": "ok"` within a minute, and a test
   upload should convert.

**Rolling back is one setting:** `WORKER_MODE=inline` on `web`, then redeploy `web`; it runs its
own worker thread again. Scale `worker` to zero or remove it afterwards so that it does not keep
claiming (two claimers are safe, only needlessly costly).

**Cost.** A second service: its memory is billed by use, about 0.3 GB resident while idle
(about $3 a month at $10 per GB-month) and more while a conversion runs, which `web` no longer
holds. `WORKER_SLOTS` above 1 runs that many conversions at once in the one service and needs 1 GB
more memory for each.

## Ship a new version

If the change alters how the public demo page or the student page looks, take the landing
page's picture again first (`scripts/hero_screenshot.py`, see "Ship a new version" in
`deploy.md`) and commit it with the change.

Production is deployed from a checkout; none of its services is connected to GitHub (`source` is empty
in `railway.ts`, so `railway config apply` does not change that).

- **From a clean `main` checkout:** `railway up --service web` uploads the working tree
  (`.dockerignore` applies), builds the Dockerfile, runs the pre-deploy migration, checks `/healthz`,
  then switches traffic. It deploys uncommitted files too, so run it from a clean tree. Deploy the
  cron service the same way (`railway up --service cron`) after changes that touch `ops`.
  `.railwayignore` keeps `.claude/` and `.git/` out of the upload: a checkout that holds coding
  agents' worktrees is many gigabytes with them, and its upload failed in November 2026. If an
  upload from the checkout fails all the same, export the commit to an empty folder
  (`git archive <commit> | tar -x -C <folder>`) and name the folder: `railway up <folder>
  --service web`, run from the linked checkout.
- **GitHub-connected deploys** (not used today) would be a `source: github(...)` on the service in
  `railway.ts`, applied with the plan and apply steps above. Do not give it `build.watchPatterns`
  unless every deploy comes from GitHub: with them `railway up` skips builds.

Infrastructure changes and code deploys are separate: `railway config apply` changes settings,
`railway up` ships code. Nothing in this repository runs either for you, and none of the checks in
this guide need them.

**Roll back.** In the web service's **Deployments** list, open the menu of the last good deployment
and choose **Rollback** (Railway keeps images for 72 hours after removal; **confirm** the menu name).
This switches the code only. The database is not rolled back and the pre-deploy migration does not run
backwards: an older version starts against a newer schema (it logs `schema_ahead` and carries on), which
works while migrations are additive. A release whose migration removed or renamed something needs a
forward fix or a backup restore (deploy.md "Roll back" explains this and the stored-results limit).
A newer release can store a value the older code refuses to load: a rollback across such a release
is safe only until the first document is converted by the new release, and after that the older code
answers 500 for each such document, so fix forward.
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
  notifications (Settings > Notifications, for failed deploys and crashes; **confirm**), the GitHub
  uptime workflow (`/readyz` every 5 minutes; `/healthz/ops`, which goes 503 when the daily backup or the
  hourly cron did not finish, every 3 hours), and the model provider's own spend alert (the hard stops are the provider's workspace limit
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
Before the Google client is published, the launch-check items are what clear it.

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
pages a week cost a few dollars). With open sign-up, accounts that signed themselves up can use at most
`OPEN_TIER_DAILY_SPEND_CAP_USD` ($4) of that cap a day, and the day's cap still binds them too. Set the provider's own monthly workspace limit as the hard stop. Railway has
a usage limit setting under billing (**confirm** on Hobby): set it a little above the estimate so a runaway
service cannot bill without bound.

## The Cloud Run files

`deploy/deploy.sh`, `deploy/backups.sh` and `deploy/deploy.env.example` are the Google Cloud path and are not used
on Railway. They are kept, with deploy.md, as the documented alternative.
