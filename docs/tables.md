# Tables

How a data table in a PDF becomes an accessible HTML table. Two parts:

- **Detection** (`pipeline/tables.py`, the tables-detect work): finds tables, builds the
  grid from the PDF's own words and lines (`Element.table`, source "local") and claims
  the rows and the "Table N" caption from the text flow. Its section is added here when
  that work merges.
- **The table step** (`pipeline/table_step.py`, below): a vision model reads the table
  from its crop, deterministic checks compare the answer with the PDF's words, and the
  result is written by `pipeline/table_html.py` (`render_table`, the one serialiser)
  inside `pipeline/assemble_tables.py`.

Contracts, flags and review tiers: [design/content-features.md](design/content-features.md).
The plan behind it: `a11y-research/PDF-RECOMMENDATION.md` sections 4.3 to 4.5.

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
- **Row headers** only when the model marks the first column's body cells as row
  headers **and** those cells are non-empty, not numbers (a cell whose first letter or
  digit is a digit: "2019", "-3.5", "2 kg") and all different (planets, substances,
  quantities). Otherwise they are data cells: gpt-6-luna marked numeric first columns as
  row headers in 14 of 59 bench tables. Row flags anywhere else are dropped.
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
| `table.cell_text` | `whitespace_differs` | equal once spacing is ignored ("12 345" and "12345", "tempera- ture") | optional note |
| `table.cell_text` | `no_pdf_text` | no PDF words to compare (no local grid with text, no element text) | required |
| `table.math` | `invalid_latex` | a maths cell's LaTeX fails a validator (error severity; the cell shows its text) | required |
| `table.math_symbols` | `symbol_mismatch` | a maths cell's letters, digits or Greek letters differ from the PDF glyphs in its place | required |
| `table.math_signs` | `sign_mismatch` | a sign or relation in a maths cell differs (the equation step's `compare_signs`) | required |
| `table.math_style` | `style_mismatch` / `style_cosmetic` | a meaning-level letter style (a bold vector read as plain) / a cosmetic one | required / optional note |
| `table.source` | `local_grid` | no model answer was usable: the proposal is the local grid | required |

**The cell-text bijection.** The words compared are numbers (with a minus sign glued to
them at the start, or after a space, an opening bracket, a colon, a semicolon, a comma or
one of the symbols below, so "10 × -5" and "(3,-5)" keep their signs while "1-3" is a
range and "C-14" a hyphen), runs of letters, and symbols that carry meaning in a cell
(`/ × · * % ‰ + ± ∓ = ≠ < > ≤ ≥ ≈ ~ ° √ ∞ ∝`): m/s is not "m s", and 5 % is not 5. Text
is NFKC normalised (ligatures, full-width forms, superscript digits), invisible format
characters dropped, dashes and minus signs folded into one, pdfTeX's U+2206 into Δ; case
is kept (mW is not MW); brackets, quotes, commas and full stops are not compared. The
non-maths cells of the answer, as a multiset, must equal the PDF's words:

- with a local grid of the same size, **row by row**: each answer row against the local
  row with the same number, leaving out the local cells where the answer has maths (the
  maths checks compare those). A row-by-row failure whose totals still match is
  `row_mismatch`; one that holds once all spacing is removed is `whitespace_differs`;
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
| 2 | the answer kept says it is not a data table | ABSTAINED | `not_data_table` | required (image kept) |
| 3 | no usable answer, a local grid exists | NEEDS_REVIEW, the local grid with first-row headers | (check `local_grid`) | required |
| 3b | no usable answer, no local grid | FAILED | `model_<outcome>` or `invalid_table` | required (image kept) |
| 4 | every check passed | ACCEPTED | | none |
| 5 | only notes failed (`whitespace_differs`, `style_cosmetic`) | ACCEPTED | | optional |
| 6 | otherwise | NEEDS_REVIEW, the model's grid | | required |

The model gives no confidence: the checks decide, so `Fix.confidence` is None. A table
waiting for review is written as rebuilt (like an equation waiting for review);
rejecting it keeps the image. The review screen shows the proposal beside the crop and
the local grid (`Element.table`), with the failing checks named (tables-review work).

### Output

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
TABLE element layout finds in `tests/fixtures/layout/latex` with its real local grid; it
is skipped until a fixture has a table.

TODO for the integrator: once tables-detect has merged with its LaTeX table fixture, run
`tests/test_tables_live.py` (or `a11y-stem convert` with `FEATURE_TABLES=true` and a small
`--max-usd`) and compare with the results above.

### Known limits

- Values swapped between two columns of the same row keep every row's words, so the
  bijection does not see it (grid agreement and the review screen are the guard).
- A maths cell is compared as a bag of letters and digits (as an equation is): digits
  in the wrong order inside one maths cell are missed. Plain numbers are text cells
  (LaTeX without commands or scripts is treated as text), so the word check covers them.
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
