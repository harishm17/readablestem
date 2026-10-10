# Tables

How a data table in a PDF becomes an accessible HTML table (pipeline 0.4.0). Two parts:

- **Detection** (`pipeline/tables.py`, below): finds tables on every analysed page,
  builds the grid from the PDF's own words and lines (`Element.table`, source "local")
  and claims the rows and the "Table N" caption from the text flow. No model call.
- **The table step** (`pipeline/table_step.py`, further below): a vision model reads the
  table from its crop, deterministic checks compare the answer with the PDF's words, and
  the result is written by `pipeline/table_html.py` (`render_table`, the one serialiser)
  inside `pipeline/assemble_tables.py`.

The review card is in [web.md](web.md) ("The table card") and the evaluation in
[eval.md](eval.md) ("Tables"). Contracts, flags and review tiers:
[design/content-features.md](design/content-features.md). The plan behind it:
`a11y-research/PDF-RECOMMENDATION.md` sections 4.3 to 4.6.

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
   is joined. A glyph smaller than the one before it with its baseline raised by a
   fifth of that glyph's size is a superscript, written in superscript characters
   (`10⁵`, `10⁻¹⁸`, `m/s²`; `^(...)` when a character has no superscript form), so a
   flattened exponent never reads as other digits. Other maths in a cell is flattened
   to its characters (`K = ¹2mv²` for `K = ½mv²`); the table step's model gives LaTeX
   for maths cells.
5. **Headers.** The top rows are column headers when SLANet-plus or PyMuPDF marks them,
   except when a header cell starts with a number ("Mass | 2 kg" is a label and its
   value) or a header cell would span into the body. Empty cells are never headers.
6. **Data or layout** (the local data-vs-layout check). Without a "Table N" caption, a
   **grid of terms** (`is_term_grid`: at least three columns and at most six rows, no
   digit, every term at most four words with a word of three letters or more, no term
   twice, empty cells only at the end, less than a row of them) is claimed but marked
   not data (`TableGrid.is_data` False, reason `"grid of terms"`): layout writes its
   terms as list items in reading order, in the grid's place (`layout._term_items`), so
   a screen reader hears a list, with no table, image or decision, whatever
   `FEATURE_TABLES` says. These are the ruled "Key Terms" boxes OpenStax prints
   (displacement | distance | kinematics | magnitude); left in the text flow their
   lines ran together ("position reference frame scalar vector"), claimed as data they
   were an image and a required decision. A grid of one column, or two columns whose
   second is prose (cells longer than 60 characters on average) and neither numeric, or
   two columns of single words (`is_word_grid`), is not claimed and keeps the paragraph
   path (equations and headings in it are found as usual). Everything else is a data
   table.
7. **Caption.** A `"Table N ..."` line just above or below the table (within 36 pt, the
   same reach as figure captions; "Table 2 shows that ..." is not a caption) and its
   continuation lines become `Element.caption` and `TableGrid.caption`. A caption is
   never invented.
8. **Continued tables.** A table split across pages stays two tables. The second gets the
   reason `"continued from previous page"` when it has the same columns (left edges within
   3 % of the page width) as the last table of the page before and nothing but a running
   header above it. When the page break split a row (Word and browsers break a tall row;
   the gold's orders-of-magnitude table ends "... the edges of the known" on one page
   and "universe" on the next), `join_split_row` joins the end back: the first row after
   any header rows repeated from the table before is the end of that table's last row
   when its first cell is empty where the last row's is not, and each cell with text
   goes on a cell of the last row (it starts in lower case or with a bracket, or the
   cell before ends with a hyphen). Its text is joined to the last row
   (`"last row continues on the next page"`), the row leaves the second table
   (`"first row ends the last row of the page before"`), and a second table left with
   only its repeated header is dropped (its caption goes to the first). The table step
   reads the model's answers the same way (`table_step.with_split_rows`).

What layout does with the result: the table's lines, rules and caption lines leave the
page, and a TABLE element is placed in reading order like a figure (a grid of terms:
its list items). This happens whatever `FEATURE_TABLES` says: with the flag off the
table step keeps a data table as an image with a required decision
(`table_not_rebuilt`), which fixes today's misreadings (numeric tables as equations,
blank tables as figures) without rebuilding anything; meaning would be lost otherwise.

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

What claiming changes: with detection switched off, layout put a display equation inside
48 of these 76 tables, a figure inside 6 and a heading inside 21 (an element whose centre
falls in the table's box); now each is one TABLE element.

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
`tb/predict.py` and `tb/score.py` for the benchmark, `misrouted.py`, `time_analyze.py`; run them pinned
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
- **Maths in cells** is flattened to characters in the local grid (only superscripts are
  kept, as superscript characters).
- **Without the model file**, booktabs tables with spanning headers or maths cells can
  get a column too many (PyMuPDF splits "Specific heat (J/kg K)" or "p = mv").
- **Prose layout grids** (one column, or a term and its definition in two columns) keep
  the paragraph path, which can still read two columns as text columns. A key-terms
  grid printed so that a term wraps onto a row of its own (one print of the gold's
  1-3 page) is not a grid of terms and stays a data table. So does a form set out as two
  columns of a label and a value with digits in it (the authored contact grid:
  Instructor | Dr. Alex Rivera, Office | Science Building 2.104): with `FEATURE_TABLES`
  off it is an image and a required decision; the local check cannot tell it from a
  two-column table of data.
- **A row split by a page break in an unruled table**: the next page's part (one row
  under a repeated header, no rules) is not detected, so `join_split_row` has nothing to
  join: the repeated header, the split row's last line and the next row are read as
  headings and paragraphs (the `norules` print of the gold's 1-3 orders-of-magnitude
  table). Not fixed: tables get no further work while `FEATURE_TABLES` is off.

### Upgrade check

`make fuzz-tables` (`tests/test_tables_fuzz.py`, marked `slow`): byte mutants of the
repo's own fixtures (MIT, no instructor content) go through `layout.analyze` in a child
process each, as a job would; a child that the probe let in and that then dies or hangs
fails the check. `TABLE_FUZZ_MUTANTS` (default 300) and `TABLE_FUZZ_JOBS` set the size. On 2026-10-09 the
300 mutants took a minute on this box, with no escape.
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

## The table step

### When it runs

`pipeline.run` sends every TABLE element to `table_step.rebuild` with its crop, like an
equation or a figure. Rebuilding is behind `FEATURE_TABLES` (`ConversionOptions.tables`,
off by default). Off, the table is kept as an image of the table with a required decision
(FAILED, reason `table_not_rebuilt`) before any model work.

### What the model is asked

The prompt is `models/prompts/table.md` (versioned like the others, `table-<sha8>`). The
answer is strict structured output (`TableOutput`, `extra="forbid"`):

| Field | What it holds |
|---|---|
| `is_data_table` | true for values read against row or column labels; false for text in boxes, a list of terms, a form, a figure, a table of contents, an equation or a matrix |
| `caption` | a caption printed inside the image, else empty (never used: see below) |
| `rows[].cells[]` | each printed cell once, left to right, top to bottom: `text` exactly as printed, `latex` for a maths cell (words in `\text{}`), `header` (`col`, `row` or `none`), `row_span`, `col_span` |

Rows of cells rather than (row, column) numbers: models list an HTML-like table far more
reliably than they count positions, and that is the shape gpt-6-luna scored TEDS 0.97 and
maths-cell F1 0.99 with on table-bench (59 crops, $0.0007 a table).

The model sees the crop, the "Table N" caption and the neighbouring text, fenced as
untrusted data by the client. It is **not** sent the PDF's own words for the table:
those are what its answer is checked against, as the equation step keeps an equation's
text layer from the model. A model that had copied them could pass the checks with a
flattened exponent (10⁵ is "105" in most text layers).

### Routing

| Step | First model | Escalates to `main_model` when |
|---|---|---|
| Table | `cheap_model` (gpt-6-luna), through `router.call_with_fallback` (an outage falls back to the main model) | the answer is unusable, says it is not a data table, or fails any check except the spacing note and `no_pdf_text` (a second reading cannot change those) |

The escalation is one main-model call with no fallback (its fallback would be the cheap
model again). The main model's answer is kept unless it produced nothing, only the cheap
one is usable, or only the cheap one is free of problems (for example the cheap answer
escalated for a cosmetic maths style and the main model then misread a number). Every
attempt is a `ModelCall` on the fix. The output budget is sized from the local grid
(2,048 tokens plus 40 a slot, between 4,096 and 16,384), so a small table does not reserve
a large one's worst case. Spend goes through the client's `JobBudget` reservations;
`BudgetExceeded` propagates to the run.

### From the answer to a grid

`grid_from_output` places the cells as HTML does: each row's cells from the left, skipping
slots a cell above spans into; a span is cut at the last row; slots no cell covers become
empty cells (models often leave empty cells out). Whitespace in a cell is collapsed.
LaTeX is kept only for real maths: `5.2\ \text{m/s}` is plain text, `10^{-11}` is maths,
converted to inline MathML through `validate.check_latex`, `safe_latex_to_mathml` and
`check_mathml`; a cell whose LaTeX fails keeps its text. An answer with no rows, more than
60 columns, more than 300 rows or 5,000 cells is unusable.

### Header policy (`apply_header_policy`)

Decided once (plan section 4.5): OpenStax marks only column headers, while the models also
mark an identifying first column as row headers. Both are valid; the rules are:

- **Column headers:** the top rows whose non-empty cells are all headers, at least one
  of them a column header, are the header rows (`<thead>`, `scope="col"` or
  `"colgroup"`). A "row" flag in a header row (the corner cell) is a column header. A row
  of one full-width header cell counts as a header row only above the column labels (a
  group label over every column); below them it is a section.
- **Row headers** only for a column whose body cells the model marks as row headers
  **and** that are non-empty, not numbers (a cell whose first letter or digit is a
  digit: "2019", "-3.5", "2 kg") and all different (planets, substances, quantities).
  Otherwise they are data cells: gpt-6-luna marked numeric first columns as row headers
  in 14 of 59 bench tables. The leading such columns are the row headers (`header_cols`;
  two when a row-spanning group label such as "Metal" comes before "Aluminium",
  "Copper"). A label column after data cells is the second of two tables printed side
  by side ("Planet | Mass | Planet | Mass"): it is kept, and `table_html` then writes
  headers/id, so 1898 is labelled Jupiter, not Mercury. A column marked so after data
  cells whose cells are not labels would leave the row headers to its left labelling
  its half of each row (`scope="row"` covers the whole row), so those are dropped too,
  with the note `row_headers_dropped`. Partial row flags are dropped.
- **Label/value tables** ("Mass | 2 kg") therefore get row headers and no `<thead>`.
- **Sections:** a header cell across the full width of the body is a section heading
  (`scope="rowgroup"`, its own `<tbody>`).
- **Caption:** the "Table N" caption layout found beside the table (`Element.caption`),
  never the model's: a caption is never invented. No caption, no `<caption>`.

### Checks

All codes are content-free (`Check.name`, `Check.code`); each has a sentence in
`web/messages.py`.

| Check | Code | What it means | Tier after escalation |
|---|---|---|---|
| `table.grid` | `grid_invalid` | cells overlap or reach outside the table (`grid_problems`) | required |
| `table.headers` | `header_problem` | a header below the body, a data cell among the headers, a header spanning into the body, headers covering the table | required |
| `table.headers` | `no_headers` | a data table with no header cell at all | required |
| `table.grid_agreement` | `grid_size_differs`, `spans_differ` | the model and the local grid disagree on rows x columns or merged cells (only with a local grid) | required |
| `table.cell_text` | `text_mismatch` | a word, number or meaningful symbol dropped, added twice, invented or changed | required |
| `table.cell_text` | `row_mismatch` | every word is there, one is in another row than in the PDF | required |
| `table.cell_text` | `cell_mismatch` | every row's words are there, one is in another column of its row (an empty cell left out, two values swapped) | required |
| `table.cell_text` | `whitespace_differs` | each cell equal to the PDF cell in its place once spacing is ignored ("12 345" and "12345", "tempera- ture") | optional note |
| `table.cell_text` | `no_pdf_text` | no PDF words to compare (no local grid with text, no element text) | required |
| `table.math` | `flattened_maths` | a grid read from the PDF (the local-grid fallback, or "Use the grid read from the PDF") has a cell whose maths its text gives flat: an arrow over a symbol, another accent (a combining mark or spacing accent) on a word of at most three letters (an accented word such as an OT1 font's "Mu¨ller" is text), a stacked fraction (a raised digit before a baseline one), a raised run written `^(...)` (`assemble_tables.flattened_cells`). Error severity: no Accept, and `rebuilt_grid` keeps the image until those cells are LaTeX | required |
| `table.math` | `invalid_latex` | a maths cell's LaTeX fails a validator (error severity; the cell shows its text) | required |
| `table.math_symbols` | `symbol_mismatch` | a maths cell's letters, digits or Greek letters differ from the PDF glyphs in its place; unlike an equation, with their case (5 mW is not 5 MW) and, unless the MathML stacks digits (a fraction, a root, scripts above and below), its numbers as written (6.67 is not 66.7, 10^-31 is not 10^-13; a text layer that runs a flattened exponent into its base, "1024" for 10^{24}, still agrees) | required |
| `table.math_signs` | `sign_mismatch` | a sign or relation in a maths cell differs (the equation step's `compare_signs`; an en dash between digits in the PDF cell counts as a minus sign, the raise of 10^-18 lost) | required |
| `table.math_style` | `style_mismatch` / `style_cosmetic` | a meaning-level letter style (a bold vector read as plain) / a cosmetic one | required / optional note |
| `table.source` | `local_grid` | no model answer was usable: the proposal is the local grid | required |
| `table.row_headers` | `row_headers_dropped` | the model marked a column after data cells as row headers that are not labels, so the row headers to its left were dropped too | optional note |

**The cell-text bijection.** The words compared are numbers (with a minus sign glued to
them at the start, or after a space, an opening bracket, a colon, a semicolon, a comma or
one of the symbols below, so "10 × -5", "(3,-5)" and "10^-18" keep their signs while
"1-3" is a range and "C-14" a hyphen; U+2212 is always a minus sign, so "10−18" keeps
it), runs of letters, and symbols that carry meaning in a cell
(`/ × · * % ‰ + ± ∓ = ≠ < > ≤ ≥ ≈ ~ ^ ° √ ∞ ∝`): m/s is not "m s", 5 % is not 5, and
10^5 is not 105. A run of superscript characters is first written as `^` and its plain
form (`10⁻¹⁸` is "10^-18", as the model types it), then the text is NFKC normalised
(ligatures, full-width forms), invisible format characters dropped, dashes folded into
one, pdfTeX's U+2206 into Δ; case is kept (mW is not MW); brackets, quotes, commas and
full stops are not compared. The
non-maths cells of the answer, as a multiset, must equal the PDF's words:

- with a local grid of the same size, **cell by cell**: each answer cell against the
  local cell in its place (row by row when the merged cells differ, which is
  `spans_differ` anyway), leaving out the local cells where the answer has maths (the
  maths checks compare those). A failure whose row totals still match is
  `cell_mismatch` (a value moved to another column: the blank cells the model left out
  of "Male | ___ | ___ | 14 | 55" put 14 under the wrong heading); one whose table totals
  match is `row_mismatch`; one where each cell holds once all spacing is removed is
  `whitespace_differs` (a cell boundary moved, "1.5 | 23" read as "1.52 | 3", is not);
- otherwise (the grids disagree, or only `Element.text` is there) over the **whole
  table**: no word may be added, and the PDF words no text cell accounts for must be
  exactly the maths cells' letters and digits (a maths cell's text layer is flattened,
  so it is compared as a bag of symbols, like an equation).

This catches dropped, duplicated and invented numbers that a substring test cannot (a
wrong "1" when a "1" appears elsewhere in the table).

**Maths cells** are compared with the PDF glyphs of the local cell in the same place
(only when the grids are the same size): `equation_step.text_layer_symbols_differ`,
`signs.compare_signs` when the letters mostly agree, and `text_layer_style`, the same
rules and thresholds as an equation. Unlike an equation, a maths cell where the PDF has
no letters or digits at all is a `symbol_mismatch`: the rest of the table has a text
layer, so maths read into an empty cell was invented.

### Status and review tier

First matching rule wins (content-features.md "Review tiers"):

| # | Condition | Status | `reason` | Tier |
|---|---|---|---|---|
| 1 | rebuilding off | FAILED | `table_not_rebuilt` | required (image kept) |
| 2a | the answer kept says it is not a data table, but the grid read from the PDF is a grid of numbers (`numeric_grid`: half its cells below the first row hold a digit) | NEEDS_REVIEW, the local grid with first-row headers | (check `table.kind` `numbers_not_a_table`) | required |
| 2 | the answer kept says it is not a data table | ACCEPTED, the grid read from the PDF (`is_data` False) written as its text in reading order | (check `table.kind` `written_as_text`) | optional |
| 2b | ... and there is no grid read from the PDF to write (none, no text, flattened maths) | ABSTAINED | `not_data_table` | required (image kept) |
| 3 | no usable answer, a local grid exists | NEEDS_REVIEW, the local grid with first-row headers | (check `local_grid`) | required |
| 3b | no usable answer, no local grid | FAILED | `model_<outcome>` or `invalid_table` | required (image kept) |
| 4 | every check passed | ACCEPTED | | none |
| 5 | only notes failed (`whitespace_differs`, `style_cosmetic`, `row_headers_dropped`, `written_as_text`) | ACCEPTED | | optional |
| 6 | otherwise | NEEDS_REVIEW, the model's grid | | required |

The model gives no confidence: the checks decide, so `Fix.confidence` is None. A table
waiting for review is written as rebuilt (like an equation waiting for review);
rejecting it keeps the image. The review screen shows the proposal beside the crop and
the local grid (`Element.table`), with the failing checks named (tables-review work).

### Output

A grid that is not a table of data (rule 2) is written as its cells' text in reading
order (`assemble_tables.text_html`), keeping what goes together: two columns whose every
row is a short term and its text as a description list (`<dl>`), cells that are all
short terms as a list, anything else one paragraph per cell; its "Table N" caption comes
first as a paragraph, and nothing is flagged.

`assemble_tables.render_table_element` writes a usable proposal with `render_table`
inside `<div class="table-scroll">`, which scrolls sideways on a narrow screen (WCAG
1.4.10: the table scrolls, not the page). Only a table that may be wider than a 320 CSS px
screen is a tab stop, named by its caption (else "Table") with `role="group"`: a
keyboard user can only scroll a box that takes focus, and a tab stop on every small
table would make a long document tedious. The width estimate (`estimate_min_width_em`:
per column the longest word at 0.6 em a character, or a maths cell's estimated width,
plus 1.4 em of padding and borders a cell; tab stop from 13 em of the 16 em available)
errs wide: measured in Chromium on the 59 bench tables, none overflowed without being a
tab stop (47 overflow; 7 more are tab stops without needing it). A proposal without a
caption gets the element's "Table N" caption, which layout took out of the text, so it
is never lost. Table CSS is added only to documents with a rebuilt table.

`html_checks.static_checks` adds five rules to documents that contain a `<table>` (none
otherwise): `html.table_headers` (a data table has a `<th>`), `html.table_header_scope`
(every `<th>` has `scope`, or an id in a table that uses `headers`),
`html.table_header_text` (no empty `<th>`), `html.table_caption` (a warning: a "Table N
..." paragraph right beside a table without `<caption>`, by layout's caption pattern, so
"Table 2 shows that ..." is not one) and `html.table_layout` (no `role="presentation"`
table: the output never uses tables for layout). axe-core covers `th-has-data-cells`,
`td-headers-attr` and `scope-attr-valid`; the browser suite runs it on tables of every
shape the step writes.

### Smoke test (2026-10-09, real models)

Ten crops: the six authored LaTeX tables of table-bench's `tables.tex` (written for that
benchmark, no third-party text) and four OpenStax CC BY 4.0 tables printed by the bench,
each with a local grid made by filling the gold structure with the PDF's words by
position (a stand-in for tables-detect). Spend: $0.045 in all.

- Eight of ten were ACCEPTED with nothing to note on gpt-6-luna alone ($0.0003 to
  $0.0006 each): booktabs with siunitx units, a fully ruled table, an unruled table of
  signed numbers, maths in cells (`\bar{v} = \frac{\Delta x}{\Delta t}`,
  `\vec{p} = m\vec{v}`, `W = \int \vec{F} \cdot d\vec{r}`), planets with row headers,
  and three OpenStax tables. The header rows matched the printed tables, and the first
  columns of names (planets, quantities) became row headers, as the policy allows.
- Two went to review after escalating to gpt-6.1-sol ($0.012 and $0.030): the
  multirow/multicolumn table and the 16-row particle table, where the stand-in local grid
  was wrong (both answers themselves were right), so grid agreement and the bijection
  asked for a decision.
- Five of the cases are offline fixtures (`tests/fixtures/tables/`, the LaTeX ones). With
  one letter or digit of each accepted answer changed, every table goes to review.

The smoke test can be run again (opt-in, `live_model`, each test capped at $0.10):

```
uv run --env-file .env pytest -m live_model tests/test_tables_live.py -s
```

`test_live_drawn_table` draws a small ruled table with PyMuPDF (signed values, a squared
unit) and fills its local grid from the page's words by position; on 2026-10-09 it was
ACCEPTED on gpt-6-luna alone for $0.0005. `test_live_latex_fixture_tables` rebuilds every
TABLE element layout finds in `tests/fixtures/layout/latex` with its real local grid.

After integration (2026-10-09, branch `feat/content-integration`, detection and the step
together): `test_live_latex_fixture_tables` rebuilt all 5 tables layout finds in
`tables.pdf` (Table 3, the unruled all-number table, is the known detection miss) and
every one was ACCEPTED on gpt-6-luna alone with every check passed ($0.0003 to $0.0005
each; the drawn table again $0.0004). `a11y-stem convert` of the same PDF with every
flag on gave the same 5 accepted tables for $0.0194 in all (13 equations included). The
tables evaluation in model mode is in [eval.md](eval.md) ("Tables").

### Known limits

- A maths cell is compared as a bag of letters (with their case) and of numbers: two
  whole numbers swapped inside one maths cell are missed, and so are digits in a
  fraction or a root (only their bag is compared, as for an equation). Plain numbers
  are text cells (LaTeX without commands or scripts is treated as text), so the word
  check covers them.
- The checks are only as good as the local grid: a grid that splits a wrapped cell into
  two rows, or misses a merged cell, sends a correct answer to review.
- A text layer that differs from the print (small caps given as lower case, a ligature
  NFKC does not fold) makes a correct answer a `text_mismatch`.
- An em dash for "no value" folds into the dash that is not compared: an answer that
  leaves that cell empty passes.
- No speech text for maths cells (the review screen reads the MathML).

### Tests

| File | What it covers |
|---|---|
| `tests/test_steps_table.py` | every route and tier with recorded JSON answers (`FakeVisionClient`), the bijection (misread, dropped, duplicated, invented, moved, signs, symbols, case, spacing), grid agreement, maths cells (MathML, symbols, signs, style, invalid LaTeX), the header policy, placement, the local-grid fallback, budget, the request (no table words, fenced context), model HTML and script in cells escaped, the recorded real answers and a misread in each, a conversion with the flag on, axe on every shape (`browser`) |
| `tests/test_assemble_tables.py` | the scroll box and tab stop, the width estimate, the caption kept, the HTML table rules, axe and 320 px reflow in Chromium (`browser`) |
| `tests/test_table_html.py` | the serialiser (contracts work) |
| `tests/test_tables_live.py` | the smoke test with the real models (`live_model`, opt-in; above) |
