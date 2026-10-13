# Third-party notices

## Which licence covers which files

`LICENSE` is the plain MIT text. It covers the project's own work, with the exceptions in
this table. This is a plain statement of scope, not legal advice.

| Files | Licence | Where to read it |
|---|---|---|
| The code, tests, documentation, `deploy/`, `migrations/` and everything not listed below | MIT | `LICENSE` |
| `data/authored/` (documents written for this project: LaTeX and Word sources, their PDFs, gold labels) | MIT | `data/authored/README.md` |
| `data/eval/*.json` (the project's own eval reports: metrics, cost and dataset hashes) | MIT | The reports name OpenStax items by id only |
| `data/openstax-licenses.csv` (a table of each OpenStax book's licence, read from OpenStax's public interfaces) | MIT | `docs/decisions/0001-scope.md` |
| `data/gold/` (equations, figure alt text and captions, tables and headings extracted from OpenStax web books) | CC BY 4.0 (OpenStax) | `data/gold/ATTRIBUTION.md`, `data/gold/tables/ATTRIBUTION.md` |
| The precomputed demo page: `src/a11y_stem/web/static/demo/{index.html,report.json,source.pdf}` (OpenStax *Physics* 2.3, converted) | CC BY 4.0 (OpenStax) | Attribution is inside `index.html` |
| Test fixtures made from OpenStax content: `tests/fixtures/assemble/golden/demo.html`, `tests/fixtures/assemble/golden/stored_demo_0_2_0.html`, `tests/fixtures/stored/demo-report-0.2.0.json`, `tests/fixtures/sample_latex_predictions.jsonl` (LaTeX of OpenStax equations), `tests/fixtures/tables/table_bench_excerpt.json` (cell text from OpenStax tables) | CC BY 4.0 (OpenStax) | `data/gold/ATTRIBUTION.md` |
| `proof/bundles/` (recorded proof runs: the printed OpenStax sections as PDFs, a copy of the answer key, model answers, the HTML and Markdown that other systems wrote for those sections) | CC BY 4.0 (OpenStax) for everything derived from OpenStax text and figures, with the attribution in each PDF and in `gold/ATTRIBUTION.md`; the run metadata (`bundle.json`, counts, times, costs) is MIT. The held-out set uses retired first editions, which OpenStax licensed CC BY 4.0 | `proof/bundles/*/gold/ATTRIBUTION.md`, `docs/proof.md` |
| `proof/PREREGISTRATION.md`, `proof/PREREGISTRATION-ADDENDUM-1.md`, `proof/sets/` | MIT | |
| Vendored files under `src/a11y_stem/pipeline/vendor/` and `src/a11y_stem/web/static/vendor/` | Their own (table below) | The licence files beside them |

All OpenStax material used in this repository is from books that OpenStax publishes under
CC BY 4.0. Books published under CC BY-NC-SA are not used. a11y-stem is not affiliated
with or endorsed by OpenStax. The PDFs under `tests/fixtures/` are built from sources in
the repository (LaTeX, HTML, python-docx or a script) with invented text; the one
OpenStax PDF is the demo's `source.pdf`.

The hosted service links PyMuPDF (AGPL-3.0), so the combined work, when run as a network
service, is offered under the AGPL-3.0 terms through the source link in every page
footer. The code in this repository stays MIT. See
`docs/decisions/0003-pdf-library-agpl.md`.

## Third-party packages

This project depends on third-party packages under their own licences (see `uv.lock` for
exact versions). The ones with obligations beyond attribution are PyMuPDF and its layout
add-on (AGPL-3.0), and psycopg (LGPL-3.0, used unmodified as a dependency):

| Package | Licence | Notes |
|---|---|---|
| PyMuPDF (`pymupdf`) | AGPL-3.0 (or commercial from Artifex) | Used for PDF parsing and rendering. The hosted app links to this repository's complete source in every page footer. See `docs/decisions/0003-pdf-library-agpl.md`. |
| PyMuPDF Layout (`pymupdf-layout` 1.28.2, Artifex) | AGPL-3.0 (or commercial from Artifex), the same terms as PyMuPDF | Finds table regions (`page.find_tables(union=True, refine=True)` with its ONNX layout models, which ship in the wheel). Licence of 1.28.2 read from its PyPI metadata, wheel `COPYING` and GitHub `LICENSE` (the PyPI classifier still says "Other/Proprietary"; earlier releases were under other terms). See the 2026-10-09 amendment in `docs/decisions/0003-pdf-library-agpl.md`. |
| psycopg and psycopg-binary 3.3 (the psycopg team) | LGPL-3.0-only | The PostgreSQL driver of the hosted app. Used unmodified and installed from PyPI, not vendored. LGPL-3.0 lets you replace it with another build of the library. Source: https://github.com/psycopg/psycopg. |
| certifi | MPL-2.0 | Root certificates for HTTPS, pulled in by httpx and the OpenAI client. Used unmodified. |
| pgserver | no licence metadata | A development dependency only (an embedded PostgreSQL for local tests, `dev` group). It is not installed in the Docker image and not distributed with this project. |
| onnxruntime (Microsoft) | MIT | Runs the layout models and SLANet-plus on the CPU. Pinned to 1.31.x. |
| SLANet-plus model (PaddlePaddle, from PaddleOCR) | Apache-2.0 | Recognises a table's rows, columns and spans. The ONNX file RapidTable 3.0.2 distributes is downloaded at build time and checked against its SHA-256 (`src/a11y_stem/pipeline/table_model.py`); it is not in this repository. |
| RapidTable 3.0.2 (RapidAI; code from PaddleOCR's PP-Structure) | Apache-2.0 | The pre- and post-processing around SLANet-plus, vendored with changes as `src/a11y_stem/pipeline/vendor/rapid_table.py`, with its licence (`rapid_table-LICENSE.txt`) and notice (`rapid_table-NOTICE.txt`). Source: https://github.com/RapidAI/RapidTable. |
| NumPy, Pillow, networkx, flatbuffers | BSD-3-Clause (NumPy, networkx), MIT-CMU (Pillow), Apache-2.0 (flatbuffers) | NumPy and Pillow prepare the image for SLANet-plus; networkx (used by the layout add-on) and flatbuffers (by onnxruntime) come in with them. |
| Docling, MinerU, PyMuPDF4LLM (the parsers compared in `proof/parsers`) | MIT (Docling), the MinerU Open Source License (Apache-2.0 plus extra terms: credit MinerU), AGPL-3.0 (PyMuPDF4LLM) | Used only by `proof/parsers`, a separate uv project with one environment per tool outside this repository. They are not dependencies of the app or of its Docker image. Versions and licences: `proof/parsers/README.md`. |
| axe-core 4.14.0 (Deque Systems) | MPL-2.0 (bundles MIT-licensed code) | Vendored, unmodified, as `src/a11y_stem/pipeline/vendor/axe.min.js` with its licence files; runs in headless Chromium to check generated HTML. Source: https://github.com/dequelabs/axe-core. Version and checksums: `src/a11y_stem/pipeline/vendor/README.md`. |
| htmx 2.0.11 (Big Sky Software) | 0BSD | Vendored, unmodified, as `src/a11y_stem/web/static/vendor/htmx-2.0.11.min.js`; progressive enhancement only (pages work without JavaScript). Version and checksum: `src/a11y_stem/web/static/vendor/README.md`. |
| Latin Modern Math 1.959 (B. Jackowski, P. Strzelczyk, P. Pianowski, for the TeX Users Groups) | GUST Font License (an instance of the LPPL 1.3c) | Vendored, unmodified, as `src/a11y_stem/web/static/vendor/fonts/latinmodern-math.otf` with its licence, manifest and readme; draws proposed equations on the review screen. Source and checksum: `src/a11y_stem/web/static/vendor/README.md`. |
| py3langid 0.4 (Marco Lui, Adrien Barbaresi) | BSD-3-Clause | A dependency (with its bundled model file, under the same licence); detects the language of a document and of passages in another language when the PDF does not say (`pipeline/language.py`). Chosen over lingua-py (Apache-2.0, about 290 MB of models) and fastText's lid.176 (model under CC BY-SA 3.0): docs/layout.md "The text pass". Source: https://github.com/adbar/py3langid. |
| NumPy (NumPy Developers) | BSD-3-Clause (its wheels bundle OpenBLAS and other libraries under BSD-style licences) | Required by py3langid. |

The gold-set data under `data/gold/` is OpenStax content under CC BY 4.0; see
`data/gold/ATTRIBUTION.md`.
