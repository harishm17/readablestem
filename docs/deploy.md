# Deploying a11y-stem to Google Cloud Run

This walks through the first deploy, the parts that cannot be scripted (OpenAI spend
limit, Google sign-in client, custom domain), and how to roll back. Day-to-day running is
in [operations.md](operations.md).

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
3. **Authorized redirect URIs**: add `https://YOUR_DOMAIN/auth/callback`. Before the
   domain works, also add the service URL the script prints, for example
   `https://a11y-stem-abc123-uc.a.run.app/auth/callback`. Google requires https (only
   localhost may use http), an exact match, and no wildcards. For local development add
   `http://localhost:8000/auth/callback`.
4. Copy the **client ID** (goes in `GOOGLE_CLIENT_ID`) and the **client secret** (you
   type it at the script's hidden prompt; it is stored in Secret Manager only).

The app builds the redirect URI from `BASE_URL`, so `BASE_URL` must match one of the
URIs above exactly.

## 4. Fill in the settings

```sh
cp deploy/deploy.env.example deploy/.env.deploy   # git-ignored by the .env.* rule
$EDITOR deploy/.env.deploy
```

Required: `PROJECT_ID`, `REGION`, `BASE_URL`, `DAILY_SPEND_CAP_USD`,
`PER_USER_DAILY_PAGE_QUOTA`, `GOOGLE_CLIENT_ID`, `CONTACT_EMAIL`, `ADMIN_EMAILS`,
`ALERT_EMAIL`. Leave `OPENAI_API_KEY` and `GOOGLE_CLIENT_SECRET` empty and type them when
asked. `BASE_URL` is the URL people will use; if the domain is not ready yet, set it to
the domain anyway and add the `run.app` redirect URI from step 3 meanwhile.

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
| `build` | `gcloud builds submit --tag ...`: Cloud Build builds the `Dockerfile` (tag = git commit) |
| `database` | Cloud SQL Postgres 16, `db-f1-micro`, Enterprise edition (shared-core tiers need it; Postgres 16+ defaults to Enterprise Plus), daily backups; database `a11y`. Skipped with `DB_MODE=external` |
| `secrets` | `openai-api-key`, `google-client-secret` (prompted), `session-secret`, `cron-token` (random), `database-url` (built from a random database password, or your external URL). Existing secrets are kept |
| `migrate` | Cloud Run job `a11y-stem-migrate` runs `a11y-stem db upgrade`, and waits for it |
| `service` | `gcloud run deploy` with `--min-instances=1 --max-instances=2 --no-cpu-throttling --concurrency=20 --memory=1Gi --timeout=900`, a `/healthz` startup probe, the env vars and the five secrets |
| `cron` | Cloud Scheduler job `a11y-stem-cron`: hourly POST to `/internal/cron/aggregate-and-purge` with the `X-Cron-Token` header |
| `monitoring` | Email notification channel, uptime check on `/healthz` every 5 minutes, alert policy |
| `budget` | Monthly billing budget with alerts at 50%, 90%, 100% and forecast 100% (needs `BILLING_ACCOUNT_ID`) |

Why these Cloud Run settings: the worker thread that converts PDFs lives in the web
process, so one instance is always up and its CPU stays allocated between requests;
`--max-instances=2` bounds both cost and database connections. On db-f1-micro Postgres
allows 25 connections, which two instances at the default pool size come close to; see
operations.md before raising the instance count.

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

Then sign in at `$URL` with an admin email and convert a small PDF.

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

Make sure `BASE_URL` is the domain, then confirm `https://a11y.example.org/auth/callback`
is in the client's authorized redirect URIs (step 3). If you changed `BASE_URL`, re-run
`./deploy/deploy.sh service`. You can remove the `run.app` redirect URI afterwards.

## 9. Budget alert

Set `BILLING_ACCOUNT_ID` and re-run `./deploy/deploy.sh budget`. A Cloud Billing budget
only sends alerts; it never stops spending. The hard stops are the OpenAI project limit
(step 2) and the app's own caps.

## 10. GitHub settings

- **Uptime from outside Google**: repository variable `LIVE_URL` (Settings > Secrets
  and variables > Actions > Variables) = `https://a11y.example.org`. The weekly
  `uptime.yml` workflow then fetches `/healthz` and `/demo` and fails (GitHub emails you)
  if either does not answer 2xx. In a public repository GitHub disables scheduled
  workflows after 60 days without activity; re-enable it from the Actions tab.
- **Live eval**: repository secret `OPENAI_API_KEY`, from a separate OpenAI project with
  its own small hard limit. `eval-live.yml` runs only when started by hand and refuses a
  `max_usd` above 10.

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

Database: migrations only go forward. A release whose migration removes or renames
something cannot be rolled back by traffic alone, so write migrations in two releases
(add the new thing, ship code that uses it, remove the old thing later). If data has to
come back, restore a backup (this overwrites the instance's current data):

```sh
gcloud sql backups list --filter=instance:a11y-stem-db --project="$PROJECT_ID"
gcloud sql backups restore BACKUP_ID --restore-instance=a11y-stem-db --project="$PROJECT_ID"
```

A restore also brings back documents that retention had already deleted; run
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
value. For `database-url`, change the user's password first
(`gcloud sql users set-password a11y --instance=a11y-stem-db --prompt-for-password`),
then add the new URL as a version.

## Tear down

`gcloud projects delete a11y-stem-prod` removes everything (after a 30-day recovery
window). Delete the OpenAI project's key and the Google sign-in client separately.
