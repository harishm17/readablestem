# Launch blockers

Everything that is still open before real people use the service, from the pre-launch audit
(2026-10-14, [security.md](security.md)) and from every `TODO(Harish)` in the repository.
Each line has a class and a recommended default, so a decision can be "accept the default"
rather than "think about it".

**Classes**

- **blocking-beta**: settle before the 3 to 5 instructors get their first sign-in.
- **blocking-public**: settle before the repository is made public or sign-up is opened beyond
  the pilot.
- **later**: improves things; nothing breaks or misleads without it.

`uv run a11y-stem ops launch-check` lists the unfinished copy in the public pages (it reads the
templates for `TODO(Harish)` and the draft note) and exits 1 while any remains; it is a
reminder, not a refusal, so a staging deploy still starts. Items marked "(launch-check)" below
clear it when done.

## Blocking the beta

| ID | Where | Decision | Recommended default |
|---|---|---|---|
| B1 | `web/templates/privacy.html` (Anthropic); `docs/design/beta-architecture.md` | Final wording for the model provider. | Say what is true and checkable: images and nearby text go to the provider; the provider's policy is deletion within 30 days (longer for flagged material), so a deleted document can remain there that long; no training on API data by default. Do not ask for zero data retention for the beta. Re-read the provider's policy page the day you launch. Change "Uploads are never used to train AI models, sold or shared" to "We do not use your uploads to train models, sell them or share them beyond the services named here." (launch-check) |
| B2 | `privacy.html` (backups); `docs/deploy.md` | How long a deleted document can sit in a database backup. | Keep `SQL_RETAINED_BACKUPS=1` and the sentence as written ("up to about one more day"). It is true of the deploy today. (launch-check) |
| B3 | `privacy.html` (usage events) | Final wording of the usage-events paragraph. | Keep it. It is accurate: random ids, a time, a category and a number, deleted after `EVENT_RETENTION_DAYS`; shorten that to 180 days if a university asks. (launch-check) |
| B4 | `privacy.html` ("Where it is processed", new) | Name the region and the model provider's country. | Use the sentence in the template comment with the region from `deploy.env`. A university privacy office will ask where the data lives. (launch-check) |
| B5 | `terms.html` ("Your institution", new) | Confirm with the pilot universities. | Keep as written; if a university needs its own vendor or data-protection terms, record the agreement here and link it. (launch-check) |
| B6 | `limits.html` and `docs/output.md` | The known-limits page is yours to write (architecture rule 8). | Publish the list in `docs/output.md` ("what the file does not handle"): tables, links, coloured text, language, maths in captions, handwritten and scanned files refused. Drafted text is accurate; edit for voice. (launch-check) |
| B7 | `pipeline/assemble.py` ("About this version" student note); `docs/output.md` | Wording students read in every file. | Keep the drafted note; do not name screen readers; keep "checked" only where an instructor has made a decision. |
| B8 | `help_posting.html`, `_job_ready.html`, `docs/output.md`, `docs/web.md` | Canvas and Blackboard steps are untested. | Ship the generic steps ("upload the file and link it; never paste it into a page"), say the Canvas and Blackboard click-paths are not yet tested, and try Canvas in a real course with the pilot instructor in week 1. |
| B9 | `docs/deploy.md`, `deploy/deploy.env.example` | Confirm `BUDGET_USD` against the pricing calculator. | Keep 90 with the 50, 90 and 100 percent alerts, and the model provider's own hard monthly limit set below your tolerance. |
| B10 | `docs/deploy.md` (admin consent) | Ask each pilot university's IT team to approve the Microsoft app. | Start now (it takes weeks). If one is slow, that instructor signs in with Google. Use the request text in `docs/deploy.md`. |
| B11 | `docs/deploy.md` ("Before launch" list) | Run the pre-launch list in order; none of it has run. | Do it in a staging project first. |
| B12 | `docs/decisions/0003-pdf-library-agpl.md`; `SOURCE_URL` | PyMuPDF is AGPL-3.0, so people using the service over a network are owed the source, **including in the beta**. | Make the repository public before the first beta sign-in, after the "Before the repository is public" list below. If that is not possible, give the pilot instructors a written offer of the source before they sign in, and point `SOURCE_URL` at the deployed commit. |
| B13 | Deploy configuration | `TRUSTED_PROXY_HOPS` must match the front end: 1 on Cloud Run's own address or a domain mapping, 2 behind an external HTTPS load balancer. | Default 1 is right for the documented deploy. Wrong value means the sign-in and upload limits are shared by everyone (too many hops) or can be bypassed with a forged header (too few). Owner: the deploy change. |
| B14 | Deploy configuration | `ENV` defaults to `dev`; a deploy that forgets `ENV=prod` runs without the production start checks, Secure cookies and HSTS. | The deploy script sets `ENV=prod`; check the running service's env once after deploy. Owner: the production-defaults change. |

## Sign-in fallback, Word input and Canvas (T3-8, T3-10, T3-30)

Built in `feat/t3-integrations`. Nothing in this table blocks the beta unless you switch the
feature on; each row says what the feature does while it is off.

| ID | Where | Decision | Recommended default |
|---|---|---|---|
| S1 | `EMAIL_LOGIN_ENABLED` (off), `web/email_senders.py` | Whether to offer an emailed sign-in link if a university's Microsoft approval is slow, and which provider sends it. **No provider is wired in**: sending mail is a new data processor (it sees each person's address and the time of each sign-in), and production refuses the setting until a sender marked for production is registered. | Do not turn it on for the beta. The linked Google account (S3) covers the case with no new processor. If you do want it: pick one transactional provider with a data-processing agreement, write a class with `send(message)` (about 20 lines), register it with `register_sender(name, factory, production_ok=True)`, set `EMAIL_SENDER`, set up SPF, DKIM and a sending address on your domain, and do S2. |
| S2 | `privacy.html`, `docs/security.md` | Privacy wording for the email provider, if S1 is done: it receives the address and the time. Also that the link is in a URL that Cloud Run's request logs hold for their retention. | Add one sentence to "Who else sees your documents" (it is not a document) and to the Cloud Run logs line (P5): "If you sign in by emailed link, our email provider sees your address and when we sent it." Name the provider. |
| S3 | `/admin` ("Let an instructor sign in with another Google account"), `a11y-stem users link` | Whether admins may link a second Google address to an instructor. Built and on: it adds no processor and no setting. | Keep it. Use it for the instructor whose invitation went to a university address but who can only use Google under a personal one. Remove the link when they no longer need it (it signs them out). |
| W1 | `Dockerfile` (`INSTALL_LIBREOFFICE`), `docs/deploy.md` "Word and PowerPoint uploads" | Whether the deployed image takes `.docx` and `.pptx`. It does not unless built with LibreOffice (a few hundred MB more image and `MEMORY=2Gi`). The development box has Writer but not Impress, so PowerPoint conversion has not been run end to end. | Build with it for the beta if an instructor will upload Word or slides: those are the best sources the tool can get (the author's headings and picture descriptions come with them). Install Impress on a staging box and run `a11y-stem convert slides.pptx` once before switching on. |
| W2 | `privacy.html` (new line when the form takes these files) | Final wording of how a Word or PowerPoint file is handled: checked in a separate process, written to a private temporary folder for a few seconds, turned into a PDF on the server itself (nothing is sent to another company for this), the original not kept. | Keep it. It is what the code does and the tests check. (launch-check) |
| W3 | `config.py` (`OFFICE_INPUT_ENABLED`, `OFFICE_TIMEOUT_S`) | Whether to accept the extra attack surface (LibreOffice parsing files from the internet) at all in the beta. The checks, limits and sandbox are in `docs/security.md`; a bug inside LibreOffice is contained, not prevented. | Accept for 3 to 5 invited instructors; set `OFFICE_INPUT_ENABLED=false` before opening sign-up wider, unless a scheduled LibreOffice update is part of the deploy. |
| C1 | `docs/canvas-sandbox-test.md` | Run the manual Canvas test (about 45 minutes) and send back the table. | Do it in week 1 with the pilot instructor (B8). It decides whether the README can promise that an `.html` file in Files shows its pictures, whether Canvas draws the equations with its own MathJax (making the font stack moot there), and how a Common Cartridge arrives. |
| C2 | Job page ("Download as a Common Cartridge") and `help_posting.html` | The cartridge link says "Not yet tried in a live course". Keep the link visible before C1? | Keep it visible with that sentence: it costs nothing and an instructor with Blackboard or Moodle can try it. Remove the sentence after C1. |

## Blocking the public repository or open sign-up

| ID | Where | Decision | Recommended default |
|---|---|---|---|
| P1 | `SECURITY.md` and `pyproject.toml` | The security contact is `harish_manoharan@outlook.com` in one and `harishmanoharan17@gmail.com` in the other. | Use one address you read daily, a dedicated alias if you have one; make both files agree. Chosen: `SECURITY.md` now says `harishmanoharan17@gmail.com`; the `pyproject.toml` authors stay as they are. |
| P2 | GitHub settings | Private vulnerability reporting, secret scanning and push protection, branch protection on `main`. | Turn all on the day the repository is public. |
| P3 | `privacy.html` ("Changes to this notice", new) | How people are told of changes. | Use the sentence in the template comment. (launch-check) |
| P4 | `terms.html` (governing law, new) | Jurisdiction. | Say nothing beyond "governed by the law of {state}" for a free beta; ask a lawyer or a university counsel before adding a venue or a liability cap. (launch-check) |
| P5 | `privacy.html` (platform logs) | Cloud Run's own logs hold URLs and client addresses outside the app's control, kept 30 days by default. | Add one sentence: "Our host keeps standard request logs (time, address, page) for up to 30 days." and set the log bucket retention to match. |
| P6 | `landing.html` (ADA rule date) | Do not state a legal deadline you have not confirmed. | Leave the date out; say "the Department of Justice's ADA Title II web rule" until checked against 28 CFR part 35, subpart H and your university's page. (launch-check) |
| P7 | `landing.html` (review-load numbers) | Whether the product shows "3 decisions" style numbers. | Do not show numbers; link to how they are measured (`docs/eval.md`). (launch-check) |
| P8 | `docs/web.md` (identity conflict) | Whether a second identity for an existing user's address is refused. | Beta: keep linking by verified email (3 to 5 known people). Before adding tenants beyond the pilot, refuse it (`login_refused reason=identity_conflict`) and add the admin command to unlink. |
| P9 | `docs/eval.md` | Promote the reviewed `latest-<task>.json` runs as baselines so CI's eval gate stops skipping. | Promote after you have read the numbers once. |
| P10 | `docs/eval.md`, `/demo` | Build the demo page with the real model (`a11y-stem demo build`, about 7 cents) and keep `2-2-speed-and-velocity`. | Do it; launch-check flags a missing or offline-built demo. (launch-check) |
| P11 | `docs/eval.md` | Own course PDFs for the real-document gate. | Add 3 to 5 with the owners' permission; never commit them (`private-inputs/` is ignored). |

### Before the repository is public

1. Run the secret scan again over the full history (the audit found only test placeholders).
2. Read `git log` for names and emails you do not want public.
3. Make sure `private-inputs/` and `.env` are untracked (they are in `.gitignore`).
4. Set `SOURCE_URL` to the public repository.
5. Turn on the GitHub settings in P2.

## Later

| ID | Where | Decision | Recommended default |
|---|---|---|---|
| L1 | `web/static/app.js` | Say on the privacy page that unsaved review text is kept in the browser tab. | Add: "While you type a review decision it is kept in your browser tab until you save, close the tab or sign out." |
| L2 | `web/templates/app.html` | Replace the one "keep my document 30 days" checkbox with two choices. | Keep one checkbox for the beta; revisit after feedback. |
| L3 | `ops/export.py` | The opted-in export copies documents to a local disk. | Do not use it until the privacy page says opted-in results may be copied to the maintainer's machine and deleted by the same date. |
| L4 | `privacy.html`, `docs/operations.md` (Langfuse) | Retention at Langfuse. | Keep `TRACING_ENABLED=false` for the beta. Before turning it on, set the project retention to 30 days and say so on the privacy page. |
| L5 | `messages.py`, `outcome.py`, `routes/jobs.py`, `routes/review.py`, `pipeline/assemble.py`, `docs/output.md` | Final wording of the handwritten and scanned messages. | Ship the drafts; they are accurate. |
| L6 | `docs/steps.md` | Required review decision or optional note for a letter a course writes in bold only. | Keep it required. |
| L7 | `docs/operations.md` | Email on decision-canary drift. | Add the log line to the cron-problems metric. |
| L8 | `docs/eval.md`, `eval/gate.py`, `eval/sample.py` | Table floors, opt-in sampling, scheduling the paid live eval, prose-maths gold, rebuilt demo with flags on. | Do these after the first month of beta data. |
| L9 | Identity providers | Admin accounts: require multi-factor sign-in at the identity provider. | Enforce MFA for the admin Google and Microsoft accounts. |
| L10 | CI | Run `uv audit` and `bandit` on a schedule. | Add a weekly job next to Dependabot; the audit found none today. |
| L11 | Deploy | Rate limits and the upload gate are per instance. | Fine at 1 to 2 instances; use Cloud Armor or a shared store if sign-up opens. |
| L12 | `pipeline/assemble.py` | A `<meta http-equiv="Content-Security-Policy">` inside the converted file as a second line of defence when it is opened from disk. | Skip: the file is tested to hold no script or external resource, and the Canvas export may rewrite the head. |

## How the audit's controls are kept true

The audit's tests live in `tests/test_audit_*.py` and run with the normal suite (`uv run
pytest`). They fail when a route is added without being classified (`test_audit_authz.py`),
when a response kind loses a security header, when a state-changing route accepts a request
without the session's token, when a stranger can tell that someone else's id exists, when a
canary from a document reaches a log, event or cookie, when retention stops following the
clock, and when a model- or PDF-controlled field reaches the HTML unescaped.
