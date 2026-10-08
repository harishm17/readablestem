# Third-party notices

This project's own code is MIT-licensed (`LICENSE`). It depends on third-party packages
under their own licences (see `uv.lock` for exact versions). The one with obligations
beyond attribution:

| Package | Licence | Notes |
|---|---|---|
| PyMuPDF (`pymupdf`) | AGPL-3.0 (or commercial from Artifex) | Used for PDF parsing and rendering. The hosted app links to this repository's complete source in every page footer. See `docs/decisions/0003-pdf-library-agpl.md`. |
| axe-core 4.14.0 (Deque Systems) | MPL-2.0 (bundles MIT-licensed code) | Vendored, unmodified, as `src/a11y_stem/pipeline/vendor/axe.min.js` with its licence files; runs in headless Chromium to check generated HTML. Source: https://github.com/dequelabs/axe-core. Version and checksums: `src/a11y_stem/pipeline/vendor/README.md`. |

The gold-set data under `data/gold/` is OpenStax content under CC BY 4.0; see
`data/gold/ATTRIBUTION.md`.
