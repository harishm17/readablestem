# ADR 0009: A tagged PDF, an audio file, braille files and an LMS tool are not offered for now

| Field | Value |
|---|---|
| Status | **Accepted 2026-11-18** |
| Date | 2026-11-18 |
| Decider | Harish handed product and build decisions for ReadableSTEM to the maintainer of the plan on 2026-11-10; this is one of them |
| Revisits | nothing; it records what the outputs in docs/output.md leave out, and why |

## Decision

ReadableSTEM makes a web page, an EPUB, a Word file and a Canvas export, and reads the page aloud
in the browser (Listen). It does not make a tagged PDF, a downloadable audio file or a braille
file, and it does not connect to a learning management system as an installed tool. Each was
asked for in the audit (findings F-380, F-381, F-383) and each is declined for now, for a reason
that can change:

- **Tagged PDF.** No route we could run is verified to tag math: every equation has to be a
  Formula with a spoken alternative or carry MathML, and neither the browser's tagged print of
  the student page, LibreOffice from our Word file, nor LaTeX's own tagging has been shown to do
  it. What would change this: one of those routes passes veraPDF's PDF/UA-1 profile on our
  fixture documents and reads correctly in a PDF reader with a screen reader. Until then the
  print rules make "Save as PDF" a faithful copy, and the public guide keeps sending "it must
  stay a PDF" cases elsewhere.
- **Audio file.** Listen is free, runs in the reader's browser and reads math by our rules. A
  file needs a speech engine on the server: an open one (the license of the engine and of each
  voice checked, quality judged by a blind listener on a math document) or a paid one (a cost
  per document, and the text leaves our servers, which the privacy page would have to say).
  What would change this: a disability office or an instructor asks for a file students can
  take offline, and one engine passes those checks.
- **Braille files.** The page and the EPUB carry MathML, which a screen reader with a braille
  display turns into Nemeth or UEB itself, and the Word file has real equations, which is what a
  campus transcriber imports. What would change this: an office asks for BRF files, and a
  braille reader checks the result.
- **An OCR'd copy of the PDF.** Declined, not deferred: the web page is the readable copy.
- **LMS tool (LTI).** The Canvas export and the hosted link cover posting today. What would
  change this: campus accounts and teams exist in the app and one campus is ready to install
  the tool. The form then is LTI 1.3 with Deep Linking, so students open a document through the
  LMS without an account.

## Consequences

Public pages say "not offered" for each of these, with the route that exists instead, and claim
nothing about them. No code, route or test is added for any of them until its condition is met.
