# Deploying a11y-stem to Google Cloud Run

This walks through the first deploy, the parts that cannot be scripted (OpenAI spend
limit, Google sign-in client, the optional Microsoft sign-in app, custom domain), and how
to roll back. Day-to-day running is in [operations.md](operations.md).

What you end up with:

```
Cloud Scheduler (hourly) ──POST /internal/cron/aggregate-and-purge──┐
Uptime check (/healthz, every 5 min) ──────────────────────────────┤
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

## Before you start

- The gcloud CLI, signed in: `gcloud auth login`.
- A Google Cloud billing account (`gcloud billing accounts list` shows its ID).
- An OpenAI account with API access to `gpt-6.1-sol` and `gpt-6-luna`.
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

## 2. Cap OpenAI spend in the OpenAI dashboard

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
`ALERT_EMAIL`, and `BILLING_ACCOUNT_ID` (or `SKIP_BUDGET=true`). Leave `OPENAI_API_KEY`
and `GOOGLE_CLIENT_SECRET` empty and type them when asked.

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

The first run takes 15-20 minutes, most of it creating the Cloud SQL instance. The steps,
in order (run any subset with `./deploy/deploy.sh STEP ...`):

| Step | What it does |
|---|---|
| `apis` | Enables Cloud Run, Artifact Registry, Cloud Build, Compute, Secret Manager, Cloud SQL Admin, Cloud Scheduler, Monitoring, Billing Budgets and IAM |
| `iam` | Creates the `a11y-stem-run` service account (Cloud SQL client, secret accessor); gives the build account `roles/cloudbuild.builds.builder` |
| `registry` | Creates the Docker repository in Artifact Registry |
| `build` | `gcloud builds submit --tag ...`: Cloud Build builds the `Dockerfile` (tag = git commit). This runs a plain `docker build` in Cloud Build's docker 20.10 image, which may use the legacy builder, so the Dockerfile uses no BuildKit-only syntax (CI builds it both ways) |
| `database` | Cloud SQL Postgres 16, `db-f1-micro`, Enterprise edition (shared-core tiers need it; Postgres 16+ defaults to Enterprise Plus); database `a11y`. Daily backups, of which only `SQL_RETAINED_BACKUPS` (default 1) are kept, and no point-in-time recovery (see "Backups and deleted documents" below). An existing instance is patched down to that count. Skipped with `DB_MODE=external` |
| `secrets` | `openai-api-key`, `google-client-secret` (prompted), `session-secret`, `cron-token` (random), `database-url` (built from a random database password, or your external URL), and `ms-client-secret` (prompted) only when `MS_CLIENT_ID` is set. Existing secrets are kept |
| `migrate` | Cloud Run job `a11y-stem-migrate` runs `a11y-stem db upgrade`, and waits for it |
| `service` | `gcloud run deploy` with `--min-instances=1 --max-instances=2 --no-cpu-throttling --concurrency=20 --memory=1Gi --timeout=900`, a `/healthz` startup probe, the env vars and the five secrets (six with Microsoft sign-in) |
| `cron` | Cloud Scheduler job `a11y-stem-cron`: hourly POST to `/internal/cron/aggregate-and-purge` with the `X-Cron-Token` header |
| `monitoring` | Email notification channel; uptime check on `/healthz` every 5 minutes with an alert; three log-based metrics and two alert policies for the hourly cron: no `cron_done` for 2 hours, and any `cron_refused`, `aggregate_failed`, purge-backstop line or failed Cloud Scheduler attempt |
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

With `DB_MODE=external`, the same applies to your provider's backups.

Secrets never appear in the script's output. Two caveats: the generated database
password is passed to `gcloud sql users create` as an argument (gcloud has no stdin
option for it), so it is briefly visible in your machine's process list; and the cron
token is stored in the Cloud Scheduler job's header, readable by project viewers.

## 6. Check it

```sh
URL=$(gcloud run services describe a11y-stem --project="$PROJECT_ID" --region="$REGION" \
  --format='value(status.url)')
curl -fsS "$URL/healthz"
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
`https://a11y.example.org/healthz` answers, set `UPTIME_HOST=a11y.example.org` and
re-run `./deploy/deploy.sh monitoring` (it adds a check only if none exists; delete the
old one in **Monitoring > Uptime checks** first, or keep both).

## 8. Point sign-in at the domain

Once `https://a11y.example.org/healthz` answers: confirm
`https://a11y.example.org/auth/callback` is in the client's authorized redirect URIs
(step 3), and `https://a11y.example.org/auth/microsoft/callback` in the Entra app's
(step 3b) if you use it; set `BASE_URL=https://a11y.example.org` in
`deploy/.env.deploy` and re-run `./deploy/deploy.sh service`. Sign in again at the domain
(sessions are per host). You can remove the `run.app` redirect URIs afterwards.

## 9. Budget alert

Set `BILLING_ACCOUNT_ID` and re-run `./deploy/deploy.sh budget`. A Cloud Billing budget
only sends alerts; it never stops spending. The hard stops are the OpenAI project limit
(step 2) and the app's own caps. The budget covers Google Cloud only: OpenAI bills
separately (its own alerts, step 2).

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
| OpenAI | per conversion; capped by `DAILY_SPEND_CAP_USD` (at most 30x it a month) | measured $0.0037 per PDF page (document eval: 43 pages, $0.161) |

A second instance (`--max-instances=2`) only runs, and costs, under load.

## 10. GitHub settings

- **Uptime from outside Google**: repository variable `LIVE_URL` (Settings > Secrets
  and variables > Actions > Variables) = `https://a11y.example.org`. The weekly
  `uptime.yml` workflow then fetches `/healthz` and `/demo` and fails (GitHub emails you)
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
2. Set the OpenAI project's hard monthly limit (step 2) before the first deploy.
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
backup restore (or a forward fix). If data has to come back, restore a backup (this
overwrites the instance's current data):

```sh
gcloud sql backups list --filter=instance:a11y-stem-db --project="$PROJECT_ID"
gcloud sql backups restore BACKUP_ID --restore-instance=a11y-stem-db --project="$PROJECT_ID"
```

Only `SQL_RETAINED_BACKUPS` backups exist (one by default), so a restore goes back at
most that many days. A restore also brings back documents that retention had already
deleted; run
`gcloud scheduler jobs run a11y-stem-cron --location="$REGION"` right after so they are
purged again.

Secrets: every secret keeps its old versions. To go back to version 3 of the OpenAI key:

```sh
gcloud run services update a11y-stem --region="$REGION" --project="$PROJECT_ID" \
  --update-secrets=OPENAI_API_KEY=openai-api-key:3
```

## Rotate a secret

```sh
read -rs VALUE            # paste the new value; nothing is echoed
printf '%s' "$VALUE" | gcloud secrets versions add openai-api-key \
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
window). Delete the OpenAI project's key, the Google sign-in client and the Entra app
registration (if any) separately.
