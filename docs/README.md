# Documentation index

The [README](../README.md) is the short version. This page says which document answers
which question. The docs are long because they record how each number and each rule was
arrived at; read them by question, not front to back.

## If you want to know what the numbers mean

| Question | Read |
|---|---|
| How is quality measured, on what data, with what error bars? | [eval.md](eval.md): "The idea", "Metrics", "Results", then "What the numbers do not show" |
| Why does the app ask a person about some conversions and not others? | [design/beta-architecture.md](design/beta-architecture.md), "Review tiers"; [eval.md](eval.md), "Review load" |
| How are equations and figures checked after the model has answered? | [steps.md](steps.md) |
| The proof cites a commit hash that is not in this repository. | [history-note.md](history-note.md): original hash to public hash, for every cited commit |
| How are headings, lists, equations and figures found in a PDF? | [layout.md](layout.md): "How a page is read" and "Known failure modes" |
| How do tables work, and why are they off by default? | [tables.md](tables.md); [eval.md](eval.md), "Tables" |
| Which equation scores are secondary, and what do they say about the held-out run? | [eval-secondary-metrics.md](eval-secondary-metrics.md) |
| What does the current system score on the held-out sections (a re-measurement, not blind)? | [remeasurement-2026-10-21.md](remeasurement-2026-10-21.md) |
| Does the page math pass help, and what did it cost? | [eval-page-math.md](eval-page-math.md); [decisions/0006-page-math-pass.md](decisions/0006-page-math-pass.md) |

## If you want to know how it is built

| Question | Read |
|---|---|
| What are the parts and the rules every part follows? | [design/beta-architecture.md](design/beta-architecture.md) |
| What do the optional content features promise? | [design/content-features.md](design/content-features.md) |
| What is in the HTML file that comes out? | [output.md](output.md) |
| Why do equations not have an `alttext` yet, and how is it tested? | [mathml-alttext.md](mathml-alttext.md) |
| How does the web app work (routes, sign-in, review screen, retention)? | [web.md](web.md) |
| How do I run, test and extend it? | [development.md](development.md) |
| What is the threat model, what protects it, and how do I report a problem? | [security.md](security.md) |
| What do I give an accessibility or procurement office? | [vendor-information.md](vendor-information.md), and the review record each document has (web.md "The review record") |
| How do I test the Canvas export in a real Canvas? | [canvas-sandbox-test.md](canvas-sandbox-test.md) |
| What is still open before real users, and what should the default be? | [launch.md](launch.md) |

## If you want to know why a choice was made

The decision records are in [decisions/](decisions/): 0001 scope, 0002 models, 0003 PDF
library and the AGPL, 0004 web stack, 0005 the second automated check, 0006 the page math pass (a proposal, off by default). Each ends with its
consequences.

## If you want to run it as a service

[deploy-railway.md](deploy-railway.md) for the current first deploy (Railway) and rollback,
[deploy.md](deploy.md) for the Google Cloud Run alternative and the settings behind both, [operations.md](operations.md)
for what runs on a schedule and what to do when something breaks. [../SECURITY.md](../SECURITY.md)
says how to report a vulnerability.

## Reading notes

- **Labels such as "MF10", "BW9", "FEATURE-REVIEW 10.3" or "handwriting design 3.3"** point
  into internal working notes (UX and feature reviews) that are not part of this
  repository. The text around each label states the decision, so nothing depends on
  finding the note.
- **"WP"** is a work package: a unit of the original build plan that owns one module
  contract.
- **`TODO(Harish)`** marks wording or a decision that the author has not settled yet.
  `uv run readablestem ops launch-check` lists the ones that still sit on the public pages
  of the web app.
- **Private research.** Some evidence (the PDF-parser comparison, the handwriting corpus,
  the lab-manual table set) comes from the author's private research folder. Where a number
  depends on it, the doc says so, and the number cannot be reproduced from this repository.
- **Licences.** Which files are under which licence is listed in
  [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
