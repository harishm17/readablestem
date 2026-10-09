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
| py3langid 0.4 (Marco Lui, Adrien Barbaresi) | BSD-3-Clause | A dependency (with its bundled model file, under the same licence); detects the language of a document and of passages in another language when the PDF does not say (`pipeline/language.py`). Chosen over lingua-py (Apache-2.0, about 290 MB of models) and fastText's lid.176 (model under CC BY-SA 3.0): docs/layout.md "The text pass". Source: https://github.com/adbar/py3langid. |
| NumPy (NumPy Developers) | BSD-3-Clause (its wheels bundle OpenBLAS and other libraries under BSD-style licences) | Required by py3langid. |

The gold-set data under `data/gold/` is OpenStax content under CC BY 4.0; see
`data/gold/ATTRIBUTION.md`.
