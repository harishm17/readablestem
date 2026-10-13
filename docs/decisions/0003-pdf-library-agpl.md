# ADR 0003: Keep PyMuPDF and disclose AGPL-3.0

| Field | Value |
|---|---|
| Status | **Accepted** (Harish, 2026-10-07) |
| Date | 2026-10-07 |
| Decider | Harish |

## Context

PyMuPDF (`pymupdf`) is licensed AGPL-3.0 (or a commercial licence from Artifex). The
hosted beta runs it on a network service. The alternative was `pypdfium2` + `pdfplumber`
(permissive), with lower parsing quality and more code.

## Decision

Keep PyMuPDF. This repository's own code stays MIT (`LICENSE`). Because the hosted app
combines MIT code with an AGPL library:

- the app links to its complete source (this public repository) in every page footer
  (`SOURCE_URL`), satisfying the AGPL network-use source offer;
- `THIRD_PARTY_NOTICES.md` names PyMuPDF and its licence;
- anyone redistributing or hosting a modified version must comply with AGPL-3.0 for the
  combined work.

Not legal advice; revisit if the project ever needs a closed-source deployment.

## Consequences

Drafted from what the repository shows; the author confirms or edits.

- **Easier.** Best parsing and rendering quality of the options compared, and less code
  than the permissive alternative.
- **Harder.** The AGPL applies to the hosted combination, so the complete source has to
  be offered to its users: the repository must be public (or the source offered another
  way) before the first outside user, and `SOURCE_URL` must point at the deployed commit.
  A closed-source deployment would need Artifex's commercial licence or a replacement
  library.
- **When to revisit.** If a closed-source deployment is ever needed, or if PyMuPDF's
  licence terms change (see the amendment below for PyMuPDF 2.0).

## Amendment (2026-10-09): table detection with PyMuPDF Layout

| Field | Value |
|---|---|
| Status | **Proposed** (merged behind `FEATURE_TABLES`, which is off; it stays proposed until the real-document check in `docs/eval.md`, "Tables", has passed) |
| Date | 2026-10-09 |
| Decider | Harish |

### Context

Data tables were not an element kind: their cells became paragraphs, numeric tables were
read as display equations and blank ruled tables as figures. PyMuPDF's own
`find_tables()` (lines strategy) found 0 of 108 booktabs and unruled tables in the
2026-10-08 benchmark. The evaluation (the author's private research notes, which are not
in this repository, so these numbers cannot be reproduced from here; their method is in
`docs/tables.md`) compared six detectors and four structure
recognisers that fit Cloud Run (1 vCPU, 1 GiB).

### Decision

- Keep PyMuPDF for glyphs, drawings, images and rendering, pinned at exactly **1.28.2**.
- Find tables with **PyMuPDF Layout** (`pymupdf-layout==1.28.2`,
  `page.find_tables(union=True, refine=True)`): the only accurate detector measured that
  fits 1 vCPU and 1 GiB. It carries the same terms as PyMuPDF (AGPL-3.0 or Artifex
  commercial), so the licence position above does not change in kind. The 1.28.2 licence
  was read from three primary sources (PyPI `License` field, the wheel's `COPYING`, the
  GitHub `LICENSE`); the PyPI classifier still says "Other/Proprietary", and earlier
  releases were under a commercial or noncommercial licence. Archive that evidence.
- Arbitrate every proposed box before it claims any text (the Challenge measured a
  precision of 0.68 on real lab manuals without it): a ruled-grid or aligned-text-grid
  check, a dot-leader rule for tables of contents, bullet columns, diagram labels and
  drawings, and box extension along the rules for blank worksheet tables.
- Recognise rows, columns and spans with **SLANet-plus** (Apache-2.0 weights, RapidTable's
  Apache-2.0 pre- and post-processing vendored), compared with the drawn grid and
  PyMuPDF's grid; fill cells with the PDF's own words.
- Run it all on `onnxruntime` (MIT), pinned to 1.31.x, with its thread pools sized to the
  process's CPU allowance (5-9 times slower otherwise on a 1-CPU container).

### Results (docs/tables.md has the method and every number)

- Four public lab manuals (393 pages, 87 real tables; local evaluation only): precision
  0.68 -> **1.00** and full-extent recall 0.72 -> **0.85** after arbitration, against the
  Challenge's floors of 0.95 and 0.85. The arbitration was developed on these same pages,
  so expect lower numbers on new documents; a private set of 3-5 course PDFs is the next
  check before `FEATURE_TABLES` is turned on.
- Detection and structure cost 0.33 s per page (median) at 1 CPU, 0.38 s mean.

### Risks

- **PyMuPDF 2.0** (on PyMuPDF's main branch, not yet on PyPI) folds the layout add-on into
  `pymupdf` and pulls in onnxruntime, numpy and networkx; `pymupdf_layout`'s own build
  now refuses to make a package. The `pymupdf-layout==1.28.2` pin is a holding position
  with known unpatched hangs (#5082, #5125; the probe bounds them). Before moving to 2.0:
  re-run the table evaluation, `make fuzz-tables`, the extraction hashes, the thread pin
  test and the timings.
- `union` and `refine` are new, opt-in parameters (1.28.2), documented only in the
  docstring and changelog; the exact pins and the fixture tests catch a change.
- Deeper lock-in: a permissive exit now needs two replacements (the glyph layer and the
  detector). PP-DocLayoutV2 (Apache-2.0) is the measured alternative detector (better on
  the real manuals, about 5 times slower and 550 MB).

### Launch blocker: the AGPL source offer

The AGPL network-use source offer above (a footer link to this repository's complete
source) is **not met while the repository is private** (checked with `gh` on 2026-10-09:
`PRIVATE`). Before the first user outside the project (allowlisted beta users
included), the repository must be public, or the source offered another way, and
`SOURCE_URL` must point at the deployed commit. TODO(Harish): make the repository public
(or decide another way to offer the source) before the beta; this change does not alter
its visibility. Optionally ask Artifex to confirm the 1.28.2 terms, without blocking on
the reply.

### Consequences

Drafted from what the repository shows; the author confirms or edits.

- **Easier.** Tables can be found and rebuilt on a 1-CPU container at about 0.33 s per
  page, with no GPU and no second service.
- **Harder.** A second AGPL package, two pinned ONNX models and vendored code to keep
  current. The `pymupdf-layout==1.28.2` pin is a holding position with known unpatched
  hangs that the probe bounds. A permissive exit now needs two replacements (the glyph
  layer and the detector). The arbitration rules were developed on the same pages they
  were measured on, so the numbers above are optimistic for new documents.
- **When to revisit.** Before moving to PyMuPDF 2.0 (re-run the table evaluation, the
  fuzz check and the timings), and before turning `FEATURE_TABLES` on (the private
  real-document check in `docs/eval.md`, "Tables").

