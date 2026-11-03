# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
- Instructors (faculty, lecturers, TAs) at universities who hold a STEM course PDF (notes, slides, problem sets) and need to post an accessible web page in their course.
- Accessibility and disability-services staff, and procurement reviewers, who read the public pages to decide whether to pilot the tool. They read documents carefully and know WCAG.
- Students who open the converted page, many with a screen reader. The converted page is a separate output, not the site chrome.

## Product Purpose
ReadableSTEM assists instructors who need to turn a STEM course PDF into an accessible web page. It reads the PDF, rebuilds equations as MathML a screen reader can speak, restores headings and lists, writes short alt text for figures, and returns one self-contained HTML file. Every change it is unsure about goes to a review screen where the instructor accepts, edits or rejects it. It assists; it does not certify.

## Positioning
The converted output and the review step are shown, not claimed: the site shows a real PDF page beside the HTML it became and what a screen reader says for each. Results are published with sample sizes, including where a general chatbot does better.

## Operating Context
- Live at readablestem.com as a free beta (Python, FastAPI, server-rendered Jinja templates, one stylesheet, htmx; deployed on Railway). Sign-in with Google (or Microsoft for set-up universities).
- Public pages: landing, /demo (a stored conversion of OpenStax Physics 2.3), /limits, /help/posting, guides (ADA Title II, comparison with Mathpix and LaTeXML), /for-accessibility-offices, privacy, terms, status. App pages: upload, documents, job page, review (split and list), Symbols, account. A static showcase is built from `site/`.
- Strict Content-Security-Policy: no inline scripts, no outside requests, fonts and scripts self-hosted. Converted student documents are separate files and are sandboxed.

## Capabilities and Constraints
- Input is a PDF (Word and PowerPoint are first turned into PDFs); output is one HTML file, plus a Canvas zip and Common Cartridge.
- Not a compliance checker, not an OCR engine, not a general PDF-to-Markdown converter, does not work from LaTeX sources.
- Wording rules (CONTRIBUTING.md): the tool assists; no claim of conformance, guaranteed accessibility or checked results; numbers carry their sample size and say what they do not show; no document content in logs.
- Copy is the owner's. New labels are placeholders until reviewed.

## Brand Commitments
- Name: ReadableSTEM (formerly a11y-stem). Built and maintained by Harish Manoharan; MIT code; OpenStax material CC BY 4.0.
- Two-tone focus ring (dark outline with yellow halo) is kept as is.

## Evidence on Hand
- Held-out and development results in `PROOF.md` and `README.md`, with sample sizes; reports under `proof/`.
- A stored real conversion with its source PDF: `src/readablestem/web/static/demo/` (OpenStax Physics 2.3, untagged PDF).
- No testimonials, customer names or press exist; none may be invented.

## Product Principles
1. Show the real output next to the real source rather than describing it.
2. Every number names its set, sample size and date; the tool never claims to beat or certify.
3. Accessibility is the first product feature: readable text, visible focus, no information by colour alone, works with forced colours and 200% zoom.
4. The instructor decides; the interface makes the decisions easy to scan.

## Accessibility & Inclusion
Text contrast of at least 7:1, a visible two-tone focus ring, forced-colours support, reduced-motion support, usable at 200% zoom and 320px width, keyboard-complete flows. The site's own pages should be a fair example for an accessibility office to read.
