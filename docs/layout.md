# Layout analysis (`pipeline/layout/`, `pipeline/heading_step.py`)

Step 1 of the conversion: a PDF becomes a list of `Element`s (headings, paragraphs, list
items, display and inline equations, figures, data tables) in reading order, with no model
call. Everything later
(equation transcription, alt text, HTML assembly) works from these elements and from crops
of their boxes. The code is deterministic: the same PDF always gives the same elements.

## Contract

| Function | What it returns |
|---|---|
| `analyze(pdf_bytes, *, pages=None, max_pages=None) -> Layout` | elements of the requested 0-based pages (all by default), `page_count`, `pages`, `page_sizes`, `title`, `lang` |
| `crop(pdf_bytes, element, *, dpi=200, pad=2.0, max_side_px=2048, tag_dpi=False) -> bytes` | PNG of the element box grown by `pad` points, clipped to the page, long side capped; `tag_dpi` writes the resolution into the PNG header (equation crops for the student file, `pipeline/equation_scale.py`) |
| `release(pdf_bytes=None)` | drops one cached document (or all); call it in a `finally` when a job finishes |
| `heading_levels(elements) -> dict[id, level]` | raw outline level per heading (before repair) |
| `page_glyphs(page) -> list[Glyph]` | every visible character of an open page with its box (display space), span color and size, and its face's weight, slant and pitch, for the text pass |
| `page_paint(page) -> (list[Strike], list[Highlight])` | thin horizontal rules and bars, and StrikeOut annotation quads, of an open page, with its Highlight annotation quads and filled rectangles within 36 × 600 pt (display space), from one read of its drawings, for the text pass |
| `heading_step.heading_fixes(elements) -> list[Fix]` | one `HeadingFix` per heading, outline repaired, with checks and confidence |
| `text_pass.apply(pdf_bytes, elements, *, pages, layout_lang, colour, runs) -> TextPassResult` | the elements with links, color runs, struck runs and passage languages, the document language and its source, and the pass's notices (see "The text pass") |

Element conventions:

- **Ids** are `p{page+1}-e{index}`, `index` counting from 0 per page in reading order.
  An inline equation gets the id right after its paragraph (`p1-e3` paragraph, `p1-e4` and
  `p1-e5` its inline equations, `p1-e6` the next block).
- **`order`** is the position in the whole document (0, 1, 2, ... across pages).
- **Inline equations** have `display="inline"` and `parent_id` = the paragraph (or list
  item) id; the parent's `text` contains `core.types.inline_marker(id)` (`⟦p1-e4⟧`) where
  the math was. Display equations have `display="block"` and no parent.
- **Equation numbers** ("2.4", "(2.4)", "[2.4]", "Eq. 2.4") of a display equation are in
  `equation_label`, as printed, and are not in its `text` or `bbox`, so they are not in
  the crop the model transcribes. A symbolic tag ("(*)", "(†)") is one too. A number at the
  right edge is never a paragraph of its own: it is the label of the equation piece on its
  baseline (in either column of a page with a two-column section below, the same column
  first), or, when it is centered between lines, of the nearest piece within 2.5 font sizes
  that has no number yet and is not level with a numbered line; a number that fits none
  stays text. A number of one column does not go to the math of the other when text of its
  own column stands within two lines of it. Equation pieces that
  `display_grouping` does not join keep a number each, or none. A bracketed number set less
  than a font size from its equation (a `multline`'s, `CLOSE_NUMBER_GAP` at the least) is cut
  off when it ends at the right edge of its column's text and what stands before it reads as
  math on its own (`_closes_display`); elsewhere it stays in the equation's text. A number
  that stands under the right end of its equation, not beside it, is its label when nothing
  stands between them (`_number_under`, `_line_over_number`), and a line of math with a few
  words in it ("... for every n > 0") is a display when such a number stands beside it
  (`_promote_numbered_lines`). A number left of the math, at the column's margin
  ("(10.18)" in front of the equation), is not read as a label yet.
- **List items** are `Kind.LIST_ITEM` with `list_marker` (as printed: "•", "a.", "(iii)",
  a drawn bullet reported as "•", "◦" or "▪"), `list_ordered` and `list_level` (1 = top);
  `text` does not include the marker.
- **Boxes** are PDF points in display space: the page's `/Rotate` is applied (and the turn of
  a page saved sideways with none, "A page saved sideways" below), so a
  landscape page reports `(792, 612)` in `page_sizes` and `crop` uses the boxes as they are.
  The origin is the top-left of the CropBox; content outside it is ignored and figure boxes
  are clipped to the page.
- **`reasons`** are short content-free evidence strings (`"math font"`, `"fraction bar"`,
  `"larger font"`, `"raster image"`, ...). They never contain document text.
- **Text** is copied from the PDF with whitespace collapsed and invisible characters
  (zero-width and control characters) removed. Text nobody sees on the page is never
  content: render mode 3, 5 % opacity or less, under 2 pt, in the color of what is behind it,
  or painted over by an image or a filled shape ("Hidden text and page types" below). The inline-marker brackets `⟦` `⟧` (U+27E6/U+27E7) are replaced by the
  look-alikes `〚` `〛` (U+301A/U+301B) in every piece of document text, so a PDF cannot
  fake a marker: every marker in an element's `text` is a real inline child of it. Private-use
  codes of symbol fonts (Word's Symbol bullet U+F0B7 is `•`) are mapped by `font_codes`, by font name.
- A `"Table N ..."` or `"Figure N ..."` line is a caption by one pattern, `core/captions.py`
  (N Arabic or Roman; "Table 2 shows that ..." is not one), shared by the table step, the
  figure step and the HTML check.
- Figures carry the nearby `"Figure N ..."` text in `caption` and any labels drawn inside
  or right next to them (axis titles, tick labels) in `text`.
- **Tables** (since pipeline 0.4.0) are `Kind.TABLE`: `table` is the grid built from the
  PDF (`TableGrid`, source "local"), `caption` the `"Table N ..."` text printed above or
  below it (also in `table.caption`), `text` the cells' text row by row, and `bbox` the
  table without its caption. `reasons` say how it was found and built (`"ruled grid"`,
  `"aligned text grid"`, `"box extended along rules"`, `"SLANet-plus grid"`,
  `"drawn cell grid"`, `"PyMuPDF grid"`, `"continued from previous page"`). How tables
  are found: [tables.md](tables.md).

## Where the code is (`pipeline/layout/`)

The layout step is a package of one module per stage, in the order a document is read. A module
imports only from the ones above it in this list, so there is no import cycle and a stage can be
read with the stages before it. `pipeline/layout/__init__.py` holds the package docstring (how a
page is read) and re-exports every name that code or a test outside the package uses
(`analyze`, `crop`, `release`, `Layout`, `LayoutError`, `REASON_*` and the private helpers the
tests import), so `from readablestem.pipeline import layout` and `layout.analyze` work as before.

| Module | What it holds |
|---|---|
| `context` | `_Document`, the document context (below), the result types `Layout`, `LayoutError`, `PageStatus`, and the page guard that keeps one failing page from stopping a document |
| `access` | opening the PDF under the one lock, the document cache, the probe process (see "Untrusted PDFs"), the content budget, `crop`, `crop_line`, `render_page`, `page_ink`, `release` |
| `glyphs` | `_Char`, font classes and styles, character classes, text cleaning, `glyph_style` |
| `shared` | the types the stages hand to each other (`_Row`, `_Block`, `_Run`, `_Figure`, `_Region`, `_Path`, ...) and the few helpers more than one stage needs |
| `entries` | where a list of entries (an index, a glossary, key terms, an outline) starts a new entry, asked only with the region tree on |
| `rows` | lines merged into rows, form fields, ligatures, right-to-left text, stacked fractions, scripts, big operators, reaction arrows; `line_glyphs`, `page_glyphs`, `page_paint` |
| `drawings` | the vector paths of a page: lines, turns, boxes, radicals, rules, axes |
| `figures` | raster images and their budgets, vector clusters, diagrams, plots, labels and their owners, page furniture bands |
| `furniture` | margins: running heads, page numbers, margin notes |
| `order` | columns, the gutter, full-width lines and reading order |
| `inline_runs` | inline math, math pictures, chemistry and nuclides |
| `display` | display equations and the number beside them |
| `lists` | list markers and levels |
| `blocks` | rows grouped into paragraphs, headings, list items, captions, code, contents lists, entries, with their text |
| `tables` | table claims: the cells of a found table taken out of the page, their math and key terms |
| `regions` | the region tree (`REGION_TREE`, off): the page cut into regions before its lines are made, and the reading order taken from it ("The region tree") |
| `page` | one page, from its rows to its blocks (`_page_blocks`); figure labels, pen marks, drawn rules, footnote marks |
| `joins` | passes over the whole document: run-in labels, numbered headings, paragraphs that go on over a page break |
| `bold` | bold-letter runs and glossary entries |
| `notices` | what was left out or could not be read |
| `elements` | blocks turned into `Element`s, with contents and footnote links |
| `document` | `analyze`, `_analyze_document` (the steps in order), slide decks, heading levels |

A step that runs for one page is called by `_analyze_document` (in `document`), so a test that
makes a step fail patches it in the module that calls it, not in `layout`.

**The document context.** What is known about the document being read (made by TeX, a slide
deck, how it spells its compounds, whether its displays are grouped) and the ink and detector
budgets are one `_Document`, made by `analyze` and passed as `document=` to the steps that read
it. Nothing about a document is kept in a module, so two documents read at once (one in the
middle of the other, through the page-math hook) cannot change each other. `_PLAIN`, the
document with none of these set, is what a caller outside a read gets by default. `_LOCK`
guards PyMuPDF, its warning store and the caches, nothing else.

**The page trace.** `analyze` times each step on each page (`step_facts.PageTrace`, on
`_Document.trace`) and returns the seconds as `Layout.page_steps`: `read` (text, drawings and
page type), `tables` (the table search), `rows` (rows, labels and the rows' classes),
`figures` (figures, fragment figures, pictures of equations, drawings left over, plots),
`equations` (grouping the rows of a display), `order` (reading order and column
continuations), `blocks` (rows to blocks, captions, lists) and `elements` (blocks to
`Element`s). The conversion adds `crops` (cutting the page's pictures for the model steps)
and carries the list on `StepFacts.page_steps`; `eval speed` reports it. Steps that are done
over the whole document (the margin filter, list markers, label keys, the notices) belong to
no page and are in none of the seconds. The trace is not stored with a job (the web worker
keeps the calls, the stages and the unfinished steps) and decides nothing: it reads
the clock and adds up. The second layout run of the page math pass is not in it.

## How a page is read

The order and the regions of a page are found by rules, with no model and no learned layout
reader, neither to find them nor as a second opinion
([ADR 0010](decisions/0010-reading-order-rules-first.md): the figures and what would change it).

0. **Tables first.** On every analyzed page the data tables are found
   (`pipeline/tables.py`, [tables.md](tables.md)) before anything below: their lines,
   rules and `"Table N"` caption leave the page, and each comes back as one TABLE element
   placed in reading order like a figure. So a table's numbers are not a display
   equation, its bold header cells not headings, its drawn grid not a figure, and its
   columns not text columns. This happens whatever `FEATURE_TABLES` says; with the flag
   off the table step keeps the table as an image with a required decision. A grid of
   prose (one column, or a term and its long definition) is not claimed and is read as
   before. In a TeX-made PDF a cell that is one formula by its glyphs (the rules that
   find inline math in prose, applied to the cell alone) is also an inline equation
   element of the table, `TableCell.equation_id` naming it (`_cell_maths`,
   [tables.md](tables.md) "Math cells from glyphs"); `run.py` drops these when the flag
   is off.
1. **Characters.** PyMuPDF's raw text dictionary with `TEXT_ACCURATE_BBOXES`, so glyph boxes
   are tight. Without it, a fraction's numerator and denominator boxes overlap the main
   line and cannot be told apart. Ligatures are expanded. A glyph the file's own table does
   not decode is read by the name the font gives it (`pipeline/glyph_names.py`, "Glyph names"
   below), then, for a font with no usable table, as what the font draws
   (`pipeline/font_codes.py`, "Font code tables" below); one that stays undecoded is a gap mark.
   **ActualText** is chosen once per page (`page_type.actualtext_flag`). A file can give a span
   the text it means in place of the glyphs' own (Chromium does for Hindi conjuncts and CJK;
   a tagged PDF does for a figure's labels, and a scan's hidden layer may). A page that carries
   it is read both ways, with and without (`TEXT_IGNORE_ACTUALTEXT`). It keeps ActualText when
   the two readings say the same words (they differ only in spaces, hyphens, format
   characters or composed forms: a hyphen at a line end that the file marks as a soft one
   stays soft), and when the reading without it holds more U+FFFD, Kangxi radicals (U+2F00 to
   U+2FDF) and private-use characters. It is read without it otherwise, so the printed labels
   a replacement dropped are read. A page is never mixed, a span at a time. A page with no
   ActualText is not read twice and does not change. Every reader of a page's characters
   (rows, `line_glyphs`, `page_glyphs` for the text pass, the page type, the page labels, the
   tagged headings, the bold letters of equations, the page audit's words, and the review
   probe's region text and glyph marks) adds the page's flag to its own, so no two steps
   disagree about what the page says. The choice is kept on the open document. A typed page
   that is read without it gets the optional notice `actualtext_ignored` ("Worth a look", one
   per page): the file says other words for part of the page than the ones printed, and the
   printed words were used. A page that keeps ActualText gets none, and neither does a page
   kept as an image.
2. **Running headers, footers and other decoration.** A line whose center is in the top or
   bottom 7 % of the page is dropped when its text, lower-cased with digits replaced by
   `#`, appears in the same band on at least half of the reference pages (and at least
   two), unless it stands out and changes: a line at least 1.15 x body size, or bold and
   within 0.5 pt of the body size or larger, whose exact text differs between pages
   (`Problem 3 (15 points)` at the top of each page) is
   content and stays. A fixed text (`PHYS 101 Lecture Notes`) goes whatever its size.
   A running head whose words change with the section (a textbook's chapter title on the
   right pages and the section title on the left ones, with the page number beside it) is
   found by its place instead, in the top or bottom 10 % of the page (textbook heads sit
   lower than the 7 % of an exact repeat): a line that sits at the same height (within 2 pt)
   and size (within 0.5 pt) on at least half of the left pages, or of the right pages (and at
   least two), and is apart from the body (smaller than it by 0.5 pt, italic, or another font
   family) goes when the line is a head's or a foot's by more than its place: a whole
   number on it goes up by the pages between, from one page of the place to the next, on
   half of those steps (the page number; an excerpt joined from parts of a book jumps at
   each join), or the same words with letters in them stand at the place on two pages. The
   place alone would also take the last line of a footnote, a one-line footnote or a slide's
   source line, which sit at one height in small type on page after page. So a row less
   than 0.6 of its height from a kept row next to it towards the middle of the page stays
   (it goes on from the text), and a row that opens with a character at most 0.85 of its
   size (a note's mark) is never a head. A repeated heading (`Solution`, `Example`) is bold
   and as big as the body or bigger, so it is not apart from it and stays; a first line of
   the body in the band stays too. A bare page number that shares a line with a line that
   stays (a table cell) is not dropped, and a number set at heading size is a chapter
   number, not a page number. A head or foot that no other page repeats (a one-page file, a
   page cut from a journal) is found by its shape (`_MarginFilter.by_shape`, since
   2026-11-19): at an edge of the page, up to 4 lines that stand together in the outer 12 %
   (each less than a line from the one before) and then a line's height clear of the rest of
   the page, none set as a heading (1.15 x body or bold), one of them with a page number at
   its start or end (digits, or `Page 3 of 9`; a bare year is not one). A page number alone on
   its line is taken on a file of any length (a number 90 % of the way down an A4 page is
   outside the 7 % band); the lines with words, only when the reference holds one page, since
   on a longer file their words repeat and the repeat rule has them, and only with a number
   that says it is a page's (`Page 3`, `Page 3 of 9`, `3 of 9`) or with every word of the
   stack set at least 0.5 pt smaller than the body text, as a running head is: the name line
   and the points of a one-page quiz end in a bare number as a running head does (`Points:
   ... 25`, `Total marks ... 50`), but they are set as the body is, and they stay. A journal's
   head with a bare page number beside its name, in type smaller than the body, goes; a
   footnote above a bare number stays when one of its rows is eight words or more that open
   in lower case (the rest of a sentence from the page before). A head with no page
   number beside it (a draft stamp, a title) stays; a table's last row or a note has a line
   under it a line away, so it never stands clear. What goes is counted as `furniture` left
   out, as on a longer file. The
   reference pages are the analyzed pages plus their neighbors, up to 24 pages, so
   analyzing one page of a long PDF still recognizes its header. Bare page numbers (`7`,
   `Page 3 of 9`, `iv`, `- 12 -`) in the band are always dropped from the text (the page markers of
   the output still carry them: `pipeline/page_labels.py` reads the PDF's page labels and the
   numbers printed in the margins separately, docs/output.md "Navigation"). Figures in the band
   (raster images, and drawings clustered within the band) whose box, rounded to a point,
   repeats on half of the reference pages are logos and are dropped too. Watermarks go:
   diagonal text at least 2 x body size, and rotated text repeated at the same place on
   half of the reference pages. A figure takes back the dropped rows that label it
   (`_figure_labels`, since 2026-10-12: the silent-loss sweep found a slide's x tick
   labels in the footer band dropped as page numbers, an axis title repeated under each
   chart of a deck dropped as a running footer, and a repeated rotated y-axis title
   dropped as a watermark). A dropped row is a label when it is at most 40 characters,
   not diagonal, and the one label rule below takes it (`_label_owners`: within one font
   size of the figure's nearest stroke, or of a label the figure holds already; a rotated
   row 2.5 times as far, up to 40 characters). Bare numbers count only
   two or more on one baseline (tick labels), or alone when level with a figure (the
   axis title `v` at the top of a full-page plot's y-axis, which the page-number rule
   reads as a roman numeral) and the same band of the other pages holds no changing
   number there (a page number beside a figure that reaches into the band stays a page
   number; since 2026-10-12); other text only when it is beside a figure on every analyzed page it was
   dropped from (`_label_keys`), so a running footer that also runs under pages of text
   stays dropped. The rows taken back are the figure's labels, in its text and its crop
   (before, tick labels 6 pt under the x-axis were read as paragraphs of one digit each). A short footer repeated under a
   chart on every page of a document is read as text (noise, not a loss).
3. **Columns.** Narrow lines (at most 55 % of the text width) are projected onto the x-axis.
   A gap of at least 8 pt in the middle half that no narrow line touches (or at most two,
   so a centerd title does not hide the columns) is a gutter, if each side has at least
   three lines sharing a left edge and the typical line on each side is at least 30 % of the
   text width (two table columns are not text columns). Lines for that test are boxes that
   overlap vertically by half the smaller height: TeX splits a line with inline math into
   pieces whose bottom edges differ, and measured piece by piece a full LaTeX column
   looked like a column of short cells. Margin-band lines are ignored here. A first page
   with a head of centered lines (title, authors, affiliations) across both columns has more
   lines across the gap than that, so when the page as a whole has no gutter the columns are
   looked for under the head: the narrow lines below each run of lines across the page (up to
   eight runs, the top of the page first), the first run that gives a gutter whose thinner
   side holds at least 0.4 of the lines of the other (`MIN_HEAD_COLUMN_BALANCE`: two columns
   hold about as many lines; a column of steps beside a few lines is a ruled table on a page
   of one column, and is read row by row; and at least four lines), and the two columns run
   unbroken (`order._columns_run`). An exam's questions, each a line across the page or a
   short line at the left with its options two by two under it, are no columns: a line across
   the page that has lines of both sides above and below it breaks them (a note across the
   page under the columns, whose last line is short and sits at the left, has lines of one
   side below it, and does not), and so do breaks in the thinner side (a step from a line's
   top to the next one's over 1.9 line pitches, `MIN_BREAK_PITCHES`, with one to three lines
   of the other side or across the gap beside it: a short question that the gap runs through
   stands in the break too) that cut it into runs of fewer than five lines on average. Measured
   top to top, a question set at the pitch of its options with no space between questions
   skips one line, 2 pitches, at any leading. A column page of the development set has one
   such break in 10 lines on its thinner side, just enough to keep its columns, and a step of
   1.78 pitches where a heading's space stands beside one line, which is no break; one with
   three breaks in 28 lines keeps its columns too (`ax_astrophga_240306409`, its tenth page,
   index 9). A step in the other side's own lines over 1.9 of their line steps, with lines of
   the thinner side beside it but none of its breaks within a line of it (a figure or a
   heading's space in one column only), keeps the columns when there are two or more such
   steps and at least half as many as breaks: an exam's questions cut both sides at once, a
   section line set apart above the first question or under the last has no line of the
   thinner side beside its space, and one option that runs on to a second line makes the other
   side skip a line once. The pitch is the median step of
   both sides' lines, each side measured on its own: an exam's thinner side skips a question's
   line at every other step, so its own median lies between one pitch and two. A hole under the thinner column's end, one within
   a line of a break of the thinner side, a hole only in the thinner column, fewer holes than
   that beside the breaks, and a heading's space under 1.9 steps leave the page to the breaks
   (read across, as before), and so do columns whose thinner side is set at about twice the
   other's pitch (a spaced list beside running text: the pooled pitch finds a break at every
   line).
   Left as it is: a longer exam (six questions; five with options that run on to a second
   line; four with three rows of options) is taken by the gutter of the page as a whole,
   before the runs are asked about, since a few lines across it are allowed for every ten
   lines; an exam of four questions with two options that run on makes two holes beside three
   breaks and keeps the columns; an exam with one row
   of two options under each question has no break in its option columns, and one with ten
   options under one or two questions too few; all of these are read as columns (left options
   first). Not built, since no real document shows them (checked 2026-11-22 on the 81 public
   PDFs of the development set and `data/`, where no page reaches the shapes; on the 996
   analyzed files and 73 replayed uploads of the corpus a fix for them changes nothing): a
   label test (a pair of option lines level with each other, "(a)" beside "(b)", on 0.4 of the
   thinner side's lines) reads all 127 exam probes of reviews 18 to 21 question by question
   (95 before) and a run length of four lines instead of five reads 96 of the 117 column probes
   as columns (70 before), with none made worse; the patch is kept outside the repository. What it
   cannot tell apart on spacing alone, and so would still read wrongly if a real page showed it:
   columns whose thinner side is set at twice the other's pitch (the steps of an exam whose left
   option runs on), and columns of paragraphs of two or three lines (the groups of an exam's
   options). The lines of the head are
   then read in the segments the lines across the page make (`order._find_gutter`).
4. **Rows.** PyMuPDF splits one visual line into several "lines" (justified text, font
   changes in inline math). Fragments on the same baseline are joined, never across the
   gutter. Small sub/superscript fragments are folded into their line as one block, after
   the glyphs that start before their right edge (a sub/superscript lands after its base,
   a small fraction's denominator after its numerator). A fragment is a script only if
   every glyph in it is small: a denominator "v" with a small "avg" is not, and folding
   it had interleaved it with the line above ("dis v ta avg nce"). A fragment of at most
   four glyphs with a big operator (∫ ∑ ∏ √) joins the line it sits in, before the
   first glyph to its right: TeX sets the sign of an inline `$\int_0^1 x^2\,dx$` on a
   baseline of its own, and left alone it became a display equation of its own.
5. **Reading order.** One column: top to bottom, left to right within a line. Two columns:
   left column, then right column, segment by segment. Items that cross the gutter are
   full-width and close a segment. A narrow item is full-width too when it sits at a
   section break across both columns (a left-aligned section heading between two-column
   sections): the other column has nothing beside it (gaps under 2 x body size are
   bridged) but continues below it, the item stands apart from its own column's text above
   (a gap of at least 0.8 x body size), and both columns resume below it at the same height
   (within 0.5 x body size). A heading inside one column that happens to sit beside a large
   gap in the other column (space before a Word Heading 1, around an equation) fails the
   last test and stays in its column, so the other column's paragraph is not cut.
6. **Runs.** Rows in reading order are grouped: a row continues the paragraph above when the
   font size matches (within 1 pt or 12 %), the baseline step is at most 1.3 x the typical
   body line pitch, it starts within the paragraph's horizontal span, it is not a first-line
   indent after a short line (LaTeX paragraphs), and it does not start a list item or a
   `Figure N` caption. While a run holds only its first line, and that line is indented,
   runs to the right edge of its column (`_RightEdges`: the end that a quarter of the
   column's lines of that size reach), is no list item or caption and does not end a
   sentence, the next line may start up to 4 em to its left (`INDENT_REACH`): the second
   line of a paragraph that opens with a typewriter's tab is not a new paragraph. Two lines
   centered on one another (an author over an affiliation) and a line that opens with a
   reference number ("[20]") are not that. A
   two-line paragraph whose second line falls short of the first stays cut. A row continuing a list item never starts left of the item's
   marker (so the paragraph after a list is not swallowed by its last item). Words set in
another style that run on over the end of a line (a bold phrase that ends a list item's
first line and goes on) are cut off the block by the style change and joined to the item
again, as for a paragraph (`_merge_style_splits`; only a block that is a paragraph is joined,
so a heading is not, and a line that starts a block is not).
   An entry of a glossary, an index or a bibliography is a block of its own (SW34b), by three
   signals that need no heading over them: a lead term (bold, italic or small capitals, at a
   line's start at the paragraph's left edge, ending in a period or a colon, after a line that
   does not end in a lead term or run full to the right edge in mid-sentence; `_lead_end`,
   `_starts_entry`); a single capital letter alone on a line, bold or larger than the text, with
   the line right under it (within a line or two) starting with that letter, three or more different letters set that
   way in one size and typeface through the document (`_letter_rows`: a heading one level
   under the section's, `REASON_LETTER`; the test reads the line under the letter and not the
   order of the columns, so it holds on an index of three columns and never takes the panel
   labels of a figure); and a hanging indent shared by three or more entries in a row, each a
   line at the left edge with lines that start 1 to 4 em further right, the same distance for
   every entry, the lines that go on running to the right edge or stopping where the next
   line's first word no longer fits (`_hanging_entries`, `_flows`; a contents list nested by
   indent stops where each title does, so it is not one). An entry of one line between such
   entries, at their left edge and short of the right edge, is an entry too (`_one_line`). A one-line entry followed by an indented paragraph starts a paragraph of its own
   (`_continues`). Every such start is one question, `_starts_block`, asked last by `_continues`
   (SW34): the entry and the "Keywords:" line above, and three more that the line geometry does
   not show. A raised digit or sign (`*`, `∗`, `†`, `‡`, `§`, `¶`) at the line start, in type
   smaller than the body, after a line that does not go on in mid-sentence, is a note or an
   affiliation of its own (an isotope's mass number in running text is set in body size and is
   not one). A theorem's label ("Definition 2.", "Example 5:", "Proof.") starts a block after a
   line that does not go on in mid-sentence, or when it is bold or italic over a line that is
   not; a numbered label with no stop ("Remark 5.1 It ...") starts one only in bold or italic.
   "Theorem 3.6 we show" and "see Example 5 and" go on. (A speaker label such as "TEACHER:" is
   not a signal: in the development material every speaker turn that joined was already cut by
   the lead-term rule.) The next number of
   a column of numbers ("4 Receive ...", "5: while ...") starts one after a line that stops
   short of the right edge, the steps of an algorithm or the lines of a listing; the line
   numbers in the margin of a passage do not (a number set apart from its line, in smaller
   type or more than 1.2 em from the text, with a sentence that goes on in lower case across
   it and no step word such as "end" or "do": `_margin_number`). A number after a line that
   runs full starts one only when that line is the whole run so far (the affiliations of a
   paper, one line each, numbered 1, 2, 3 to the margin); inside a run of lines a number
   after a full line is the next line number of a passage. Without the page's line
   ends (`edges`) only the first group and the note and theorem signals are asked. A change
   between bold and regular, or into or out of heading size, ends a run. A single
   display-equation row that starts at the paragraph's left margin at the usual line
   pitch is the last line of the sentence above (`... exactly one solution,` / `x =
   A⁻¹b.`), not a display equation: its math becomes inline equations of the paragraph. PyMuPDF's own blocks are not used: they glue a caption to the next
   paragraph and a fraction's denominator to the text under it.
7. **Paragraph text.** Rows are joined with spaces. `veloc-` + `ity` becomes `velocity` (a
   letter, a hyphen at the line end and a lowercase start); `Newton-` + `Raphson` keeps the
   hyphen without a space; so does a compound whose pieces are words when the joined word
   is not (`well-` + `known`, `cross-` + `section`, `self-` + `consistent`), and a hyphen
   after a single letter (`x-` + `ray`). The list is `pipeline/data/words-en.txt.gz`
   (63,849 lower-case English words from SCOWL; source, license and SHA-256 in
   `pipeline/data/README.md`) and the rule is `pipeline/hyphens.py`: the joined word is a
   word, or neither piece is one (`eigen-` + `vector`, a technical word the list lacks):
   the hyphen goes. Soft hyphens disappear. Whitespace is collapsed and zero-width
   and control characters removed. A paragraph cut by a column break (first part does not
   end a sentence, second part starts lowercase at the top of the next column) is rejoined.
   The document's own spelling overrules the list (`hyphens.document_spellings`, 2026-11-02):
   when the same two pieces are written with a hyphen inside a line elsewhere in the
   document (`eigen-vector`), the hyphen stays; when the joined word is written whole
   elsewhere (`antiderivative`), a break inside it is soft hyphenation and the hyphen
   goes, even for two words whose join the list lacks. A word cut at a line end is not
   counted, so a break never vouches for itself.
   Two lines that end and start in a script written without spaces (Han, Hiragana, Katakana,
   Thai, Lao, Khmer, and their full-width punctuation) join with no separator
   (`blocks._line_break`).
   A break that falls inside one token leaves it whole (`hyphens.joins_inside_token`, for
   paragraph text, headings, captions, list items and the cells of a table): a digit, a
   hyphen and a digit keep the hyphen with no space (`(2012-08-` / `17)`); a line that ends
   in `/`, `.`, `_`, `-` or `=` inside an address (a scheme or `www.` start, or a `/` or `@`
   in the word) goes on from it (`project/` / `memspectrum/`), with the hyphen of an address
   never dropped; a full stop joins only a host that still lacks its suffix (`www.example.` /
   `org`, with a lowercase start), so a sentence that ends with an address keeps its space
   and so does an address with a path that ends in a full stop; a non-breaking hyphen
   (U+2011, also U+FE63 and U+FF0D) after a letter or digit is a compound that must not
   break (`easy-` / `to-use`), kept as written. Otherwise the address test reads the word before the
   break. The file's own links settle the rest: where two link annotations of one address
   (one per line, as hyperref writes them) run across the break, the words under them are
   the address, and the break joins with no space whatever its last character is
   (`hyphens.link_cut`, read per document by `document._link_cuts` into `_Document.link_cuts`,
   and so used only by the paragraph joins: not yet by the cells of a table, and not for one
   annotation drawn over two lines). Only printed text that is an address counts (it holds
   `.`, `/`, `@` or `:`; a sentence sign the second annotation covers after the address, as in
   "Academy." or "tics.2022.11.009)", is left out, and so is a full stop before a capital,
   "Dr." / "Smith"): a named link over two lines ("Smith" / "Jones", or "Khan" / "Academy"
   under khanacademy.org, with or without a full stop under the link) is not, and its break
   keeps its space, here and wherever else those words break. A middle line of a three-line
   address that prints none of those signs keeps its space.
   Inline math that wraps from one line to the next is one equation (`_wrapped_inline`): a
   relation sign ends the first line or starts the second (`x + y =` / `z + w`), or a plus,
   minus or product does and its half is longer than one token. Two ions (`Na+` / `Cl-`),
   two variables side by side and two formulas with a word between stay separate. The
   joined equation's box is the union of both lines, so its crop also shows the words
   either side of the break.
   A paragraph (or list item) that goes on over a page break is one element
   (`_join_page_splits`, `Element.reasons` "joined across pages"). The tail is the last block
   of the body flow of a page (`_flow_tail`): a picture, a table or a caption at the foot,
   footnotes in smaller type, and a lone page number the margin filter left in the body (a
   footer set low on a tall page) are skipped, but a display equation or a heading is the
   tail itself, so nothing joins across it. The head is the first block that is not a
   picture, a table or a caption (`_flow_head`), so a figure on top of the page does not hide
   it. `_goes_on` is the one test: the tail does not end a sentence, the head is a paragraph
   without a marker that starts with a lower-case letter and has no first-line indent, and
   the two share size, typeface and weight. They stand at one edge (`_same_edge`): the same
   x, or each at the text edge of its own column (`_text_edges` of its own page, so odd and
   even pages with other margins agree, and a two-column page that ends in the right column
   goes on in the left one; across columns the tail needs two rows or more, since a
   one-line tail across columns is too often a license line). A last line that stands in by
   a first-line indent (the one line of a paragraph, or the first line of the last paragraph
   of a block) counts as at the edge. A tail of two lines or more whose lines all stand
   twelve ems or more in from its page's text edge is in the other column of a page whose
   columns were not told apart (a first page with a one-column head over two columns), and is
   compared like a tail across columns; an index's lines at several x do not qualify. A tail
   whose block began in the other column has no edge of its column's: its last two lines at one
   x are its column's text. The head may also start with something other than a lower-case
   letter when the tail cannot end where it does: the tail ends on a comma or a semicolon
   (a head that starts with an acronym or a name), or the head starts with a citation shut by
   punctuation ("[12]."), the end of a sentence. A head that is a "Table N" or "Figure N"
   caption never joins (a caption set at the top of the page under a comma tail). For a list item the head stands at the item's
   text, not at its marker. When the place test is in doubt the break stays: a wrong join is worse
   than a cut. The element keeps the
   first part's page and box, so a link or a color in the second part is not placed on the
   text (the text pass aligns against the first box), and an inline equation of the second
   part keeps its own page for its crop. A hyphen at the page break is repaired like one at
   a line break. A paragraph that starts with math, a break onto a column that is not at the
   text edge of the next page, and a head at a list item's marker rather than its text
   are not joined. A list item that is joined no longer ends the list: the next item goes
   on at the same level. `Element.last_page` is the 0-based page of
   the last part (None when it ends on its own page). The assembler writes the page markers
   of the pages after `page` up to `last_page` right after the joined paragraph (`marks_after`),
   never inside it, so the markers stay in order and continuous (Page 1, Page 2, Page 3) and the
   next element on the last page gets none; `pipeline/combine.py` maps `last_page` with `page`.
   Pipeline 0.5.1: the joins shift element ids after them, and a heading's words change
   where it had math-italic letters. The review carry (`web/carry.py`, `carry_edits.py`)
   matches by content (a heading by its words, size and weight; an equation by its crop; a
   paragraph's edits by what it said before them), never by id, so a revised upload is safe:
   a decision on a heading whose words changed, and an edit to a paragraph that is now two
   joined ones, are not carried and are shown as "decide again" or counted in
   `EditsCarried.dropped`, which the instructor is told about. Results from 0.5.0 load
   unchanged.

### The region tree (`REGION_TREE`, off in code and in production)

`pipeline/layout/regions.py` (and `entries.py`). The reader above finds one gutter for the page,
merges the page's fragments into lines and then orders the lines. A page with three columns, a
notes column, text on both sides of a picture or a boxed sidebar has no one gutter, so its lines
are read across the columns. With `REGION_TREE=on` the page is cut into regions first and read
from the tree; with `off` (the default) none of this code runs and the output is byte-identical
(a hash of `layout.analyze` over 996 PDF-and-variant pairs: 0 differences).

**What a region is.** A node of a tree, with four kinds. A `leaf` holds lines read top to bottom
(and, in a leaf, baselines are merged: a line never runs across a column, a picture or a box,
because merging happens only inside one leaf). A `stack` is horizontal cuts: its children are
read from the top. `cols` is one vertical cut: its children are read left to right, with a notes
column last. A `grid` is a set of labeled cells read in label order. The items cut are the
unmerged line fragments, the page's raster pictures (they occupy space), the rectangles drawn
round text, and the tables found on the page (`_claim_tables` takes their cells and captions out
of the fragments before the rows are made, so a table is one item with the box of its cells and
its caption, like a picture). A vector figure is not an item: its drawings are read later. It was
tried as one (the reader's `_vector_figures` boxes, cut like a picture) and measured on the 267
development pages and the 91 corpus gold pages: no page's reading order changed, and one page
(`ax_qbionc_240307089` page 8) was faultless again, because two lines were no longer merged
across the figure. On generated pages with a drawing between two columns of short lines it read
worse (the rows of the two columns alternated, as they do with the tree on for a narrow raster
picture there), so the code was not kept. A figure between narrow columns has to be cut like a
column first. Wave 4 (2026-11-22, O-013) did not build it again: that count stands (0 pages
changed, 1 page better, a worse read where a drawing stands between two columns of short
lines), no page asking for it is known, and the tree is off, so the item would change
nothing in production.

**The cuts.**

- *Horizontal.* Items are grouped in bands (items that overlap vertically; an item that stands
  over a shorter one's whole height overlaps it by a fifth of the shorter one's height, so a
  picture that starts a little below the first line beside it is in that line's band). A run of bands that
  keep one set of white channels is a segment. A band that does not (a line that spans the
  region, a blank band) starts the next one (rule 1). A gap of 2.5 line pitches closes a segment
  only when the band below is side by side (rule 6).
- *Vertical.* In a segment, a white channel of at least 7 pt (`CHANNEL_PT`) with at least three
  lines each side (two on a slide) that have a line of the other side at their height (the pieces
  a formula cuts a line into are one line), and a line of three words or more, cuts it in two.
  Only the channels that count are counted: the numbers in a margin are a channel with no
  words, and a segment with more than two channels that count is not cut. Each side is cut again,
  so a page has at most three columns. A column at most 0.35 of its neighbor's width
  and at most 0.6 filled is a notes column: it is read after the body block it stands beside (at
  block level) or after the whole body (at row level).
- *Boxes (rule 2).* A rectangle drawn at least 40 x 18 pt and less than 0.8 of the page, holding
  at least two lines and crossed by none, is cut as a region of its own, ordered inside itself
  and read as one item. Touching filled pieces are joined. With text beside it, it is read at
  the first block break below its bottom edge.

**The rules, in the order they run.** The numbers are the plan's (WS15.1).

1. Flat bands: one-column bands continue a one-column segment.
2. Box regions (above).
3. Section breaks in a column pair: `order._section_break` with `_ColumnExtent`, captions in the
   extents, items taller than 2.5 body lines skipped, one line of large type allowed to be as
   wide as its column; a short item directly above a spanning item opens it.
5. Footnote zones: lines at most 0.92 of the body size at the foot of a column, at most 40 % of
   it, are read at the end of the page.
6. Wide gap (above).
8. Options in label order: `(A) (B)` over `(C) (D)` and `(A) (C)` over `(B) (D)` read A, B, C, D;
   a grid of labeled cells is a `grid` node.
9. Index pages (`entries.starts_entry`, asked by `figures._continues` with `_Context.region_tree`
   on): on a page of 20 or more lines, half ending in a page number, median line seven words or
   fewer, a line at the entry margin after a line that ends in a number starts a new entry.
10. Entry lists: a line at the first line's edge starts a new paragraph when the run hangs (a
    glossary: the line before it ends short of the right edge by a size and a half, because a
    line that runs to the edge is a paragraph going on, under a heading or the last page's
    tail, with its first line indented), or when both open with a section number or a bold
    term.
12. Numbered runs, the reordering only: where a run of six or more numbered labels is out of
    sequence, the column-by-column reading is used if it makes the run consecutive.

Rules 4 (a foot heading), 7 (the question rule) and 11 (labels given back to the text) were
built, measured and taken out: leaving each out changed no page of the development sets, or
adding it changed none. Rule 13 was not ported, and rule 12's page check was not built: of the pages still wrong with the
tree on (1 of the corpus gold pages made wrong, below, and none of the development pages), none
asks for them.

**How it is wired.** `RegionTree` stands where the gutter stood: `order._Ordering` is a protocol,
`Gutter` is `tuple | _Ordering | None`, and `order._reading_order` hands the boxes to
`gutter.order(...)` when it is not a pair. `page._prepare_rows` returns the tree when
`_Document.region_tree` is set; the row-level and the block-level calls differ (`blocks=True` only
on the final block-level order of `_page_blocks`). Row `column` names are the tree's
(`L`, `R`, `LL`, a `#k` suffix for a box, `~index` on an index page). The setting is
`Settings.region_tree` (`off` or `on`), passed by `run.py` and the say-once report as
`layout.analyze(region_tree=True)`. The blanks (`_mark_blanks`) are marked with the page's
gutter as with the tree off, so a rule that heads one column is never the blank of a line in the
other; the tables found on the page (`_claim_tables`) are handed to the tree as boxes.

**Measured** (`eval order --set layout-dev-1` and the corpus gold pages, no model, scored on
the elements; 2026-11-20, 0.5.14): 267 development pages, 137 right off, 156 on (19 made right,
none made wrong), 71 faultless off and 79 on (9 gained one, 1 lost: `ax_qbionc_240307089` page 8
has two lines merged across a gutter, a fault the page did not have off); 91 corpus gold pages,
71 right off, 83 on (13 made right, 1 made wrong), 37 faultless off and 42 on. The page made
wrong is `e1_mc_columns` page 1: the last line of a block of choices in two columns has a mark at
the right edge, and its text reaches the gutter, so the band shares only the 9 pt that are left of
the gutter's 30 and the line joins the choices' columns. Keeping an overlap only when half of the
narrower channel is left fixed it and made four development pages wrong (a 10 to 18 pt channel
cut from 21 to 65 pt is a gutter on those pages), so it was not built. Rule 10's hanging test
(the line before the first ends a size and a half short of the edge) mended no page's status:
it took 10 splits and 18 cuts out of the development pages (a paragraph whose first line is
indented, under a heading or the last page's tail, was read as a glossary), and it costs one
glossary entry whose last line runs to the edge (`sm_glossary_a` page 1: 157 pages right before
it and 156 after; the page is wrong off as well). The earlier measurement (0.5.13 pages, 141 right
off, 163 on) is not comparable: the record was made again at 0.5.14. Added alone on top
of rules 1 to 8 (that measurement): rule 9 made 1 page right, rule 10 made 8, rule 12 made 1,
none made one wrong. Leaving one of rules 1 to 8 out (pages that stop being right / pages that
become right): rule 1 1/0, rule 2 2/0, rule 3 7/2, rule 5 2/1, rule 6 1/1, rule 8 1/0; rules 4
and 7 0/0. Speed: 0.575 (off) and 0.578 s per page (on) on a quiet box; in the runs after the
five fixes, with the box busy, on took 0.90 of off's time (0.91 before them).

The order numbers alone do not say whether to turn it on. With the tree on, the replayed result
of 24 of the 73 corpus uploads changes (`hashrun.py full`, 2026-11-20), and a changed box changes
a crop, so the recorded answers of the pages it moves stop matching: turning it on comes with a
re-recording and a version bump.

**The nine pages the first measurement made wrong** (154 right on, 9 right off and wrong on) are
right again (163 right on, 22 made right, none made wrong; the corpus gold pages are unchanged at
82 right, none made wrong). Five causes, each in the tree and none of them per page:

- A table, with its caption, was not an item, so the lines beside it were read as a column of
  their own and the cut fell inside a paragraph, or a footnote zone took a figure's labels
  (4 pages: two tables at the top of a column, two tables with captions in both columns).
- A picture that starts a little below the first line beside it was a band of its own, so that
  line was cut off as a segment (1 page).
- A rule drawn at the head of one column was taken as the blank of a line in the other, because
  the blanks were marked without the page's gutter, which reached that line across the channel
  (1 page: a boxed algorithm at the top of a column).
- Three channels (the numbers in the margins and the gutter) were more than three columns, so
  the page was one leaf and its lines were merged across the gutter (1 page).
- The gaps that inline formulas leave in two lines were counted as a channel with three lines on
  each side, which they are in pieces and not in lines (2 pages).

**Where a table proposal is accepted (WS15.2).** `tables.find` proposes tables on the raw page,
before the page has columns, so a proposal box can take the lines of two columns (a syllabus's
sidebar and its body, paired at their sentence ends: `s02_syllabus_sidebar` page 1). With the tree
on, `layout.document._analyze_document` still calls `_claim_tables` first, but `_claim_tables`
asks `page._accepted` about what the detector proposes before anything is taken out of the page.
`_accepted` builds the page's tree from every fragment (the proposals' lines among them; no
table is an item yet) and `regions.table_refusal` judges each proposal against it:

- A proposal whose lines stand in one region (a leaf, a column, a drawn box) is accepted. A
  horizontal cut is no gutter: a table has rows.
- A proposal across a gutter (a `cols` channel, the edge of a drawn box, two cells of a label
  grid) counts only where the page has lines of its own on two sides of it. The page's own lines
  are the ones no proposal of the page takes (`_between`, `taken`). Without that, the cut that
  parts a table's own columns, with text below the table in the segment under one of them, was
  taken for a gutter and two real tables (a ruled rubric, a booktabs table) were refused. A
  table that spans the columns on purpose, and a table with nothing else in the cut, are
  therefore accepted, and so is `s04_worksheet`'s matching table (its lists have no line of the
  page's beside them).
- A proposal across such a gutter is refused when a line of three words or more stands on each
  of two sides (`REFUSED_PROSE`). Nothing else refuses it. The plan names two more grounds (rows
  that do not line up across the gutter, and two labeled lists side by side); neither had a
  case on the 332 development files, so neither is built.
- A refused proposal is left where it is: its lines and rules stay in the page, and are read
  by the tree like any other line. The tree that reads the page is built after the judging,
  from the accepted tables only, so the tree a proposal was judged against is not the tree that
  reads the page: it differs by the room the accepted tables take. Verdicts are not made again.
- The refusals are on `Layout.table_refusals` (page, box and reason code; no text). The page
  trace holds seconds only, so this is where they are kept.
- The next page's continued-table check (`find_continued`) is given only the accepted tables.
- `_text_block_dropped` and the "box extended along rules" growth (`REASON_EXTENDED`) are not
  changed: they run on the raw page and take no region argument; that belongs to the second
  table reader (WS5.16).

Still not an item: a vector figure (its drawings are read after the rows are made), so a figure
drawn as vectors at the top of a column beside text is placed by its labels only.

**Why it is off.** Turning it on changes the output of pages, so it needs a `PIPELINE_VERSION`
bump and a recorded re-measurement first.

### Headings

- **Body size** is the font size covering most characters in the analyzed pages.
- A run of at most 3 rows and 200 characters at **1.15 x body size or more** is a heading
  (`"larger font"`).
- A **bold body-size** run of at most 2 rows and 120 characters that starts a block (after a
  gap, an indent or a sentence end) and reads like a title (no lowercase start, no trailing
  comma) is a heading with weak evidence (`"bold body-size line"`). A row that is the item of a
  run of its series (`_Row.in_series`, below) is not one: the bold "B. Newton's second law"
  among upright options of an answer key, the bold lettered lines of an agenda and "I. Outline
  topic" between "H." and "J." are items, and a standalone numbered line is held to the same.
- A **standalone numbered line** at body size ("2.3 Units of Speed", one row, a gap above
  and below, no full stop) is a heading (`"standalone numbered line"`), but only with a
  real section shape (`section_heading_number`): a dotted number
  that does not start with `0.` (`2.3`, `2.3.1.`), a prefix word (`Chapter 3`, `Section
  2.1`) or a roman numeral with a full stop (`IV.`), followed by a capitalised word that
  is not a unit or a month. `12 Metres`, `2 kg`, `2.5 Kilograms`, `0.5 s`, `10 October
  2026`, `9.8 metres per second`, a bare `2.4` and numeric table cells are paragraphs. A
  title starts with a capital letter after any number, so a bold `2 kg` is not one either.
- **Plain numbered lines in sequence** (`_promote_numbered_lines`, 2026-11-02). A regular
  body-size line that reads `3 Forces` (a bare integer of one or two digits, a capital
  after it, up to eight words, no closing punctuation, not a quantity) is a paragraph on
  its own, because a bare integer at body size is too often a quantity or a list line. It
  is a heading, with the weak `"standalone numbered line"` evidence (an optional note,
  never `"numbered section"`), when the document corroborates it: the next such line in the
  same type carries the next integer, with a paragraph of two lines or more between them
  (a run of short numbered lines is a list), in the same column. One `3 Forces` alone stays
  a paragraph. A line that opens like an exercise or a question (`1 Find the final speed of
  the cart`, `2 What is the mass`: a verb from a fixed list of imperatives, or a question
  word) is never a candidate, since problem sets are content. Where the sequence does not
  look like a document's sections (it does not start at 1 or 2, or the title is longer than
  six words) the heading carries `REASON_NUMBERED_UNCONFIRMED` and is a required decision
  (check `heading.numbered_unconfirmed`, NEEDS_REVIEW, like a sentence-like line); a
  sequence from 1 or 2 with short titles is the weak heading above (an optional note).
- Section numbers are evidence: `2.3`, `2.3.1`, `Chapter 2`, `IV.` are
  `"numbered section"`; `1.` and `A)` are `"list-style number"` because lists start that way.
  In a larger font any section number counts (`3 Forces` is a chapter title); at body size
  only the section shape above does.
- Rotated and diagonal text is never a heading.
- **A heading that is mostly math is an equation** (`_mostly_maths`): a large or bold run
  with a relation sign at full size, no plain word of more than three letters and at least
  70 % math characters (`KE = m v` set large on a slide, a bold `PE = mgh`; a letter
  group of up to three letters counts as variables beside a relation sign) goes to the
  equation step, not to the outline. A line set bold as a whole keeps no bold in its text
  layer, since the weight is the heading's, not a vector's. `2.3 Units`, `Summary of
  results` and a title with a number but no relation sign stay headings.
- **A heading with the same text as the heading directly above it, at any level**
  (nothing between them, case, spacing and punctuation aside: `heading_step.repeated_above`)
  is not a second heading. OpenStax prints a box's label and its title as two lines
  ("Teacher Support" at 14 pt over 12 pt); both are headings in the PDF, and a screen-reader
  user navigating by headings would hear the pair twice. The second line's fix is ACCEPTED,
  review `none`, reason `repeats_heading_above`, with no `HeadingFix`, and the assembler
  writes it as `<p><strong>`. It takes no part in levels, the outline or the repeated-label
  groups. The same text with a paragraph or any other element between the two copies is a
  real second heading and stays one. History: T1-13 first demoted only same-level repeats,
  because the development set's gold (OpenStax's own markup) counts both lines as headings
  and the broader rule lowered heading F1; on 2026-10-14 the student-facing outline won
  (docs/eval.md "2026-10-14, doubled box headings").
- A bare chapter number (`4`, `Chapter 4`) in a large font directly above the title is
  merged into it (`4 Energy and Work`).
- **Lines of one heading share a typeface and a size** (T2-8, 2026-10-14). Two bold lines
  are lines of one block only when their font family (`_Row.family`: the name without
  subset tag, weight, style and design size, so bold and regular of one family agree and
  Sans and Serif do not) and size (to half a point) are the same; heading-sized lines
  already had to agree in size. A bold sans "Practice Makes Perfect" over the bold serif
  title of the section it introduces is two headings, not one that reads "Practice Makes
  Perfect Solve a System of Equations by Substitution" (two such merges on the dev-v2
  set); the second line starts a block of its own.
- **The PDF's own tags** (T2-18, "Tagged PDFs" below) can make a line a heading, and give
  its level.
- **The PDF's outline** (bookmarks, "The PDF's outline" below) does the same, when it
  matches the page text; a tag wins over a bookmark.
- **Math in a heading** stays text: math-italic and other mathematical alphanumerics
  become the plain letters they style (`readable_math_letters`: `𝑔` is `g`, `ℎ` is `h`;
  only the bold, italic, sans and monospace variable ranges and Planck's h: `ℝ ℂ ℕ ℤ ℚ ℓ ℒ`,
  script, fraktur and double-struck letters stay),
  so the heading and the outline read as words. A heading, unlike a paragraph or a
  figure's caption, has no inline equation element; the italics are lost, the letter is
  not.

**Levels** (`heading_levels`): the sizes of size-based headings are clustered (within 5 %),
largest = level 1. Bold body-size and standalone numbered headings sit one level below the
smallest size; among them a deeper section number goes one level deeper. Levels cap at 6.

**Outline repair and confidence** (`heading_fixes`): a heading never goes more than one
level below the nearest earlier heading with a smaller raw level, and the first heading is
h1 or h2. Siblings stay siblings (`h1 h3 h3 h2 h3` becomes `h1 h2 h2 h2 h3`).

| Evidence | Confidence | Strong |
|---|---|---|
| larger font | 0.90 | yes |
| larger font + section number | 0.95 | yes |
| bold body-size or standalone numbered line | 0.60 | no |
| ... + section number | 0.88 | yes |

Penalties: all caps -0.15, fewer than 4 letters -0.20, list-style number without a larger
font -0.15, outline repaired -0.03. A heading is *certain* with strong evidence and none of
the first three penalties (so its confidence is >= 0.85, the default
`AUTO_ACCEPT_CONFIDENCE`). Checks (all warnings): `heading.outline` /
`heading.level_repaired`, `heading.evidence` / `heading.weak_evidence`, `heading.all_caps`,
`heading.length` / `heading.very_short`, `heading.list_like`. An empty heading text gives
ABSTAINED with reason `heading.empty_text` (review "required").

**Review tiers**. Every heading is
written as a heading; the tier only says whether a person must decide:

| Heading | Status | `Fix.review` |
|---|---|---|
| certain | ACCEPTED | `none` (`optional` when the outline repair moved it) |
| uncertain | ACCEPTED | `optional`: its failed checks are the notes |
| uncertain, the same label and style two or more times | NEEDS_REVIEW | `required`, one decision for the group (`group_key`, `group_size`) |
| weak evidence and a sentence (see below) | NEEDS_REVIEW | `required`, check `heading.sentence_like` (T2-7) |

Same label: NFKC, case-folded, letters and digits only ("Teacher Support:" = "teacher
support"). Same style: font size to the half point, bold or not, larger-font evidence or
not, and the same proposed level. Certain copies of the label are never in a group, and
nor are sentence-like lines, which are decisions of their own.

*A sentence* (T2-7, 2026-10-14, `heading_step.sentence_like`): a heading with weak
evidence (bold at body size: no larger font, no section number, no tag) whose text ends in
a full stop, a question mark or a colon (closing quotes and brackets aside) and has at
least three words ("Given a binomial, write it in expanded form.", "Can factorials always
be found using a calculator?", "Remember the order of operations:"). It is still written as
a heading; a person decides whether it is one. Shorter lines ("Answer:", "Definition.") are
labels. The backlog's evidence (A5): every wrong heading of the held-out run was in the
optional bucket, so no wrong guess ever reached a person.

*Repeated labels, the T2-7 measurement* (2026-10-14, development sets only; the held-out
set was not used). The backlog proposed making repeated-label groups optional notes. The two
development sets disagree about them, so four ways of handling a group were run on both
(`eval pipeline --task headings --fake`, no model; the sentence rule is on in all four):

| Variant | Set | Required decisions | Flag precision | Flag recall | Wrong headings not asked about |
|---|---|---|---|---|---|
| (a) groups are optional notes | original dev (3 sections, 43 PDF pages) | 0 | n/a | 0.000 (0 of 18) | 18 |
| | dev-v2 (28 documents, 338 pages) | 26 | 0.577 (15 of 26) | 0.122 (15 of 123) | 108 |
| (b) one required decision per group (kept) | original dev | 9 | 0.556 (5 of 9) | 0.778 (14 of 18) | 4 |
| | dev-v2 | 59 | 0.305 (18 of 59) | 0.236 (29 of 123) | 94 |
| (b2) only the first copy of a group is required, the others optional notes | original dev | 9 | 0.556 (5 of 9) | 0.278 (5 of 18) | 13 |
| | dev-v2 | 59 | 0.305 (18 of 59) | 0.146 (18 of 123) | 105 |
| (c) every copy is a decision of its own | original dev | 41 | 0.341 (14 of 41) | 0.778 (14 of 18) | 4 |
| | dev-v2 | 159 | 0.182 (29 of 159) | 0.236 (29 of 123) | 94 |

Heading F1 is the same in every variant (0.9344 original dev, 0.9168 dev-v2). Before T2-7
(no sentence rule) the group behavior was (b) minus the 26 sentence decisions: 9 decisions
on the original dev set and 34 on dev-v2 (flag precision 0.118, flag recall 0.114). (b) is
the old "one decision per group, not per copy"; (b2) is the variant that asks for the group's
first copy only.

*Why (b).* The criterion was: keep one decision per group if it holds most of the original
dev set's flag recall and adds few decisions on dev-v2. (b) keeps all of it (14 of its 18
wrong headings, 0.778, against none in (a)) and costs 33 more decisions on dev-v2 (26 to 59,
0.077 to 0.175 per PDF page: one more every ten pages). Those 33 are mostly wasted there: 30
of the 33 groups hold only right headings ("Answer:", "Definition." under every example), so
dev-v2 flag precision falls from 0.577 to 0.305; but they also double its flag recall
(0.122 to 0.236, 14 more wrong headings), and a group is answered with one action for all its
copies (keep all, set a level, make all bold text). (a) loses the original set's recall
entirely. (b2) keeps the decisions and loses most of the recall (it checks one copy and the
rest go unasked). (c) has (b)'s recall at four times the decisions on both sets. The weakness
of (b) is precision on dev-v2; the next lever would be a rule that tells "Answer:"-type
labels from "Teacher Support"-type boxes, which no development set separates yet.

**Run-in labels.** OpenStax's outline does not count the bold labels inside a worked
example or lab box ("Solution", "Discussion", "Procedure") as headings, but does count
the titles of boxes ("Teacher Support", "Worked Example" titles, "Strategy"). On the
printed gold pages they differ only in ways our print CSS decides, not the book: the
labels are at exactly body size inside an indented box, the box titles are an h4 at 1.05 x
body size (or larger, like "Strategy").
Rules tried (2026-10-08):

| Rule | Gold pages (17 labels among 138 predicted headings; the other 121 are gold headings) | Fixtures (`tests/fixtures/layout`, 4 HTML and 4 LaTeX PDFs; no run-in label) |
|---|---|---|
| bold, body size exactly (within 2 %), at most two words | 17 of 17 labels; 1 real heading lost ("Displacement Problems", an h5 at body size) | 1 real heading lost ("Worked example", an h4 at body size in `worked_example.pdf`) |
| ... and indented more than 3 pt from the page's usual text edge (inside a box) | 15 of 17 (the two "Procedure" on page 2.1 sit at the edge of a page full of boxes); 0 lost | 0 lost |

Neither is reliable: the first loses real headings, the second misses labels as soon as a
page is mostly boxes, and both signals (font size equal to the body, the box's padding)
come from `eval/render.py`'s CSS, not from the book, and none of our fixtures has a
run-in label to test them on. Real documents set labels and headings in many other ways
(LaTeX `\subsubsection*` is bold at body size at the margin; Word users bold a paragraph
as a heading), so neither rule is used on any bold line. Other bold lines stay headings:
alone they are optional notes, repeated they are one group decision.

*The narrow rule* (2026-11-02, `_mark_run_in_labels`, `heading_step.run_in_labels`). A
heading found only by the bold body-size rule (no larger font, no number, no tag), one
line, at body size (within 5 %), whose text is one of `solution`, `answer`, `discussion`,
`procedure`, `proof`, `hint` (a trailing full stop or colon aside), **set in more than 3 pt
from its column's text edge** (`_text_edges`: inside a box; a section heading sits at the
margin), and which occurs at least twice in the document, is a label:
`REASON_RUN_IN_LABEL`, no `HeadingFix`, review `none`, written as `<p><strong>` (the fix is
the one `repeated_above` gives, reason `repeats_heading_above`, which the assembler writes
as bold text). It stays out of the outline and the repeated-label groups. A bookmark or tag
that calls the line a heading wins.

The inset test is there because the first version, without it, was measured and
rejected: the authored dev-v2 documents set "Solution" and "Example" as real headings
(LaTeX `\subsubsection*{Solution}`, a Word heading style), at the margin of their column,
and the rule removed 10 gold headings from them (dev-v2 recall 0.9165 to 0.9019, F1 0.9192
to 0.9112). The edge is the column's, not the page's: on a two-column page measured against
the page, the right column's whole text looked indented and two Word headings were lost.
With the inset against the column the authored documents are untouched. What is left: a
label that sits at the margin of a page mostly set in boxes (the two "Procedure" labels
of OpenStax 2.1), and a document that really indents a repeated "Solution" heading, which
loses those headings from the outline; the word list is closed and short for that reason
(`Example`, `Strategy`, `Teacher Support` and the like are not in it).

**Known-plain letters** (`Element.regular_letters`, equations only). The letters of an
equation's text that the PDF draws, not bold, in a font known to have a bold face
(`_REGULAR_FACE`: Computer Modern CMR/CMMI/CMTI/CMSS, Latin Modern, cm-super, txfonts and
pxfonts, Times, Nimbus, Liberation, DejaVu, Arial, Helvetica, Calibri, Cambria, Georgia,
Palatino, Garamond, Charter, Noto, Source, STIX, XITS, TeX Gyre, Libertine, ...). A bold
reading of such a letter is a scalar read as a vector, which the equation step treats as
meaning-level. Fonts that cannot show a letter's style in the text layer are left out on
purpose: MSBM (double-struck), EUSM and RSFS (script), EUFM (fraktur), CMSY
(calligraphic), Symbol. A letter that is already a styled math code point says its own
style and is not listed. On the two-column handout: `𝐯 ∈ Rn` gives `n` (R is MSBM), the
bold omega gives nothing.

**Glyph evidence** (`Element.glyphs`, `layout.line_glyphs`; only with `EQUATION_GLYPH_CHECKS`). For
the checks of a reading against the PDF the glyphs under an equation's box are kept with it, each as its
character (as `Element.text` writes it) and the style its font gives a letter (`glyph_style`: italic,
upright, bold, bold italic, script, double-struck, fraktur, or none). For an inline equation read with its
line, the glyphs left and right of the box on its row (within half the box's height, 24 either side,
nearest first) in the region of `crop_line` too (`_line_clip` is the one rule for that region, so the glyphs
kept are what the model saw): a reading that goes past the box is compared with them. A turned page, and an
equation with no glyph under its box (a picture), have none. Display equations keep their own glyphs only.
With `EQUATION_GLYPH_RECONCILE` the context also says whether the PDF is TeX-made with every non-ASCII
glyph of the span in a TeX font (`tex`), the equation numbers printed at an edge of the box (`labels`) and
the capitals drawn in an RSFS or EUSM face (`scr`): `docs/steps.md`, "Corrections from the glyphs".
With `EQUATION_FILE_FACTS` it also says how each glyph's character was recovered (`how`: name, font table or guess), whether it sits raised or lowered off the baseline (`lift`) and the math glyphs on each side of the box on its row (`math_before`, `math_after`); with the switch off these stay empty and are not written: `docs/steps.md`, "The file's characters in the request".

### Lists

- **A line of key terms.** Right under a heading that ends in "Key Terms", a paragraph of
  plain text whose lines break at gaps of at least 0.7 x the font size, with at most six
  words between gaps, becomes one list item per piece (`_key_term_lines`): OpenStax's
  unruled "Key Terms" box is `dependent variable independent variable tangent` in the
  text layer, one box per term in print. A ruled box found as a table is turned into
  items the same way (`_term_items`). A paragraph with no wide gap, or a long piece, stays
  a paragraph; a term that wraps onto a second line becomes two items (not told apart).
- **Glossary entries** (T2-16). Under a heading that ends in "Key Terms", "Glossary",
  "Vocabulary" or "Terms and Definitions" (`GLOSSARY_HEADING`; the section lasts until a
  heading in a larger font or with a section number), a term and its definition become one
  paragraph with `Element.term_end` (the term is `text[:term_end]`), and the assembler
  writes consecutive entries as one `<dl>` with a `<dt>` and a `<dd>` each
  (`_glossary_entries`). Four shapes: a term at the margin with the definition on the
  indented lines below it in one paragraph (OpenStax's "Key Terms" pages; the term takes
  one or two lines); the same as two blocks, a term line over an indented paragraph; a bold
  term of at most six words that layout took for a heading, directly over its definition
  paragraph (those terms stop being headings: the authored glossary page goes from 22
  predicted headings to 7); and a bold term that runs into its definition on a line, one entry per
  line that starts with one after a sentence end (LaTeX description lists). A paragraph
  that only looks like one (an indented first line) and text outside such a section are
  left alone. Not read: a glossary set as an unruled two-column table (the Word fixture
  in the dev set), an entry split across a page break, and terms and definitions that
  share a line without a bold term.
  Outside such a section, three or more paragraph blocks in a row that open with a lead term
  are entries too when their terms are in alphabetical order (`_lead_entries`,
  `_listed_terms`): the run is one `<dl>`. Two are a sentence with a bold start. A theorem
  and its proof, the cases of a proof ("Case 1:", "Part A:") and the speakers of a dialogue
  ("Simon: ...") open with a lead too and are not a list of terms: their leads are in no
  alphabetical order, carry numbers, or differ only in the last word, and they stay
  paragraphs, each a block of its own. One term in ten may be out of place.
- **Teacher tags.** OpenStax's level tags at the start of a paragraph or item, `[BL]`,
  `[OL]`, `[AL]`, `[All]` and runs of them (`[BL][OL] Describe a scenario ...`), are dropped
  (`_strip_teacher_tags`); a screen reader would spell them out. Only those four, in
  capitals, before other text. `[Visual]` and similar stay, and so do `[1]` and a tag in
  the middle of a sentence. The tag tells a teacher which students a prompt is for, so a
  document that uses them loses that information in the student file.

A row starts a list item when it begins with a marker followed by the item's text
within 3 x font size:

- a bullet glyph (`•` `◦` `▪` `▫` `■` `□` `●` `○` `‣` `⁃` `➢` `✓` `►` `▸` `▶` `▹` `◆`,
  Word's Symbol and Wingdings bullets (private-use codes, mapped by `font_codes` before this
  step), beamer's AMS triangle `▶`). A bullet glyph at the start of a row counts as a marker
  when it stands at least 0.1 x font size from text set in another font (0.2 from text in the
  same font), though no space character separates them (Word's Wingdings bullet leaves 0.23 x
  font size and no blank in the text layer; the usual split of a row's tokens is 0.3). A
  bullet that touches its text (a gap under that, "\u25aaGrading" set in a bullet font with no
  space at all) is the same glyph a word could start with, so it is an item only when another
  row in the document starts with the same glyph, like a dash; a lone one stays a paragraph;
- Word's second-level bullet, the letter `o` set in Courier New: a monospaced `o` followed by
  text that is not monospaced, with a sibling like a dash (so code that starts a line with
  `o` in one monospaced face is not a list). It is reported as `◦` and nests by its indent.
  The text after a bullet glyph may be only an operator (`◦ **`); after any other marker it
  needs a letter, a digit or math;
- a bullet drawn as a small shape (at most 0.6 x font size) just left of the row at its
  height: Chromium draws CSS disc markers as paths. Filled curves are "•", outlines "◦",
  filled squares "▪";
- a line-start dash or star (`-` `–` `—` `*` `∗`, LaTeX's second and third levels) or a
  middle dot (`·`, Word's Symbol bullet as extracted). A dash followed by a digit or a
  decimal point (`- 40 m/s`, Word's `– 5 °C`), or set in a math font, is the sign of a
  value, not a bullet: the sign stays in the text;
- an ordered marker: `1.` `2)` `(3)`, `a.` `b)` `(c)`, `A.`, `iv.` `(ii)` `IV)`. OpenStax's
  "3 ." (number and full stop in separate spans) is read as "3.". A blank to fill in before
  the marker (three or more underscores, or a drawn rule that `_mark_blanks` writes as five)
  does not hide it ("___ 1. Osmosis"): the marker is the label after the blank
  (`_Marker.start` is its first character) and the blank stays in the item's text. A blank
  before a bullet is no marker. Two series of labels set side by side under one question
  (matching exercise: "___ 1." to "___ 3." in the left column, "A." to "C." in the right) are
  read column by column as two lists one level below the question (`_matching_series`, the
  option-grid machinery with two edges and two kinds of label), the left one first. They need
  a full run in each, a line holding one label of each, and the question above.

A bullet glyph, a drawn bullet, or a number before a capital letter ("3. Measure") is
enough, except that the number needs a sibling too when its row reads as the next line of
a wrapped paragraph: the line above is at the usual pitch and size, starts as far left as
the marker, and does not end with `.` `:` `;` `!` `?`, and the line below does not hang at
the item text ("... is exactly" / "2. The heavier ball ..."). Letters, roman numerals,
other numbers and line-start dashes need a sibling: a row
anywhere in the document (lists cross pages) with the same kind of marker printed the same
way, the next or previous value ("b." for "a."; the same glyph for a dash) and the marker
or the item text starting within 3 pt in x. "i" is both a letter and a roman numeral; its
siblings decide. So "A ball rolls", "2.5 metres is", "2.3 Speed", a lone "B. Smith" or
"I. Newton", and a wrapped sentence that happens to start a line with a dash, stay
paragraphs. A capital letter and a full stop whose text opens like a list of authors (another initial,
or a surname and a comma, "and" or "et al": "A. Smith, B. Jones", "D. S. Turaga, ...") is the
marker of a list only beside its neighbor: the item just before or just after it in reading
order (a page's left column, then its right), or the one just above or below it at the same x,
is the next or the previous letter (`_names`, `_list_markers`), unless that neighbor opens
like an author line itself (an initial, a surname and a comma or "et al", "and" before an initial;
an alphabetical reference list has the next initial in its next entry). Some line of a long document
has the next letter, and a reference list or a byline had its lines read as lettered items.
Any other capital letter keeps the sibling test above. Headings are decided first:
"1. Introduction" in a large font is a heading; so is a line of 2 to 12 words that starts with
a capital letter and a full stop and is set, all of it, in bold or in italics, and does not end
like a sentence ("A. Problem Descriptions", IEEE's subsections; reason "lettered title", one
level under its section like a letter of an index). The type alone does not tell such a title
from an item (the options of a question are set in italics too), so two more things are asked
of it (`_mark_lettered_titles`): the next row of its column is not text in the title's own
face, and has no marker unless it opens a numbering ("A. Datasets", then "1) CIFAR-10: ...");
and the nearest marker above it in its column is not the letter before with only that item's
own lines between them, which is the next item of a list. An option of a grid is never one.
The title is a block of its own, apart from the text under it. A title that wraps onto a second
row of its face (no marker, less than half a row height under it, at most 12 words in all, the
second row not ending in a comma, a semicolon or a colon) is one title of two rows. A full stop
may end a title of four words or more, the letter counting ("B. Electron coupling and mobility.",
`MIN_STOPPED_TITLE_WORDS`), on its first row or its second; with fewer words ("A. Yes.") it ends
an answer and the line is an item. The type alone does not make a heading of a lettered or numbered line
that stands in a run of its series either (`_mark_series_items`, `_Row.in_series`): the next
marker of its column, past its own lines, is the next value ("A.", "B.", "C."; "I." between "H."
and "J."), or the marker nearest above is the value before with nothing but its own lines
between. A heading of the same shape has text, or a figure, under it: text of another face is
no line of the item, and a figure or a table, which is no row of text, shows as the white it
leaves, where a run ends (more than `SERIES_GAP` times the type size between two rows of the
column: "1. Setup" over a picture and "2. Procedure" under it are two titles). The white alone
ends a run, whatever stands in it: numbered items set that far apart, with room to answer
between them, end their runs too, and set in bold they are headings (O-401, a limit: on the
review probes `l_questions_bold_space20/40/80` and `l_bold_items_lead36/44` the questions are
headings at white of 36 pt and more; a rule that ends a run only at white that holds a picture,
drawing or table read them as items and the picture case ("1. Setup", a picture of 6 to 120 pt,
"2. Procedure") as two titles, but not one of the 996 PDFs of the layout runs (the gold sets,
the layout-gold files, the repository's PDFs and the page cache, three variants each) or of the
73 uploads changed with it, so it was not built, wave 4). Two titles in
a row, the first with no text of its own, are read as a list of two. A bold line that starts
with a bullet glyph in its text is a list item, not a heading (a bullet drawn beside the row
changes nothing). Known limit (O-407): a deck of formulas has a body size (the commonest glyph
size) of 18 pt while its bullets are set at 25 pt, and the size alone makes headings of the
bulleted lines. Only the authored `html-slides` fixture shows it: none of the 332 files of the
public sets, and none of the 286 bulleted lines of the slide decks in the private corpus, has a
bullet larger than 1.2 times the body size, so it is not built. A bullet glyph before a large line
is also decoration in a textbook ("▪ Key Terms"), so a rule on the bullet alone would be wrong
outside decks.

A numbered line that is the first line of a paragraph is not cut from its second line: when the
numbered line is the only one of its run, starts indented (at most `INDENT_REACH` em right of the
next line), runs to the right edge and ends in the middle of a sentence, and the next line is
full width with no marker, that line goes on from it although it starts left of the marker. A list
item that wraps goes on at its marker or its text, never left of both
(`_numbered_paragraph_goes_on` in `_continues`; IEEE's "2) Prompt Design: The controller ..."
was cut after its first line, and the rest was a paragraph of its own, O-408).

The item text starts after the marker; continuation lines join the item (a hanging
indent is fine). **Levels** come from the left edge of the item text, which is the same
for every item of one level even when the markers are right-aligned (LaTeX's "(i)",
"(ii)", "(iii)"): consecutive items in reading order form one list, a deeper indent
(more than 0.6 x body size) opens a level, a shallower one closes levels until one
matches; an indent within that tolerance of both of the two innermost open levels takes
the nearer one (a slide's 26 pt body makes the tolerance 16 pt). Where the item texts agree
within that tolerance, the marker still opens a level when it is of another series than the
one of the item above (bullet or number, "1)" or "(a)", another bullet glyph) and stands more
than half the font size right of it (`MARKER_INDENT`; slides set the sub-bullets under "1)" 13 to
15 pt in while their text is 5 to 14 pt right of the text above). Each open level keeps the
series that opened it: when the sub-bullets' text starts exactly on the text edge of their
numbered parent, the x does not tell which level "2)" goes back to, so it goes back to the open
level of its own series at that x. A numbered series that
starts again at its first value ("(a)" after "1.", "(i)" after "(a)") is a list under the item
above, even where the item texts start at one indent (the answer lines of an exam); "(b)" then
goes back to the letters. The same holds after a cut: "(a)" after "1." and a figure is
the list of that item, one level down, and not the first level again (`_list_levels`), on the
page of that item and with no heading between; lettered items pages after a numbered list, or
in the next section, are a list of their own. The level is what the result stores and the page
audit shows: on the student page a list that a figure or a paragraph cut starts a new list
(`assemble._list_levels` counts from the first item of each run). A marker with two readings ("(i)": the letter after "(h)", or roman
one) is read against the open level the item stands on, then the innermost one, after the
levels right of it are closed: the bullets under "(h)" say nothing of the "(i)" that follows
them (`_numbered`). A list that a paragraph, a figure or a table cut ("(i)", the words of
an answer, "(ii)") goes on where the next item takes up the numbering of one of the levels the
block closed (the next value of the same series at that level's indent): it keeps those
levels (`_list_levels`, `_taken_up`). Items of one series (a
wrapped list of names that start with initials) and the options of a grid are not moved by
it. The indent is measured from the left edge of the item's column on its page (the
leftmost x where at least three rows start), so a list that goes on in the next column or
on a page with another margin keeps its levels. A display equation inside an item does
not end the list; any other block does.
A list item's row is never a display equation ("b. - 40 m/s" had become one): math in
it becomes inline equations with the item as their parent.

### Equations

Every character is classified: **math** (a math font such as CMMI, CMSY, MSBM, STIX,
Cambria Math, Latin Modern Math, Noto Sans Math, Symbol; a Unicode math-italic letter like
`𝑣`; a Greek letter that is not part of a Greek word (below); a symbol that only occurs in math
such as `∑ √ ≤ × →`), **digit**, **operator**
(`+ - * / = < > ^` and the minus/plus-minus signs), bracket, letter, code (monospaced) or
other. A minus sign alone is not math evidence: groff prints command-line options as `−d`.
A run of two or more letters of the Greek script (U+0370 to U+03FF and U+1F00 to U+1FFF,
accented letters included) in a text font is a Greek word, and prose (`glyphs.mark_greek_words`,
set when the rows are read); so is the article "η" or "ο" with a Greek word beside it. Two
letters count as a word only on a line that holds a Greek word of three or more letters: alone
in a line of another language ("Δθ") they are two symbols. A lone Greek letter, and Greek in a
math font, stay math.

- **Display equation rows**: `parse.equation_score >= 0.5`, no prose word (3+ plain
  letters other than `sin`, `log`, units like `mol`; or English two-letter words like `is`,
  `if`), at least half the characters math-capable, and not mostly monospaced.
- **Fragments**: short math-only rows that do not score on their own (a lone `t`
  denominator, `2a`), and equation numbers (`(2.1)`, `[3]`, `Eq. 2.4`, a bare `2.4`).
  They become math when they touch a fraction bar or radical together with other math
  rows, or sit right next to a math row. An equation number belongs to math whose height
  it shares: its vertical center within half a font size of a math row (OpenStax sets
  `2.4` level with the fraction bar, on neither the numerator's nor the denominator's
  line); a bare `2.4` only counts to the right of the math with a gap of at least one
  font size. Otherwise fragments stay text (`12 m/s.` at the end of a sentence, `2.4`
  alone on a line). Attachment spreads breadth-first through a vertical index, so a page
  of thousands of short numeric rows costs about n log n.
- **Fraction bars and radicals** come from `page.get_drawings()`: horizontal rules at most
  1.6 pt thick (lines or thin filled rectangles; Chromium and TeX both draw them as
  rectangles) and stroked square-root shapes. A rule counts when math rows touch it and it
  is not much wider than they are (a table rule beside a `>4` cell does not count). A bar
  that starts at the top-right corner of a `√` glyph is reported as `"radical sign"`.
- **Display equations** are built from math rows in two steps (`_equation_regions`):
  1. *Lines.* Rows touching one fraction bar or radical (numerator, denominator,
     radicand), the main row at the bar's height (the `v =` before a fraction, within 4
     font sizes of it), rows on one baseline up to 6 font sizes apart (the columns of an
     aligned row) and a lone `,` or `.` overlapping a row (Chromium sets the full stop
     after a fraction on a baseline of its own) form one line.
  2. *Equations.* Lines are stacked top to bottom. A line joins the equation above when
     the gap is at most 0.8 x font size (1.2 x when it starts with a relation or
     operator), they overlap horizontally, at most one of them has an equation number,
     and either the line starts with `=`, `+`, `−`, `×` ... (the next row of a
     derivation), or one of the two lines is not a whole equation (no relation with
     something on both sides: a numerator, `f(x) =`), or one is at script size (the
     limits under a sum), or the line's vertical middle is above the bottom of the
     equation (it sits beside it: `sgn(x) =` level with the middle row of a cases brace,
     whose rows `1  x > 0` are whole equations of their own), or both are whole equations
     whose first relations are aligned within 0.3 x font size (an `align` system). Two
     whole equations stacked close but
     centerd on their own (`time = d / v` over `d = v × time`), two numbered equations
     (each row of a numbered `align`) and equations at paragraph spacing stay apart.

  The box covers every row and the rules the equation uses (no rule of a neighboring
  equation). A single row of at most two characters without a math character (`>4`,
  `+8`) is not an equation.
- **Exercise numbers.** A numbered exercise that is only an equation (`18. 0 = 5 t -
  4.9 t`) is one row, because the number and the equation sit on one baseline. The first
  token is cut off into a text row of its own (`_split_exercise_numbers`) when it is one
  to three digits and a full stop or bracket (`18.`, `4)`; also `18 .` in two spans),
  drawn in a text font (no math font, no math character), with a gap before the rest,
  and the rest alone reads as a display equation. The number then stays a paragraph in
  front of the equation and is in neither its crop nor its text layer, which the
  text-layer checks compare the model's answer with. A row that already starts a list
  item (siblings `17.`, `19.` confirmed it) is untouched, and so are `2.5` (a decimal) and
  `A)` (a letter).
  A number that is a row of its own (`6.`, `14)`, in a text font, one or two tokens: OpenStax
  sets it above or beside the first line of an exercise that is only a stacked fraction or a
  system) is marked the same way (`_lone_exercise_number`, `_Row.exercise_number`); read as
  a fragment it was drawn into the equation by `_math_neighbors`.
- **Rows of a table, braced systems (T2-2).** Two whole equations also join when they are
  set at most 0.7 x font size apart and share a left edge, a right edge or a center
  within 0.3 x font size (`_table_rows`: OpenStax centers each row of a system; a row may
  line up with any earlier row of its equation, not only the last). A row with a fraction set
  at the size of its line (a glyph that size on both sides of a bar, with no relation among
  them) is never a table row: `time = d / v` over `d = v t` are two equations, where the
  small fractions of a system's rows are not. Two numbered rows still stay apart. A system's opening brace is part of the equation: a brace in the text font on
  the first row after prose ("Solve the system by substitution. { 2x + y = 7") is cut from
  the sentence (`_split_braced_systems`: math follows it, there is no closing brace, and
  a math row sits under that math), and a brace that is a row of its own, level with the
  middle of the rows, joins them whatever its distance to the left (`_Line.opens_system`);
  alignment ignores it (`_Line.body_x0`).
- **Inline stacked fractions (T2-1, T2-11).** A fraction set in a line of text has its
  numerator and denominator on either side of a thin bar in a smaller size, and the text
  extractor cuts them into the lines above and below, each among the words of its line.
  `_lift_stacked_fractions` takes the glyphs over and under each short bar out of those
  lines and puts them back as one fragment (numerator, space, denominator, each glyph
  marked `stacked`) on the baseline of the text beside the bar (the nearest letter or
  digit, not a tall bracket). It applies only when the line of the bar has three plain
  letters (a display equation's fractions are read with their rows), the glyphs are
  smaller than the text and not wider than the bar, and no other bar lies among them (a
  complex fraction is left as it was). `stacked` glyphs count as math and are evidence of
  an inline equation (`"stacked fraction"`); they do not set a row's size or baseline.
  `_lift_stacked_pairs` does the same for a binomial or a column of two (`( 5 over 3 )`:
  brackets round one short token above and one below, nothing between them on the line of
  the brackets). A lone bracket that is a row of its own (TeX sets a tall bracket lowered)
  joins the line it stands in (`_attach_big_operators`), and a closing bracket followed by
  a sub- or superscript is evidence of math (`(x)ₙ`).
- **Half a fraction (T2-11).** An equation whose box ends at a thin bar with glyphs on the
  other side of the bar gets the reason `"half a fraction"` (`_flag_half_fractions`). The
  equation step adds the warning `equation.region: half_fraction`, holds the confidence
  under the accept bar and never accepts without a decision.
- **One printed display, one element (`LAYOUT_DISPLAY_GROUPING`, off in code and in production; bump `PIPELINE_VERSION` when it is turned on).** Seven patterns cut one printed
  formula into several elements, and each is joined on evidence from the page; anything less
  clear keeps the old behavior.
  1. *Two formulas and a word.* "P_A = ... and P_B = ...", "0 < p < 1 for all i": a connective
     (`_CONNECTIVES`: and, or, for, for all/any/every/each, where, with, if, when, otherwise)
     alone in its row, between two formulas on its line. `_join_connectives` joins the formula
     before it (a whole equation) and the one after it (a whole equation, or after "for" a
     condition of three glyphs or fewer) when both reach it within `CONNECTIVE_GAP` (9 font
     sizes) and the word sits at the height of their middle (a fraction's bar); the word becomes
     a math row, so the crop, the text layer and the reading hold it, and the display's one
     number (at the right margin) belongs to it. A row that is one line of text and holds only
     this (`_joined_formulas`: one connective, a relation with something on both sides of it,
     the rest math; "P(A) = 1 and P(B) = 2,") is a display row too. Any other word
     ("where we assume", "Let x = 1 and y = 2") leaves the row as text; two numbered equations
     are never joined. Before, the word was a paragraph read after both equations and an
     inline "and" row was two inline equations.
  2. *The right half of a long row.* Lines are stacked top to bottom, so a long second row
     whose right half is a bare fraction (no main row on the baseline to chain it) opened a
     region of its own beside the row above, and the row's left pieces, starting with "=",
     joined that one. `_merge_continued_regions` joins a region whose leftmost top line starts
     with a relation or operator to the display above it (within the line gap, `MAX_LINE_GAP`
     x 1.5, or overlapping it: limits and numerators hang below), and a region whose line
     starts level with, and at most `MAX_COLUMN_GAP` sizes to the right of, a line of another
     (one of the two not a whole equation). Two numbered displays are never merged.
  3. *A trailing ellipsis.* `_trim_run` kept the dots of "j = 1, 2, 3, ..." and
     "0 > s_1 > s_2 > ..." out of the inline formula as a full stop; three dots after a comma
     or a relation or operator (`_trailing_ellipsis`) stay in it. Four dots are an ellipsis
     and the sentence's full stop, and the full stop still goes.
  4. *A line set off like a display, with a word in it.* A line with a prose word is read as
     text, so its formula was cut around the word ("Diam(C) <= ... for all i" came out as two
     inline formulas and a paragraph). `_promote_set_off_lines` makes such a line a display
     when it is mostly math (`NUMBERED_SHARE`) with at most `SET_OFF_WORDS` words, has some
     math row or a line score at the equation threshold, and stands in `SET_OFF_INDENT` font
     sizes from both edges of the text of its column (the left edge is that of the lines within
     `NEAR_LINES` sizes of it, so the last line of an indented paragraph is not a display), and
     does not sit within `SET_OFF_GAP` of the bottom of a line of text above it (a paragraph's
     last line). The rows of the line become math rows; nothing is joined.
  5. *A piece inside a display.* The top of a tall bracket set in pieces, the double bars of a
     norm: a line of its own to the stacking, inside the box of the formula it belongs to.
     `_absorb_contained_regions` joins a region with at least `CONTAINED_SHARE` (0.9) of its
     area inside a larger one; two numbered displays are never joined.
  6. *Limits at the size of the row.* `_joins_line` takes limits by their size (a script size
     under a big operator); a paper that sets them at the size of the row ("k=1" at 7 pt under
     a sum at 7 pt) gave it nothing to go by. `_limits_of` joins a line that hangs right under
     (or sits right over) a big operator (sum, product, integral, union, intersection),
     centered on it and no wider than twice its sign.
  7. *A line far from its number.* `_promote_numbered_lines` makes a line with words in it a
     display when a bracketed number stands beside it and the line is `NUMBERED_SHARE` math. A
     number set `NUMBERED_FAR_GAP` (4) font sizes or more from its line, at the right margin or
     at the left one ("(3)" beside a centered line), asks less of the line: `NUMBERED_FAR_SHARE`
     (0.35) of it math and `NUMBERED_FAR_INDENT` (1.0) font sizes in from the text edge. A
     prose line that ends in a reference stands a word space from it, so it is never this
     case. Box score of the 21 development papers, switch on, before and after this rule:
     display formulas whole 902 to 904, cut 38 to 35, split 32 to 33; one formula changed from
     cut to split, two from cut to whole, none got worse. Layout hashes of 1,069 files before
     and after: 0 differences with the switch off, 4 files with it on (ax_mathap_240306464:
     twenty numbers at the left margin become labels; ax_hepth_240306509: a sum and its
     "(B.3)"; ax_qbionc_240306496: "(21)"; 2411.07613: "(62)"), each read and each better.

  Measured on the 21 development papers (a development box score kept outside the repository; 11,258 formulas, display
  class 1,004): whole 80.0% with the switch off, 83.9% with rules 1 to 3, and with 4, 5 and 6
  added one after the other 88.4% (rule 4: 46 formulas made whole, 1 broken: an inline formula
  that a display's box now reaches), 89.8% (rule 5: 14, none broken) and 89.9% (rule 6: 1,
  none broken). No paper got worse. The one display that was whole in the first round of the
  grouping and is not any more (2412.12319, equation 82) is not in the box score: the gold
  writes its aligned rows one by one and the layout reads them as one array that holds every
  row, the form kept below.

  Loose equation numbers (O-037, O-391), counted again (2026-11-22) on 94 papers (the 21
  development papers of the box score and the public development and fresh papers of the
  real-world set: 2,327 displays, 956 with a label), with the switch off: 18 bracketed numbers
  that are a paragraph of their own or stand at the end of a display's text with no label,
  of six shapes. 6 stand beside a line with words in it that reads as text; rule 7 takes 4
  of them with the switch on ("(C11)" in 2103.06872, "(P2)" in 2308.10321, "(15)" in
  2412.00753, "(3)" in 2309.01468). "(5)" in 2309.01468 is beside a line that is mostly words,
  and "(3.7)" in 2305.10416 is beside a line the display rules make text again. 3 are glued
  to the end of a display's text, under a font size from it ("(A.28)" in 2305.10416, "(8)" in
  2412.18448, "(12b)" in 2411.03309; "(8)" misses `_closes_display` by 0.6 pt, and in the
  other two the display does not fill its column, so no distance rule takes them without
  taking prose). 1 is set right-aligned under a display at the right end of the display's
  longest row, not of its last ("(A5)" in 2412.18448: `_number_under` measures the line the
  number stands under). 1 stands above and left of its display on a row of its own
  ("(10.18)" in 2412.12319). 1 stands beside a group of lines that each start with a word
  ("(63)" in 2411.07613). 3 stand beside a line of digits with a drawn box that is not read
  as math ("(10)", "(11)", "(B.2)" in 2411.03309). 2 are on pages where a figure took the text
  in ("(IV.14)", "(II.10)" in 2407.20815: the figure rules, not the number rules). The
  other 14 are no cluster, and none was built. The label that moved from the brace to the
  rows on 2210.13562 page 2 is a display cut into a brace, a paragraph and rows: the display
  rules cut it, not the number rules.
  Labeled parts inside one display ("(a) ... (b)", O-048), counted again on the 94 papers:
  none of the 2,186 displays holds them (12 have "(a)" and "(b)" in the text, all arguments
  of a function or proof-step marks). With the 7 of 65,817 of wave 3 the shape stays too rare
  for a change that moves crops.

  Display groups left (wave 4, 21 development papers, switch on: cut 38, extra 29, split 32, whole 902 of 1,004): 20 of the 38 cuts leave a connective ("where", "for", "and", "subject to") outside the box, some of them in a line of inline items that the gold reads as one display; 29 extras are 19 in one paper (2305.10416) around sub-formulas the gold painted apart. Widening the host's box in `_absorb_contained_regions` was tried: 30 pieces are absorbed on the 21 papers, 9 hosts grow (by up to 7.4 pt), and the box score of 7 papers (one with a host that grew 7.4 pt) did not change, so it is not built. Counting the words of a set-off line without its connectives (`SET_OFF_WORDS`) was tried: +1 whole in one paper, 1 lost in another, so not built.

  Not done, and why: aligned rows that the gold writes one by one stay one element with rows
  (that is the form a screen reader reads best, and `scripts/rw_spoken.py` scores it per
  row); a display set with function names in a text font (`PMI`, `Unif` in Times) is still
  cut at the word, and an equation number set above the first row of a tall display is still
  an element of its own.
- **Inline formulas kept whole (`LAYOUT_INLINE_JOINS`, off in code and in production; bump `PIPELINE_VERSION` when it is turned on).** Four rules for the places where an inline run ended inside one printed formula, all behind `analyze(inline_joins=True)`; with the switch off the output is byte for byte what it was (the layout of 996 files, hashed before and after). Measured on the position truth of 21 development papers (a development box score kept outside the repository; 11,258 formulas, 8,990 whole before), each rule alone, and none made a paper worse:
  1. *Operator names* (`_MORE_MATH_WORDS` in `shared.py`): `erfi`, `erfc`, `supp`, `diam`, `codim`, `coker` and `arccot` count as math words, like `sin` or `max`. +2 whole, none lost.
  2. *A colon or semicolon inside a bracket* (`_separator_in_bracket`): "I(A : B)", "W(P; G)". A TeX-made page's separator inside a bracket the run opened, with math in between and the bracket closing on the same row, does not end the run. +76 whole, none lost.
  3. *A math word between formulas* (`_math_word_joins`): "a = b mod n". A word space next to a math word, with math on the other side, does not end the run: the spaces around `\bmod` and `\pmod` are the author's. The run before it must be math on its own evidence. +3 whole, none lost.
  4. *A label joins the run after it* (`_joined_labels`): "H0:" and "f :=". A short label (four glyphs or fewer, letters and digits only) that ends in a colon right before a run, and carries an index or a ":=", is the left side of that formula and joins it. A bare letter before a colon ("A:") is as often a list heading and stays out. +6 whole, none lost.
  All four together: 9,077 whole (+87), none lost; with `LAYOUT_DISPLAY_GROUPING` also on, 9,118 (+128), none lost.
  Two more rules, added after the box score of the merged head (wave 4, builder 05); both behind the same switch:
  5. *Italic words of a sentence* (`_ITALIC_CONNECTIVES` in `shared.py`): "for", "and", "any", "all", "the", "let", "are" and "not", three italic letters between blanks in a row that has upright letters or a relation, were read as the variables f, o, r (a theorem's statement is set in italic), so a line such as "x ≥ 2, for j = 1, ..., d and k = 3" was one math row and its box took the words with it. They are words now; the formulas around them are boxes of their own. With the first four rules and `LAYOUT_DISPLAY_GROUPING` on: 9,174 whole before, 9,183 after (+10 gained, 1 lost: a numbered display that ends in ", for any u in V" is now a formula and a sentence tail, its "for any" outside the box).
  6. *A colon inside a Computer Modern index* (`_TEX_FACE`): the colon of "dx_{1:T}" ended the formula after the "1" when the file's producer field does not say TeX (a paper re-written by a viewer or a converter, `data/real-tables/2407.13734.pdf`), because `glyph_math.script_punctuation` only ran for a file typeset by TeX. A glyph of a Computer Modern or Latin Modern face is TeX's whatever the producer says. No box of the 21 development papers changes (their producer says TeX); the public file's "dx1:T" is whole.

  Measured and not built (wave 4, 21 development papers, the four rules and the display rules on): the inline "extra" group is 394 boxes. Of 219 read (the five papers with most of them), 83 are a formula that wrapped from one line to the next (`_wrapped_inline`): its box is the union of the two pieces, a rectangle that holds the prose between them, and no change of a rule shrinks it (two boxes would be "split", and one element needs one crop); 31 are a multi-line display or a row of a display whose box holds a neighboring formula; 87 are a piece the gold leaves out of the formula (an unpainted bold letter, the isotope of "27Al", the "rank" of "rank r": the box is right); 18 hold prose, and the ones read are the same kind of piece ("CaWO4" for its "4"). The cuts at a spaced relation (285) and merged inline (144) were not taken.

  Not built: a part label "(a)", "(b)", "(iii)" that splits a run and stays text. A TeX word-space rule already splits "(a) x = 1" on TeX pages; on the other pages the narrowed rule changed no box on the development papers (a first version lost 29 whole boxes by taking a raised "(j)" and "V(f)" for labels), so it is not in the code.

- **Inline math split or merged (T2-6).** A middle dot `·` or `∙` in the text font (Chromium
  prints a product so) is an operator, so a run goes on through it. A blank in a text font
  between math-font glyphs ends the run (`_between_formulas`): Chromium puts the spaces
  inside a formula in the math font and the space between two formulas ("B = 0, C = 1")
  in the text font.
- **Equation numbers.** A row that reads as an equation number and touches no rule goes
  to the line it sits beside (`_number_beside`, nearest vertical center first), unless
  that line ends with a relation or operator (`x =   2.4`: the number is its right-hand
  side). It becomes `equation_label` and stays out of the text and box. A number merged
  into a math row (`_merge_baselines` joins pieces up to 4 font sizes apart: `= 2000 m −
  525 m ,   2.8`) is first cut off when it stands at least one font size clear of math
  (or of the comma that ends it) that does not end in a relation; a prefixed number
  (`Eq. 2.4`, `Equation (2.4)`) is cut off whole, measured from before the prefix. A
  number inside the math that is not beside any line stays part of it.
- **Inline equations** inside paragraph rows: a stretch of math-capable characters
  (math characters, digits, operators, brackets, single letters, sub/superscripts, a decimal
  point or a comma inside brackets), trimmed of spaces, trailing operators and punctuation,
  unbalanced brackets and a leading or trailing English `a`/`A`/`I`. It never starts with
  an operator that cannot start an expression (`/ * ^ = <` ...: `/h)] = 40` is the tail
  of a plain-text formula cut by a line break) or ends with one (including `−` and `×`).
  A bar `|` is kept at either end when the run has a pair of them (`|x| < 1`, `|r|ⁿ`);
  an unpaired bar at an edge is dropped.
  It is kept only with evidence: a math character; an italic single letter with an
  operator (a leading sign does not count: `-49 m` with an italic unit is a quantity) or
  script or Greek letter; a single letter with a sub/superscript (`x²`); a signed number
  with a real minus sign (`−3`); or a relation with operands on both sides (`x = 5`, not
  markup such as `<a><b/>`). Without a math character the run's brackets must also
  open and close in order. A lone italic word, `[3]`, `10-12` and `2019` are not math.
  TeX's spacing accents (the hat of `x̂`, tilde, bar, dot) do not end a run, and a run
  starts at the accent TeX sets before its first letter (left in the prose, the accent was
  lost: 7 inline equations in the first four pages of 15 arXiv papers); math
  function names (`sin`, `lim`, `proj`, `rank`, `diag` ...) are not English words. List
  bullets (`•`, `▪`, Word's Symbol bullet (U+F0B7, mapped to `•` by `font_codes`), and a
  `·` or `∙` that starts a line) are never math, even in the Symbol font Word draws them in.
- When `,` `.` `;` `:` or `?` follows an inline equation, its box's right edge moves left by
  up to `CROP_PAD` (crop's default padding, 2 pt) so the padded crop ends where the
  punctuation starts and still holds every glyph. The mark stays in the paragraph after
  the marker; if the crop showed it, the model transcribed it as well ("d0 , ,").
  `equation_step` also strips such a mark from the end of inline LaTeX.

### TeX and beamer

Checked on `tests/fixtures/layout/latex/` (Tectonic, Computer Modern and beamer's Latin
Modern Sans):

- CMMI, CMSY, CMEX and CMBSY are math fonts; CMBX, CMB, CMMIB (`\bm`, `\boldsymbol`)
  and CMBSY are bold, so bold vectors keep their weight in the text layer (`𝐯`).
- TeX's `\neq` is a zero-width negation slash (U+0338) over `=`; text cleaning joins it
  into `≠` (and `≮`, `∉`, ...). The text-font capital Delta (OT1 CMR) extracts as U+2206
  and counts as a math symbol.
- MuPDF's superscript flag is set on any span that starts above the previous one, which
  in TeX output is most of a line after an inline fraction, so a character is a script
  only if it is smaller, or flagged and raised by at least 0.2 x font size.
- **Small capitals are text.** Word, LibreOffice and TeX with a scaled font print the lower-case
  letters of "Lotka" as capitals at 80 % of the size, on the baseline, in the same font: a
  full-size "L" and the smaller "OTKA". Size alone made them a base with scripts, and an inline
  equation in the middle of a name. A script is off the baseline (a Word subscript is lowered by
  12 % of the row size, the least `SCRIPT_OFFSET` accepts is 4 %). `shared.mark_small_capitals`
  sets `_Char.small_cap` when the rows are read. Its unit is a word of capital letters in one
  font, all on the baseline (to within `SMALL_CAP_BASELINE`, 5 % of the row size), with no letter
  or digit next to it. A letter smaller than the word's largest (`SCRIPT_RATIO`) is a small
  capital, and that is a lower-case letter of the source: the word is lower-cased there ("Lotka",
  "Volterra", "LongMemEval" printed as `L ONG M EM E VAL`), so search and speech get the word. A
  word of small capitals only is lower-cased as well when its font and size are those of a small
  capital found on the same line ("and" in `P OLICIES AND P ROCEDURES`: 68 such words in 10 of
  the 19 corpus PDFs the rule touches). On a line with no such word it is left to the size rule,
  as is a capital next to a lower-case letter or a digit: an all-small-caps "OTKA" by itself does
  not say whether it is a name, an acronym or the name of a function in a formula (`OPT(x)` in a
  set: read as text it cut that formula in three, measured on a corpus upload), so it reads as
  before.
  `_Row.is_script` and the tests of a glyph's size against its row's that decide "script" from
  size alone (`_equation_block`, `_leading_script`, `_stacked_tail`) skip `small_cap` glyphs; the
  tests that also look at the baseline (`_script_glyphs`, `_Row.size`, `_stack_position`,
  `_over_arrow`) need nothing. TeX's `\textsc` in a font of its own (CMCSC10) is one size with the
  lower-case letters in the file: it reads as typed and was never affected.
- Beamer sets math letters in Latin Modern Sans Oblique, a text font. Short italic runs
  (at most three letters) are variables, not words, when the row also has upright
  letters, or has a relation sign and no longer italic run: "at" in `v = v₀ + at` is
  not the English word. Beamer captions have no number ("Figure: ...") and still attach.

### Slide decks (`pipeline/slides.py`, 2026-11-03)

A deck is read as a sequence of slides, not of pages. `_read_deck` (in `_analyze_document`,
after the page rows are read, before the margin filter) shows `slides.detect` every selected
page (size, PDF page label, text rows, big pictures) and the file's `/Creator`.

- **Detect.** At least 80 % of the pages are slide-shaped (landscape 4:3, 16:9 or 16:10, within
  2 %; a landscape Letter or A4 sheet is not), and the file is one of: `/Creator` says Beamer; the
  pages have at least two different labels and the labels group some pages and not all (a label
  tree with a prefix and no numbers labels every page alike and is not evidence); or at least
  70 % of the pages have a title (`slides.TITLED_SHARE`): the largest text in the top third, or a
  title slide's title (below). The last needs no Beamer and no labels, which is what a PowerPoint
  or Keynote deck exported as PDF has. 70 % is chosen from the files measured: the decks have a
  title on 91 to 100 % of their pages and the slide-shaped files that are not decks (photographed
  and pictured slides, a template page) on 0 to 33 %, so any share in between separates them;
  70 % keeps a deck with a third of its pages untitled (a picture slide, a credits page). For
  the titles route a Letter or A4 sheet on its side counts as a slide page too: a deck printed to
  PDF is on one (the one such file among the corpus uploads, 39 pages, has a title on 97 % of
  them; the corpus holds no landscape sheet that is not a deck, so the titles are the only guard
  measured). Beamer and page labels still need the slide shape. A landscape file with no titles,
  a portrait document and a landscape table are not decks and are read as before (`_SLIDE_DECK`
  False: no rule below applies). A
  page kept whole as its image (a photographed slide) has no text, is never dropped, never makes
  the pages beside it a frame, and is untitled; it does not count against the titled share
  either (the share is of the pages that have text), so a deck with some scanned slides is
  still a deck, and a file whose pages are all kept whole is not. The deck number of each page
  of such a deck, scanned ones included, is on `Layout.deck_pages`; a heading the page reader
  makes on a scanned slide (`image_page`) is given that deck's reason (`slide_deck N`) in
  `run`, and `heading_levels` puts its level under the deck's title: the reader numbers the
  headings of a slide from its own title (1), which is a slide title (level 2) in a deck with a
  title, as the typed slides' titles are (F-254: the mixed deck's scanned titles came out one
  level lower).
- **Several decks in one file** (`slides._decks`). A deck ends where the page size changes, and
  where a title slide comes when the file's first page is one too. A title slide is a page whose
  largest line is at least 1.25 x an ordinary slide's title (the median title size of the file)
  and which has at most three other lines in its body (the top and bottom 12 % are headline and
  footline): a title, a subtitle, an author, a date. Its title may be anywhere on the page. A deck
  that starts on a title slide has that slide's title as its title (`Deck.starts`); the title's
  lines are one title when they follow each other in one size. When the file has more than one
  deck, or a deck has a title, every heading on a deck's pages gets the reason `slide_deck N`
  (`slides.DECK_REASON`) and a deck title also `slide_deck_title`. `layout.heading_levels` then
  judges heading sizes deck by deck: a deck's title is level 1, each page's largest heading is
  its slide's title, one level under the deck's title whatever its size (one slide sets its
  title in 80 points, the next in 48), the page's other headings are clustered by size under the
  slide titles, and a heading with no deck (the pages of a portrait part between two decks) is
  judged on its own. So two lectures in one file have titles at one level
  and slide titles one level below, whatever sizes each uses; `assemble.title_plan` (the one H1,
  SW6c) then writes the document's title above the deck titles, or makes the only deck title the
  H1. A file with one deck and no title slide gets no reasons and the levels it had.
- **Frames.** Consecutive pages with the same label are one frame (hyperref gives every overlay
  step of a Beamer frame its number); without labels, the same title text. In a deck found by its
  titles alone (no Beamer, no labels) a frame is a run of pages each of which extends the page
  before it: every line and every picture or drawing of the earlier page is on the later one
  (`slides._repeats`, the same test as below), at the same size. Two slides that share a title
  and differ in content ("Example", "Example, continued") are two frames. The labels are read
  by `page_labels.pdf_label`, which decodes the UTF-16 form PyMuPDF returns for xdvipdfmx files
  (`<FEFF0031>`): `clean_label` refused it, so no Tectonic deck's own labels were used before
  (the page markers fell back to the number printed in the footline).
- **Overlay steps.** A page of a frame is dropped only when everything on it is on the frame's
  last page (`slides._repeats`): each row is a whole row there (the same text exactly, not a part
  of a longer row), the rows are in the same order and within 10 % of the page of where they were,
  and every picture and drawing is there too. Pictures are every raster image (a digest of its
  pixels, `get_image_info(hashes=True)`, empty ones skipped) and the vector drawings in the body
  (kind, color and box; the top fifth and bottom 12 % are the headline and footline), each within
  3 pt of where it was. A page's pictures and drawings are read only when its rows are all on
  the frame's last page (`slides.LazyMarks`), so a file that is not a deck never decodes its pictures
  for this. So `\pause`, `\item<n->` and `\uncover`, which only add, collapse, and a
  step with a different picture, a changed row or a reordering stays. A dropped page is removed
  before margins, page types, tables and rows are read, so it costs no crop and no model call, and
  it is not in `Layout.pages`, `page_sizes` or `page_types` (as if the user had not selected it).
  A page that has something the last page lacks (`\only<1>{A}\only<2>{B}`) stays, and the kept
  pages of the frame are numbered: the title heading says "(step 1 of 2)". Measured on the
  fixtures: `beamer_overlay` 6 pages to 2 (67 % fewer), `slides_steps` 13 to 6 (54 % fewer).
- **Frame titles.** A page's title is its largest text in the top third, at least 1.15 x the
  deck's body size (the lines that follow it in the same size, each within half a line of the one
  before, are part of it). A line that is a formula or a bare number and nothing else (no
  word of three letters besides function names like "sin" and "log", and a math sign, an italic
  math letter or no letter at all; a name that ends in a plus or two ("C++", "g++", "A+") and
  "Q+A" are not math signs, in any line, so "C++ 17" and "C/C++" are text, while "A+B", "N+1"
  and "x + y = 2" are formulas: `slides._is_formula`) is never a title, nor the title of a
  title slide: a slide with a large equation under its title is titled by the title (on the
  html-slides deck 17 of 22 slides took their equation or a lone "1"). Whether a file is a deck is counted as before: a page whose largest top line is a formula is titled like a slide all the same (`detect`, `_title(formulas=True)`). A slide's title is always a heading (reason `slide_title`, plus `larger font` so the
  levels group the titles): a title the heading rules read as a paragraph is promoted, and a
  title that a theme's headline picture took as its label (Warsaw: the figure's text is "Waves
  Superposition Cases") becomes the heading in place of that figure. Every frame is therefore a
  section, and the assembler writes one `<h2>` a frame (docs/output.md "One h1"). Frame titles
  used to vanish on overlay decks because they repeat on the pages of one frame, which made them a
  running header; the dropped steps are no longer reference pages for the margin filter
  (`_reference_pages(skip=)`).
- **Furniture.** After the blocks are made, `slides.finish` drops, with their inline equations:
  paragraphs above the title that are at most 0.8 x its size, in the top fifth (the headline's
  section and subsection names); a drawn bar across the page (at least 60 % of the width, at most
  18 % of the height, in the top fifth); and a bare number or "3 / 20" in the top or bottom 12 % that is furniture: the page's own label
  ("3" on slide 3) or a number in the same place on most slides. Any other number there is content.
  The footline text, the navigation symbols and other repeated bands are the margin filter's, as
  before. The headline's text is kept on `Deck.context` (page to names, top to bottom) and is not
  in the output.
- **Columns** (`_find_gutter(slide=True)`, tried only when the strict rule finds none): four rows
  are enough, a side needs two rows whose left edges line up and a median row width of 15 % of
  the text width, and both sides must have a word of three letters (`_words_both_sides`), so the
  brace and the cases of a piecewise function are not two columns. Left column, then right.
- **A bullet that wraps onto a line of math** ("The energy is zero at" / "x = 0.") was a display
  equation: `_wrapped_maths` compared the line with the paragraph's left margin, and a list
  item's text starts after its marker. In a deck the marker's x counts; elsewhere the rule is
  unchanged.
- **Ligatures** (every file): MuPDF gives the "ff" of "Stiffness" the whole glyph box and the
  second f a box of no width at its end, so everything that sorts characters by x put it after
  the next letter ("Stifnf ess", "Ofcfi e", "Specifci"). `_text_blocks` shares the glyph box
  among its characters (`_spread_ligatures`) wherever layout reads characters from the PDF: the
  rows (so tables, whose cells come from them), `line_glyphs` and `page_glyphs`. Text running down
  the page is shared along y; other angles are left as MuPDF has them.
- **Glyph names** (`pipeline/glyph_names.py`, `data/glyph-names.tsv`; every file). The text layer
  decodes a glyph through the font's table to Unicode, or through its name when Adobe's list has
  it. TeX's extension fonts (CMEX10, CMEX9, txex, LMMathExtension10) have neither for their sum and
  integral signs, radicals, big brackets and the pieces of built-up ones, so MuPDF gives U+FFFD or
  one of Adobe's private-use codes. The file still names each glyph inside the embedded font
  program. `_text_blocks` runs a ladder over the page's raw text: (1) the file's own table
  (MuPDF); (2) `glyph_names.recover`: the glyph's name, read with MuPDF (`fz_get_glyph_name`,
  through PyMuPDF, so no font parser is added), looked up in one table kept as data (the names of
  TeX's extension fonts; a sized variant such as `summationdisplay` is its base character, a
  built-up piece the Unicode piece character: `⎧ ⎨ ⎩ ⎪ ⎮ ⎯ ⏐`); (3) `font_codes.remap`, by font
  name; (4) `glyph_names.mark_gaps`: a glyph still undecoded becomes `output_chars.GAP` (U+2BD1), not
  U+FFFD and not dropped. A raw character says how it was read in `"how"`: `"name"`, `"font"` or
  `"gap"` (no key: the file's own table). Nothing reads the key yet; it is there to tell a
  recovered character from a decoded one. A raw character becomes a glyph by font and origin
  (`page.get_texttrace()` lists the glyph ids MuPDF drew), and by the character the text layer gave
  it, because TeX draws an overlay (the slash of `\l`) at the origin of the letter it crosses; a
  glyph two names would fit is left alone. A glyph's name is read once per font and page, and only
  on a page that holds an undecoded glyph. The gap mark is not a private-use character: the output
  filter (`output_chars.NOT_TEXT`) turns it into "[unreadable character]" on the student page, the
  lint (`html_checks`) fails on it as it does on U+FFFD, and the sign check reads it as an
  unnamed glyph that may be any sign (`signs._unnamed`). To rebuild the table:
  `scripts/make_glyph_names.py` (source and license: `pipeline/data/README.md`).
- **Font code tables** (`pipeline/font_codes.py`, `FONT_CODE_TABLES`, on; every file). A font with
  no usable ToUnicode gives its codes as control characters, private-use code points or plain
  letters that are not what is printed. `_text_blocks` rewrites them, before rows, markers, math
  detection or any output read the page, so this module is the one place a code is mapped. Tables,
  keyed on the font's base name (subset prefix removed): the Symbol family (Symbol, SymbolMT,
  SymbolPS, Standard Symbols; OpenSymbol for its private-use codes), for private-use codes
  (U+F061 is alpha) and Latin letters (SymbolMT's "h" is eta); Wingdings (not 2 or 3), the five
  codes Word uses for bullets and an arrow (U+F0A7 `▪`, U+F0D8 `➢`, U+F0FC `✓`, U+F076 `❖`,
  U+F0E0 `→`, or the Latin-1 letters a font with no ToUnicode gives); MT-Extra (U+F06C `ℓ`); the
  APS math font AdvP4C4E74 (thorn is "+",
  one-quarter "=", eth and capital thorn the parentheses); and the MathTime Pro 2 fonts (MT2MIT,
  MT2MIS, MT2MIF, MT2BMIT, MT2SYT, MT2SYS, MT2SYF, MT2SYAT, MT2EXA: their parentheses read as "."
  and "/", "+" and "=" as "C" and "D", their big delimiters, sums and bracket pieces as digits
  and capitals). A MathTime row is keyed on the font and on what its glyph is read as, which is
  the same in every form of the font a file holds. A glyph the reader gives as U+FFFD or a
  private-use code is keyed on the name the file gives it (`_Table.named`, asked by
  `glyph_names.recover` before its own table: a Type 1 slot name such as "NUL", or for the CID
  form the glyph number as text); the names the table knows must agree on one character. A big
  delimiter is the character it is sized from, a piece of a built-up one the Unicode piece
  character. Glyphs not settled by eye have no row (a lozenge in MT2SYAT, the stretched part of
  a long brace in MT2EXE) and a glyph read as a space cannot have one. The same code is another glyph in
  another font (U+F0A7 is a club in Symbol), so a code is never mapped without its font. Also, for
  fonts with no table: TeX's ligatures, dashes and quotes at control codes (OT1 or T1, told by the
  font's own codes), a spacing accent printed over a letter becomes the accented letter ("Ren´e"),
  and an unreadable glyph that starts two or more lines of a page, set off from the text by a gap
  and nowhere inside a line, is a bullet. A code with no row stays as it was (and is a gap
  mark by the end of `_text_blocks`). Left out: Wingdings 2 and 3, Webdings, ZapfDingbats, MSAM, the other MathTime fonts (MT2BMIS, MT2EXE and any not named above), and MSBM, whose
  double-struck letters `_Char.styled` already gives equation text (`_LETTER_STYLE_FONTS`). With the setting
  off, only U+F0B7 and U+F0A7 are mapped (to `•` and `▪`), whatever the font.

Known gaps: Beamer's ball and circle bullets (Warsaw, Madrid and the other infolines themes) are
small pictures, not drawn shapes, so a list in those themes is one paragraph (the default theme's
triangles are glyphs and work); sidebar themes (Berkeley, Goettingen) put the outline in a column
beside the frame, which is read as a column of text; a deck that is neither Beamer nor has
repeated labels (a PowerPoint export) gets none of this, and its steps, if any, stay pages; `\only`
steps are kept whole (one page each, labeled) rather than merged.

### Inline formulas in LaTeX papers (2026-10-21)

Found by the error analysis of the real-world set ([eval-equation-rootcause.md](eval-equation-rootcause.md)):
a paper page is mostly `$x$` in running text, in a font the OpenStax prints never use. What the
layout learned from it, all from the text layer and the glyph boxes, no model call. The rules that
come from what a TeX author types (italic variables, bold letters with an index, the edges: parentheses, units,
citations, word spaces) apply to PDFs typeset by TeX only (`_TEX_MADE`, from `page_type.producer_hint`: the producer or creator names pdfTeX, XeTeX or XeLaTeX (`xdvipdfmx`, what Overleaf offers), LuaTeX, LuaHBTeX, dvips, dvipdfm(x), Tectonic, arXiv's `tex2pdf` ("arXiv GenPDF (tex2pdf:...)", one word that `tex` does not match) or VTeX, or says LaTeX or TeX, as whole words in `page_type.TEX_PRODUCER`; a substring match took Quartz's "PDFContext" and iText for TeX): an
OpenStax print from Chromium, where emphasis is italic and the unit is inside the math, is read as before
(`scripts/layout_identity.py` on 126 PDFs: 111 identical, the rest explained in
[eval-equation-rootcause.md](eval-equation-rootcause.md) section 7). Captions, ellipsis lists and `:=` apply to every producer.

- **Italic variables** (`_italic_variables`, evidence `italic variable in prose`). mathptmx, mathpazo,
  Charter and newtx set the Latin letters of a formula in the text family's italic, so a lone `S`,
  `N_i` or `K(t)` has no glyph in a math font. A run of italic letters (at most three letters in a
  group, a group that is not a short English word, so "not" and "for all" stay words) standing in
  upright prose, whose neighbors are not italic letters, is a variable. Not variables: an italic
  phrase (a theorem set in italics, where `a` and `I` are words and a short word beside a longer
  italic one is text, `_word_lengths`), a label (`(a)`, `a)`), a reference to a part of a figure
  (`6(a)`, `5(d,e)`), a number and a space and an italic letter (`-49 m`, a quantity with its unit).
- **A bold letter with an index** is a vector set in a text font (`v` in Times Bold with a lowered `2`);
  its script is an index, not the count of `H2O` (`_bold_variable`).
- **Where a formula starts and ends** (`_trim_run`, `_inline_runs`). The author's math stops at the dollar
  sign, so the parentheses of the sentence around a whole formula (`(x = 0.9)`; a tuple, which has a
  comma at its top level, keeps them), the upright unit after a number (`915 s`), a numbered
  citation after a space (`[8]`) and an opening bracket that the formula does not close are not part
  of it. A visible gap of at least `WORD_SPACE` (0.25 em) between two operands ends the formula (TeX
  sets nothing between operands, so the gap is a word space of the sentence: "eps(k) (i = 1)" is two
  formulas). A list with an ellipsis (`i = 1, ..., S`) is one formula, a map `H : R -> S` is one (a colon
  between two runs, the second with an arrow or a membership sign: `_join_signatures`).
  On the 25 development papers 27 of 31 formulas cut inside a pair of parentheses had the pair
  outside the author's math, and of the formulas that ended in an upright unit 37 were span failures
  against 2 that were exact.
- **Inline span edges from glyphs** (`pipeline/glyph_math.py`, 2026-11-03). Five more rules, all gated on a TeX-made PDF, read what the
  page shows at an edge, and only move the edge of a run that was already math (none makes text a formula,
  so no text is lost: what leaves a run stays in the paragraph). (1) A comma, colon or semicolon that is
  raised or lowered between two raised or lowered glyphs is part of the index: `\rho_{m,q}` was read as
  `q(x)`, `g_{1:T}` as `g_1`. (2) A blank whose two neighbors touch (gap under 0.1 em) is no space: the
  text layer puts one between a radical and its radicand, and `O(\sqrt T)` was cut at the radical.
  (3) One of a short list of operator names that papers set upright (`glyph_math.OPERATOR_NAMES`: `Pr`, `Cov`,
  `supp`, `vech`, `Unif`, ...) that touches an opening parenthesis is a function name and the formula goes on through
  it: `\Pr(A)`, `\mathrm{supp}(\psi)`. The text layer cannot tell an upright name from a word of the prose (both are in
  the text font), so the rule never guesses: an acronym (`ARFIMA(1, d, 1)`, `CTCS(n)`), a word and a reference (`Eq(4)`,
  `Smith(2019)`) are left alone. (4) A percent
  sign after a digit is part of the formula (`\phi = 5\%`), and a word space after it ends it. (5) The tail
  of a run that is a unit after a number (upright letters of the text font, a factor's raised or lowered
  exponent figures, other factors after a space: `24h`, `5 m`, `10^{-11} m^3 kg^{-1}`) is out of it,
  except raised figures with no number before them (`15O`, a nuclide). The unit rule replaces the old
  one-letter case (`_tail_outside_maths`). Measured with `scripts/eq_span_edges.py` on the development
  gold, layout alone: [eval-equation-diagnostics.md](eval-equation-diagnostics.md), "Inline span edges".
  What it does not do: a formula the author split in two (`h = $1.0 \times 10^7$`, the relation set as
  text) cannot be told from one formula by the glyphs, accents drawn as a separate glyph that PyMuPDF
  puts in another line (`\widetilde W`) still end a run, and a top-level comma between two variables
  (`S, M \to \infty`) still ends it.
- **Short formulas the runs miss** (`glyph_math.relation_runs`, `layout._with_simple_maths`, 2026-11-03). A relation
  glyph in a math font (`\approx`, `\sim`, `\le`, `\ge`, `\ll`, `\gg`, `<`, `>`) and the number after it (`\approx 0.14`,
  `\geq 2`, `\geq 90\%`) is one inline formula when the glyph before the relation is no math and no digit: the run finder
  dropped such a relation as the tail of a formula that a line break cut, and the number had no evidence of its own. A
  run that is only the number is replaced by the whole; a longer run is left alone. Measured, with the evidence that was
  tried and left out, in [eval-equation-diagnostics.md](eval-equation-diagnostics.md), "Simple inline math": a number
  with a percent sign, a number with a unit, an equation reference `(1)`, a bare number and an upright letter have no
  glyph that tells the author's `$...$` from text (a percent rule predicted 20 and aligned 4), so none is a formula
  here; a page number, a citation, a list marker, a year and a figure number are never taken
  (`tests/fixtures/layout/latex/short_maths.pdf`).
- **Figure captions** are cut like paragraphs (`_attach_captions`): `Element.caption` stays plain text,
  `Element.caption_marked` has an inline marker where each formula was, and each formula is an
  equation element whose `parent_id` is the figure (id right after the figure's). `assemble` writes the
  math into the `<figcaption>`; the equation step reads them as any inline equation. Table captions
  are not cut this way (the table step owns them): a table caption is joined as a cell is, with its
  raised and lowered glyphs marked (`tables._line_text`, "CP³" stays "CP³" in the layout output and
  in the HTML).
- **Bold italic.** `CMMIB`, newtx's `bmi`, Nimbus `MediItal` and `BoldItalic` fonts give bold-italic code
  points in the equation's text layer (`_Char.styled`), so the equation step can tell `\boldsymbol{x}` from
  `\mathbf{x}` (docs/steps.md "Letter-style check").

### A stray opening brace (2026-10-21)

OpenStax also prints "Solve the system by substitution. {" with the brace the last glyph of the
sentence's row and the two equations in rows of their own to its right, level with the brace and
not with the sentence. `_stray_brace_cut` cuts the brace off the sentence's row when a math row
starts within two font sizes to its right, another math row sits under that one, and the brace's
middle is level with the two; `_math_neighbors` then takes the brace row into the system, so the
crop shows the brace and the text reads it first (`_equation_block`). Measured on dev-v2 with the
layout alone: Elementary Algebra 5.2 has 80 instead of 53 of its 101 gold equations cut as the
answer key has them (text layer within 0.99), no other section moves by more than two; nothing else in
the 121 PDFs of `scripts/layout_identity.py` changes except nuclide paragraphs and the brace text
order (headings, figures, tables, lists, kept pages are identical; the "scripts flattened" notice
goes where a nuclide was raised text).

### Chemistry in running text and on display lines (2026-10-21)

Formulas, ions and reactions are mathematics, so in a paragraph or a list item they are inline
equations, and a line that is one reaction is a display equation (`pipeline/chem.py`; the full
description, the decisions, the measurements and the limits are in [chemistry.md](chemistry.md)).
Raised and lowered text that is not chemistry (units, footnote marks, a letter subscript) stays
raised text (next section).

- **Words** (`_chem_words`). The row is cut into words at a blank, at a gap of 0.25 of the glyph
  size (TeX sets no space after a script: `Cl-and`) and around an arrow glyph, which is a word of
  its own together with the dash that is its shaft (`−→`). Each word carries the script kind of its
  characters (`_script_glyphs`, so only the scripts of upright text count).
- **A formula** (`chem.label_word`): the letters read as element symbols, and there is evidence
  only chemistry has: a lowered number on an element or a bracket (`H2O`, `Al2(SO4)3`), a raised
  charge (`Na+`, `SO4 2-`, a number then a sign), or a state symbol (`NaCl(aq)`). Sentence marks and
  a pair of parentheses around the whole formula are cut off (`(NaHCO3).` is `NaHCO3`); a bracket
  around it is kept (`[H3O+]`, a concentration). Not a formula: `cm3`, `m2`, `Kw` (a letter
  subscript), `K-1` (a raised sign then a number is an exponent), a footnote mark (`Nature1`), a
  word. Plain letters and digits that a PDF set at one size (`H2O` with no lowered 2) are never
  marked: the text layer cannot tell them from a word.
- **A reaction** (`chem.reactions`): an arrow (`→ ⟶ ⇌ ⇄ ↔ ←`, `−→`) with species on each side, the
  species, the `+` and the coefficients taken as far as they go, and at least one formula in all;
  words that read as elements but carry no evidence (`HCl`, `NaOH`) count only inside a reaction.
  A trailing number belongs to the sentence. Several arrows in a row are one reaction.
- **Where it applies**. `_inline_runs` adds the formulas and reactions of a row to its math runs
  (`_with_chemistry`; a run that overlaps one, such as the arrow that used to stand alone, is taken
  into it). `_classify_row` makes a row a display equation line when the whole row is one reaction
  (`_reaction_row`: the ink outside it is at most the punctuation that ends it), so the net ionic
  equations of a LaTeX page are display equations and not a paragraph with an arrow cut out of it.
- **Spaces**. An inline equation that stands against its neighbors with a visible gap and no blank
  glyph between them (the text after `SO42-` in TeX output) gets a space on that side
  (`_gap_before`, `_gap_after`); `releases ⟦e1⟧ions` read as one word before.
- Measured on the 121 PDFs of `scripts/layout_identity.py`: 96 are identical, and the 25 that differ
  are the chemistry material (21 files: the section prints, the authored chemistry document, a fixture), the Word lecture (its
  formulas and ions), one slide whose reaction line was a heading and is now an equation, and
  a space after "Simplify:" in the two prints of an algebra section. Nothing in physics, algebra,
  statistics or the other slides and Word material gains a formula that is not one.
- **Lists** (`_split_chemistry_lists`, `_reaction_list_row`). A run of species cut by top-level
  commas or semicolons into pieces that are each one species (`CO3 2−, F−`: a count or a charge,
  an element symbol first) is one inline equation per item, and a row that is two or more
  reactions with a semicolon between them (`(a) A ⇌ B; (b) C ⇌ D`) is a prose row whose
  reactions are inline equations of their own, the list marker left out, not one display line.
  This works on the letters whatever their font: chemistry set in a math font has no
  upright-text evidence for `pipeline/chem.py`.
- **Half-reactions** (`_half_reaction_label`). A label that names a half-reaction and ends in a
  colon (`anode (oxidation):`, `Overall:`) joins the reaction that follows it on the same baseline
  up to 12 glyph sizes away (`HALF_REACTION_GAP`); the block was cut after the label.
- **Labels over an arrow** (`_label_over_arrow`, `_arrow_under`, `_arrow_label_flags`). A script-size
  label over or under an arrow, whatever it says and however long (`Δ`, `heat`, `high
  temperature`, `500 °C, Fe`, one over and one under), is part of the reaction: it joins the
  arrow's line, it is read after the arrow in words of its own, and `chem.Word.over_arrow` makes
  each of its words a label for `pipeline/chem.py`. A long arrow (`−−−−→`) is one arrow word
  (`chem.MAX_SHAFT`). The labels do not count in the row's size (`_Row.plain`), which a long label
  used to pull down to the label's size and with it every subscript of the formulas. A script
  at the height of the arrow (a charge before it) is not a label.
- **`E°cell`** (`_degree_over_label`). A degree sign over the start of a lowered label
  (`E°cell`, `E°cathode`) is read after the label, not inside it (`E c°ell`, which cut the inline
  equation in two); several degree signs in one row are scripts of their own; and the degree sign
  after a symbol is part of the inline run (`_inline_runs`).

### Raised and lowered text in prose (backlog T2-4, T2-5)

Text sets formulas, ions, units and nuclides with scripts that the text layer flattens:
H2O, Na+, SO4 2-, g/cm3, 226Ra. A paragraph or list item or heading keeps them as
`Element.script_runs` (`ScriptRun(start, end, kind="sup"|"sub")`, offsets into `text`), which
the assembler writes as `<sup>` and `<sub>` (docs/output.md "Raised and lowered text").
**Since 2026-10-21 formulas, ions and nuclides in a paragraph or list item are inline equations
instead** (the section above and "Nuclides" below); units, footnote marks, letter subscripts and
anything in a heading or a table cell stay raised or lowered text, which is what the rest of
this section describes.

- **Which glyphs** (`_script_glyphs`): smaller than the row's text (`SCRIPT_RATIO`) and off
  its baseline by 4 % of the row size (`SCRIPT_OFFSET`: Word and LibreOffice lower a subscript
  by 8 % of its size, TeX by 15 %), or full size with MuPDF's superscript flag and raised by
  20 % (`FLAGGED_RAISE`).
- **Whose scripts** (`_script_base`): a run belongs to upright text. The base is the
  letter, digit or closing bracket just before it (a gap of at most 35 % of the row size), a
  closing bracket only when the group it closes holds upright text (`(SO4)3`, `[Zn]2+`), or,
  for digits only, the capital letter right after it (`226Ra`, a mass number; before a word,
  never after one; since 2026-10-21 that case is no longer raised text, see "Nuclides" below).
  A script of an italic letter or a letter in a math font (`E` with a
  subscript `k`, `x2`) is not given to this: it stays an inline equation, as before, and the
  model reads it.
- **Nuclides** (`_nuclide_runs`, 2026-10-21): the mass number raised and the atomic number
  lowered before an element's symbol (`14C`, `235U`, `4 2 He` with the 4 above the 2), in running
  text, are one inline run from the first number to the last letter of the symbol, so the equation
  step reads it and the speech says "carbon 14, atomic number 6" (it was `<sup>14</sup>
  <sub>6</sub>C`). The group must be all digits (at most six), at the start of a word, with the
  symbol of an element right after it (a blank the PDF puts between is not a gap, `NUCLIDE_GAP`)
  that does not go on into a longer word; `10 m2 S`, `3 Xx` and `235Uranium` are not nuclides.
  Measured on dev-v2 with the layout alone (`--fake`): the authored chemistry document finds 70 of
  its 108 gold equations instead of 64 and Chemistry 2.3 finds 12 of 38 instead of 7; the
  isotope tables of 2.3 are tables, not prose, and are untouched. The new inline equations in a
  Word lecture are real nuclides its gold does not list as equations.
- **Evidence** (`_inline_evidence`): a script of upright text is no evidence that a run is
  math. `H2` was an inline equation, `SO4` was not, and half a formula reached the model
  ("2O3", "34-"); now a run that only such scripts made is dropped. Inside a run that is
  math for another reason (an arrow, a relation, an italic variable with an operator: `2H2 + O2
  -> 2H2O`, `E = mc2`) the scripts stay part of it. A formula or an ion is math for its own
  reason, found by `pipeline/chem.py` (the section "Chemistry in running text" above), so `H2` and
  `SO4` are no longer dropped, they are inline equations.
- **Stacked scripts** (`_merge_baselines`, `_insert_script`, `_stack_position`): a lowered and
  a raised script one over the other (the 4 and the 2- of SO4 2-, the 6 and the 14 of a
  nuclide) arrive as two fragments, and the lowered one used to start a paragraph of its own
  (MuPDF starts a line with it). It is now folded into the row it is under, read lowered
  first after a base (`SO42-`) and raised first before one (`14 6 C`).
- **Rows of mostly scripts** (`_Row.size`): a row's size is the size of most of its glyphs,
  and a nuclide's "92 U -> 234" has five script glyphs and three full-size ones, so its
  scripts were taken for its text, never recognized as scripts, and the equation was cut
  into pieces (the symbol of the nuclide lost, a heading made of "238"). A row whose small
  glyphs are 62-85 % of a larger size, each run of them off the larger glyphs' baseline by
  at least 4 % and at most 70 % and within 60 % of a larger glyph, is now set in the larger
  size, provided the larger glyphs include a letter and no word of three letters. The numbers
  of a fraction do not qualify: they are half the size (OpenStax), or they stand beside an
  operator or a sentence (`Simplify: 5/12 + 2/36`: the larger glyphs are a plus sign, or a
  word), which a first version of this rule took for scripts: it turned three equations of
  "Simplify complex rational expressions" into figures.
- **Spaces**: TeX sets no space glyph after a script (`Cl-and`); a gap of 30 % of the glyph
  size at the edge of a script gets one (`_marked`).
- **Carrying the offsets**: pieces of a paragraph are cleaned, joined and de-hyphenated on
  the way, so the runs travel as private-use marks around their text
  (`pipeline/script_marks.py`) and are cut out once when the element is made.
- **Looser fallback** (`_loose_scripts`): raised or lowered glyphs that no equation took and
  that are not a script of upright text (the 2 of `(a + b)2` after a line break, the `cell`
  of `E°cell`) are still given a kind when something stands before them: written raised they
  read better than flat.
- **Flagged when flat** (`scripts_flattened`, optional "Worth a look"): raised or lowered
  glyphs left with no base, lowered glyphs in a figure caption, and lowered glyphs in a table
  (a table cell keeps raised marks as superscript characters, `tables._marked`, and not
  lowered ones).
- **Raised digits in a caption**: a caption is a plain string with nowhere to hold a script
  run, so a raised run in it is written in superscript characters, as a table cell's and a
  table caption's are (`script_marks.raised_characters`: "10⁸", "CP³"; a run with a character
  that has no superscript form is written "^(...)"). A figure caption was written "108" and
  "CP3" and only flagged; the page now has the raised form in `Element.caption_marked`, and
  `Element.caption` stays the flat text the figure step is sent, so no recorded request stops
  matching. Listen says superscript characters as a `<sup>` is said ("10
  superscript 8"). A power of ten in a TeX paper is part of an inline equation (`power of
  ten`); if that equation is not converted, its alt text begins with the warning that
  exponents run together in the characters, as it does for any equation with a raised part.

Not done: lowered text in table cells (the cell text stays flat, the notice says so);
a script that is part of a word the text layer splits across rows.

### Equations pasted as pictures (backlog T2-3)

A MathType or Word equation pasted as a picture, a formula saved from a slide: raster images
and small vector drawings were figures, so the figure step wrote alt text for them and
nobody checked the formula. A picture of an equation has no text layer, so `layout` now
decides before it makes a figure (`_equation_pictures`, `pipeline/equation_images.py`):

- **The picture**: not larger than a few lines of text (at most 10 body sizes and 110 pt tall,
  520 pt wide), black marks on white with almost no color (`MAX_COLOUR`), 0.4 to 25 % of the
  area inked, at least 60 % white, and no single piece of ink spanning 70 % of both sides
  (a plot's axes, a box). It is rendered at 144 dpi, shrunk to 400 px, and read as a bitmap;
  no model is asked. Neither a picture with labels nor one with a caption ("Figure 2.1" within
  36 pt, overlapping it) is ever one.
- **In a line of prose** (text on both sides, or prose on one side and punctuation on the
  other): the picture becomes one character of the row (`PICTURE_CHAR`), the rows beside it
  are joined, and the paragraph keeps one text with an inline marker; the equation has
  `display="inline"`, `text=None` and the reason `equation image`. Pictures drawn under 12 pt
  on a side were dropped as bullets and spacers before this (a one-letter equation is that
  small); `_raster_figures` now lets `_equation_pictures` keep one when it stands in a line.
- **On a line of its own**: a block equation (`display="block"`, `text=None`, reason
  `equation image`); the equation number printed to its right is its `equation_label`.
- **Then the equation step** (docs/steps.md "Equations as pictures"): a usable reading is
  always a decision, because nothing can confirm it.

Not done: equations that Word exports as vector drawings (EMF/WMF, drawn as paths) are only
considered when the layout already made them a figure by `_vector_figures`; they are
classified like a raster picture, but they have not been measured; handwritten equations.

### Answer options and right-to-left text (WS2.4a, SW54a)

Two things layout does not get right are never left silent: both set a reason on the
paragraph or list item (`Element.reasons`). The run turns each into a decision on that
element (`notices.text_decisions`, WS16.6 part 1): the element's own `Fix` with
`status=NEEDS_REVIEW`, `review="required"` and the reason `text.rtl_order` or
`text.option_labels`, the same fix shape a scanned page's paragraph has. Its proposal is the
element's own words; the person says "Looks right", changes the words, or uses the page
tools. A block with both flags has one decision, `text.rtl_order` (the words in the wrong
order change what is read more than options run together do); the other flag keeps its bare
required notice. The notice (`option_labels`, `rtl_text`, listed under "Not handled in this
file") is derived from the pending decision (`notices.from_fixes`) and goes when it is made.
A result stored before this has only the reason; `notices.from_reasons` still lists the
notice, and `web.states` still holds that job at "Ready" (there is nothing to decide).
Not built: the page record, the document card, and the per-source switch. The reviewer's tool
for a block that is still whole is "Split it" in the page tools (docs/web.md, "The page
tools"); the notice's words say so.

- **Option labels** (`option_labels`, `_later_labels`, `_split_options`): a block that
  starts with a label ("(A)", "a.", "1)") and holds the next one of the same form later in
  its text, set off by a gap wider than half an em, is a grid of options that reading order
  merged. A line of text that stands alone is cut at each later label, one run per option;
  any other block that `_option_grids` does not resolve is kept whole and flagged. Where a
  table is detected over the grid, the table keeps it.
- **Option grids** (`_option_grids`, WS2.4b; default path, tree on or off): options set two or
  four across are one list in label order, whether the page sets them row by row ((A) (B) over
  (C) (D)) or column by column ((A) (C) over (B) (D)). A grid starts at a row with an option label
  and takes the rows after it that start with a label of the same form (a row that holds
  several labels set apart is cut at each) and the lines wrapped under one of its columns. It
  is a grid when its labels are a full run of letters or numbers from the first (A, B, C or
  1, 2, 3), each once, two options share a line, no option ends the way a clause of a
  sentence does ("(c) X," "(d) Y, and": a list in prose), and it follows a question (`_asks`:
  a line ending in "?" or ":", or the lines of a list item of another marker family such as
  "4." over "A."; a grid of capital letters that nothing above it in its column leads into
  carries on a question from the page before). Subfigure captions "(a) ... (b) ..." and
  runs of numbered blanks follow no question and are left as they were. Then each option is a list item (flagged `_Marker.option`), the cells are read
  in label order (`_option_order` keeps that order when the page's blocks are put in reading
  order by their boxes), and the options are one list a level below the question they follow,
  whatever the x of their column (`_list_levels`). Anything that is not a full run (a label
  missing, a label twice) is left as it was, and a block that holds a later label is flagged
  as above. A right-margin points label ("(2 points)", "[4 pts]") on the baseline of a line is
  too far from it for `_merge_baselines`; `_runs` puts it at the end of that line's text. The
  region tree's grid rule (8) does what the default path cannot know: where a grid ends above the
  number of the next question that stands in the same two columns (`_Build._grid_above`).
  Not done: labeled parts inside one display equation ("(a) 2x + 3 = 11 (b) ...": the row is
  an equation, and `_split_labels` cuts only an equation number off its end).
- **Right-to-left text** (`rtl_text`, `_rtl_words`): a block with two or more words that
  have a strong right-to-left letter (bidi class R or AL) is flagged, because the order of
  the words in the row is the PDF's, which is not always the reading order (the reorder,
  SW54b, is on hold). A lam-alef ligature glyph (`_swap_lam_alef`) comes out of MuPDF with
  its two letters reversed ("إىل" for "إلى"); where an alef-family letter and a lam share
  one glyph box they are put back in order, in `_text_blocks`, so every reader of
  characters sees it.

### Bold letters in prose

Only with `FEATURE_NOTATION_MEMORY` on (the run's `ConversionOptions.notation_memory`);
with it off nothing here runs and the output is as before. A single bold letter in
running text ("the projection of **b** onto **a**") is a vector the text would otherwise
give as a plain letter, heard as the article "a" (UX review MF2). Layout makes it an
inline equation, and the equation step fixes it without a model call
(docs/steps.md "Course notation").

Two functions in `layout/` (`blocks._bold_letter_runs`, `bold._settle_bold_letters`), nothing else in the page flow changes:

1. `_bold_letter_runs`, per paragraph row, beside the inline math runs:
   - a math run that is one bold letter (a bold omega in CMMIB, a math bold code point)
     is marked `bold letter in prose` with the evidence `maths font`;
   - any other bold Latin letter that stands alone (its token, split at spaces, is the
     letter and punctuation only) is a candidate, except: a bold word in the token on
     either side (a bold phrase such as "Part A", a bold sentence); a label ("(b)"
     anywhere, "a)", "A." or "b:" starting the paragraph, and "a." or "b)" after the end
     of a sentence with a capital after it, as the parts of a question run on in one
     paragraph: "... the question. a. Find the sum. b. Find the angle."); a capital starting a sentence
     (the paragraph's first letter or after `. ! ? :`), because "A" is also the article,
     unless the course lists it. A candidate the course lists (`NotationEntry.canonical`)
     keeps the evidence `course notation`.
2. `_settle_bold_letters`, once for the whole document, keeps a waiting candidate only
   with evidence: the same bold letter in the document's math (a display or inline
   equation: `bold in the document's maths`), or, for a small letter, another bold letter
   that is math in the same paragraph (`maths in the paragraph`). Any other candidate
   goes back into the text as the plain letter it was.

Running text is paragraphs and list items (the blocks that may hold inline math), so a
bold letter in a heading or a caption is never touched; bold words and labels are left
alone, not raised as notes. The evidence is the element's last reason (content-free,
like every reason).

Measured on 2026-10-09 (`uv run python -m readablestem.eval.bold_letters --openstax`;
`tests/test_layout_bold_letters.py` pins the LaTeX numbers):

| Documents | Bold letters in the source's prose | Found | Missed | False positives |
|---|---|---|---|---|
| LaTeX fixtures (6) | 11 (all in the two-column handout `linear_algebra_twocol`: 10 bold vectors r, x, a, b and a bold omega) | 11 | 0 | 0 |
| OpenStax gold pages 2.1, 2.2, 2.3 | 9 (bold d, x, s, v) | 9 | 0 | 0 |

Recall and precision are 1.0 on both. The expected letters come from the sources: a lone
`$\mathbf{x}$` (or `\bm`, `\boldsymbol`) in the running text of a `.tex`, and a
one-letter `<strong>` in the OpenStax XHTML outside headings and captions that is not
continued as a formula. The other five LaTeX fixtures (`vectors_relations`,
`calculus_notes`, `kinematics_slides`, `problem_set`, `letter_styles`) have no bold letter
in their prose and get none (`vectors_relations` writes its vectors with `\vec`). On the
handout, 10 letters were kept by the same bold letter in its displayed math and the omega
by its math font; on OpenStax, 7 by the document's math and 2 by a bold letter nearby.
With a course notation listing bold d, v, a, x and s, the same 9 are found, each by the
course. Not covered: PDFs whose font does not mark bold (no candidate is seen at all
there), and bold letters in captions. A bold answer letter in a document whose same bold
letter is a vector elsewhere ("The correct answer to question 3 is **b**") is still made
math by the document's evidence; the equation step's own tiers then decide it.



### Figures

- **Raster images** from `page.get_image_info()`, skipped when drawn under 12 pt on a side
  (bullets, spacers, rules drawn as images), or when the image covers 85 % of the page or
  more (the page's own picture: a background or a scan; whether the page keeps its image
  is the safety net's, below). Decoration is decided by the size drawn, not the pixels: a
  21-pixel QR code drawn an inch wide is a figure. An image under 24 px on a side is
  decoration only when it is plain (`_plain_image`: looked at shrunk to 128 px, at most 2
  pairs of neighboring pixels differ by more than 16 levels, and what is seen, alpha
  above 13, covers all of it or nothing: a transparent spacer, a one-color block, a
  smooth gradient). Before 2026-10-12 any image under 24 px on a side was dropped.
- **Vector figures**: page furniture is set aside first: a frame round the page (an
  outline whose sides each lie within 15 % of the page's, drawn as one path that runs
  along all four edges of its own box and nowhere else: a rectangle, rounded corners, a
  double frame, a frame with corner ticks or curls; or a page-long rule within 15 % of an
  edge that is no plot's axis and no ruler, below), a background fill (85 % of the page's
  area or more, or a straight-edged panel round the page with a margin of up to 15 %) and
  a band of color along an edge (a slide template's title bar, at most 15 % of the page
  deep). A drawing's box holds every point of its path (`_drawing_bounds`: MuPDF's box
  leaves out the start of a subpath that begins away from where the last one ended, so
  a plot's axes drawn as one path, `80 75 m 640 75 l 80 335 m 80 75 l S`, read as the
  x-axis alone and the y-axis was dropped, until 2026-10-12). **Axes and rulers**
  (`_mark_axes`, 2026-10-12): a horizontal and a vertical line, each at least 48 pt long,
  that meet end to end at a corner with nothing else at that corner or at their other
  ends are a plot's axes (an L; a frame drawn line by line meets itself at every corner),
  whether each is its own path or one path draws both. Since 2026-10-13 an L whose far
  end comes within 4.5 pt of the end of another long line is not one (`AXIS_CLOSE_PT`: a
  report's box of four lines whose corners do not quite meet showed one L at the corner
  that does). A line at least 48 pt long that three or more short perpendicular lines
  (16 pt at most, and at most a quarter of it) cross or end on at different places, a
  tick's other end free, is a ruler (a timeline, a number line, an axis with ticks; a
  table's column rules end on the rules above and below them, and a frame's corner marks
  are two a side), unless its ticks all stand on one side of it, evenly, a letter cell
  apart (8 to 30 pt, the widest gap at most 1.3 x the narrowest, all as long) with no
  short row beside them (`_answer_fields`, 2026-10-13): that is an answer field's cells,
  to write a candidate number or a name in, and its lines are rules (an exam cover's two
  comb fields had become two figures and two decisions). **Evidence of a plot**
  (`_mark_plots`, 2026-10-13): an L is a plot's axes when a ruler is drawn on it, a
  curve starts within 4 pt of its corner, two bars stand on it, or a short row (40
  characters at most: a tick label, an axis title; the rows the header and footer rules
  dropped count too) lies just outside one of its lines (within 3 x its size, on the
  side away from the other line). A ruler, and axes with that evidence, are never page
  furniture or a border piece: a full-page plot's y-axis near the page's edge and as tall
  as it was set aside as a side of a frame, the x-axis then dropped as a lone rule with
  the tick labels and the axis title in the bands, and the figure was the curve alone;
  a timeline across the top 15 % of a page was dropped as a header rule. An L with no
  such evidence is two sides of a frame (a template's accent along the left and bottom
  of a title slide): furniture when it runs round the page, a border piece in a
  page-filling cluster. A frame's box holds everything on the page, so clustered with
  the rest it made every figure on a page with a drawn border one "page border",
  dropped with no decision. Then drawings closer than 6 pt to each other are clustered,
  and clusters whose boxes overlap are joined (the markers of a sparse scatter plot are
  further apart than 6 pt but sit inside one plot). A cluster is a figure when it is at
  least 24 x 24 pt, is not only horizontal/vertical rules (separators, underlines, table
  grids, the long separate lines of ruled paper; rules still count when they are a
  ruler, join end to end into an open chain, leave a box or bonds join atom groups, below)
  and is not a box or table around text (next).
  **Boxes round text and diagrams** (`_diagram_signs`, `_round_text`, 2026-10-13): a
  cluster without meaningful curves (a plotted line covering 5 % of its area, or at
  least three curved paths such as markers) and without bar-chart bars, with two or
  more text rows inside, is a figure when it shows signs of a drawing: an L of axes or a
  ruler, three small marks (8 pt at most) in different places that are no drawn bullets
  (a scatter plot's squares or crosses), or an open polyline at least 12 pt long (lines
  in two directions or a curve: a step, a skyline, a square wave, a zigzag, an arrow, a
  wire round a corner) inside none of the cluster's other boxes; or, with no running text
  inside, four or more rules joined corner to corner into a chain that does not close
  (a staircase drawn one line at a time), two or more separate box outlines with a line
  outside every box that starts at one of them (an org chart with plain connectors),
  a line outside every box that leaves one across its side or runs along two or more
  boxes apart from each other (a box plot's whisker, the connector of boxes drawn line
  by line, the baseline of outlined bars: `_connected`), or a rule with an atom group
  at each end (`_bond_ends`, below: a Fischer projection). Pen strokes of the page type,
  and drawings inside the page's handwriting regions, are no signs (a hand mark inside
  a box round text is cut out as handwriting; the box stays structure). Without signs,
  the cluster is a box or table round text (dropped, as before) when every row inside
  it lies in one of its outlines (an outline drawn as one path or as four rules that
  close) or filled shapes (a panel, or a band of thin strips drawn as a gradient), or
  between two of its rules that span it; otherwise
  something is drawn there that is no box round text and the safety net keeps the page
  image (`left_out`). Before, scatter plots with a legend, outlined bars on a baseline,
  histograms and step plots drawn as one path, timing diagrams, a box plot, circuits
  and flowcharts with labels inside were all dropped as boxes round text, and the job
  page said "Everything was converted". A box drawn line by line closes on itself, a
  table's rules meet in crossings and T-junctions, and underlines do not meet; two
  boxes side by side with nothing joining them are boxes round text; a worked example's
  frames round paragraphs with a grid of cell borders drawn in pieces (an open chain)
  hold running text, so the chain is no sign. A rounded rectangle (at least 60 % of its
  outline straight horizontal and vertical lines) is a box, not a curve, so a rounded
  callout around text is not a figure. One check-mark icon in a table cell does not make the
  table a figure. A small mark just left of a text row at its height is a drawn list
  bullet, not a curve of a figure. Equation rules are taken out first.
  **Drawings that fill the page** (85 % both ways: a full-page plot, a slide's chart):
  a plotted line or bars on axes with evidence of a plot, or on a ruler, is a figure
  whatever text is inside its axes (a title, a note, a legend: running text stays in
  the reading flow), once its border, if it has one, is taken off (a frame of dashes
  round the page: the figure is the plot, not the page); a cluster with three curves or
  two bars and no running text inside is one too. Else it may be a decorated border or a background drawn behind text, but
  nothing drawn is lost with it: its border (frame-shaped pieces, an L with no evidence
  of a plot, straight pieces across the page, curved pieces across the page along one
  of its edges, marks smaller than a figure along its edges: a frame drawn in pieces,
  corner ornaments) is set aside and the rest clustered again, so a plot inside such a
  frame is still a figure; a curve across the middle of the page is no border piece (a
  wave across a title slide is a figure of its own), and border marks smaller than a
  figure within 12 pt of a figure made of the rest are part of it, along a row of them
  (`_take_border_marks`: a plot's axes drawn as dashes along two edges; a frame of
  dashes round a plot further in stays apart). When nothing is made of what is
  left and it holds curves or bars, or there is no border to take off and the cluster
  holds a plotted line, curves or bars, the box is left out and the safety net keeps
  the page image (a cluster of rules and boxes behind text is left out as before).
  Until 2026-10-13 running text inside the axes sent a full-page plot to the border
  test, which set its page-wide curve aside, and the axes alone made nothing: a slide's
  plot with its title or a note inside the axes was lost, with "Everything was
  converted". On a page with ink (any `ink_*` reason of its page type) such a drawing
  is never a figure for the model, as it may be a page of notes: the safety net keeps
  the page image instead (on a typed page with handwriting, `mixed_ink`, the handwriting
  is cut out already and the rest is left out).
- A drawing too small to be a figure within 12 pt of a vector figure is part of it
  (axis titles and tick labels drawn as outlines, a legend's markers a little apart):
  left out, the figure's crop missed them.
- **Small drawings on a typed page** (`_leftover_drawings`, 2026-10-12): the other
  drawings too small to be figures were dropped with nothing to say so (the silent-loss
  sweep: a warning icon, a benzene ring, a number line, math drawn as glyph outlines, a
  hand check mark). On a page with a line of text or more, they are grouped (within 24 pt) and
  each group is kept when it is at least 6 pt on its longer side or has 3 marks in
  different places. Pen marks become a handwriting region (below, never sent to the
  figure model); any other drawing a figure for the figure model. A group is a pen mark
  when every path is a hand mark over typed text (below), or when half its paths or more
  carry pen evidence in the stroke itself:
  - a page-type pen stroke (`page_type._candidate`: enough points, bends more than half a
    circle, not jagged, not a blob; since 2026-11-11 not a circle's arc either, a turn
    rate within 25% of its mean all along, all one way (`ARC_SPREAD`): a printed seal's
    open ring);
  - an open stroke the same tests take for a pen stroke at any length
    (`page_type.pen_line`: a signature or a wavy line longer than the 90 pt stroke limit,
    which the page's own ink count keeps);
  - a short open stroke of straight lines, too few points for those tests (a tick, a
    cross, a slash: `_short_lines`, `_hand_strokes`), when every line runs more than
    about 5 degrees off both axes, its lines are not all one length within 2% (a printed
    chevron), no end comes within 0.75 pt of another stroke's end or lies in a filled
    shape of the group (an arrowhead), no line lies alongside another stroke's line,
    parallel within 1 degree (a double bond), and no line has the length of another
    stroke's line to rounding precision (0.1%, the page type's size-twin test: machine
    drawings repeat sizes, hands do not; a printed cross of two equal lines).

  Being an open stroke is no evidence by itself (2026-11-11, WS6.10): a skeletal
  structure's single straight bonds, meeting at their ends or running parallel, were cut
  out as "handwriting, not converted" on typeset exams (F-400); they now go to the
  figure step as drawings, while a grader's tick, cross or signature stays handwriting.
  On a `mixed_ink` page all of them are cut out with the handwriting; on a
  page with ink the checks rejected or did not analyze, the safety net keeps the page
  image instead. Not counted: a drawn list bullet, a mark repeated in the header or
  footer band (a logo), horizontal and vertical lines and boxes alone (an empty answer
  box, a black box over a line) unless they are a ruler (a timeline), bonds or boxes
  joined by lines (below), dots alone (4 pt or less: dot leaders, bullets the bullet
  test missed) unless they are round a letter (below), anything whose middle lies in an
  element (an underline, a box round a word, math drawn inside a line of text) unless
  it is a pen mark over the text, bonds, dots or an icon round a glyph (below), the
  ornaments of a page frame (marks at its edges, or a group that runs round the page)
  and a shape painted over hidden text. A page with less than a line of text keeps its
  image instead (the safety net). Since 2026-10-13 (the drawings sweep) these count too:
  **bonds** (`_bond_ends`): a horizontal or vertical rule with an atom group (a token of
  at most 6 characters) beyond each end within 1.5 x its size, the rule at least as long
  as the group is tall and through the middle half of its height (a horizontal rule) or
  through its width (a vertical one), and no text over or under a horizontal rule or
  beside a vertical one: a structural formula's bonds, which the row of its letters held
  (the drawing's box takes in the groups it joins); a blank to fill in lies on the
  baseline, a fraction bar has its numerator and denominator over and under it, and a
  minus sign drawn as a rule is shorter than its text. **Boxes joined by lines**
  (`_connected`): a one-line circuit of resistor boxes and wires, under 24 pt tall.
  **Dots round a letter** (`_dots_out_of_line`): dots in three places or more over a
  line of text that reach out of it by a quarter of their height (a Lewis structure's
  electrons; dot leaders lie in their line). **An icon round a glyph**: a paragraph of
  one or two glyphs smaller than the drawing round it (the "!" of a warning triangle,
  the "i" of an information circle) does not hold it.
- **Pen marks over typed text** (`_pen_mark_over_text`, 2026-10-12): one or two strokes,
  each an open stroked diagonal line or curve that is no radical sign, whose middle lies
  in a paragraph, list item or heading and that reach out of the line under them (or the
  nearest line they are beside) by a quarter of their height or more: a hand check mark, a
  cross or a slash drawn over a line, bigger than its text. They are cut out as
  handwriting with the rest; the text under them stays read. Before, they were taken as
  part of the line, like an underline, and lost with no decision. An underline is
  straight, a box round a word closes, and a cancel stroke across a term or an icon set
  in a line stays within it: those are still part of the text. Since 2026-10-13: one
  curved outline, closed (an answer circled by an ellipse) or filled (a check mark drawn as a
  note app's filled ribbon, covering at most half of its box: a highlight behind a line
  covers nearly all of it), in another color than the text under it (an sRGB component
  0.3 or more apart: a circle round a step number in the text's color is the text's),
  is a pen mark too; and a display equation holds no mark at least as tall as its text
  (a check mark over an equation: the transcription would leave it out). Measured on the
  private research corpus (not in this repository) and the repository's
  PDFs: of the 17 drawings too small to be figures whose middle lies in a text element
  on born-digital pages (the 586 real born-digital pages, fresh set 4, the 42 OpenStax
  renders, the three printed gold sections, the demo source and the fixtures), none is
  an open diagonal or curved stroke, so none is cut out at any overflow threshold; on
  15 synthetic marks over text (check marks of 10 to 30 pt, crosses, a slash, a circle, a
  question mark, a wavy underline, an arrow), 13 are cut out (3 were before, as pen
  strokes or outside the text): a check mark no bigger than the text (10 pt over 11 pt type)
  stays within its line, and an arrow whose head reads as a radical sign is not a pen
  mark. The only machine-drawn marks of this shape seen are check-mark icons in table cells
  (the sweep's `B_table_with_tick_icons`), which a table holds and this rule leaves
  alone. The 2026-10-13 rules change no element on those born-digital pages; the one
  page they first touched, a yellow highlight annotation over a line in the handwriting
  fixtures, is why a filled mark must be a thin ribbon.
- Overlapping figures merge (a photo with arrows drawn on it). Text inside a figure, and
  short labels (at most 25 characters) within 0.6 x font size of its edge or across it (a
  tick label half inside the box), belong to it and grow its box. A math row is a label
  only when it is at most three glyphs (an axis variable `x` in CMMI). Running text does
  not: two prose lines (3+ English words each) that read as consecutive lines of one
  paragraph, and the short last line that continues them ("the three."), or a title-sized
  line of 2+ words (1.15 x body, or 18 pt whatever the body: a slide whose only text is
  its title sets the body size itself, and its title was the figure's label), stay in
  the reading flow. A picture with such text on it stays a figure and the text keeps its
  place in the outline (2026-10-12: a slide's photo under its title, a caption box over a
  photo and a report's screenshots under their captions were dropped as backgrounds,
  with no decision). Only a plain picture (a colored panel or a wash drawn as an image,
  `_plain_image`) under running text is a background and is dropped.
- **One label rule by distance** (`_label_owners`, `_claim_labels`, SW40, WS6.12's reaches
  made one): a row is a label of a figure when it lies within `LABEL_REACH` (1.0) x its
  font size of the figure's nearest drawn stroke (`_stroke_gap`: the distance to the
  strokes, not to the drawing's box, which reaches over blank ground beside a diagonal or
  an L; a path drawn by a few straight lines counts by those lines, `_Path.edges`, any
  other by its box) or of a label the figure holds already, or when most of it lies
  inside the drawing's box (any length: the text in a flowchart's boxes); the rule runs
  again from the figure and what it holds until nothing more is found, since an axis
  title stands a line beyond the tick labels. A row two figures could claim goes to the
  nearer. Outside the drawing a label is short (`LABEL_SHORT`, 25 characters, or the
  tick or category labels of an axis set in one row, or several short ones far apart on
  one line: a timeline's events). A turned row (an axis title) is never a line of a
  paragraph, so it may stand up to `LABEL_TURNED` = 2.5 times as far and hold
  `LABEL_SHORT_TURNED` = 40 characters. Any kind of row counts, math and rotated ones
  too: a circuit's "R1 = 4 ohm", a triangle's sides and angle, a force arrow's "N". Never
  a label: a line of running text (a paragraph that passes by a figure, a title; for a
  row inside the drawing, found among the rows inside it), a caption, a line of code.
  Measured (SW40, 2026-11-18, 116 tracked PDFs and the 28-file corpus, short rows outside
  a vector figure's box, by distance to the nearest stroke in font sizes): the rows that
  were labels all lie within 1.0; beyond 1.0 the rows are prose fragments, headings and
  list items; turned rows that are labels reach about 2.0 and no turned row that is not
  one lies nearer than 2.5. So one reach, 1.0, with the turned rows' 2.5 times that.
  Not a label either: a short numbered title (a problem's "5. Blocks and springs"), a row
  that starts like a list item, a marker of one or two characters ("5.", "(a)", "B)") and
  a word of three letters or more (`_starts_like_item`). The list markers are not known yet
  where the labels are found, so the row's own start is read. A title taken as a figure's
  label is lost as a heading, and a label kept out stays a paragraph as before, so the
  rule leans toward the title. Measured (2026-11-18, the labels the rule claimed outside their figure and not
  turned, on the tracked files and in the corpus; the corpus has one title among them, the
  tracked files none): a row a candidate would keep out of the labels, tracked / corpus.
  A marker and a word of three letters: 0 / 1, the title, so no label released. A font
  larger than the figure's other labels: 5 / 62, nearly all true labels (chart titles,
  braces) and the title too. Bold unlike the others: 0 / 2, not the title. At the left
  margin with the figure to its right: 1 / 8. Running text below it: 20 / 175, and the
  title has none. Only the first keeps the title and releases no true label.
  A slide's footer bar set in from the page's edge (`_edge_band`, a rule or box at least 85
  % of the page wide inside the top or bottom 15 %) is furniture like one that touches
  the edge: before, a line of code or a total just above it was read as the bar's label
  (the 6.0001 deck, 35 pages, each with an empty figure for the bar). The tail line of a
  caption counts as the caption's own, not a label (`_running_text`).
  A picture has no strokes: only the rows on it are its labels. The figure's box, and so
  the picture cut for the figure step, grows to its labels. Replaces `_absorb_labels`,
  `_absorb_outer_labels` and the dropped rows' own reach (`_take_labels`), five reaches
  that disagreed: the figure with no tick labels took nothing, a circuit's part names
  were equations (the label take refused math of more than three glyphs), a triangle's
  four labels stayed equations. Before, a chart's category names stayed in the body as
  a stray paragraph and its crop had no axis values, so the model estimated them
  (2026-11-11, WS6.12, F-399).
- **Panels of one figure** (`_join_panels`, `LAYOUT_JOIN_PANELS`, off in code and in
  production; bump `PIPELINE_VERSION` when it is turned on). A figure printed as panels
  (a), (b), (c) under one caption was found as one figure for each panel, and only the one
  nearest the caption took the caption (`_attach_captions`), so the others were figures
  with a picture and no words: a reader got one description for each panel and the caption
  on one of them. With the switch on, a figure that holds a caption takes the captionless
  figures that stand in a row or a column with it (gap at most `PANEL_GAP` = 3.5 body
  sizes from the panels it holds, not at a corner), under the caption's width and on the
  caption's side of it, until none does; a line of text among the panels ends the join,
  and the short label rows among them (panel letters, axis words) do not. The rows among
  the panels are claimed as labels again, and a row of one or two characters ("A", "b",
  "3", "n") within `PANEL_MARK_REACH` = 2.5 body sizes of any figure's box is a label of
  it (panel letters and the names of a drawing's parts stand at a corner, beyond the one
  reach of `_label_owners`), unless it is a word ("to", "of") or the start of a list item.
  Measured (2026-11-20, the 277 readable public files: the repository's, the layout gold
  and the real-world dev and fresh sets; and the 28-file corpus): captionless figures
  standing beside a captioned one, 285 in 59 files public and 30 in 3 files corpus; with
  the switch on the figures go from 1,611 to 1,320 public and from 456 to 405 corpus, and
  the captioned figures stay 517 and 145. Not joined, by the same rules: 83 candidates in
  24 public files and 3 in 2 corpus files (a paragraph between, another caption's figure,
  a gap wider than the reach). The other half of the fault, an axis label or a part mark
  found as a figure of its own (`drawing_fragments`), is on no file of either set (0
  figures), so nothing was built for it. Off, the output is byte-identical to before.
  *What the rows were in* (2026-11-22, O-422). The marks and the rows among the panels are
  claimed after the blocks are built, so a claimed row could sit in a block that keeps its
  text. Counted with the switch on over the 228 real public documents (the openstax pages,
  the layout development papers, the real-world sets and the real tables; the repository's
  generated sheets and fixtures are left out of every count here) and the 72 corpus uploads:
  128 rows claimed among the panels in 14 files and 70 marks in 19 files (corpus: 16 marks in 6
  files, none among the panels). By the block the row was in: a one-row paragraph 165 (panel
  letters, ticks and part names), a one-row equation 1 ("−0.1", a tick), a one-row list item
  4 (ticks read as "0.0"), 22 rows of blocks that are all tick numbers of plots, and 5
  marks in 2 papers (`ax_cscl_240306350`: a bar "|" four times in a paragraph;
  `ax_qbionc_240307089`: a list marker "2.") and 1 corpus mark in a block that kept other
  rows. Those 6 were written twice, in the block and as a figure label. None was inside a
  table's box (0 of 198 public and 0 of 16 corpus). So a row is now claimed only when every
  other row of its block is claimed or already a label (`_whole_blocks_only`, for both the
  rows among the panels and the marks); the rest stay text. The shape also appears on a
  generated lecture-notes fixture (the symbol of a display equation, the last line of a
  paragraph), which is the test's case, not evidence. Captions are keyed by their text in the
  join (`captions`), but no page of either set has two figures with the same caption text
  (552 attached captions in the real public documents, 41 corpus; 0 repeated), so that part
  is not changed.
  *What the join leaves* (same counts): after the join 135 captionless figures stand in
  39 public files that have a captioned figure on the page (the 83 candidates above and
  figures of other captions); `drawing_fragments` figures with the join on: 0, so axis labels
  found as figures are still on no file, and nothing is built for them. A one- or
  two-character row left 2.5 to 4 body sizes from a figure: 22 rows in 14 real public files
  (corpus 4 in 3), 4 to 6 body sizes: 35 rows in 23 files (corpus 10 in 5). Among them are the
  entries of a matrix and the "n" of a formula next to a small drawing, so a wider reach would
  take text that is not a label; the corpus page with two arrow diagrams is one figure each
  now and keeps 13 one- or two-character rows 2 to 8 body sizes from them. A wider reach is
  not built (O-057, O-173): the counts do not separate the labels from the matrix entries, and
  nothing here measures a gain. The generated sheet's triangle
  (`tests/workflows/corpus/selfmade/f2_figures.pdf`) is one figure with all of its labels,
  switch off and on; a generated probe, so it is a limit noted here, not a fault built for.
- **Shapes that vanished** (WS6.15, 2026-11-18). A timeline's ruler (a long thin line
  with ticks, 5 pt tall) fell under the 24 pt a figure needs on both sides, so it was a
  small drawing; no label was held for it and the paragraph's block took its labels, so
  its drawing was dropped and the labels were a paragraph in scrambled order: a ruler at
  least 96 pt long with ticks standing out of the line (more than 1 pt) is a figure
  (`thin_ruler`, `_vector_figures`; a table's header rule alone stays a rule). A free-body diagram's
  force arrows, each thin and 20 pt off its box, were separate small drawings that
  joined the box only within 12 pt: a small drawing joins a figure within
  `LEFTOVER_REACH` (24 pt), as the leftover drawings do among themselves, unless a line of
  prose (`SENTENCE_WORDS`, 6 words or more) lies wholly in the gap between the two and
  overlaps both sideways (`_sentence_between`; a glyph drawn as an outline in a line of
  text has no such line between it and the figure): a dark tab with its label inside ("EXAMPLE 5.2") a few points over
  a sentence and a grid joined the grid's figure, whose crop took the label and the
  sentence (O-388, 2026-11-20). Such a drawing was part of a picture, so it is a figure of
  its own: among the page's leftover drawings it is kept though a block holds its middle (a
  formula drawn as outlines in a line of a paragraph was dropped there as "math drawn in a
  line", and was in no element), and it is not grouped with the leftover drawings near it,
  whose group box would take the sentence in again (`apart_out`, `_leftover_drawings`). A
  figure's box grows with each join, so a join stopped also leaves out the small drawings
  further on that were in reach only through it: they are read as any small drawing of the
  page (in a line of a paragraph: held by the paragraph and lost, as the list of what is not read says further down). A
  circuit's parts were not found as labels, see the label rule above.
- **A drawing made of text fragments** (`_fragment_figures`, WS2.9a): the labels of a drawn
  structure, circuit or diagram whose strokes are too short and plain for a drawing cluster
  were written as dozens of loose paragraphs and one-symbol equations, each its own
  decision. A group of 12 or more text rows of 3 characters or fewer, within 3 body sizes
  of each other and outside the figures and tables already found, with at least half a
  short stroke per fragment among them (a stroke between 0.5 and 6 body sizes long: a
  contents page's leader dots are not strokes, and a derivation has a fraction bar or two)
  is one figure, reason `drawing_fragments`, its rows kept as its labels and written as
  text no more. A region whose rows have a median length of 4 characters or more, or one
  with a row of three words, is text and stays text. Found after the drawing clusters,
  before the page's text is read, so the rows are out of the reading order.
- **Glyphs drawn as outlines in a line of text** (`_glyph_outlines`, 2026-11-11, WS6.10,
  F-364): a page-type pen stroke inside a typed line (within its height, a tenth of it
  over at most, with typed glyphs on both sides) whose drawing is a fill with no stroke
  that lies in the line too is outline math (an italic variable, the digits of a
  quantity), not handwriting: it becomes a small figure for the figure step.
- **Glyphs drawn as lines** (`pipeline/glyph_strokes.py`, 2026-11-17, WS6.11, F-550): LaTeX's
  picture environment sets a slanted line, an arrowhead or an arc as glyphs of a drawing font,
  and xy-pic does the same with its own: `line10` and `linew10`, `lcircle10` and `lcirclew10`,
  and `xyatip`, `xybtip`, `xybsql`, `xycirc`, `xycmat`, `xycmbt`, `xydash`, `xyeuat`, `xyeubt`,
  `xyline`, `xyluat`, `xylubt`, `xymisc` and `xyqc` at their sizes (the TFMs of a TeX Live
  install). A font is one of these when its whole name after the subset prefix is (`LINE10`,
  `IIMURR+LINE10`); a font whose name only contains "line" or "circle" is text. A triangle's
  slanted side was a paragraph of 17 quote marks beside seven equations, and no figure existed
  to hold the drawing. `_text_blocks`, the one place layout reads characters, takes those
  glyphs out of the text it returns, so rows, the text pass's glyphs and the line reader see
  text without them, and leaves them in one block of type 2. `_read_page`, where a page's
  drawings are gathered, turns that block into entries of the page's drawing list (what
  `get_cdrawings` gives), before the page type, the ink analysis and the path classification
  read it, so the figure clustering sees a drawing. A glyph's geometry is its outline as
  MuPDF draws it (the page's SVG with text as paths, read once on a page that has such
  glyphs): a thin straight outline (its width at most 12 % of its length, as a line's glyph
  is) is the segment it draws, stroked; any other outline (an arrowhead, an arc) is a filled
  path. A glyph the SVG has no outline for is left out. Labels join the figure by the rules
  above, none of them new: the labels of the picture-environment triangle that sit within
  their reach (`c`, `θ`, `a = 6`) are the figure's, the ones further out stay equations (the
  one label rule is SW40). Glyphs in a font that is not on the list are not read this way:
  none of the 28 corpus files and 116 tracked test PDFs has a stroke set in another font.
- **Captions**: a run starting `Figure N` / `Fig. N` followed by punctuation or a capital
  letter ("Figure 2 shows ..." is prose) attaches to the nearest figure that overlaps it
  horizontally, below or above within 36 pt (below wins ties). An unattached caption stays
  a paragraph.

### Title and language

`title` is the PDF metadata title unless it is empty, a file name (`notes.docx`) or a tool
default (`Untitled document`, `Microsoft Word - ...`); otherwise the first level-1 heading.
`lang` is the catalog `/Lang` when it looks like a language tag, else `en`. The text pass
decides the document's language from there (the chosen language, then the text, then
`/Lang`; see "Language" below).

### Code blocks, footnotes and contents pages (T2-16, T2-17)

**Code blocks.** Consecutive lines of upright text that are monospaced throughout
(`CODE_MONO_SHARE` 80 % of each line's glyphs: MuPDF's flag, or a name such as Courier,
cmtt, Consolas), in one column, one line pitch apart (up to `CODE_MAX_PITCH` 3.4 x the font
size, so a blank line stays inside) and of one size, with at least 12 glyphs in all (or 6
in two lines or more), are one block (`_code_groups`): `Element.preformatted` with
`text` holding the lines and their indentation (`_code_text`). A monospaced font moves
the same distance for every character, so a line's indent is its distance from the left
edge of the block over that advance; spaces are the text layer's own, except that a
space narrower than half an advance is one the layer made up for a gap and is not kept,
and a gap of 1.8 advances or more between two characters with nothing between them is
spaces. Blank lines are kept. The block is never joined with a paragraph above or below
and its inline math is not looked for. The assembler writes `<pre><code>` with the text
escaped and the CSS added only when a document has code; a block with a line over 36
characters is a tab stop named "Program code" (axe `scrollable-region-focusable`, as
equations are). A box drawn round one line of code is its frame, not a figure
(`_backdrop`). The notice `code_block` (review "none") is still given, now saying the
code keeps its line breaks and indentation but not its highlighting.

**Code fonts (O-440).** A font is monospaced when MuPDF flags it or its name matches
`glyphs._MONO_FONT`: "mono" (not "Monotype"), Courier, Consolas, Menlo, Inconsolata, CMTT and
SFTT, and, added after counting every font name on the 332 files of the corpus hash set and the
25 private corpus uploads (the glyphs of a candidate must all advance alike), NimbusMonL (the
Courier clone of the Times-Nimbus LaTeX stack: 10 files with the regular face, 5 oblique, 1
bold; 2 corpus uploads), CMITT (3 files), t1xtt and txtt (txfonts typewriter: 1 file each),
TeXGyreCursor (1 file) and Lucida Console (1 file). Their text in those files is code: a
listing, an inline identifier or a URL (a URL stays inline, see below). No other font on those
files has all glyphs of one advance and a name the list misses. A new name enters the list only
when a real document sets code in it.

A block of monospaced lines that are all one email or web address (`_is_contact`: an author's
contact line on a title page, set in NimbusMonL, was a one-line code block once the font was
known) is not code. Measured on the corpus hash set, with the names in: 5 of 332 files change
(a SQL snippet set as a heading or a paragraph is code, in ax_cslg_240306367 7 times; the Python
listings of ax_csro_240306420; the listings of 2505.13042 pp2-3; the algorithms of 2504.06207);
one corpus upload changed by an address line, which `_is_contact` now leaves alone. Measured and
not built: in 2504.06207 p4 the algorithm (its rows are NimbusMonL) is now taken by the code rule
before the algorithm rule can see it (`_algorithm_groups` skips rows that are code), so the block
holds the first seven steps, the comments right of the steps and "for each algorithm" are
paragraphs beside it, and its steps 12 and 13 are a block of their own. Before, the algorithm
rule read the page too, with steps 9 to 13 outside the block already. The algorithm rule would
need to take an algorithm set in a monospaced face; 1 page of 1 file of 332. The listing of
ax_csro_240306420 p11 to p16 comes out as 100 short blocks (a wrapped argument line, or a comment,
each its own block) where it was prose lines before.

**Footnotes.** A footnote (the notice `footnote`) is still found as before; since T2-16
its mark in the text above it also links to it (the output writes the mark as `doc-noteref`, the note as `doc-footnote`, with a `doc-backlink`, docs/output.md "Navigation"): the mark is found again in the element's
text by the ten characters printed before it, and a mark that cannot be found exactly once
gets no link (`_footnote_links`; an internal `Link` made by layout, which the text pass
keeps and which wins over a PDF link on the same text).

A file that names its footnotes is read from the file (WS2.8 g, `pipeline.named_links`, the one
place that reads destinations and links from PyMuPDF). LaTeX's hyperref writes a named
destination `Hfootnote.N` about 2 pt above the footnote's line and a link to it from the mark in
the text. The line a destination points at starts a footnote (`_mark_notes` gives the rows from
there their own run, which is why a footnote set in a mono font, a URL, is not a code block);
the link's rectangle is the mark. In such a file a block it does not name is not made a
footnote by its place and size alone (a listing's line numbers in small type were 40
"footnotes" in one file, and a numbered list at the foot of a page was one too), and a file that
names none behaves as before.

Three more cases of such a file (the wave 2a footnote work). Some classes put a one-line note's
destination at the foot of its line, a little under it: when no line has the point above its top
or within it, the line it lies just under is the note (`_row_at`). The symbol notes of a title or
author block (`*`, a dagger) have a destination at the corner of the page, on no line: such a
destination starts the one line low on the page, in small type, that opens with the glyphs its link
covers in the text, and a mark that opens no line or two places nothing (`_place_by_mark`). And a
block that begins a column is no continuation of the note that ends the one before it
(`_merge_column_continuations`): a note ending in a URL, with no full stop, took the first
paragraph of the next column in with it. The glyphs of a note's mark (the ones its link covers, and
the ones that open the note's own line) are never read as math (`NOTE_MARK_FLAG`, `_char_class`),
so a mark beside a letter in a math font is not made an inline equation with it. The mark is found
again in the element's text by what is printed before it; when an inline equation took some of
those glyphs (it stands in the text as a placeholder) the context is cut from its left, and what is
left of it has to stand right after a placeholder's end, down to nothing left (the mark right after
the placeholder). It still has to be found exactly once, and not as the end of a number. A short
tail of the context found anywhere else in the text is no evidence: one digit of it and the mark
can also stand inside a placeholder's own name, and the link would land in the equation
(`_mark_spots`).

A raised number is set off from the word it touches where the glyphs are cut (`_mark_notes`,
`SET_OFF_FLAG`), not by repairing the string afterwards: the mark that opens a note's own text
("2 We built"), a mark in the body that a link names, and a raised digit that ends a heading (a
note mark, not one of the heading's words). The blank is one the nuclide rule (`_before_symbol`)
leaves alone, so "2Feehly" no longer happens. An exponent is not touched, and nor is a raised
mark of a table: in a file that names its footnotes only the glyphs a footnote link covers are
marks, and in a file that names none only a raised digit or sign that opens a line of smaller
type low on the page, or a raised run at the end of a heading that is the same as the opening
of such a line on the page.

Wave 2b, from a count of the public papers (131 destinations, 155 notes found, 124 linked; now 139 found, 135 linked; the
count is the footnote destinations, the `footnote` notices and the internal links to them).
In a file that names none, a block is a note only when it opens with a mark printed raised in
the text above it that is the whole mark ("12" is not the "1" raised above it) and goes on in
words. The mark is read from the note's first line as printed (`_lead_token`): raised glyphs
at its start end the mark where they end ("1" before "2019 was the first year"; at the start
of a line a glyph a little smaller than the line and lifted off its baseline counts as
raised, `NOTE_MARK_LIFT`), and a start on the line ends at the first blank ("1 10 mL of
water"). A raised run in the text holds each of its marks (`_mark_groups`: "1,2" is two
marks, "1)" is the mark "1"). So: a row of axis labels in small type low on a figure (11 "notes" on one page), a lone page
number, an integral's lower limit and a running footer ("12 Authors") were notes when one raised
digit of the same kind stood anywhere above (`_footnote`, `_lead_token`; 14 false notes on 4
papers, none now; one note of that shape is lost with them, the "Mathematics Subject
Classification" line of ax_mathap_240306449, a note only because a "2" was raised somewhere above,
as the same line of ax_mathco_240306468, which was never one). The nuclide rule leaves a mark alone (`_nuclide_runs` reads no glyph with
`NOTE_MARK_FLAG`), so a note that opens "In fact" or "As" is not indium or arsenic with the mark
for its mass number (2 notes, 2 papers). In a file that names none, the raised mark in the text
before a math letter, right after a word of text type or a comma, stop or colon, is flagged the
same way as a named one and is no part of the equation with the letter (`_flag_text_marks`: an
exponent, after a math letter or a bracket, is not touched). And a note the file names but no link
points at (a class that writes the destinations and no link rectangles: 6 notes on 2 papers) is
linked by its mark, the raised one in the text above on the same page that the note opens with
(`_layout_notices`); it still has to be found once. Not changed, and why: the 2 author notes of
ax_astrophga_240306369 and the note of a table (`a`) have no mark printed in the text, nothing to
link; `[1]` of 2503.22999 is a bibliography item the file names `footnote`. The false footnote on
2407.04005 p10 (p10-e50), "a mark inside a heading" and the 17 not found could not be measured
on the public papers: none of them has a false note, a heading mark that is not linked, or a
named note not found (131 destinations, 131 found).

**Note signs and exponents (wave 4: O-034, O-054, O-429).** The asterisk operator (U+2217, the
star TeX sets for an author note) is a note sign (`_FOOTNOTE_MARKS`), and a number and a sign
printed raised together ("1∗", "∗1") are two marks (`_mark_groups`). Real files: the star of
an author note was a stray equation in ax_condmatmtrlsci_240306379 and 389 and in
ax_csro_240306533 and sat against its word in ax_mathco_240306383 and 2505.03022 ("∗Email"); two author
notes were not found and are now, with their links (ax_mathap_240306449 "∗ Corresponding
author.", ax_mathap_240306464 "∗ Institut de recherche XLIM ..."). A note that opens with
that sign must go on in two words (`_footnote`): a norm's subscript ("∥M∥Fro" under a
display, a raised "∗" on the page) made 2 false notes on 2411.07613 p3, none now. A raised
digit right after a letter set in a math font ("p1", "m0", "n2") is a subscript or an exponent,
no mark (`_after_math_letter`; `_layout_notices` and `_flag_text_marks`): the 29 of them on
w_notes_6042_ch2 had taken the place of the raised "1" of a real note, and the link of that note
(p4-e21) went to the page header (p4-e0); it goes to the note (p4-e44). Count on the public gold
(the 158 PDFs of `docs/eval.md`): 131 destinations, notes 139 to 141, linked 135 to 137, false 0
before and after, no note lost. Measured and not built, because no real file shows them (the review
probes do): a raised range over notes ("1-3", review 13 `n_range.pdf`, whose separator is a middle
dot, which no file prints) marks no note (0.5.14 marked the two ends); and a unit's exponent
("25 cm" and a raised 2) over a low line that opens with a digit (axis numbers, a row number:
`x_exp_axis_words.pdf`, `x_exp_then_small_row_number.pdf`, `note_unit_exponent.pdf`) is a note.
Rules for both were built and taken out again: with them on, none of the 996 files and 73
uploads of the corpus hash changed because of them. Also not built: the "Mathematics Subject
Classification" and "Key words" lines of the front matter (about 14 low front-matter label lines
on the public set, "Mathematics Subject Classification" on 2 files: they are no notes and a mark
standing above is what made one a note, so the line of ax_mathap_240306449 stays lost); the
one-line email note at the foot of the left column of ax_condmatmtrlsci_240306379 and 389 that
is read in one run with the right column's first lines (the file names no footnotes; the
footnote zone rule of the region tree that would set it apart is off in the reading order; it
needs a rule that cuts a run at a low, smaller line opening with a sign, which touches every
two-column page: 2 of the 158 files show it); the note "∗ Email: ..." of
ax_mathco_240306383, whose mark stands in the author heading (1 file: still no note). The bottom
notes of the private documents s06 and s17 and the 7 notes of O-054 (two in r18_arxiv_opt) were
not looked at one by one: no upload of the private corpus changed in the corpus hash (one upload
of 73 changed, the public notes chapter w_notes_6042_ch2).

**Contents pages.** A page with at least two lines that end in dot leaders (five dots, or
an ellipsis counting for three) is a contents page (`_contents_rows`). Each such line is a
list item of its own with its title only: the leaders go, and so does the page number (at
the end of the line, or a line of its own at the right edge on the same baseline); a line
with no leaders whose number stands at that right edge is an entry too (a chapter line in
bold). A line of prose that ends in dots but has no page number (a quotation cut off with
an ellipsis) is not an entry, so two of them are no contents page. A title that wraps is one
entry: the line right above an entry, at its size and no further right than it, stopping near
the page numbers' edge and with no leaders or number of its own, is the first line of its
title (`_title_head`; the hyphen at its end is repaired as in a paragraph). It is so only in
the middle of a list, when the nearest line above it is an entry (`_entry_above`): above the
first entry it may be the last line of a paragraph, a heading or a part line, which look the
same, so it stays a line of its own. In a list whose entries start with a label ("1
Temperature", "A.4", "Part II", "Week 3:", "Module 4": a number, a letter and a number, a
Roman numeral of I, V and X, alone or after a capitalised word, then a space; so a lone "I"
counts, and so does any capitalised word before a number), an entry that starts with one is never
a title's second line: the line above it is a part, chapter or unit line with no page number
of its own (`_starts_with_label`), and a title whose second line starts with a number is two
lines. In any list, a line without a label under a line with one goes on from it in lower
case, after a hyphen ("2.1 Work and heat in closed" / "systems"), or under a label of the form
of the entries' own (`_label_shape`: "2" among "1" and "3"; "E.5 Proof of ... (Lemma" / "D.6)"
among "E.4" and "E.6"; the entries are those with leaders or a page number at the margin, so a
LaTeX article's section lines count; a Roman numeral has a form of its own); otherwise the
line with the label is a chapter line over an entry of its own ("Chapter 2 ..." /
"Introduction", "2 ..." over it among "2.1" and "2.2", "IV ..." over it among "1" and "2",
`_wraps`). Left as it is: a chapter line whose label is a word ("Chapter Two", "Part Two:"),
in capitals ("CHAPTER 2") or that has none is still joined to the entry under it, and so is
one over an entry that starts in lower case or with a year, and one with the entries' own
form of label over an entry with none ("2 ..." over "Introduction" among "1", "2", "3", with
or without leaders), which looks like a title of entry 2 that wraps. A wrapped title whose
Roman label is the only Roman one among Arabic entries, or that starts with the word "I", is
two lines (`_label_shape` gives the Roman numeral a form of its own).
Items nest by
indent (`list_level`). The page numbers are the printed book's; the
converted file marks its source pages instead (docs/output.md "Navigation"), and its own
contents list is not written when the PDF has a contents page. An entry links to the one heading later in the document
whose title is the same once section numbers are left out (`_contents_links`); when the
entry and the heading both print a number ("2.1", "A.1", "Appendix A", "Chapter 4", "II."; one
reading, `layout.elements.heading_number`, which the references below use too) it must be
the same number, so "3.1 The model" does not link to "2.1 The model" when only that one is
here (a part of a book), and of two headings with one title the entry's number picks one. With a
number on one side only, or none, the title decides; so it does when one is a section number and
the other an outline's roman numeral ("4.1 Scope" and "I. Scope"), which are not compared. When the PDF's own links already say where
it goes, the text pass keeps those. Before, the
chapter lines became headings with their numbers as stray paragraphs, and the section
lines paragraphs full of dots (`tests/fixtures/layout/latex/tables.pdf`).

A section number that starts an entry ("6.1") and a page number that ends it after math
("Forces on Σ 9") are the entry's own text, not part of its equation: before, the number next to
the symbol was read into the formula (`_clear_of_entry_numbers`).

In a list with leaders, a line that has none and runs up to its page number ("D Ablation study of
a very long title 21" among "C Setup . . . . 14") is an entry too (`_contents_rows`, `_leader_split(bare=True)`):
it ends at the edge of the numbers (within 1.5 em), starts where an entry with leaders does (within
0.6 em) and ends in a page number after at least `MIN_TITLE_LETTERS` letters. Before, such a line
was left a paragraph with its number in it.

A contents list with no leaders is found by its shape (WS2.7, `_shape_entries`): three or
more lines one after the other in one column, each ending in a page number (or followed on
its baseline by a line that is only that number, in a column of its own), the numbers at one
right edge (within 1.5 em; a list of six or more lines may have each number follow its
title instead) and not decreasing (roman numbers of the front matter come first), under a "Contents" heading or on one of the first three pages. Each line is an entry
that keeps its number as its last word ("Motion 3"), joined to it where the number stood
apart; a line with no number ends the list, so the section heading right after it is not
taken in. It is tried before a grid is taken for a table: a table whose lines are mostly the
list's is dropped and its lines stay text (`_contents_lines`, in `_claim_tables`), so the
reason `contents entry` fires and no second contents list is written. An entry with a number
links to its heading by its title with the number left out, and by the number when both print one (`_contents_links`). A list whose numbers do not stand at one edge is read as numbers that follow their titles only when none of them stands apart in a column of its own: marks ("x", a roman numeral) set in several columns of a table, after the names, are a table's cells and the table is kept. Not read: a
list of fewer than six lines whose numbers do not stand at one edge, a list on a later page with no heading over
it, a title that wraps onto a second line with the number on the second (in a list with leaders it is
read), and a two-column
page (the columns are not known when tables are claimed).

### Kept line ends, the abstract and algorithms (SW53 a and e, WS2.6, F-261)

**Kept line ends (SW53 a).** A paragraph joins its lines with a space. A poem, an address
or a list of short statements needs its line ends, so a paragraph of at least
`KEPT_MIN_LINES` (3) lines keeps them when every line but the last starts at one left edge
(within `KEPT_LEFT_SPREAD` 1 em) and stops at least `KEPT_SHORT_BY` (8) em short of the right
edge its column's text reaches (`_RightEdges`, the edge a quarter of the lines reach), none
ending in a broken word (`_keeps_lines`, set on `_Block.lines` in `_page_blocks`). A paragraph
that wraps runs to the edge in every line but the last, a centered line starts each line
elsewhere, and a slide deck's text box is not measured by its page's edge (slide decks are
left out), so none of them is one. Two more guards come from the corpus: justified text in a
column narrower than the edge measured (a two-column page, a text box beside a figure) has at
least `KEPT_JUSTIFIED` (60%, and two) of its lines ending at one place (within
`KEPT_SAME_END`, 0.3 em), and a narrow ragged paragraph wraps in mid-sentence, so when at
least `KEPT_WRAPPED` (25%) of its line ends have no stop (`KEPT_STOPS`: . ? ! : ; ) and
closing quotes) with a lower-case letter starting the next line, it is a paragraph. It is stored as a `Run` of kind `break` on the single
space that joins two lines (`script_marks.LINE_END`, a private-use mark put in the text by
`_append_row` and taken out, with its offset, by `script_marks.split_lines`); the writer
puts `<br>` there (`inline_text`), and Listen already pauses at a `<br>`. A block that keeps
its line ends is not joined with the block after a page or column break (`_goes_on`). Not
read: a poem alone on its page (no line reaches a right edge, so none is "short"), and a
paragraph of one or two short lines. `break` runs are moved with their space by a page edit
and dropped when the space is (`web/page_edits.remap_spans`, `core/inline`).

**The abstract (SW53 e).** A single line that says "Abstract" (or "Abstract.", "Abstract:")
and nothing else, in bold or centered in its column (at least 2 em in from the column's
left edge, within 1 em of its middle), is a heading with the reason `abstract label`
(`_label_abstracts`). A heading step reads that reason as strong evidence (confidence
0.88, like a numbered heading), and `heading_levels` gives it the level under the title (2
when the document has a heading set larger than the body, else 1). "Abstract" at the start
of a sentence or run in before its text is not one. A line that begins "Keywords:", "Key
words -" or "Index Terms -" starts a block of its own (`_KEYWORDS_LINE`, in `_runs`). There
are no roles and no `keywords` role.

**Algorithms (WS2.6 b, O-050).** An algorithm is read as one block, `Element.preformatted`, from
its steps (`_algorithm_groups`, `_scan`, `_is_algorithm`). Under a line that opens "Algorithm 2"
it takes at least `ALGORITHM_MIN_ROWS` (3) lines, `ALGORITHM_MIN_WORDS` (2) of them beginning with
a step word (for, if, else, while, repeat, until, end, return, input, output, require, ensure,
function, procedure, do, then ...), and one that opens or closes a block (or
`ALGORITHM_MIN_NUMBERS` (2) numbered steps). Lines that all run on from one to the
next (each reaches the right edge or stops where the next line's first word no longer fits) are a
paragraph that opens "Algorithm 2 shows ..." and are left as one (`_reads_on`). Steps that carry
line numbers are not asked this, nor the proof for a line that runs on below: both are for lines
with fewer than `ALGORITHM_MIN_NUMBERS` numbers (one is no numbering: the heading of the next
section, "4 Results", has one). A line that
opens "Algorithm 2 is ..." or "Algorithm 1 shows ..." is a sentence and no caption
(`_CAPTION_SENTENCE`: a list of the words such a sentence goes on with, so "Algorithm 2 computes
..." is still read as a caption). The lines under a caption before its first block word (Input,
Initialize ...) are steps. One that the next line runs on from (the first word of the next line
would not have fitted on it: `_stops_short`) is a long step when a step stands over it and,
under it, a step word on a line that ends short of the right edge ("Choose a start vector b.",
the long line, "repeat"; the line of a paragraph goes on to a "for ..." that runs to the
margin). Any other (a long first line, a long line that wraps into a second one, a long line
over a loop line that reaches the margin) may be a long step ("Initialize the table of
counts ..." set out to the margin) or the running text under a float's caption, so the lines
must then show more: one step set further right than the line above it and a later one set back
to the left of the line above it (`_nested`, from the indent alone: it reads no word, so a block
that opens with no "do", "then" or colon shows itself, and a sentence that begins "For" is no
proof by its first word; it was a proof of opening and closing words before wave 2b, and it is
the same on all 22 public pages with an "Algorithm" or "Procedure" line, which is the set it was
tried on). A formula on a line of its own counts as a step here (a loop whose body is "b <- A b"). Two
lines that start more than `ALGORITHM_SET_IN` (6) em apart prove nothing: a line that far right
is a display equation of the text, centered in the column ("... as follows:" over "v = d / t"),
and a closing line that far left of it is the sentence going on ("until the last word is read").
With no caption line (one under a rule, a box or nothing) it takes more proof:
`ALGORITHM_BARE_ROWS` (4) lines, `ALGORITHM_BARE_WORDS` (3) with a step word, a step that closes
a block, and either consecutive line numbers ("3:") with one step set in and a later one set back (`_nested`;
the numbered lines of a manuscript and the questions of an exam all start at one place), or no
numbers, an indent ladder of three left edges and something only code holds (a formula, an
assignment, an "end", a block word on a line that ends in "do", "then" or a colon). A bullet is
never a step. A number written as a list writes it ("1.", "2)") is no step where there is no
caption; under one it is, and the lines must then show one step set inside another (`_nested`):
pseudo-code typed with a word processor's automatic numbers is one block with its indentation,
and "Algorithm 5.1 (Euclid)" written as numbered sentences stays a list. Two lines whose
numbers differ in length ("9." and "10.") prove nothing: typed with no tab stop, the longer
number sets its words a digit further right. A caption here is a line that opens "Algorithm" and
a number ("Algorithm 2", and an appendix's "Algorithm A-1" or "Algorithm B.2": `_ALGORITHM_CAPTION`): under "Procedure 2 ...", "Algorithm: Scan a verse" or "Algorithm A (...)" pseudo-code
numbered "1." is still a list, with no indentation and its "count = count + 1" step not an
item of its own. The lines are in one column and one size, within
`ALGORITHM_MAX_PITCH` (2.2) lines
of the one above, and each begins with a step word or a line number, holds an assignment
("count = 0", "c <- 0", `:=`), is a line of math, or starts further right than the first step; a
line that is none of these ends the block. The caption stays a block of its own. The text has one
line to a step, indented `ALGORITHM_INDENT` (2) spaces per level. Where the steps have no numbers,
a line in lower case under one that ran to the right edge is the rest of that step, but not under
a line that opens a block (it begins with a block word and ends in "do", "then" or a colon: "for
each w do"; an "Input: ... :" line opens none), and not when it holds an assignment. Where the
lines of the column are not set to a right margin (`_RightEdges.firm`: the longest lines end
more than an em past the edge that a quarter of the lines reach, as on any page set ragged and
on a page of little but the algorithm), a line inside a block that starts where the step above
it starts is the next step ("put a tea bag in the cup", "wait three minutes"); where the edge
is a margin it is the rest of the step, as algorithms set with no hanging indent wrap. The
shape is the same in both and the rule guesses: on a ragged page a step that wraps with no
hanging indent stays in its two lines (all its words are there), and on a page set to a margin
a short step under one that reaches the margin is joined to it. A step that goes on for more
than `ALGORITHM_RUN_ON` (2) lines is a paragraph (the text that goes on under an algorithm in
the middle of a sentence): the rows end before it. Lines hung under their step, set further
right than where it starts, do not count, so an "Input:" line goes on for as long as it needs.

Line numbers ("3:", "12") are cut off the words and written in a gutter, right-aligned in front
of the step ("3:   if x then"). The levels come from where the words start, not from the numbers,
and the steps must carry consecutive numbers (`_Steps.expect`), so a numbered list of sentences, a
table with a line-number column and line numbers in a page margin (which no step follows) are not
taken. Up to `ALGORITHM_PREAMBLE` (4) lines (Input, Output; a line that wraps is one line) may come
before the first number. A
step that goes on in a second line (the line before it ran to within `ALGORITHM_WRAPPED` (3) em of
the column's right edge, and the new line has no number, does not start with a capital or a step
word) is joined to its step; a word broken by a hyphen across the wrap is mended. A line with no
number right of the numbers (a step that broke early, a comment on a line of its own) is joined
too when the next number is within `ALGORITHM_BETWEEN` (2) rows below it. A bit of math or
small print the text layer cut off a line (`ALGORITHM_SAME_LINE`) is a piece of that line, and so
is one that hangs under the line's end, up to `ALGORITHM_HANG` (0.7) em lower and starting no
more than `ALGORITHM_HANG_AT` (1.5) em left of where the line ends (the subscripts of a tall
expression: the steps after it stayed outside the block, Algorithm 3 of ax_statme_240307124);
rows the page lists out of order are put back in the order of their lines.

A display equation between two steps ends the rows there; the algorithm goes on at the next step
within `ALGORITHM_REACH` (16) lines (`_after_equation`) as a second block: the step with the next
number, or, with no numbers, a step word, an assignment written with an arrow or ":=", or one
written with "=" inside a block that is still open (right of the leftmost step, under a "for"
or an "if" that no "end" has closed) and set either where a step above it starts (within a
quarter em) or, when the last step before the equation opened the block, anywhere right of
that step (the equation is the first thing the loop holds). A "where d = 12 is ..." after the
equation is the text going on, at the left edge or set in as the first line of a paragraph
is. Under an algorithm with a block no "end" closes, such a sentence is still taken wherever
it starts within a quarter em of the start of any step above it (a paragraph indent equal to
one or two step indents), or right of a last step that opens a block; its paragraph is then a
second block. The levels are those
of the whole algorithm, so the second block keeps the indentation it had, the line numbers
continue, and the equation keeps its crop and its place in the order. A formula of one line that
is a step by itself is not such an equation: it is a line of the block and its formulas are
inline equations. Not built: an algorithm that goes over a page break is read page by page, so
the part on the second page is a block only when it proves itself an algorithm with no caption,
with levels of its own; typed pseudo-code in a proportional font with two-digit line numbers
gets its levels from where each line's words start, which the wider number shifts; and where
the caption ends within `ALGORITHM_WRAPPED` (3) em of the column's right edge (a page that holds
only an algorithm, a narrow column), a first step with no step word is read as the second line
of the caption. Algorithms still lost (independent reviews 9, finding 1, and 10, findings 2
to 5): under a long first line in plain words, steps that show no nesting in the way asked
above, which are a flat algorithm (no line set further right), one whose last line is still inside
its loop (nothing is set back to the left after the line that goes in; the words "do", "then"
and a colon are no longer asked), a loop
that holds two formula lines or more (a display equation of several lines is not a step) and
a loop whose only line is a formula set more than 6 em to the right. The same pages with a
short first line are read as algorithms, and so are those with the long line second when it
is one line with a step word under it that ends short. Other long lines after the first ask
for the same nesting: a long step that wraps into a second line (hung or not), two long steps
one after the other, a long step over "count = 0" or over a loop line that reaches the
margin; with flat steps under them these are paragraphs. Pseudo-code numbered "1." under a
caption whose only nesting is between lines 9 and 10 is a list. An "=" step that is the first
of its block after an equation is not picked up when the line that opens the block wraps
into a second line that starts where it does. Counted on the public pages (wave 2b: the 22 pages
of `layout-dev-1`, the arXiv gold and the repository's own with an "Algorithm" or "Procedure" line)
and not built, each on one page: a step whose second line is a paragraph of its own between the
steps (Algorithm 1 of ax_statme_240307124, p12). The other two (a step after a monospaced comment
row, 2405.15050 p4; an Input list of two-row math regions, 2504.06207 p4) are built, below.

**Algorithms, wave 4 (O-394).** Five shapes counted on public pages are read (the 27 pages that
hold an algorithm were read before and after; the pages the change touches are 2405.15050 p4,
2504.06207 p4 and p5, ax_csro_240306420 p9 and ax_eesssp_240306460 p6):
a *line number alone in its row* ("7:" with the step's words, formula or comment in rows of their
own on its baseline: the text layer lists them apart; `_lone_number`) is the next number when it
is the one expected (`_Steps.number`), and the rows right of it on its line are pieces of its step
(`_Steps.after_lone_number`), as small rows and rows of math are; a lone number is not the
equation's end (`_after_equation`) nor a proof of nesting or of a step word (`_is_algorithm`), and
the paragraph writer gives it an empty step that its pieces fill (`_paragraph_pieces`). A
*comment that opens with "▷"* ("▷ set of HPs to apply") belongs to the step on whose line it
stands, whichever row the page lists it in (`_COMMENT_MARK`; any step taken, not only the last
four). A *monospaced row between two numbered steps* is a step's comment when the next number is
just below it (`_scan`: a code row is free only then). A *row of math under a numbered "Input:"*
list is the rest of that step, as text rows are (`_step_kind`: right of the numbers with the next
number just below it). A *number a little smaller than the steps* (a gutter in 8 pt over 10 pt
text) resumes after an equation (`_resumes`). "Algorithm A-1" is a caption, so its Input and
Output lines join the numbered steps (before this they were headings). Measured on a counted
page and not built: a numbered tail at the top of the next page (2504.06207 p5 step 9; the page
has no caption and starts at a number), a heading under an algorithm that its last line
absorbs, a `for ...:` line over an equation, an opener that wraps flush, a one-line block,
2504.06207 p4 steps 9 to 13 (the content of a numbered step listed by the text layer before its
number), 2405.15050 p4 steps 10 to 12 (a stacked math region listed before the numbers), the
boxed "TABLE III" algorithm of ax_eesssp_240306423 p6 (two columns interleaved), and the garbled
math of step 8 in ax_csro_240306420 p9 (a math-region fault, not the algorithm's). Found beside
them and outside the algorithm functions: `glyphs._MONO_FONT` did not name "NimbusMonL"
(`Mon` + `L`), "CMITT", "TeXGyreCursor" or "t1xtt", so a listing set in those fonts (the numbered
listing of 2505.13042 p3; 42 pages of 8 files carry NimbusMonL) was not taken for code and was
read as prose; the names are now in the list (O-440, "Code fonts" under Code blocks).
Known wrong reads, all prose
taken for steps (independent reviews 8, findings 5 and 7, 9, finding 5, and 10): text set in
under a last step that reaches the right edge is taken into the block (lines hung under a
step are the step's); the bold heading of the next section ("4 Results ...") right under an
algorithm is taken as its last line (and with four lines or fewer the levels of the steps shift
too); the running text under a float's
caption, when two numbered lines follow it, is one block with them; under a float's caption, a
line that opens "for" and ends in a colon over a display equation that starts within 6 em of
it (a wide equation, a narrow column, equations set flush left), and a short line, then a
paragraph of two lines whose second line opens with a step word ("for every word of the
verse."), then a paragraph that opens "If"; a paragraph that opens
"Algorithm 2 computes ..." or with another verb not on the list; two
short paragraphs that open "For ..." and "If ..." right under a float's caption; a sentence
that opens "Then", "For" or "If" right after a display equation that follows an algorithm; a
loop line with no "do" or colon that reaches the right edge takes the first line of its body;
and a manuscript page with line numbers, indented paragraphs and a line that opens "until".
Unlike program code an
algorithm's inline math stays math: a math row inside it becomes text, the formulas in it are
inline equations as in any paragraph, and `assemble.code_block` writes them with the equation
markup. In the Word file a code block that holds an equation is written as lines of a paragraph
(pandoc would flatten the equation to its characters), the indentation and the gap after a line
number as no-break spaces (`export_docx._lines_of`). A monospaced listing is code, read before
this, and its line numbers are not taken apart.

**Monospaced quotations (WS2.6 c).** A block of monospaced lines that has none of the
punctuation code has (brackets, `;`, `=`, `_`, `#`, `\`, `|`, `&`, an identifier followed by
`(`, an option such as ` -x`, `//`, `->`), has no line that ends in ":" over a body set
further right, has at least `QUOTATION_MIN_WORDS` (8) words and a sentence ending in a full
stop, comma, question mark or exclamation mark is prose, not code (`_is_quotation`). A shell
command, a name or a short snippet stays code.

**Code joined across prose (WS2.6 a), not built.** Monospaced lines already make one code
block with their printed indentation (`preformatted`). Joining two listings that only a
caption or a comment sets apart was measured on the corpus before it was built: of the 37
places where prose sits between two listings, every one introduces the next listing ("The
output is:"), so joining them would have made one block of two. The two stay two blocks.

### Tagged PDFs (T2-18, `pipeline/tagged.py`)

A PDF exported from Word or PowerPoint with tags carries a structure tree. Read once after
layout (`read`, MuPDF's structured text for the headings, the structure elements for the
figures), used as proposals (`apply`):

- a tagged `H1` to `H6` over a line whose text is the same (letters and digits, case
  aside) makes it a heading even in body-size regular type (a paragraph becomes a
  heading), with the reason `tagged heading h2`; the tag's level is the raw level
  (`heading_levels`) and the evidence is strong (confidence 0.90, no weak-evidence note).
  A tag over text that layout split or joined differently changes nothing;
- a `Figure`'s `Alt`, matched to the figure by its `BBox` (or alone on the page), goes to
  `Element.source_alt` when `check_alt` finds no error and it is not Word's own
  "Description automatically generated". The figure step sends it to the model as context
  ("Alt text in the PDF's own tags (may be wrong): ..."); when the model gives no usable
  description (failed, abstained, or a description that fails an error check) the tag's
  text is the proposal, NEEDS_REVIEW and required, with the warning `figure.source_alt` /
  `source_alt_unchecked`. It is never written into the output unchecked.

Lists and table header cells are not read from the tags (WS14.11, measured 2026-11-18): the
only tagged files in the measured sets are generated test files (21 of 332 PDFs; no real
upload is tagged), and on them layout already calls every list item a list item where the
tag's text equals one element (37 of 37), and no tagged table sits over a grid that has no
header cells (0 of 7). Read them when a real tagged file shows a missed list item or header
cell: the generated files write `Lbl` then `NonStruct` (not `LBody`), a list item can wrap a
heading, and a tag's header cells belong on the table step's answer, never in its request.

Fixture: `tests/fixtures/tagged/` (HTML exported by LibreOffice as a tagged PDF, two
edits to look like Word's export; `make_tagged_pdf.py`). The eval documents are printed
untagged on purpose (`eval/render.py`), so the dev-v2 numbers do not move with this.

### The PDF's outline (bookmarks, `pipeline/outline.py`, 2026-11-02)

Word, LaTeX with hyperref, InDesign and browsers write the document's real structure as
bookmarks: a title, a level and a page each (`Document.get_toc`). `layout.analyze` reads
them once after the elements are made (`outline.apply`) and uses them before the font-based
rules would guess:

- a heading layout found, or a paragraph that is only a plain title (no list marker, code,
  glossary term or inline math), whose letters and digits equal a bookmark's on that
  bookmark's page (a section number the other lacks aside: `3 Forces` and `Forces`). A
  heading gets the reason `outline heading h2`: `tagged_level` reads it as it reads a tag
  (strong evidence, confidence 0.90, the bookmark's level as the raw level; the outline
  repair still applies). A tag wins over a bookmark; each line serves one bookmark, in
  reading order;
- a *paragraph* becomes a heading only if it looks like one (`outline._promotion`): at most
  12 words and 100 characters, no sentence end (`. ? ! : ; ,`), and either bold or set at
  1.1 x the body size or more (`outline heading h2`, as above), or set off by space above it
  (0.8 x its own height, or first on its page): `outline candidate h2`, the bookmark's level
  but weak evidence, so an optional note like any bold body-size line. Otherwise it stays a
  paragraph. A tool that bookmarks every short paragraph is not believed: when more than 30 %
  of the document's paragraphs (and more than two) would be promoted, none is;
- the heading's text becomes the bookmark's title when the two are the same letters (a
  math-italic letter, a ligature, spacing) and the heading has no raised or lowered runs;
- consistency: at least 60 % of the bookmarks that point into the analyzed pages must name
  such a line on their page, or the whole outline is ignored (an outline of another
  edition, or one a tool generated per page). An outline with one level only is ignored
  when the headings it names are set in two or more sizes, because the sizes then say more
  about depth. Headings the outline does not name keep their font-based levels;
- reading is bounded (5,000 bookmarks, titles of at most 200 characters) and an outline
  that cannot be read gives none.

The headings and document evals print the gold pages from HTML with Chromium, which writes
no outline by default, so their numbers do not move with this; on the authored dev-v2 PDFs
that have one (Word and LaTeX exports) it is measured below ("Structure fixes, 2026-11-02").

## Hidden text and page types (`pipeline/page_type.py`, pipeline 0.4.0)

**Why.** A PDF can carry text nobody sees. An iPad notes app writes an invisible layer of
handwriting-recognition guesses over the ink (fill opacity 0, an Apple ".SF" font); an
OCR program writes its guesses over a scan in render mode 3, or paints the page image
over visible text. Read as content, a real 4-page set of iPad lecture notes became 26
headings and 72 paragraphs of garbage, titled with a 35 pt scrawl, with no math, no
model call and 0 decisions: "Finished. The accessible HTML is ready." OCR'd scans fell
into the same hole, and the app's own advice for scans ("run OCR and upload again")
produced exactly those files. There is **no handwriting or scan conversion by default**
(decided 2026-10-09): such pages are kept as page images, marked "Not converted", with a
decision. On request, per upload (the box "Convert scanned or handwritten pages", offered by
`FEATURE_IMAGE_PAGES`, default off in code, production's value in `src/readablestem/ops/production.profile.toml`; [ADR 0007](decisions/0007-image-pages.md), measured in
[eval-image-pages.md](eval-image-pages.md)) a page the types below call
`scan_no_text`, `scan_ocr`, `handwritten` or `handwritten_ocr` (kept reasons `scanned_page` and
`handwritten_page`, and a picture under a text layer that does not cover it, `unread_page`
with the reason `text_not_covering_content`; nothing else) is read from an image of the page instead, every block of it
is a review item, and the page keeps its original: see "Pages read from an image" below. A page
the pass cannot read stays a kept image exactly as described here.

**What is never content** (`_page_rows`, the same extraction as before, no second one):

- a span in render mode 3 (MuPDF's char flags show neither fill nor stroke);
- a span drawn under 2 pt (`page_type.TINY_PT`). The size is the one the glyphs are drawn at
  (`page_type.drawn_size`): dvips writes a bitmap TeX font as a Type 3 font measured in
  pixels and sets 10 pt text at a stated 0.24 pt (300 dpi) or 0.12 pt (600 dpi), so the
  stated size times the font's height in its own units is used, and layout reads that size
  everywhere (`layout._text_blocks`). Before 2026-11-18 such a page was typed blank and came
  out empty;
- a span at 5 % opacity or less (rawdict `alpha` <= 13), unless it is text used as a
  clip (render mode 7, a gradient- or picture-filled title: MuPDF gives it alpha 0 with
  the CLIPPED flag), `page_type.span_hidden`. Clip text is visible only when something
  is painted through it: it goes through the render test below, and a clip nothing was
  painted through counts as invisible;
- a visible character painted first and then covered by an image, a shading, a filled
  shape or a stroke at least 3 pt wide ("covered": ABBYY-style text under the page image,
  a black box over a line, a white bar or a marker scribble over it). Annotations count:
  a box, highlight or text box drawn with Acrobat's or Preview's tools over a line is the
  most common fake redaction. An icon the reader opens or moves (a sticky note's, an
  attached file's) is not a cover: the word under it is kept whole. Paint order comes from `get_bboxlog` and `get_texttrace`
  (only read when a visible span lies inside an image, a shading, an opaque fill or a
  thick stroke); a cover must hold the middle 60 % of the glyph, so an underline or a
  strikeout is not one; and the text counts as covered only when removing it (a
  text-only redaction on a one-page copy, its annotations made page content first, as a
  redaction deletes the ones it touches) does not change the rendered page there by more
  than 1 % of its pixels. That render is the opacity check: soft masks, color-key and
  stencil masks, constant alpha, blend modes and a yellow highlight all let the text
  show, and then it is kept. Up to 24 runs are tested per page, the rest follow them.

- a span the rendered page shows nothing of: white on white, navy on a navy box, black on a
  black box, a grey too faint to read. The page is rendered once (100 dpi, with its
  annotations) and the lightest and darkest pixels inside the span's box are compared: under
  a contrast ratio of 1.25 (WCAG's measure) the box is flat and the span is left out
  (`page_type._flat_spans`). The render is made only for a page with a span that could be
  flat, text lighter than 2:1 against white or text over an image or an opaque fill (dark
  text on the bare page is seen), and never on a page with a picture over the pixel budget.
  The earlier test that compared fill colors gave false positives and is not used. Their
  characters are counted with the other hidden ones (`hidden_text_ignored`) and leave
  `Element.text`, Listen, Word, EPUB and the other outputs at the row. They, and the spans
  under 2 pt, are painted text and not the mark of an OCR layer, so they do not count toward
  calling the page a scan under a text layer: a typed page with a long white sentence stays
  a typed page. The stored page type keeps their count (`unseen_chars`, written when above 0, 0 for
a record stored before it), so the review screen counts the hidden text of a kept page as its
notice does (`PageType.left_out_chars`). The glyph reads for
  an equation's line (`line_glyphs`) and the text pass (`page_glyphs`) do not look at the
  render: they skip only the spans of the first three kinds above, as they skip covered
  ones.

The text pass's glyphs (`page_glyphs`) skip the same invisible spans.

**A page saved sideways.** A page with no `/Rotate` whose text runs up or down the page (a
landscape page printed to PDF, a `sidewaystable` page) has only "vertical" rows if it is read
as it is. When at least 80 % of its visible characters (60 or more of them) run one way,
the page is turned before anything reads it (`page_type.turn_page`: 90 degrees clockwise when
the text runs up, 270 when it runs down): its `/Rotate` is set in the open document, so
rows, headings, reading order, tables, crops and page sizes are in the turned frame, as for a
page that has a `/Rotate`. Every reader that opens the PDF again and uses layout's boxes
calls the same function on the pages it uses (the crop cache, the text pass, tagged figures,
the source facts, the page labels and the review page image in `web/pdf_probe.py`). A table
set sideways on an otherwise upright page is still turned by the table step alone
(`tables._page_turn`); a short label set sideways (an axis title, an arXiv stamp) turns
nothing. The turn is in the document the readers open, not in the PDF file: layout keeps it on
the page's `PageTypeResult.turn` (not in the stored page type) and says it with the notice
`page_turned` (information, one per page, listed under "Handled in this file": "N pages saved
sideways in the PDF and turned upright to be read"), so the pictures of the page, which show
the turned page, are accounted for. A job read before the notice existed has no such line, and
nothing else differs: its elements are stored in the turned frame, and every later reader of the
file turns the page again by itself, so the stored page type need not keep the turn.

Which typed pages with hidden text are worth a look was measured on the corpus (212 PDFs: the
uploads, the real-world sets and the layout gold; counts only): ten files had a typed page with 3 or
more hidden characters, 38 pages in all. 32 pages had under 400 (a stamp repeated on every page,
a few labels, words printed on the page as well, text under a picture), and the largest of them
had 317. The other 6 pages, all in one file, had 484 to 2,767 each, mostly words that are not on
the page, beside drawings made of hundreds of paths and, on four of them, almost no visible text:
a text layer for words drawn as shapes. That is the case that holds the job; below 400 the line
stays optional. The tool that would keep such a page's hidden words was not built (docs/layout.md
"Not built", `CHANGELOG.md`).

**Page types.** `page_type.classify` reads the rawdict and the drawings layout already
extracts (`_read_page`; image boxes come from MuPDF's image blocks, which decode no
image) and returns a kind, the characters it may trust, where its content would have to
come from, and content-free reason codes. It is detect v4 from the research, ported with
the design review's changes (`CLASSIFIER_VERSION` "page-type-v5.5"):

| Kind | What it is | What the app does |
|---|---|---|
| `born_digital` | visible text that covers the page's content, nothing hidden | reads it as before |
| `mixed_hidden_text` | visible text plus invisible or covered words | reads the visible text; notice `hidden_text_ignored` (optional) with the count, or `hidden_text_check` (required) from `page_type.HIDDEN_BLOCK` (400) characters on |
| `mixed_ink` | visible text plus handwriting | reads the text; the ink is kept out of the figures and each region of it (strokes within 24 pt, at most 6 regions) becomes a FIGURE kept as an image (`handwriting_on_page`). On a typed page with lighter ink (fewer than 12 strokes, so `born_digital`), any pen stroke, clean or not (two pen widths in one note fail the clean test), that no figure holds is cut out the same way, from one stroke up (`layout.LOOSE_INK_STROKES`: a short note, a signature, a word circled and checked by hand over the typed text, which stays read); an equation or a table does not hold it (a transcription or a rebuilt table would leave the note out). Marks too simple or too long for the stroke test (a tick of three points, a cross of two lines, a one-stroke signature) are handwriting when the stroke itself is a pen's (since 2026-11-11: "Small drawings on a typed page"), or when drawn over typed text ("Pen marks over typed text"); straight lines that meet at their ends or run parallel (a skeletal structure's bonds) and a printed seal's even arcs are figures for the figure step; a pen stroke that is a glyph outline in a typed line is a figure too ("Glyphs drawn as outlines in a line of text") |
| `handwritten`, `handwritten_ocr` | vector ink, no or only a hidden text layer | the page is kept as its image (`handwritten_page`): no elements |
| `scan_no_text`, `scan_ocr` | a picture of a page, no or only a hidden text layer | the page is kept as its image (`scanned_page`): no elements. With a little visible typed text and nothing hidden (a typed header on a scan, a title slide on a photo, a watermark or page number over a photographed page) it is not called a scan: its visible text is read, and the page is also kept as its image (`unread_page`). If the text does not cover the picture (reason `text_not_covering_content`, below) the image page pass reads it as a scan (`page_type.reads_image`) |
| `vector_no_text` | drawings, little or no text | read as before (figures); kept as an image (`unread_page`) when it has less than a line (40 characters) of visible text and its ink could not be analyzed in time; its visible text is read, its drawings are not sent to the figure model |
| `blank` | nothing painted, or paper rules alone | nothing |

The decisions in the rules, with the measured values next to each threshold in the code:

- 40 visible characters make a page "have a text layer". Ink is "clean" when exact
  repeats are at most 35 % of the complex shapes, filled strokes share one pen width
  (80 % within 0.75-1.33 x the median of their color), and at most half the strokes have
  a same-size twin. A page of writing has 40 or more clean strokes in 3 or more rows; a
  typed page with 12 strokes in a row has some handwriting. With no visible text at all,
  5 clean strokes in a row are already a short note; under a typed title (1 to 39
  visible characters) the page needs 12 strokes or a second row, as a row of five
  machine-drawn curves (five waves on a slide, five inductor coils) is not handwriting,
  unless a hidden layer lies over them: that is a recognizer's guess at a note (machine
  drawings have none), so the page is `handwritten_ocr` whatever its title.
  A handwritten page refuses the upload whatever its typed title (as before
  page-type-v5.2): the few curves that may be machine-drawn are not called handwriting
  under a title in the first place.
- **Paper rules** are long (half the page or more) horizontal and vertical lines, each
  its own: the rules of ruled, squared, margin or Cornell paper. A page with only these
  is `blank`. A square wave, a step plot or a line-drawn block diagram joins its lines
  into one path, or has short ones: those are drawings.
- **The page is its CropBox**: image coverage and the off-page test of drawings are
  measured in the CropBox's frame, in which MuPDF gives every box, so a scan whose
  CropBox trims the scanner's margins is a picture of the whole page.
- **Hidden-ratio rule** (C1.2): a page is a picture or ink page whatever its visible text
  when its hidden characters are at least max(20, its visible characters), or when a page
  image covers half of it and 20 or more characters are hidden (a scan with a visible
  "Downloaded from" stamp; notes under a typed template header).
- **A text layer makes a page typed only when it covers the page's content**
  (page-type-v5.5, 2026-11-03; `TEXT_NOT_COVERING`). Three of five real photographed notebook
  pages carried a watermark text layer (a diagonal channel name, a header, a page number), so
  they were called typed, skipped the image page pass and came out "Not converted" with only the
  watermark as text (a decision was asked, as for any kept page, but the page was never read).
  Now a visible text layer does not make a page typed when all of these hold: pictures cover 75 %
  of the page or more; 60 % of the page is picture with no visible text over it (boxes of text
  lines, or of characters on a slanted line); the text covers under 12 % of the page; the text
  has under 20 characters of *body* (furniture is translucent text, slanted text, or text whose
  line is in the top or bottom 8 % of the page: a watermark, a running header, a page number, a
  stamp); and a 72 dpi render shows ink (tone 45 grey levels from the page's usual one) on at
  least 2 % of the page outside the text. The kind stays `scan_no_text` and the page stays
  `unread_page` (its text read, so the words of a title slide are not lost when the pass is off),
  but `page_type.reads_image` is true and the image page pass reads it as a scan. The render is
  made only for a page the rule might apply to. A picture that is the template's background
  repeated on the pages or one plain color is not content: layout asks (`_page_picture`) and
  decides such a page again without the rule (`page_type.without_picture`).
  Calibration: on the 846-page labeled corpus (the author's machine, not in the repository;
  `scripts/page_type_corpus_compare.py`) the rules change no page's kind or action; a first version
  without the body-text gate changed 10 pages, all against their labels (designed covers and
  background-image pages with real text over a picture; typed slides and boxes with ink
  annotations, which a rule for vector writing under thin text had turned into `handwritten`; that
  rule was dropped). The rule for vector writing is back with the same gate (below). The three real pages: picture 0.88 to 0.91, no text over it 0.78 to 0.81, text
  0.08, ink outside the text 0.13 to 0.17. **Vector writing** (a page of writing, 40 clean strokes in 3
  rows, as iPad notes) under furniture-only text goes the same way: `handwritten`, read by the pass
  (`reads_image`), its text still read when the pass is off. For writing a header or footer line
  counts as furniture only when it is a page number (15 characters or fewer); a typed header line
  over notes is text (the corpus labels such pages `mixed_ink`, and a looser rule turned 2 of them
  into `handwritten`). Ink-annotated typed slides have body text and stay `mixed_ink`. Not caught:
  a watermarked page whose body text is real; a typed page
  with a photograph of handwriting covering less than 75 % of it (a figure; its writing is not
  read). A scan whose OCR layer is hidden (render mode 3, covered) is read from its image as it
  always was; a visible text layer that covers the picture is the page's text.
- **A scan is a picture of a page** (C1.5): images cover 85 % of the page, or half of it
  with no visible text at all. A slide title over a photo is a typed page with a figure.
  Hidden text with no picture of a page over vector ink (clean, or rows of writing the
  checks rejected, or ink not analyzed) is handwriting (`invisible_without_image`). Over
  machine-drawn vector content (text turned into outlines, stamped shapes: what an OCR
  program writes over a vector page with no text layer) the page is `vector_no_text`: the
  hidden layer is dropped and the drawings are read as figures. Machine-drawn means
  evidence of it (`_machine_drawn`): more than 35 % of the complex shapes repeat exactly,
  or fewer than half of them pass the pen-stroke tests. Marks with no such evidence under
  a hidden layer and no visible text are pen strokes too simple for the stroke test (a
  letter written as one curve): handwriting again, as in page-type-v5.1, which called
  them a scan. With no picture at all (image coverage 0) a page is never a scan
  (page-type-v5.4): under a typed title, drawings with 3 or more pen strokes are
  `handwritten_ocr`, any other drawings `vector_no_text` (a slide's state diagram or a
  flowchart whose labels an OCR program read was refused at upload as `scanned_ocr`).
- **Near-white paint** (every color component 0.97 or more) is seen only on something
  darker painted under it (page-type-v5.4, `page_type.paint_visibility`, used by layout
  too): the topmost fill of 100 pt² or more at 50 % opacity or more painted before it
  that holds its middle is not near-white (a dark slide's background, a colored panel),
  or no fill holds it and a picture does. A dark slide's white diagram and its white
  chalk-style writing were dropped as invisible; a white box on the white page still is.
- **Work budget** (C1.6, plan WS14.9): the ink analysis (pure Python) is bounded per page by
  counts of work, which are the same on every machine (`page_type.InkBudget`). A page with
  more than 200,000 path items (`INK_MAX_ITEMS`), or whose paths the analysis cuts into more
  than 500,000 pieces (`INK_MAX_SEGMENTS`), is not analyzed. On the corpus (2026-11-11, 329
  pages) the most a page has is 117,162 items and 207,522 pieces, and the slowest page takes
  1.6 s, so no corpus page reaches a limit. Because the budget is per page, what a page holds
  never depends on how long the pages before it took. A clock stays only as a guard against
  a hang: 60 s a page (`INK_GUARD_S`), about 40 times the slowest corpus page. Such a page says
  `ink_not_analysed`, and its stored page type says why (`ink_cause`: `work` or `time`); the
  conversion writes a "not finished" fact for it (`page_type`, `work_budget` or `time_limit`),
  and a page read for its text gets the notice `drawings_not_analysed` ("Worth a look"), so
  the job page does not say "Everything was converted". A page not analyzed with less than a
  line of typed text is kept as its image beside that text; with a line or more it is read as
  before (its drawings are figures for the figure model), unless a drawing fills the page,
  which keeps the page image (the safety net below).
- **The upload probe's budget**: the probe reads every page that is not clearly typed inside a
  child that is killed after 30 s, so the document has an allowance of 1,000,000 path items
  (`PROBE_MAX_ITEMS`; the corpus's largest, a handwritten upload, has 386,717 on such pages)
  and one deadline of 20 s as the guard (`PROBE_GUARD_S`). A page past either is not analyzed:
  it never refuses the upload, and the count of scanned and handwritten pages
  (`image_page_count`) says "at least N" (`ImagePageCount`); a request to read such pages
  then still applies.
- **Page cap** (`page_type.PAGE_MAX_PATHS` = 50,000 drawings, `PAGE_MAX_ITEMS` = 500,000
  path items): the budget above is checked only after the work that costs most, and one
  dense page costs seconds and tens of megabytes of Python objects whatever the budget (a
  page of 86,000 strokes: 5 to 9 s in layout; 30 of them 4 minutes and 1.5 GB, before the
  cap). A page over either number is not read for drawings at all: `ink_stats` returns at
  once with `analysed` False and `over_cap` True, the page says `ink_not_analysed` and
  `drawings_over_cap`, `layout._read_page` builds no paths for it, and `kept_reason` keeps
  it as its page image with a decision (`unread_page`), whatever the typed text on it: a
  page with visible text keeps that text (as `text_kept` above), and a page with none
  has nothing but the image. Over the cap a page was before either read in full (a typed
  page with a huge vector figure: the figure went to the figure model; now the page image
  keeps it) or analyzed as ink (handwritten pages: the kept reason was
  `handwritten_page`, now `unread_page`; an upload of such a file is accepted, not refused,
  as for every page whose ink was not analyzed). The largest real page found has under
  30,000 paths; the dense synthetic pages in the research corpus have 30,000 to 86,000.
  The stored page record carries the reason code, so `kept_reason` gives the same answer
  from it.
- **A page kept as its image beside its text** (`text_kept`) is not given to the table
  detector (docs/tables.md): its drawings are not read, so no rule can judge a grid, and
  the detector is the slowest step on the pen-stroke pages these are (a lecture PDF with
  pen annotation: 12 pages, 4 minutes, nearly all of it there).
- **Clustering** (`layout._cluster`, the groups of drawings that make a figure) sweeps
  the drawings from left to right and looks only at earlier boxes still in reach and in
  the horizontal bands the box touches, instead of every earlier box in its column. The
  groups are the same ones (`_cluster_scan` is the plain version it is tested against);
  on a page of 40,000 strokes it took 38 s and now takes under a second.

**A page that raises** does not stop the document. If any step that works on one page at a
time raises on a page (a bug), that page is kept as its image with the reason `page_read_failed`
(a required decision, the words "This page could not be read"), and every other page is read.
One rule, `layout._PageGuard`, covers them all: reading the page (`_read_page`), the slide-deck
view of the page, the page's share of the margin statistic, the page-type adjustments, table
claims (`_claim_tables`) and rows (`_prepare_rows`, the plot and answer-field marks), a page's
list-marker candidates, its label keys, the page's blocks (`_page_blocks`), its elements
(`_to_elements`) and the safety net's look at it. The log has the page number and the error's
class and place, never its message or any text of the page. A spend cap, a cancel, a
configuration error and a refusal of the file (`LayoutError`) are not taken for a page that
raised. A document in which every page it was to read has raised still fails, counted over the
document and not per step: a page that raised in one step and another page in a later one
count together (`_PageGuard.settle`, after every step).

What a page that failed leaves out: the steps after it do not read it (its rows, paths, tables,
prepared rows and page type are dropped, and `page_types` has no entry for it, so the hidden-text
notices do not mention it); the margin filter does not take it as a reference page, and its
rows count for nothing in list markers or label keys. The document-wide steps work on the pages
that are left. A table that goes on from the page before is not joined to a page that failed. A
paragraph that goes on from the failed page onto the next is written there as a paragraph of its
own, not lost to the join. The
steps that look at several pages together stay unguarded, since one page cannot be blamed for
them: the margin statistic's thresholds, the body size and line pitch, the document's
spellings, the list-marker judgment, glossary entries, bold letters, numbered lines, run-in
labels, joins over a page break, the outline, notices, footnote and contents links, and the
slide-deck finish. The safety net is different: when its look at a page raises, the page keeps
the elements read from it and gets its picture beside them, as when something painted was left
unread.

**The safety net** (C1.1, independent of the page types): after layout, a page keeps its
page image, with a decision (`unread_page`), when something painted on it was read into
nothing. The elements read from it stay; the image comes after them. It fires when:

- something is painted outside every element read from the page (each grown by 2 pt)
  (an image or a drawing at least 12 pt on a side, or 3 smaller marks), on a page with no
  element or with less than a line (40 characters) of visible text: a short note of a few
  pen strokes under a typed title (too few to be called handwriting there, as they could
  be machine-drawn curves, and too small to be a figure), a drawing too small to be a
  figure. Rules (unless joined into a drawing, `_line_chain`, or a ruler's line and
  ticks, `_mark_axes`), paper rules, a frame round the page, a background fill, a band of color along an edge (a blank slide of a
  template), a drawn list bullet beside its row, and a mark that repeats in the header
  or footer band on half of the reference pages (a logo,
  `_MarginFilter.drops_mark`) do not count (`layout._painted`). Anything else in the
  bands counts, since 2026-10-12: a note written in the top or bottom margin under a
  title, or a pen mark in the footer of an otherwise blank page, was lost;
- on a typed page, a picture covering 85 % of the page or more that shows something
  (`layout._page_picture`): not plain (`_plain_image`: one color, a smooth gradient,
  nothing seen) and not the template's background (the same image on half of the
  reference pages, `_MarginFilter.backgrounds`). Such a picture is never a figure, so it
  was dropped as a background: a scan with a stamp or a header of 40 or more visible
  characters and no hidden layer (the design review's "d1" case), a slide on a
  full-bleed photo with a title and a paragraph, a typed page under a transparent picture
  of hand-drawn circles. Its visible text is read, and the page keeps its image;
- on a typed page with ink the checks rejected or did not analyze, a drawing too small to
  be a figure lies outside every element (it may be writing: never a figure for the
  model);
- a drawing filling the page was left out (`layout._vector_figures`) because the page
  has ink the checks did not confirm (rejected or not analyzed: a page of notes under a
  typed title, whatever the title's length), or because it is no plot and has running
  text inside or too few curves to be called a figure while holding a plotted line,
  curves or bars, and nothing was made of it once its border was off; or a drawing
  with text rows inside showed no sign of a drawing and is no box round text either
  (some row lies outside its boxes, panels and rules: "Boxes round text and diagrams",
  2026-10-13);
- 12 or more of the page's handwriting-like strokes (the page type's ink analysis, clean
  or rejected) lie outside every element read from it and outside the header and footer
  bands: writing that became no figure, table or handwriting region. Typed text turned
  into outlines (`vector_no_text` with rejected ink) is kept this way too, beside the
  figures read from it.

A page is "blank" only when nothing is painted on it but paper rules.

Layout returns `page_types` (per page) and `kept_pages` (page -> reason); the pipeline
turns each kept page into a figure for the whole page that is FAILED before any model
work, groups the kept images with one reason into one decision (`Fix.group_key`
"pg-" + the first one's id: a 20-page handwritten file asks once), and stores the
content-free records on `DocumentResult.page_types` (docs/output.md). Notices:
`ocr_text_layer_ignored` (review "none") for the hidden characters of a kept page,
`hidden_text_ignored` (optional) for those of a typed page, `hidden_text_check` (required, and
held at "Ready" by `states._HOLDS`) when that page has 400 or more of them, and one per kept image with
its reason (required: its decision is the image's fix).

**At upload** (`page_type.document_refusal`, in the probe child): the text of every page
is read first and the check stops at the first typed page, so only a PDF with none pays
for the drawings and the ink analysis. Every page certainly handwritten or scanned, or
blank, refuses it before any quota is spent: `handwritten` (hidden recognition layer),
`handwritten_no_text`, `scanned_ocr` (hidden OCR layer) or `scanned`. A handwritten page
is certain whatever its typed title; a scan needs no visible text or a hidden layer
(`page_type._certain`). A full-page picture with a few visible characters and no hidden
layer (perhaps a slide), a page kept by the safety net (a short note under a typed title
that may be a row of machine-drawn curves, see below) and a page whose ink was not
analyzed never refuse: the conversion keeps them as images with a decision. The worker
fails a result with the same rule (`worker.result_outcome`).

### Pages read from an image (on request, ADR 0007)

For a conversion that has the option on (a job whose owner checked the box; `ConversionOptions.image_pages`,
or the whole run for the CLI and the eval with `FEATURE_IMAGE_PAGES`), the kept pages with the reasons `scanned_page` and `handwritten_page` (not
`unread_page`, not handwriting on a typed page) are read by `pipeline/image_page.py`: one vision
call per page, the page at 200 dpi, long side at most 2,576 px (`IMAGE_PAGES_DPI`), the main model
unless `IMAGE_PAGES_MODEL` says otherwise, effort `ANTHROPIC_EFFORT_IMAGE_PAGES` (default low), up
to `IMAGE_PAGES_TIMEOUT_S` (180) per send. The answer is a list of blocks in reading order
(heading with a level, paragraph or list item as text and inline-math runs, display equation,
figure with a short description, table as a grid), each with a box on a 0 to 1000 scale and a
confidence, an unreadable word being the marker `[illegible]`. `image_page.build` makes elements
of them (ids `p{page}-e{n}`, `Element.reasons` has "image page", `Element.image_confidence` the
model's word), validates every LaTeX and table grid, trims each box to the ink in it, and
decides each fix:

| Block | What it becomes | Tier |
|---|---|---|
| display equation | an equation element with the picture-of-an-equation reason and the page reader's LaTeX in `page_latex`; the equation step reads the crop of its box and compares (ADR 0006's two readings) | always **required** (`equation.no_text_layer`) |
| table | a TABLE element, `TableFix` from the model's grid through the table step's constructor and checks | always **required** |
| paragraph, list item | an element and a `PARAGRAPH` fix | optional at `high`; **required** at `medium` or `low`, with an `[illegible]`, or with an inline formula that failed |
| inline formula | an inline equation element validated like any LaTeX, accepted without a decision of its own (it rides with its paragraph) | none |
| heading | a heading element with the page reader's level (`REASON_IMAGE_HEADING`); the heading step repairs the outline, then `tier_headings` | optional; required as for prose, or when the heading step asks |
| figure | a figure element, the description as its alt text | optional at `high`; required otherwise |

A display equation whose crop cannot be read (box off the page, "not an equation") keeps the page
reader's reading as the proposal, required (`image_page.rescued`, check
`image_page.single_reading`). The page's own picture is one more figure element, first in the page,
with no fix (`IMAGE_PAGE_ORIGINAL_REASON`). A page the model cannot read (refused, cut off,
invalid, no usable block, a spend cap) stays in `kept_pages` and is kept as its image. Each page
has a record in `DocumentResult.image_pages` (outcome, model, calls, tokens, dollars, blocks by
kind, markers, decisions and notes), its cost is in `DocumentStats` and its notice
`image_page_converted` (optional) on the job page. With `SCAN_LAYER_CHECK` on (off in code and in
production), a scan's own text layer (an OCR program's, invisible or painted under the picture;
not a tablet's on a handwritten page) is never sent to the model and never becomes text, but
it checks the page read that was made anyway (`image_page.layer_missing`, `layout.page_words`,
no extra call and no change to any request): its words of three letters or more that the read has
no word like (within an edit or so, which a scanner's noise explains) are counted, and when they
are 15 % or more of the layer's words (a layer of fewer than 20 words is left alone) the page
gets the notice `image_page_layer_differs` (optional, with the count). It ships off because it
adds a notice to the output and real scanners' layers are not measured (O-058). The threshold was
chosen on the tracked scans with a layer (`tests/fixtures/handwriting/scan_ocr.pdf`,
`scan_stamp_ocr.pdf`, `scan_text_under_image.pdf`, page 4 of `mixed_course.pdf`; 58 to 68 words
each): layer words edited once in 5 to 40 % of them left at most 0.12 without a partner in the
read; a read cut short by a tenth gave 0.12 to 0.15, by a fifth 0.24 to 0.26, by half 0.50. Words
the read has and the layer lacks are not counted: a layer may lack text and the read adds none to
it (0.19 to 0.20 for a read 30 % longer than the layer, against 0.15 for the noisiest layer: too
close to separate). When the box is checked the upload probe lets a
PDF of scans through and counts its scanned and handwritten pages (`pdf_probe MAX_PAGES
image_pages`, `page_type.image_page_count`), and `worker.result_outcome` does not fail the result for
being only scans; the job's spend allowance is wider ([web.md](web.md), "Scanned and handwritten
pages on request").

**What is not detected** (the text reaches the HTML, or the content is lost as before):

- white text on a white page or a white cell (needs the color under each glyph);
- text under a shape that covers less than the middle of its glyphs, or under strokes
  thinner than 3 pt (many passes of a thin pen over a line);
- an annotation hidden from view (the Hidden or NoView flag) is not a cover, rightly:
  the text under it shows;
- very small text (0.4 pt "microtext");
- OCR text drawn visibly in place of the image (Acrobat ClearScan-like): it is trusted,
  garbled math included;
- white paint on a page whose dark background is a picture painted after it, or on a
  fill under 100 pt²: still taken as invisible;
- a full-page picture under a line of typed text or more is kept as an image with a
  decision (above), a needless one for a background photo that is not repeated on half
  of the pages (a one-off title slide on a photo); a plain or repeated background is
  not;
- hand marks too simple for the stroke test written over a line of typed text are cut
  out when they are one or two strokes that reach out of the line, or one closed or
  filled curved outline in another color than the text ("Pen marks over typed text");
  a check mark no bigger than the text, a mark whose shape reads as a radical sign, three or
  more such strokes over the text, a circle drawn in the text's own color, and a
  straight-sided mark (a box drawn round an answer) are still taken as part of the line
  (as an underline and a box round a word are) and lost. Pen strokes the stroke test
  counts are cut out wherever they are. Strokes that cluster into a drawing of 24 pt or
  more on a side are a figure for the figure model, as before. Small machine drawings
  made of open strokes only (a curly arrow), one or two such strokes over a line that
  reach out of it, and a colored outline drawn round two or more characters of the text
  by its author (a red ellipse round a term on a slide, a colored badge round a step
  number) are cut out as handwriting: a needless decision, never a loss;
- grids of small machine-drawn curves (sparklines, small multiples of loops) can look
  like handwriting: kept as an image with a needless decision (a PDF of only such pages
  is refused at upload, with or without a short typed title);
- a slide whose background is a full-slide picture and which has fewer than 40 visible
  characters is kept as an image with a decision (never refused); its typed text is read
  as well, and the student file says "shown as a picture", not "scanned";
- a typed page with at least as much hidden text as visible text (an OCR layer run over a
  typed page) is kept as an image with a decision under the hidden-ratio rule, called a
  scan: its typed text is not read (not silent, but a loss);
- a page of dense handwriting the ink checks reject (exact repeats among tiny strokes)
  is `vector_no_text` (or `born_digital` under a line of typed text); the safety net keeps
  it as an image when its writing fills the page or became no element, but writing that
  covers less of the page and clusters into one drawing is a figure, described by the
  figure model like any drawing. Layout is slow on such pages (tens of seconds at 85,000
  paths, most of it table detection and figure clustering, as before);
- a page-filling drawing with fewer than three curves and two bars and no axes with
  evidence of a plot or ruler (a full-page plot of one line whose axes cross rather than
  meet at a corner, with no ticks), or with running text inside it and no such axes, is
  not sent to the figure model (a decorated border or a background drawn behind text
  looks the same): its page is kept as an image with a decision when it holds a plotted
  line, curves or bars that the border test does not make a figure of, a needless one
  for a decorated background; a frame round the page with a margin wider than 15 % is
  not recognized, so a plot inside it is a figure the size of the frame;
- an L of axes with no evidence of a plot (no ticks, no tick label or axis title just
  outside it, no curve or bars starting on it: a sketched plot with a curve that starts
  part way up the y-axis) is taken for a frame's two sides, as a title slide's accent
  is: the curve is a figure, but the axes are neither in its crop nor kept (as before
  2026-10-12);
- axes that cross (a T or a +) instead of meeting at a corner are not axes to
  `_mark_axes`: a page-long one near the page's edge with no ticks is still set aside as
  a side of a frame. The plot that touches it is a figure all the same, but its crop
  holds only the part of that axis beside the plot. An axis drawn as three or more lines
  (a box of four spines with no ticks) is a closed frame-like chain, not axes either;
- tick labels outside the header and footer bands further from a figure than 0.6 x
  their size (a slide's labels 7 pt under its x-axis) are read as paragraphs after the
  figure, not as its labels: in the text, not lost;
- a flat drawing less than 24 pt tall (a one-line circuit, a timeline) is too small to
  be a figure by the 24 pt rule; on a typed page it is a figure of its own ("Small
  drawings on a typed page"), with less text the safety net keeps the page; a ruler (a
  line with three or more ticks), bonds between atom groups and boxes joined by wires
  count there even when they are only straight lines. A drawing inside a line of text
  (math drawn as outlines mid-sentence, an icon in a sentence) is held by the paragraph
  and lost, and so are straight lines and boxes there that are no bonds or wires;
- a ruler whose ticks stand on one side of it a letter cell apart with no labels is
  taken for an answer field's cells and dropped with the rules (a scale of that shape
  with no numbers is lost);
- a bar chart's baseline that runs along a page edge (85 % of its length, within 15 % of
  the edge) is set aside as page furniture: the bars are a figure, the baseline is not
  in its crop. Axes drawn as one path that stops a few points short of the corner are no
  L: the curve is a figure, the axes are neither in it nor kept. Unfilled bars with no
  baseline joining them are one figure each (a decision each), their category labels
  paragraphs. None of these loses the chart's content (the drawings sweep, 2026-10-13);
- with `FEATURE_TABLES` on, drawn marks inside a claimed table's cells (check-mark and cross
  icons, a hand check mark over a cell) exist only in the table's crop: a model that leaves
  those cells empty passes the check against the PDF's words, and the marks are lost.
  Not tested offline (the fake client's table is always a decision); with the flag off,
  the default, the table is kept as an image with a decision;
- typed text turned into outlines (no text layer) is read as figures where it clusters
  into blocks; lines that do not are lost, so the safety net keeps the page image too
  (one decision for the document's kept pages).

**Accuracy, honestly.** Measured with `python -m readablestem.eval.page_types --labels
LABELS.json` (docs/eval.md "Page types") on sets that are not in this repository (they
hold derivatives of a real lecturer's notes and local research PDFs). "Right action"
means the app does with the page what its true kind calls for (read the text, keep the
page as an image, cut out the ink); the scorer does not run layout, so the safety net,
which keeps some `vector_no_text` pages of dense ink as images, is not counted.

- **Fresh set 4, the only out-of-sample number**: 31 pages built after page-type-v5 was
  frozen (commit `dc891a2`), with new generators (a cursive writer with one stroke per
  word, grid and dot paper, scans of other pages at other resolutions, a highlight
  annotation, a slide background, partial covers, blank paper). v5 got **27 of 31 page
  kinds and 28 of 31 actions** right. Wrong: a picture of a slide letterboxed on a
  portrait page (44 % of it; read as a figure, not lost), an /Ink annotation of 4 strokes
  on a typed page (as designed: fewer than 12), a picture pasted over half of one line
  (the check looked for covers of whole lines), and a page of ruled paper with nothing on
  it (called drawings). The last two were then fixed (page-type-v5.1: a cover over part
  of a line is looked for; paper rules alone are blank), so v5.1 has no out-of-sample
  number of its own.
- **The research corpus, 996 labeled pages** (not out of sample: every page was seen
  while detect v1-v4 were tuned): 586 real born-digital pages, the 4 pages of one real
  set of handwritten notes and 23 derivatives of them, and synthetic handwriting, scans,
  OCR layers, mixed pages and hard negatives. page-type-v5.1 gets 984 page kinds right
  (98.8 %) and 989 actions (99.3 %); detect v4 had 984 and 990. page-type-v5.2 (the
  routing review's changes, 2026-10-10) gets the same 984 and 989, with the same errors;
  on fresh set 4 (seen while v5.1 was fixed, so no longer out of sample) 29 of 31 kinds
  and actions, and on the design review's 41 adversarial pages 29 and 35, as v5.1.
  Its new cases (fake redactions drawn with annotation tools, a CropBox, square waves,
  diagrams under a title, a hidden layer over machine drawings) are tested on synthetic
  fixtures only, so v5.2 has no out-of-sample number either. page-type-v5.3 (the final
  verifier's changes, 2026-10-12: a hidden layer over a few strokes under a title, and
  over marks with no evidence of machine drawing, is handwriting; handwritten pages
  refuse the upload whatever their title) gets the same numbers on all three sets, with
  the same errors; on the 45 synthetic handwriting pages of the corpus, each also with a
  short typed title added, the upload is refused for 45 of 45 without the title and 44
  of 45 with it (the 45th, eight strokes in one row, is kept as an image by the safety
  net), and none is lost. One real page labeled
  born-digital now has 8 characters under a grey box and a seal painted over them
  (checked by eye: they cannot be seen), so it is called `mixed_hidden_text` and counted
  as an error against its label. page-type-v5.4 (the silent-loss sweep's changes,
  2026-10-12: near-white paint on something darker is seen, a page with no picture is
  never a scan) gives every page of the three sets the same kind and action as v5.3:
  984 and 989 on the corpus, 29 and 29 on fresh set 4, 29 and 35 on the adversarial
  pages; two born-digital corpus pages count more strokes (white paths on a colored
  panel), with no kind changed.

  | true \ predicted | born | mixed ink | mixed hidden | scan | scan OCR | hand | hand OCR | vector | blank |
  |---|---|---|---|---|---|---|---|---|---|
  | born_digital (631) | 628 | 2 | 1 | | | | | | |
  | mixed_ink (68) | 2 | 66 | | | | | | | |
  | mixed_hidden_text (28) | 2 | | 26 | | | | | | |
  | scan_no_text (51) | | | | 51 | | | | | |
  | scan_ocr (46) | | | | | 46 | | | | |
  | handwritten (60) | | | | | | 58 | | 2 | |
  | handwritten_ocr (64) | | | | | 2 | | 62 | | |
  | vector_no_text (44) | | | | | | 1 | | 43 | |
  | blank (4) | | | | | | | | | 4 |

  By split: the tuning pages 107 of 108, held-out 734 of 738, the three fresh sets of
  the research 79 of 79, 48 of 48 and 16 of 23 (detect v4 scored 16 of 23 on its last
  fresh set too, out of sample then, with 19 of 23 routes).
- **The design review's 41 adversarial pages** (seen while the C1 changes were made;
  built to break the classifier, so not a field error rate): v5.1 gets 29 kinds and 35
  actions right. Still wrong: white on white (2), microtext, visible OCR, the stamp
  without a hidden layer, a signature, two plot grids called handwriting, a dense page
  called drawings (kept by the safety net at run time), and the 3 photo slides, whose
  kind is `vector_no_text` but whose outcome (title read, photo a figure) is right.
- **Only 4 real handwritten pages exist**, one writer and one app (iPad). No real OCR
  engine output, real scan or real GoodNotes, Notability, OneNote or Xournal++ export was
  measured: those were simulated from their documented properties. The labels were
  given by the people who built the pages.
- Time: median 6 ms a page, 95th percentile 0.14 s, at most about 1 s on the densest
  synthetic ink (this machine); the real iPad notes (4 pages) are refused at upload in 0.5 s.

### Vector ink and strokes (`pipeline/ink_strokes.py`)

A handwritten page that a notes app exported as vector paths still holds the pen's trace.
`ink_strokes.extract_ink(page)` reads it into ordered strokes (pure PyMuPDF and numpy, no model
call, no network). The image page pass uses it beside the page picture, for pictures drawn from
the ink and for checks on the reading ("Strokes in the image page pass", below); `page_type`
shares its rule for a stroke drawn as samples.

**What is extracted**, from `page.get_cdrawings()` in paint order and in display space (the page
rotation applied):

| Drawn as | Stroke |
|---|---|
| stroked polylines and Bezier paths with a pen width, one per stroke, or one short segment per path object (as Xournal++ writes them) | `source="path"`: the path itself; consecutive two-point segments that continue each other, in one color and width, are chained |
| one small filled shape per pen sample (as GoodNotes writes them) | `source="samples"`: three or more touching shapes of one color in a row of paint order are one stroke through the shape centers |
| a filled outline of the whole stroke, several to a path object (as the committed `ios_notes.pdf` fixture imitates PencilKit) | `source="outline"`: the two edges are paired and averaged; the center-line is the stroke, and the mean gap is the pen width |

A `Stroke` has `points` (page points, y down), `colour` (`#rrggbb`), `opacity`, `width` and
`order` (paint order, which is the pen order). A short filled mark on its own is a one-point
stroke (a dot) or, when thin, a short tick. Wide see-through strokes (width at least 5 pt,
opacity at most 0.85: a highlighter) are returned apart, in `PageInk.highlights`. Fully
transparent paint is not read.

**One rule for "is this a pen stroke drawn as samples".** The page type's ink analysis
(`page_type._ink_candidates`) does not test small filled shapes on its own (a disc has no wiggle:
it was a dot, so a page drawn as one disc per pen sample was `vector_no_text`). It asks this
module: `dot_grid_members` takes the shapes on a lattice out (dot-grid paper), `sample_run_end`
finds the run of touching short shapes of one color in paint order, and `is_sample_stroke`
(three or more) makes it one stroke, which replaces the shapes' own candidates. The page-level
tests are unchanged (40 strokes in 3 rows, no exact repeats, one pen width), so scatter-plot
markers (equal discs that repeat, rarely touch in paint order), stippled shading (rows of
identical dots, no writing rows) and dot-grid paper stay what they were. Measured on the
846-page corpus: no page changes its kind; the added cost is about 55 microseconds per small
filled shape (a 3,000-marker scatter page: 0.37 s to 0.54 s).

**What is dropped as page furniture**, counted by reason in `PageInk.dropped`, never by color
(pastel ink on a pastel template stays): `rule` (an exactly axis-aligned line at least half the
page across), `grid` (four or more evenly spaced parallel axis-aligned lines, or four or more
equal exact rectangles, whatever their length), `dashed` (a dashed straight line), `frame` (an
exact rectangle at least 30% of the page across), `background` (an exact filled rectangle at
least a quarter of the page across), `dot_grid` (filled shapes on a lattice), `blob` (a filled
shape that is neither a pen sample nor a pen outline), `hidden` and `off_page`.

**Grouping.** `PageInk.regions` (reading order) holds `InkRegion`s. Strokes join into a line when
they overlap vertically and sit within four median-stroke sizes of each other horizontally;
lines that stack within half a stroke size and overlap horizontally join into one region (a
fraction, a matrix, a multi-line equation). Sizes are relative, so large and small writing
group alike. `InkRegion.strokes` is in pen order and `lines` indexes them row by row.
`InkRegion.normalised(size)` gives each stroke's points with the region's top left at the origin,
scaled evenly so the longer side is `size` (default 1000), and `to_inkml(size)` writes the same
as InkML (one trace per stroke, one brush per color and opacity, no time channel).

**Limits.** Checked on synthetic ink in each style and on the two committed fixtures only; no real
GoodNotes, Notability, OneNote or Xournal++ export has been read yet.

- The direction of an outline's center-line is a guess (the outline's first point is taken as the
  stroke's start). A closed loop such as a handwritten o can come out either way: on the seeded
  writer behind `ios_notes.pdf`, 139 of 150 strokes ran the right way and the 11 misses were
  closed loops.
- Fast pen samples whose shapes do not touch split into single dots. Three or more thin ticks
  that touch in a row join into one stroke. There is no pressure or time.
- A filled shape over 12 pt across that is not a long thin outline is dropped as a blob.
- A rule drawn by hand, exactly level, across more than half the page is taken for paper.
  A stroke whose outline crosses itself can pair its edges wrongly.
- Regions are a proximity guess: tightly spaced lines become one region (use `lines`), and a far
  superscript or answer can be a region of its own.
- Limits and time: the caps and the budget are `page_type`'s (`PAGE_MAX_PATHS`, `INK_MAX_ITEMS`,
  `INK_MAX_SEGMENTS`, `InkBudget`, `INK_GUARD_S`); `extract_ink` counts the pieces its curves
  are cut into against the budget, checks the clock guard in every phase and inside the loops,
  and over a cap, more than 20,000 strokes (`MAX_STROKES`) or the budget returns an empty
  result with `analysed` false and its `cause`. The budget is per page, so a fresh one (a
  stored result shown again, `run.recrop`) reads a page as the conversion did. Grouping finds neighbors with a
  grid hash, so it is near linear: 50,000 strokes in a column, a row or at random group in
  1 to 3 s on this machine; strokes piled on one spot (over 1,000 in one grid cell) are an
  overrun, not a stall. Reading outlines costs about 3 ms a stroke (0.5 s for the 150 strokes
  of `ios_notes.pdf`; 0.03 s for the 270 polylines of `ink_no_layer.pdf`).
- Strokes wholly off the page are dropped (`off_page`), and a region's box is clipped to the page.
- On the seeded outline writer (150 strokes, 7 pt writing), the center-line lies a median 0.4 pt
  (at most 1.4 pt) from the pen path; its ends can fall up to 2 pt short.

### Strokes in the image page pass (`pipeline/image_page_vector.py`)

A handwritten page whose ink `ink_strokes` can read is read from its picture as before (one
vision call, ADR 0007); the strokes are used beside it, never instead of it. A page qualifies
when `page_type` calls it `handwritten` (or `handwritten_ocr`) and `extract_ink` analyzed it
within the page's ink budget with at least 3 strokes (`image_page_vector.analyse`, through
`layout.page_ink`). A scan, a photo, a typed page and a page over the caps go through the pass
exactly as before. Measured on the vector dev set, a clean re-render of the ink does not make the
reader read better than a scan (docs/eval-handwriting.md), so the strokes serve pictures a person
compares against and checks, not re-reading. Four uses, none calling a model:

- **Sharp pictures** (`IMAGE_PAGES_VECTOR_CROPS`, default off). The picture of a display equation,
  of a prose block or list item that needs a decision, and of a stretch of ink the reading left
  out is drawn from the strokes that sit in its box (center within the box grown by a fifth of its
  height and 4 pt): black on white at 300 dpi (long side at most 2,048 px), cut to the ink with
  6 pt of paper round it, with no ruling, template or pen color. The model's equation request, the
  review screen and the student file get this picture; the student file's is drawn at the size its
  text is shown at and tagged with its resolution (`equation_scale.png_dpi`). A box with no stroke
  in it keeps the ordinary crop. The second reading's and the recovery's line pictures
  (`image_page._picture`) are drawn the same way, and `run.recrop` draws a stored page the way the
  conversion did when it is given the pages' kinds. The page picture the first call reads is still
  the 200 dpi render of the page.
- **Case check** (`IMAGE_PAGES_VECTOR_CHECKS`, default on). In a display equation's reading, a
  plain letter among c, o, s, u, v, w, x, z is compared with the line's own letters: the x-height
  from a, e, m, n, r and the capital height from digits, capitals nothing else is written like and
  ascenders, each read off the glyph of that symbol. k is not measured (its small form is as tall
  as K), nor are p and y (the tail hides the height). The ink must be nearer the other case than
  `CASE_UP_AT` and `CASE_LOW_AT` allow (0.62 and 0.38 of the way from x-height to capital height).
  A line with only one of the two heights takes the other as 1.55 times it; a line with neither
  uses the hand's heights on the page when two other equations on it showed both. The symbols and
  the glyphs must pair one to one, or nothing is said.
- **Script check.** The reading's symbols (`reading`: LaTeX as symbols with the level each is
  written at; fractions, roots, sums, matrices and accents over several symbols are not read) are
  lined up with the glyphs (strokes whose x-ranges overlap and that are close in height) in writing
  order. The baseline is fitted through the glyphs the reading puts on it (repeated medians, so a
  stray script does not move it; a slope up to 0.25 is allowed); a glyph is raised or lowered when
  it is no taller than 0.9 of the line's letters and its bottom is 0.45 above or 0.3 below the
  baseline. A raised or lowered glyph that the reading has on the baseline or leaves out is "script
  lost"; a script of the reading over a glyph as tall as the letters is "script added"; one raised
  in the reading and lowered in the ink, or the reverse, is "script moved". A line whose baseline
  scatters by more than 0.2 of a letter height, and a base with a subscript and a superscript
  written one over the other, are not judged.
- **Leftover ink.** Every glyph on the page must be near a block's box: within 1.2 glyph heights
  of it (the reader's boxes sit off handwriting by up to a line). Leftover glyphs within 3 glyph
  heights sideways and 1.5 vertically are one stretch; a stretch of 3 or more glyphs, outside the
  strips (5% of the page) where a short header, footer or page number sits, becomes the same figure
  element and required decision that the raster check makes (`image_page.omitted_region`), and
  `recover_omissions` reads it from a picture drawn from the strokes. It replaces the raster
  comparison on such a page, and sees pale ink, which the raster threshold does not. It is off with
  `IMAGE_PAGES_VECTOR_CHECKS` or `IMAGE_PAGES_OMISSION_CHECK`.

The checks only add a failed warning to the equation's fix (`image_page.case_check` and
`image_page.script_check`, sentences in `web/messages.py`); the fix stays a required decision with
its status unchanged. What each finds, and how the thresholds were set, is in
docs/eval-handwriting.md, "Checks from strokes". Inline formulas in a paragraph have no box of
their own and are not checked. The thresholds are in `image_page_vector.py`; they were set on
MathWriting ink drawn as paths and no real GoodNotes ink has been tried.

## The text pass (`pipeline/text_pass.py`, pipeline 0.4.0)

After layout and before any step, `run` calls `text_pass.apply` once. It reads the PDF
again for what layout does not keep (link annotations, `/Lang`, the color of each
glyph) and adds links, cross-references, color runs and languages to the elements. It
never changes an element's text and sends nothing to a model; URLs are never fetched. It
never fails a conversion either: a PDF it cannot read again gives no links or colors
(cross-references and the language still come from the text), and an error inside the
pass leaves the elements as they were, logging only the exception type. What the output
does with all this is in docs/output.md ("Links and cross-references", "Language",
"Colored text").

### From the page to offsets in `Element.text`

Layout does not record where each character of an element's text was printed: the text
is cleaned, joined across lines and dehyphenated. So the pass takes the glyphs of the
page (`layout.page_glyphs`: the same raw extraction as layout's, in display space, with
the span's sRGB color, spaces left out), keeps those whose center lies inside the
element's box (1.5 pt slack), and aligns the element's text with them
(`text_pass.align`, `difflib.SequenceMatcher` over the characters; spaces and inline
markers are left out of the text side). The alignment is monotonic, so a dehyphenated
word, an extra glyph from another block inside a merged box or an inline equation's
glyphs only leave characters unplaced; an unplaced character never starts or ends a link
or a color run on its own. Alignment runs only for elements a link rectangle touches,
and for elements that have glyphs in a run color, a style (bold, italic or fixed pitch) or
a strike; text over 20,000 characters is not aligned.

A link covers the characters whose glyph box is at least half inside its rectangle
(`MIN_GLYPH_OVERLAP`), so a rectangle that grazes the next word does not take it. Offsets
are into the text as stored, markers included. A link stops at an inline equation and
starts again after it; a URL split over two lines (hyperref writes one annotation per
line) is one link again when only spaces separate the two pieces.

### Links

`Page.get_links()` gives each annotation's rectangle (display space) and kind:

| PDF link | Becomes |
|---|---|
| URI with http, https or mailto (`core.types.is_safe_href`) | `Link(kind="uri", href=...)` |
| URI with any other scheme (ftp, javascript, file), a launch action, a link to another PDF, an action PyMuPDF cannot read | no link; the text stays; notice `link_scheme_dropped` on the element (or the page, with a count, when it covers no text) |
| GoTo, or a named destination, on an analyzed page | `Link(kind="internal", target_id=...)`: the element at the destination |
| GoTo to a page that was not analyzed, a named action ("next page"), a link to its own element | nothing, and no notice |

The destination is a point: an explicit GoTo gives it in display space, a named
destination (hyperref's) in PDF space, which is turned into display space with the
page's transformation and rotation. The target (`target_at`) is the element whose box
holds the point (2 pt slack), else the nearest one below it, since a PDF puts a
destination a little above its target and hyperref puts a figure's at its caption, under
the figure; elements above the point count double their distance, and an element in the
point's column (6 pt slack) wins over one that is not. Only elements the assembler
writes with their id can be targets: headings and paragraphs with text, list items,
display equations, figures and tables. A target that is an inline equation becomes its
paragraph.

Link text that says nothing on its own ("click here", "here", "this link", "read more")
gets the optional notice `link_text_unclear`, and so does a bare URL longer than 40
characters with fewer than three words around it in its paragraph or list item ("Notes:
https://..."). A URL inside a sentence or after a reference ("Smith (2024). Notes on
kinematics. Retrieved from https://...") is not flagged: the text around it says what it
is (WCAG 2.4.4, link purpose in context), so a reference list does not fill the job page
with notices. The text itself is kept: it is the visible name of the link (WCAG 2.5.3),
and only the instructor can reword it.

### Cross-references

In paragraphs and list items, `references` finds "Figure 2.3", "Fig. 4", "Figures 2 and
3", "Table 1", "Equation (3)", "equation (3)", "Eq. 3", "Eqn. (4)", "Eqs. (3) and (4)"
and "Equations (3), (4) and (5)" (lists joined by commas, "and", "or", "&", "to" or a
dash), and a bare "(3)" not right after a letter, digit or bracket (so "f(3)" is not
one) and not at the start of a paragraph or list item (there it numbers the paragraph:
"(3) Measure the speed"). A bare "(3)" must also come right after a word that
introduces an equation ("from", "using", "into", "by", "in", "with", "substituting",
"adding", "comparing" and the like, `_BARE_CUE`), or be joined to such a reference
("using (1) and (2)", "substituting (3) into (4)"): procedure steps, parts of a
question and enumerations print "(2)" too ("Repeat steps (2) and (3)", "Answer question
(3)", "three parts: (1) measure ..."). On four real lab manuals (393 pages) the one bare
reference made before this rule, "Repeat (2)", was a step, not equation (2). The targets (`crossref_targets`) are figures and tables by the number at the start
of their caption, and display equations by every number in their `equation_label` ("(2)
(3)" on an `align` gives both). A reference links (`kind="crossref"`) only when exactly
one target has its number; a bare "(3)" only when an equation prints exactly "(3)"
beside it (an equation numbered "2.4" without brackets is not named by "(2.4)"). No link
when two targets share a number, when none has it, when it would overlap a PDF link or
an inline equation, or at the very start of a paragraph that reads like an unattached
caption ("Figure 2: ..."). The same table holds the other numbered things: a heading that
starts with "3.2" is "Section 3.2", one that starts "Chapter 4" is "Chapter 4", one that
starts "Appendix A" (or "B.2") is "Appendix A" ("Appendix B.2": `APPENDIX_REF` finds "Appendix A",
"Appendix B.2", "Appendix 1" and "Appendices A and B"; the letter is a capital, so "an appendix a
reader" is not one), and a
paragraph or heading that starts "Theorem 2.1." (or "(name)." or ":" after the number) is
that theorem; Lemma, Proposition, Corollary, Definition and "Example" (capital E, so "for
example 3" is not one) work the same. A reference is never linked to the element it is in.
`typed_addresses` makes "typed" links of the web and email addresses in the text that no PDF
link covers.

hyperref links only the number of `Figure~\ref{fig}` or `\eqref{eq}`; such a PDF link is
widened to the whole reference ("Figure 1", "Equation (1)") so the link text says what
it points at, keeping the PDF's own target (`widen_internal`).

### Language

The document's language (`DocumentResult.lang`, `lang_source`):

1. the language chosen at upload (`ConversionOptions.language`, from `Job.language`):
   source "user", no notice. It is not part of a step's key (`exclude=True`), as no model
   request reads the language;
2. else the body text (`language.document_language`): source "detected" and the notice
   `lang_detected`, or "pdf" when the catalog `/Lang` names the same language;
3. else the catalog `/Lang`, when it is a tag whose language is a real two- or
   three-letter code (`language.document_tag`; "x-none" and "und" do not count) and its
   script fits the text (`language.fits_text`): source "pdf". It is below the text because
   Word writes `en-US` into files in other languages;
4. else "en": source "default" and the notice `lang_assumed`.

No language holding 60 % of the letters adds `lang_mixed`. `lang_assumed` and
`lang_mixed` are required checks (`web.states`): the job is not "Ready to post" until the
owner confirms or changes the language on the job page
(`text_pass.set_document_language`, source "user"). `lang_detected` stays optional.

Detection (`pipeline/language.py`) counts letters by script (Chinese, Japanese and Korean
count like any other text) and classifies words only: inline equations, numbers,
single letters (mostly variables), symbols, styled math letters and code-like tokens
(anything with a digit, an underscore or a bracket in it) are left out. Kana, Hangul,
Hebrew, Greek and Thai fix the language by script; Han with no kana is Chinese. Latin,
Cyrillic and Arabic text goes to the detector, restricted to the languages written in that
script, and one table maps the labels with no voice ("ary", "arz") to the tag a voice
exists for ("ar"). The document needs at least 100 letters of such text (a letter of Chinese,
Japanese, Korean or Thai counts three, as it carries about that much) and a
probability of 0.90, the same for every language (an earlier rule asked 250 letters and
0.99 of any language other than English). Latin and the constructed languages
(Esperanto, Volapük) are never taken from detection alone: py3langid reads a
one-page list of English terms (reagents, element names, species) as Latin at 0.99,
and a wrong document language puts the whole file in the wrong voice, while the
default only asks for a check. A paragraph or list
item gets `Element.lang` when it has at least 40 characters of prose, at least 60 % of
its tokens are words, and the detector gives another language than the document's with
a probability of at least 0.98. Headings are never marked. Tried on about 230 pages of
English course material and reports (OpenStax pages, LaTeX notes and slides, the layout
fixtures, accessibility reports, a Federal Register notice, an arXiv paper, a 59-page
chapter): no passage was marked; the French quotation of the LaTeX fixture is. In an
86-page English report with Swedish sources, two reference entries with Swedish titles
were marked Swedish (one of them starts "Retrieved from" in English): a reference in
another language is read in that language's voice, which is usually what a reader wants.

The detector is py3langid 0.4 (BSD-3-Clause), chosen 2026-10-09 over two others:

| Detector | License | Installed size | Short academic sentences (24) |
|---|---|---|---|
| py3langid 0.4 (langid.py fork, naive Bayes on byte n-grams, 97 languages) | BSD-3-Clause | 4.5 MB model, plus NumPy | 24 right |
| lingua-py 2.2 (`lingua-language-detector`) | Apache-2.0 | about 290 MB of models | 23 right |
| fastText lid.176 | model CC BY-SA 3.0 | 126 MB (0.9 MB compressed) | not tried: a share-alike license on the model |

The test sentences were English with math ("Let f be continuous on [a, b]", "Office
hours: Tuesday and Thursday, 2 to 4 pm"), French, German, Spanish, Italian, Portuguese,
Dutch and a Latin tag. py3langid loads in about 0.3 s (once per process) and takes well
under a millisecond per paragraph. It unpacks its model through a temporary file; when
that cannot be written (a full temporary directory) the model is unpacked in memory
instead. If the detector still cannot run, the document gets the default language with
`lang_assumed` and no passage is marked, and the rest of the pass (links,
cross-references, color runs) is kept: only the language depends on it.

### Color (FEATURE_COLOUR)

With the flag on, a paragraph or list item gets `Element.colour_runs`: maximal stretches
of placed characters printed in one run color, from the first to the last such
character, across an inline equation when the text on both sides has the color, minus
any linked text (a link is written in the link color), trimmed of spaces, and only with
a letter, a digit or math in them. A color is a run color unless:

- it is the body color: the commonest glyph color of the analyzed pages, when it reaches
  4.5:1 on white (a light body color is not one, so light body text is kept and made
  darker);
- it is near black (OKLCH lightness below 0.2, whatever its tint) or a readable grey
  (chroma below 0.035 and 4.5:1 or more on white): it reads as body text;
- it is near white (lightness 0.96 or more): it was printed on a dark fill the output
  does not have, so it becomes body text.

A light grey is a run (it is a contrast problem: the output darkens it); so is any real
color. Headings get no color runs. Running text that names a color ("in red", "shown
in blue", "highlighted in yellow", "the green curve", "red arrows") on or within two
paragraphs of colored text on the same page gets the optional notice
`colour_reference`.

With the flag off the color runs are still found (the same rules, from the glyphs already
read) and counted (`colour_runs` in the trace), but not stored or written: the text is in the
body color, and the job gets one notice for the whole document, `colour_used`, with the number
of colored passages as its count and no text of the document. It tells the instructor that the
converted file does not keep color, so a meaning that only color carried (answers in red) has
to be said in words. No color is named: the pass has no table of color names. `colour_reference`
is made only with the flag on.

### Bold, italic and fixed-pitch words (F-510, `CONTENT_RUNS`)

Each glyph the text pass reads carries its face's weight, slant and pitch (`Glyph.bold`,
`italic` and `mono`, set once per span by `glyphs.face_traits`: the tests `_Char` makes for
`Element.bold` and the mono share of a code line, the font's flags then its name; a face
in a math font has none, so a variable or a bold vector in CMMI or CMMIB is never emphasis).
The pass makes no second read: the page's glyphs are read once, on every page, for color,
links, strikes and these runs. For a paragraph or list item, `text_pass.style_runs` maps each
style onto the text the way it maps color (`labelled_runs`, `across_markers=False`, so a run
stops at an inline equation) and writes a `strong`, `emphasis` or `code` run for each
stretch, trimmed of spaces and only with a letter or a digit in it. Three rules keep it from
inventing emphasis:

- a style that covers 70% or more of the element's placed characters is the block's own
  style (a paragraph set in italic, a bold caption), and makes no run (`STYLE_SHARE`);
- one italic letter is not a run: it is a variable the equation path left in the text, and
  the letters it took are inline-equation markers, which are never placed on a glyph;
- a PDF with no italic face has no italic glyph, so it has no italic runs. Slant is not
  guessed from geometry.

Headings, program code (`preformatted`) and glossary entries get none; `Element.bold` stays the
whole-line evidence heading detection uses. Corpus counts (996 files) are in the changelog.
The Word and LaTeX readers write the same runs from what the source says, with the same 70% rule
(docs/output.md "From a Word or LaTeX source").

### Highlighted words (SW51c, `CONTENT_RUNS`)

A highlight is a run of kind `highlight`, written as `<mark>`, on a paragraph or list item.
Two marks make one, both read with the strikes in `layout.page_paint` (one read of the page's
drawings and annotations, no second pass):

- the quads of a Highlight annotation: every glyph whose middle is inside one is highlighted;
- a filled rectangle behind the glyphs (a visible fill, at most 36 pt tall and 600 pt wide, a
  single rectangle) whose color is a color of its own and whose shape is one line of text
  (`text_pass.highlighted_glyphs`). The color is lighter than 0.5 (OKLCH; a darker fill is a
  bar or reversed text), not near white (0.96 or more: the page's own or a faint tint) and not a
  grey (chroma below 0.035: the shade of a table header, a code block or a boxed theorem). The
  height is 0.6 to 1.5 times the size of the glyphs it holds, so a box with padding round a line
  or round several lines is not a highlight. Three or more rectangles of one color, top and
  bottom on a page are shading (a table's header row, a heat map) and none is a highlight
  (`text_pass.highlight_marks`).

Two limits, measured on 2026-11-22 and not built because no real document shows them (the 332
public files and the 25 corpus PDFs hold 928 and 116 pages with filled rectangles; none has a
highlight dropped as shading, the 6 pages that lose rectangles to the row rule are table shading
and a heat map): a real highlight drawn as three or more touching rectangles on one line (one per
style run) is dropped with the shading, and paint order is not read. The second does not make a
false highlight: a filled rectangle painted over text (after it) is opaque, so
`page_type`'s cover rule ("Hidden text and page types" below) leaves that text out of the page,
and a rectangle behind the text or a translucent one over it leaves the words and is a highlight.

The glyphs map onto the text the way struck glyphs do (`labelled_runs`, `across_markers=False`:
a run stops at an inline equation), trimmed of spaces and kept only with a letter or a digit in
them. Table cells, headings, program code and glossary entries get none, because only
paragraphs and list items take runs. The color of the mark is not kept: the page's own
stylesheet writes every `<mark>` the same way (docs/output.md "Highlighted text"). `CONTENT_RUNS`
off finds and counts them (`highlight_runs` in the trace) and writes none.

### Struck text (F-509, `CONTENT_RUNS`)

Text struck through on the page is kept and marked as a `deleted` run (`Element.runs`), so
"October 3 October 10" with the first date struck is not read as two live dates. The marks
come from `layout.page_paint`: every visible horizontal stroked line and every filled bar
no thicker than 3 pt (curves are skipped; a page too complex to read its drawings gives its
annotations only), and the quads of each StrikeOut annotation. A glyph is struck
(`text_pass.struck_glyphs`) when:

- a StrikeOut quad holds its center; or
- a rule crosses it between 25% and 65% of the glyph's height from its bottom, no thicker
  than 0.12 of its font size, and the rule crosses at least half of the glyphs of that row
  it touches. The glyphs whose vertical middle lies in the band of those it crosses are
  struck with them, so a rule drawn at x-height also covers the capitals and digits beside.

A rule under the text (an underline) or under a blank ("Name: ____") crosses no glyph in
that band and strikes nothing (`baseline_rules.classify`, below). Struck glyphs become runs the same way as color
(`labelled_runs`, across inline markers), kept only with a letter, a digit or math in them,
on paragraphs, list items and headings. Each element with runs gets the optional notice
`struck_text` (with its page; no required decision). Glyphs are read on every page.

Two checks beyond the band rule, both in `text_pass`. A drawn mark that would strike text must
also show on the page (`drawn_strikes`): a strip of the page along it is rendered (8 pixels to
the point) and 90% of its columns must be inked, since a vector figure can keep a legend
sample in its drawings that its clip hides, and a reader who sees no line sees no deleted
text. Only marks that cross glyphs are rendered. And an inline equation the page strikes
through (a majority of its glyphs struck) is a run that is its marker alone (`deleted_runs`).
A struck table cell and a struck display equation carry no runs (a cell has no offsets into
glyphs, an equation is read by a model), so the strike cannot be kept; the table or equation
gets the optional notice `struck_not_kept` (`text_pass.struck_not_kept`, with `CONTENT_RUNS`
on), which says the words read as plain text. An equation counts when most of its glyphs are
struck (a fraction bar or a line over a symbol also crosses glyphs); a table when a mark as
short as a cell (an annotation, or a drawn rule under 60% of the table's width) crosses two
letters or digits of the table's own size. On the tracked PDFs (332 files, 2,598 pages) the
first rule found 54 tables and equations, all but the 4 tables bars and borders, and the 4 were
borders and marks over small glyphs; with the size and width tests none is left, so the
rule is measured on generated pages only. `layout.page_paint` still lists marks that the
page clips away or covers (`layout.py`, `page_paint`: it reads `get_cdrawings()` without the
clip entries), which is why the text pass renders the ones that matter.

With `CONTENT_RUNS` off (the shadow rollout, the default until 2026-11-18) the runs are found and counted
(`TextPassResult.deleted_runs`, trace `deleted_runs` and `deleted_runs_written: 0`) but not
stored, and no notice is raised. The LaTeX path follows the same switch for its `\sout` runs.

### Annotation comments and attached files (F-541, F-362)

A reviewer's comments live in the PDF's annotations, not in its text, so a student who heard the
page heard the stale words alone. `text_pass.page_marks` reads each page's shown annotations
(not the Hidden or NoView ones) while the pass already has the file open: a sticky note, a caret,
a highlight, underline, squiggle, strike-out, box, circle, line, polygon or polyline with a
non-empty `/Contents`. A text box (FreeText) is not a note: its words are
already text on the page. A popup is the window of its parent's comment and is read once;
a file-attachment annotation's `/Contents` is the label of the file and is not a note. A page is
read for at most 500 comments and a comment for 10,000 characters. The box of an annotation is
read with the page turned upright (PyMuPDF gives it unreliably on a page with `/Rotate`) and then
turned into the display space the elements use.

`text_pass.comment_notes` writes each comment as a paragraph, "Note on the page: <comment>", after
the element whose text its box covers most (an annotation that covers none: the nearest element
beside it, a margin sticky note included, by overlap, then vertical, then horizontal distance).
A reply (`/IRT`) stands after the comment it answers. A caret that the file calls a replacement
(`/IT /Replace`, or a reply to a strike-out) says "replace with <text>", and stands after the struck
words it replaces; any other caret is the text alone. A highlight or underline without a comment
adds nothing and no new kind of run exists. The paragraph is made by the text pass, after the
passage languages, so no model request changes (the context texts were cut before). Ids are one past the
highest on the page and only `order` is renumbered. Layout keeps no `notes` field (WS16.3), so a
note is a paragraph whose words an edit can change like any other. The notice `annotations_read`
(required, the whole document, a count) says how many were read, and holds the job at "Ready".

An attached file (an annotation on a page or an entry of the file's embedded files) is not
converted. The optional notice `attachments_not_converted` carries the count and, in
`Notice.names`, each file's name (cut to 120 characters, 20 names): a name is document content, so it
is shown to the owner (the job page line, "The files: “rubric.txt”") and kept in the record, and it
is never logged. `Notice.names` is empty for every other notice and is left out of a stored notice
then. Counts by annotation type are not stored.

The drawings of annotations are not the page's art. A sticky note's balloon, a caret, a pushpin, a
highlight's fill and a box are painted after the page's own content, so they are the last of
`page.get_cdrawings()`. `page_type.without_annotation_art` takes, from the end of that list, those
that lie in the box of a shown annotation of a kind in `ANNOTATION_ART` (`ICON_ANNOTS`, the marks on text, the shapes
and lines, FreeText), and drops them. It is applied where the page's drawings are first read:
`layout/document.py` `_read_page` (the one list that figure, handwriting and ink detection use), and
the slide-mark pass of the same file. The hidden-text check still sees every drawing
(`classify(covers=...)`): a box drawn over a line is a fake redaction. A pen stroke (Ink) is kept: it is
handwriting. The budgeted ink probe (`page_type._probe_ink`) and `ink_strokes.extract_ink` (the strokes sent to
the reader of a handwritten page) read the list through the same function. Known limit: a drawing of
the page itself that was painted last and lies inside such a box is dropped with them.

### Rules near a baseline, blanks, boxes and form fields (SW36, SW39)

One function answers "what is this rule near a baseline": `pipeline/baseline_rules.classify`
takes the rule's box and the ink (box, size, baseline) of the row it sits in and says
`strike` (it crosses the glyphs between 25% and 65% of their height, see above), `underline`
(it lies 1 pt above to 4 pt below the baseline and covers at least half of the glyph run over
it), `blank` (it lies there with nothing over it, at least 12 pt wide, and a word of the row
within two font sizes of it), or `rule` (a table or border rule, anything else). The struck
text test (`text_pass.struck_glyphs`) and the blank test (`layout._mark_blanks`) both ask it,
so a rule has one meaning. A rule that crosses a vertical edge of another drawn path (the plate
of a battery on its wire, within 2.5 pt of the edge: boxes leave out the stroke width) is part
of a drawing and is not asked, and neither is a rule with text over or under it in any row (a
fraction, division or radical bar, an underbrace): that rule is part of the math.
`layout._mark_blanks` runs in `_prepare_rows`, on the fragments of each line before
`_merge_baselines` joins them, so a blank at the start of a line is in the line when it is
made and cannot split a paragraph.

Three things stand beside a rule on its baseline and are not text of its row, so the rule is
no blank after them (measured over 277 development files: 82 of 203 drawn blanks were one of
these, in 17 papers, and none of the 117 blanks of the 12 forms and worksheets changed):

- a row that holds only the pieces of a big math sign (`_sign_pieces`: a radical sign, the
  corners and middle of a brace, any glyph of a math symbol or extension font): the rule after
  it is the sign's own bar;
- a word across the gutter of a two-column page: a rule in one column is no blank of a line in
  the other;
- a word across an upright line or the side of a frame that spans the rule's height
  (`_parted`): a tick label, a plot's axis, then a legend's sample line.

`_parted` looks only at the nearest word on each side within two font sizes, and the rule
stays a blank when a word on either side has no wall between. Still read as blanks (4 in
those files): a fraction bar or an overline whose numerator and denominator sit in other rows.

A blank is written into the row as five underscores at the rule's place (a space on each side
where the text has none), so a drawn blank and a typed run of three or more underscores read
the same. The text pass (`text_pass.token_runs`) marks every run of three or more underscores
as a `blank` run and each of U+2610, U+2611 and U+2612 as a `checkbox` run, from the text alone
(no flag: nothing in them depends on a model or a setting). A rule that fills no gap in a row,
and an underline, change nothing.

A fillable form (AcroForm) is read the same way. `layout.take_form_fields` reads each text,
choice, check box, radio button and signature field of a page and takes it off the open copy
of the page (the file is not written to), so what a field draws (its value, a tick, a frame,
a picture of a signature) is in no later read of the page's text, drawings or images and
cannot be a stray paragraph or a figure; push buttons stay. `layout._field_rows` puts each
field back as a row on the baseline and in the size of the line it stands in, which merges
with its label: an empty text field is a blank, a filled one or a choice field is its value, a
check box or radio button is U+2610 or U+2611 for its state, a signature field is a blank
followed by "(signature field)". The text pass reads the fields again for the value (the
first place in the element, under the field, where its text stands, is a `blank` run) and
raises one document notice `form_fields` ("Not handled in this file") with the number of
fields. Fields are written as described text, not as live inputs: the converted file cannot be
filled in. Not done: a radio group as one list of its options (each button is its own box),
a choice field's other options, and the field's own label from its tooltip (the label is the
text beside it).

### Source facts

`text_pass.link_count(doc, pages)` counts the link annotations of any kind on the given
pages of an open PyMuPDF document (hold layout's lock): `SourceFacts.links`. The pass
also returns the same count for the analyzed pages as `TextPassResult.annotations`.

## The page math hook (`pipeline/page_math_reconcile.py`, `FEATURE_PAGE_MATH`, off)

`analyze(pdf, page_math=hook)` calls `hook.on_page(number, rows, ordered, rules)` once per page
after the fragments are resolved and before `_equation_regions`, and `hook.after_regions` after it.
With a collecting hook (no plan) layout is unchanged and the hook records each page's rows as
the page pass is shown them: `L0, L1, ...` in reading order, rotated rows left out, each row's
text with control characters dropped and blanks collapsed, one character of that text for one
character of the row (`page_math.PageRow`). With a verified plan the hook cuts rows at the
anchors, makes the block spans `_Region`s (`forced`, with their rules and labels), marks inline
spans on their rows (`_Row.page_inline`, which `_paragraph_pieces` turns into inline
equations) and what the pass called a look-alike (`_Row.page_nonmath`, which drops layout's runs
inside it), and the rest of the page goes through the rules above unchanged. `Element.page_latex`
and `Element.page_status` ("span": the pass placed it; "layout": only layout found it) carry the
result to the equation step. A block span that would split a layout region or leave a piece layout
reads as math in a shared row is refused (`HookStats.refused`); a span whose LaTeX and quoted text
disagree is dropped (`inconsistent`); an exception in the hook leaves the page as layout has it.
The design and its measurements: [ADR 0006](decisions/0006-page-math-pass.md),
[eval-page-math.md](eval-page-math.md).

## Errors

`LayoutError.code` is a category, safe to log; messages never contain document text.

| Code | When |
|---|---|
| `unreadable` | empty input, not a PDF (MuPDF would otherwise open a PNG as a one-page document), damaged content, or a page that crashes or hangs MuPDF in the probe child process |
| `encrypted` | the PDF needs a user password (owner-only restrictions are fine) |
| `no_pages` | the file has no pages, or `pages=[]` |
| `page_out_of_range` | a requested page index is negative or past the end |
| `too_many_pages` | more pages requested (or in the file, with `pages=None`) than `max_pages` |
| `content_too_large` | the object streams, a selected page or the selected pages together inflate past the content budget (`core.content_budget`; docs/security.md, "Content budget"), checked before any page is read |
| `too_complex` | a page needs more time or memory to read than a conversion has |
| `image_too_large` | decoding the pictures took longer than a job may spend (`core.images.DECODE_BUDGET_S`) |

`crop` raises `ValueError` for bad arguments (`dpi <= 0`, `pad < 0`, `max_side_px < 16`) and
for an element whose page or box is not in the PDF.

## Untrusted PDFs: the probe process and MuPDF's messages

MuPDF is C code parsing bytes from the internet. Some damaged PDFs open and parse without
an error but make MuPDF abort the whole process while rendering (a glibc double free in
`fz_new_display_list_from_page`; fuzzing the fixtures found dozens with PyMuPDF 1.28.2,
one is committed as `tests/fixtures/layout/damaged_render_abort.pdf`). In the worker
thread inside the web process that would take the server down, and again on each retry
of the job. So before a page is extracted or rendered in this process, a child process
(`sys.executable -I -c ...`, PDF on stdin) extracts it the same way and renders it once at
72 dpi (`_probe_pages`), at most `PROBE_MAX_SIDE_PX` (2048) pixels on the long side, with
its address space capped at `PROBE_MEMORY_BYTES` (768 MB, plus twice the PDF's size):
MuPDF accepts page boxes far past the PDF maximum, and an 18000 pt page at 72 dpi was a
1 GB pixmap. The display list, where the crash is, is built whatever the zoom; a render
that runs out of memory under the cap is a Python error there and ignored like the
others. A child that dies (any non-zero exit) or runs longer than
`PROBE_TIMEOUT_S` (60 s) fails that probe: `LayoutError("unreadable")` or `("too_complex")`, which
fails the file only when no page can be drawn (next paragraph); Python exceptions in
the child are ignored, because only crashes and hangs matter there. `analyze` probes the
selected pages; `crop` probes a page `analyze` did not cover. Which pages passed is kept
by document hash (hashes only, 64 documents), so repeated calls do not probe again. The
child costs about 0.1 s per call (interpreter start-up) plus the trial render. Its output
is discarded: MuPDF's messages quote the PDF.

MuPDF prints parser errors and warnings to stdout, quoting content-stream tokens and
resource names (`unknown keyword: 'JaneDoe...'`), and stdout goes to the service logs.
Layout switches both off (`TOOLS.mupdf_display_errors(False)`, `..._warnings(False)`) on
every call, because the switch is process-wide and other code could turn it back on, and
empties MuPDF's in-memory warning store after every `analyze` and `crop`.

**A page that cannot be drawn** is that page's problem, not the document's
(`access.probe_failures`). `analyze` first probes all the selected pages in one child, as
before. If that fails, the pages are probed again in halves, and each half that fails in
halves again, down to single pages; a page whose own probe fails is a page that cannot be
drawn. Both halves are always probed, so a failure that comes and goes (a time limit on a busy
machine) is not pinned on a page that is not at fault: if the halves pass although the whole
did not, the file fails as it did. Such a page is not read at all (the probe covers the text
read as well as the drawing, so nothing of the page is trusted) and the conversion does not
build it again: no step, crop, tag, link or image reads it, the page-number pass skips it (its
number is the one its neighbors' printed numbers give it, if they bracket it; the PDF's own
label for it is not read), and its size for the page list comes from the page tree, the size
`Page.rect` gives (`document._unloaded_size`): the MediaBox cut to the CropBox where they overlap
(both and Rotate inherited from the Pages nodes above), times the page's own UserUnit, turned by
Rotate, each read through an object of its own where the tree refers to one ("12 0 R", for a
whole box or one of its numbers); US Letter where the MediaBox is missing or empty, where the
CropBox leaves nothing of it, or if the tree cannot be read; a UserUnit of 0 or less is read as
1, where MuPDF makes the page empty; a page saved sideways that layout would turn upright is not
turned). The web
process has no PyMuPDF call of its own: the upload check and the review screen's page pictures
run in child processes (`web/pdf_probe.py`), which is where a page that kills MuPDF may be
opened. It is kept with the reason
`page_not_rendered`: a figure for the page with no picture, a required decision (one decision for
all such pages, like the other kept pages), the notice `page_not_rendered`, and in the student file
the page break, the label "Not converted." and "This page could not be shown." The log names the
page and the probe's error code, never anything of the page. The other pages are read as
if the file had no such page. The search is bounded: after the probe of all the pages it starts at
most `PROBE_SEARCH_MAX` (40) probes and runs at most `PROBE_SEARCH_S` (300 s), each probe's
own time limit cut to what is left of them (a probe the clock ends is not taken for a failing
page); past either, and when every page fails or the halves find nothing, the file fails with the first probe's error, as
before. Two probes for each halving, so 2 * ceil(log2 n) find one bad page of n (14 for 100
pages), 40 find two bad pages in 100 and every bad page of a file of up to 20 pages. A
file of 100 pages with every page bad costs 41 child processes, not 199, and then fails. Cost on
100 plain pages with one page whose child dies: 15 probes in 2.7 s (the probe of all of them
alone is 0.7 s); with three such pages, 33 probes in 5 s. A page that hangs costs its whole time limit (60 s plus 2 s for each page after the first in
the probe) each time it is in a probe that is run, which is why the search has a clock of its
own: in a file of 5 pages, one such page is found in about four timeouts of 60 s or less; in a
file of 100 pages the first timeout is 258 s and the halves (158, 108, 84 s ...) do not fit in
the clock, so the file fails with the first probe's error after the search has used its 300 s
(258 s for the first probe and 300 s for the search: 558 s at most; a probe that could start at
299 s used to run its 158 s on top, 716 s). A page that makes MuPDF abort is quick: the child dies at once.

The probe also guards table detection: `find_tables` renders the page for its layout
model, where the known MuPDF aborts are, and it only ever runs on pages the probe passed.
The 2026-10-08 evaluation ran 837 malformed files through the probe and then the table
detector: the 41 that made MuPDF abort were all stopped by the probe, and none that
passed it crashed the detector.
`make fuzz-tables` repeats that check on mutants of the fixtures; run it before any
PyMuPDF, pymupdf-layout or onnxruntime upgrade ([tables.md](tables.md), "Upgrade check").

What the probe does not cover: the analysis itself (Python code in this process) has no
time limit. Fragment resolution, run building, column detection and script attachment are
indexed so a crafted page of thousands of rows stays near-linear (a 16-column page of
1,760 tiny numeric rows took 66 s before this and takes about 0.3 s now; 28,000 rows take
about 5 s), but a job of 30 such pages still holds the module lock for minutes. **For
WP4/WP5:** full isolation (running `convert_pdf` for a job in a child process with a job
time limit) belongs to the worker, which owns the job lifecycle.

## Cropping, the cache and threads

PyMuPDF is not thread-safe, so every PyMuPDF call in `layout/` runs under one module lock (`access._LOCK`);
concurrent callers are safe but serialised (crop on one thread; a thread pool only helps the
model calls). `analyze` opens the document for that call only and closes it. `crop` caches
opened documents (4 at most), keyed by the identity of the bytes object and, failing that,
by its SHA-256, so cropping every element of a PDF parses it once. The cache holds user
content, so an entry unused for `DOCUMENT_CACHE_TTL_S` (120 s) is closed by a daemon timer
even if nobody calls `release`: an idle worker does not keep the last jobs' PDFs past the
retention purge. `pipeline.run.convert_pdf` also calls `layout.release(pdf_bytes)` in a
`finally` when the job ends.

Crops render at `dpi` on a white background; when the long side would exceed `max_side_px`,
the zoom drops so it fits (one pixel of headroom absorbs MuPDF's rounding).

Speed (this machine, 2026-10-08): a 19-page DocBook spec analyzes in 0.3 s plus the probe;
a page with a 4,000-marker scatter plot takes about 1 s, almost all of it in MuPDF's path
extraction (`get_cdrawings`, which returns plain tuples; `get_drawings` and per-point
PyMuPDF objects were three times slower). A crop takes about 0.1 s.

## Known failure modes

- **Handwritten and scanned pages** are not converted (no OCR, no handwriting
  recognition): they are kept as page images with a decision, and a PDF made only of
  them is refused at upload. What the page types miss is listed in "Hidden text and page
  types" above.
- **Synthetic italics.** When a PDF fakes italics by slanting an upright font (Chromium does
  this when the italic face is missing), the font name and flags say upright, so an italic
  `x = 5` typed as text is not recognized as math unless a math font or symbol is involved.
- **Plain-text math in the body font** (`v = d/t` typed in Word without the equation
  editor) is found inside paragraphs only through the relation rule; `d/t` alone is not.
- **A display equation line with words** (`x = 1 if y > 0`) becomes a short paragraph with
  inline math, not one display equation.
- **A CFF math font in a Chromium print.** Chromium embeds a CFF (OpenType) math font such
  as Latin Modern Math as unnamed Type3 glyphs, so the font name gives no math signal. A
  display line such as `v(t) = −gt (time-dependent)` then reads as prose with inline math
  and is merged into the paragraphs around it. A TrueType math font that names itself
  (Noto Sans Math) is read correctly. Pinned by the strict xfail
  `tests/test_sign_check.py::test_a_display_equation_in_a_type3_maths_font_stays_display`.
- **A wrapped sentence whose last line is only math** is read as a display equation when
  that line is indented or set off by extra space; at the paragraph's margin and line
  pitch it is inline.
- **Very short documents** learn the "typical line pitch" from very few lines; with one
  two-line paragraph the paragraph gap itself becomes the typical pitch and separate
  paragraphs can merge.
- **Math inside headings and captions** is text: a heading's, and a caption's plain text,
  has math-italic letters folded to plain letters (`readable_math_letters`), but only
  paragraphs (and a figure's caption that holds math) get inline equation elements, so a
  fraction or a script in a heading is not typeset.
- **Paragraphs split across pages** are joined into one element when they pass the tests
  in "Paragraph text" above; otherwise (a column break from one page to the next, a
  paragraph that starts or ends in math, a different size or indent) they stay two. The
  joined element has the first part's page and box only.
- **Inline math that wraps across lines** is one equation when a relation sign, or a plus,
  minus or product after a longer expression, sits at the break; other wraps (a break
  inside a fraction, no operator at the break) still become two.
- **A lone bold letter** in running text (`for some nonzero vector x`, `\mathbf{x}` in
  CMBX) is not recognized as math: one upright letter with nothing around it is too
  ambiguous.
- **Math written as text** (OpenStax writes `d₀`, `t = 0 s`, `y = mx + b` in HTML, not
  MathML) is found and converted like any inline math; the gold set has no MathML for
  it, so it counts against detection precision in the document eval (reported as
  `prose_math` when the prediction shows a formula: a relation with operands, a variable
  with an index or script, an italic variable with an operator; a bare quantity such as
  `-49 m` or a lone symbol is `other`).
- **Lists:** a marker on a line of its own above its text (OpenStax prints exercise
  numbers that way) is not an item: the number stays at the start of a paragraph. Bullets
  that are raster images are not recognized. A list that the PDF numbers with a gap, or
  in brackets, is reproduced with printed markers. A single item with a letter or roman
  marker and no sibling stays a paragraph.
- **Equation numbers** to the left of an equation are found only in brackets (`(2.4)`);
  a number that is the only thing on a line between two equations attaches to the
  nearer one.
- **Tables the detector does not propose** keep the old path: their cells become
  paragraphs (read left to right, row by row), and a table of numbers set without any
  rules can become a display equation. So does a grid of prose, on purpose (a two-column
  table with long cells can still look like text columns). What is missed, and why:
  [tables.md](tables.md), "Known misses".
- **Equations drawn entirely as vector outlines** (some MathJax SVG prints) have no text;
  they come out as figures for the figure model (a short one whose glyphs mostly pass
  the pen-stroke tests, such as a small fraction, is cut out as handwriting instead: a
  decision, not a loss). One drawn inside a line of text is held by the paragraph and
  lost.
- **Drop caps** (a huge first letter) become a one-letter paragraph.
- **Hyphenated compounds across lines**: `well-` / `known` keeps its hyphen (the word
  list, then the document's own spelling, "Paragraph text" above). A compound of technical
  words that is neither in the list nor written whole or hyphenated elsewhere in the
  document (`eigen-` / `vector`, written nowhere else) still loses its hyphen.
- **Plain body-size numbered headings with a bare integer** (`3 Forces` in body text,
  not bold) are headings only in a sequence (`3 Forces`, `4 Energy` with text between);
  one alone, a sequence with a gap, and any run with no paragraph between are paragraphs.
  They are found when bold (as weak evidence) or in a larger font.
- **Bold table header cells** (`Time`, `Distance`) of a table the detector missed can
  still become weak headings (an optional note; a group decision if the same header
  repeats); numeric cells no longer do. In a detected table they are header cells.
- **Bold run-in labels**: the six words of the narrow rule, repeated, are bold text; any
  other label ("Example", "Strategy", a single "Solution") is still a weak heading; see
  "Run-in labels" above.
- **Repeated margin logos drawn as vectors** are recognized only from drawings that lie
  inside the header/footer band; a logo that extends into the body is a figure on every
  page.
- **A single prose line on a picture** (one subtitle on a slide backdrop) is absorbed as
  the figure's label, not read in the flow.
- **Margin text in the body font that changes per page** (`Problem 3`, not bold, in the
  top band) is still dropped as a running header; only larger or bold text is kept.
- Numbers are tuned on Chromium, PyMuPDF-generated and DocBook/groff PDFs. Real course PDFs
  (LaTeX, Word, PowerPoint exports) need a check before the thresholds are trusted; record
  what breaks here.

## Tests and fixtures

- `tests/test_layout_analyze.py` builds small PDFs with PyMuPDF for each rule (headings,
  hyphenation, fraction bars, inline math, figures, columns, headers, errors).
- `tests/test_layout_fixtures.py` checks three realistic PDFs printed by Chromium from HTML
  in `tests/fixtures/layout/` (lecture notes with MathML and a running header, a two-column
  handout, a worked example with a raster figure). The `browser`-marked test re-prints them
  and checks the structure is unchanged. Regenerate with
  `uv run python tests/fixtures/layout/make_fixtures.py` (needs `uv run playwright install
  chromium`), and commit new PDFs only when the fixture tests pass on them.
- `tests/test_layout_crop.py` covers crop sizes, rotation, the cache and its expiry,
  threads, and the probe (a child that crashes or hangs, the damaged fixture);
  `tests/test_layout_headings.py` covers levels, outline repair and confidence.
- `tests/test_layout_review.py` pins the fixes from the 2026-10-08 review: numbered-line
  headings, equation numbers, two-column order, Symbol bullets, logos, watermarks, text
  over images and panels, fake markers, MuPDF's stdout, margin headings, and the cost of
  a dense fragment page.
- `tests/test_layout_structure_fixes.py` pins the 2026-11-02 structure fixes with PDFs drawn
  in the test: bookmarks (levels, text, consistency, a tag winning), plain numbered lines
  in sequence, run-in labels, math-italic in a heading, paragraphs over a page break (a
  footnote and a running header between, a hyphen at the break, each reason not to join),
  the document's spelling of compounds and inline math that wraps.
- `tests/test_layout_slides.py` (the slide rules on made-up pages, the four Beamer decks and
  `slides_steps.pdf`: steps dropped or kept, titles, headline and footline, columns, no line
  of the last step lost, non-slide files read exactly as before, ligatures, UTF-16 page
  labels, and the pipeline making no call for a dropped page).
- `tests/test_ink_strokes.py` draws ink in each export style (stroked paths, filled shapes per
  pen sample, filled outlines) on ruled, squared and dotted paper and in pastel ink, and
  checks the recovered strokes against the drawn paths, in pen order; it also reads the two
  ink fixtures (`ios_notes.pdf`, `ink_no_layer.pdf`) and the seeded writer that made them.
- `tests/test_layout_lists.py` (bullets, ordered markers, siblings, nesting, false lists),
  `tests/test_layout_equations.py` (display-equation regions and equation numbers) and
  `tests/test_layout_fragments.py` (inline fragments and the punctuation after inline
  math) pin the layout v2 fixes from the eval pages and the smoke test.
- `tests/test_page_types.py`, `tests/test_page_types_pipeline.py` and
  `tests/test_page_types_browser.py` (`browser` marker) cover hidden text and page types
  on synthetic PDFs from `tests/fixtures/handwriting/make_fixtures.py` (seeded ink in the
  shapes note apps export, made-up text in the layers iPad notes apps and OCR programs
  write, the design review's failing cases rebuilt; each under 100 KB, no real
  handwriting or scan): each fixture's kind and what happens to it, which spans are
  hidden, covered text on a turned page, the refusal codes, the time budget, that every
  committed born-digital fixture has no hidden text and no kept page, an iOS-style page
  never giving a text element and always a decision, one decision per reason, the
  upload refused before any quota, the group decision on both review screens, axe and
  320 px reflow. The routing review's cases (2026-10-09) are rebuilt there too: fake
  redactions drawn with annotation tools, clip text with nothing painted, text under a
  shading or a thick stroke, a scan with a CropBox, a timing diagram, machine-drawn
  curves under a title, a hidden layer over machine drawings, rejected writing under a
  short and a long title (and with the ink budget run out), a drawn page border, a
  full-page plot and a slide's chart, blank template pages, and a title slide on a photo.
- `tests/test_layout_silent_loss.py` rebuilds the silent-loss sweep's cases (2026-10-12)
  small: boxes joined by plain lines, a step plot of separate lines, math drawn as
  outlines, an icon, a benzene ring, a number line, hand check marks, crosses and one-stroke
  marks, strokes over an equation, notes in the header and footer bands, white drawings
  and writing on a dark slide, pictures with text on them, full-page pictures under a
  line of text, small-pixel pictures drawn large, chart labels in the bands, a hidden
  layer over a diagram with no picture; each must end in an element or a kept page.
  Its controls pin what must still add nothing: boxes and panels round text, rules that
  do not meet end to end, a repeated footer logo, a plain or gradient page background,
  a spacer, a running footer and page numbers, white paint on the white page. The
  second final verify's cases (2026-10-12) are committed fixtures built by
  `make_fixtures.py` (`build_edge_cases`): `full_page_plots.pdf` (one-curve plots
  filling the page, framed or not, axes as two lines or one L-shaped path: the axes,
  tick labels and axis title are the figure's), `open_axes.pdf` (axes drawn as one path
  of two separate lines), `timelines.pdf` (a timeline across the top of a page as one
  path and as separate lines, and a running header over a rule, which adds nothing) and
  `marks_over_text.pdf` (a check mark, a cross and a slash over typed lines, cut out; an
  underline, a box round a word and a cancel stroke, still part of the text); with a
  frame of four lines and a header rule with end marks as controls.
- `tests/test_layout_drawings.py` rebuilds the drawings sweep's cases (2026-10-13)
  small: slide and letter plots with a title, a note or a legend inside their axes, a
  title slide's L-shaped accent and wave (no figure swallows its title), a scatter plot
  with a legend, outlined bars on a baseline, a circuit with labels inside its loop,
  flowcharts of filled boxes with open arrows and of boxes drawn line by line, a timing
  diagram, a box plot, Fischer projections, bond lines, a one-line circuit of boxes and
  wires, electron dots, an answer circled in red, check marks over an equation and drawn as a
  filled ribbon, comb fields on an exam cover, and icons round a glyph. Its controls:
  a worked example's frames with a grid of cell borders, a box of four lines whose
  corners do not quite meet, blanks to fill in, a fraction bar, dot leaders, a circle
  round a step number in the text's color, a red underline, a yellow highlight, and a
  labeled scale.
- `tests/test_tables_detect.py` and `tests/test_tables_fixtures.py` cover table
  detection and how layout claims tables, on generated pages, the LaTeX
  `latex/tables.tex` (Tectonic) and a Word-style page exported by LibreOffice
  (`tests/fixtures/tables/`); see [tables.md](tables.md).
- `tests/test_layout_latex.py` checks four documents written for the tests and compiled
  by Tectonic, in `tests/fixtures/layout/latex/`: article notes (numbered equations,
  `align`, limits, inline integrals, nested itemize/enumerate, a TikZ figure), a problem
  set ((a)/A./(i) lists, a three-row derivation, `2.1`-style numbers), beamer slides
  (triangle bullets, sans-serif math, a captioned figure) and a two-column handout
  (matrices, bold vectors, `\neq`, a numbered projection). The `.tex` files are the
  source; rebuild with `uv run python tests/fixtures/layout/latex/make_pdfs.py --tectonic
  PATH` (deterministic, byte-identical with the same Tectonic version; PDFs must stay
  under 300 KB).
- `tests/test_layout_beamer_math.py` checks four small Beamer decks
  (`latex/beamer_*.tex`: frame titles, itemize with inline math, display equations and
  `align`, `\pause` and `\item<n->` overlays, two columns, a table with math, slide
  numbers, a theme with header and footer bars) against `beamer_*.gold.json`, the frames
  and equations read from the `.tex` (`latex/make_beamer_gold.py`, using
  `gold/arxiv_tex.py`). Slide numbers and footer bars are dropped and display equations
  are found; what still fails (repeated overlay pages, the overlay frame title dropped as
  a running header, a theme header bar kept as content, two columns read out of order,
  table-cell math without latex) is a strict xfail with its reason.
- The text pass: `tests/test_text_pass.py` (alignment with inline markers, link
  rectangles, unsafe schemes, destinations, cross-reference edge cases, color rules,
  the language and its change, on glyphs built in the test), `tests/test_text_language.py`
  (detection on short and math-heavy text, tags), `tests/test_text_contrast.py`,
  `tests/test_text_inline.py` (the HTML), and `tests/test_text_latex.py` on
  `links_language_colour.tex` in the LaTeX fixtures folder (hyperref links, `\ref`
  cross-references, a French quotation, colored text) and on PDFs built in the test
  (rotated pages, a named destination, `/Lang`); its `browser` tests run axe-core on the
  converted file and check that a long link wraps at 320 px.
