# Security

The threat model, the controls that answer it, what the pre-launch audit found, and how to
report a problem. The controls are exercised by tests, named below; where a statement here and
a test disagree, the test is right and this page is the bug. The policy for reporters is in
[SECURITY.md](../SECURITY.md).

## What is protected, and from whom

**Assets.** Instructors' course PDFs and what is made from them (copyrighted, and occasionally
carrying a student's name by mistake); the email addresses and names of people with an account; the session
cookie; the model provider's API key and the daily spend it can create; the database.

**Who might attack.**

| Actor | What they can do | Main controls |
|---|---|---|
| Anyone on the internet | Reach public pages, the sign-in routes and the cron route; send anything | Sign-in (the allowlist, or with `SIGNUP_MODE=open` a verified Google or allowed Microsoft identity), CSRF, rate limits, body-size caps, security headers |
| Someone who makes many accounts (open sign-up) | Use the free quota again and again, run up model spend, fill the database | A verified identity, 5 new accounts an hour per address, `MAX_NEW_ACCOUNTS_PER_DAY` for the service, lower daily pages and model reads for a new account, a separate spend pool inside the daily cap, block on `/admin` |
| A signed-in instructor (curious or malicious) | Call every route with other people's ids, send hostile files, send floods | Owner check on every job and course route, 404 for strangers, per-person quotas and queue caps |
| A crafted PDF | Crash or stall the parser, balloon memory, smuggle text that becomes HTML or a prompt | Isolated probe process with time and memory limits, the conversion itself in a killable child with memory, CPU and time limits, page and size limits, escaping and allowlists on everything that reaches HTML |
| The model's output | Contain markup, script, unsafe URLs, wrong content | Validation before use; HTML built by escaping and by rebuilding MathML from an allowlisted tree |
| A person with read access to logs, traces or the aggregate tables | Read document content if it were there | Content-free logging by construction; no document text in events, aggregates or traces |
| A compromised dependency or a leaked key | Run code, spend money | Locked and audited dependencies, pinned CI actions, spend caps, secrets only in Railway's service variables (never in the repository; `.railway/railway.ts` lists names only) |

**Not defended:** a compromised instructor account or machine; a malicious operator; volumetric
denial of service (the host and its edge are the answer, not this app); the model
provider's own handling of what is sent to it (see "Where document data goes").

## Controls

### Sign-in and sessions

- Google and Microsoft (Entra ID) OpenID Connect through Authlib, with state, nonce and, for
  Microsoft, PKCE. Only a verified email is trusted from Google. Microsoft accounts must come
  from a tenant in `MS_ALLOWED_TENANTS`, the ID token is checked against that tenant's keys
  (`web/microsoft.py`), and an email whose domain the directory has not verified is not
  trusted. Tests: `test_web_auth.py`, `test_web_auth_microsoft.py`.
- Another Google account for one instructor (T3-10): an admin links a second address to an
  allowed instructor (`web/linked_emails.py`). Google's verified email is still the only
  thing trusted; the instructor's own `is_allowed` decides on every request, so removing
  them ends the linked address too; a linked address can never also be another person's
  own address; unlinking ends the instructor's sessions. Tests: `test_web_linked_emails.py`.
- Email sign-in link (T3-10), **off** (`EMAIL_LOGIN_ENABLED=false`) and with **no email
  provider wired in**: the settings check refuses turning it on without a registered
  sender, and production refuses any sender not marked for production, of which there is
  none. When on: one answer for every address (no list of who is invited), a 32-byte random
  token of which only the SHA-256 is stored, one use, 15 minutes, a newer link replacing
  older ones, a button page so a mail scanner cannot use the link up, CSRF on the
  button, a limit of three messages per target address per 15 minutes besides the
  sign-in rate limits, nothing about the address or token logged. Tests:
  `test_web_email_login.py`, `test_web_signin_browser.py`.
- Access is `users.is_allowed`, checked on **every request**, so removing or blocking
  someone ends their session at once, and admin demotion likewise (`test_audit_auth.py`).
  With `SIGNUP_MODE=allowlist` (default) only people an admin added have a row; with `open`
  the first verified sign-in makes one (tier `open`), unless the day's accounts are used up,
  and a blocked account is refused and never made again. The tier sets the daily limits and
  the spend pool (`web/access.py`). Tests: `test_web_open_signup.py`, `test_web_open_spend.py`.
- A signed-in person can delete their own account (`/account/delete`, a confirmation, then a
  POST with the CSRF token): the same erasure as the command line, then signed out. Tests:
  `test_web_open_signup.py`.
- The session is a signed cookie: HttpOnly, SameSite=Lax, 7 days, and in production Secure
  with the `__Host-` prefix (browsers then refuse it unless it is Secure with Path=/ and no
  Domain). It is rotated at sign-in (a token seen before sign-in is worthless afterwards).
  Signing out bumps `users.session_version`, which ends every copy of the cookie, not only
  the one in the browser that clicked (`test_audit_headers.py`, `test_audit_csrf.py`,
  `test_web_auth.py`).
- The cookie is signed, not encrypted: its owner can read it. It holds the user id, the
  session version, the CSRF token and one-time notices that never contain document content.
- Dev login exists only when `ENV` is `dev` or `test` **and** `DEV_LOGIN_ENABLED` is set.
  Production refuses to start with it on, and the application context refuses it again when
  the environment is not dev or test (`test_audit_auth.py`).

### CSRF

Every state-changing route is a POST that checks a per-session token in the form body
(`deps.require_csrf`); the upload checks it after streaming the file, the cron route uses a
shared secret instead. `test_audit_csrf.py` posts to every one of them without a token, with
an empty one, with another session's, with the token in the query string or a header, and as
JSON, and checks nothing changed. GET routes change nothing the owner would notice (same
test). Because the route list in `test_audit_authz.py` must match the app's, a new route
cannot skip these checks unnoticed.

### Routes and who may call them

`tests/test_audit_authz.py::ROUTES` is the table; it is compared with the running app.

| Class | Routes |
|---|---|
| Public | `/`, `/demo`, `/style` (the design tokens and components; `noindex`, no data), `/privacy`, `/terms`, `/limits`, `/for-accessibility-offices` and its `/summary` download, `/help/posting`, `/status`, `/healthz`, `/healthz/ops` (names of late scheduled tasks only), `/readyz`, `/login` (GET, POST), `/login/microsoft`, `/login/email` and `/login/email/confirm` (GET, POST; 404 unless `EMAIL_LOGIN_ENABLED`, which is off), `/auth/callback`, `/auth/microsoft/callback`, `/logout`, `/static/*`; `/dev-login` (404 outside dev and test) |
| Signed-in user, owner or a person the owner shared the document with (below) | `/account`, `/account/delete` (GET, POST), `/app`, `/app/documents`, `/app/download`, `POST /jobs`, `POST /sample`, everything under `/jobs/{id}/...`, everything under `/uploads/{id}` (a large file kept for choosing its pages), everything under `/courses/...` (including `/courses/{id}/download`) |
| Admin | `/admin`, `POST /admin/allowlist`, `POST /admin/allowlist/remove`, `POST /admin/users/block`, `POST /admin/users/unblock`, `POST /admin/linked-emails`, `POST /admin/linked-emails/remove` |
| Analytics proxy (only with `ANALYTICS_ENABLED`) | `GET`/`POST /m/{path}`: no sign-in, no cookie, reaches PostHog's two hosts only (fixed in code, `..` refused), rate limited per address (`analytics`, 300 per 10 minutes), the visitor's address is not forwarded (PostHog sees the relay's own), bodies capped by the global 1 MiB limit; `tests/test_analytics.py` checks it. Absent from the route table above while the setting is off |
| Cron token | `POST /internal/cron/aggregate-and-purge` (header `X-Cron-Token`, constant-time compare; production refuses a `CRON_TOKEN` shorter than 32 characters; wrong tokens count against the cron limit below) |

`GET /jobs/{id}/share` and `POST /jobs/{id}/share` and `/share/{share_id}/revoke` (who can open a document, below) are owner-only; the POSTs are CSRF-checked and not rate limited (they send nothing, and a document holds at most 10 shares).

`POST /jobs/{id}/retry` ("Try again") is owner-only and CSRF-checked like every other write on a job, and rate limited; it refuses with 409 when the document is not stopped or has had its 3 tries, and with 429 when the person already has 10 documents waiting or converting.

The job page's three settings, `POST /jobs/{id}/title`, `/contact` and `/keep`, are owner-only
and CSRF-checked like every other write on a job. They are not rate limited: each costs one
re-render of the person's own document (the same as a review decision or a language change,
which are not limited either), the body limit applies, and the number of documents a person
can hold is capped. `/jobs/{id}/download/canvas`, `/jobs/{id}/download/cartridge` (the same page as an IMS Common Cartridge), `/jobs/{id}/download.docx` and `/jobs/{id}/download.epub` are GETs for the owner, like `/download`. The Word file is made by pandoc in a child process (minimal environment, CPU, size and heap limits, 60 s, two at a time) from HTML that went through the EPUB's sanitiser, so it cannot fetch a picture or read a file (docs/output.md, "The Word export"). So is `/jobs/{id}/combine` (one file from the person's own finished, still-kept parts of one source; another person's part in the same group is never included, a part with a decision open is held back, and it restarts the clock of the parts it took and records the downloads, as "Download all" does). `/app/download`, `/courses/{id}/download` and `/jobs/{id}/combine` take `?format=docx` or `?format=epub` (any other value is a 400; the same owner checks, the same sanitised path and the same two-at-a-time pandoc limit as the single downloads; a Word zip holds at most 10 documents). A
download restarts the deletion clock of the person's own document (`retention.restart_clock`),
which is the one thing a GET changes; `test_get_requests_change_nothing` allows that and no
other change.

The review edits (T3-5, T3-11, T3-16) are owner-only and CSRF-checked like the decisions, and
each carries the review version its page was drawn at (a form from an out-of-date tab is
refused, nothing applied): `POST /jobs/{id}/edit/{element}` (the words of a paragraph or a
heading; a paragraph made a heading), `POST /jobs/{id}/pages/{n}/add` (a picture or an equation
the tool missed), `GET /jobs/{id}/pages/{n}/region.png` (the region about to be added),
`POST /jobs/{id}/pages/{n}/omit` and `/restore` (a page left out), `POST
/jobs/{id}/tables/accept-clean` and `POST /jobs/{id}/tables/{element}/read-cell`, and `POST /jobs/{id}/symbols` (one symbol written as another in the
handwritten equations, a single write that one Undo takes back; its redirect is built only from
the checked job id and a fixed view, `routes/review.symbols_url`). Like a
decision, a text edit, an omission or accepting the clean tables is not rate limited (it
changes the person's own document, the body limit applies, the document is capped). Two of them
cost something and are limited: adding a region cuts a crop in the render child (60 in 10
minutes per person, `abuse.RULES["edit_region"]`, plus the render slots every page image
needs), and a model read (an equation marked on a page, a table cell read) is 20 in 10 minutes
per person (`"reread"`), one at a time per person (`reread.ReadGate`), and reserves its
worst-case cost against the day's cap and the job's own caps before anything is sent
(`reread.ReadBudget`, the same conditional UPDATEs as the worker's); a refused reservation is a
sentence on the page, not an error. Added pictures and equations are at most 30 a document.
Text the person types reaches the student file exactly as other review edits do: cleaned
(`validate.clean_alt` or the same without collapsing lines for code; NUL, controls and
invisible format characters out), checked (`check_alt` for alt text and descriptions), kept in
the stored result as data and written through `html.escape` by the assembler;
`tests/test_web_edit_page.py` and `tests/test_web_edit_describe.py` put markup, quotes,
control characters and the tool's own equation marks through every one of these fields.

Every route that takes a job, course or entry id loads it through one access check
(`routes.jobs.job_access` for a job, `notation.owned_course` for a course). A stranger gets the **same** 404 as for an id that does
not exist, so ids cannot be probed. Admins have no access to other people's documents.
`test_audit_authz.py` calls every such route as a second user, as an admin and anonymously,
with real ids, and compares the answer with the one for a made-up id.

### Sharing a document with named people (G1)

An owner can give one document to named people (`web/shares.py`, `routes/sharing.py`,
the `job_shares` table). It adds a way in, so it is described in full here.

- **Who.** Only a person signed in with the exact address the owner entered, and only when
  that sign-in *proved* it: Google's verified email, or the Microsoft directory's own
  verification of the `email` claim (`xms_edov`). Microsoft's `preferred_username` (a sign-in
  name, which `email_claims` still offers the allowlist as a fallback) never matches a share,
  and neither does an admin-linked second address. An emailed sign-in link does count, for the
  address it was sent to: it is random, single-use, expires in minutes, is delivered to the
  address and opened by the person, so using it proves control of that mailbox
  (`login_links.email`, migration `ecb0b22515e0`; the sign-in is verified only when that
  address is the account's own, so a link sent to a linked address does not count, nor does a
  link made before the column existed). Someone whose session proved nothing, and who opens a
  document whose share names their own address, gets a 403 page asking them to sign in again
  with Google or Microsoft (`job_access`); anyone else still gets the plain 404. The session records
  this (`CurrentUser.email_verified`, set at sign-in only when the proved address is the
  account's own), so no migration is needed; a session from before this release has no proof
  and must sign in again to use a share. The address is compared in lower case and whole: no
  domain rule, no `+tag` folding. Signing in is still the service's own gate (`users.is_allowed`,
  or open sign-up), so a share does not let anyone in who could not sign in. Nothing is emailed
  and there are no links that work without signing in.
- **What.** Two rights. `view`: the job page, `/view` preview, the downloads, the crops and the
  review record. `review`: also the review screen and every decision and edit. The owner alone
  can delete, retry, change the title, contact line, language or retention, upload a revision,
  combine parts, use "Download all", add or change or remove a share, or read the course
  notation. A person who is shared a document cannot share it further.
- **One check.** `job_access(session, job_id, user, need)` with `need` `view`, `review` or
  `owner` replaces `owned_job` at every call site. A stranger, a person whose share was removed
  and a made-up id all get the same 404; a shared person asking for more than they have gets a
  403 page. The share is read from the database on every request, so removing it works on the
  next click; the session holds no copy. A document past its retention time is refused to
  everyone before any share is read, and `ops.retention.delete_jobs` deletes its shares (and
  `erase_user` those that name the erased person's address). Erasing a reviewer's account also
  deletes their `ReviewEdit` rows on other people's documents (as for any erased account), so
  the owner's record loses those rows and the reviewer's decisions stay in the stored result.
- **Who did what.** `ReviewEdit.user_id` is the person who made the edit, and the review
  record's "Decided by" column names them. Only the owner sees email addresses: the owner gets
  "Name (address)", everyone else gets names only ("the owner" or "a reviewer" when a person
  has no name). The banner on a shared document and the "Shared with you" list name the owner
  the same way. The sharing page, which lists addresses, is owner-only.
- **Undo.** Undo takes back the last write only if the person asking made it; nobody undoes
  another person's write (the owner does not undo a reviewer's, nor the reverse).
- **Money and limits.** A re-read a reviewer asks for uses the owner's daily allowance, spend
  pool and the document's caps (`reread.read_equation`), and its rate limit is keyed by the
  owner (`enforce(..., "reread", job.user_id)`), so any number of reviewers share one budget
  and sharing never raises what a document can spend or how fast. One read at a time per
  person still applies to each asker. Only the owner's own actions restart the deletion clock
  (`routes.jobs.restart_owner_clock`): not a viewer's download, nor a reviewer's decision.
- **Logs.** `share_set`, `share_revoked` and `share_refused` carry the job id, the right and a
  category; never an address. The flash notices on the sharing page are text without addresses,
  because they live in the session cookie.
- **Tests.** `tests/test_job_shares.py` runs every job route against the owner, a viewer, a
  reviewer, a person whose share was removed and a stranger; and the new routes are in
  `test_audit_authz.py::ROUTES` and the CSRF list of `test_web_auth.py`.

### Headers and the served document

Every response carries (`web/security.py`, `test_audit_headers.py`): a Content-Security-Policy
with `script-src 'self'` and `style-src 'self'` and no `unsafe-inline`; `frame-ancestors
'none'` and `X-Frame-Options: DENY`; `X-Content-Type-Options: nosniff`; `Referrer-Policy:
same-origin`; a Permissions-Policy that turns off camera, microphone, geolocation and payment;
Cross-Origin-Opener-Policy and Cross-Origin-Resource-Policy `same-origin`; `Cache-Control:
no-store` except for `/static/` (public files: a year for names that carry a hash or a version,
an hour with an ETag for the rest, `web/assets.py`) and the demo and showcase pages (an hour with an
ETag, the same for everyone); and, in production, HSTS. That includes error pages, the 413
for an oversized body, the 500 page, redirects, `HEAD`, 404s from the static route and the
sandboxed document.

The converted document (`/view`, `/download`, `/demo`) is served under `sandbox;
default-src 'none'; img-src data:; style-src 'unsafe-inline'`: it cannot run scripts, submit
forms or read the app's cookies even if its markup were hostile. The downloaded file is
opened elsewhere without that header, so it is also built to be inert on its own: no script,
no external resource, no event handler (below). `test_audit_browser.py` loads a document that
does contain a script, an inline handler, a `javascript:` link and a form in Chromium and checks
that none runs, the cookie is unreadable and the form does not submit; it also shows that a
script injected into an app page is refused by the policy.

### Uploads and abuse limits

| Limit | Value | Where |
|---|---|---|
| Request body, any route but the upload | 1 MiB, cut off while streaming | `RequestSizeLimitMiddleware` |
| Upload size | `MAX_UPLOAD_MB` a file converted at once, `MAX_LARGE_UPLOAD_MB` a file kept for choosing its pages and the whole request; checked while streaming, never spooled to disk (a Word or PowerPoint file is written to a private temporary directory by the converter for the seconds it takes: below) | `web/uploads.py` |
| Pages per upload | `MAX_PAGES_PER_JOB` converted at once, `MAX_SOURCE_PAGES` for a file kept for choosing its pages; the quota counts only the pages converted | the probe, before quota or model |
| Page size | 5000 pt a side | probe |
| Uploads in progress | 3 per instance, 1 per person; each is held in memory about three times over, so at most about 900 MB at the largest size (`MAX_LARGE_UPLOAD_MB`) | `UploadGate` |
| Stored bytes per person | `STORAGE_PER_USER_MB` (300, invited) or `STORAGE_OPEN_TIER_MB` (250, open tier): everything they have stored (source PDFs, crops, results, kept large files) that is not past its deletion time, counted in the same transaction as the job with the person's row locked, so two uploads cannot both pass; a new upload that would go over is refused (413) with how much is used and to delete documents. The old worst case was 2.2 GB a person | `web/storage.py`, `web/intake.py`, `web/pending.py` |
| Stored upload bytes, whole service | `STORAGE_GLOBAL_MAX_MB` (2000): over it new uploads get a plain 503 before the body is read, and `/healthz/ops` goes 503 naming `storage`, until the hourly purge has deleted enough | `web/storage.py`, `routes/jobs.py`, `web/health.py` |
| Files in one upload | 10; the request, files together, at most `MAX_LARGE_UPLOAD_MB` (100), so ten files of 10 MB or two of 50; each file after the first counts as an upload for `upload_user`; the probes of one request share 120 s | `web/uploads.py`, `routes/jobs.py` |
| Large files kept for choosing their pages | 2 per person, up to 100 MB and 500 pages, deleted after `RETENTION_HOURS`; their parts are held to the page quota and the caps below | `web/pending.py`, `web/intake.py` |
| Zip of ready documents | 25 documents and 80 MB, in memory | `web/bundle.py` |
| Answers to an upload of several files | one row per upload, names and numbers only, kept 1 hour, deleted with the person; readable by its owner only (404 for anyone else) | `web/upload_result.py`, `ops.retention.purge_upload_results` |
| Word and PowerPoint files of one upload | their conversions share 180 s (`OFFICE_BUDGET_S`); the rest are refused with a reason | `routes/jobs.py` (`_place_files`) |
| Sample document | one per person, outside the quota, the spend and the caps; `sample` rate rule 10 per 10 minutes per address | `web/sample.py` |
| Probe child | 30 s timeout, 768 MiB address space, minimal environment | `inspect_pdf_isolated`, `pdf_probe` |
| Conversion child | The whole conversion, in its own process and process group: `CONVERSION_MEMORY_MB` (3072) plus twice the PDF of address space, CPU time 4 times the job's time limit, no core file, killed at the job's time limit, on cancel or shutdown, dies with the web process; minimal environment (no keys, no database URL), model requests and every database write made by the parent; a crash fails that job at once (`conversion_crashed`, `conversion_oom`) | `convert_isolated`, `conversion_child` |
| Word and PowerPoint (T3-30) | File checks in a child with a minimal environment and a 768 MiB cap (a ZIP of more than 3000 entries, 500 MB unpacked or an entry over 250 MB is refused unopened: with the 100 MB upload limit that is the same room for compression as the 200 MB and 30 MB were; the sizes are read from the ZIP's directory, nothing is unpacked to check); LibreOffice in its own session and process group with 2 GiB address space, CPU, output-size and open-file limits and `OFFICE_TIMEOUT_S` (90 s) then killed; one conversion at a time per instance; the PDF it makes is probed like any upload | `convert_office_isolated`, `office_convert` |
| Daily pages per person | `PER_USER_DAILY_PAGE_QUOTA` (invited), `OPEN_TIER_DAILY_PAGE_QUOTA` (open tier) | `web/quota.py`, `web/access.py` |
| Model reads from review per person per day (open tier) | `OPEN_TIER_DAILY_REREADS`, counted before the read and not given back | `web/quota.py` (`try_consume_reread`), `web/reread.py` |
| New accounts (open sign-up) | `MAX_NEW_ACCOUNTS_PER_DAY` a UTC day for the service, not given back when an account is deleted; 5 an hour per address; an address deleted today comes back with the day's pages, reads and place already used (a keyed hash of the address, kept to the end of the day) | `web/quota.py` (`try_take_signup`), `web/abuse.py` (`signup`), `web/tombstones.py` |
| Spend holds of a dead process | Recorded per hold (`spend_holds`) and given back by job when a lease runs out and by age at start-up | `web/spend.py` (`release_holds`), `web/worker.py` |
| Waiting or running documents per person | 10 | `web/intake.py` (every route that makes a job: the upload and the parts of a large file) |
| Kept documents per person | 40 (the sample document does not count) | `web/intake.py`; "Keep 7 more days" (`routes/job_settings.py`) refuses with a 429 page when it would exceed it |
| Daily and per-job model spend | `DAILY_SPEND_CAP_USD`, per-job caps | `web/spend.py` |
| Daily spend of open-tier accounts | `OPEN_TIER_DAILY_SPEND_CAP_USD`, a pool inside the daily cap, held and settled by the same conditional UPDATE; their queued jobs wait when it is spent | `web/spend.py`, `web/worker.py` |
| Tries again of one document | 3 | `web/retry.py`; "Try again" never touches the page quota, the job's spend caps cover all tries |
| "Try again" requests, per address / per person | 20 / 10 per 10 minutes | `web/abuse.py` |
| Regions added to a document (T3-5), per person | 60 per 10 minutes; at most 30 added per document | `web/abuse.py` (`edit_region`), `web/page_edits.py` |
| Model reads from the review (an equation marked on a page, a table cell), per person | 20 per 10 minutes, one at a time, under the job's and the day's spend caps | `web/abuse.py` (`reread`), `web/reread.py` |
| Sign-in starts, per address | 10 per 5 minutes | `web/abuse.py` |
| Emailed sign-in links, per target address (off) | 3 per 15 minutes, whoever asks | `web/abuse.py` (`email_link`) |
| Sign-in callbacks, per address | 20 per 5 minutes | `web/abuse.py` |
| Uploads, per address / per person | 30 / 15 per 10 minutes | `web/abuse.py` |
| Cron route, per address (every call counts, so wrong tokens are limited too) | 10 per 5 minutes | `web/abuse.py` |
| Analytics relay `/m/` (only with `ANALYTICS_ENABLED`), per address | 300 per 10 minutes | `web/abuse.py` (`analytics`) |

The rate limit is on by default and production refuses to start with `RATE_LIMITS_ENABLED=false`. It is in memory, per instance: with two instances the allowance is doubled, and a
restart forgets it. It is a seatbelt for a few people, not a flood defence. The address is the
entry `TRUSTED_PROXY_HOPS` places from the right of `X-Forwarded-For` (1 with Railway's edge alone, which is production; 2 if another proxy such as Cloudflare's sits in
front as well); entries further left are written by the client and never
believed. **If this is set wrong the limits are shared by everyone or by no one**.
Two checks help catch it. At run time, an `X-Forwarded-For` with more than
`TRUSTED_PROXY_HOPS + 1` entries logs `proxy_hops_unexpected` once per process (the two counts,
never an address): a proxy is missing from the setting, or a client is forging the header. And
`readablestem ops launch-check` lists `TRUSTED_PROXY_HOPS=0` in production, which makes every
visitor share the platform's address. Launch-check runs from the repo and the settings, so it
cannot see the live header; the log line is the only live check.

### Running uploaded TeX (sandbox)

A `.tex` file is a program, so a LaTeX upload (a later change; the switch is `FEATURE_LATEX_SOURCE`)
runs LaTeXML only inside `core/sandbox.py`: bubblewrap with a minimal read-only root (no
application, no `/home`, `/proc/1/environ` hidden), the project mounted read-only and one output
folder writable, a new network namespace (no network), a new pid namespace that dies with the
caller, an empty environment, a seccomp filter (`core/seccomp.py`: `ptrace`, `mount` and the new
mount API, `unshare`, `setns`, `clone` with namespace flags, `keyctl`, `bpf`, `perf_event_open`,
`userfaultfd`, `io_uring_*`, `kexec_*`, module loading, `process_vm_*` answer EPERM; `clone3` answers
ENOSYS; x86-64 and arm64 only) and `prlimit` caps. **What is capped, exactly:** address space
(512 MB for LaTeXML), CPU time (120 s), the size of any one file (50 MB) and open files (256) are
limits **per process**. The sandbox joins its cgroup before it starts bubblewrap, so when the
container delegates a cgroup v2 (the probe says `sandbox_cgroup=ok`) the memory limit (512 MB) and
the pids limit hold for the whole tree. Without one, the total is bounded by arithmetic: at most 4
tasks (LaTeXML needs 3, measured: the sandbox's init, `prlimit` becoming perl, perl; counted inside
the sandbox's own user namespace on kernels from 5.14, else as this user's tasks now plus 4) times
512 MB of address space each (a 29-page equation-heavy document needs between 256 and 384 MB), and
one source conversion runs at a time on the machine (`sandbox.exclusive`, a file lock), so the
bound is 4 x 512 MB = 2 GB for the whole server. `ops sandbox-probe` prints it as
`sandbox_total_bound_mb` (the cgroup's limit, else tasks x address space) and, with
`--require-latexml`, fails when it is over `SOURCE_SANDBOX_MAX_TOTAL_MB` (2048). **Residual:** address
space is more than memory in use, so the bound is loose, and it can only be approached by code that
runs inside LaTeXML's Perl first (a LaTeXML bug); the container's memory limit and the host's OOM
killer are what is left behind it. The caller kills the group at a wall-clock limit
(120 s) or when the output folders and the program's log pass 200 MB. If bubblewrap or user namespaces are not available the run is
refused; there is no unsandboxed fallback (a plain child of the web process shares its user, so
it could read the web process's files). Whether the platform allows it is checked on the deployed
image by `readablestem ops sandbox-probe` (docs/operations.md, "Sandbox probe"); a FAIL there means
LaTeX upload must stay off.

### Rotating the session secret

`SESSION_SECRET` signs the session cookie. To change it without signing everyone out:

1. Generate a new value (`python -c 'import secrets; print(secrets.token_urlsafe(48))'`).
2. Set `SESSION_SECRET_PREVIOUS` to the old value (several, comma-separated, if rotating
   again before step 4), set `SESSION_SECRET` to the new one, and redeploy. New cookies are
   signed with the new secret; a cookie signed with a listed old one is still accepted, and is
   re-signed the next time the session changes. Production refuses an entry shorter than 32
   characters.
3. Wait 7 days (the cookie's lifetime), after which no cookie signed with the old secret is
   valid anyway.
4. Remove `SESSION_SECRET_PREVIOUS` and redeploy.

Leaked secret: skip the wait. Rotate with `SESSION_SECRET_PREVIOUS` unset, so every session
ends. The secret is also the key of two HMACs that do not accept an old key: the upload
fingerprints in usage events (`web/events.py`, after rotation a repeat upload no longer counts
as the same file again) and the deleted-account tombstones (`web/tombstones.py`, a person
deleted before the rotation who signs up again the same day is no longer matched to the
earlier use). Both are only statistics and quota carry-over for that day.

`CRON_TOKEN` and the provider keys rotate by changing the variable on the service (and on the
scheduler that calls the route, if any); the token needs at least 32 characters in production.

A Word or PowerPoint file (T3-30) is handled the same way, with more care because
LibreOffice is far larger than MuPDF and, unlike it, reads files and not pipes. In a child
with no secrets in its environment (`web/office_convert.py`, `test_office_convert.py`,
`test_web_office.py`): the ZIP is checked before LibreOffice sees it (entry count, unpacked
size, encrypted entries, exactly one Word document or presentation, no macros, no DOCTYPE
or ENTITY declaration or UTF-16 in any XML part, and no relationship that points outside
the file except an ordinary hyperlink, because **LibreOffice fetches a linked picture's
address when it converts**: tested with a listening socket, and refused before it can); then
LibreOffice runs headless with macros off, links never updated and an unreachable proxy for
every protocol (a second line of defence: with the check skipped, the socket still sees no
connection), a private profile in a private directory that is removed afterwards, resource
limits, and its process group killed at the timeout. The upload exists on disk, in a
directory only the child can read, for the seconds the conversion takes (LibreOffice cannot
read from a pipe); the container's file system is not kept across restarts. Hostile files end in a category,
never an exception or a hang; byte-mutated files never escape as another exception. The PDF
that comes out is probed like any upload, and only it is stored.

The PDF is opened first in a child process (`python -m readablestem.web.pdf_probe`) that has no
secrets in its environment, caps its own memory and is killed at its timeout; MuPDF is C code
reading bytes from the internet and is never run in the web process on first contact. The
conversion that follows is run the same way (`web/isolated_convert.py`, `web/conversion_child.py`): a
file that passes the probe and still crashes, exhausts memory or hangs MuPDF in a later step
fails that one job and nothing else, where it used to take the whole web process (every session,
the worker, and again on each retry) down. The child has no API key, no database URL and no
token; it asks the parent for model requests and progress over a socket, and what it sends is
JSON and bytes that the parent validates and never unpickles, so a compromised child can
only do what a conversion of that document could: ask for requests under the job's spend caps
(only models the deployment is configured with, only the steps' own schemas, a bounded number of requests, bounded frames), and return a result. The parent kills it at the job's
time limit, when the job is deleted or taken over, and at shutdown. The exposure that remains is
the model request itself: a compromised child could choose its text and images.
`test_audit_uploads.py` feeds it a decompression bomb, a page tree that points at itself, a
page count that lies, a page a million points wide, a stream length past the end of the file,
truncated and garbage files, and files that are not PDFs; each ends in a category, never an
exception or a hang. File names are reduced to a plain base name (control and bidi
characters dropped, 200 characters), are shown only to their owner, escaped, and never reach
a header or a log.

### Output escaping

Jinja autoescape is on for every template. Everything a model or a PDF controls and that
reaches the converted file passes through `html.escape` or is rebuilt from a parsed tree:
MathML is parsed with defusedxml (no DTDs or entities), checked against an element allowlist
and re-serialised, never pasted; link targets from a PDF must be http, https or mailto
(`core.types.Link`); language tags must match a BCP 47 pattern; LaTeX is checked against a
command allowlist before conversion. `test_audit_escaping.py` fills **every** free-text field
(title, headings, paragraphs, list markers, captions, alt text, long descriptions, table cells
and captions, equation labels, speech, LaTeX, MathML, notices, the source note, the language)
with markup that would run script or break out of an attribute, renders the document, and
parses the result: no script-capable element, no event handler, no URL that is not an ordinary
link, images only as `data:image/png`. It also feeds 12 hostile MathML strings (entity
bombs, an external entity, `mglyph`, `annotation-xml`, `maction`, XLink, style URLs) to the
sanitizer and to the renderer, and shows the review screen hostile alt text and LaTeX.

PDF text reaches a model only inside a fenced context field. The model has no tools and no
way to act; its output is validated as above. A prompt injection in a PDF can at worst make
a wrong alt text or equation, which the instructor reviews.

### Privacy, retention and erasure

- Logs carry event names, ids, counts and category codes only. A key denylist drops anything
  that names content, exception messages are never logged (type and code location only),
  uvicorn's access log is off, and third-party libraries cannot write below WARNING; the
  Anthropic and OpenAI SDKs' `*_LOG=debug`, which would print request bodies, is switched off
  at start. Events, error aggregates and quota rows have no free-text column.
  `test_audit_privacy.py` runs a canary document (a marker in the PDF text, file name, LaTeX,
  alt text, caption, heading and an error message) through upload, conversion, review,
  download, failure and deletion and finds the markers in no log record, stdout line, cookie,
  response header, event, aggregate or quota row. `test_web_ops.py` does the same for logs
  alone.
- Retention: 24 hours by default, 30 days with the opt-in checkbox. A document past its time
  answers 404 immediately, and the hourly purge (cron, with the worker as a backstop) deletes
  the job, its blobs and review edits. `test_audit_privacy.py` moves a clock through the
  upload route, the job page and the cron endpoint to check 23 h 59 min, 24 h 1 min, 29 days
  and 30 days 1 min. "Delete now" removes the same rows at once.
- The same clock covers what is made from a document: a large file kept while its pages are
  chosen (`pending_uploads`, `RETENTION_HOURS` after it was made), the answer page of a
  multi-file upload (`upload_results`, an hour after it was made) and the sample document. Usage
  events are deleted after 400 days or with the person. The keyed hash kept for the rest of
  the day after an account is deleted (so the day's limits are not reset) is deleted the next
  day. Accounts, the daily spend and quota counters and the content-free aggregates are kept
  until a person deletes the account (counters and aggregates hold no content or link to
  them). Details: docs/operations.md, "Retention".
- Erasure of a person (`readablestem users delete EMAIL --yes`, or the person's own "Delete my
  account", `ops.retention.erase_user`)
  removes their jobs, blobs, review edits, courses and notation, events, sign-in identities,
  quota rows (with the person's daily model spend) and the user. Only content-free aggregates and
  global daily spend remain, and one ledger row (`erasure_ledger`: a SHA-256 of the random user id
  and the time) so that restoring a backup can erase the account again (`readablestem ops
  replay-erasures`, docs/operations.md "Backups and restore"). The ledger cannot be turned back into
  a person; its limit is the window after the newest dump if the whole database is lost. A test
  fails when a table with a user id is added without being listed there.
- **What deletion does not reach:** Postgres keeps dead rows until vacuum; the daily database
  backups hold every document that existed when they ran (the `backup` service on Railway keeps
  the last 6 dumps, no point-in-time recovery, so up to about a week after a deletion); Railway's
  own request logs hold URLs (job ids, and for one moment an OAuth `code`) and client
  addresses, for a time that depends on the plan; and the model provider's copy (below).

### Where document data goes

| Recipient | What | How long |
|---|---|---|
| Railway (the web service, Railway Postgres and the `backup` volume) | The PDF, results and HTML while retained; sessions; logs without content | Until the retention time; backups up to about a week more |
| The model provider (`MODEL_PROVIDER`) | Images cut from the page (equations, figures, and tables when enabled) with a short fenced piece of nearby text; a course's notation when that feature is on. No file name, no email, never the whole PDF | OpenAI: requests are sent with `store=False`. Anthropic: per its published policy, deleted from its systems within 30 days (longer for material its safety systems flag); zero retention only by agreement |
| A handwriting reader (`MATH_READER_CLAUDE_ENABLED`, `MATH_READER_OPENAI_ENABLED`; off; the pipeline calls one only when `IMAGE_PAGES_SECOND_READER` names it, and in the hosted conversion child only the local reader can run) | One tight crop of an expression, no text | Anthropic: deleted within 30 days (longer if flagged). OpenAI: sent with `store=False`. Turning one on can add a recipient to this table and to the privacy page (`docs/math-readers.md`). The local reader (`MATH_READER_LOCAL_ENABLED`) runs in the conversion child, sends nothing and adds no recipient |
| Langfuse (only if tracing is on) | Timings, model names, token counts, costs, error categories, random ids; payloads are masked | Set in the Langfuse project |

Two review actions send the model provider the same kind of material after the conversion has
finished (T3-5, T3-11): marking a region of a page as an equation sends its image with the
short fenced piece of nearby text, and "Read this cell with the model" sends the cell's image.
Nothing else leaves the server, they are the same "what is sent" as the table above, and they
are under the same spend caps (docs/web.md, "What the model costs here").

The privacy page's wording for the model provider is the beta wording.
What is known as of the audit is above; re-read the provider's policy page on the day of
launch. No zero-data-retention agreement is requested for the beta. A document deleted here
can remain at the provider for up to 30 days.

## Pre-launch audit: findings

Audit date 2026-10-14, branch `feat/launch-audit`, from `feat/public-proof`. Findings 1 to 6
have regression tests; 7 to 9 are wording.

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Medium | No rate limit anywhere: sign-in starts and callbacks, uploads (each non-PDF still costs a probe process) and the cron token were unlimited | Per-address and per-person limits (`web/abuse.py`), 429 page with `Retry-After`; `test_audit_ratelimit.py` |
| 2 | Medium | One person could queue or keep any number of documents (bounded only by the daily page count, which one-page files stretch) | Caps of 10 waiting or running and 40 kept; `test_audit_uploads.py` |
| 3 | Low | The three child processes that read untrusted PDFs inherited the server's whole environment (session secret, API keys, database URL) | `core/childenv.py`: a minimal environment; `test_audit_uploads.py` |
| 4 | Low | Setting `ANTHROPIC_LOG=debug` or `OPENAI_LOG=debug` makes the SDKs log whole request bodies (document text and images); the Anthropic logger was not clamped | Variables removed and the loggers clamped at start; `test_audit_privacy.py` |
| 5 | Low | The session cookie had no `__Host-` prefix in production | Prefix in production; `test_audit_headers.py` |
| 6 | Low | No `Cross-Origin-Resource-Policy` | Added; `test_audit_headers.py` |
| 7 | Low | The privacy page did not say that a document deleted here can remain with Anthropic for up to 30 days | Stated; the wording is final for the beta |
| 8 | Info | The privacy page had no "how it is protected", "where it is processed" or "changes" section, and the terms had no accounts, institution or changes section | Sections added; the defaults are now the wording |
| 9 | Info | SECURITY.md names a different email address (outlook) from the project metadata (gmail) | Left for Harish to confirm |

Checked and found sound (tests added as regression guards, no code change): headers on
every response kind; CSRF on every state-changing route; owner checks and IDOR on all 29
routes that take an id; admin routes; session fixation and cross-copy sign-out; dev login in
production; Microsoft tenant and Google allowlist behaviour; size, page and probe limits
including a 1 GiB decompression bomb; hostile PDFs; file name handling; escaping of every
model and PDF field; no document content in logs, events, aggregates, cookies or headers;
the retention clock and erasure; no 500 for junk in any parameter of any route.

## Scanners

| Tool | Result |
|---|---|
| `uv audit` (lockfile, 89 packages) | No known vulnerabilities, no adverse project statuses |
| `pip-audit` on the exported lockfile | No known vulnerabilities |
| `bandit -r src` | 0 high, 9 medium, 55 low (triage below) |
| `ruff check --select S src` | 43 findings, the same set as bandit plus asserts (triage below) |
| `semgrep` (`p/python`, `p/secrets`, `p/flask`, `p/owasp-top-ten`; 265 rules, 224 files, including the Dockerfile and deploy script) | 2 findings, both false positives |
| Secrets in history | A regex scan of every added line in 695 commits (keys, tokens, private keys, connection strings with passwords, Google client secrets) found only the placeholders that tests use to prove secrets are not printed |

Triage of what the scanners reported:

| Rule | Where | Verdict |
|---|---|---|
| B704 / S704 `Markup` (4) | `web/mathml.py`, `routes/review.py`, `web/table_card.py` (2) | False positives: each wraps the output of the allowlisting serialisers (`sanitize_mathml`, `render_table`, `text_html`), not raw input; covered by `test_audit_escaping.py` |
| B314 / S314 `xml.etree` parse (3) | `eval/equations.py`, `gold/openstax.py` | Real pattern, not reachable from the server: developer tools parsing the project's own gold data. Every parse on the server path uses defusedxml with DTDs forbidden |
| B405 `xml.etree` import (13) | `pipeline/*`, `web/*` | False positives: the import builds elements or provides types; parsing goes through defusedxml |
| B310 / S310 `urlopen` (2) | `eval/render.py`, `gold/openstax.py` | Developer tools fetching fixed https sources; not part of the server |
| B603, B607, B404, S603, S607 `subprocess` (10) | `uploads.py`, `page_images.py`, `layout.py`: fixed argv, `sys.executable`, no shell, the PDF over stdin, minimal environment. `proof/*`, `eval/reproduce.py`: developer tools | False positives for the server; developer tools run only on the developer's machine |
| B105 / S105 hardcoded password (13) | `pipeline/vendor/rapid_table.py`, `core/notation.py`, `web/compare.py`, others | False positives: variables named `token` that hold text tokens, and the OAuth `code_challenge_method` constant |
| B311 / S311 `random` (6) | evaluation sampling, retry back-off in `models/openai_client.py` | False positives: no token, id or secret is made with `random` (ids use `uuid4`, tokens use `secrets`) |
| B101 / S101 `assert` (5) | exception-handler type narrowing | False positives: not used for authorisation or validation |
| semgrep `plaintext-http-link` (2) | `static/demo/index.html` | False positive: the Creative Commons licence link inside the OpenStax attribution the licence requires; a link, not a resource |

## Residual risks, accepted for the beta

- Word and PowerPoint input puts LibreOffice, a large C++ program, in front of files from
  the internet. The checks, the limits and the unreachable proxy reduce that and do not remove
  it: a bug in LibreOffice's own parsers is contained by the child's environment, limits and
  timeout, not prevented. Turning `OFFICE_INPUT_ENABLED` off, or not installing LibreOffice
  (the production image does not unless built with it), removes the exposure.
- The emailed sign-in link (when it is switched on) is in a URL: Railway's request logs and
  the recipient's mail provider hold it until it expires or is used, and anyone who reads the
  mailbox can sign in.
- Rate limits and the upload gate are per instance and in memory.
- `/healthz/ops` is public and has no rate limit: it reads two rows and a sum of stored sizes, and answers with at most the
  words `backup`, `cron`, `storage` or `database`, and the stored and allowed megabytes (totals, no names). Scheduled-task failures are found by a GitHub Actions job
  that stops after 60 days without a commit to the public repository, unless re-enabled.
- The session cookie is readable by its owner (signed, not encrypted).
- A copy of the converted HTML opened from disk has no CSP; it is built to need none.
- Ingress is the public internet; nothing here is behind an identity-aware proxy.
- Deleted documents live on in the database's dead rows and the daily backup for up to about a day,
  and at the model provider for up to 30 days.
- Prompt injection in a PDF can degrade a conversion (not leak or act); review catches it.

## How to report a problem

Follow [SECURITY.md](../SECURITY.md): GitHub private vulnerability reporting first, otherwise
email the address given there with "ReadableSTEM security" in the subject. Test against a local
checkout, use a synthetic PDF, and do not touch other people's documents. If you find a
document of yours somewhere it should not be, say which page it was on and when; the
maintainer can then check the logs, which hold ids and times but never content.
