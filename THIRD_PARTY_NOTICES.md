# Third-party notices

This project's own code is MIT-licensed (`LICENSE`). It depends on third-party packages
under their own licences (see `uv.lock` for exact versions). The ones with obligations
beyond attribution are PyMuPDF and its layout add-on (AGPL-3.0):

| Package | Licence | Notes |
|---|---|---|
| PyMuPDF (`pymupdf`) | AGPL-3.0 (or commercial from Artifex) | Used for PDF parsing and rendering. The hosted app links to this repository's complete source in every page footer. See `docs/decisions/0003-pdf-library-agpl.md`. |
| PyMuPDF Layout (`pymupdf-layout` 1.28.2, Artifex) | AGPL-3.0 (or commercial from Artifex), the same terms as PyMuPDF | Finds table regions (`page.find_tables(union=True, refine=True)` with its ONNX layout models, which ship in the wheel). Licence of 1.28.2 read from its PyPI metadata, wheel `COPYING` and GitHub `LICENSE` (the PyPI classifier still says "Other/Proprietary"; earlier releases were under other terms). See the 2026-10-09 amendment in `docs/decisions/0003-pdf-library-agpl.md`. |
| onnxruntime (Microsoft) | MIT | Runs the layout models and SLANet-plus on the CPU. Pinned to 1.31.x. |
| SLANet-plus model (PaddlePaddle, from PaddleOCR) | Apache-2.0 | Recognises a table's rows, columns and spans. The ONNX file RapidTable 3.0.2 distributes is downloaded at build time and checked against its SHA-256 (`src/a11y_stem/pipeline/table_model.py`); it is not in this repository. |
| RapidTable 3.0.2 (RapidAI; code from PaddleOCR's PP-Structure) | Apache-2.0 | The pre- and post-processing around SLANet-plus, vendored with changes as `src/a11y_stem/pipeline/vendor/rapid_table.py`, with its licence (`rapid_table-LICENSE.txt`) and notice (`rapid_table-NOTICE.txt`). Source: https://github.com/RapidAI/RapidTable. |
| NumPy, Pillow, networkx, flatbuffers | BSD-3-Clause (NumPy, networkx), MIT-CMU (Pillow), Apache-2.0 (flatbuffers) | NumPy and Pillow prepare the image for SLANet-plus; networkx (used by the layout add-on) and flatbuffers (by onnxruntime) come in with them. |
| axe-core 4.14.0 (Deque Systems) | MPL-2.0 (bundles MIT-licensed code) | Vendored, unmodified, as `src/a11y_stem/pipeline/vendor/axe.min.js` with its licence files; runs in headless Chromium to check generated HTML. Source: https://github.com/dequelabs/axe-core. Version and checksums: `src/a11y_stem/pipeline/vendor/README.md`. |
| htmx 2.0.11 (Big Sky Software) | 0BSD | Vendored, unmodified, as `src/a11y_stem/web/static/vendor/htmx-2.0.11.min.js`; progressive enhancement only (pages work without JavaScript). Version and checksum: `src/a11y_stem/web/static/vendor/README.md`. |
| Latin Modern Math 1.959 (B. Jackowski, P. Strzelczyk, P. Pianowski, for the TeX Users Groups) | GUST Font License (an instance of the LPPL 1.3c) | Vendored, unmodified, as `src/a11y_stem/web/static/vendor/fonts/latinmodern-math.otf` with its licence, manifest and readme; draws proposed equations on the review screen. Source and checksum: `src/a11y_stem/web/static/vendor/README.md`. |
| py3langid 0.4 (Marco Lui, Adrien Barbaresi) | BSD-3-Clause | A dependency (with its bundled model file, under the same licence); detects the language of a document and of passages in another language when the PDF does not say (`pipeline/language.py`). Chosen over lingua-py (Apache-2.0, about 290 MB of models) and fastText's lid.176 (model under CC BY-SA 3.0): docs/layout.md "The text pass". Source: https://github.com/adbar/py3langid. |
| NumPy (NumPy Developers) | BSD-3-Clause (its wheels bundle OpenBLAS and other libraries under BSD-style licences) | Required by py3langid. |

The gold-set data under `data/gold/` is OpenStax content under CC BY 4.0; see
`data/gold/ATTRIBUTION.md`.
