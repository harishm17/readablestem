# Documentation index

The [README](../README.md) is the short version. This page says which document answers
which question. The docs are long because they record how each number and each rule was
arrived at; read them by question, not front to back.

**Start here**

| If you want | Read |
|---|---|
| Every measured result on one page | [results.md](results.md) |
| How quality is measured and what the numbers leave out | [eval.md](eval.md) |
| How a PDF page is read | [layout.md](layout.md) |
| What is in the file that comes out | [output.md](output.md) |
| To run, test and extend it | [development.md](development.md) |
| Why a choice was made | [decisions/](decisions/) |

On this page: [Results and how they are measured](#results-and-how-they-are-measured) ·
[Evaluation reports](#evaluation-reports) · [How it is built](#how-it-is-built) ·
[Why a choice was made](#why-a-choice-was-made) · [Running it as a service](#running-it-as-a-service) ·
[Reading notes](#reading-notes)

## Results and how they are measured

| Question | Read |
|---|---|
| What are all the results, and which were measured on documents the system had never seen? | [results.md](results.md) |
| What is the newest registered result, and what may be claimed from it? | [eval-equation-fresh5.md](eval-equation-fresh5.md) (fresh5, 26 documents, 2026-11-04: no difference from the Opus chatbot this set can detect) |
| How is quality measured, on what data, with what error bars? | [eval.md](eval.md): "The idea", "Metrics", "Results", then "What the numbers do not show" |
| How is the recorded proof run replayed with one command (`make proof`)? | [proof.md](proof.md); [../PROOF.md](../PROOF.md) |
| What does the current system score on the held-out sections (a re-measurement, not blind)? | [remeasurement-2026-10-21.md](remeasurement-2026-10-21.md) |
| Which equation scores are secondary, and what do they say about the held-out run? | [eval-secondary-metrics.md](eval-secondary-metrics.md) |
| How is the invented-content rate (output the PDF does not support) computed? | [eval-invented.md](eval-invented.md) |
| Why does the app ask a person about some conversions and not others? | [steps.md](steps.md); [eval.md](eval.md), "Review load" |

## Evaluation reports

One report per measurement, each with what was run, on what, and what it cost.

**Equations on real papers**

| Question | Read |
|---|---|
| How does it do on real LaTeX PDFs from arXiv (not Chromium prints), against the chatbot recipe? | [eval-realworld.md](eval-realworld.md) |
| Which causes held back its equation score on real papers, what was fixed, and what did a fresh set of papers say? | [eval-equation-rootcause.md](eval-equation-rootcause.md) |
| What did the second round of equation fixes do, on a third registered set of papers? | [eval-equation-round2.md](eval-equation-round2.md) |
| What did reading each inline equation with its line do, on a fourth registered set of papers? | [eval-equation-round3.md](eval-equation-round3.md) |
| What did the page witness do on twelve development papers (round 5, no default changed)? | [eval-equation-round5.md](eval-equation-round5.md) |
| Where does ReadableSTEM stand today on the 25 real-world development papers, against the recorded Opus and Haiku chatbot answers (pipeline 0.5.3, production flags)? | [eval-dev-baseline-2026-11-03.md](eval-dev-baseline-2026-11-03.md) |
| Does the page math pass help, and what did it cost? | [eval-page-math.md](eval-page-math.md); [decisions/0006-page-math-pass.md](decisions/0006-page-math-pass.md) |

**Which equations a person is asked about**

| Question | Read |
|---|---|
| Why are math in table cells missed, do the misses carry glyph evidence the rules ignore, and why were wrong equations not flagged? (no model, recorded runs) | [eval-equation-diagnostics.md](eval-equation-diagnostics.md) |
| What do the glyph checks (a reading's extent past its box, letter styles from the fonts) do to flag recall and decisions, replayed on recorded runs? | [eval-equation-glyph-checks.md](eval-equation-glyph-checks.md) |
| Which accepted equations does a small calibrated rule (`FLAG_POLICY`) ask a person about, fitted on development and replayed on fresh2 to fresh4? | [eval-flag-policy.md](eval-flag-policy.md) |

**Chemistry, scans, handwriting and figures**

| Question | Read |
|---|---|
| How well are chemistry formulas read on five held-out sections, and what did a second round of chemistry prompts do (not shipped)? | [eval-chemistry-heldout.md](eval-chemistry-heldout.md); [eval-chemistry-round2.md](eval-chemistry-round2.md) |
| Can scanned and handwritten pages be converted, how well, and at what cost? | [eval-image-pages.md](eval-image-pages.md); [decisions/0007-image-pages.md](decisions/0007-image-pages.md) |
| How well does it read real human handwriting (an answer key of ink with known LaTeX), and may the app advertise it? | [eval-handwriting.md](eval-handwriting.md) |
| How are volunteers' hand copies of OpenStax examples collected, with answer keys from the source, and published only with consent? | [data/handwriting-copy/README.md](../data/handwriting-copy/README.md) |
| What is reader B (a second reading of a handwritten expression), what does it cost, and what must change before one is turned on? | [math-readers.md](math-readers.md) |
| Which 50 figures will long descriptions and chart data tables be judged on, how were they drawn, and what is pre-registered (draft, unsigned: no model has run on it)? | [eval-figures-long.md](eval-figures-long.md) |

**Reading a page, with no model**

| Question | Read |
|---|---|
| How does the text path do on a public parsing benchmark (a subset of olmOCR-Bench, at no cost)? | [eval-parsing-benchmark.md](eval-parsing-benchmark.md) |
| Which words of a page does no element say, and which are said twice (a development-set count)? | [eval-say-once.md](eval-say-once.md) |

## How it is built

**The pipeline**

| Question | Read |
|---|---|
| How are headings, lists, equations and figures found in a PDF? | [layout.md](layout.md): "How a page is read" and "Known failure modes" |
| How are equations and figures checked after the model has answered? | [steps.md](steps.md) |
| How do tables work (off by default in the code)? | [tables.md](tables.md); [eval.md](eval.md), "Tables" |
| How are formulas, ions and reactions found, written (`\ce`) and read aloud? | [chemistry.md](chemistry.md) |
| What is in the HTML file that comes out? | [output.md](output.md) |
| What is in the EPUB export? | [output.md](output.md), "The EPUB export" |
| Why do equations not have an `alttext` yet, and how is it tested? | [mathml-alttext.md](mathml-alttext.md) |

**The web app and the repository**

| Question | Read |
|---|---|
| How does the web app work (routes, sign-in, review screen, retention)? | [web.md](web.md) |
| How do I run, test and extend it? | [development.md](development.md) |
| What is the threat model, what protects it, and how do I report a problem? | [security.md](security.md) |
| What does the app depend on, under which licenses, and what limits the parts that read a stranger's file? | [dependencies.md](dependencies.md) (generated; the rules are in [development.md](development.md)) |
| What is the static reviewer demo under `site/`, and how is it built? | [site.md](site.md) |

**Checking it with people and devices**

| Question | Read |
|---|---|
| What do I give an accessibility or procurement office? | [vendor-information.md](vendor-information.md), the site's `/for-accessibility-offices` page and its downloadable one-page summary (web.md), and the review record each document has (web.md "The review record") |
| What has been tested for keyboard use and WCAG 2.2 on the web app, and what has not (the draft accessibility conformance report)? | [acr/README.md](acr/README.md) and [acr/acr-draft.md](acr/acr-draft.md) |
| How do I check the app and its files on real devices and screen readers (Safari on a Mac and an iPhone, VoiceOver, NVDA, JAWS, TalkBack, EPUB, Word, Canvas, Spanish Listen, high contrast)? | [device-checks.md](device-checks.md) |
| How do I test the Canvas export in a real Canvas? | [canvas-sandbox-test.md](canvas-sandbox-test.md) |

## Why a choice was made

Each decision record ends with its consequences.

| Record | Decision |
|---|---|
| [0001](decisions/0001-scope.md) | Scope |
| [0002](decisions/0002-models.md) | Models |
| [0003](decisions/0003-pdf-library-agpl.md) | The PDF library and the AGPL |
| [0004](decisions/0004-web-stack.md) | Web stack |
| [0005](decisions/0005-decisions-api.md) | The OpenAI Decisions API as a second automated check |
| [0006](decisions/0006-page-math-pass.md) | The page math pass (a proposal, off by default) |
| [0007](decisions/0007-image-pages.md) | Converting scanned and handwritten pages |
| [0008](decisions/0008-latex-source.md) | LaTeX source uploads converted by LaTeXML in a sandbox (see also [web.md](web.md), "LaTeX source", and [security.md](security.md), "Running uploaded TeX") |
| [0009](decisions/0009-formats-not-offered.md) | Why there is no tagged PDF, audio file, braille file or LMS tool |

## Running it as a service

| Question | Read |
|---|---|
| How is it deployed today (Railway), and how is a deploy rolled back? | [deploy-railway.md](deploy-railway.md) |
| What is the Google Cloud Run alternative, and what are the settings behind both? | [deploy.md](deploy.md) |
| What runs on a schedule, and what do I do when something breaks? | [operations.md](operations.md) |
| How do I report a vulnerability? | [../SECURITY.md](../SECURITY.md) |

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
- **Licenses.** Which files are under which license is listed in
  [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
