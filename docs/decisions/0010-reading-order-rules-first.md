# ADR 0010: Reading order and regions are found by rules; no learned layout reader runs when a document is converted

| Field | Value |
|---|---|
| Status | **Accepted 2026-11-19** |
| Date | 2026-11-19 |
| Decider | Harish handed product and build decisions for ReadableSTEM to the maintainer of the plan on 2026-11-10; this is one of them |
| Revisits | when a learned reader beats the rules on held-out real pages (see "What would change this") |

## Decision

The order a page is read in, and where its regions (columns, tables, figures, captions, notes)
are, is found by rules in `pipeline/layout/`: no model, no learned layout detector, in either
of two ways:

- **not to find regions or order**, and
- **not as a second opinion** on the rules' answer.

No learned layout reader is added to the conversion. Docling's layout model (Heron) is not
added. The page check (`WS15.8`) may flag a page whose order looks wrong, but it never moves an
element: only the rules order a page.

## Why

Two audit reports disagreed. One said leading parsers run a learned layout detector and that
the rules' "stitching" faults come from not having one. The accuracy rounds of November 2026
measured it, with no model cost, on pages the tool had to read in order.

**Development figures** (the pages are in `layout-dev-1`, which is development in
[eval.md](../eval.md), "Layout gold sets"; no figure here comes from `layout-heldout-1`, and the
scores of the half once kept sealed are not quoted on their own):

| Measure | Reader before the rules | Rules, first round | Rules, second round | Learned reader |
|---|---|---|---|---|
| Pages fully right, 78 development pages (scorer v3) | 59 | 70 | 72 | 50 with PyMuPDF's layout reader (29 of the real pages, 21 of the pages made here); 55 with Docling's Heron as region finder |
| Pages fully right, the 132 development pages of the second round (scorer v4) | 70 | 70 | 89 | not measured |

The rules beat both learned readers on the 78 pages by 15 to 20 pages, and the second round's
rules gained 19 more pages on the 132.

Since then the region tree (`pipeline/layout/regions.py`, behind `REGION_TREE`) has been built
on the same rules. Measured on layout-dev-1's 267 pages, with no model: 141 pages right with the
tree off and 163 with it on (22 made right, none made wrong), and on the 91 corpus gold pages
70 right off and 82 on (none made wrong). Those figures are from 2026-11-18 and 2026-11-19 and
are the builders' counts; the tree stayed off in production until the paid re-recording of
pipeline 0.5.18 (2026-11-23), which turned it on (`docs/layout.md`, "Measured").

**Cost.** A learned reader takes about 3.5 CPU-seconds and up to 1.4 GB of memory a page. The
rules take 0.607 s a page today and 0.799 s with the second round's rules (542 development
pages, one process, a loaded machine), so they add 0.1 to 0.2 s, with a peak of 830 to 856 MB
for the whole conversion. The first round's figure of 0.01 to 0.02 s added was withdrawn: it
came from a run where the rules were a patch layer over the repository's functions. The region
tree itself costs nothing measurable: 0.575 s a page off and 0.578 s on, on a quiet box.

**As a second opinion.** Once the rules are in, a learned reader catches about half of what is
left wrong. That does not pay for 3.5 s and 1.4 GB a page, and a flag from a second reader
would have to be answered by a person on every page it fired on.

## What this does not decide

- PyMuPDF's layout package is already shipped for table proposals (`pyproject.toml`,
  `pymupdf-layout`; `pipeline/tables.py`). Whether it stays is for the table plan and the
  platform plan (WS5.16, WS26.7); the answer there is that it stays. Reading order does not
  use it either way.
- Its license is settled by [ADR 0003](0003-pdf-library-agpl.md) and its amendment of
  2026-10-09.
- Whether the region tree is turned on. That needs a paid re-recording of the answers whose
  crops change and a bump of `PIPELINE_VERSION`; the owner decides when.

## What would change this

A learned layout reader beats the rules on held-out real pages by a margin that the held-out
comparison shows, on every run of it: the comparison rows (PyMuPDF's learned reader, Docling,
and the PDF's own tag order) are kept offline in the layout eval (`readablestem eval order`)
so they can be run again without a model. A margin that appears on development pages only does
not count; neither does one that needs a model call on every page.

## Revisit in progress

2026-11-26: the model's page order is built as a product step behind an off-by-default switch
(`FEATURE_LAYOUT_MODEL_ORDER`, docs/layout.md "The model's page order"), because on the 267
development pages of layout-dev-1 a cheap model's grouping and order, replayed through the
product (`eval order --model-order`), gave the student 184 pages right where the rules gave
183, and 140 with no fault where the rules had 94 (in the elements before the page is written:
188 against 182, and 148 against 95). That is development evidence only, which "What would
change this" above does not count. That section also does not count a margin that needs a
model call on every page; this step needs one. The owner asked on 2026-11-26 for the harder
steps to be tried with a model before more rules are built, so turning the switch on would
amend this record, not meet it: the amendment states the held-out margin and the price per
page. Nothing changes here until one run on held-out real pages decides whether the switch goes
on; until then reading order is found by rules and checked, and the public pages still say so.

## Consequences

The public pages say that order is found by rules and checked, and they claim nothing about a
learned reader. No test fails on an import of a learned reader: the rule is kept by this
record and by review, not by a guard. Nothing in the product changes.

Each figure above is dated and has its scorer version, so the next person to read this can tell
whether a newer measurement has replaced it.
