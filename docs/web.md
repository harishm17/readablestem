# Web app

The hosted beta: an instructor signs in with Google or a university Microsoft account,
uploads a PDF, watches it convert, reviews the uncertain fixes and downloads one
accessible HTML file. One FastAPI process serves the pages and runs the background worker
(ADR 0004).

Code: `src/readablestem/web/`. Tests: `tests/test_web_*.py`.

## Run it locally

```
uv sync --all-groups
cp .env.example .env            # then set the values below
uv run readablestem users allow you@example.org --admin
uv run readablestem serve --reload # http://127.0.0.1:8000
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

`readablestem serve` runs `uvicorn readablestem.web.app:create_app --factory`. Options:
`--host` (default 127.0.0.1; use 0.0.0.0 in a container), `--port` (default `$PORT` or
8000), `--reload`.

## Settings the web app uses

All live in `readablestem.config.Settings` (environment or `.env`).

| Setting | Used for |
|---|---|
| `ENV` | `dev`, `test` or `prod`. Production refuses to start without its limits and secrets (`Settings.check_production`, plus `web.app.check_web_secrets`, which also refuses empty secrets) |
| `DATABASE_URL` | SQLite locally, Postgres (`postgresql+psycopg://...`) in production |
| `SESSION_SECRET` | Signs the session cookie. Required in production, at least 32 characters (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Without them the "Sign in with Google" button is hidden |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_ALLOWED_TENANTS` | Microsoft (Entra ID) sign-in, on only with all three set; tenants are directory ids, comma-separated. Half of them set refuses to start (`check_production`, any environment). See "Sign-in, sessions and CSRF" |
| `EMAIL_LOGIN_ENABLED`, `EMAIL_SENDER`, `EMAIL_LINK_MINUTES` | Sign in by an emailed one-time link (T3-10). **Off, and no email provider is wired in.** `EMAIL_SENDER` names a registered sender (`web/email_senders.py`); the settings check refuses `EMAIL_LOGIN_ENABLED=true` without one in any environment, and in production refuses a sender that is for development only, which today means always. See "Sign-in, sessions and CSRF" and docs/launch.md "Sign-in fallback and Word input" |
| `OFFICE_INPUT_ENABLED`, `SOFFICE_PATH`, `OFFICE_TIMEOUT_S` | Word and PowerPoint uploads (T3-30). On by default but taken only where LibreOffice is installed (`SOFFICE_PATH`, else `soffice` on `PATH`); `OFFICE_TIMEOUT_S` (90) is how long LibreOffice may take for one file. See "Uploads and limits" |
| `FEATURE_TABLES`, `FEATURE_LONG_DESCRIPTIONS`, `FEATURE_COLOUR`, `FEATURE_NOTATION_MEMORY` | Content features, all off by default (docs/design/content-features.md "Flags") |
| `FEEDBACK_QUESTIONS_ENABLED`, `EVENTS_ENABLED` | The two questions after a download, and content-free usage events (`events` table; see "Usage events"). Both on by default; the questions are stored as events, so they need both |
| `FEEDBACK_URL` | Where the footer's "Send feedback" link goes (http or https only). Unset: an email to `CONTACT_EMAIL`. A form service here is a new data processor: name it in the privacy notice first |
| `BASE_URL` | Builds the OAuth redirect URIs, `${BASE_URL}/auth/callback` (Google) and `${BASE_URL}/auth/microsoft/callback` (Microsoft). Production: `https://readablestem.com`, so Google's redirect URI is `https://readablestem.com/auth/callback` |
| `ADMIN_EMAILS` | Comma-separated; made allowed admins at every start-up |
| `DEV_LOGIN_ENABLED` | Enables `/dev-login` (only with `ENV=dev` or `test`) |
| `DAILY_SPEND_CAP_USD` | Global model spend per UTC day (`spend_days`) |
| `PER_USER_DAILY_PAGE_QUOTA` | Pages per user per UTC day (`quota_days`) |
| `MAX_UPLOAD_MB`, `MAX_PAGES_PER_JOB`, `MAX_CONCURRENT_UPLOADS` | Upload limits (the last: uploads taken in at once per instance, one per user) |
| `MAX_LARGE_UPLOAD_MB` (30), `MAX_SOURCE_PAGES` (300) | A file over the limits above, up to these, is kept so its pages can be chosen and converted in parts ("Large files in parts"); `MAX_LARGE_UPLOAD_MB` is also the most one upload request may carry, files together (Cloud Run takes at most 32 MiB). Equal to the limits above, they turn the feature off: such files are refused |
| `MAX_USD_PER_JOB`, `MAX_MODEL_CALLS_PER_JOB` | Per-job budget given to the model client (all attempts together). The second automated check's requests count in dollars, not as model calls |
| `MAX_USD_PER_PAGE`, `MAX_MODEL_CALLS_PER_PAGE` | Lower the per-job caps for short documents: a job may use at most this much per page of its upload (defaults $0.30 and 80), so one dense page cannot use a long document's allowance |
| `DECISIONS_FIGURE_VERIFY`, `DECISIONS_EQUATION_SHADOW`, `DECISIONS_CANARY` (and thresholds) | The second automated check (ADR 0005): an optional note on accepted figures, a shadow on equations, a daily drift canary in the cron (docs/steps.md, docs/operations.md). All on by default |
| `MODEL_PROVIDER`, `OPENAI_API_KEY`, `MAIN_MODEL`, `CHEAP_MODEL` | Which provider answers (`openai`, the default) and its key and models |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MAIN_MODEL`, `ANTHROPIC_CHEAP_MODEL`, `ANTHROPIC_WORKSPACE_ID`, `ANTHROPIC_EFFORT_MAIN`, `ANTHROPIC_EFFORT_CHEAP`, `ANTHROPIC_REFUSAL_FALLBACK` | With `MODEL_PROVIDER=anthropic`: the key (required in production instead of `OPENAI_API_KEY`), the models per role (`claude-opus-5-5`, `claude-haiku-5-5`), the workspace id an organisation-level key needs, how hard the models think, and the server-side refusal fallback. `MAIN_MODEL`/`CHEAP_MODEL` and the Decisions API check are then not used. docs/deploy.md, "Using Claude" |
| `FAKE_MODELS` | Dev and test only: every model request is answered by the offline client (placeholder output), so the app runs end to end without an OpenAI key. Refused in production |
| `RETENTION_HOURS`, `OPT_IN_RETENTION_DAYS` | When a job is deleted: 24 h after the last activity, 7 days if the user keeps it, 30 days from upload if they also let the developer look (`web/retention.py`) |
| `CRON_TOKEN` | Shared secret for `/internal/cron/aggregate-and-purge`. Required (non-empty) in production, or the purge would never run |
| `RUN_WORKER`, `WORKER_POLL_S` | Run the worker thread in this process; queue poll interval |
| `SOURCE_URL`, `CONTACT_EMAIL` | Footer source link (AGPL notice); contact on the 403, privacy and terms pages |

## Routes

| Method and path | Who | What |
|---|---|---|
| GET `/` | anyone | What the tool does, why now, how long, how accurate (numbers from docs/eval.md, linked), what to do with the file, sign-in, link to the demo |
| GET `/demo` | anyone | `web/static/demo/index.html` if present (served with the sandbox CSP), else a "being prepared" page |
| GET `/privacy`, `/terms` | anyone | The beta wording, describing the real behaviour (hourly purge, Railway's daily backups kept about 6 days, Langfuse only when tracing is on). `readablestem ops launch-check` lists any unfinished wording left on the public pages |
| GET `/limits` | anyone | Known limits: the facts that hold today (including tables, links, colour, language and file types, as this server is set up) and the measured accuracy |
| GET `/help/posting` | anyone | "Posting the file in your course": upload it to Canvas Files and link it, never paste it into a page; make the HTML the main version and keep the PDF only as a labelled print copy (28 CFR 35.202, the DOJ fact sheet); check with your accessibility office; guidance, not legal advice |
| GET `/status` | anyone | Service status: up, conversions open or paused for today, whether the AI service is answering (from this server's own last requests), documents waiting and converting (numbers only; 503 when the database does not answer) |
| GET `/login` | anyone | Sign-in page |
| POST `/login` | anyone | Redirects to Google (CSRF-checked form) |
| GET `/auth/callback` | Google | Finishes sign-in; 403 page if not allowlisted or email not verified |
| POST `/login/email`, GET and POST `/login/email/confirm` | anyone | The emailed sign-in link (404 unless `EMAIL_LOGIN_ENABLED`): the form answers with the same page for every address; the link opens a page with a button, and only the button's POST (CSRF-checked) uses the link up. See "Sign-in, sessions and CSRF" |
| POST `/login/microsoft` | anyone | Redirects to Microsoft (CSRF-checked form); 503 when Microsoft sign-in is not set up |
| GET `/auth/microsoft/callback` | Microsoft | Finishes sign-in; 403 page if the organisation is not set up for the beta or the person is not allowlisted, 400 for any token that fails a check. With `admin_consent` (an IT admin back from the approval link, deploy.md step 3b): a thank-you notice on `/login`, nothing else |
| GET, POST `/dev-login` | dev/test only | Any email, no password; the allowlist still applies |
| POST `/logout` | signed in | Clears the session |
| GET `/app?revises=` | signed in | My documents as a to-do list (below), then the upload form; with `revises=<job id>` of the person's own finished document, the form for a revised version of it ("Revised versions" below) |
| GET `/app/documents?v=&course=` | signed in | The documents list alone, polled by htmx while a document is waiting or converting (204 while unchanged); with `course=` only that course's documents (the course page polls it) |
| POST `/jobs` | signed in | Upload of one file or up to 10: checks, quota, queue, one job each; 303 to `/jobs/{id}` for one file, to `/uploads/{id}` when it is kept for choosing its pages, 303 to `/app/upload/{id}` (the results page) for several ("Uploads and limits") |
| GET `/app/download` | signed in | One zip of the person's ready documents ("Several files, courses and download all") |
| POST `/sample` | signed in | Opens the person's sample document, made from the recorded conversion; 303 to its job page ("The sample document") |
| GET `/uploads/{id}` | owner | A large file kept for choosing its pages: the suggested parts, the box to change them, "Shrink large pictures first" ("Large files in parts") |
| POST `/uploads/{id}` | owner | Convert the chosen parts: each is cut out, checked, counted against the quota and queued as a document of its own; rate limited like an upload. 400 for pages that cannot be used, 413 for a part over the size limit, 429 for the quota or the caps |
| POST `/uploads/{id}/delete` | owner | "Discard this file": only a POST with `confirm=yes` deletes it, any other POST shows the confirmation page |
| GET `/jobs/{id}` | owner | The job page: status, live progress, what is left to do, the before/after card, notices, results, language (below) |
| GET `/jobs/{id}/status?v=` | owner | Status fragment for htmx polling (204 while unchanged); changes per step |
| GET `/jobs/{id}/progress?v=` | owner | The list of steps with their counts, polled on its own outside the live region (204 while unchanged) |
| GET `/jobs/{id}/draft` | owner | Asked before a download while decisions are open: what students would get, "Review N decisions first" or "Download the draft anyway"; with none open, 303 to the finish line |
| POST `/jobs/{id}/language` | owner | Confirm or change the document language (`lang`, one the control offers); lang_source becomes "user" and the HTML is made again |
| POST `/jobs/{id}/retry` | owner | "Try again" / "Convert the missing parts" on a stopped or partly converted document (below): the same job goes back in the queue; 409 when it is not stopped or has had its 3 tries, 429 with 10 documents already waiting or converting; rate limited per address and per person |
| GET `/jobs/{id}/download` | owner | The HTML as an attachment |
| GET `/jobs/{id}/download/canvas` | owner | The Canvas export as a zip (`pipeline/export_canvas.py`, docs/output.md "The Canvas export"): `index.html` with no style block, `images/`, `README.txt`, and with `?course=<digits>` (the Canvas course number) `canvas-page.html` to paste into a Canvas Page. A bad course number is a 400 page; a job that is not done is 409, another user's or an expired one 404. Recorded as a download. The job page's "For Canvas (zip with images)" is a plain GET form (no script, no token: like the HTML download) |
| GET `/jobs/{id}/download/cartridge` | owner | The same page as an IMS Common Cartridge 1.3 package, `.imscc` (`pipeline/export_cartridge.py`, docs/output.md "The Common Cartridge export"). Same errors and recording as the Canvas zip; a plain link on the job page |
| GET `/jobs/{id}/view` | owner | The HTML inline, sandboxed (see below) |
| GET `/jobs/{id}/crops/{element_id}.png` | owner | Original image of one equation, figure or table |
| GET `/jobs/{id}/review?item=&page=&accepted=1&cell=&note=&tools=1&change=` | owner | Review screen: one decision at a time beside its page (below); `cell` (`r1c2`) opens that cell's form on a table; `note` (`link_text_unclear:p3-e4`) opens the panel of a notice about text (T3-7); `tools=1` opens the page tools and `change=<element>` the text open in them (T3-5, T3-16); `add`, `left`, `top`, `right`, `bottom` describe a region to add |
| GET `/jobs/{id}/review/list` | owner | "All items as a list": every fix, grouped by kind (no bulk accept) |
| GET `/jobs/{id}/review/marks.css` | owner | Where each mark and its frame go on its page image, and the compare sizes (element ids and numbers only) |
| GET `/jobs/{id}/pages/{n}.png` | owner | Page `n` (1-based) of the upload, rendered now at 144 dpi, never stored; 429 beyond four renders in progress for one person (a review page asks for the page and up to three crops of a repeated-heading group at once), 503 when the instance's two render slots stay busy |
| GET `/jobs/{id}/regions/{element_id}.png` | owner | An item with the page around it: an equation with the words on each side (its differing symbol outlined), a heading with the whole lines around it (outlined), a figure, a table (outlined, its caption above it in whole); 429 and 503 as for pages |
| POST `/jobs/{id}/review/{element_id}` | owner | Accept, save edit or reject one fix (a table's edit says what it changes: `change` is `cell`, `headers`, `use_local` or `not_a_table`; `action=add_data_table` adds a chart's data table read from the image; `action=describe` with `description` gives an item kept as an image its alt text, T3-16); a kind (`equations`, ...) in place of the id, the bulk accept removed on 2026-10-09, is a 404; with a heading group's key (`hg-p1-e7`), one decision for every heading of the group still waiting (with `scope=all`, for every heading of the group: a decided group decided again). A hidden `view` field (`split` or `list`) says which view to go back to, and `version` the review version the page was drawn at (a form from an out-of-date page is refused with a 409; "Stale tabs" below) |
| POST `/jobs/{id}/review/{element_id}/check` | owner | The checks Save edit would run, on the LaTeX being typed (htmx, after a 0.6 s pause): an HTML fragment, nothing stored; 204 when signed out |
| POST `/jobs/{id}/undo` | owner | Undo the job's last review write, from the page that write drew (its `version` must be the job's); a structural edit below is put back too |
| POST `/jobs/{id}/edit/{element_id}` | owner | Change what the tool did not propose (T3-5): `action=text` changes the words of a paragraph, list item or heading (`text`), `action=heading` makes a paragraph a heading (`level`). Review `version` checked; 422 with the error beside the field |
| GET `/jobs/{id}/pages/{n}/region.png?left=&top=&right=&bottom=` | owner | The region of page `n` the four edges (percent of the page) describe, cut as it would be added; rendered now in the render child, never stored; 404 for a region that is not one |
| POST `/jobs/{id}/pages/{n}/add` | owner | Add a picture the tool did not find (`kind=picture`, `alt`) or mark a region as an equation (`kind=equation`: the equation step reads it, the answer is a required decision), the region as `left`, `top`, `right`, `bottom`. Rate limited (`edit_region`, and `reread` for an equation), one read at a time per person, under the job's and the day's spend caps |
| POST `/jobs/{id}/pages/{n}/omit`, `/restore` | owner | Leave page `n` out of review and of the student file (T3-16), and put it back |
| POST `/jobs/{id}/tables/accept-clean` | owner | Accept every table that waits with nothing wrong found in it (T3-11), one write |
| POST `/jobs/{id}/tables/{element_id}/read-cell` | owner | Read a table's maths cell with the equation step and check it like a typed edit (T3-11); rate limited (`reread`), one read at a time, spend capped |
| GET, POST `/jobs/{id}/delete` | owner | A "Delete <title>? This cannot be undone" confirmation page (the file, its upload time, the decisions made on it); only a POST with `confirm=yes` from that page deletes the job and everything made from it (WCAG 3.3.4, works without JavaScript). The list's Delete links open it with `?from=app`, so "No, keep it" goes back to the list |
| GET, POST `/jobs/{id}/feedback` | owner | The two optional questions after a download, on a page of their own; POST stores them as usage events, once per document (404 when the questions are off) |
| GET, POST `/courses` | signed in | Courses (with `FEATURE_NOTATION_MEMORY` only, else 404): the user's courses; add one ("Courses and notation" below) |
| GET `/courses/{id}/download` | owner | One zip of the ready documents converted for the course ("Several files, courses and download all") |
| GET `/courses/{id}`; POST `/courses/{id}/rename`, `/courses/{id}/entries` | owner | A course's notation; rename; add a symbol |
| GET, POST `/courses/{id}/entries/{entry}`; POST `.../delete` | owner | Edit or remove one symbol |
| GET, POST `/courses/{id}/delete` | owner | "Delete <course>?" confirmation; only a POST with `confirm=yes` deletes |
| GET `/jobs/{id}/record`, `/jobs/{id}/record.csv` | owner | The review record (T3-6, "The review record" below): a page to print and a CSV; owner only, finished jobs only (409 otherwise), changes nothing |
| POST `/courses/{id}/suggestions` | owner of the course (and of the job, when sent) | Add the ticked symbols found in the course's documents; back to the course page or the job page |
| POST `/courses/{id}/remember` | owner of the course and the job | "Remember for <course>" from the review screen; back to the review screen |
| GET `/admin` | admins | Allowlist, today's spend against the cap, queue depth, aggregate counts, usage counts per day and per pseudonymous person |
| POST `/admin/allowlist`, `/admin/allowlist/remove` | admins | Add or remove someone |
| POST `/admin/linked-emails`, `/admin/linked-emails/remove` | admins | Let an allowed instructor also sign in with another Google address, or take that away (T3-10; "Sign-in, sessions and CSRF") |
| POST `/internal/cron/aggregate-and-purge` | Cloud Scheduler | `X-Cron-Token` header; aggregates, then purges |
| GET `/healthz` | anyone | `{"status": "ok"}` after a database ping, else 503 |
| GET `/readyz` | anyone | `{"status": "ready", "checks": {"database", "worker", "model_provider"}}`, one word per check, 503 `not_ready` if any is not ok (docs/operations.md, "Health, readiness and shutdown") |

Every `/jobs/*` route answers 404 for a job that does not exist or belongs to someone
else, admins included, so job ids cannot be probed and admins never see documents. Its
owner, though, is told what happened to a document that is gone (UX review MF10): a job
past its `retain_until` (not purged yet) shows "This document has been deleted. It was
deleted automatically 24 hours after upload, as promised" (the time the job was kept, whatever the choice), and
once the purge has removed the row, the owner's own `uploaded` event still says the id
was theirs, so the page says it was deleted and when documents are. Anyone else gets the
plain "There is no document at this address" page, which no longer says "or it is not
yours to see". It is a 404 either way. The
`/courses/*` routes do the same for courses.

## Sign-in, sessions and CSRF

- **Google.** Authlib's Starlette client with the discovery document
  `https://accounts.google.com/.well-known/openid-configuration` and scope
  `openid email profile`. In the Google Cloud console, add
  `${BASE_URL}/auth/callback` as an authorised redirect URI. The ID token's nonce is
  checked by Authlib; we also require `email_verified`. Access is by verified email, as
  before; the Google account (issuer `https://accounts.google.com`, its `sub`) is also
  recorded as an identity, on the first sign-in after this release.
- **Microsoft (Entra ID)**, when `MS_CLIENT_ID`, `MS_CLIENT_SECRET` and
  `MS_ALLOWED_TENANTS` are all set (`web/microsoft.py`). The sign-in page and the landing
  page then offer "Sign in with your university account (Microsoft)" first and "Sign in
  with Google" second (the secondary button style); without them, Google only, as
  before. One multi-tenant app registration (deploy.md, step 3b) at the authority
  `https://login.microsoftonline.com/organizations/v2.0` (work and school accounts),
  scope `openid profile email`, the authorisation code flow with state, nonce and PKCE
  (S256), the client secret sent in the token request's body, and `prompt=select_account`
  so someone signed in to Microsoft with the wrong account can pick another. Redirect URI:
  `${BASE_URL}/auth/microsoft/callback` (query response mode: the session cookie is
  SameSite=Lax, so a form-post answer would arrive without it). The discovery
  document's issuer is a template
  (`https://login.microsoftonline.com/{tenantid}/v2.0`) that no token matches as written,
  so we check the ID token ourselves instead of Authlib: an RS256 signature by the
  Microsoft key named by `kid` (the keys are fetched again, once, for an unknown `kid`,
  since Microsoft rotates them), `aud` equal to our client id, `tid` a directory id with
  `iss` exactly `https://login.microsoftonline.com/{tid}/v2.0` (and matching the key's
  own issuer template), the nonce this browser's sign-in started with, `exp`, `nbf` and
  `iat` with two minutes of clock skew, and finally `tid` in `MS_ALLOWED_TENANTS`. A
  token from another directory gets "Your organisation is not set up for this beta",
  with the contact address and the directory id to pass on; a token that fails any other
  check gets "Sign-in did not finish". The logs get the reason code only
  (`login_refused` or `login_failed` with `reason`, `provider`), never a claim.
- **Identities and nOAuth** (`web/identities.py`, table `auth_identities`, unique on
  provider, issuer and subject). An email claim is only as good as the directory that
  issued it, and anyone can create a directory and put any address in it ("nOAuth"). So
  a Microsoft account is recognised by its identity: issuer (which names the tenant) and
  `sub`, with `tid` kept as `tenant`. The address in the token (the `email` claim,
  unless the optional claim `xms_edov` says its domain owner is not verified; else
  `preferred_username`, the sign-in name) is read only from allowed tenants, and used
  once: on the first sign-in, the identity is linked to the user with that address only
  if they are on the allowlist. Afterwards the identity alone decides, so a changed
  address, or one that is now someone else's, does not move it. Removal from the
  allowlist still ends access. Nothing is stored for people who are refused.
  Why `sub` and not `oid`: OpenID Connect promises that `iss` and `sub` together are a
  stable identifier that is never reassigned (Core 1.0, section 5.7), the same rule as
  for Google; and Microsoft's `sub` is pairwise per app registration, so a token minted
  for another app never matches. If the app registration is ever replaced (a new client
  id), every `sub` changes and people link again by email on their next sign-in, under
  the same rules. What the binding does not protect: an account that is already linked.
  A second Microsoft identity from an allowed tenant whose address is that user's (a
  reassigned or re-created university address, or a `mail` an administrator of an
  allowed directory set) is linked to the same user too, the way the re-registration
  case needs; Google sign-in gives access by verified address alone in the same way.
  Refusing it (`login_refused reason=identity_conflict`, with an admin command to unlink
  an identity after a re-registration) is not done for the beta (docs/launch.md P8).
- **Other Google accounts for one instructor** (T3-10, `web/linked_emails.py`, table
  `linked_emails`). An instructor invited under a university address who can sign in with
  Google only as a personal one is linked by an admin (`/admin`, "Let an instructor sign in
  with another Google account", or `readablestem users link EMAIL OTHER` and `users unlink
  OTHER`). Signing in with the linked address, verified by Google as always, signs in the
  same user: same documents, courses and daily pages. Rules, each tested: the instructor
  must be on the allowlist and their own `is_allowed` decides on every request, so removing
  them ends the linked address too; a linked address is never another person's own address
  and belongs to one instructor (adding someone to the allowlist under an address that is
  linked to a different instructor is refused); at most five per instructor; removing a link
  ends the instructor's sessions everywhere (a session does not remember which address it
  came from); Google only (Microsoft signs in by directory identity). No new processor.
- **Email sign-in link** (T3-10, `web/email_login.py`, `web/email_senders.py`, table
  `login_links`), behind `EMAIL_LOGIN_ENABLED` (off). Built so that turning it on needs one
  decision and one small module, not more code: Harish picks a provider and writes a class
  with `send(message)`, registered under a name and chosen with `EMAIL_SENDER`
  (docs/launch.md). Nothing is wired in; the start-up check refuses the setting without a
  sender, and in production without one marked `production_ok`. How it works: the same
  page is the answer for every address, so the form does not say who is on the list; a link
  is made only for an allowed person's own or linked address; the token is 32 random bytes
  of which only the SHA-256 is stored; a newer link replaces older ones; it works once and
  for `EMAIL_LINK_MINUTES` (15); opening it shows a button and the button's POST (with the
  session's CSRF token) signs in, so a mail scanner fetching the link does not use it up;
  redeeming is one conditional UPDATE, so two clicks sign in once; at most three messages
  per target address in 15 minutes whoever asks, besides the per-address sign-in limits; a
  failing sender is logged by type and never shown. The link is in the URL, so Cloud Run's
  request logs and the mailbox hold it until it expires or is used.
- **Allowlist.** `users.is_allowed`, managed with `readablestem users allow|deny|list` or on
  `/admin`. It is checked on every request, so removing someone ends their access at
  once. People who are not on it see a polite 403 page with `CONTACT_EMAIL`; no row is
  created for them.
- **Sessions.** Starlette `SessionMiddleware`: a signed cookie `readablestem_session`
  (`__Host-readablestem_session` in production), HttpOnly, SameSite=Lax, 7 days, Secure in
  production. The session is cleared and a new
  CSRF token issued at sign-in. It holds the user id, the user's session version, the
  CSRF token, one-time notices (never document content) and Authlib's sign-in state (the
  state, the nonce and, for Microsoft, the PKCE verifier; each works once).
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
   `MAX_LARGE_UPLOAD_MB` (413), files together; a file over `MAX_UPLOAD_MB` is kept for
   choosing its pages ("Large files in parts"). The files stay in memory; they never touch the
   server's disk;
4. the CSRF token;
5. Word and PowerPoint (T3-30): a file that is not a PDF but is a ZIP or an old Office
   container goes to `web/office_convert.py` first, in a child process, and the PDF that
   comes back is what the next step sees (below, "Word and PowerPoint files");
5b. the PDF: magic bytes (`%PDF-` in the first 1024 bytes, else 415), then opened in a
   child process (`python -m readablestem.web.pdf_probe`) with a 30 s timeout. A crash or
   hang in MuPDF *while opening the file and counting pages* kills the child, not the
   server. Password-protected, unreadable or empty files and more than
   `MAX_PAGES_PER_JOB` pages are refused (422); a file that only opens after MuPDF
   repairs it and then has no pages (a download that did not finish) is `damaged`, not
   "no pages"; and so are refused, before any quota is spent:
   a page larger than 5000 pt on a side (`page_too_large`; MuPDF accepts page boxes far
   past the PDF maximum, and the conversion's crash probe renders whole pages, about
   1 GB for an 18000 pt page), a PDF whose every page is handwritten or scanned
   (`pipeline.page_type.document_refusal`: `handwritten` with a hidden recognition layer,
   `handwritten_no_text` without one, `scanned` with no text, `scanned_ocr` with a hidden
   OCR layer; text nobody sees never counts; each message says what the PDF is, that its
   hidden text is not used, that nothing was taken from the daily pages, and to upload a
   typed version: never "run OCR", which makes exactly these files) and one whose every
   page is blank (`no_content`). A page the probe cannot be sure of (a full-page picture
   with a short title and no hidden layer, which may be a slide; ink not analysed within
   the 10 s budget) never refuses the upload: the conversion keeps it as an image with
   a decision (docs/layout.md "Hidden text and page types"). The child caps its own address space at 768 MB. The conversion
   itself is not isolated this way; see "Limits of the worker" below;
6. the page quota: one conditional `UPDATE quota_days SET pages = pages + n WHERE ... AND
   pages + n <= quota`, in the same transaction as the job insert (if over: 429 and the
   upload page again, naming the file, its pages and the pages left today).

Validation errors re-show the upload page with an error summary linked to the file input.
For a refused handwritten or scanned PDF the summary's link is the message's first
sentence ("notes.pdf is handwritten, so this tool cannot convert it."), and the field
says the rest once: why, that no pages were taken, and what to upload instead
(`routes/jobs.py upload_error_summary`).
Every message names the file and the numbers it is about ("chapter2.pdf has 59 pages;
the limit is 30 per upload"); the file name is shown to its owner only, never logged.
With JavaScript, `app.js` checks the chosen file's size and type as soon as it is chosen
(the same field error, in a polite live region) and refuses to send a wrong one; while a
good one is sent it says "Uploading notes.pdf. Keep this tab open." and a second click
does nothing. The server checks everything again.

### Word and PowerPoint files (`web/office_convert.py`, T3-30)

A `.docx` or `.pptx` is the best source an instructor has: the headings and the picture
descriptions they wrote are in it. The upload form takes them where LibreOffice is
installed (the Writer component for Word, Impress for PowerPoint; the form and the
messages say which). The kind is decided by the file's parts, never by its name or media
type: a Word file called `notes.pdf` is converted, a PDF called `notes.docx` is a PDF.

The conversion is a child process like the PDF probe: `python -m
readablestem.web.office_convert SOFFICE TIMEOUT`, file on stdin, a JSON line and then the
PDF on stdout; a minimal environment (`core/childenv.py`); its own address-space cap; one
at a time per instance (a second waits five seconds and is told to try again, 503). Before
LibreOffice sees the file the child refuses, with a category: a ZIP that is damaged,
encrypted, has more than 3000 entries or more than 200 MB unpacked (`office_unsafe`); one
that is not exactly one Word document or one presentation (`office_unsupported`); an old
binary `.doc` or `.ppt` or a password-protected file (`office_old_or_locked`); macros
(`vbaProject.bin`, macro-enabled content types); a DOCTYPE or ENTITY declaration or UTF-16
in any XML part; and any relationship that points outside the file except an ordinary
hyperlink. The last one matters: **LibreOffice fetches the address of a linked picture
when it converts** (tested: it connects), so linked pictures, linked objects, templates and
sub-documents are refused. LibreOffice then runs headless in its own session with a
private profile in a private temporary directory (removed in a `finally`; on Cloud Run
that file system is memory), macros off, links never updated, an unreachable proxy set for
every protocol (a second line behind the check, also tested), `RLIMIT_AS` 2 GiB, a CPU
limit, an output size limit and 256 open files, and its whole process group is killed at
`OFFICE_TIMEOUT_S`. It makes a **tagged** PDF (`UseTaggedPDF`), so the headings and alt
text of T2-18 (`pipeline/tagged.py`) apply as for a PDF exported from Word. The PDF is then
probed and limited like any upload (pages, size, scanned or blank), stored as the job's
upload, and converted as usual; the Word file itself is not kept, and the daily pages
counted are the PDF's. A page count from LibreOffice can differ from Word's. Measured on
the development box: a one-page Word file converts in under a second; with the model, a
one-page file with a Word equation took 5.5 s and $0.014 on Claude.

Not done: Word fields are not evaluated (an `INCLUDETEXT` field did not read the local file
it names, tested); no PowerPoint conversion could be run on the development box (Impress is
not installed there), so that path is covered by the file checks and a stand-in only.
`readablestem convert FILE.docx` takes Word and PowerPoint files too.

A third box, off by default: "Exam or quiz: describe figures without giving answers"
(`exam`). It is stored as `jobs.exam_mode` and the worker passes it to the conversion as
`ConversionOptions.exam_mode` (`web/notation.conversion_options`, which also carries the
course notation; with neither, the run uses the settings' flags as before). Figure
descriptions then say what is shown and never the result (`pipeline/figure_step.py`).

Two separate boxes, both off by default (T1-8, `web/retention.py`): "Keep for 7 days, only
I can see it" (`keep`) and "Also let the developer look (30 days)" (`share`; implies
keeping). They set `jobs.retention_mode`: `day` (neither), `week` or `dev`; `dev` is also
`jobs.opt_in_retention`, which `ops.export` reads, so only `dev` jobs can be looked at.
`retain_until` is a full window from upload: `RETENTION_HOURS`, 7 days, or
`OPT_IN_RETENTION_DAYS`. For `day` and `week` jobs the window restarts on activity (a
review write, a language, title or contact change, a download), never earlier than it
was; `dev` keeps its 30 days from upload. "Keep 7 more days" on the job page
(`POST /jobs/{id}/keep`) makes a `day` job a `week` job and starts its seven days now. The
purge knows only `retain_until` (`ops.retention.purge_expired`), so it honours all of
this unchanged. The privacy page's three lines are the beta wording.

When today's budget is spent, the upload form is replaced by a notice saying when
uploads open again (the next midnight UTC, shown in the reader's time), instead of a form
that would send the whole file to be refused (UX review MF20). The same goes for a
person's daily page limit once no page is left ("You have converted all 20 pages you can
today. Uploads open again at ..."); before that, the hint under the file field says how
many pages are left and when the count starts again.

### Several files in one upload

The upload field takes up to `MAX_FILES_PER_UPLOAD` (10) files (`<input type="file" multiple>`;
`app.js` checks the number, each file and the total as soon as they are chosen). Each file
becomes **a job of its own**, with the course, exam and keeping choices of the form
(`routes.jobs._place_files`, `intake.store_job`), and each is held to every control an upload has:

- the probe, in a child process with its own 30 s (all the probes of one request share
  120 s, so ten files that hang cannot hold the person's upload slot for five minutes);
- the daily pages (`quota_days`, one conditional UPDATE in the job's own transaction), the
  10 documents waiting or converting and 40 kept (`intake`), checked per file: a file that
  does not fit is refused and the others still go in;
- the rate limits: the request counts once against the address (`upload`) and once per file
  against the person (`upload_user`), so ten files cost ten of the fifteen uploads a person
  may make in ten minutes;
- the token, once, and the upload gate (one request in progress per person).

One file is answered as before (303 to its job page, or the upload form again with the
reason). Several files are answered by **post-redirect-get**: the outcome of each file is
stored for an hour under the person (`upload_results`, `web/upload_result.py`; names and
numbers, no document content, deleted with the person and by the hourly purge) and the POST
answers 303 to `GET /app/upload/{id}` (`upload_results.html`, status 200 whatever came of the
files, owner only, 404 for another person's or an old one). The page lists the documents
that are converting, the large files waiting for their pages, and the files that were not
uploaded with the reason for each ("It did not use any of your pages"). A refresh, the back
button or a bookmark opens the page again and uploads nothing; before, the page was the POST's
own response and a refresh sent every file again. The outcomes are stored, not carried in the
URL or the session cookie, because the reasons name the files and a file name never goes in a
cookie. A revised version (`revises`) replaces one document, so it takes one file.

**Word and PowerPoint among several files, and large ones.** Each file is checked on its own, so
a `.docx` or `.pptx` among the files is made into a PDF in the sandbox (above) first, one
after another, and from there on the PDF is what is probed, stored and converted:
its pages are what the quota counts and what the page limits apply to. A Word file that
cannot be used is one refusal beside the others. The conversions of one request share
`OFFICE_BUDGET_S` (180 s); a file the time ran out for is refused ("was not converted: Word and
PowerPoint files take a while ...", `office_budget`), and the probes keep their own 120 s.
A Word file whose PDF is over the page or size limit of one conversion is kept for choosing its
pages like a large PDF, as the PDF (never the Word file): its parts are named as PDFs
("Long notes (pages 1-5).pdf"). A PDF over `MAX_LARGE_UPLOAD_MB` is `too_large`. A Word file can
be a revised version if it fits one conversion; a large one cannot.

### Large files in parts (`web/pending.py`, `web/routes/parts.py`, `web/ranges.py`)

A PDF over `MAX_PAGES_PER_JOB` pages or `MAX_UPLOAD_MB`, and within `MAX_SOURCE_PAGES` pages and
`MAX_LARGE_UPLOAD_MB`, is not turned away. It is kept as a **pending upload**
(`pending_uploads`: the bytes, the page count and the course, exam and keeping choices made
with it), nothing is converted or counted, and the person is sent to `/uploads/{id}`:

- **The page** says how many pages and MB the file is and what one conversion takes, and
  suggests parts: as few as fit and of nearly equal size, one per line (59 pages: `1-30` and
  `31-59`; `ranges.suggest_parts`). The person edits the box: one part per line (or after a
  semicolon), ranges with a hyphen, ranges joined with commas in one part, pages numbered
  from 1 as in a PDF viewer. What cannot be used is said in words naming the part and the
  page ("Part 2: page 70 is not in this file, which has 59 pages", `ranges.parse_parts`).
  Pages already sent are left out and cannot be sent twice.
- **Cutting.** Each part is cut out in the probe's child process (`pdf_probe extract`,
  same memory cap, 60 s), so MuPDF never touches the file in the web process, checked again
  by the probe like any upload (a part of only scanned pages is refused as a scanned
  upload is) and must be at most `MAX_UPLOAD_MB`. A part over it says so and offers the
  shrink.
- **Shrink large pictures first** (`shrink`, ticked by default for a file that is large
  because of its pictures): pictures sharper than 200 dots per inch are saved at 150 as JPEG at
  quality 80 (`Document.rewrite_images`); text and equations set as text are not touched,
  unused objects of the pages left out are dropped. A figure in the converted file can look a
  little less sharp.
- **Quota and caps.** The parts are started in order, each through `intake.store_job`: its
  pages are counted against the daily quota, the cap on documents waiting and kept holds. The
  first part that does not fit stops the run, and **the file stays** (with the pages sent
  recorded in `done_pages`) so the rest can be chosen when the count starts again. The
  page names what was started and why it stopped.
- **Each part is a document** named "chapter2 (pages 31-59).pdf", with `jobs.part_group` (the
  pending upload's id) and `jobs.part_label`; its page says which pages it holds and links
  its sibling parts in page order. Parts are separate documents, not merged into one HTML
  file.
- **Retention.** The pending upload is deleted `RETENTION_HOURS` after it was made, by the
  hourly purge (`ops.retention.purge_pending`), "Discard this file" (asks first), when its last
  page has been sent, and with the person (`erase_user`). Past its time it answers 404 at
  once. At most 2 wait per person (`pending.MAX_PENDING_PER_USER`). It is not a job: not in
  the queue, not in the 40 documents a person may keep. My documents lists the waiting files.

### Several files, courses and download all

With `FEATURE_NOTATION_MEMORY` on, the course page lists the documents converted for the
course ("Documents in this course", the same list as My documents, polling
`/app/documents?course=<id>` while any is converting) and offers "Download all N ready
documents (zip)". My documents offers the same for everything the person has
(`web/bundle.py`, `web/routes/bundle.py`):

- Only a document that is **ready to share** goes in (finished, no decision open). One with
  decisions open is a draft, and the single download asks first; the zip leaves such documents
  out and says so in its `contents.txt`.
- Names are the single download's (`slug-of-the-title-<id6>.html`), so two documents called
  "Notes" never overwrite each other. `contents.txt` lists them in words.
- It is built in memory, at most 25 documents and 80 MB of files (the rest are listed in
  `contents.txt` as not included), and with fixed timestamps so the same documents give
  the same bytes.
- It is a plain GET for the owner, like the single download: the deletion clock of the
  documents in it restarts, each counts as downloaded, a stranger gets a 404 for someone
  else's course, and with courses switched off the course route is a 404.

### The sample document (`web/sample.py`)

My documents offers "Try a sample first" (before the upload form for someone with no
document of their own, after it otherwise). `POST /sample` (token, rate limited) puts a
finished **recorded conversion** in the person's list and sends them to its page; opening it
again returns the same one. It is OpenStax *Physics* 2.3 (CC BY 4.0, 13 pages, the committed
`static/demo/report.json` and `source.pdf`: a real run with the models), with the pictures
cut from its pages as a conversion cuts them (`pipeline.run.recrop`: an unconverted equation sharp
for the size of the text) and the file written by today's assembler (one h1, the dark colour
scheme, the About region), once per process: the sample shows the current output, not the output
of the day it was recorded. A sample document a person already has was made by the code of that
day and is deleted with its day.

- **No model call for anyone**, no pages (no `quota_days` row) and no spend (cost 0), and it
  takes no place from the 10 waiting or 40 kept documents (`Job.is_sample`,
  `intake.live_job_count`).
- **Not in the numbers**: created already aggregated, no usage event is written for it
  (`events.is_sample`; no questions after its download), and its review actions are left
  out when it is deleted (`ops.retention`).
- **Theirs to use**: reviewing, deciding, previewing and downloading work as for any document,
  it is deleted by the same clock or by "Delete now", it cannot be named as the earlier version
  of a revised upload, and its page says what it is, where it comes from and its licence.

## My documents (`web/documents.py`)

`/app` is a to-do list (UX review BW5): "Needs your decisions" (finished with decisions
still open, oldest first, with a "Review next" button to the first one's review
screen), then "Ready to download" (newest first), then "Converting, waiting or stopped".
A summary line counts them ("2 documents need your decisions (5 decisions in all) · 1
ready to download · 1 converting"). Each row shows the document's title (the stored
result's title, else the file name), the file name, pages and upload time under it, the
status in words with its one next step (Review or Download), when it is deleted
automatically, and a Delete link to the confirmation page. A partly converted result
says so first ("Stopped early, 2 decisions to make"). When there are documents the list
comes before the upload form.

Only the title and the fixes are read from each stored result, through JSON paths, and
only for finished jobs. While a document is waiting or converting, htmx polls
`/app/documents` every 5 s; the list reads no stored result: the title and the number
of open decisions of a finished job are columns of the job (`jobs.result_title`,
`jobs.open_decisions`), set whenever `result_json` is assigned (a SQLAlchemy set event in
`web/db.py`), and a finished job from before them is filled in the first time the list
sees it. `list_version` (from the jobs' own columns) answers 204 when
nothing changed, otherwise the whole list is swapped. Every link in it has a stable id,
so htmx puts focus back on the same link after a swap; the list is not a live region,
but the tab title follows it ("My documents (4 of 6 finished)"). Without JavaScript a
"refresh the list" link does the same. Below 600 px each row is a block of labelled
lines instead of a table row wider than the screen (the table keeps explicit ARIA roles,
since some browsers drop a table's semantics when its display changes).

Downloads are named after the title plus the start of the job id
(`problem-set-2-kinematics-3f9a2b.html`), so two versions of a handout never overwrite
each other (UX review MF24).

## Usage events (`web/events.py`)

Server-side, content-free events answer the beta's questions (did each instructor
upload, review, download and mean to post it?) without cookies or scripts:
`signed_in`, `uploaded` (value: pages; code: a fingerprint), `reuploaded` (the same
file as an earlier upload of the same person; value: hours since), `finished` (code:
`done`, `failed:<error code>`; value: seconds since upload), `review_opened` (once per
visit of 30 minutes), `decision_made` (code: `single`, `group` or `structure` for an edit to the words, a heading, an added region or a page left out, `bulk` in older rows; value: fixes
changed), `review_completed` (the first time no decision is left open; value: seconds
since it finished), `html_viewed` (once per visit), `downloaded` (code `open` or `done`:
whether decisions were still open), and the two answers after a download, `will_post`
and `minutes_saved`. Each row is a user id, a name, a job id, a code and a number
(`events.make` refuses anything else; the column set is fixed by
`tests/test_web_content_schema.py`). The fingerprint is an HMAC of the file's SHA-256
under a key derived from `SESSION_SECRET`, salted with the user id and cut to 96 bits:
it matches that person's uploads of the same bytes and nothing else, and is never the
plain hash. "Delete now" clears it (`events.forget_upload`): the `uploaded` row stays
for the counts, but nothing derived from the deleted file does. A failing event is logged as `event_failed` and dropped; the action it
records carries on. The events of a review decision are written in its transaction.

`/admin` counts them per UTC day (last 28 days) and per pseudonymous person (`u-` and
the start of the random user id, never an email). Events are deleted after 400 days by
the hourly purge, and with their user (docs/operations.md, "Usage events").
`EVENTS_ENABLED=false` writes nothing (and turns the questions off).

## After a download (`web/feedback.py`)

Under the job page's download link (`templates/_after_download.html`): with decisions
open, under the draft link in `job.html`; once the file is ready to share, at the end
of "Put it in your course" in `_job_ready.html`, whose own line already links
`/help/posting` ("How to post it in your course", so the partial leaves its link out
there: `posting_link=false`). It has a link to `/help/posting` and two optional
questions, "Will you post this
for students?" (yes, no, not sure) and "About how many minutes did this save you?" (a
whole number up to 2000). They are in the page but hidden until the download link is
used (`app.js`); without JavaScript they show on the next visit, once the server has
recorded the download. Answers are stored as `will_post` and `minutes_saved` events,
once per document (a second answer keeps the first). An error (text in the minutes box)
shows on `/jobs/{id}/feedback` with an error summary and what was typed kept.

## Times

Every time in the page body is a `<time datetime="...Z">` element written by the `utc`
template filter, saying the time in UTC ("9 Oct 2026, 23:32 UTC"); `app.js` rewrites it
in the reader's time zone with how far away it is ("Fri, Oct 9, 6:32 PM CDT (in 23
hours)"), also in lists swapped in by htmx. Without JavaScript the UTC text stays (UX
review MF16). The `utc_text` filter is the plain text, for titles and attributes.

## Courses and notation (`web/routes/courses.py`, `web/notation.py`)

Only with `FEATURE_NOTATION_MEMORY` on. Off, every `/courses` route is a 404, the upload
form shows no course choice and ignores a `course` field, and the job page and review
screen show nothing about courses. The pipeline side is in docs/steps.md "Course
notation" and docs/layout.md "Bold letters in prose".

- **Courses** (`/courses`, "Courses" in the main navigation for a signed-in user while
  the flag is on): a table of the user's courses with how many symbols each has,
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
- **Suggestions (T3-12).** Typing `\mathbf{d}` is not needed: the course page ("Symbols
  found in your documents", the course's five latest finished documents) and the finished
  job page ("Symbols found in this document", for a job with a course) list the notation
  the documents use that the course does not have yet, from `pipeline/notation_suggest.py`:
  the style commands in the LaTeX of every equation that is used in the file
  (`\mathbf`, `\boldsymbol`, `\bm`, `\mathbb`, `\mathcal`, `\mathfrak`, `\mathsf`, `\mathtt`),
  `\vec`, `\hat`, `\overrightarrow`, `\nabla` and `\hbar`, and styled letters in the
  PDF's text (a bold d the text layer gives as U+1D41D). Each row says how many
  equations use it and the first page, with a checkbox and a meaning list that starts at
  a guess (bold, an arrow or a hat: a vector; double-struck: a set, but E and P an
  operator; the nabla an operator; the h-bar a constant; anything else "something
  else"). Nothing is added until the instructor ticks symbols and presses "Add the
  ticked symbols to <course>" (POST `/courses/{id}/suggestions`; owner of the course, and
  of the job when one is sent; the offer is worked out again on the server, so only
  symbols the documents use are taken; a symbol the course already has is skipped). They
  are saved with source "document" ("From a document" on the course page) and used from
  the next conversion. Up to 40 are shown. Recurring macros in the instructor's own
  LaTeX source are not seen: the PDF holds the expanded symbols, so the commands above
  are what can be found.
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

## Revised versions (`web/carry.py`)

An instructor who fixes a typo and uploads the PDF again should not decide the same
equations twice. The job page's "If you change the PDF" links to `GET /app?revises=<job id>`,
the ordinary upload form with a hidden `revises` field (and the earlier document's course and
exam choice as its starting values); `POST /jobs` accepts the field only for a finished,
still-kept document of the same person (else the form comes back with "was not found", status
400, and nothing is queued). The new job stores `jobs.revises_job_id`. Nothing else about the
upload changes: its pages count against the daily quota like any other conversion.

When the new job has been converted, the worker (`Worker._carry_decisions`, in the same
transaction that stores the result) reads the earlier version's result and crops, if it is
still the owner's, finished and not past its deletion time, and carries the decisions over
(`carry.carry_decisions`):

- An item is **the same item** only if its kind and content fingerprint are equal: for an
  equation, figure or table the SHA-256 of its crop, plus an equation's display mode and a
  figure's caption (the alt text is written for it); for a heading its words (case and
  spacing ignored), size and weight. One pixel of difference is a different item, and a
  changed item never inherits a decision.
- **Position** chooses among items with one fingerprint (a small figure repeated on every
  page): the nearest pairs (page first, then place on the page) are matched first, one earlier
  item to one new item. It also finds the items that **changed**: a decided earlier item with
  no match, and a new item waiting for a decision at least half inside its box, are reported
  as "changed, decide again".
- A person's decision is carried, the tool's own acceptance is not. An *accept* is carried
  only if the tool proposes the same thing again; an *edit* or a *rejection* belongs to the
  picture and is carried either way. A heading level is carried only if the output would still
  write that level there (`review.heading_level_written`). Whatever is not carried stays
  waiting, and says why in the report: `suggestion_changed` (same picture, another proposal),
  `changed`, `new` (waiting, nothing like it before) and `removed` (an earlier decision whose
  item is gone).

The HTML file is made again with the carried decisions; each carried decision is a
`ReviewEdit` with action `carried` and no write number (so Undo never takes back a whole
carry, and usage counts show `review:carried`). The report is `jobs.carried_json`: element
ids and counts, and the earlier file's name for the owner's page; no text of the document.
The job page's "Compared with the earlier version" card says how many decisions were carried
over, how many items changed or are new, and links to the first that needs a decision; the
review screen marks a decision it carried. If the earlier document was deleted before the new
one finished, the card says so and every decision is new.

**Edits that are not decisions (`web/carry_edits.py`).** The review screen also changes what no
fix covers: the words of a paragraph, a paragraph made a heading, a picture or an equation
added by hand, an image described in words, a page left out. They are carried by the same rule,
onto an item that is **unchanged**, and never onto changed content; the worker does it in this
order (`Worker._carry_decisions`): words, headings and added items, then the decisions, then
the pages left out. The earlier version is read with its omitted pages put back, so a decision
made before a page was left out is found.

- **Words and headings** are found by what the item said before the edits: the earlier
  version's `ReviewEdit` rows (`edit_text`, `make_heading`) are walked back from the result as it
  stands, so an edit that was undone is not carried and two edits in a row are one. The new
  file's item must say the same words (case and spacing aside), be of the same kind, size and
  weight, and be told from a twin by place only when both files have the same number of them.
  The inline equations in the text must be the same pictures in the same order, or the words
  are left alone. The edit is made by the review's own functions (`page_edits.edit_text`,
  `make_heading`), so a level the outline would now change is refused and nothing is left half
  done.
- **Pictures and equations added by hand** come back only on the same page, where the region
  looks exactly as it did (both PDFs drawn the same way and compared; a file that is the same
  bytes needs no drawing), and where the tool has not found something new in that place (a
  caption that was there before does not count). The equation keeps its read and what was
  decided about it.
- **A description in words** travels with the rejection it belongs to
  (`carry._put_decision`), onto the same picture only.
- **A page left out** is left out again only when it looks exactly as it did; a blank page
  is told apart from another blank page by that, not by its number alone.

What was not carried is counted (`edits_dropped`, the card says "N changes you made by hand
were not carried over, because the part of the page it was made on is not the same any
more"); what was is a `carried` `ReviewEdit` with the kind in its `after`
(`carried_edit`), shown on the record. A document **tried again** is the same file, so the
comparison needs no drawing; a picture added by hand on a page the tool could not read before
is not added a second time once the tool reads it. Any failure leaves the result as the
pipeline made it (a copy of it is put back).

## The review record (`web/record.py`, `web/routes/record.py`, T3-6)

For the person's accessibility office. `GET /jobs/{id}/record` is a page made to print
(site navigation and the buttons are hidden by `@media print`); `GET /jobs/{id}/record.csv`
has the same facts as one table. Both are made on request from what is stored, are owner
only, and change nothing (no deletion-clock restart, unlike a download). The job page links to
both under "Record for your accessibility office".

It lists, in this order: the document (title, file name, pages, when and by whom it was
converted, the tool and pipeline version, the language, when the record was made, a short
reference); what the tool did (parts found by kind, accepted by the tool with no decision
from a person, decided by a person, still open, shown as pictures); every review decision
(`ReviewEdit` row: when in UTC, the part, its page, what the tool had, the decision, by whom
- the document's owner, the only person who can review it); the decisions still open; the
parts shown as pictures and why; the automatic checks (how many ran, which did not pass);
**what was not checked**; what the instructor set (a chosen title, a contact line); and blank
lines for the office to sign. Every kind of change a person can make is a row with who and
when: accepted, edited or rejected decisions; text changed, a paragraph made a heading, a
picture added, an equation marked (the tool read it, a person checked), an image described in
words, a page left out or put back; clean tables accepted together in one action and a table
cell read by the AI service at the person's request (marks in the `ReviewEdit`'s `after`,
`review.BULK_KEY` and `MODEL_READ_KEY`); and what was carried over from an earlier version. The
summary counts the pictures and equations added, the images described and the pages left out,
and "not checked" says that the tool checked a description's form and not what it says. The
record brings the HTML file up to date first if a review write left it out of date (it lists
that file's checks), which is not a change to the document. "Not checked" is five fixed facts (no screen reader testing, the
axe test not run on the document, meaning is not checked, not tried in a course site, the PDF
itself is out of scope) plus counts from the result: the equations, figure descriptions,
headings and tables the tool accepted without a person, the decisions still open, the parts
that are pictures.

It is a record and not a verdict: the page opens with a statement that it does not say the
document meets any standard or law, and neither it nor the CSV uses the word "certification"
or "compliance" (`tests/test_web_record.py` checks both). It goes when the document goes
(`ReviewEdit` rows are deleted with the job), and the page says to print it or save the CSV
first. CSV cells that start like a formula (`=`, `+`, `-`, `@`) get a quote in front; the file
starts with a byte order mark so Excel reads non-English letters right. Its columns are
`section, item, page, tool_had, decision, by, when_utc, detail`, with the sections `document`,
`summary`, `decision`, `open`, `not_converted`, `check` and `not_checked`.
`docs/vendor-information.md` is the matching one-page sheet for a procurement office: what the
tool does, what happens to a document, and where it falls short.

## Spend cap (`web/spend.py`)

`DbSpendGuard(factory, cap_usd)` implements `core.budget.SpendGuard` on `spend_days`
(one row per UTC day). A reservation is a single conditional UPDATE:

```sql
UPDATE spend_days SET held_usd = held_usd + :a
WHERE day = :d AND spent_usd + held_usd + :a <= :cap
```

No row updated means `BudgetExceeded("daily")`. When the UPDATE finds no row and this
process has not seen the day's row yet, the row is created with an insert-ignore (`ON
CONFLICT DO NOTHING`, on SQLite and Postgres) and the UPDATE runs once more; after that a
reservation and a settlement are one UPDATE each, where each used to be an insert-ignore
and an UPDATE (a model call now costs four UPDATEs on the two rows it touches, the day's and
the job's, instead of four and two inserts). Settling moves the hold to `spent_usd` on the
day it was reserved. The Postgres test runs 20 threads against the
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
- **Idle polls.** Every `WORKER_POLL_S` (2 s) the loop does one read: is a job queued
  (the claimable kind) or has a running job's lease run out (`Worker.pending`). With
  neither it goes back to sleep, with no write, no spend snapshot and no claim attempt
  (it used to be two UPDATEs and two SELECTs in three transactions, about 130,000
  transactions and 86,000 writes a day on an idle server). Only when a lease has run out are
  the requeue UPDATEs run, and the daily budget is looked at only when a job waits.
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
  | `handwritten`, `handwritten_no_text`, `scanned`, `scanned_ocr` | Every analysed page was handwritten or scanned (`page_type.refusal_code` on the stored page types; the upload probe normally refuses these first). A result stored before page types: `scanned` when every page was only a picture of a page |
  | `too_complex` | A page needs more time or memory to read than a conversion has: the layout step's child process (open and render the pages, `layout._probe_pages`) passed its time limit (`PROBE_TIMEOUT_S` + `PROBE_PER_PAGE_S` per page after the first) or its 768 MB address-space cap (`PROBE_MEMORY_BYTES`) |
  | `no_content` | The result had no elements at all |
  | `model_not_configured` | The model client is misconfigured (no OpenAI key): every job would fail the same way |
  | `access_removed` | The owner was removed from the allowlist |
  | `timeout` | The conversion ran past its time limit |
  | `store_failed` | The result could not be written to the database |
  | `lease_expired` | The job was interrupted 3 times (since it was last tried again) |
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
  | `pages_not_converted` | Pages kept as images, not converted (handwritten, scanned, or with drawings or writing nothing could be read from), or handwriting on typed pages kept as images | "Finished, with pages not converted" ("Finished, with handwriting not converted" when only handwriting on typed pages was kept): which pages are handwritten, scanned or unread; that the HTML keeps it (or each one) as an image marked “Not converted”; that hidden text guessed from them was not used. No OCR advice |
  | `scanned_pages` | Jobs from before page types: some pages were only scanned images | The same sentence, for scanned pages |

  A finished job with no code whose equations, figures and tables mostly failed with
  `model_api_error` or `model_timeout` is shown as "Stopped: the AI service did not
  answer" (`outcome.MODEL_OUTAGE`, read from the stored result on the job page; nothing
  is stored on the job, and it goes away once enough of them are filled in). "Convert
  the missing parts" on that page tries it again ("Trying a document again", below).

### Trying a document again (`web/retry.py`, `web/step_cache.py`)

A conversion can stop for reasons that are nobody's fault. The job page offers a button, not
a dead end:

| The document | The button | Why |
|---|---|---|
| Failed with `timeout`, `lease_expired`, `store_failed`, `internal_error`, `budget_daily` or `model_not_configured` | "Try again" | Stopped by something that is not about the file. The other failure codes (a handwritten or scanned file, `no_content`, a removed account) are not offered a retry: it would stop the same way |
| Finished with `budget_daily`, or converted while the AI service was down | "Convert the missing parts" | Only the parts that were not converted are sent again |

`POST /jobs/{id}/retry` (owner only, CSRF, rate limits `retry` per address and `retry_user`
per person) puts the **same job** back in the queue: `queued`, `jobs.retries` plus one,
`jobs.attempts_at_retry` set to `attempts` (the limit of 3 interrupted attempts counts from
there). What it does not do:

- **Charge pages again.** Pages are counted when a file is uploaded (`quota_days`); this is
  not an upload, and nothing in the route touches the quota. The route restarts the
  deletion clock, as any activity does.
- **Pay for finished steps again.** While a job converts, each model step whose answer is
  worth keeping (`pipeline.run.reusable`: not `model_*`, `budget_*`, `internal_error` or
  `crop_error` failures, which a new try could change) is stored in `step_records` under a
  key made from the element, its picture, its nearby text, the conversion options and the
  models in use (`pipeline.run.step_key`). A later conversion of the same job takes those
  answers instead of calling the model, so a retry spends only on what was missing; the
  cost and calls of the steps given back are taken off the new result's totals
  (`DbStepCache.reused_usd`), so `jobs.cost_usd` counts every step once. A changed key (a
  new course notation, exam mode, another model) asks again. When a job finishes with
  nothing a retry could improve, its records are deleted at once; the rest go with the job.
- **Lose decisions.** A partly converted result keeps what the person decided: the new
  result inherits the job's own earlier decisions item by item (`web/carry.py`, the same
  rules as for a revised upload), no `ReviewEdit` is added (they already are the job's) and
  the HTML is made again.
- **Skip a limit.** A retried job waits for the daily budget like any queued job; the job's
  own spend caps cover all tries together (`jobs.cost_usd` is kept); a person with 10
  documents waiting or converting is refused (429); a job has at most 3 tries (409, "No more
  tries": upload the file again as a new document); a job that is not stopped is a 409.

**Is the AI service answering?** The converter reports how every model request ended
(`convert_pdf(call_observer=)`); `web/provider_health.py` keeps the last few minutes in
memory. When the most recent 5 requests, all within 10 minutes, ended in `api_error` or
`timeout`, the public `/status` page says "Not answering since ...", and a document that is
waiting or converting says so on its page and in its status box; one answered request ends
it. It is a hint from this server's own traffic (per process, forgotten on restart), never a
gate: nothing is refused because of it.

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
   | pages kept as images | Finished, with pages not converted | which pages and why (handwritten, scanned, nothing readable); hidden text not used |
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
   ("How to post it in your course" links the Canvas steps on `/help/posting`; upload it
   as a file and link it where the PDF was, as the main version, the PDF kept only as a
   labelled print copy, as the help page says; do not paste it into a page editor; for a
   Canvas page, "For Canvas (zip with images)" below the download, a plain form with an optional
   course number; the
   Canvas and Blackboard steps are stated as not yet tried in a live course), then the two questions after a download ("After a
   download"). Deleting is a quiet button at the end of the page.
4. **Not handled in this file** and **Worth checking**: `pipeline.notices.current(result)`
   (notices derived again from the fixes as they are now, plus the stored ones of other
   codes), one sentence each from `messages.NOTICE_MESSAGES`, with the page ("Page 3:",
   "The whole document:"), "(2 times)" for a count (a message with "{count}" in it, the
   hidden-text notices, says "(1,083 characters)" in the sentence instead), and, when the
   notice has a decision, "Decide it (one of your decisions)" or "You have decided it". A
   notice never adds a decision: the table kept as an image is counted once, as its fix,
   and the pages kept as images for one reason once, as their group. A kept page is one
   line: its hidden text that was not used is said on it, and only the first page of a
   group has "Decide it"; the others say "It is decided together with page 2."
   (`job_page.notice_lists`). The Results list counts "Pages kept as images" and
   "Handwriting kept as images" apart from the figures, the decisions name them ("the
   handwritten pages and the scanned page", never "groups of repeated figures"), and the
   waiting items say why they make fewer decisions ("pages kept as images for the same
   reason are decided together"). The finish line's heading is the status box's label:
   **Ready to share** only when everything was converted, else "Ready" or "Finished,
   with pages not converted", as on the review screen.
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
6. **Results**: what was found, and decisions as the one unit everywhere (MF6, T1-3,
   T1-11): "Decisions to make" and "Decisions you made", counted as on the review
   screen and in My documents (a group of repeated headings is one decision). A heading
   made bold text is a decision, never "shown as an image" (`job_page.shown_as_image`),
   so a job whose decisions are done is "Ready to share". Items are not counted, and
   there is no model cost, internal code or confidence here (cost is on `/admin`). One
   line says how many parts were accepted for you with a note, with a link to them. Then
   the links per kind to their first decision, and the automatic checks of the file with
   their plain sentences and a disclosure, "What was checked, and what was not": the
   structure checks ran; the axe test and screen-reader testing did not.
7. **Language**, one line with a "Change" control folded behind a details element:
   "Language: English (detected; your PDF did not say)" ("from your PDF",
   "assumed; ...", "you chose it"; a result made before detection existed counts as
   "from your PDF" when the PDF has /Lang) and a select of the languages in
   `pipeline.language.LANGUAGE_NAMES` (35) with "Confirm this language" (when it was
   detected or assumed) or "Use this language". The POST (CSRF-checked, owner only,
   finished jobs only, only a listed language or the document's own) goes through the
   text pass's rule (`text_pass.set_document_language`): `lang` and
   `lang_source="user"`, the `lang_detected` and `lang_assumed` notices dropped, and the
   passage languages worked out again against the new language. Then it makes the HTML
   again (`review.rerender_html`, with the day of the last decision for the student
   note) and bumps `jobs.result_version`. The language tag is not logged.
8. **For students** (`web/routes/job_settings.py`, `web/titles.py`): the title students
   hear (T1-7) and a contact line (T1-17). The title field starts with the stored
   `jobs.title_override`, else what the file's `<title>` already is
   (`assemble.suggested_title`: the PDF's title or first top-level heading without its
   section number, "1 Derivatives" to "Derivatives"; "Contents" and similar names of
   parts are skipped for the first heading that is not one). Saving a different title
   stores it and makes the HTML again (`assemble.render_html(title_set=True)`): `<title>`,
   a top-level heading when the body has none, the job page heading and tab, My documents,
   the delete page and the download name. Empty, or the suggestion, clears the choice. The
   contact line is an email or an http(s) address (`titles.clean_contact`); it goes in the
   student note's "tell your instructor" sentence as a `mailto:` or web link
   (`assemble._contact_sentence`, which checks it again). Both are CSRF-checked, owner
   only, finished jobs only; an unusable line shows the job page again, status 400, with
   the error under the field and the typed text kept. Neither is logged.
9. **Keeping and deleting**: when the document will be deleted and why, "Keep 7 more
   days" (not for a `dev` job) and "Delete now".

## Review screen

Code: `web/routes/split.py` (the review screen, page images, marks), `web/queue.py` (the
decisions, numbered, in order), `web/compare.py` (what differs in an equation, crop
geometry), `web/page_images.py` and `web/pdf_probe.py` (rendering in a child process),
`web/routes/review.py` (the decision POST, Undo and the list view), `web/review.py` (rules:
order, eligibility, validation, table edits, the review version), `web/edit_check.py` (an
edit checked like a proposal), `web/speech_diff.py` (where the readings differ),
`web/return_to.py` (back to the decision after signing in again), `web/table_card.py`
and `templates/_table_card.html` (the table card), `web/messages.py` (what each code
means). Tests: `tests/test_web_review_ux.py` and
`tests/test_web_review_ux_browser.py` (the fixes from the UX review: below),
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
`tests/test_web_review_bulk.py` (order, landing, no bulk accept),
`tests/test_web_review_messages.py` (every code has a sentence),
`tests/test_web_review_triage.py` (decisions, optional checks, heading groups),
`tests/test_web_review_browser.py` and `tests/test_web_review_triage_browser.py`.
The review edits (T3-5, T3-7, T3-11, T3-16, below): `tests/test_web_edit_page.py` (the
words, a heading made, a picture or an equation added, the model's read under its caps),
`tests/test_web_edit_describe.py` (a description in words, pages left out),
`tests/test_web_notice_places.py` (notices that name their place),
`tests/test_web_edit_tables.py` (clean tables, not a table, a maths cell read),
`tests/test_web_edit_postgres.py` (the same writes on Postgres, `postgres` marker) and
`tests/test_web_edit_browser.py` (axe at 1280 and 375 px in every new state, the keyboard,
no JavaScript; `browser` marker).

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
  Under them, two readings: "Read aloud roughly as: …" (the proposal's speech text) and
  "From your PDF a screen reader gets: …" (the PDF's own text layer, `Element.text`:
  what a screen reader reads from the PDF as it is; "nothing here" when the PDF has no
  text for the equation). Like everything on the review screen, it is shown only to the
  document's owner. When the checks found a difference, the PDF's text gets a reading of
  its own, made by the same speech rules, and the words where the two readings differ
  are marked in both (`web.speech_diff`; UX review BW12): "**omega**" against "**bold
  omega**", with a line that says what the marks are. Only symbols are compared (letters
  with their style, numbers, signs and relations), never the words that carry structure
  ("to the power", "the fraction … over"), which the flat text layer cannot have; a
  style the text layer cannot carry is never marked, and multiplication and division
  style are left out as the checks leave them out. The marks are `<mark>` (bold,
  underlined, the decision tint), so they do not rest on colour.
  Accept and **Keep as image**, with "Edit the LaTeX" folded (open when there is no
  proposal or an error). When the proposal *changes the meaning* (a "Yes" in the
  table), the fix comes first: a one-click **Use bold ω** (`compare.suggest_fix`: the proposal's LaTeX with
  only that letter's style changed, `\boldsymbol` for Greek, `\mathbf` for an upright
  Latin letter, `\mathbb`/`\mathcal`/`\mathfrak` for a lost letter style, or the bold
  taken off; posted as an edit, so it goes through the same checks, and offered only
  when those checks pass and the same comparison then finds no meaning-level
  difference), the edit box open, and Accept as the secondary **Accept anyway**.
- *Figure*: its image (the stored crop), the caption, and the alt text field with
  Accept, Save edit and **Keep without a description**. With long descriptions on (docs/steps.md), a chart's
  data table comes right under the image (`table_html`, in a box that scrolls sideways
  and takes focus; its cells never break a number across lines, `table-view`). When its
  values were read from the image it is not in the student file: it is offered as an
  optional extra after the decision's form, never the decision, under "Optional: a data
  table for this chart" ("A data table was read from the chart image; the values may be
  off. Add it if you check them."), captioned "Data read from Figure 1; the values are
  approximate", with its own small form and **Add the data table**
  (`action=add_data_table`, `web.review.add_data_table`: sets
  `FigureFix.data_table_added`, changes nothing else, stays on the figure; an automatic
  accept with that note becomes the reviewer's own, a figure still waiting keeps
  waiting; Undo takes it out again). A table in the file (values from the PDF's text,
  or added) is shown as students get it. Then the long description in its own field
  ("Longer description for figure 1"; empty to remove it) and, for a table in the file,
  a **Remove the data table (keep the descriptions)** box, both saved with Save edit. The long description is cleaned
  and checked like the model's (`figure_details`: no URL or markup, 40-2,000
  characters, at most 12 lines); the table cannot be edited, only removed. Accept with
  either changed is refused like a changed alt text ("You changed the longer
  description. Choose Save edit ..."). The long description field is shown when the
  figure has a long description or a table in the file, and, empty, for a chart or
  diagram described with long descriptions on but left without one (the notice
  `long_description_missing`), so the reviewer can write it; the remove box only with a
  table in the file. A form without these fields (the list view) keeps both, and
  marking a figure decorative drops both. The list view shows the longer description
  and the data table (as students get it, or as the optional extra with its own **Add
  the data table**), with a link to change the rest on the review screen.
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
- *Single heading*: its band of the page, text and level, a level select, Accept and
  **Make bold text** (or **Make plain text**, when the line is not bold in the PDF).
- *Table* (below, "The table card").
- *Pages kept as images* (handwritten, scanned, nothing readable, or handwriting on a
  typed page; docs/layout.md "Hidden text and page types"): one card for every page of
  one reason (`routes/review.py page_group_view`; their fixes share a "pg-" group key).
  It says which pages and what they are ("Pages 2 and 3 are handwritten."), what students
  get ("This tool does not convert handwriting. In the HTML each page is an image marked
  “Not converted”, so a student using a screen reader hears only that."), shows the
  first page's image and the pages it is on, and has one button, **Keep the page images,
  marked “Not converted”** (each one's Keep as image, `web.review.apply_page_group`),
  with the hint "To make them accessible, type them up (for example in LaTeX or Word) and
  upload the typed version." (for pages of drawings or writing nothing could be read
  from, "describe it in your source file (for example as a figure with a description)").
  "On pages 2 and 3" links each page number. Each page of a waiting group counts the
  decision in the page strip. "Hidden text in the PDF" (folded) gives only a count of the
  hidden characters that were not used, never the text. There is nothing to accept and
  no promise of a later conversion. The wording is the beta wording;
  its second button ("Leave this page out of the HTML") is not built.

**"Reject" is named for what it does** (UX review MF14), per kind
(`routes/review.py reject_words`): "Keep as image" for an equation, "Keep without a
description" for a figure, "Keep as image" for a table, "Make bold text" or "Make plain text" for a heading, "Keep the
original" otherwise. A line under the buttons, tied to the button by
`aria-describedby`, says what that means for students ("Keep as image: Students see a
picture of the original; screen readers hear the PDF text of the equation, marked as not
checked."; "Equation not converted" is what they hear only when the PDF has no text there),
and the notice after it and the item's state say the same in other words ("Kept as an image by
you"). The action posted is still `reject`; stored results, ops counts and the output
are unchanged.

"Details for this item" (folded) keeps the confidence, every failed check with its
sentence and code. Previous and next decision links close the card. After Accept, Save
edit or the "Reject" button the form redirects (303) to the next decision still open
after this one (`queue.next_open`, wrapping round), or to the done state, the finish
line as the job page says it (`SplitBuilder.ready`, from `job_page.headline` and
`job_page.Finish`): **Ready to share** ("You made 3 decisions on 9 October." or "Nothing
in this file needed your decision."), or **Ready** with what is shown as an image ("2
tables are shown as an image that screen readers cannot read."), then **Download the
HTML file**, "Preview the student version", a line on reading it through before sharing
(and the notes, optional), "How to post it in your course" and "Back to the results". A
job that stopped early shows its outcome instead, with no download button. There the
decision's heading takes focus
(`autofocus` on the `tabindex="-1"` heading, which works without JavaScript; no URL
fragment, which would cancel autofocus) and is described by a status line above it:
"Equation 1: Accepted. 2 decisions left." The page title starts with the position
("Decision 2 of 3: Review …"). A refused edit comes back on the same decision (422) with
the error summary and the field open.

**Undo** (MF14). The status line after a decision has an **Undo** button (one Shift+Tab
from the heading that took focus). It puts back every fix the job's last review write
changed, from the `before` snapshot its `ReviewEdit` stored (status, proposal, checks and
`reviewed`; `web.review.undo_last`), re-renders the HTML once and is a write of its own
(counted, recorded as `undo` ReviewEdits), then lands on the item again ("Undone. It is
waiting for your decision again."). It undoes only "what I just did": the form carries
the version that write made, so a decision made since (in another tab) refuses it
("Nothing was undone: this document changed after this page was opened."), and an undo is
never offered for an undo. Edits stored before snapshots held the checks cannot be
undone, so Undo is not offered for them. The list view offers "Undo the last decision" the
same way.

**Your decisions apart** (MF14) **and lines that name their item** (MF15). Below the
optional checks, "Your decisions (N)" (folded) lists what the reviewer decided, in the
queue's order, then any other item they changed: "Equation 1, page 1 *ω* Edited by you.",
"Repeated heading, 3 times “Teacher Support” All 3 were made bold text." Each line of
both folded lists says what the item is, not only its number: a heading's words, the
start of the alt text (60 characters), or the equation itself, small (re-serialised
MathML). The list view splits "Accepted or decided" the same way into "Your decisions"
and "Accepted automatically", and its folded summary lines name the item too.

**Edits are checked like proposals** (MF3; `web/edit_check.py`). Save edit used to run
only the LaTeX and MathML checks, so "bold ω" typed as words (read "b o l d omega"), or a
plain ω where the PDF has a bold one, was saved without a word and the warning that asked
for the decision went away. Now an edit gets what the equation step runs on a proposal:
the same cleaning, the LaTeX validators, the conversion, the MathML checks, the spoken
version, and the cross-checks against the PDF's own text (letters, digits and Greek
letters; letter styles, such as a vector's bold; signs and relations), with the step's
check names and codes. With FEATURE_NOTATION_MEMORY and a job read for a course, the
check runs with that course's notation in force (`web.notation.in_force`, the options
the run had): a letter the course writes bold is evidence for the style check where the
PDF shows no bold at all, and the spoken version reads it by its meaning ("vector d"),
as for the proposal. What fails is said in plain words, in three kinds:

- *cannot be used* (an error-level check: an unknown command, unbalanced braces, LaTeX
  that does not convert): never saved; the message names the problem without codes
  ("Your LaTeX was not saved. It has one more { than }: add the missing }.", "\omegaa is
  not a command this tool supports");
- *differs from the PDF where that changes the meaning* (the codes that make a proposal a
  required decision: `symbol_mismatch`, `text_layer_mismatch`, `style_mismatch`,
  `sign_mismatch`, `number_mismatch`, `case_mismatch`): not used. The card says what differs, in the compare table's words
  ("The ω is bold in the PDF (a vector) but plain in your edit."), and offers **Use my
  edit anyway** (the PDF's text is sometimes wrong). Choosing it saves the edit with the
  failed checks kept on the fix, and its `ReviewEdit` records `"anyway": true`;
- *small notes* (a cosmetic style, no spoken version): saved, and listed.

Saving the LaTeX, alt text or level the item already has is refused too ("Nothing
changed. Choose Accept to keep it as it is, or change the LaTeX first."), so it is never
recorded or counted as a correction. (Saving a rejected item's proposal unchanged puts it
back, as before.)

With JavaScript, the LaTeX is checked while it is typed: htmx posts it to
`/jobs/{id}/review/{element_id}/check` after a 0.6 s pause (`hx-sync` drops a request
overtaken by the next), and the server answers with a fragment (`_edit_check.html`): the
edit as it would look, roughly how it is read aloud, and the same three kinds of finding.
One short sentence goes into the field's polite live region ("Your LaTeX differs from the
PDF where it changes the meaning. The problem is listed below."), which also describes
the field, so a screen reader hears the state, not the whole list. The route stores and
logs nothing but the request line; signed out it answers 204 and leaves the page alone.
Save edit runs the same function, so the two never disagree.

**Stale tabs** (MF12). Every review write (a decision, a group decision, Undo)
counts itself on the job, inside its transaction after the row is locked
(`web.review.bump_result_version`, `jobs.result_version`), and every review form, in both
views, carries the number its page was drawn at (`version`). A form drawn before the last
write (a tab left open while the document was changed in another one) is refused with a
409, nothing applied: the decision is shown as it is now, with "Your choice was not
saved. This document was changed in another tab or window after this page was opened. …
Equation 1 is now: accepted by you." and a **Reload this page** link (by GET; reloading
the POST would only send it again). What was typed stays in the field. The notice takes
focus, like the error summary. A form without the field (a page from before this release,
a script) is not refused: the guard is for tabs, not a lock.

**When the session ends** (MF19; `web/return_to.py`). A session ends when the reviewer
signs out on another device (that ends every session of the account) and after 7 days.
A decision posted then used to give "This form has expired" or a bare 401, and sign-in
always went to My documents. Now:

- a page asked for while signed out is remembered, and signing in goes back to it
  (`auth.finish_login`), so a deep link to a decision survives sign-in;
- a decision (or Undo) posted while signed out answers "Please sign in again. Your session
  ended, so this was not saved. … Sign in again to come back to the same decision.", with
  the sign-in buttons on the page; signing in lands on that decision. When the cookie
  itself expired (its CSRF token with it) the page is the same, not "This form has
  expired". A form whose token is from before the reviewer signed in again (in another
  tab) says the form has expired and links **Back to the decision**;
- with JavaScript, what is typed into a decision's fields is kept as a draft in this
  tab's `sessionStorage`, under the form's address (ids only; plus the form's
  `data-draft-key` where two forms post to one address, as a table's cell form, keyed by
  the cell, and its headers form do), and put back when that
  decision is shown again, with a line under the field ("What you typed before is back
  here. It is not saved yet.") that the field's description includes. The sign-in page
  for that form says the text is kept. A draft never leaves the browser and is gone when
  the tab is closed; it is forgotten when the decision is saved (every form of that
  address), when the page is drawn with that text anyway, and on Sign out in that tab.
  Choosing a table cell from the list is navigation, so it keeps no draft.

Only pages of this app are remembered (`/app`, `/admin`, a job's page, review screen,
list view or HTML, with a query of ids and numbers; one pattern), so it is not an open
redirect: `//elsewhere.example`, images, stylesheets and anything else are neither
remembered nor followed. It lives in the signed session cookie, which signing in clears.
The server never sends typed text back on these pages.

**Keyboard** (BW11). "Skip to the decision" is the second Tab stop (after "Skip to main
content"); it moves focus to the decision's heading, and from there the first control of
the decision is at most three Tab stops on (five from the top of the page). Focus after a
decision, an error and a refused stale write is described above.

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
upload probe's child process (`python -m readablestem.web.pdf_probe render ...`, 768 MB
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
- **Accept** and **Keep as image** (Reject, named for what it does: "Students see a
  picture of the table, marked “not converted”; screen readers get no table to read.").
  A table kept as an image (`table_not_rebuilt`, no proposal) has **Use the grid read
  from the PDF**, when there is one (shown in the proposal's place, "Read from the PDF's
  own text and lines, without a model"), and **Keep as image**, which rejects it: the
  document keeps the flagged image with its "not converted" note. When that grid holds
  maths its text gives flat (`assemble_tables.flattened_cells`: a vector arrow as a
  combining mark, a stacked fraction such as "K = ¹2mv²" for K = ½mv²), Keep as image is
  the main button, the grid's button is secondary, and a line says those cells must be
  written as LaTeX.

Every change to a table goes through the same route as other decisions (`change` is
`cell`, `headers` or `use_local`), is stored with a `ReviewEdit` and re-renders the
HTML. It is `edited` (decided) only when every required-tier check of the table as it
is now passes (`web.review.table_still_open`: any failed check but the table step's
notes); otherwise the change is saved and the decision stays open (`needs_review`, "Saved,
but a required check still fails, so the table still waits for your decision"), so one
cell put right cannot settle a table whose headers are still wrong, and a cell typed
unlike the PDF is not taken as settled. The reviewer goes on changing it, or decides
with Accept (the proposal as it is now) or Keep as image. After one the reviewer stays on the table, which is shown as it is
now, with "Go on to decision N" when it was the last change; Accept and Keep as image
move on as for other kinds. The rules (`web.review`, "tables"):

- *Headers.* Ticking a box makes the first row's cells column headers (`header_rows`
  1), or the first column's body cells row headers (`header_cols` 1); unticking makes
  them data cells again (a full-width section heading stays one). A model may mark the
  first column's cells without the count; the box is then ticked. The same boxes as
  before are refused ("The headers are already like that").
- *A cell.* Its text is cleaned as alt text is (invisible format characters removed,
  whitespace collapsed), at most 1,000 characters, and escaped when written; a maths
  cell's LaTeX goes through an equation edit's checks (`web.edit_check.check_cell_edit`:
  `clean_latex`, the LaTeX and MathML checks, the spoken version, and the cross-checks
  against the PDF's text in that cell, `table_step.cell_text_layer`: letters, digits and
  Greek letters, a letter's style, signs). LaTeX that differs from the PDF where that
  changes the meaning is refused with **Use my edit anyway** on the cell's form, as for
  an equation (the failed checks stay on the fix and the ReviewEdit records the choice).
  Its plain text stays as the serialiser's fallback. A cell of the grid read from the PDF
  whose maths its text gives flat is edited as LaTeX too (its hint quotes the flat
  text). Text typed into an empty cell of the header row or column makes it a header.
  The same text again is refused.
- *Structure.* An edit that would give the grid a structural problem it did not have is
  refused with what it would break ("The headers were not saved. The headers would
  cover the whole table, leaving no row or column for data."): the first row of a
  one-row table, a header row whose cell spans into the body. A problem the proposal
  already had is left for the reviewer to judge and does not block a cell.
- *The grid read from the PDF* replaces the proposal with a copy of `Element.table`; the
  fix keeps its reason (`table_not_rebuilt`), so ops still counts why it was asked, but
  the card and the list no longer show it: the table is converted now. Its words are the
  PDF's own, so the notice does not say it passed the checks ("Its words are the PDF's
  own, so the checks could not compare them with anything: check it against the
  image"). Flattened maths in it is the error `table.math` `flattened_maths`: it cannot
  be accepted as it is, the decision stays open, and the document keeps the image
  (`data-reason="flattened_maths"`) until those cells are LaTeX.

After a table edit the fix's checks are the ones the review ran, as after an equation
edit: a maths cell's LaTeX and MathML checks, and the table step's own checks of the
table as it is now (`web.review.table_checks`, which runs
`pipeline.table_step.table_checks(grid, element)`: structure and headers, agreement
with the grid read from the PDF, the cells' words, the maths cells). So a finding stays
on the card until the cell is put right, and a failed one is a warning on the change
("1 check gives a warning"), never a refusal. Nothing about a table is logged but
codes: the `review` event gets `change=cell|headers|use_local`.

A table the model judged not a table of data (`table_step`: `written_as_text`, an
optional note) is shown as students get it, its text in reading order ("As students get
it: text, not a table"), with "Use this grid instead" (the grid read from the PDF, a
table again) and Keep as image; its cells and headers are not changed as text ("use the
grid read from the PDF first"). The list view offers "Use the grid read from the PDF"
for it too. A grid of numbers the model called not a table is not written as text: its
values would lose their columns, so the grid read from the PDF is proposed as a table,
a decision (`numbers_not_a_table`).

**Rough edges** (T3-11). *Accept all N clean tables*: with two or more tables that wait although
nothing was found wrong with them (`web.review.is_clean_table`: a data table with a proposal, no
failed check of any kind, no structural problem, no maths the PDF gives flat), one button on a
waiting table's card and above the list view's tables (`POST /jobs/{id}/tables/accept-clean`)
accepts them all in one write, one `ReviewEdit` each, one Undo for all; a table with a finding
or kept as an image is never in it. *This grid is not a table* (`change=not_a_table`): the
table is written as text, one cell after another (a description list for a term and its text,
a list for short terms, else a paragraph a cell), from the grid read from the PDF's own words
when there is one (`table_step.text_grid`), else the proposal's; refused for a grid with no
text or flattened maths (keep the image); it settles the decision (`edited`), and "Use this
grid instead" makes it a table again. *Read this cell with the model*: in a maths cell's form,
when the cell has a place on the page (its own box, or the box of the cell of the grid read from
the PDF in its place), the equation step reads that crop (`POST
/jobs/{id}/tables/{element}/read-cell`, the controls of "What the model costs here" above) and
what it read goes through the cell's edit checks as if typed: saved when it passes, else shown
in the cell's box with what differs from the PDF (and "Use my edit anyway").

With keyboard shortcuts on, A accepts a proposed table and E opens "Change the table" at
the cell list. In the list view a table card shows its crop, the proposal (or the grid
read from the PDF) and Accept, Keep as image, Use the grid read from the PDF;
headers and cells are changed on the review screen ("open it on the review screen").

**Stale tabs.** The table card's forms carry the review version like every other form
("Stale tabs" above, one guard: `web.review.is_stale` and `bump_result_version`). A
stale table form gets the same 409 page; a cell being changed opens again with what was
typed. Without the guard a cell changed by its place in the grid could land on another
cell once a tab elsewhere had changed the table.

**Accept never drops an edit.** Accept and Save edit share a form; Accept keeps the
proposal and never used the fields, so a correction typed and then accepted out of habit
was lost without a word. Accept with the LaTeX, the alt text, "Decorative" or the level
changed from the proposal is refused on that field ("You changed the LaTeX. Choose Save
edit to use your change, or undo it to accept the proposal."), the change kept in the
field and Save edit made the main button; nothing is saved. Fields the form did not send
are not compared (both views).

**JavaScript** (`static/app.js`, and htmx for the live check) only enhances: it scrolls
the page region so the selected mark is in its visible box, working inside the region
only (stacked, the region starts below the window, and a window term used to scroll it
to its end; side by side it also moves the window when the mark is near the foot of a
page, never so far that the decision's heading leaves the window), asks once more after
1.5 s for an image that was refused for a moment and only then says the page image could
not be shown, makes "Save edit" the main button once a field of the decision is changed,
keeps a draft of what is typed in this tab (above), and offers keyboard shortcuts: N next
decision, P previous, A accept, E go to the edit field. They are off until turned on
("Keyboard shortcuts", stored in `localStorage`; WCAG 2.1.4), ignore keys with Ctrl, Alt
or Meta, and never act while focus is in a field.

**Describe it in words** (T3-16; `web.review.describe_in_words`, `templates/_describe.html`).
An item shown as an image (an equation or table kept as the original, a page or handwriting
kept as an image; `web.review.can_describe`) has a folded "Describe it in words" box, in both
views. The text becomes the image's alt text in the student file instead of the PDF's own
text (marked not checked) or the fixed words ("Equation not converted"). It is cleaned and
checked exactly like a figure's alt text (invisible format characters out, whitespace
collapsed, `validate.check_alt`: length, no "image of", no file name, no address or markup, not a
placeholder) and escaped where it is written; a refusal comes back on the same item with the
text kept. Describing an item that waits for a decision keeps it as an image (it is the
decision); an empty box takes the description away; accepting or editing the item converts it
and drops the description. The page is still marked "Not converted" (it is an image, not
text), and a described image is no longer counted as one "that screen readers cannot read"
(`job_page.shown_as_image`). The description is `Fix.described`, in the snapshot Undo puts
back. For a group of kept pages each page has its own box.

**Notices that name their place** (T3-7; `web/notice_places.py`). A notice stays a code, a page
and an element id; what it is about is worked out again from the result when a page is shown,
for the owner only: the text of each link in the paragraph that says nothing on its own
("click here": `text_pass.unclear_link_text`, the rule that made the notice), a figure or table
by its name in the review ("Figure 1"), a paragraph by its first words. The job page's
"Not handled" and "Worth checking" lines say it ("The link text: “here”.") and link to **See it
on the page**. On the review screen the notices are listed with the optional checks (after the
fixes with notes; the count includes them), each line a link to its place: a figure's or
table's own panel, or the **panel of a notice about text** (`?note=<code>:<element id>`): the
sentence, the link text, the words around it and **Change the words** (the page tools below).
The paragraph is marked on the page image like an optional check (a dashed "Note" frame in
`marks.css`, laid out apart from the fixes' marks and under them, so a paragraph never takes a
click from an equation inside it). A notice that is a decision of its own (a table or a page kept
as an image) stays in the queue, not in the optional checks, and a whole-document notice (the
language) stays on the job page. The list view has the same lines under "Notes on links, text
and figures".

**The page tools** (T3-5, T3-16; `web/page_edits.py`, `routes/edits.py`,
`templates/_page_tools.html`). Under the optional checks, a folded "Change page N" (open after
an edit or an error, and when `tools=1`) with, in this order:

- *Change the words, or make a line a heading.* Every paragraph, list item and heading of the
  page by its first words, each a link that opens it. The box holds the words as they read, an
  inline equation as "[equation 3]" (the review's own number for it): the mark the tool keeps in
  the stored text cannot be typed, so it is named and put back when the text is saved. Every
  equation must stay exactly once, and the tool's own marks ⟦ ⟧ are refused (an equation is
  changed in its own decision). The text is cleaned like alt text (controls, NUL and invisible
  format characters out, whitespace collapsed; program code keeps its lines and indentation),
  at most 6,000 characters, and written escaped like all text. A link, a colour or a raised or
  lowered run stays on the words that did not change (`page_edits.remap_spans`, a character
  diff); one whose words changed is dropped and the notice says how many links were taken off; a
  glossary term stays a term only while its words are unchanged. A heading's words change in its
  fix (`HeadingFix.text`) and its element. *Make it a heading, at level N* (paragraphs only, not
  program code or a glossary entry, one line of at most 300 characters): the levels that keep
  their place in the outline are named (`heading_levels_that_fit`) and another is refused as for
  any heading; the element becomes a heading with a fix the reviewer made (`edited`), so
  "Make plain text" and Undo take it back, and later headings are repaired in the outline as
  always (the notice says how many moved).
- *Add something the tool missed.* Step 1 is a plain GET form: a picture or an equation on its
  own line, and the region as four numbers from 0 to 100 (left and right across the page, top
  and bottom down it). Step 2 shows the region cut out (`pages/{n}/region.png`, the same crop
  the file will carry, at 200 dpi with 2 pt of page round it) with **Add the picture** (alt text,
  checked like any alt text before anything is cut) or **Read it as an equation**. A picture is
  a new figure element in the reading order (placed after the last element above it in its
  column, `reading_order_slot`) with a fix the reviewer made and its crop stored. An equation is
  read by the equation step (`reread.read_equation`: the crop, the text the PDF has under it
  read in the render child, the neighbouring text as context, the course notation in force); the
  answer is **always a required decision**, also when the model was confident, and a read that
  failed waits too. Added elements have ids `p3-x1`; at most 30 per document. Only equations on
  their own line can be marked (an equation inside a line of text would need the paragraph
  rewritten).
- *Leave this page out.* A blank page or a cover moves, with its elements, fixes and notices,
  into `DocumentResult.omitted` (`OmittedPage`): nothing downstream sees them (the queue, the
  counts, the student file), the file says "Page 1 of the original PDF was left out of this
  file." in its note, links to elements on it are taken off at render, and the job page lists the
  notice `page_omitted`. A page that was kept whole as an image comes off `image_only_pages` while it is out (a job whose only such pages were left out is no longer "Finished, with pages not converted"). The last page left cannot be left out. **Put page N back** moves it all
  home; Undo does the same.

Every one of these is a write like a decision: the job row is locked, the stored result changed,
a `ReviewEdit` stored, `jobs.html_stale` set (the HTML is made again when asked for), the review
version counted, and a form from an out-of-date tab refused. Undo puts a structural edit back
exactly: its `ReviewEdit.before` holds a snapshot of the elements, fixes, reading order, omitted
pages and notices it touched (`page_edits.capture`, `restore`), and an undone addition drops its
crop. The list view has a "Change the text, add what was missed, leave a page out" section with
one link to each page's tools.

*What the model costs here.* Marking an equation and reading a table cell are the only
review actions that call a model. Each reserves its worst-case cost first against the day's cap
and against the job's own caps (the whole job, the conversion's spend included;
`reread.ReadBudget`, one conditional UPDATE on the job's row), is limited to 20 a person in 10
minutes (`abuse.RULES["reread"]`) and to one at a time for each person, and is made with no
transaction open (it takes seconds). A refusal (a cap, the model not answering) is a sentence on
the same page with the form kept ("type it yourself"), never an error page; the money of a read
whose write was then refused as stale is spent.

### The list view (`/jobs/{id}/review/list`)

Every fix of the document, grouped by kind, as the review screen was before the split
view, without bulk accept. Its forms send `view=list`, so a decision there comes back to
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
3. **Your decisions** and **Accepted automatically**: everything else, folded, what the
   reviewer decided apart from what was accepted for them (UX review MF14). Each folded
   summary line names its item: "Heading 2 “Displacement Problems”, page 1: Accepted
   automatically", an equation shown small.

A table of contents at the top links to each section and kind group with its counts.
Every fix is numbered within its kind in reading order ("Equation 3"), so a number stays
the same while the list reorders. Tables carry the number their captions print instead
("Table 4", `web.review.item_labels`) when every table has a "Table N" caption of its own,
so a table the detector missed does not make the card titled "Table 3" show the caption
"Table 4"; otherwise all keep their ordinals, so no two names clash.

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

**Pages kept as images.** The pages (or the handwriting on typed pages) kept as images
for one reason are one card in "Figures", like the review screen's: which pages, what
students get, **Keep the page images, marked “Not converted”** and the hint; once kept,
"Kept as images, marked “Not converted”." It posts to the same route with its group key
(`pg-` + the first page image's element id); only "reject" (keep) is accepted. Each page
is named by its page ("Page 2", "Handwriting on page 3"), not counted among the figures:
the list has its own section, "Pages kept as images". After the decision each page's
card under "Your decisions" says what it is now ("Kept as an image, marked “Not
converted”") and has no form: no alt text and no Decorative, which would take the
"Not converted" mark, or the page, from students. `web.review.apply_review` refuses
anything but keep for such a fix (422, "there is nothing else to choose for it"); Undo
the last decision still works.

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
  the "Reject" button named for what it does, as on the review screen, each with an
  accessible name that includes the fix ("Keep without a description (figure 2)").

A decision is one transaction: lock the job row, refuse a form from an out-of-date page
(the stale-tab guard above), apply the decision (`web/review.py`), clean and check edits
the way model output is (LaTeX through `web.edit_check`, the equation step's own checks
against the PDF included; alt text through `validate.clean_alt`, then `check_alt`), store
a `ReviewEdit` (its `after` snapshot also records the review version the write made, so
Undo can find the write's edits), set the fix status (`accepted`, `edited`, `rejected`)
and `Fix.reviewed` in `result_json`, mark the HTML file out of date (`jobs.html_stale`)
and count the write (`jobs.result_version`). The file is made again with
`pipeline.assemble.render_html`, and its static checks re-run (`result_json.html_checks`,
listed on the job page), when somebody asks for it: download, view, the Canvas version or
the job page (`routes.jobs.current_html`, under the job's row lock, so a click that comes
meanwhile waits and marks it again). A run of clicks therefore costs one render, not one
each, and the figure images in it are kept per crop (`pipeline.figure_images`, 15 minutes,
64 MB); measured on a 12-page document with 52 crops, a click went from 210 ms of CPU to
58 ms (`scripts/bench_runtime.py review`). The student note's date is the day of the last
decision, not of the render. A failed check
re-shows the page (422) with the reviewer's input as typed and an error summary linked to
the field; nothing is saved.

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

**No bulk accept** (removed 2026-10-09). The list view used to offer "Accept every
equation proposal at or above X%" and "Accept all N remaining headings". Both took
proposals that need a decision without anyone opening them, and under the review tiers
("meaning-level only", docs/design/beta-architecture.md) every decision asked for could
change what a student reads: each is now opened and decided on its own (repeated
headings are already one decision). Optional notes are accepted already and need no
bulk action. A POST with a kind (`equations`, `figures`, `tables`, `headings`) in place
of the element id is a 404 and changes nothing; Undo still puts back a bulk write made
before the change. The one exception, asked for in T3-11, is **Accept all clean tables**
(`POST /jobs/{id}/tables/accept-clean`, "Rough edges" under the table card): only tables
where nothing at all was found wrong, only when there are two or more, named in the
button, and one Undo takes them all back.

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
  form-action 'self' https://accounts.google.com https://login.microsoftonline.com
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Cache-Control: no-store            (everything except /static/; page images and
                                    marks.css: private, no-store)
                                   (/static/: public, max-age=31536000, immutable for
                                    hashed names and vendor/ files, no-cache otherwise)
Strict-Transport-Security: max-age=31536000; includeSubDomains   (production only)
```

`form-action` names Google's and Microsoft's sign-in hosts because the sign-in buttons
are forms whose answer redirects there, and browsers check `form-action` on that
redirect too.

Request bodies are capped at 1 MiB while they stream in (`RequestSizeLimitMiddleware`),
before any form is parsed: Starlette's multipart parser has no limit on file parts, and
CSRF can only be checked after parsing. The upload route is exempt because it enforces
its own `MAX_UPLOAD_MB` limit the same way.

Sign-in starts and callbacks, uploads and the cron route are rate limited per address (and
uploads per person) in memory (`web/abuse.py`; the numbers are in [security.md](security.md)).
The address is read from `X-Forwarded-For` counting `TRUSTED_PROXY_HOPS` entries from the
right. A person may have 10 documents waiting or running and 40 kept at once.

The converted document (`/view`, `/download`, `/demo`) gets its own policy instead:
`sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'`. It may carry
inline styles and data: images, and the sandbox stops it running scripts, submitting
forms or reading our cookies.

The app pages have no inline scripts, styles or event handlers (a test scans the
templates). htmx is vendored at `web/static/vendor/` (version, hashes and licence in its
README) and configured to need no `eval`, inject no `<style>` and keep no history cache.
Static files are cached for a year under names that change with their content: pages ask
for `/static/app.<12 hex>.css` and `.js` (`web/assets.py`, `static_url`; the hash is the
file's SHA-256, read again only when the file changes), `vendor/` files carry their version
in the name, and anything else is revalidated (`no-cache`, ETag). The math font is sent as
WOFF2 (392 KB, against 733 KB for the OpenType file kept beside it). Our own script, `web/static/app.js`, places error summary links (below), shows times in
the reader's time zone ("Times"), checks a chosen upload before it is sent ("Uploads and
limits"), shows the questions after a download, and adds the review screen's
enhancements (scrolling the page image to the selected mark, opt-in keyboard shortcuts,
drafts kept in the tab; see "Review screen"). The review screen's per-job geometry comes as
a stylesheet from our origin (`marks.css`), not as inline styles.

## Accessibility of the app itself

Server-rendered pages that work without JavaScript: htmx only polls the job status
and, separately, its list of steps (every 3 s, 204 while unchanged), and the documents
list while something converts (every 5 s, focus kept; see "My documents"). The status
box is a polite live region that changes per step, not per element; the counts are in
the list of steps outside it. When the job finishes the status box says so and links to
the first decision and to the results ("Start the review or show the results", "Show
the results", or "Show why it stopped"); the page is not reloaded under the reader, which
would send a screen reader back to the top. Without
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
their colours in forced-colours mode. `test_web_review_ux_browser.py` does the same at
1280 and 320 CSS px for the states the UX review fixes added (the readings marked, the
live check, "Use my edit anyway", the notice with Undo, your decisions, a stale write
refused, the page after the session ended, a draft put back) and checks the keyboard
flow: the first control of the decision within five Tab stops, Undo one Shift+Tab from
the heading that takes focus after a decision.

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
- Not-allowlisted sign-ins, and Microsoft sign-ins from directories that are not set
  up, are not stored. For allowlisted people the sign-in identity (provider, issuer,
  subject id and, for Microsoft, the directory id) is kept with the account and erased
  with it.
- Usage events are content-free and pseudonymous (see "Usage events"); the privacy page
  has one line on them (its final wording is Harish's) and admins see them per day and
  per pseudonym only.

## Database and migrations

Schema: `web/db.py`. Migrations: `migrations/` with `alembic.ini` at the repo root. The
URL comes from `DATABASE_URL` (or the settings, which read `.env`).

Besides jobs and their children, the schema holds a user's courses and their notation
entries (user content), content-free usage events, sign-in identities and the daily
counters (docs/design/content-features.md "Database"). `readablestem users delete` erases a
person through `ops.retention.erase_user`: every job (counted first), then every row of
every table that holds their user id, then the account; a test fails when a table with
a user id is added without it.

- Dev and test create tables directly on start-up. Production refuses to start when the
  schema is missing or behind this release's migrations: run `uv run readablestem db
  upgrade` (same as `uv run alembic upgrade head`) before deploying. A schema at a
  revision this release does not know (a newer release migrated it) is accepted and
  logged as `schema_ahead`, so traffic can roll back to an older revision; see
  deploy.md, "Roll back".
- A local SQLite database made by start-up cannot then be upgraded with Alembic (its
  tables already exist). Delete `.local/readablestem.db` if you need to test migrations.
- `7c1d9a4e2b60` adds `jobs.retention_mode` (server default `day`; old opt-in rows become
  `dev`), `jobs.title_override` and `jobs.contact_line`; the downgrade drops them.
- `3b8e5f1a9d27` (after `7c1d9a4e2b60`) adds `jobs.exam_mode` (server default false); the
  downgrade drops it.
- The jobs work (docs/web.md sections above) adds, in this order: `e2b6d1f84a30` (`jobs.revises_job_id`,
  `jobs.carried_json`), `f7a3c9d20b15` (`step_records`, `jobs.retries`, `jobs.attempts_at_retry`),
  `a8c4e0b61d97` (`pending_uploads`, `jobs.part_group`, `jobs.part_label`) and `b1d5f7a29c46`
  (`jobs.is_sample`; its downgrade deletes the sample documents first). `tests/test_web_jobs_migrations.py`
  runs each one up and down on SQLite and `tests/test_web_postgres.py` on Postgres.
- After them, in this order and as the one chain (one head): `5e2b8d70a3c1` (`linked_emails`,
  `login_links`: the sign-in fallback; made on the same parent as the jobs work and put after
  it when the two were merged) and `d3f8a1c5b920` (`upload_results`: the answer to an upload of
  several files, kept an hour). `tests/test_web_migrations.py` checks the chain, the one head
  and both downgrades.
- New migration: change `web/db.py`, then
  `DATABASE_URL=sqlite:////tmp/empty.db uv run alembic upgrade head` followed by
  `DATABASE_URL=sqlite:////tmp/empty.db uv run alembic revision --autogenerate -m "..."`,
  and review the file. `tests/test_web_migrations.py` and `tests/test_web_postgres.py`
  check that the migrated schema matches the models on SQLite and Postgres.

## Tests

| Command | What runs |
|---|---|
| `uv run pytest tests/test_web_*.py` | Offline web tests (SQLite) plus the Postgres ones when a server is available |
| `uv run pytest -m postgres` | Postgres only: spend and quota under 20 threads, SKIP LOCKED with two workers, migration check, first sign-ins of one identity under 20 threads (`tests/test_web_auth_postgres.py`) |
| `uv run pytest tests/test_web_auth_microsoft.py` | Microsoft sign-in against a fake identity platform (`tests/fixtures/web/oidc.py`: keys made and tokens signed in the test, served to Authlib through an httpx mock transport): every ID-token check, tenants, linking, identities, sessions |
| `uv run pytest -m browser tests/test_web_browser.py` | Chromium: axe-core on every page, CSP, htmx polling, no-JS flow |
| `uv run pytest -m browser tests/test_web_auth_browser.py` | Chromium: the sign-in pages with Microsoft set up (axe, 320 px reflow) and the whole Microsoft round trip under the real CSP |
| `uv run pytest -m browser tests/test_web_review_split_browser.py` | Chromium: the review screen (axe at 1440 and 390 px, reflow, target size, no-JS completion, focus, shortcuts, forced colours) |
| `uv run pytest -m browser tests/test_web_review_figures_browser.py` | Chromium: the figure card with a long description and a data table (axe at 1440 and 390 px, as shown, after a refused edit and with the table added; no sideways scrolling at 320 px, and every number in a six-column table on one line; "Add the data table" then "Remove the data table" without JavaScript) |
| `uv run pytest tests/test_carry_edits.py tests/test_web_carry_edits.py` | Edits carried to a revised file or a document tried again: words, headings, pictures and equations added, pages left out, descriptions; carried only onto an unchanged item (rules on small results, then the whole path with real PDFs and renders) |
| `uv run pytest tests/test_web_edits_record_exports.py` | Every new edit on the review record (who, when, what), every edit route marking the file out of date, and download, view, Canvas, cartridge, download all and the record making it again |
| `uv run pytest tests/test_web_office_upload_together.py` | Word files among several files, a large Word file kept for choosing its pages, Word as a revised version, the time budget of one upload |
| `uv run pytest tests/test_web_record.py` | The review record: the page and the CSV, decisions with who and when, what was not checked, no "certification", owner only, nothing changed by asking, formula-safe cells |
| `uv run pytest tests/test_notation_suggest.py tests/test_web_course_suggestions.py` | Notation suggested from documents: what is found and guessed, the course and job pages, ticking and meanings, ownership, CSRF, skipped duplicates, logs without symbols |
| `uv run pytest tests/test_web_courses.py` | Courses and notation: the flag off, ownership (404), CSRF, every accepted symbol form and the errors, the upload's course, the worker's options, the review card and "remember", logs without course content, erasure |
| `uv run pytest -m browser tests/test_web_courses_browser.py` | Chromium: axe on every courses page and error state, the upload choice, the job page and the remember offer; 320 px reflow with text spacing; the whole flow without JavaScript |
| `uv run pytest -m browser tests/test_web_job_page_browser.py` | Chromium: the job page in each state (axe at 1280 and 320 px, reflow with text spacing, htmx steps outside the live region, draft and language without JavaScript, the one-click download) |
| `uv run pytest -m browser tests/test_web_ux_browser.py` | Chromium: My documents, the posting help, the questions after a download, the deleted-document page and the usage tables (axe at 1280 and 320 px, reflow with text spacing, no-JS), local times, the upload check, the list refreshing with focus kept |
| `uv run pytest tests/test_page_types_pipeline.py` | Hidden text and kept pages through layout, the pipeline and the app (synthetic fixtures, offline model client): handwritten and scanned PDFs refused before any quota, the job page's outcome, one card per group on both review screens, one click keeping every page, a refused action |
| `uv run pytest -m browser tests/test_page_types_browser.py` | Chromium: a course PDF with typed, handwritten and scanned pages (axe at 1440 and 390 px before and after the pages are kept, 320 px reflow with text spacing, the group decision without JavaScript) |
| `uv run pytest -m browser tests/test_web_review_ux_browser.py` | Chromium: the UX review fixes (axe at 1280 and 320 px in each new state, reflow, keyboard flow, the live check, stale tab, Undo and "Use my edit anyway" without JavaScript, drafts kept across a session ending) |
| `uv run pytest tests/test_web_carry.py tests/test_web_retry.py tests/test_web_parts.py tests/test_web_ranges.py tests/test_web_multiupload.py tests/test_web_sample.py` | The jobs work: decisions carried to a revised upload, trying a document again (the real pipeline with a model that stops answering), large files in parts, several files, the course page and the zip, the sample document |
| `uv run pytest -m browser tests/test_web_jobs_browser.py` | Chromium: the new pages in each state (axe at 1280 and 375 px, no sideways scrolling at 320 px, keyboard flows) |

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
covers budget stops, scanned pages and removed users with `pipeline.run.convert_pdf`
(`tests/test_page_types_pipeline.py` does the same for handwritten and scanned pages).
