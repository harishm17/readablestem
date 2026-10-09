# Authored table documents (tables eval)

Course-style documents with tables, written for a11y-stem: no third-party text, no
student or instructor data. Licence: MIT, like the code (`LICENSE`). They complement the
OpenStax table gold in `data/gold/tables/` (CC BY 4.0), whose tables all come from one
web print: these come from other toolchains and cover what OpenStax lacks.

Each folder holds one document: its source, the PDF the eval reads, and `gold.jsonl`
(one table per line: `id`, 0-based PDF `page`, `is_layout`, `html` with `th`/`td`,
`scope`, `rowspan`, `colspan`; a table split over pages has one record per page sharing a
`cluster`). `index.json` lists the folders the eval uses.

| Folder | Toolchain | Tables | What it covers |
|---|---|---|---|
| `tablebench-latex` | LaTeX (Tectonic), `tables.tex` | 6 | booktabs + siunitx, multirow/multicolumn with row headers, maths in cells, fully ruled, unruled, row headers |
| `tablebench-word` | Word-style HTML printed by Chromium, `word_tables.html` | 2 | Word "Grid Table 4" (banded fills), "Plain Table 2" with a merged two-level header and row headers |
| `handout-latex` | LaTeX (Tectonic), `handout.tex` | 9 (+1 part) + 1 layout | lab data, constants with powers of ten, two-level header, a prose rubric, an unruled schedule, a longtable over two pages, row headers, a layout table, vertical merges, `\rowcolors`; beside table-like traps (a matrix, an augmented array, `align*`, `cases`, multiple choice in two columns, form lines) |
| `handout-word` | Word (python-docx), exported to an untagged PDF by LibreOffice 26.2, `handout.docx` | 9 (+1 part) + 1 layout | the same ten tables in Word's built-in table styles, a table repeating its header row over a page break, the same traps |
| `maths-and-headers` | HTML printed by Chromium, `maths_and_headers.html` | 4 (+1 part) + 1 layout | maths in header cells, a two-level header with maths, row headers with maths in the cells, a table split across two pages with its header repeated, a form laid out as a table |
| `slides` | HTML at 16:9 printed by Chromium, `slides.html` | 2 | PowerPoint's default table style (filled header row, banded rows, white lines, no rules), one with row headers |

`tablebench-*` and `handout-*` were built during the PDF research of 2026-10-08/09
(table-bench and its challenge); their PDFs are kept as built. `maths-and-headers` and
`slides` are rebuilt by `make_authored.py` (see its docstring). The slides are HTML in
PowerPoint's look, not an export from PowerPoint: LibreOffice Impress was not available
to export a real deck.

TODO(Harish): `LICENSE` says the code is MIT and `data/gold/` is CC BY 4.0; say also that
`data/authored/` is MIT (it is written for this project), if you agree.
