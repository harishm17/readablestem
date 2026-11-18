# olmOCR-Bench at $0 (A27, F-403)

The first run of the product on a public benchmark. It is a subset: only the categories whose
checks the text path can answer without a model. Command: `readablestem eval parsing-bench`
(`--fetch` the first time). Record: `data/eval/parsing-bench-olmocr.json` (counts only: no
page text, no file from the benchmark, no home path).

## What was run

- Benchmark: `allenai/olmOCR-bench`, revision `54a96a6fb6a2bd3b297e59869491db4d3625b711`,
  license ODC-BY-1.0 (stated in the README's front matter; read from the fetched file by
  `read_license`). Fetched to `~/.cache/readablestem/bench/olmocr-bench/`, outside the
  repository; nothing from it is committed. The README does not name Docling or pymupdf4llm,
  so the plan's two figures for them (64.2 and 43.4) are not quoted.
- Checker: the benchmark's own `olmocr.bench.tests` (`olmocr==0.4.27`), in a separate
  environment built by `--fetch`. No model is needed for these checks.
- Conversion: each one-page PDF through `convert_isolated` with the production profile and a
  client that refuses every request (`FakeVisionClient`). A page the product routes to a model
  (`vision_page`: a scan, with or without an OCR layer) has no text at $0 and is dropped, not
  scored as a failure.
- Adapter (`eval/olmocr_bench.py`): the `<main>` of the student page as markdown, minus what the
  writer adds (notice, About, the title it makes, labels, hidden text). Headings `#`, ordered
  list items with their printed number, tables as HTML tables (cells, spans, `th`), ligatures
  spelled out, invisible characters removed, the ellipsis and `''` as the benchmark writes them.
- Run 2 and run 3 converted all 747 pages from scratch; see "Repeat" below.

## Scores (pipeline 0.5.11, 747 pages, 3,846 checks listed, $0)

| Category | Ours (checks run) | Run of listed | Dropped |
|---|---|---|---|
| multi_column (order) | 68.8 (705) | 884 | 179 scans |
| headers_footers (absent) | 32.5 (636) | 753 | 117 scans |
| long_tiny_text (present) | 37.3 (83) | 442 | 359 scans |
| table_tests (table) | 51.8 (857) | 1,020 | 163 scans |
| baseline (not blank, no repeats) | 99.0 (586) | 747 | 161 scans |
| arxiv_math, old_scans_math | not run at $0 | 2,927 and 458 | equations are read by a model |
| old_scans | not run | 526 | scans: a model reads the picture |

Dropped checks are in the score of no category; the record also holds each score with the
dropped checks counted as failed (54.9, 27.5, 7.0, 43.5, 77.6). Of the 161 dropped pages, 60 are
scans with no text layer, 72 are scans with an OCR layer (the product reads such a page from its
picture and does not use the layer, so at $0 there is no output), 4 are drawings with no text and
25 are typed as text but hold only pictures (image-only pages). long_tiny_text keeps 83 of 442
checks (13 of 62 pages), so its score is a thin sample. 603 of the 747 pages asked a model for something (equations 3,038,
figures 1,266, tables 434, image pages 132); at $0 those parts are left as the text path
makes them.

Published scores of other tools (README; their full runs, all categories): olmOCR 79.4 / 93.4 /
81.7 / 70.2 / 99.0, Marker 71.0 / 87.1 / 76.9 / 69.8 / 99.5, MinerU 59.0 / 96.6 / 39.1 / 60.9 /
96.6, Mistral OCR 71.3 / 93.6 / 77.1 / 60.6 / 99.4, GPT-4o anchored 69.3 / 93.8 / 60.6 / 70.0 /
96.8, for MultCol / Hdr-Ftr / TinyTxt / Tables / Base. Ours covers a subset (no scans, no
math, no model), on text-layer pages only, so the columns are not a like-for-like ranking. The
full table is in the record.

## Failures by cause

Each category's failed checks were read through a sample (multi_column 78 read one by one,
headers_footers 44, long_tiny_text 24 of 52, table_tests 40, baseline 6 of 6) and the whole
set was then counted with a script on the same test (text found or not, where it was found).
Counts for the big groups are therefore sizes of a script class, not hand labels. "In the
plan" names the item that already covers the cause; "new" means no part file or STATUS row
does.

### multi_column: 220 failed of 705

| Cause | Checks | Layout rule or step | In the plan |
|---|---|---|---|
| Lines of two or three columns joined row by row, so a sentence is cut by the next column's text (the "before" or "after" text is only partly in the output) | about 150 (68%) | `layout._gutter_with` finds one gutter, and only when the median line is 0.3 of the page; short-line and three-column pages are read across | In the plan: WS15.1 (F-352, F-390, F-534), measured and not built (STATUS "WS15.1 note", 2026-11-18) |
| Both texts are found, off by 1 to 7% of characters: accent marks set apart from the letter, a "Th" ligature printed as "T", words fused or split by the PDF's own text layer, a kept line-end hyphen | about 50 (23%) | text layer as given; `_line_break` (K159c) | In the plan: WS2.16 for the hyphen and split words (built on fix/line-end-joins, not merged), WS2.13 and WS2.12 for glyph codes. The accent and "Th" cases: new, small |
| Margin line numbers read into the prose | few | line-number column | In the plan: WS15.4 rule 2 (F-539) |
| Both texts found and in the wrong order | 9 | `_top_down` | In the plan: WS15.1 |
| Page is mostly a picture: little text to find | about 10 | figure step | A model reads it in the product: not a $0 case |

### headers_footers: about 430 failed of 636

The check says a running head, foot or page number must not be in the output. 44 failures were
traced to the element that holds the text and its place on the page.

| Cause | Share of the 44 | Layout rule or step | In the plan |
|---|---|---|---|
| The head or foot is within the top or bottom 7% and was kept as a paragraph or heading: a one-page file has no second page, and furniture is removed by repeating across pages | 66% | `layout._MarginFilter`, `MARGIN_BAND = 0.07`, needs `MARGIN_REFERENCE_PAGES` repeats | In the plan: SW5 rule 3 and K26/K27, F-253. SW5 is built (fix/running-heads-and-startup, 6d0af458) but keeps the repeat condition, so it does not help a single page. The single-page case is the new part of this line |
| The line sits between 7% and 10% from the edge, outside the band | 16% | `MARGIN_PLACE_BAND = 0.10`, `_is_page_number` | New for the band; the repeat rule is the same as above |
| The line sits further in (a printed footnote, a heading line the benchmark calls a header, a page number set inside the text block) | 11% | none: it is body by position | Partly the benchmark's convention: its labels include footnotes as footers |
| The head's glyphs came out garbled (private-use characters) | 5% | glyph codes | In the plan: WS2.13 (built), WS2.12 |

The first cause is the largest single cause in the whole run. A running head on a page the
product reads alone stays in the text, and this is the way the benchmark gives it a page. On a
document it would be removed by repeats. The product's claim (furniture is removed) therefore
holds for documents and not for one-page uploads; the plan's repeat rule does not cover them.
New cause; it holds for 29 of the 44.

### long_tiny_text: 52 failed of 83

| Cause | Checks | Step | In the plan |
|---|---|---|---|
| Newspaper and magazine columns read across (text of the neighbouring column inside the target text) | 45 | same as multi_column | In the plan: WS15.1 (F-355) |
| The page's text is in pictures or the text layer has almost nothing | 6 | figure and image-page steps | A model reads it: not a $0 case |
| Word spaces missing in the PDF's text layer ("Whattypesofmattress") | 1 | text as given | New, n = 1; the same fused-word pattern appears in multi_column's near matches |

### table_tests: 413 failed of 857

| Cause | Checks | Step | In the plan |
|---|---|---|---|
| No table built, and the text of the cell is on the page as plain text (a table without ruling lines, a leader-dot table, a table inside a column page) | 148 (36%) | `tables.find` | In the plan: K187g (27 of 297 sealed tables not found, "most of them unruled or beside prose"). New: on this set the rate is much higher than the plan's 9% |
| No table built, the cell's text is not in the output either (columns of the page joined across the table, or the table is a picture; 18 of these are pages with almost no text) | 156 (38%) | layout order first, then `tables.find` | Same as above, plus WS15.1. Pictured tables cannot be told from missed tables at $0: the figure type `table_image` is set by the model. They are therefore not dropped, and this group is an upper bound on them |
| A table was built but the cell's text is not found in one cell (two cells merged, or a row lost) | 71 (17%) | table grid | In the plan: WS5 table work; new as a rate |
| The cell is found and its heading or a neighbour is not the expected one (header rows read as data, two header rows) | 38 (9%) | header detection | In the plan: WS5.2 (F-091, F-092) |

### baseline: 6 failed of 586

Four pages have the text in a picture and the product asked a model; two are typed `blank` with
route `nothing` and have visible text in Type 3 fonts with no text map. Those two pages come
out empty with no flag and no model request. New cause, n = 2 of 747, no repeat inside this
set.

## Adapter share

The adapter's own failures are small. The fixes made after reading the first run (ordered-list
numbers, ellipsis and doubled quotes) changed 8 of 3,846 check results (6 and 1 in the first
two categories from fail to pass, 1 from pass to fail because the check now sees a footer
that was hidden by a different ellipsis). Of the 78 multi_column failures and 44
headers_footers failures read one by one, none left is the adapter's, beside the Unicode and
spacing forms that are the text layer's own. Estimate of what remains: under 3% of failures in
multi_column and headers_footers, and not measurable in the others.

## Repeat

Run 2 and run 3 are full conversions in separate folders; their per-category scores are
equal (0 of 3,846 check results differ; the whole record compares equal). `readablestem eval parsing-bench --run-dir` keeps
page files, so a stopped run resumes.
