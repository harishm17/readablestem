# Third-party notices

This project's own code is MIT-licensed (`LICENSE`). It depends on third-party packages
under their own licences (see `uv.lock` for exact versions). The one with obligations
beyond attribution:

| Package | Licence | Notes |
|---|---|---|
| PyMuPDF (`pymupdf`) | AGPL-3.0 (or commercial from Artifex) | Used for PDF parsing and rendering. The hosted app links to this repository's complete source in every page footer. See `docs/decisions/0003-pdf-library-agpl.md`. |
| axe-core 4.14.0 (Deque Systems) | MPL-2.0 (bundles MIT-licensed code) | Vendored, unmodified, as `src/a11y_stem/pipeline/vendor/axe.min.js` with its licence files; runs in headless Chromium to check generated HTML. Source: https://github.com/dequelabs/axe-core. Version and checksums: `src/a11y_stem/pipeline/vendor/README.md`. |
| htmx 2.0.11 (Big Sky Software) | 0BSD | Vendored, unmodified, as `src/a11y_stem/web/static/vendor/htmx-2.0.11.min.js`; progressive enhancement only (pages work without JavaScript). Version and checksum: `src/a11y_stem/web/static/vendor/README.md`. |
| Latin Modern Math 1.959 (B. Jackowski, P. Strzelczyk, P. Pianowski, for the TeX Users Groups) | GUST Font License (an instance of the LPPL 1.3c) | Vendored, unmodified, as `src/a11y_stem/web/static/vendor/fonts/latinmodern-math.otf` with its licence, manifest and readme; draws proposed equations on the review screen. Source and checksum: `src/a11y_stem/web/static/vendor/README.md`. |

The gold-set data under `data/gold/` is OpenStax content under CC BY 4.0; see
`data/gold/ATTRIBUTION.md`.
