# Held-out results that need no model

`INTERIM-heldout-2026-10.md` is built by `report.py` (`make proof-interim`, after
`make heldout-gold`) from `heldout-2026-10/`:

| Path | What |
|---|---|
| `pdfs/` | the 15 section PDFs every system was given (OpenStax retired first editions, CC BY 4.0, attribution in each PDF) |
| `a11y-headings/` | `readablestem eval pipeline --task headings --set heldout` (no model call): report and per-section items |
| `parsers/<arm>/` | what `proof/parsers` wrote for each section: `<book>__<page>.json` (status, wall time, memory, versions; no document text) and `.md` (the tool's Markdown export, images cut to a marker) |

Why it exists, and why it is not a recorded proof run: the paid parts of the held-out run
(ReadableSTEM's document, equations and figures tasks, the chatbot arms, the figure judge) could not
be made because the API account had no credit, so there is no bundle. See
`../PREREGISTRATION-ADDENDUM-3.md` and `../PREREGISTRATION-ADDENDUM-4.md`, which say exactly what
was run, and the top of the report, which says what it does not show.

One detail Addendum 4 gets slightly wrong: the headings task and the parsers were run with the
working tree at the commit named `ca034ad`, not `e9b5979`. The commits between them change the
proof harness, tests and documentation only; the pipeline's code and settings are the same.

Docling with formula recognition (a non-default arm of the parser plan) was started and stopped
after one section (11 pages, 1,495 s); it is not part of this data.
