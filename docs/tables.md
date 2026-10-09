# Tables

Data tables in a PDF become `Kind.TABLE` elements with a grid of cells (pipeline 0.4.0).
The work is split as in [design/content-features.md](design/content-features.md): this
page's first part is **detection** (`pipeline/tables.py`, the local grid); the table step
(the model's rebuilt grid, its checks and the HTML), the table evaluation and the review
card add their own parts.

## Detection (`pipeline/tables.py`)

Layout calls `tables.find(page, lines, paths)` for every analysed page, before columns,
rows, headings and equations are worked out ([layout.md](layout.md), "How a page is
read", step 0). No model call; the same PDF always gives the same tables.

### How a page's tables are found

1. **Propose.** PyMuPDF Layout (`pymupdf-layout`, ONNX layout models in the wheel) with
   `page.find_tables(union=True, refine=True)`, under layout's PyMuPDF lock. It returns
   boxes in display coordinates (on a page with `/Rotate` too) and PyMuPDF's own refined
   grid for each box.
2. **Arbitrate** (`judge`). On four real lab manuals the detector alone had a precision
   of 0.68: it also boxes vector figures, tables of contents, equations and bullet lists.
   Each box is first trimmed of a `"Table N"` caption the detector boxed with it, then
   grown to the rules drawn just around it (`_snap`) and, for a blank worksheet table the
   detector cut to its header row, along its column rules and a stack of full-width row
   rules (`_extend`, `_stack`; never across running text, a caption, or the gap to
   another ruled table). Then it is kept only with a grid:
   - *ruled*: three or more row rules (or column rules), or two of each; or
   - *aligned text*: text in two or more columns on at least three lines, with at least
     75 % of the cells lining up (left, right or centre) in their column.

   It is dropped as a table of contents (dot leaders, or titles each ending in an
   ascending page number), a list (most cells start with a bullet), diagram labels (no
   cell longer than two letters or digits), a figure (curves or raster images in it,
   unless the text grid covers half its lines), or "no grid". Rules that run through
   words (graph paper) are not evidence. When two kept boxes overlap, the larger stays.
3. **Structure.** Up to three grids compete: the **drawn cell grid** (the lattice of the
   rules, cells merged where a rule is missing; only for a table ruled on all four
   sides), **SLANet-plus** (PaddlePaddle's table-structure model, run as RapidTable does,
   on a 144 dpi crop of the detector's box, or of the extended box) and **PyMuPDF's
   refined grid** (not for an extended box: it stops where the detector did). The one
   that fits the glyphs best wins (`misfit`: text with no wide gap in it that a grid
   splits between cells, or puts side by side in one cell); on a tie, the drawn grid,
   then SLANet-plus (the better recogniser in the benchmark), then PyMuPDF.
4. **Fill.** Each cell's text is the PDF's own words, never OCR: a word goes to the cell
   its box falls in (by the medians of the cells' edges, so one badly placed cell box
   does not matter). A subscript stays in its word, an accent drawn above a letter
   (TeX's vector arrow) goes right after it, and a word hyphenated across lines in a cell
   is joined. Maths in a cell is flattened to its characters (`K = 12mv2` for
   `K = ½mv²`); the table step's model gives LaTeX for maths cells.
5. **Headers.** The top rows are column headers when SLANet-plus or PyMuPDF marks them,
   except when a header cell starts with a number ("Mass | 2 kg" is a label and its
   value) or a header cell would span into the body. Empty cells are never headers.
6. **Data or layout.** A grid of one column, or two columns whose second is prose (cells
   longer than 60 characters on average) and neither numeric, is text set out in boxes
   (a key-terms list). It is not claimed and keeps the paragraph path.
7. **Caption.** A `"Table N ..."` line just above or below the table (within 36 pt, the
   same reach as figure captions; "Table 2 shows that ..." is not a caption) and its
   continuation lines become `Element.caption` and `TableGrid.caption`. A caption is
   never invented.
8. **Continued tables.** A table split across pages stays two tables. The second gets the
   reason `"continued from previous page"` when it has the same columns (left edges within
   3 % of the page width) as the last table of the page before and nothing but a running
   header above it.

What layout does with the result: the table's lines, rules and caption lines leave the
page, and a TABLE element is placed in reading order like a figure. This happens whatever
`FEATURE_TABLES` says: with the flag off the table step keeps the table as an image with
a required decision (`table_not_rebuilt`), which fixes today's misreadings (numeric
tables as equations, blank tables as figures) without rebuilding anything.

### Where it runs, and what it costs

- **Only on probed pages.** `find_tables` renders the page for its layout model, where
  MuPDF's known aborts are. Layout only calls it on pages its probe child has rendered
  ([layout.md](layout.md), "Untrusted PDFs").
- **Threads.** pymupdf.layout builds its onnxruntime sessions with default options, which
  size the thread pools from the host's cores: on a 1-CPU container that ran 5-9 times
  slower. Before the add-on is imported (lazily, on first use), the session class is
  replaced by one that sets `intra_op_num_threads` to the CPUs this process may use
  (`cpu_allowance`: the CPU affinity, capped by a cgroup v2 or v1 quota, so Cloud Run's
  `--cpu` and Docker's `--cpus` count), `inter_op_num_threads` to 1 and turns spinning
  off. Only sessions made from PyMuPDF's own model files are changed. `LAYOUT_SESSIONS`
  records what each session got; a test checks it.
- **Messages.** `find_tables` reports problems with `pymupdf.message` and the add-on uses
  `print`; both can quote the PDF, and stdout goes to the service logs. While it runs,
  PyMuPDF's message stream and this thread's stdout go to a sink that only counts
  writes; the log gets the count.
- **Cost** (this box, Ryzen 7 5800H, pinned to one CPU with `taskset`, 2026-10-09):
  finding the tables (propose, arbitrate, structure, fill) takes 0.33 s per page
  (median; mean 0.38, 95th percentile 0.68, slowest 1.3 s) on the real manuals and
  0.30 s (median; 0.51 s on pages that hold a table) on the benchmark. `layout.analyze`
  of 30 manual pages went from about 1 s to about 10 s. Loading the add-on takes about
  0.6 s once per process. Expect Cloud Run to be slower: measure a staging revision
  before promising times.

### The model file

SLANet-plus is the 7.5 MB ONNX file RapidTable 3.0.2 ships (`slanet-plus.onnx`,
7,758,305 bytes). It is not in the repository.

| | |
|---|---|
| Source | `https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx` (RapidTable 3.0.2's `default_models.yaml`, entry `slanet_plus`) |
| SHA-256 | `d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b` (the same file) |
| Where | `$TABLE_MODEL_DIR/slanet-plus.onnx`; default `~/.cache/a11y-stem/models` (or under `$XDG_CACHE_HOME`); the image sets `TABLE_MODEL_DIR=/app/models` |
| Fetch | the Docker build (`python -m a11y_stem.pipeline.table_model --dir /app/models`); for development `make table-model`; `--from FILE` installs a copy you already have |

A download whose checksum differs is refused and nothing is installed; a file on disk
with the wrong checksum is not loaded. **Without the file nothing breaks**: tables are
still found and claimed, their grid comes from the drawn rules or PyMuPDF, and the log
gets one content-free warning. The offline tests run without it (as CI does); the tests
that need it skip.

The code around the model (resize and normalisation, decoding the structure tokens and
cell boxes) is RapidTable's, vendored with its Apache-2.0 licence and a notice in
`pipeline/vendor/` (`rapid_table.py` lists what changed: Pillow instead of OpenCV, cell
boxes kept in order, bounded span parsing).

### Numbers (2026-10-09)

**Real lab manuals.** Four public instructor manuals (393 pages: Word through Quartz,
Word through Distiller, pdfTeX, dvips; kept local for evaluation, never committed). The
labels are the 2026-10-09 challenge's: every box either the detector or PP-DocLayoutV2
proposed, labelled by eye from overlays; gold is the 87 real text tables found by either,
so recall is an upper bound. A box counts at full extent when the challenge did not mark
it cut short, or when the kept box now covers PP-DocLayoutV2's box for that table (90 %).

| | Boxes kept | False tables | Precision | Full-extent recall |
|---|---|---|---|---|
| Detector alone (before arbitration) | 115 | 37 | 0.68 | 0.72 (63 / 87) |
| With arbitration and structure (`find`) | 76 | 0 | **1.00** | **0.85** (74 / 87) |

The challenge's floors were precision >= 0.95 and full-extent recall >= 0.85. All 37 false
tables were dropped (as a figure 18, diagram labels 7, table of contents 9, no grid 2, list
1); 2 real tables were dropped too (a sparse booktabs table with one filled column, and a
short list of label-value pairs joined by drawn leader lines). Of the 15 tables the detector cut
short, 13 are now at full extent; two stay short because one box holds two tables (or a
table and a plot). The 9 tables the detector never proposed (booktabs-style blank tables
in the dvips manual) stay missed.

**These numbers are optimistic.** The arbitration was developed on these same pages, so
expect lower precision and recall on new documents. Before `FEATURE_TABLES` is turned on,
check it on a private set of 3-5 real course PDFs kept out of git (the challenge's
recommendation); `FEATURE_TABLES` stays off until then.

**Benchmark** (table-bench: 170 tables from OpenStax pages printed in three CSS styles,
grid, booktabs and no rules, plus a LaTeX and a Word page; its own matcher: a prediction
matches a gold table on the same page by word or character F1; TEDS against the gold
HTML, cell text included):

| | Recall | Precision | TEDS (all gold) | TEDS (matched) | Header rows exact |
|---|---|---|---|---|---|
| Detector boxes, PyMuPDF's grid, our words (before) | 0.90 | 0.94 | 0.81 | 0.91 | 0.75 |
| `find` (after) | 0.90 | **0.97** | **0.85** | **0.95** | **0.82** |

By style (after): grid R 1.00 / P 0.98 / TEDS 0.98; booktabs 0.89 / 0.96 / 0.86;
no rules 0.81 / 0.98 / 0.73; LaTeX 0.83 / 1.00 / 0.76; Word 1.00 / 1.00 / 1.00.

How to repeat: the benchmark, the labels and the challenge's scripts are in
`a11y-research` (`pdf-eval-2026-10-09.tgz`, `pdf-eval-challenge-2026-10-09.tgz`); the
scripts and result files behind this section are in
`a11y-research/tables-detect-eval-2026-10-09.tgz` (`evaluate_full.py` for the manuals,
`tb/predict.py` and `tb/score.py` for the benchmark, `time_analyze.py`; run them pinned
with `taskset -c N`). The manuals come from the public URLs in the challenge section of
`PDF-RECOMMENDATION.md`. The tables-eval work turns this into `a11y-stem eval tables`.

### Known misses

- **Tables the detector never proposes** stay on the old path. Seen: a table of numbers
  set without any rules (`tests/fixtures/layout/latex/tables.tex`, Table 3, pinned as a
  strict xfail so an upgrade that finds it shows up), booktabs-style blank tables in a
  dvips manual, label-value pairs joined by leader lines.
- **A sparse table** whose body has text in only one column, between two rules, has no
  grid to see and is dropped.
- **Two tables in one box** (or a table and a plot) stay one table.
- **Maths in cells** is flattened to characters in the local grid.
- **Without the model file**, booktabs tables with spanning headers or maths cells can
  get a column too many (PyMuPDF splits "Specific heat (J/kg K)" or "p = mv").
- **Layout grids** (a key-terms list set as a table) keep today's paragraph path, which
  can still read them as text columns; they are not turned into `<dl>` yet.

### Upgrade check

`make fuzz-tables` (`tests/test_tables_fuzz.py`, marked `slow`): byte mutants of the
repo's own fixtures (MIT, no instructor content) go through `layout.analyze` in a child
process each, as a job would; a child that the probe let in and that then dies or hangs
fails the check. `TABLE_FUZZ_MUTANTS` (default 300) and `TABLE_FUZZ_JOBS` set the size.
Run it, with the table numbers above, the extraction tests and the thread-pin test,
before any bump of `pymupdf`, `pymupdf-layout` or `onnxruntime`. PyMuPDF 2.0 moves the
layout add-on into `pymupdf` itself; see the 2026-10-09 amendment of
[ADR 0003](decisions/0003-pdf-library-agpl.md).

### Privacy

Cell text and captions are document content: they only leave `tables.py` inside the
grid. Logs carry codes and counts (a verdict, the number of PyMuPDF messages dropped, an
exception's class name), never text. MuPDF's own messages are counted and dropped.

### Tests and fixtures

- `tests/test_tables_detect.py`: the thread pin and quiet stdout, the CPU allowance,
  rotated pages, each arbitration verdict on generated pages, extension along rules,
  `fill`, `misfit`, the drawn grid's spans, layout grids, the missing or wrong model
  file, and how layout claims tables (reading order, captions above and below, headers
  that are not headings, continued tables, the probe in front, no text in logs).
- `tests/test_tables_fixtures.py`: `tests/fixtures/layout/latex/tables.tex` (Tectonic;
  rebuild with `make_pdfs.py --tectonic PATH tables`) and the Word-style
  `tests/fixtures/tables/word_tables.html`, exported by LibreOffice Writer
  (`uv run python tests/fixtures/tables/make_word_pdf.py`), plus an end-to-end run where
  the detected tables are kept as images with a required decision.
- `tests/test_tables_model.py`: the checked download, the command, the Docker step, the
  licence files and the vendored pre- and post-processing.
