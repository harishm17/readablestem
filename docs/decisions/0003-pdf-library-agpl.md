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

TODO(Harish)
