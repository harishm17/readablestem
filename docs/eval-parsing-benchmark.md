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

## Scores (pipeline 0.5.14, 747 pages, 3,846 checks listed, $0)

Measured on 2026-11-19 at commit `709a023f`. The interval is the 95 % Wilson interval of the
checks run. The two columns after it are the records before this one (pipeline 0.5.13 at
`3b177567`, 2026-11-19, and the first record, pipeline 0.5.11, 2026-11-18).

| Category | Ours (checks run) | 95 % interval | At 0.5.13 | At 0.5.11 | Run of listed | Dropped |
|---|---|---|---|---|---|---|
| multi_column (order) | 69.2 (490 of 708) | 65.7 to 72.5 | 69.2 (708) | 68.8 (705) | 884 | 176 scans |
| headers_footers (absent) | 38.6 (244 of 632) | 34.9 to 42.5 | 34.5 (632) | 32.5 (636) | 753 | 121 scans |
| long_tiny_text (present) | 37.3 (31 of 83) | 27.7 to 48.1 | 37.3 (83) | 37.3 (83) | 442 | 359 scans |
| table_tests (table) | 66.6 (571 of 857) | 63.4 to 69.7 | 66.6 (857) | 51.8 (857) | 1,020 | 163 scans |
| baseline (not blank, no repeats) | 100.0 (586 of 586) | 99.3 to 100.0 | 100.0 (586) | 99.0 (586) | 747 | 161 scans |
| arxiv_math, old_scans_math | not run at $0 | | not run | not run | 2,927 and 458 | equations are read by a model |
| old_scans | not run | | not run | not run | 526 | scans: a model reads the picture |

From 0.5.13 to 0.5.14 only headers_footers moved (26 more checks pass: the head and foot of a
one-page file found by their shape, as narrowed after review; see "A head and foot found by
their shape" below). The other four categories pass and fail the same number of checks. The
table score rose from 0.5.11 to 0.5.13 as the corner-square fix below measured on the two
halves (571 checks pass, 444 before); no category fell. The sections under "Failures by
cause" were read on the 0.5.11 run, and the counts in their headings are that run's.

Dropped checks are in the score of no category; the record also holds each score with the
dropped checks counted as failed (55.4, 32.4, 7.0, 56.0, 78.4). Of the 161 dropped pages, 60 are
scans with no text layer, 72 are scans with an OCR layer (the product reads such a page from its
picture and does not use the layer, so at $0 there is no output), 3 are drawings with no text and
26 are typed otherwise but hold only pictures (image-only pages). long_tiny_text keeps 83 of 442
checks (13 of 62 pages), so its score is a thin sample. 599 of the 747 pages asked a model for
something (equations 2,854, figures 1,174, tables 530, image pages 264); at $0 those parts are
left as the text path makes them.

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

**A head and foot found by their shape (2026-11-19, `_MarginFilter.by_shape`).** A page that is
a file of its own has no second page to repeat on, so its head and foot are found by what they
look like: a stack of at most 4 lines in the outer 12 % of the page, a line clear of the rest of
the text, nothing set as a heading, and a page number (digits or "Page 3 of 9") at the start or
end of one of its lines (docs/layout.md, "Running headers"). Run of all 747 pages at the same
settings ($0, `eval parsing-bench`), before and after on the same tree:

| Category | Before | After | Note |
|---|---|---|---|
| headers_footers | 34.5 (218 of 632) | 51.4 (325 of 632) | 107 more checks pass |
| multi_column | 69.2 (490 of 708) | 69.1 (492 of 712) | 4 more checks ran (one page was read as a scan in the first run and not in the second, with or without the rule); 2 order checks fail that passed, see below |
| table_tests | 66.6 (571 of 857) | 66.6 (571 of 857) | none changed |
| long_tiny_text | 37.3 (31 of 83) | 37.3 (31 of 83) | none changed |
| baseline | 100.0 (586) | 100.0 (587) | the page above |

The two multi_column checks are the order checks of two pages of one journal article whose
"before" text is the article's running head (its title, with the page number on the same line):
the benchmark's gold counts that head as text, and the same kind of line is a "must be absent"
head in headers_footers. A head with a page number beside it is furniture by this rule on
every page of every file; the benchmark disagrees with itself on this one document. The rule
took 112 rows that a headers_footers check names, 0 rows of any table, order or present check
other than those two pages, and 0 rows of table_tests (66 rows there are heads, feet and page
numbers the checks do not name). Of the 307 headers_footers checks that still fail (150
pages): 144 are on pages with no page number at an edge (an ISSN, a DOI, a copyright line,
"Downloaded from": not taken, because a flyer's contact line has the same shape); 145 are on
pages that have one and are left for another reason (a stack of more than 4 lines, a foot
under a table or a list that runs on, a head set in a heading size, a number in the middle of
a line); 13 are lines further in than 12 % of the page; 5 have no furniture line left. For
88 of the 307 (48 pages), the other edge of the page is the one found: a journal's head at
the top and the page number at the foot. That would be a second rule, a head with no number of
its own taken because the foot proves the page was cut from a paginated file. It is not built:
a single page keeps a draft stamp or title at its top, whatever is at its foot, by a test
that stays, and the rule is not measured against the gold sets.

**The rule as it shipped (pipeline 0.5.14).** The rule above also took the lines of a one-page
quiz that end in a number ("Name: ... 20", "Quiz 3: Limits and continuity ... Points: 25"),
which an independent review found. Lines with words now go only with a number that says it is
a page's ("Page 3", "Page 3 of 9", "3 of 9"); a page number alone on its line goes as before
(docs/layout.md, "Running headers"). Run of all 747 pages at commit `709a023f`: headers_footers
38.6 (244 of 632, 95 % interval 34.9 to 42.5), between the 34.5 of 0.5.13 (218) and the 51.4
of the rule as first built (325). The 81 checks given back are journal heads with a bare page
number beside the name, which this rule cannot tell from a quiz's own lines. multi_column
(490 of 708), table_tests, long_tiny_text and baseline are as at 0.5.13.

**Small-type heads and feet, measured and not built (2026-11-20).** The line left open above,
taking a stack of one-page files that is set at least 0.5 pt under the body size, clear of the
text block, with no page number needed, was run over the same 747 pages ($0, patched in a
process, not shipped). 213 pages took lines (447 lines). Read: most are furniture (DOIs, journal
and volume lines, "Downloaded from", copyright and license lines, "by guest on"), and 173 pages
have a headers_footers absent check that names a line taken. But 89 of the 447 lines, on 65
pages, read as content: footnotes (three numbered notes of a Spanish report), figure captions
("Fig. 3. (a, b) TEM images ..."), a table's note ("Stdev. = Standard deviation, CV% = ..."),
a flyer's fine print, a bullet of a program ("Three panels will be running simultaneously."),
and lines that a table or order check names (13 on table pages, 4 on multi_column). The same
shape that finds "jb.asm.org" finds a footnote, so no narrower form of it is built here. On the
one-page files of the repository's other material the rule took 9 lines on 9 of 56 files, and
on the private corpus 2 lines on 2 of 3. Candidate (ii), no removal and a notice that lists the
furniture with a put-back, is not measured: it needs a notice and a way back in the student page,
which this layout change does not touch.

### long_tiny_text: 52 failed of 83

| Cause | Checks | Step | In the plan |
|---|---|---|---|
| Newspaper and magazine columns read across (text of the neighboring column inside the target text) | 45 | same as multi_column | In the plan: WS15.1 (F-355) |
| The page's text is in pictures or the text layer has almost nothing | 6 | figure and image-page steps | A model reads it: not a $0 case |
| Word spaces missing in the PDF's text layer ("Whattypesofmattress") | 1 | text as given | New, n = 1; the same fused-word pattern appears in multi_column's near matches |

### table_tests: 413 failed of 857

| Cause | Checks | Step | In the plan |
|---|---|---|---|
| No table built, and the text of the cell is on the page as plain text (a table without ruling lines, a leader-dot table, a table inside a column page) | 148 (36%) | `tables.find` | In the plan: K187g (27 of 297 sealed tables not found, "most of them unruled or beside prose"). New: on this set the rate is much higher than the plan's 9% |
| No table built, the cell's text is not in the output either (columns of the page joined across the table, or the table is a picture; 18 of these are pages with almost no text) | 156 (38%) | layout order first, then `tables.find` | Same as above, plus WS15.1. Pictured tables cannot be told from missed tables at $0: the figure type `table_image` is set by the model. They are therefore not dropped, and this group is an upper bound on them |
| A table was built but the cell's text is not found in one cell (two cells merged, or a row lost) | 71 (17%) | table grid | In the plan: WS5 table work; new as a rate |
| The cell is found and its heading or a neighbor is not the expected one (header rows read as data, two header rows) | 38 (9%) | header detection | In the plan: WS5.2 (F-091, F-092) |

#### After the corner-square fix (2026-11-18)

Table checks 51.8% (444 of 857) to 66.2% (567 of 857), $0, same 747 pages. The split of the
188 table pages into a development half (93 pages) and a check half (95) is by document: the
page is in the development half when the last hex digit of the SHA-256 of its document id (the
file name up to `_pg`) is even. Only the development half was read while building; the check
half was converted and scored once, after the code was fixed.

| Half | Table checks passed before | after | baseline checks passed before | after |
|---|---|---|---|---|
| development (432 run) | 229 (53.0%) | 286 (66.2%) | 77 of 78 pages | 78 of 78 |
| check (425 run) | 215 (50.6%) | 281 (66.1%) | 79 of 80 | 80 of 80 |

No check went from pass to fail on either half; 57 and 66 went from fail to pass.

Cause of the fix: the finder's chart test (`tables._pictured`) counted 12 filled shapes with no
text on them as the bars of a chart. Word and some other writers draw a table's rules as thin
filled boxes and fill a 0.5 pt square where two of them cross (or two pieces of one rule meet);
a table of 8 columns and 11 rows has about 100 of them, so a fully ruled table (alignment 1.0,
3 to 45 row rules, 4 to 9 column rules) was refused as a `figure`. A filled square no bigger
than a rule's thickness that lies on a drawn rule is no longer counted (`_PageView.build`).
In the development half 18 of the 51 pages with a failed table check had a box refused with a
`figure` verdict; 13 of them now have their table built (8 pass every check, 5 still fail
cells: grid reading, the table step's).

Causes on the development half, failed table checks (203 before, 146 after), by page and by
check, split by whether the cell's text is on the page in the output ("on") or not
("missing"; columns joined across the table, or a pictured table: not the finder's):

| Cause | Pages before | Checks before (on / missing) | Pages after | Checks after (on / missing) |
|---|---|---|---|---|
| A proposed box was refused with a `figure` verdict (corner squares counted as bars) | 18 | 57 / 47 | 5 | 16 / 15 |
| Proposed and refused `no_grid` or as a layout grid (unruled number columns that do not line up, a monospace print-out, a striped table with no rules, a two-line table, a ruled key-value box) | 6 | 27 / 3 | 6 | 27 / 3 |
| A table was built but the cell is not found in one cell, or the neighbor is wrong (merged cells, a lost row: the grid, WS5) | 23 | 44 / 13 | 28 | 56 / 15 |
| Found, then written as a flagged block and not as a table (at $0 the table step has no answer) | 3 | 4 / 5 | 4 | 4 / 7 |
| Not proposed (the probe refused the page: a turned table inside a column page) | 1 | 0 / 3 | 1 | 0 / 3 |

Harm, all $0. New tables over the 29 corpus files replayed at $0 (counts only): 18 to 24,
every one a drawn cell grid (3 or more rules each way) on 3 files: four are blank-cell forms,
one is captioned, one a schedule; all 6 are tables, none false. New tables on the
non-table categories' 559 pages (multi_column, headers_footers, long_tiny_text; their baseline
checks are the same pages): 80 to 88, 8 new on 6 pages, all of them tables read by eye (a
captioned one in 5 of the 6 pages' tables); the 31 checks of those 6 pages: 25 passed before and
25 after, none changed. `eval pipeline --task tables --mode local`: equal to
`data/eval/latest-tables-local.json` on every count (recall, precision, TEDS, headers, review
load), 0 differences. Finder seconds a page on the development pages 0.60 before and 0.60 after.

What is left: 11 pages of the development half with a box the finder refuses (5 with a `figure`
verdict: of those I looked at, a table that is a picture, a table under photographs, a table
beside charts, and a text box taken for a figure; 6 `no_grid`) and no rule that
tells it from prose (each kind on one or two pages); 28 pages with a table built and a cell
not found, which is the table grid (WS5); 156 checks with the text missing (columns joined,
pictured tables; WS15.1 and the figure step).
### baseline: 6 failed of 586

Four pages have the text in a picture and the product asked a model; two were typed `blank` with
route `nothing` and came out empty with no flag and no model request. Their text is in the
bitmap fonts dvips writes: Type 3 fonts measured in pixels, set at a stated size of 0.12 or
0.24 pt and drawn 10 pt tall. The rule that text under 2 pt is unseen read the stated size.
Fixed after this run (`page_type.drawn_size`, docs/layout.md "a span drawn under 2 pt"): both
pages are now typed `born_digital` and read from their text. The scores above were made
before the fix. n = 2 of 747.

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
