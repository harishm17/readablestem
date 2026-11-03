# Accessibility conformance report (draft)

This folder holds a **draft** accessibility conformance report (ACR) for the ReadableSTEM web
app, laid out like the ITI VPAT 2.5Rev, WCAG edition (WCAG 2.2, levels A and AA). It is not
published, it is not linked from the site, and it is not a statement that the product
conforms to WCAG or to any other standard. The tool assists; this report says what has been
tested and what has not.

- [acr-draft.md](acr-draft.md): the report. Every row says what it rests on (a named test or a
  place in the code) and what is still open.
- How a row gets its level: **Supports** only where a test in this repository or the code
  itself shows it, and the row names which. **Partially Supports** where something is known not
  to meet the criterion everywhere. **Not Applicable** where the web app has nothing the
  criterion is about (no audio or video). **Not yet evaluated** where the answer depends on
  what a screen reader says, or where nobody has looked yet. Those rows stay that way until a
  session with VoiceOver in Safari is recorded ([../screen-reader-test.md](../screen-reader-test.md),
  its results table names the rows each result feeds). The VPAT form itself allows "Not
  Evaluated" only for level AAA, so before publication every such row is given a tested level.
- The converted file (the student's document) is a separate subject: its structure is described in
  [../output.md](../output.md) and checked by the output suites, and it is not part of this report.

## Keeping it true

A row's level changes only with its evidence: when a test is added, removed or changed, update
the row that names it. The keyboard rows rest on `tests/test_web_keyboard_browser.py` (a Tab
walk of every page and the main flows, with the helpers in `tests/fixtures/web/keyboard.py`);
the focus ring's colours on `tests/test_web_focus_style.py`. The other browser suites named in
the report are listed in its evidence table. Wording rules from [CONTRIBUTING.md](../../CONTRIBUTING.md)
apply: no claim of conformance, a guarantee, or a checked result.
