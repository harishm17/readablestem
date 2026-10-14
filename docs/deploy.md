# Deploying a11y-stem to Google Cloud Run

This walks through the first deploy, the parts that cannot be scripted (the model provider's
spend limit, Google sign-in client, the optional Microsoft sign-in app, custom domain), and
how to roll back. Day-to-day running is in [operations.md](operations.md). The beta runs on
Claude (`MODEL_PROVIDER=anthropic`); OpenAI stays supported and is the script's default.

**Harish's checklist is the next section: every step only he can do is marked HARISH.**

What you end up with:

```
Cloud Scheduler (hourly) ──POST /internal/cron/aggregate-and-purge──┐
Uptime check (/readyz, every 5 min) ──────────────────────────────┤
                                                                    ▼
Instructors ── https://your-domain ──> Cloud Run service "a11y-stem" (1-2 instances,
                                        web app + worker thread, CPU always on)
                                          │  secrets from Secret Manager
                                          ▼
                                        Cloud SQL Postgres 16 (db-f1-micro)
```

`deploy/deploy.sh` does everything in that picture. It is idempotent: re-running it skips
what already exists, so it is also how you ship a new version.

Every `gcloud` command in this guide and in the script was checked against the gcloud
reference (docs.cloud.google.com/sdk/gcloud/reference) and the Cloud Run, Cloud SQL and
Cloud Monitoring guides on 2026-10-07. Two features exist only as `gcloud beta`:
notification channels and Cloud Run domain mappings.

## First deploy: Harish's checklist

Everything marked **HARISH** needs your accounts, your money or your decision; the script
does the rest (it is safe to re-run). Do these in order. `PROJECT_ID`, `REGION` and the
other names are the ones in `deploy/.env.deploy`.

**1. HARISH: tools and sign-in.** Install the gcloud CLI (cloud.google.com/sdk), `uv`, and
`openssl`, then:

```sh
gcloud auth login
cd /path/to/a11y-stem && uv sync          # the deploy checks your spend limits with it
```

**2. HARISH: Google Cloud project and billing** (step 1 has the reasoning):

```sh
gcloud billing accounts list                         # note the ID: XXXXXX-XXXXXX-XXXXXX
gcloud projects create a11y-stem-prod --name="a11y-stem"
gcloud billing projects link a11y-stem-prod --billing-account=XXXXXX-XXXXXX-XXXXXX
gcloud projects describe a11y-stem-prod --format='value(projectNumber)'   # note the number
```

**3. HARISH: Anthropic workspace, key and hard limit** (step 2, "Using Claude"). In the
Console: create a workspace "a11y-stem prod", create an API key inside it, then
**Settings > Limits** for that workspace: a monthly spend limit of **$100** (the app's own
daily cap of $10 makes $300 the most a month could ever cost; a real month of 3-5
instructors is under $15, see "Spend limits") and a usage alert at $25. Keep the key; you
type it into the script's hidden prompt. If the key is an organisation-level key instead,
note the workspace id (`wrkspc_...`).

**4. HARISH: Google sign-in** (step 3). In the project's **Google Auth Platform**: Branding
(app name, support email), Audience **External**, **Testing**, test users = the beta
instructors; **Clients > Create client > Web application** with the authorized redirect
URI `https://a11y-stem-PROJECT_NUMBER.REGION.run.app/auth/callback` (the number from
item 2; Cloud Run's URL is predictable). Copy the client ID and the client secret.

**5. HARISH: settings file.**

```sh
cp deploy/deploy.env.example deploy/.env.deploy
$EDITOR deploy/.env.deploy
```

Fill `PROJECT_ID`, `REGION` (`us-central1` supports domain mappings), `BASE_URL` (the
run.app URL from item 4), `GOOGLE_CLIENT_ID`, `CONTACT_EMAIL`, `ADMIN_EMAILS` (you),
`ALERT_EMAIL`, `BILLING_ACCOUNT_ID` (item 2). Leave `MODEL_PROVIDER=anthropic`,
`DAILY_SPEND_CAP_USD=10`, `PER_USER_DAILY_PAGE_QUOTA=90`, the feature switches and the sizing
as shipped ("Spend limits", "Content features", "Sizing"). Leave every secret empty.

**6. HARISH: run the deploy** (15-20 minutes, mostly the Cloud SQL instance). It checks the
spend limits first, then creates the database (Postgres 16, `db-f1-micro`, one daily
backup), Secret Manager secrets, the migration job, the Cloud Run service, the hourly
Cloud Scheduler job, the uptime check on `/readyz` with an email alert, the cron alerts
and the billing budget. It asks for two secrets with hidden input: the **Anthropic API
key** (stored as the secret `anthropic-api-key`, mounted into Cloud Run as
`ANTHROPIC_API_KEY`) and the **Google client secret**.

```sh
set -a; . deploy/.env.deploy; set +a
./deploy/deploy.sh
```

**7. HARISH: check it** (step 6):

```sh
URL=$(gcloud run services describe a11y-stem --project="$PROJECT_ID" --region="$REGION" \
  --format='value(status.url)')
curl -fsS "$URL/readyz"       # {"status":"ready","checks":{"database":"ok","worker":"ok","model_provider":"ok"}}
gcloud scheduler jobs run a11y-stem-cron --project="$PROJECT_ID" --location="$REGION"
```

Open `$URL`, sign in with your admin email, convert a two-page PDF with an equation and a
figure, download it, delete it. Then open **Monitoring > Alerting** and check the three
policies exist (`a11y-stem readyz failing`, `a11y-stem cron not running`, `a11y-stem cron
or purge problem`) and **Billing > Budgets** shows `a11y-stem monthly budget`.

**8. HARISH: add the instructors.** Sign in as admin, open **Admin**, add each email under
Allowlist. They must also be test users in item 4 while the consent screen is in Testing.

**9. HARISH: backups, once** (details in operations.md, "Backups and restore"):

```sh
./deploy/backups.sh status        # automated backups on, newest under 36 hours old
```

Then decide the privacy wording for backups (step 5, "Backups and deleted documents").
Run the restore drill once before real documents arrive.

**10. HARISH: domain, if you want one** (steps 7 and 8). Verify the domain, create the
mapping, add the DNS records, then set `BASE_URL`, add the new redirect URI to the Google
client and re-run `./deploy/deploy.sh service`.

**11. HARISH: before you invite anyone.** From your machine, with the production settings in
the environment, `uv run a11y-stem ops launch-check` must list nothing (it names the draft
copy, the demo, the eval baseline, and any spend-limit warning). Put the weekly reminders
in your calendar: `./deploy/backups.sh status` and a look at the **Admin** page.

If a step fails, fix the cause and re-run the same command; nothing here is done twice.

## Before you start

- The gcloud CLI, signed in: `gcloud auth login`.
- A Google Cloud billing account (`gcloud billing accounts list` shows its ID).
- An Anthropic API key with access to `claude-opus-5-5` and `claude-haiku-5-5` ("Using
  Claude" below), or, to use OpenAI instead, an account with `gpt-6.1-sol` and `gpt-6-luna`.
- `openssl` (the script uses it to generate random secrets) and this repository checked
  out at the commit you want to deploy.
- Optional: a domain you control, for a URL nicer than `*.run.app`.

## 1. Create a project just for this app

A dedicated project keeps the bill, the IAM grants and the alerts about this app only,
and tearing it all down later is one command (`gcloud projects delete PROJECT_ID`).

```sh
gcloud projects create a11y-stem-prod --name="a11y-stem"
gcloud billing projects link a11y-stem-prod --billing-account=XXXXXX-XXXXXX-XXXXXX
```

Project IDs are global; pick another if `a11y-stem-prod` is taken.

## 2. Cap spend in the model provider's dashboard

For Claude, jump to "Using Claude" below; the OpenAI dashboard steps come first and stay for
anyone who deploys on OpenAI.

The app has its own daily cap (`DAILY_SPEND_CAP_USD`) and per-job caps, but the provider's
limit is the only one that survives a bug in this code. Set it before the first deploy.

1. Create an OpenAI project for the app (for example "a11y-stem prod") and create the API
   key inside that project, so the limit below applies to every call the app makes.
   Use a different project and key for GitHub's live eval (step 10).
2. In that project: **Project settings > Limits > Spend > Edit spend limit**. Enter a
   **Monthly spend limit** and turn on **Enforce a hard limit**.
3. Add spend alerts at lower thresholds (for example 50% and 80%) so you hear about it
   before requests start failing.

Is it a hard stop? Yes, when **Enforce a hard limit** is on. Once the project reaches the
limit, API requests fail with HTTP 429 and the code `project_spend_limit_exceeded` until
the next monthly cycle or until you raise the limit. OpenAI notes that enforcement is not
instantaneous, so recorded spend can go slightly over the amount. Without that switch the
limit only sends alerts. (Source: developers.openai.com/api/docs/guides/spend-limits,
checked 2026-10-07.)

In the app, a 429 from the provider ends up as a failed model call; the document is still
assembled with the originals kept, and the fixes that could not run are flagged.

### Using Claude (Anthropic) instead of OpenAI

`MODEL_PROVIDER=anthropic` answers every model request with the Claude API instead
(`models/anthropic_client.py`); OpenAI is then not called at all. Anthropic becomes a data
processor for the app, so the privacy page changes with the setting and
`a11y-stem ops launch-check` flags a deployment whose page does not name Anthropic
(`docs/design/beta-architecture.md`, "Data path and retention with Anthropic").

1. In the Anthropic Console, create a workspace for the app (for example "a11y-stem prod")
   and an API key inside it. **Use a workspace-scoped key.** A key that is not scoped to a
   workspace (an organisation-level key) is refused with HTTP 400 "This API key is not
   scoped to a workspace" unless every request carries the workspace id. The id is
   `ANTHROPIC_WORKSPACE_ID` (`wrkspc_...`; the client sends it as the `anthropic-workspace-id`
   header). Set it only for such a key; the settings cannot tell the two kinds of key apart
   (the key text does not say), so a deployment with an organisation-level key and no id
   starts, and then fails each model call with that 400 (`call:<model>:outcome:api_error`
   in the aggregates, HTTP status 400 in the log lines). The proof's one-request API check
   (`a11y-stem proof --live --provider anthropic`) names the missing id.
2. Cap spend in the Console: **Settings > Limits** for the workspace (a monthly spend limit)
   and usage alerts, the same two layers as step 2 above. The app's own caps still apply.
3. In `deploy/.env.deploy` set `MODEL_PROVIDER=anthropic`, leave `ANTHROPIC_API_KEY` empty
   and type it when asked (the secret is `anthropic-api-key`; `OPENAI_API_KEY` is neither
   asked for nor given to the service). Optional: `ANTHROPIC_WORKSPACE_ID`,
   `ANTHROPIC_MAIN_MODEL` (default `claude-opus-5-5`) and `ANTHROPIC_CHEAP_MODEL` (default
   `claude-haiku-5-5`; `claude-sonnet-5-5` is the usual alternative). Models without a price
   in `models/pricing.py` are refused at start-up.
4. Locally, the same names go in `.env` (`.env.example`). `Settings.check_production` asks
   for `ANTHROPIC_API_KEY` instead of `OPENAI_API_KEY` when the provider is anthropic.

Things that differ from OpenAI, all on by default and safe to leave:

- The second automated check (the Decisions API, ADR 0005) is OpenAI's and is **not used**
  with Claude: no figure note, no equation shadow, no daily canary (`DECISIONS_*` are
  ignored; the canary command still needs an OpenAI key if you run it by hand). A deployment
  on Claude therefore has one automated check fewer than one on OpenAI, and recorded
  numbers from the two are not comparable on that point.
- `claude-opus-5-5` cannot turn thinking off, and `claude-sonnet-5-5` and `claude-haiku-5-5`
  think by default; the control is `ANTHROPIC_EFFORT_MAIN` (default `medium`) and
  `ANTHROPIC_EFFORT_CHEAP` (default `low`). Thinking is billed as output, and it counts
  toward `max_tokens`, so each request asks for the step's output limit plus a
  2,048-token thinking allowance (`THINKING_ALLOWANCE_TOKENS`). Measured on the held-out
  run (2,405 Claude calls): the largest answer was 707 output tokens, 671 of them
  thinking, and none was cut off. The equation step asks for 1,024 answer tokens on
  Claude (2,048 on OpenAI, whose reasoning is counted inside the limit).
- Refusal fallback: for `claude-opus-5-5` (and `claude-sonnet-5-5`) each request asks
  Anthropic to re-run a request its safety classifiers decline on the model it recommends
  (`fallbacks: "default"`, beta header `server-side-fallback-2026-07-01`,
  `ANTHROPIC_REFUSAL_FALLBACK=false` turns it off). `claude-haiku-5-5` has no such fallback;
  a decline there is a `refused` outcome, which the app then retries on the other model as
  usual. The fallback model is billed at its own rates; the reservation uses the dearer.
- Prices (`models/pricing.py`, checked 2026-10-13): claude-opus-5-5 $4 / $20 per million
  tokens (cache read $0.20, write $5), claude-sonnet-5-5 $2 / $10 (read $0.20, write $2.50),
  claude-haiku-5-5 $0.10 / $0.50 for prompts up to 100K tokens, $0.50 / $2.50 above
  (read 0.1x, write 1.25x).
- The instructions are cached (5 minutes), so from a job's second call on, the instruction
  tokens are billed at the cache-read rate.
- A reservation (the worst case held before each call, then replaced by the real cost) is
  larger than on OpenAI because it covers thinking and the image. It prices the crop that
  is actually sent: Claude counts a PNG at about width x height / 750 tokens (scaled to at
  most 2,576 px on the long edge, capped near 4,800 tokens; measured with the free
  token-counting endpoint on 2026-10-14), with a 1.35 allowance for newer tokenizers. For
  a typical 600 x 200 equation crop one `claude-opus-5-5` call reserves $0.10 ($0.002 on
  `claude-haiku-5-5`); the
  worst case (a 2,048 px crop) is $0.14, $0.22 with long descriptions and $0.53 with
  tables. It used to be a flat 6,500 image tokens plus an 8,192-token thinking allowance,
  about $0.32 a call: more than the $0.30 a one-page job was allowed, so a one-page upload
  could not make a single call and every element came back "not converted"
  (`budget_job_usd`). `tests/test_models_pricing_images.py` keeps the reservation an upper
  bound of what a call can bill.

### Spend limits

How the numbers were chosen, so they can be re-chosen. Every figure is Claude
(`claude-opus-5-5` main at effort medium, `claude-haiku-5-5` cheap at effort low, the
Decisions second check off), measured with `a11y-stem convert` on 1 vCPU
(`taskset -c 0`) on 2026-10-14, plus the existing development reports in
`.local/eval/claude-dev/`.

| What was measured | Result |
|---|---|
| Three development sections, 43 PDF pages, 3 runs (document task) | $0.0030-0.0047 per page; 71-89 calls, about 2 a page |
| 30 dense pages (physics 2.1 + 2.2), figures and equations, no long descriptions | $0.131, 62 calls: $0.0044 a page |
| The same with long descriptions on (the beta setting) | $0.209, 61 calls: $0.0070 a page |
| 5 table pages, tables off / on | $0.036 / $0.083: $0.0067 more per rebuilt table |
| Dense held-out pages (the held-out run) | about $0.014 a page |

The planning rate is **$0.015 a dense page** (`ops/limits.py`, `PLANNING_USD_PER_PAGE`);
a 30-page document at that rate is $0.45. Worst-case reservations on `claude-opus-5-5`:
$0.14 a call (equations, figures), $0.22 with long descriptions, $0.53 with tables; four
calls are in flight at once (`convert_pdf` concurrency 4).

| Setting | Claude | OpenAI | Why |
|---|---|---|---|
| `MAX_USD_PER_PAGE` | **$1.00** | $0.30 | A job's cap is at most this times its pages, and holds count: a one-page job must hold four calls at once ($0.57; $0.88 with long descriptions). $1.00 is also the most a runaway one-page job can spend |
| `MAX_USD_PER_JOB` | **$3.00** | $2.00 | A 30-page dense document is $0.45 spent plus about $0.9 held at once, so $1.3 at worst: it never reaches $3.00; a runaway stops at 7x a normal document |
| `MAX_MODEL_CALLS_PER_JOB` / `_PER_PAGE` | 400 / 80 | 400 / 80 | Measured 2-2.7 calls a page, so 30 pages is about 80 calls: 5x room |
| `DAILY_SPEND_CAP_USD` | **$10** (set by you) | any | 5 instructors converting their full quota is 5 x 90 pages x $0.015 = $6.75, plus the calls in flight (two instances x $0.9) = $8.5 |
| `PER_USER_DAILY_PAGE_QUOTA` | **90** (set by you) | any | Three full-size (30-page) documents a day each; a course week of lecture notes |

If you set `MAX_USD_PER_PAGE`/`MAX_USD_PER_JOB` yourself, they apply to whichever provider
is selected. The worst case for the day: $10 a day is $300 a month; the Anthropic
workspace limit ($100, checklist item 3) is the hard stop above that. A realistic month for
3-5 instructors converting a few documents a week is under $15.

`Settings.check_production` refuses to start (and `deploy.sh` stops before touching
anything) when a number cannot work for the provider: a page or job cap below one call's
reservation, or a daily cap below four calls held at once. `a11y-stem ops launch-check`
lists the softer problems too: a page cap below four calls (short documents may lose
elements), a job cap a normal document comes near or a runaway would run far past (over
25x), a daily cap under one job's cap or one user's quota, a quota under one full-size
document. Turning on `FEATURE_TABLES` makes the reservation $0.53 a call; the check then
asks for `MAX_USD_PER_PAGE` of at least $2.20 and `MAX_USD_PER_JOB` of at least $2.60.

To measure again: `uv run --env-file .env a11y-stem convert FILE.pdf --out out.html
--max-usd 1.5`. A small `--max-usd` stops a Claude run early, because the four calls in
flight each hold a reservation against it (use at least $1, or `--concurrency 1`).

### Content features

`deploy/.env.deploy` sets the switches, and `deploy.sh` writes them into the service. A
redeploy replaces the service's variables, so a value set by hand in the console is lost
at the next deploy: put it in the file. Choices for the beta, and why:

| Setting | Beta | Why |
|---|---|---|
| `FEATURE_LONG_DESCRIPTIONS` | on | Long descriptions and data tables for charts are what a blind student needs from a figure. Cost: figures about twice as dear ($0.13 to $0.21 for 30 pages) and one more review decision per such figure |
| `FEATURE_NOTATION_MEMORY` | on | A course's second chapter reads its notation hints. It stores course names and notation; the privacy page says so (`launch-check` verifies) |
| `FEATURE_TABLES` | off | Not through its held-out gate (docs/tables.md): off, a table stays an image with a required decision. On: real `<table>` for $0.007 each, but the worst-case reservation rises to $0.53 a call, so the dollar caps must be raised |
| `FEATURE_COLOUR` | off | No eval has turned it on |
| `DECISIONS_*` | off with Claude | The Decisions API is OpenAI's. With `MODEL_PROVIDER=anthropic` the script writes the three switches as `false`; the app ignores them for that provider either way |
| `FEEDBACK_URL` | empty | The footer then links to an email to `CONTACT_EMAIL`. A form service is a new data processor; name it on the privacy page first |

Flip a switch by editing the file and running `./deploy/deploy.sh service`.

### Sizing

Measured with `taskset -c 0` (one CPU) on the development box, Python 3.12, SLANet table
model loaded, load average 5-9 from other jobs (wall times are upper bounds; CPU seconds
are not affected):

| Run | CPU seconds (user + system) | Wall | Peak memory |
|---|---|---|---|
| 30 dense pages, fake models, tables off (4 runs) | 14.7-16.4 | 14.7-30.6 s | 437-456 MB |
| The same, tables on (3 runs) | 15.0-16.0 | 15.0-31.8 s | 437-453 MB |
| 30 pages mostly tables (35 tables), fake models, tables off / on (2 runs each) | 20.6-21.9 / 19.5-20.8 | 21.9-32.0 / 19.6-36.7 s | 444-456 MB |
| 30 dense pages, real Claude, tables off | 17.7 | 41.7 s | 497 MB |
| The same with long descriptions on | 19.5 | 51.2 s | 481 MB |
| 5 table pages, real Claude, tables off / on | 7.3 / 7.4 | 18.3 / 25.1 s | 475 / 474 MB |
| Web app + worker thread, the same table-heavy job, tables off / on (fake models) | 23.3 / 21.7 | 21.6 / 20.4 s for the job | 504 / 500 MB |

What follows from it:

- **CPU**: about 20 CPU-seconds for a 30-page document, whichever way `FEATURE_TABLES` is
  set (the table model runs either way; the flag only adds model calls). With a real model
  the conversion waits on the API about half the time (CPU 43% busy), so **1 vCPU** is
  right: a second would help only a second concurrent job, which one instance never runs.
- **Memory**: one conversion peaks near 0.5 GB including the web app. **1 GiB** leaves room
  for a 20 MB upload (held a few times over while it is parsed). 512 MiB would be too tight.
- **Speed**: 1.4-1.7 s a page on Claude, so a 30-page document is done in about a minute.
  Pages stay responsive meanwhile: a job page answered in 10 ms (median) and 155 ms (worst)
  while a 30-page conversion ran on the single CPU.
- **Concurrency** `20` requests per instance is far above need; nothing is CPU bound in a
  request. `--max-instances=2` keeps database connections under db-f1-micro's 25 (each
  instance pools up to 5), and a second instance would only take pages, not a job.
- **Request timeout** `300` seconds: it covers the upload request (a 20 MB file on a slow
  link) and every page; the conversion runs on the worker thread, which a request timeout
  does not stop. It was 900.
- **Worker**: one job at a time per instance; the model steps inside it run 4 at once
  (`convert_pdf`; more would put more reservations in flight and the CPU is not the limit).
  `WORKER_POLL_S=2`. The job time limit is 180 s plus 30 s a page (18 minutes for 30
  pages: 20x the measurement); it was 300 s plus 90 s a page. A job claimed again after a
  restart counts an attempt; three attempts end in `lease_expired`. On SIGTERM the worker
  stops claiming at once and ends the running job's lease, so the other instance (or the
  new revision) picks it up without waiting two minutes.
- Cloud Run settings: `CPU=1 MEMORY=1Gi MIN_INSTANCES=1 MAX_INSTANCES=2 CONCURRENCY=20
  REQUEST_TIMEOUT=300`, all overridable in `deploy/.env.deploy`; CPU always allocated
  (`--no-cpu-throttling`), because the worker thread runs between requests.

## 3. Create the Google sign-in client

1. In the Cloud console for the new project, open **Google Auth Platform**. Under
   **Branding** set the app name and support email; under **Audience** choose
   **External** and leave it in **Testing**, then add each beta instructor as a test
   user. Only allowlisted accounts can use the app anyway (`a11y-stem users allow`).
2. **Clients > Create client**, application type **Web application**.
3. **Authorized redirect URIs**: add the service's own URL,
   `https://a11y-stem-PROJECT_NUMBER.REGION.run.app/auth/callback` (Cloud Run's
   predictable URL; `gcloud projects describe PROJECT_ID --format='value(projectNumber)'`
   gives the number), and `https://YOUR_DOMAIN/auth/callback` if you will map a domain.
   Google requires https (only localhost may use http), an exact match, and no
   wildcards. For local development add `http://localhost:8000/auth/callback`.
4. Copy the **client ID** (goes in `GOOGLE_CLIENT_ID`) and the **client secret** (you
   type it at the script's hidden prompt; it is stored in Secret Manager only).

The app always sends `redirect_uri = BASE_URL + /auth/callback`, and keeps the sign-in
state in a cookie on the host it was started from. So sign-in only works on the host
`BASE_URL` names: registering a second URI does not let you sign in at another host.

## 3b. Optional: create the Microsoft sign-in app (Entra ID)

With this, instructors sign in with their university account ("Sign in with your
university account (Microsoft)", shown first; Google stays as the second button). Skip
it to offer Google only. One app registration serves every university; which
universities may use it is the app's setting `MS_ALLOWED_TENANTS`, not the registration.
How the app checks the tokens: web.md, "Sign-in, sessions and CSRF". Labels below are
from the Microsoft Entra admin center as documented on learn.microsoft.com (checked
2026-10-09).

1. Sign in to the Microsoft Entra admin center (entra.microsoft.com) with an account
   that may register apps (at least **Application Developer**) in the directory that
   will own the app: your own (a free Microsoft Entra tenant works), not a university's.
2. **Entra ID > App registrations > New registration.** Name: `a11y-stem` (people see it
   on Microsoft's sign-in and consent pages). **Supported account types: Multiple Entra
   ID tenants** (older wording: "Accounts in any organizational directory", multitenant).
   Not the option that adds personal Microsoft accounts. **Register.**
3. On **Overview**, copy the **Application (client) ID**: it goes in `MS_CLIENT_ID`.
4. **Authentication > Redirect URI configuration > Add Redirect URI > Web**, and add
   `https://a11y-stem-PROJECT_NUMBER.REGION.run.app/auth/microsoft/callback`, and
   `https://YOUR_DOMAIN/auth/microsoft/callback` if you will map a domain (step 7), and
   for local development `http://localhost:8000/auth/microsoft/callback` (Microsoft
   allows http only for localhost). The app sends `BASE_URL + /auth/microsoft/callback`;
   as for Google, sign-in only works on the `BASE_URL` host.
5. **ID tokens: nothing to tick.** Leave both boxes under **Implicit grant and hybrid
   flows** unticked. The app uses the authorisation code flow with PKCE, and the ID token
   comes back from the token request, not through the browser.
6. **Certificates & secrets > Client secrets > New client secret.** Pick an expiry (at
   most 24 months; Microsoft recommends under 12) and write the date in your calendar:
   on that day Microsoft sign-in stops until you rotate it ("Rotate a secret", below,
   with the secret name `ms-client-secret`). Copy the **Value** (not the Secret ID); it
   is shown once. You type it at the deploy script's hidden prompt. Microsoft recommends
   a certificate over a secret for production; the app takes a secret, as for Google.
7. **API permissions.** The app asks only for `openid profile email` (sign in, name,
   email address) and calls no Microsoft API. Under **Microsoft Graph (delegated)** keep
   `openid`, `profile` and `email`, and remove the `User.Read` permission added by
   default, so the approval page asks for as little as possible.
8. **Token configuration > Add optional claim > ID**, tick `email` and `xms_edov`, and
   accept the offer to turn on the Graph `email` permission. `xms_edov` says whether the
   directory has verified the owner of the email's domain; the app ignores an email
   claim it marks false and uses the sign-in name instead.
9. Find each university's **directory (tenant) id**: ask its IT team, or read it from
   `https://login.microsoftonline.com/UNIVERSITY_DOMAIN/v2.0/.well-known/openid-configuration`
   (the id in `issuer`). Someone from a university that is not listed sees "Your
   organisation is not set up for this beta" with their directory id to send you. List
   the ids, comma-separated, in `MS_ALLOWED_TENANTS` (step 4). People still have to be
   on the allowlist (`a11y-stem users allow EMAIL`), with the address their university
   account has.
10. **Admin consent from the university's IT.** Many universities do not let staff
    approve a multi-tenant app themselves; then the first person to sign in sees "Need
    admin approval" on Microsoft's page. An IT administrator of that university approves the app once for
    everyone there, either in their own admin center (**Enterprise apps**, after
    someone has tried to sign in) or by opening this link (one line, with their
    directory id, your client id and your redirect URI):

    ```
    https://login.microsoftonline.com/UNIVERSITY_TENANT_ID/v2.0/adminconsent
      ?client_id=MS_CLIENT_ID&scope=openid%20profile%20email
      &redirect_uri=https%3A%2F%2FYOUR_HOST%2Fauth%2Fmicrosoft%2Fcallback
    ```

    It ends on the app's sign-in page with a short thank-you note.

TODO(Harish): ask each university's IT team for admin consent, and write the request
they will read: what the app is, that it signs people in only (no access to mail,
files or anything else in Microsoft 365: scopes `openid profile email`), who runs it,
and links to the privacy notice and terms. Some IT teams also ask for publisher
verification (a Microsoft AI Cloud Partner Program id on the registration's **Branding &
properties**) before they approve a multi-tenant app; only you can enrol for that.

## 4. Fill in the settings

```sh
cp deploy/deploy.env.example deploy/.env.deploy   # git-ignored by the .env.* rule
$EDITOR deploy/.env.deploy
```

Required: `PROJECT_ID`, `REGION`, `BASE_URL`, `DAILY_SPEND_CAP_USD`,
`PER_USER_DAILY_PAGE_QUOTA`, `GOOGLE_CLIENT_ID`, `CONTACT_EMAIL`, `ADMIN_EMAILS`,
`ALERT_EMAIL`, and `BILLING_ACCOUNT_ID` (or `SKIP_BUDGET=true`). Set `MODEL_PROVIDER` (the
example file says `anthropic`). Leave `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`, only when
the provider is openai) and `GOOGLE_CLIENT_SECRET` empty and type them when asked. The
example also carries the content-feature switches ("Content features"), the optional dollar
caps, `ANTHROPIC_WORKSPACE_ID` and the Cloud Run sizing ("Sizing").

Optional, for Microsoft sign-in (step 3b): `MS_CLIENT_ID` and `MS_ALLOWED_TENANTS` (the
directory ids, comma-separated; the script checks they look like ids). Leave
`MS_CLIENT_SECRET` empty and type it when asked. With `MS_CLIENT_ID` empty the service
offers Google only, as before.

`BASE_URL` for the first deploy: the service's own URL,
`https://a11y-stem-PROJECT_NUMBER.REGION.run.app`. A custom domain is not serving until
step 7 (DNS and the managed certificate can take up to a day), and sign-in only works on
the `BASE_URL` host (step 3), so start with the run.app URL, check everything there
(step 6), and switch `BASE_URL` to the domain in step 8. Without a custom domain, keep
the run.app URL.

Region: if you want Cloud Run's own domain mapping (step 7), choose one of the regions
that support it: asia-east1, asia-northeast1, asia-southeast1, europe-north1,
europe-west1, europe-west4, us-central1, us-east1, us-east4, us-west1.

## 5. Run the deploy

```sh
set -a; . deploy/.env.deploy; set +a
./deploy/deploy.sh
```

Before any step, the script runs the same spend-limit check the app makes at start-up
(`python -m a11y_stem.ops.limits`, through `uv`) and stops if a number cannot work for the
provider. The first run takes 15-20 minutes, most of it creating the Cloud SQL instance. The steps,
in order (run any subset with `./deploy/deploy.sh STEP ...`):

| Step | What it does |
|---|---|
| `apis` | Enables Cloud Run, Artifact Registry, Cloud Build, Compute, Secret Manager, Cloud SQL Admin, Cloud Scheduler, Monitoring, Billing Budgets and IAM |
| `iam` | Creates the `a11y-stem-run` service account (Cloud SQL client, secret accessor); gives the build account `roles/cloudbuild.builds.builder` |
| `registry` | Creates the Docker repository in Artifact Registry |
| `build` | `gcloud builds submit --tag ...`: Cloud Build builds the `Dockerfile` (tag = git commit). This runs a plain `docker build` in Cloud Build's docker 20.10 image, which may use the legacy builder, so the Dockerfile uses no BuildKit-only syntax (CI builds it both ways) |
| `database` | Cloud SQL Postgres 16, `db-f1-micro`, Enterprise edition (shared-core tiers need it; Postgres 16+ defaults to Enterprise Plus); database `a11y`. Daily backups, of which only `SQL_RETAINED_BACKUPS` (default 1) are kept, and no point-in-time recovery (see "Backups and deleted documents" below). An existing instance is patched down to that count. Skipped with `DB_MODE=external` |
| `secrets` | `anthropic-api-key` (or `openai-api-key`; prompted), `google-client-secret` (prompted), `session-secret`, `cron-token` (random), `database-url` (built from a random database password, or your external URL), and `ms-client-secret` (prompted) only when `MS_CLIENT_ID` is set. Existing secrets are kept |
| `migrate` | Cloud Run job `a11y-stem-migrate` runs `a11y-stem db upgrade`, and waits for it |
| `service` | `gcloud run deploy` with `--cpu=1 --memory=1Gi --min-instances=1 --max-instances=2 --no-cpu-throttling --concurrency=20 --timeout=300` (each from `deploy.env`; "Sizing"), a `/healthz` startup probe, the env vars (including the content-feature switches) and the five secrets (six with Microsoft sign-in) |
| `cron` | Cloud Scheduler job `a11y-stem-cron`: hourly POST to `/internal/cron/aggregate-and-purge` with the `X-Cron-Token` header |
| `monitoring` | Email notification channel; uptime check on `/readyz` (database, worker, model provider) every 5 minutes with an alert; three log-based metrics and two alert policies for the hourly cron: no `cron_done` for 2 hours, and any `cron_refused`, `aggregate_failed`, purge-backstop line or failed Cloud Scheduler attempt |
| `budget` | Monthly billing budget (`BUDGET_USD`, default 90) with alerts at 50%, 90%, 100% and forecast 100%. Needs `BILLING_ACCOUNT_ID`; the script stops unless it is set or `SKIP_BUDGET=true` |

Why these Cloud Run settings: the worker thread that converts PDFs lives in the web
process, so one instance is always up and its CPU stays allocated between requests;
`--max-instances=2` bounds both cost and database connections. On db-f1-micro Postgres
allows 25 connections, which two instances at the default pool size come close to; see
operations.md before raising the instance count.

### Backups and deleted documents

Uploaded PDFs, results and filenames live in the database, so every backup holds the
documents that existed when it ran. Retention deletes a document from the live database
on time, but a backup taken before that keeps it until the backup itself is deleted.
Cloud SQL keeps 7 daily backups by default, which would stretch the app's "deleted after
`RETENTION_HOURS`" to about a week. The script keeps one (`SQL_RETAINED_BACKUPS=1`): a
deleted document can then remain in that backup for up to about a day after the purge
(until the next daily backup replaces it), and a restore can still go back one day.
Point-in-time recovery stays off (the gcloud default for the Enterprise edition): its
transaction logs would keep deleted documents for days as well.

TODO(Harish): pick the trade-off and make the privacy page agree with it. Either keep one
backup and say so ("deleted documents can remain in a database backup for up to one more
day"), or set `SQL_RETAINED_BACKUPS` higher and state that window, or turn automated
backups off for the beta (`gcloud sql instances patch a11y-stem-db --no-backup`) and keep
the current wording. The privacy page today promises deletion after `RETENTION_HOURS`
without mentioning backups.

Recommendation for the beta: keep the default (one daily backup, no point-in-time
recovery) and say so on the privacy page. What would be lost in a disaster is small and
mostly rebuildable: the allowlist, courses and notation, and the content-free counts;
documents are gone after a day by design, and an instructor can upload again. How to
check the backup, take an extra one and prove a restore works is in
[operations.md](operations.md), "Backups and restore".

With `DB_MODE=external`, the same applies to your provider's backups.

Secrets never appear in the script's output. Two caveats: the generated database
password is passed to `gcloud sql users create` as an argument (gcloud has no stdin
option for it), so it is briefly visible in your machine's process list; and the cron
token is stored in the Cloud Scheduler job's header, readable by project viewers.

## 6. Check it

```sh
URL=$(gcloud run services describe a11y-stem --project="$PROJECT_ID" --region="$REGION" \
  --format='value(status.url)')
curl -fsS "$URL/healthz"    # the process and the database answer
curl -fsS "$URL/readyz"     # ready: database, worker thread alive, model provider configured
gcloud scheduler jobs run a11y-stem-cron --project="$PROJECT_ID" --location="$REGION"
```

Then sign in at `$URL` (the same host as `BASE_URL`) with an admin email and convert a
small PDF.

## 7. Map the custom domain

Cloud Run offers three ways; for a 3-5 instructor beta the first is enough:

- **Cloud Run domain mapping**: free and simple, but in Preview ("not production-ready"
  per the Cloud Run docs) and only in the regions listed in step 4.
- **Global external Application Load Balancer**: the recommended production option
  (custom certificates, Cloud Armor, CDN), with a monthly cost of its own.
- **Firebase Hosting** in front of the service.

With domain mapping:

```sh
gcloud domains verify example.org          # once per domain; opens Search Console
gcloud beta run domain-mappings create --service=a11y-stem --domain=a11y.example.org \
  --region="$REGION" --project="$PROJECT_ID"
gcloud beta run domain-mappings describe --domain=a11y.example.org \
  --region="$REGION" --project="$PROJECT_ID"
```

Add the DNS records listed under `resourceRecords` at your DNS provider. The managed
certificate can take a while to issue after DNS resolves. When
`https://a11y.example.org/readyz` answers, set `UPTIME_HOST=a11y.example.org` and
re-run `./deploy/deploy.sh monitoring` (it adds a check only if none exists; delete the
old one in **Monitoring > Uptime checks** first, or keep both).

## 8. Point sign-in at the domain

Once `https://a11y.example.org/readyz` answers: confirm
`https://a11y.example.org/auth/callback` is in the client's authorized redirect URIs
(step 3), and `https://a11y.example.org/auth/microsoft/callback` in the Entra app's
(step 3b) if you use it; set `BASE_URL=https://a11y.example.org` in
`deploy/.env.deploy` and re-run `./deploy/deploy.sh service`. Sign in again at the domain
(sessions are per host). You can remove the `run.app` redirect URIs afterwards.

## 9. Budget alert

Set `BILLING_ACCOUNT_ID` and re-run `./deploy/deploy.sh budget`. A Cloud Billing budget
only sends alerts; it never stops spending. The hard stops are the model provider's
workspace or project limit (step 2) and the app's own caps. The budget covers Google Cloud
only: Anthropic and OpenAI bill separately (their own alerts, step 2).

### What it costs

Estimates from list prices for us-central1 (October 2026), for the setup above. Check
them in the Google Cloud pricing calculator before relying on them. TODO(Harish): confirm
the numbers for your region and set `BUDGET_USD` (default 90, about 1.5x the fixed cost,
so the alert fires only when something unusual happens).

| Item | How it is billed | About per month |
|---|---|---|
| Cloud Run, 1 vCPU always allocated (`--min-instances=1 --no-cpu-throttling`) | 2,592,000 vCPU-s x $0.000018, minus 240,000 free | $42 |
| Cloud Run, 1 GiB memory | 2,592,000 GiB-s x $0.000002, minus 450,000 free | $4 |
| Cloud SQL `db-f1-micro`, 10 GB SSD, one backup | instance hours plus storage | $9-10 |
| Artifact Registry, Secret Manager, Scheduler, Monitoring, Logging | mostly inside free tiers | under $2 |
| **Fixed total** | before any conversions | **about $57** |
| Claude | per conversion; capped by `DAILY_SPEND_CAP_USD` (at most 30x it a month) | measured $0.0044 per PDF page, $0.0070 with long descriptions, about $0.014 on dense held-out pages ("Spend limits"); OpenAI measured $0.0037 |

A second instance (`--max-instances=2`) only runs, and costs, under load.

## 10. GitHub settings

- **Uptime from outside Google**: repository variable `LIVE_URL` (Settings > Secrets
  and variables > Actions > Variables) = `https://a11y.example.org`. The weekly
  `uptime.yml` workflow then fetches `/readyz` and `/demo` and fails (GitHub emails you)
  if either does not answer 2xx. In a public repository GitHub disables scheduled
  workflows after 60 days without activity; re-enable it from the Actions tab.
- **Live eval**: repository secret `OPENAI_API_KEY`, from a separate OpenAI project with
  its own small hard limit. `eval-live.yml` runs only when started by hand and refuses a
  `max_usd` above 10.

## Before the first real users

TODO(Harish): none of this has run yet; the code and CI are ready for it. In order:

1. Push `feat/beta-app` as a pull request so every CI job (lint-test on 3.12 and 3.14,
   postgres, browser, docker, eval-gate, gitleaks) runs on the beta code once. Run
   `make ci-local` first.
2. Set the model provider's hard monthly limit (step 2) before the first deploy.
3. Deploy once to a throwaway staging project (`./deploy/deploy.sh`, all steps), sign in
   at the run.app URL, convert one PDF, run the cron by hand and check the three alert
   policies exist (Monitoring > Alerting). Then delete that project.
4. Choose and verify the domain early: domain mapping (step 7) waits on DNS and the
   managed certificate.
5. Decide the backup window and the privacy wording (see "Backups and deleted
   documents"), write the known-limits page, and remove the "Draft" notes:
   `a11y-stem ops launch-check` lists what is still unfinished.
6. If you offer Microsoft sign-in: register the app (step 3b), get admin consent from
   each university's IT team, sign in once with a university account at the run.app
   URL, and add Microsoft sign-in to the privacy notice ("Your account": what Microsoft
   sends, the directory id, and that the account's id is kept to recognise it).
   `a11y-stem ops launch-check` run with the production settings lists the privacy
   notice until it mentions Microsoft. The notice should also say that the Google
   account's id is now kept with the account.

## Ship a new version

Commit, then:

```sh
set -a; . deploy/.env.deploy; set +a
./deploy/deploy.sh build migrate service
```

The image tag is the git commit (with `-dirty-<time>` if the tree has uncommitted
changes). Cloud Run sends all traffic to the new revision once its startup probe passes.

## Roll back

Code:

```sh
gcloud run revisions list --service=a11y-stem --region="$REGION" --project="$PROJECT_ID"
gcloud run services update-traffic a11y-stem --region="$REGION" --project="$PROJECT_ID" \
  --to-revisions=a11y-stem-00012-abc=100
```

Traffic stays pinned to that revision until you run `update-traffic --to-latest` (or
deploy again and then run it). To redeploy an older image instead, run
`IMAGE_TAG=<commit> ./deploy/deploy.sh service`.

Database: migrations only go forward. An older revision starts against a schema that a
newer release migrated (it does not know that revision, logs `schema_ahead` and carries
on), so traffic rollback works as long as migrations are additive; it refuses only a
schema that is behind its own code. A release whose migration removes or renames
something cannot be rolled back by traffic alone: the old code would fail on the missing
column. So write migrations in two releases (add the new thing, ship code that uses it,
remove the old thing later), and treat rolling back past a destructive migration as a
backup restore (or a forward fix).

Stored results are the other limit. A release reads the results that releases up to its
own pipeline version stored, not newer ones: the 0.4.0 results have table elements and
fixes (`Kind.TABLE`, made whatever `FEATURE_TABLES` says), which a release before 0.4.0
(such as `9ad41db`) refuses to load. Rolled back below 0.4.0, every job with a detected
table returns 500 on its job page and review screens (the download still works) until
traffic goes forward again; jobs without tables, and the schema, are fine. Roll back
below 0.4.0 only for a short time, or not at all.

If data has to come back, restore a backup (this overwrites the instance's current
data):

```sh
gcloud sql backups list --filter=instance:a11y-stem-db --project="$PROJECT_ID"
gcloud sql backups restore BACKUP_ID --restore-instance=a11y-stem-db --project="$PROJECT_ID"
```

Only `SQL_RETAINED_BACKUPS` backups exist (one by default), so a restore goes back at
most that many days. A restore also brings back documents that retention had already
deleted; run
`gcloud scheduler jobs run a11y-stem-cron --location="$REGION"` right after so they are
purged again.

Secrets: every secret keeps its old versions. To go back to version 3 of the Anthropic key:

```sh
gcloud run services update a11y-stem --region="$REGION" --project="$PROJECT_ID" \
  --update-secrets=ANTHROPIC_API_KEY=anthropic-api-key:3
```

## Rotate a secret

The model key is the secret `anthropic-api-key` (`openai-api-key` with OpenAI).

```sh
read -rs VALUE            # paste the new value; nothing is echoed
printf '%s' "$VALUE" | gcloud secrets versions add anthropic-api-key \
  --project="$PROJECT_ID" --data-file=-
unset VALUE
./deploy/deploy.sh service      # new instances read :latest at start
```

For `cron-token`, also run `./deploy/deploy.sh cron` so the scheduler sends the new
value. For `ms-client-secret`, make the new secret in the Entra app first (step 3b,
item 6), add it as a version, deploy, check a Microsoft sign-in, then delete the old
secret in the Entra app. For `database-url`, change the user's password first
(`gcloud sql users set-password a11y --instance=a11y-stem-db --prompt-for-password`),
then add the new URL as a version.

## Tear down

`gcloud projects delete a11y-stem-prod` removes everything (after a 30-day recovery
window). Delete the Anthropic workspace's key (or the OpenAI project's), the Google sign-in client and the Entra app
registration (if any) separately.
