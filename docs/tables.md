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
[eval.md](eval.md) ("Tables"). Flags and review tiers are in [web.md](web.md). The plan behind it is in the
author's private research notes, which are not in this repository.

## Detection (`pipeline/tables.py`)

Layout calls `tables.find(page, lines, paths)` for every analysed page, before columns,
rows, headings and equations are worked out ([layout.md](layout.md), "How a page is
read", step 0). No model call; the same PDF always gives the same tables.

### Which pages are looked at

Layout runs the detector below only on a page that could hold a table
(`tables.may_hold_table`), because the detector is most of a page's analysis time (85-90%
of `layout.analyze` on OpenStax sections). `judge` keeps a box only with a grid (step 2),
so a page is left out when it has no rule pieces enough for a ruled table (three row or
three column rules, or two of each, counted per position, every drawn rule, stroked
rectangle and thin image included), nothing for aligned text (no x position at which three
visual lines have a gap wider than half the font size, or a rule between two glyphs), and
no ruled grid with a line of text in two columns. The thresholds are looser than the
arbitration's (a gap counts from 0.5 of the font size, not 0.8), so a glyph the box leaves
out cannot decide it; the test only ever over-passes. The result is the same either way:
`tests/test_tables_prefilter.py` analyses every table fixture with and without it and
compares the elements and notices (the rest of the repository's PDFs in a slow test), and a
check over 553 PDFs (the repository's fixtures and the author's research PDFs, the first
eight pages of each, 725 pages) found the detector's tables on 82 pages, none of them among
the 289 (40%) the test skipped. How many pages it skips depends on the document: it saves
little on pages with boxed callouts (OpenStax Physics 2.2, 14 pages: layout 3.9 s, of
which 3.2 s detection, against 2.8 s; 2.3, 13 pages: 4.3 s against 2.4 s) and about 40% of
the layout time on lecture notes and problem sets (`scripts/bench_runtime.py convert`:
7.1 s of CPU in layout, 4.0 s after; two-column text and runs of numbered equations pass
the test). `layout.TABLE_PREFILTER`
turns it off (for that comparison).

Three more rules keep a page of pen strokes from costing minutes. They change what the
detector would have found only on such pages (in the fixtures, the development set, the
table sets and the 332 research PDFs of handwriting, no output changed; the largest page
with a table in all of them has 375 paths and 375 items, and a page with 26,500 items is
the largest that got through the detector at all):

- a page kept as its image beside its text (`text_kept`, [layout.md](layout.md)
  "Page cap") is not given to it;
- a page with more than `tables.MAX_DETECTOR_ITEMS` (30,000) path items has no table
  proposed, and its lines are read as text;
- a document has a time budget for slow detections (`tables.DetectorBudget`): a detection
  longer than `DETECTOR_FREE_S` (3 s; a page with a table takes under 1 s) draws the time
  beyond that from `DETECTOR_EXCESS_S` (60 s) for the whole document, and when it is used
  up the document's remaining pages get no proposals (one content-free warning). A
  document within the budget reads exactly as it would without one; one over it depends
  on the machine's speed from there on, as the ink analysis does (`INK_BUDGET_S`).

The detector's time depends on what is drawn on the page, not on its tables, and is
nearly all inside PyMuPDF: a lecture slide with 202 pen-stroke paths (11,000 curve items)
took 14 s in the layout model's text extraction (`TEXT_COLLECT_VECTORS`, `TEXT_TABLE_HUNT`)
and 1 s in the rest; one with 35,000 items 47 s (four `get_drawings` calls inside
`find_tables` cost 4 s each); a page of 800 stroked line paths 9 s; a typed page with
5,000 items of pen strokes 0.4 s. That lecture PDF (12 pages) took 260 s, 94 s with the
rules above.

### How a page's tables are found

1. **Propose.** PyMuPDF Layout (`pymupdf-layout`, ONNX layout models in the wheel) with
   `page.find_tables(union=True, refine=True)`, under layout's PyMuPDF lock. It returns
   boxes in display coordinates (on a page with `/Rotate` too) and PyMuPDF's own refined
   grid for each box. Boxes made from the page's own lines and rules (`own_proposals`) go
   through the same arbitration: *rule frames* (the span between two rules of one extent, a
   booktabs table's top, header and bottom rules, or the double rules round a table of text) and
   *text blocks* (runs of lines with text in aligned cells: an unruled list of symbols and their
   meanings, a table set without rules). They exist for what the layout model does not propose:
   unruled lists, fragments of a table ruled only above and below whose cells are justified
   paragraphs, and every page with more than `MAX_DETECTOR_ITEMS` drawn items, where the model
   is not run. A page whose text reads sideways (a landscape table on a portrait page with no
   `/Rotate`: `_page_turn`) is analysed turned upright and the results turned back
   (`TableGrid.turned` says how far the table's picture must be turned to read it; `layout.crop`
   does). See "Recall on real documents" at the end of this page for why each exists.
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
   words (graph paper) are not evidence. When two kept boxes overlap, the larger stays; the
   detector's boxes first, so a box made here never replaces one (but a box made here that
   holds a smaller table of its own is a callout's outline and yields to it, and the
   detector's box that holds two text blocks of its own is two lists, `_split_stacked`).
   Added by the real-document work: a ruled box with a "Table N" caption just above or
   below it (Roman numerals too: "TABLE II.") is a table whatever its columns look like, and
   a ruled two-column box of prose under a caption is data (`is_layout_grid` is decided after
   the captions are attached); columns of justified text, whose padding is narrower than a
   cell gap, come from the strips no word crosses (`_word_gutters`); a numbered equation or
   reaction at the top or bottom of a box is cut off it (`_without_captions`); and a box made
   here is dropped as a chart (curves, scatter dots, bars, small lettering), page text (two
   prose columns, or one gutter in the middle of the page), references, numbered or bulleted
   lines, algorithm lines, equations (lines of relations, equation numbers), authors and
   addresses, a block of bare numbers (a matrix) or running text in a box (`_text_block_dropped`).
3. **Structure.** Up to three grids compete: the **drawn cell grid** (the lattice of the
   rules, cells merged where a rule is missing; only for a table ruled on all four
   sides), **SLANet-plus** (PaddlePaddle's table-structure model, run as RapidTable does,
   on a 144 dpi crop of the detector's box, or of the extended box) and **PyMuPDF's
   refined grid** (not for an extended box: it stops where the detector did). The one
   that fits the glyphs best wins (`misfit`: text with no wide gap in it that a grid
   splits between cells, or puts side by side in one cell); on a tie, the drawn grid,
   then SLANet-plus (the better recogniser in the benchmark), then PyMuPDF. A fourth, the
   **text cell grid** (`_text_slots`: columns between the gutters, a row for each line with text
   in the first column, the wrapped end of a cell stays in its row, header rows above the first
   rule between rows), is the structure of a box found from the text, where on a tie it beats a
   model's reading (SLANet-plus joined neighbouring rows of a list), and of last resort for any
   box that no other structure fits, so a table the model file is missing for is no longer
   dropped when its box was grown past the detector's.
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
  (`cpu_allowance`: the CPU affinity, capped by a cgroup v2 or v1 quota, so a container's CPU limit,
  Docker's `--cpus` for one, counts), `inter_op_num_threads` to 1 and turns spinning
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
  0.6 s once per process. Expect a small hosted container to be slower: measure on the host
  before promising times.

### The model file

SLANet-plus is the 7.5 MB ONNX file RapidTable 3.0.2 ships (`slanet-plus.onnx`,
7,758,305 bytes). It is not in the repository.

| | |
|---|---|
| Source | `https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx` (RapidTable 3.0.2's `default_models.yaml`, entry `slanet_plus`) |
| SHA-256 | `d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b` (the same file) |
| Where | `$TABLE_MODEL_DIR/slanet-plus.onnx`; default `~/.cache/readablestem/models` (or under `$XDG_CACHE_HOME`); the image sets `TABLE_MODEL_DIR=/app/models` |
| Fetch | the Docker build (`python -m readablestem.pipeline.table_model --dir /app/models`); for development `make table-model`; `--from FILE` installs a copy you already have |

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
recommendation); `FEATURE_TABLES` stays off until then. The 2026-10-21 measurement of what turning
it on does, and what still blocks it, is at the end of this page ("Tables on, on the development
sets").

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

How to repeat: not from this repository. The benchmark, the labels and the scripts behind
these numbers live in the author's private research folder, and the manuals are
third-party documents that are not redistributed here, so the numbers measured on the
manuals cannot be reproduced from a checkout. What can be reproduced is the same task on
the OpenStax and authored table documents: `readablestem eval pipeline --task tables --mode
local` (docs/eval.md, "Tables").

### Known misses

- **Tables the detector never proposes** are now proposed from the page's own lines and rules
  when they are lists or tables set in aligned columns or between rules ("Recall on real
  documents"). Still missed: lists of relations (`X_n ~ f(n)  if ... -> 1`, 2 of 25 on the real-document
  development set), an unruled table of bare numbers with no caption (a matrix looks the same), and
  label-value pairs joined by leader lines.
- **A sparse table** whose body has text in only one column, between two rules, has no
  grid to see and is dropped.
- **Two tables in one box** (or a table and a plot) stay one table.
- **Maths in cells** is flattened to characters in the local grid (only superscripts are
  kept, as superscript characters). With `FEATURE_TABLES` off the table is a picture and the
  formulas in its cells are lost (102 of the 335 gold equations of the dev chemistry sections);
  with it on the table step reads them (59% found there), see the end of this page.
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
- `tests/test_tables_recall.py`: the real-document misses one by one (pages of `data/real-tables`),
  unruled lists and what must stay text (page columns, numbered equations, references, algorithm
  lines, matrices), a table set sideways, Roman-numeral captions, a caption over a ruled box, a
  numbered reaction above a table.

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
| `table.edge_text` | `text_at_edge` | layout left a line of the table's own text size right under its box, starting where a column starts (`REASON_EDGE_TEXT`, `tables._edge_text`): probably the rest of a wrapped cell the box ended a line early. The grid and the crop both lack it, so no second reading can differ; never escalated | required |
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

First matching rule wins:

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
each; the drawn table again $0.0004). `readablestem convert` of the same PDF with every
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


## Review: clean tables, a grid that is not a table, a maths cell read (T3-11)

Three rough edges of the table card, built in `web/review.py` and `routes/edits.py` (the card
itself is described in docs/web.md "The table card" and "Rough edges"):

- **Accept all clean tables.** A table that waits although nothing was found wrong with it
  (a data table with a proposal, no failed check of any kind, no structural problem, no maths the
  PDF gives flat; `review.is_clean_table`) is accepted with every other such table in one write.
  A table with a finding, kept as an image or written as text is never swept in.
- **This grid is not a table.** The reviewer's version of the table step's `written_as_text`: the
  grid read from the PDF's own words (else the proposal's) is written as text, one cell after
  another (`assemble_tables.text_html`). Refused for a grid with no text or flattened maths.
- **A maths cell read by the model.** The equation step reads the cell's crop (its box, or the
  box of the cell of the grid read from the PDF in its place) and the answer goes through the
  cell's edit checks (`web.edit_check.check_cell_edit`, against the PDF's text in that cell). It
  is a model call from the review, so it is spend capped like the conversion's (docs/web.md,
  "What the model costs here").


## Tables on, on the development sets (2026-10-21): measured, and the decision

Production runs with `FEATURE_TABLES` off: a detected data table is kept as a picture of the
table with a required decision (`table_not_rebuilt`), and the formulas in its cells are lost
(2.7% of the registered held-out gold, 102 of 335 equations of the development chemistry
sections: the "A2" bucket of the 2026-10-20 analysis). This is what the flag does on the
development sets, measured for this decision. Nothing on the held-out set was run.

**Decision (2026-10-21, the owner's): `FEATURE_TABLES=true` in production**, with the settings in
"The decision" below. The code default stays off. The blockers this section first listed were
closed or measured: the gate floor (`wrong cells not flagged` 2 to 0), the real-document check
(a public labelled set, "Real documents": precision 1.00, full-extent recall **0.56 against a
floor of 0.85: missed**), the spend caps (the figures first given here were wrong; "The
decision"). On every set measured it is a net gain for a reader and changes nothing else:
equations 0 to 65 of 223 table equations on real papers (0 to 60 of 102 in the chemistry
sections), required decisions per page down, headings, figures and equation scores identical, for
+$0.005 to +$0.012 a PDF page. What the missed recall means for a reader: a table the detector
does not find is read as it is with the flag off (paragraph text or a picture), so the flag
cannot make such a table worse; the tables it does find, it rebuilds.

### The tables task, model against no model

`eval pipeline --task tables`, the 45 documents of the table gold (OpenStax *Physics* and
*Statistics* printed in three styles, plus six authored documents; 197 data tables, 675 PDF
pages). Model mode: claude-haiku-5-5, escalating to claude-opus-5-5, recorded once ($2.26 at list
price). Local mode is the same run with no model, which is what production does with the flag
off (the table stays a picture, so the grid is only scored). The detection is the same code in
both.

| | no model (flag off) | model (flag on) |
|---|---|---|
| tables found: recall / precision | 0.909 / 0.984 | 0.909 / 0.984 |
| TEDS (all gold tables; found tables only) | 0.866 (0.954) | **0.882 (0.970)** |
| TEDS of the structure alone, found tables | 0.971 | 0.990 |
| column headers exact | 0.843 | **0.858** (0.792 before the header-text fix, below) |
| header texts exact (all headers, rows too) | 0.812 | 0.442 |
| cells right (of 5,616 gold cells) | 98.3% | **99.4%** |
| wrong cells | 188 | 112 |
| formula cells (706): letters and digits right / MathML right | 0.824 / 0.000 | 0.826 / **0.581** |
| tables a person must decide on | 184 of 184 (kept as images) | **55 of 183** (0.30 a table, 0.081 a PDF page) |
| equation-style flags: precision / recall (found tables wrong or not) | n/a | 0.44 / 0.68 |
| tables wrong and nobody asked | 0 (all asked) | 11 of 179 |
| cost | $0 | **$0.0123 a table** ($0.0034 a PDF page of these table-dense documents) |

By style (TEDS): grid 0.969, booktabs 0.858, no rules 0.779, authored 0.940; the lower rows are
detection (recall 0.889 and 0.815), not the model: TEDS of the found tables is 0.97, 0.97 and
0.96 on the three. Headers: the model proposes row headers on an identifying first column, valid
markup the OpenStax gold does not have (77 tables), which is most of the fall in the exactness of
all header texts. Column headers first read 0.792 against 0.843: of the 18 tables that lost it
(8 gained it), 15 were one header cell of a formula whose *text* the model gives with the marks of
its LaTeX (`d_i`, `v_w (m/s)`, `10^-18`) where the PDF's text, and so the gold's, has the letters
(`di`, `vw (m/s)`); the comparison now leaves `_ ^ { }` out (`eval/tables.header_text`, the
spelling rule body cells already had) and the same recorded answers score **0.858**, above the
no-model 0.843. The other 3 are a title row the gold writes as a header (`colgroup`) and the
model keeps as the caption: a convention. Nothing in the output changed, only what was compared.

The gate (`eval gate --current ...`, a replay of the recorded answers, $0): every floor passes
(recall 0.909 >= 0.86, precision 0.984 >= 0.93, model TEDS 0.8815 >= 0.88, column headers 0.858 >=
0.75, `table_wrong_cells_not_flagged` 0 <= 0); the real-document floors are not part of the
committed reports (below). The first run of this section had 2 wrong cells nobody was asked
about, and the cause given then (a wrapped cell's last line lost by the grid and the model
alike) was wrong for both: they were a table title, the full-width first row of the gold's
`colgroup`, that layout keeps as a heading above the box and the model keeps as the caption. Its
words are in the output, only not in a cell, so the scorer no longer counts a gold title cell the
output has as text just outside the table (`eval/tables.cell_fidelity`, `kept_text`: 10 of the
5,616 gold cells; the output is unchanged). The 11 tables that are wrong and not asked about have
become 7 (24 wrong, 17 asked), and all 7 differ from the gold in the header convention or a
structure detail with no wrong cell. The share of tables a person must decide on is unchanged:
**55 of 183 (0.301 a table, 0.081 a PDF page)**.

The wrapped-cell failure is real, though, and the 2026-10-09 run did see it, so layout now checks
for it: a line of the table's own size right under its box, starting where one of its columns
starts and not running on across them (`tables._edge_text`, `REASON_EDGE_TEXT`), makes the table
step ask a person (`table.edge_text`, `text_at_edge`, no escalation: the grid and the crop both
lack the text, so no second reading can agree or differ). It is tested on synthetic pages only
(`tests/test_tables_edge.py`: wrapped and short lines flagged; paragraph, heading, caption,
title, a note off the columns and a paragraph that follows the table closely not): on the 183
development tables it fires 0 times, so it has 0 true and 0 false positives there, and nothing in
the numbers above comes from it.

### The document task, five chemistry sections

Five dev-v2 sections with tables (1.4, 12.3, 16.3, 17.3, 2.3: 14 data tables, 43 PDF pages), the
same recorded equation and figure answers, tables off then on (`FEATURE_TABLES=true`, 36 table
calls, $0.51). The new document-task metric (`eval/table_maths.py`, `table_cell_maths` in the
report) counts the `<math>` elements of the gold's data tables and pairs them one to one, by the
symbols scorer, with the `<math>` elements in the output's table cells.

| 5 sections, 43 pages | tables off | tables on |
|---|---|---|
| formulas in the gold's table cells found in a cell (102 gold) | **0** | **60 (59%)**; 19 also exact on the primary scorer |
| by section (gold) | | 16.3: 37 of 37; 2.3: 19 of 27; 17.3: 4 of 26; 1.4: 0 of 11; 12.3: 0 of 1 |
| equations: found, matched, primary score | 94 matched, 0.108 | the same, equation for equation |
| headings, figures (every page) | | identical |
| required decisions per PDF page (of which tables) | 1.395 (21) | **1.186 (12)** |
| cost per PDF page | $0.0137 | $0.0256 (+$0.0119) |

The same sections and eight more with tables (the layout tables of two algebra books, the
authored lecture, glossary and structure documents; 13 documents, 169 pages) were converted with
the fake client with the flag on and off: the elements, headings, figures and review reasons are
identical, apart from the table fixes themselves. Layout does not read the flag (docs/layout.md,
step 0), so nothing outside a table can change.

What the dev numbers do not show: the sections are table-dense chemistry, so +$0.0119 a page is
an upper end (a page with no table costs the same either way); 17.3's and 1.4's formula cells
are mostly units and scientific-notation powers the table step writes as text; and a model
outage with the flag on writes the local grid with no header cells (the fake client does it:
`html.table_no_headers`), a decision for a person, not a table a reader can use.

### On real papers (2026-10-21): the real-world dev set, flag on

The real-world arXiv dev split (25 papers, 114 PDF pages, 2,350 gold equations, of which 223 are
typed in a table; docs/eval-realworld.md on branch `feat/realworld-eval`) converted with
`FEATURE_TABLES=true` and the equation answers of its tables-off run replayed (one new recording,
$0.81 at list price, of which about $0.23 re-asked equations whose context a layout change had
altered; the cap was $4). The comparison is the same code and the same answers with the flag off,
replayed free. The published tables-off result of that branch is 0.562 primary; this branch scores
0.550 on the same answers because its converter changed (docs/chemistry.md, "Side effect"): the
gold of that set was made with the old converter. `on` and `off` below are this branch.

| 25 papers, 114 pages | flag off | flag on |
|---|---|---|
| primary `exact_lenient` | 0.5498 | 0.5498 |
| symbols scorer, credited | 0.5872 | 0.5872 |
| detection recall / precision | 0.737 / 0.861 | 0.737 / 0.861 |
| gold equations typed in a table (223) found in a table cell | 0 | **65 (29%)**; 61 exact on the symbols scorer, 49 on the primary |
| `<math>` elements written in table cells | 0 | 102 |
| tables the pipeline claimed in the 25 papers | 17 | 17 |
| required decisions per PDF page (of which tables) | 4.50 (17) | **4.40 (6)**; 1 more optional |
| cost per PDF page | $0.0237 | $0.0286 (+$0.0049: 17 tables, $0.033 each) |
| dearest paper | | $0.38 for 5 pages ($0.077 a page) |

Nothing else moves: the equations, their scores and their flags are identical, the headings and
figures are the same elements. Tables of these papers are bigger and dearer to read than the
OpenStax ones ($0.033 against $0.012). The limit is detection, not the table step: 17 tables are
claimed, and the other 158 of the 223 table equations were not found (not analysed table by
table: a table the detector did not propose, or a cell the model wrote as text); no table of these
papers was labelled, so how many real tables 17 is cannot be said.

### The blockers (2026-10-21), and what closed them

1. **The real-document check: measured, floor missed.** The first line of this page's rule was that
   `FEATURE_TABLES` stays off until 3 to 5 real course documents are labelled and measured. The
   private lab manuals of the research cannot be committed, so `data/real-tables/` holds five
   openly licensed ones (CC BY 4.0 arXiv lecture notes, a textbook, a physics-lab study, two
   tutorials), 64 pages, 25 labelled tables, and the floors were written down before detection was
   run on any of them (its README; commit f545c73). No model: **precision 1.000 (14 of 14 predicted
   data tables are real; Wilson 95% 0.78 to 1.00), full-extent recall 0.560 (14 of 25; 0.37 to
   0.73) against floors of 0.95 and 0.85**. Every table found was found at its full extent.
   The 11 missed: 6 are the notation lists of the lecture notes (two columns of formulas and
   their meanings, no rules: whether a reader calls those tables is arguable, they are `tabular`
   in the source; without them 14 of 19, 0.74, still under the floor), 1 a list ruled only above
   and below (the textbook's acronyms), 1 a table on a rotated page, 1 a ruled two-column table of
   long text that `judge` took for layout (`layout_grid`: a term-and-description grid), and 2 ruled
   tables of long wrapped text with shaded quotes that came out as fragments. Nothing was tuned on
   this set: after reading these numbers it is a development set, not a held-out one, and the
   floors stay unmet (second measurement, after the detector was changed on it: precision 1.000,
   recall 0.920 here, and a fresh held-out set that misses both floors: "Recall on real
   documents", at the end of this page). Per-table outcome counts: `data/real-tables/result-2026-10-21.json`;
   repeat: `readablestem eval pipeline --task tables --mode local --real-dir data/real-tables`
   (free, about four minutes).
2. **The gate floor: closed.** `wrong cells not flagged` 2 to 0, as in "The tables task" above: a
   scorer rule for a title kept outside the box, plus the edge check, which fires nowhere on the
   development sets.
3. **Spend caps: the figures first written here were wrong.** With the flag on, the worst-case
   reservation of a table call is $0.53 and `ops launch-check` (`ops/limits.py`) refuses a
   `MAX_USD_PER_PAGE` under $0.53 and warns about a `MAX_USD_PER_JOB` under **$4.66** (a 30-page
   document's $0.45 plus eight calls held at once, $4.21), not $2.60: run on 2026-10-21 with the
   job cap at $4.65 it reports one blocker, at $4.66 none. The measured cost is $0.0123 a table,
   about 40 times less than the reservation; the caps only have to hold the reservations.
4. **`\ce` in a table cell: closed without a prompt change.** The table prompt does not teach
   mhchem, so a model rarely writes it; if one does, `table_step._cell` now writes it out as
   plain LaTeX with `equation_step.expand_ce` before the checks, as the equation step does, and a
   `\ce` outside the converted subset still fails the LaTeX check and goes to a person
   (`tests/test_steps_table.py`). Nothing recorded changes (the prompt hash is the same,
   `table-b3769ce7`; the recorded answers replay as before).
5. Smaller, open: the row-header convention (77 tables); a model outage leaves a header-less
   local grid. Detection of unruled lists, rotated pages and long wrapped text (item 1) was worked
   on in "Recall on real documents".

### The decision

`FEATURE_TABLES=true` in the production deployment (`deploy/deploy.env.example`, which `deploy.sh`
reads; `Settings.feature_tables` stays `False` by default, so a checkout, the tests and every
other deployment behave as before), with, for `MODEL_PROVIDER=anthropic`:

```
FEATURE_TABLES=true
MAX_USD_PER_PAGE=2.20
MAX_USD_PER_JOB=4.70
```

`ops launch-check` with these and the daily cap of $10 and quota of 90 pages: `launch_blockers=0`.
The minimum it accepts is $0.53 and $4.66; the page cap is $2.20 so that a one-page job holds four
calls at once (at $0.53 its table and equation steps would run one after another), the job cap
$4.70 is the minimum rounded up. Nothing else needs to move; the tests of the step with the flag
on (`tests/test_steps_table.py`, "a conversion with the flag on") already cover it. What to look
at in the first week: the share of tables that need a decision (0.30 expected), `table_not_rebuilt`
gone, `local_grid` (a model outage), the review screen's table card, and how many tables of
lists or wrapped text readers report as missed.

How to repeat (needs a key; $2.3 for the tables task, $0.5 for the sections, at list price):

```
uv run readablestem eval pipeline --task tables --mode local --out tables-local.json
uv run readablestem eval pipeline --task tables --mode model --provider anthropic --max-usd 3 \
  --concurrency 2 --answers tables.jsonl --out tables-model.json
uv run readablestem eval gate --current tables-model.json
FEATURE_TABLES=true uv run readablestem eval pipeline --task document --set dev-v2 --provider anthropic \
  --max-usd 1.2 --answers A.jsonl --sections 1-4-measurements 12-3-the-second 16-3-standard 17-3-rate 2-3-atomic
```

Spend of this measurement (list price, from the answer files; cap $4): the tables task in model
mode $2.26 (249 calls for 183 tables), the table calls of the five document sections $0.51 (36
calls); the real-world dev run $0.81 (2026-10-21, second measurement, above); total **$3.58** of the $4 and
$4 caps. The equation and figure answers those sections replay are the chemistry
work's (docs/chemistry.md, "Spend").

## Recall on real documents (2026-10-21, branch `fix/table-recall`)

The first real-document measurement ("The blockers", item 1 above) found precision 1.000 and
full-extent recall 0.560 (14 of 25) on `data/real-tables`, against a floor of 0.85. This is where each
of the 11 misses was dropped, the root causes behind them, what was changed, and what it did. No
model is called anywhere in it: detection is free, nothing was spent, and the spend caps
(`FEATURE_TABLES=true`, `MAX_USD_PER_PAGE=2.20`, `MAX_USD_PER_JOB=4.70`) still pass `ops launch-check`
(`launch_blockers=0`); the only cost effect is that more tables are found and rebuilt (about
$0.012 to $0.033 each, "The tables task" and "On real papers" above). Time: `layout.analyze` on the
64 pages of `data/real-tables` took 0.49 s a page before and 0.52 s after, on the 58 held-out pages
1.05 s and 1.08 s (one CPU, this box): the page-level geometry costs 2 to 5%.

### Where each miss was dropped

Traced with `judge`'s verdicts on the proposals the detector made for each page (the stage that
dropped it, and why):

| Table | Stage | Cause |
|---|---|---|
| acronym list (2401.00649, page 1) | not proposed | unruled two-column list: the layout model looks for ruled, shaded or header-marked grids. (The earlier note "ruled above and below" was wrong: the page has no rules by the list) |
| notation lists 1 to 4 (2412.19752, page 1) | not proposed | the same |
| notation lists 5 and 6 (page 2) | arbitrated: one box for both | the detector boxed the two lists as one table of 7 columns; their columns are not shared, so no common gutter, verdict "no grid" |
| "Table 2: Elements of an object" (2505.03022, page 17) | arbitrated: layout grid | `is_layout_grid` (a ruled two-column box of prose) was decided in `_build`, before the caption "Table 2:" was attached, so the caption never counted |
| lab study Table 4 (2409.08224, page 4) | proposed in fragments | ruled only above and below (double rules and a header rule), cells of justified text: the detector proposed two fragments (a 12-cell sliver and a 2-cell one), neither a table |
| lab study Table 5 (page 6) | arbitrated: no grid | the whole table was proposed (135 cells), but the glyph gaps between its justified columns (3 to 7 pt) are narrower than the 0.8 em that separates cells, so every line read as one cell, one column |
| table of balls (2505.03022, page 24) | page never looked at | text set sideways on an upright page (no `/Rotate`): every line is "vertical", `glyphs_in` leaves vertical lines out and the prefilter saw no text |

The same trace on the real-world dev papers found one more: 2410.12036 page 0 has a ruled table and
a figure of 35,000 drawn items, over `MAX_DETECTOR_ITEMS`, so the detector was not run on it.

### Root causes

1. **The layout model proposes ruled, shaded or header-marked grids, and the arbitration only checks
   what it proposes.** Nothing proposes an unruled list or table, a table ruled only above and below
   whose cells are paragraphs, or anything on a page the cost guard skips. Seven of the 11 misses.
2. **Columns were read from glyph gaps alone, with a fixed 0.8 em.** TeX's `p{}` columns are
   justified with a 4 pt padding, which is not a cell gap; the lines read as one cell and the
   table was "no grid". Two misses.
3. **A caption was not evidence.** `is_layout_grid` was decided before the "Table N" caption was
   attached, and the caption pattern had no Roman numerals ("TABLE II.", the APS and IEEE
   style).
4. **Text direction was not looked at.** A page of sideways text is invisible to every stage. One miss.
5. **Thresholds tuned on four lab manuals.** The 0.8 em gap, the 60-character prose test and the
   "first column is a label" assumption fit worksheets; lecture notes and papers differ.

### What was changed

- **Own proposals** (`own_proposals`): rule frames and text blocks (above, "How a page's tables are
  found"), judged like the detector's. The detector's boxes are kept first, so what was found
  before is found as before; a box made here never replaces one.
- **Word gutters** (`_word_gutters`): strips no word crosses (at least 2.5 pt wide, flush against text
  on both sides on 60% of at least 10 lines) are column gaps.
- **Captions count** (`is_layout_grid` after `_attach_captions`; Roman numerals; a ruled box under
  a "Table N" line is a table, also with bullets in its cells or one column of wrapped text).
- **Sideways pages** (`_page_turn`, `_find_turned`, `TableGrid.turned`, `layout.crop`).
- **A text cell grid** (`_text_slots`) as a structure.
- **Guards that keep the new proposals precise**, each from a false table seen on real pages:
  charts (curves, scatter dots, bars, lettering under 0.7 of the page's), legends inside plots, page
  text columns, references, numbered or bulleted lines, algorithm boxes, equations and their numbers,
  authors and addresses, matrices, callout boxes (an outline that holds a smaller table yields to it;
  a paragraph in a box is not a row), and a bold-term list stays a list (LaTeX `description`).
  The reaction or equation above a table ("2NO + Cl2 -> 2NOCl  17.39", OpenStax numbers its
  equations with no brackets) is cut off the table's box (`_without_captions`).

### Numbers

All free, deterministic, from `readablestem eval pipeline --task tables --mode local`.

| | before (origin/main) | after |
|---|---|---|
| real documents, development set (`data/real-tables`; tuned on): precision | 1.000 (14/14) | **1.000** (23/23) |
| same: full-extent recall | 0.560 (14/25) | **0.920** (23/25; Wilson 0.75 to 0.98) |
| OpenStax tables task (197 gold tables, 45 documents): recall | 0.909 | **0.980** |
| same: precision (3 false tables, the same three) | 0.984 | **0.985** |
| same: TEDS (all gold) / found tables / column headers exact | 0.866 / 0.954 / 0.843 | **0.936 / 0.955 / 0.898** |
| same, by style: no rules / booktabs recall | 0.815 / 0.889 | **0.963 / 0.981** |
| dev-v2 tables task (31 documents, 53 gold tables, 42 chemistry): recall / precision | 0.774 / 0.911 | **0.925 / 0.907** |
| dev-v2 chemistry tables found | 32 of 42 | **38 of 42** |
| real-world dev papers (25 papers, 114 pages): tables found, by eye all real | 17 | 18 |
| same: pages with a table that holds gold equations, found | 14 of 15 | **15 of 15** |

(The gate, `eval gate --current <run with --real-dir data/real-tables> --baseline
data/eval/baseline-tables-local.json`: PASS, every floor and every regression check. The one
"false table" added to dev-v2's chemistry count by the new code is a 2 x 7 table found whose
text the scorer cannot match to its gold, not a table that is not there.)

The 2 misses left on the development set are the two notation lists of relations
(`X_n ~ f(n)  if X_n/f(n) -> 1`): lines of symbols in two aligned columns look like a displayed
system of equations, which is what the page guard (`_text_block_dropped`) exists to leave as text.

**False tables among the boxes made here** (own proposals only, on 25 development papers and 124
papers no real-world split uses, up to 40 to 60 pages each, 233 boxes; the labels of a table are its
source `tabular`, found in the PDF by `gold/tabulars.py`, and the 66 boxes with no matching
`tabular` were looked at one by one): 222 are tables (167 matched a `tabular`; 55 were tables whose
source the matcher could not place: tables made of maths, or of macros), 11 are not (5%): front
matter (article info beside an abstract), an equation beside its explanation, code, a plot's
tick labels and legend, a table of contents with a text column. None is a rule frame (77 boxes).

### Held out: a fresh set, run once

`data/real-tables-heldout/`: seven CC BY papers with tables (physics education studies, one
deep-learning survey: the lecture notes and tutorials the rule asked for were few among what nothing
had used), 58 pages, 39 labelled tables, registered with its floors (precision 0.95, recall 0.85)
and its selection rule before detection ran on it (commit `ce1f86e`). Run once with the code
that followed that commit (`aee6915`), reported as it came out:

| | origin/main | `fix/table-recall`, as registered | after follow-up fixes (not held out) |
|---|---|---|---|
| precision | 0.912 (31/34) | **0.914** (32/35; Wilson 0.78 to 0.97) | 0.951 (39/41) |
| full-extent recall | 0.744 (29/39) | **0.744** (29/39; 0.59 to 0.85) | 0.923 (36/39) |
| any-extent recall | 0.795 | 0.821 | 1.000 |

**Both registered floors are missed**, and the registered run is no better than the code
before it: the held-out set holds almost none of what the development set taught the detector
(no unruled list, no sideways page), and its tables fail for other reasons. Per table, the registered
run's 10 misses and partial finds (`data/real-tables-heldout/result-2026-10-21.json` has all of them):

- **Five tables of 2302.07336 (Tables I, II, IV, VII, X) and two of 2311.16525 (Tables I and II).**
  The first five are ruled tables of long wrapped text under a "TABLE II." caption: the caption
  pattern had no Roman numerals, so the box was a "layout grid" (two columns of prose), or had no
  columns to see ("no grid"). The two of 2311.16525 are ruled tables whose cells are bulleted
  ("o Revolving electrons"): the arbitration called them lists.
- **Three partial finds** (two are boxes a few points larger than the label, a rule or a caption
  line, so under 75% of the box lies in the table), **three false tables** (two bar charts and a scatter plot with axes, boxed by the layout model: its rules, ticks
  and labels look like a ruled grid; the strict chart test only ran on boxes made here).

These causes were read off the held-out result, so fixing them is not held-out evidence: after the
fixes below, the same set gives precision 0.951 and recall 0.923, and that number is a development
number. What was changed: Roman numerals in `TABLE_CAPTION`; a ruled box with a caption just above
or below it is a table whatever the glyph evidence for its columns (bullets, one column of wrapped
text), for the detector's boxes and for boxes made here that have columns; the chart test (bars,
scatter dots) applies to the detector's boxes too. On the OpenStax task and the dev-v2 set these
changed nothing (recall 0.980, precision 0.985; 0.925 and 0.907). The 2 remaining false tables are bar
charts with fewer than twelve bars; the partial finds are boxes a few lines too short or too long.

### What is not fixed

- Lists of relations, set as two aligned columns (2 of the 25), are left as text; so are lines of
  symbols in aligned columns generally, unless a "Table N" caption says so.
- A two-column "term, description" list set in bold (LaTeX `description`, a glossary) stays a list: it
  looks the same as a notation `tabular` in plain type, and the bold is the only difference.
- Bar charts with few bars, and plots drawn as ruled grids, can still be taken for a table by the
  detector's model.
- A table whose first column is empty on most lines (grouped rows) in a box found from text is read
  as a table of wrapped cells (`_text_slots` joins a line with no first-column text to the row above).
- The rotated-page fix handles whole pages of sideways text turned 90 degrees either way; a sideways
  table on a page that also has upright paragraphs is not turned (the share of sideways text must be 60%).
- Held-out precision and recall were measured on seven papers and 39 tables: wide intervals, and
  both floors missed as registered.

How to repeat (free; about four minutes each):

```
uv run readablestem eval pipeline --task tables --mode local --real-dir data/real-tables --out dev.json
uv run readablestem eval pipeline --task tables --mode local --real-dir data/real-tables-heldout --out held.json
uv run readablestem eval pipeline --task tables --set dev-v2 --part dev --mode local --out devv2.json
uv run readablestem eval gate --current dev.json --baseline data/eval/baseline-tables-local.json
```

`scripts/tables_label.py` and `scripts/tables_heldout.py` (with `gold/tabulars.py`) place a paper's
LaTeX tables on its PDF pages and build a labelled set like the held-out one.


## Second held-out measurement and the residuals (2026-10-22, branch `fix/tables-round2`)

Two parts: the residuals of the first round, fixed on development data only, and a second fresh
set, registered and run once. No model is called: spend $0, no setting changes.

### The residuals, fixed on development data

Development data here is `data/real-tables`, `data/real-tables-heldout` (a development set since the
first round), the 25 real-world dev papers and the 156 other papers of the real-world cache (181
papers, 4,177 pages, run whole through `layout.analyze` and compared with the papers' `tabular`
sources), and the OpenStax and dev-v2 table tasks. Each fix has a test (`tests/test_tables_round2.py`
and `tests/test_tables_recall.py`) that fails on `main` at the branch point, except the bold-list
pin, which passes on both.

| Residual | Cause | Fix | Dev effect |
|---|---|---|---|
| bar charts with fewer than 12 bars taken for tables | the bars of one series are one drawing of many `re` items, so they counted as one fill, and the count needed 12 | `layout._Path.rects` keeps the rectangles of a filled drawing; `tables._bars`: at least four filled rectangles on one line, side by side, of three or more different lengths, mostly with no text on them, is a chart (rules drawn as thin filled boxes are not bars) | the two bar charts of `data/real-tables-heldout` (false tables) gone: precision 0.951 to 1.000 |
| lists of relations (`X ~ f(n)   if ...`) left as text | lines of symbols in aligned columns look like displayed equations | `tables._conditions`: three or more lines whose last cell opens with "if", "when", "where"..., not built from bracket glyph pieces (`cases` is maths), are a list of definitions | the two lists of `data/real-tables`: recall 0.920 to 1.000 (25 of 25) |
| a ruled table split by far-apart rules (groups of rows) found as its first part | `_stack` only follows rules within three lines; the detector stops there | `_joined_frames`: the whole stack is the table when the detector's box in it is the stack's width and covers two spans, no paragraph runs across it, no caption is inside, and no other detector table is in it | partial find of 2302.07336 Table I found whole |
| two rules and ragged number columns under a "Table N" line | the span between two rules is not "ruled" for the arbitration | the detector's box with a top and a bottom rule, three lines in aligned columns and a caption is a table | 1 table found (2503.16294) |
| grids of single digits (codes, truth tables) called diagram labels | `labels_only` | not when a "Table N" line is over a grid that lines up | 6 tables found (2505.15032) |
| bold "term, description" lists (LaTeX `description`) stay lists | the bold is the only difference from a notation `tabular` | no change: the guard (`_bold_terms`) fires on none of the 181 papers' pages; one test pins it (a bold list is a list, with a caption a table, in plain type a table) | none: not a residual the data shows |

Measured on the development data, before (`main` at `8555f6c`) and after:

| | before | after |
|---|---|---|
| `data/real-tables`: precision / full-extent recall | 1.000 (23/23) / 0.920 (23/25) | **1.000 (25/25) / 1.000 (25/25)** |
| `data/real-tables-heldout`: precision / recall | 0.951 (39/41) / 0.923 (36/39) | **1.000 (39/39) / 0.949 (37/39)** |
| 181 papers, whole pipeline, tables found | 313 | 324 (11 more, by eye all tables, none false) |
| OpenStax tables task: recall / precision | 0.980 / 0.985 | **0.985 / 0.985** (3 false tables, the same) |
| same: TEDS / column headers exact | 0.936 / 0.899 | 0.941 / 0.899 |
| dev-v2 tables task: recall / precision | 0.925 / 0.907 | 0.925 / 0.907 |
| gate (`eval gate`, with `--real-dir data/real-tables`) | PASS | PASS (every floor and regression check) |
| `layout.analyze`, s per page, one CPU: `data/real-tables` / `data/real-tables-heldout` | 0.38 / 0.85 | 0.38 / 0.85 (within 1% over two runs each) |
| the 181 papers, s per page (10 processes in parallel) | 0.87 | 0.83 |

These are development numbers: the first two rows are what the fixes were read off.

### Held out, second set: registered, run once

`data/real-tables-heldout2/` (README: the question, floors, rule, labels and comparison; hashes in
`REGISTRATION.sha256`; committed at `5b1ea27`, before any detection ran on it): ten CC BY 4.0
arXiv documents, 100 pages, 58 labelled tables, from computer science (4), engineering (3),
statistics (2) and economics (1), chosen by a seeded rule over the real-world pool and 20 further
sets (21,615 records) minus the 330 papers earlier work used. The rule's "teaching" tier matched
words in abstracts too, so the set is mostly surveys and research articles; two documents are a
tutorial and a practitioner's guide, **none is lecture notes**. Biology and mathematics gave no
document (their candidates had fewer than three tables in the PDF's words, or a PDF source).
Labels: proposals from the LaTeX source, every page looked at, boxes corrected by hand
(`label-corrections.json`). Run once for each arm, free:

| | `main` (`8555f6c`) | `fix/tables-round2` (`433b729`) | floor |
|---|---|---|---|
| precision | **1.000** (55/55; Wilson 0.93 to 1.00) | **1.000** (55/55) | 0.95 |
| full-extent recall | **0.948** (55/58; 0.86 to 0.98) | **0.948** (55/58) | 0.85 |
| any-extent recall | 0.948 | 0.948 | |

**Both floors are met, by both arms, and the two arms agree table for table.** The round's fixes
changed nothing on this set: what held up here was the first round's code (`main`). The set shows
no effect of the round-two fixes, up or down, and the intervals are wide (10 documents, 58 tables;
the recall interval starts at 0.86, barely above the floor). The three misses, the same in both
arms (`result-2026-10-22.json` has every table):

- **Table 1 of 2202.07104** (a two-row table with rotated labels in the first column): judged a
  figure; the rotated words are drawn as paths.
- **Table 6 of 2202.07104** (a 28-row table of check marks, ticks and stars): judged a figure, for
  the same reason: the marks are vector drawings in the box.
- **Table 1 of 2503.10293** (a ruled four-column table of words with no digit): the detector and
  the arbitration keep it, then `is_term_grid` turns it into a list ("grid of terms"), because its
  caption "Table 1. Correspondences ..." is not attached: the journal's small capitals make `layout`
  split the caption into one row per word, so `TABLE_CAPTION` never matches.

No false table on any of the ten documents. The misses were read off the held-out result, so fixing
them would be development work; they are left, and listed below.

### What is not fixed (second round)

- The three misses above: a caption set in small capitals that `layout` splits into one row per
  word (a grid of terms is then written as a list), check-mark tables, rotated first-column labels.
- Lists of relations without "if", "when" or "where" in the last cell stay text; a bold description
  list has no instance in the data and is pinned by a test only.
- The second set is not lecture notes, has no biology or mathematics, and its labels were checked
  by one person.
- A bar chart with values printed on most bars is not called a chart by `_bars` (the rule needs
  most bars empty): if the layout model boxes it, it stays a table.
- `tests/test_proof_interim.py::test_the_report_is_what_the_committed_data_produce` fails here on
  `main` at the branch point too (a 0.1738 against 0.1760 in one interval of the committed report);
  not caused by this work.

Unchanged from the first round: a sideways table on a page with upright paragraphs is not turned;
a table whose first column is empty on most lines is read as wrapped cells.

How to repeat (free):

```
uv run readablestem eval pipeline --task tables --mode local --real-dir data/real-tables-heldout2 --out held2.json
uv run python scripts/tables_heldout2.py pool|select|build --cache DIR   # how the set was made
```
