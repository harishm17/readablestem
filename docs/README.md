# Documentation index

The [README](../README.md) is the short version. This page says which document answers
which question. The docs are long because they record how each number and each rule was
arrived at; read them by question, not front to back.

## If you want to know what the numbers mean

| Question | Read |
|---|---|
| What is the newest registered result, and what may be claimed from it? | [eval-equation-fresh5.md](eval-equation-fresh5.md) (fresh5, 26 documents, 2026-11-04: no difference from the Opus chatbot this set can detect) |
| How is quality measured, on what data, with what error bars? | [eval.md](eval.md): "The idea", "Metrics", "Results", then "What the numbers do not show" |
| Why does the app ask a person about some conversions and not others? | [steps.md](steps.md); [eval.md](eval.md), "Review load" |
| How are equations and figures checked after the model has answered? | [steps.md](steps.md) |
| How are headings, lists, equations and figures found in a PDF? | [layout.md](layout.md): "How a page is read" and "Known failure modes" |
| How do tables work (off by default in the code, on in production)? | [tables.md](tables.md); [eval.md](eval.md), "Tables" |
| Which equation scores are secondary, and what do they say about the held-out run? | [eval-secondary-metrics.md](eval-secondary-metrics.md) |
| What does the current system score on the held-out sections (a re-measurement, not blind)? | [remeasurement-2026-10-21.md](remeasurement-2026-10-21.md) |
| Does the page math pass help, and what did it cost? | [eval-page-math.md](eval-page-math.md); [decisions/0006-page-math-pass.md](decisions/0006-page-math-pass.md) |
| How are formulas, ions and reactions found, written (`\ce`) and read aloud? | [chemistry.md](chemistry.md) |
| Can scanned and handwritten pages be converted, how well, and at what cost? | [eval-image-pages.md](eval-image-pages.md); [decisions/0007-image-pages.md](decisions/0007-image-pages.md) |
| Why does LaTeX source upload run LaTeXML in a sandbox, and what does it accept? | [decisions/0008-latex-source.md](decisions/0008-latex-source.md); [web.md](web.md), "LaTeX source"; [security.md](security.md), "Running uploaded TeX" |
| How well does it read real human handwriting (an answer key of ink with known LaTeX), and may the app advertise it? | [eval-handwriting.md](eval-handwriting.md) |
| How are volunteers' hand copies of OpenStax examples collected, with answer keys from the source, and published only with consent? | [data/handwriting-copy/README.md](../data/handwriting-copy/README.md) |
| What is reader B (a second reading of a handwritten expression), what does it cost, and what must change before one is turned on? | [math-readers.md](math-readers.md) |
| How does it do on real LaTeX PDFs from arXiv (not Chromium prints), against the chatbot recipe? | [eval-realworld.md](eval-realworld.md) |
| Where does ReadableSTEM stand today on the 25 real-world development papers, against the recorded Opus and Haiku chatbot answers (pipeline 0.5.3, production flags)? | [eval-dev-baseline-2026-11-03.md](eval-dev-baseline-2026-11-03.md) |
| Which causes held back its equation score on real papers, what was fixed, and what did a fresh set of papers say? | [eval-equation-rootcause.md](eval-equation-rootcause.md) |
| What did the second round of equation fixes do, on a third registered set of papers? | [eval-equation-round2.md](eval-equation-round2.md) |
| What did reading each inline equation with its line do, on a fourth registered set of papers? | [eval-equation-round3.md](eval-equation-round3.md) |
| Which 50 figures will long descriptions and chart data tables be judged on, how were they drawn, and what is pre-registered (draft, unsigned: no model has run on it)? | [eval-figures-long.md](eval-figures-long.md) |
| What did the page witness do on twelve development papers (round 5, no default changed)? | [eval-equation-round5.md](eval-equation-round5.md) |
| How well are chemistry formulas read on five held-out sections, and what did a second round of chemistry prompts do (not shipped)? | [eval-chemistry-heldout.md](eval-chemistry-heldout.md); [eval-chemistry-round2.md](eval-chemistry-round2.md) |
| How is the invented-content rate (output the PDF does not support) computed? | [eval-invented.md](eval-invented.md) |
| How is the recorded proof run replayed with one command (`make proof`)? | [proof.md](proof.md); [../PROOF.md](../PROOF.md) |
| Why are maths in table cells missed, do the misses carry glyph evidence the rules ignore, and why were wrong equations not flagged? (no model, recorded runs) | [eval-equation-diagnostics.md](eval-equation-diagnostics.md) |
| What do the glyph checks (a reading's extent past its box, letter styles from the fonts) do to flag recall and decisions, replayed on recorded runs? | [eval-equation-glyph-checks.md](eval-equation-glyph-checks.md) |
| Which accepted equations does a small calibrated rule (`FLAG_POLICY`) ask a person about, fitted on development and replayed on fresh2 to fresh4? | [eval-flag-policy.md](eval-flag-policy.md) |

## If you want to know how it is built

| Question | Read |
|---|---|
| What is in the HTML file that comes out? | [output.md](output.md) |
| Why do equations not have an `alttext` yet, and how is it tested? | [mathml-alttext.md](mathml-alttext.md) |
| How does the web app work (routes, sign-in, review screen, retention)? | [web.md](web.md) |
| How do I run, test and extend it? | [development.md](development.md) |
| What is the threat model, what protects it, and how do I report a problem? | [security.md](security.md) |
| What is the static reviewer demo under `site/`, and how is it built? | [site.md](site.md) |
| What do I give an accessibility or procurement office? | [vendor-information.md](vendor-information.md), the site's `/for-accessibility-offices` page and its downloadable one-page summary (web.md), and the review record each document has (web.md "The review record") |
| What is in the EPUB export? | [output.md](output.md), "The EPUB export" |
| How do I test the Canvas export in a real Canvas? | [canvas-sandbox-test.md](canvas-sandbox-test.md) |
| How do I check what a screen reader says (VoiceOver in Safari) on the demo, a converted file and the review screen? | [screen-reader-test.md](screen-reader-test.md) |
| What has been tested for keyboard use and WCAG 2.2 on the web app, and what has not (the draft accessibility conformance report)? | [acr/README.md](acr/README.md) and [acr/acr-draft.md](acr/acr-draft.md) |

## If you want to know why a choice was made

The decision records are in [decisions/](decisions/): 0001 scope, 0002 models, 0003 PDF
library and the AGPL, 0004 web stack, 0005 the OpenAI Decisions API as a second automated check, 0006 the page math pass (a proposal, off by default), 0007 converting scanned and handwritten pages, 0008 LaTeX source uploads converted by LaTeXML in a sandbox. Each ends with its
consequences.

## If you want to run it as a service

[deploy-railway.md](deploy-railway.md) for the current first deploy (Railway) and rollback,
[deploy.md](deploy.md) for the Google Cloud Run alternative and the settings behind both, [operations.md](operations.md)
for what runs on a schedule and what to do when something breaks. [../SECURITY.md](../SECURITY.md)
says how to report a vulnerability.

## Reading notes

- **Labels such as "MF10", "BW9", "FEATURE-REVIEW 10.3", "handwriting design 3.3" or a
  backlog id like "T2-0" or "T3-30"** point
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
