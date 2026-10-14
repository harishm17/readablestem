# Security policy

a11y-stem is a web service that takes PDF uploads from instructors, so security reports
are welcome and taken seriously. It is a pre-1.0 beta with a single maintainer; there is
no bug bounty.

## Supported versions

Only the latest commit on `main` is supported. There are no backports.

## Reporting a vulnerability

Please report privately, not in a public issue or pull request.

1. Preferred: use GitHub's private vulnerability reporting (the repository's Security tab,
   "Report a vulnerability").
2. Otherwise email harish_manoharan@outlook.com with "a11y-stem security" in the subject.

Include the commit you tested, the steps to reproduce, and what an attacker gains. Please
do not attach real student records or anyone else's documents; a small synthetic PDF is
enough. The maintainer aims to acknowledge a report within a week and to say what will
happen next within two. This is a best-effort promise from one person, not a service
level.

Please test against a local checkout (`docs/web.md`, "Run it locally") rather than the
hosted beta whenever you can, and do not run load tests against the hosted beta or touch
documents that are not yours.

## What is in scope

- The web app: sign-in and sessions, CSRF, upload handling, the review screen, access
  to other people's jobs, retention and deletion.
- The conversion of untrusted PDFs: crashes, hangs or memory blow-ups from a crafted
  file, and any way document content reaches HTML, logs or a model request without the
  checks described below.
- The generated HTML: script or markup injection through MathML, alt text, headings or
  filenames.
- Secrets in the repository, its history, the CI workflows or the deploy scripts.

Out of scope: findings that need an already compromised account or machine, volumetric
denial of service, social engineering, and problems in OpenAI, Anthropic, Google Cloud or Microsoft
services (report those to the provider). A wrong equation or a poor alt text is a
quality bug, not a vulnerability, unless it also leaks data. Please open an ordinary issue
for those.

## What the design assumes

These are design statements that the tests exercise; they are not guarantees.

- **PDFs and everything extracted from them are untrusted.** A PDF is first opened in a
  child process with a 30 second timeout, so a crash or hang in the PDF library kills the
  child and not the server (`docs/web.md`, "Uploads and limits"). Document text reaches a
  model only through a fenced context field. Model output is validated before use: LaTeX
  against an allowlist, MathML parsed with `defusedxml` and re-serialised from an
  allowlisted tree, alt text checked, and all HTML escaped (`docs/design/beta-architecture.md`,
  rule 2).
- **The converted document is served in a sandbox.** It gets its own Content Security
  Policy (`sandbox; default-src 'none'`), so it cannot run scripts, submit forms or read
  the app's cookies. App pages carry no inline scripts or styles (`docs/web.md`,
  "Security headers").
- **Logs carry no document content.** Event names, ids, counts, costs and category codes
  only; exceptions are logged as their type and code location, never their message.
- **Uploads are short-lived.** They live in the database, never on disk, and are deleted
  after 24 hours (30 days if the user opts in).
- **Spend is capped** per job and per day, and every model request reserves its worst-case
  cost before it is sent. Model requests are sent with `store=False`.
- **Secrets stay out of the repository.** `.env` is git-ignored, CI scans the full history
  with gitleaks, and production secrets live in Secret Manager (`docs/deploy.md`).

## Dependencies

Dependabot proposes updates weekly for Python packages, GitHub Actions and the base image.
PyMuPDF is pinned to an exact version on purpose (`docs/decisions/0003-pdf-library-agpl.md`).
A vulnerability in PyMuPDF itself is best reported to its maintainers (Artifex) as well.
