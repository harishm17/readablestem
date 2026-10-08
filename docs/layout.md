# Layout analysis (`pipeline/layout.py`, `pipeline/heading_step.py`)

Step 1 of the conversion: a PDF becomes a list of `Element`s (headings, paragraphs, display
and inline equations, figures) in reading order, with no model call. Everything later
(equation transcription, alt text, HTML assembly) works from these elements and from crops
of their boxes. The code is deterministic: the same PDF always gives the same elements.

## Contract

| Function | What it returns |
|---|---|
| `analyze(pdf_bytes, *, pages=None, max_pages=None) -> Layout` | elements of the requested 0-based pages (all by default), `page_count`, `pages`, `page_sizes`, `title`, `lang` |
| `crop(pdf_bytes, element, *, dpi=200, pad=2.0, max_side_px=2048) -> bytes` | PNG of the element box grown by `pad` points, clipped to the page, long side capped |
| `release(pdf_bytes=None)` | drops one cached document (or all); call it in a `finally` when a job finishes |
| `heading_levels(elements) -> dict[id, level]` | raw outline level per heading (before repair) |
| `heading_step.heading_fixes(elements) -> list[Fix]` | one `HeadingFix` per heading, outline repaired, with checks and confidence |

Element conventions:

- **Ids** are `p{page+1}-e{index}`, `index` counting from 0 per page in reading order.
  An inline equation gets the id right after its paragraph (`p1-e3` paragraph, `p1-e4` and
  `p1-e5` its inline equations, `p1-e6` the next block).
- **`order`** is the position in the whole document (0, 1, 2, ... across pages).
- **Inline equations** have `display="inline"` and `parent_id` = the paragraph id; the
  paragraph `text` contains `core.types.inline_marker(id)` (`⟦p1-e4⟧`) where the maths was.
  Display equations have `display="block"` and no parent.
- **Boxes** are PDF points in display space: the page's `/Rotate` is applied, so a
  landscape page reports `(792, 612)` in `page_sizes` and `crop` uses the boxes as they are.
  The origin is the top-left of the CropBox; content outside it is ignored and figure boxes
  are clipped to the page.
- **`reasons`** are short content-free evidence strings (`"math font"`, `"fraction bar"`,
  `"larger font"`, `"raster image"`, ...). They never contain document text.
- **Text** is copied from the PDF with whitespace collapsed and invisible characters
  removed. The inline-marker brackets `⟦` `⟧` (U+27E6/U+27E7) are replaced by the
  look-alikes `〚` `〛` (U+301A/U+301B) in every piece of document text, so a PDF cannot
  fake a marker: every marker in an element's `text` is a real inline child of it. Word's
  private-use Symbol bullet (U+F0B7) becomes `•`.
- Figures carry the nearby `"Figure N ..."` text in `caption` and any labels drawn inside
  or right next to them (axis titles, tick labels) in `text`.

## How a page is read

1. **Characters.** PyMuPDF's raw text dictionary with `TEXT_ACCURATE_BBOXES`, so glyph boxes
   are tight. Without it, a fraction's numerator and denominator boxes overlap the main
   line and cannot be told apart. Ligatures are expanded.
2. **Running headers, footers and other decoration.** A line whose centre is in the top or
   bottom 7 % of the page is dropped when its text, lower-cased with digits replaced by
   `#`, appears in the same band on at least half of the reference pages (and at least
   two), unless it stands out and changes: a line at least 1.15 x body size or bold whose
   exact text differs between pages (`Problem 3 (15 points)` at the top of each page) is
   content and stays. A fixed text (`PHYS 101 Lecture Notes`) goes whatever its size. The
   reference pages are the analysed pages plus their neighbours, up to 24 pages, so
   analysing one page of a long PDF still recognises its header. Bare page numbers (`7`,
   `Page 3 of 9`, `iv`, `- 12 -`) in the band are always dropped. Figures in the band
   (raster images, and drawings clustered within the band) whose box, rounded to a point,
   repeats on half of the reference pages are logos and are dropped too. Watermarks go:
   diagonal text at least 2 x body size, and rotated text repeated at the same place on
   half of the reference pages.
3. **Columns.** Narrow lines (at most 55 % of the text width) are projected onto the x-axis.
   A gap of at least 8 pt in the middle half that no narrow line touches (or at most two,
   so a centred title does not hide the columns) is a gutter, if each side has at least
   three lines sharing a left edge and the typical line on each side is at least 30 % of the
   text width (two table columns are not text columns). Margin-band lines are ignored here.
4. **Rows.** PyMuPDF splits one visual line into several "lines" (justified text, font
   changes in inline maths). Fragments on the same baseline are joined, never across the
   gutter; small sub/superscript fragments are folded into their line.
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
   indent after a short line (LaTeX paragraphs), and it does not start with a bullet or a
   `Figure N` caption. A change between bold and regular, or into or out of heading size,
   ends a run. PyMuPDF's own blocks are not used: they glue a caption to the next
   paragraph and a fraction's denominator to the text under it.
7. **Paragraph text.** Rows are joined with spaces. `veloc-` + `ity` becomes `velocity` (a
   letter, a hyphen at the line end and a lowercase start); `Newton-` + `Raphson` keeps the
   hyphen without a space; soft hyphens disappear. Whitespace is collapsed and zero-width
   and control characters removed. A paragraph cut by a column break (first part does not
   end a sentence, second part starts lowercase at the top of the next column) is rejoined.

### Headings

- **Body size** is the font size covering most characters in the analysed pages.
- A run of at most 3 rows and 200 characters at **1.15 x body size or more** is a heading
  (`"larger font"`).
- A **bold body-size** run of at most 2 rows and 120 characters that starts a block (after a
  gap, an indent or a sentence end) and reads like a title (no lowercase start, no trailing
  comma) is a heading with weak evidence (`"bold body-size line"`).
- A **standalone numbered line** at body size ("2.3 Units of Speed", one row, a gap above
  and below, no full stop) is a heading (`"standalone numbered line"`), but only with a
  real section shape (`section_heading_number`): a dotted number
  that does not start with `0.` (`2.3`, `2.3.1.`), a prefix word (`Chapter 3`, `Section
  2.1`) or a roman numeral with a full stop (`IV.`), followed by a capitalised word that
  is not a unit or a month. `12 Metres`, `2 kg`, `2.5 Kilograms`, `0.5 s`, `10 October
  2026`, `9.8 metres per second`, a bare `2.4` and numeric table cells are paragraphs. A
  title starts with a capital letter after any number, so a bold `2 kg` is not one either.
- Section numbers are evidence: `2.3`, `2.3.1`, `Chapter 2`, `IV.` are
  `"numbered section"`; `1.` and `A)` are `"list-style number"` because lists start that way.
  In a larger font any section number counts (`3 Forces` is a chapter title); at body size
  only the section shape above does.
- Rotated and diagonal text is never a heading.
- A bare chapter number (`4`, `Chapter 4`) in a large font directly above the title is
  merged into it (`4 Energy and Work`).

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
font -0.15, outline repaired -0.03. A fix is ACCEPTED only with strong evidence and none of
the first three penalties (so every accepted heading has confidence >= 0.85, the default
`AUTO_ACCEPT_CONFIDENCE`); everything else is NEEDS_REVIEW. Checks (all warnings):
`heading.outline` / `heading.level_repaired`, `heading.evidence` / `heading.weak_evidence`,
`heading.all_caps`, `heading.length` / `heading.very_short`, `heading.list_like`. An empty
heading text gives ABSTAINED with reason `heading.empty_text`.

### Equations

Every character is classified: **maths** (a maths font such as CMMI, CMSY, MSBM, STIX,
Cambria Math, Latin Modern Math, Noto Sans Math, Symbol; a Unicode math-italic letter like
`𝑣`; Greek; a symbol that only occurs in maths such as `∑ √ ≤ × →`), **digit**, **operator**
(`+ - * / = < > ^` and the minus/plus-minus signs), bracket, letter, code (monospaced) or
other. A minus sign alone is not maths evidence: groff prints command-line options as `−d`.

- **Display equation rows**: `parse.equation_score >= 0.5`, no prose word (3+ plain
  letters other than `sin`, `log`, units like `mol`; or English two-letter words like `is`,
  `if`), at least half the characters maths-capable, and not mostly monospaced.
- **Fragments**: short maths-only rows that do not score on their own (a lone `t`
  denominator, `2a`), and equation numbers (`(2.1)`, `[3]`, `Eq. 2.4`, a bare `2.4`).
  They become maths when they touch a fraction bar or radical together with other maths
  rows, or sit right next to a maths row. An equation number belongs to maths whose height
  it shares: its vertical centre within half a font size of a maths row (OpenStax sets
  `2.4` level with the fraction bar, on neither the numerator's nor the denominator's
  line); a bare `2.4` only counts to the right of the maths with a gap of at least one
  font size. Otherwise fragments stay text (`12 m/s.` at the end of a sentence, `2.4`
  alone on a line). Attachment spreads breadth-first through a vertical index, so a page
  of thousands of short numeric rows costs about n log n.
- **Fraction bars and radicals** come from `page.get_drawings()`: horizontal rules at most
  1.6 pt thick (lines or thin filled rectangles; Chromium and TeX both draw them as
  rectangles) and stroked square-root shapes. A rule counts when maths rows touch it and it
  is not much wider than they are (a table rule beside a `>4` cell does not count). A bar
  that starts at the top-right corner of a `√` glyph is reported as `"radical sign"`.
- Vertically adjacent maths rows (gap at most 0.8 x font size) form one display region; the
  box covers every row and every rule it uses. A single row of at most two characters
  without a maths character (`>4`, `+8`) is not an equation.
- **Inline equations** inside paragraph rows: a stretch of maths-capable characters
  (maths characters, digits, operators, brackets, single letters, sub/superscripts, a decimal
  point or a comma inside brackets), trimmed of spaces, trailing operators and punctuation,
  unbalanced brackets and a leading or trailing English `a`/`A`/`I`. It is kept only with
  evidence: a maths character; an italic single letter with an operator, script or Greek
  letter; a single letter with a sub/superscript (`x²`); a signed number (`−3`); or a
  relation with operands on both sides (`x = 5`, not markup such as `<a><b/>`). A lone
  italic word, `[3]`, `10-12` and `2019` are not maths. List bullets (`•`, `▪`, Word's
  private-use Symbol bullet U+F0B7, and a `·` or `∙` that starts a line) are never maths,
  even in the Symbol font Word draws them in.

### Figures

- **Raster images** from `page.get_image_info()`, skipped when either side is under 24 px
  or drawn under 12 pt (bullets, spacers), or when the image covers 85 % of the page or more
  (backgrounds and scanned pages).
- **Vector figures**: drawings closer than 6 pt to each other are clustered, then clusters
  whose boxes overlap are joined (the markers of a sparse scatter plot are further apart
  than 6 pt but sit inside one plot). A cluster is a figure when it is at least 24 x 24 pt,
  is not only horizontal/vertical rules (separators, underlines, table grids), is not a
  page border, and is not a box or table around text: a cluster without meaningful curves
  (a plotted line covering 5 % of its area, or at least three curved paths such as
  markers) and without bar-chart bars, with two or more text rows inside, is a box or
  table. A rounded rectangle (at least 60 % of its outline straight horizontal and
  vertical lines) is a box, not a curve, so a rounded callout around text is not a figure.
  One tick icon in a table cell does not make the table a figure. Equation rules are taken
  out first.
- Overlapping figures merge (a photo with arrows drawn on it). Text inside a figure, and
  short labels (at most 25 characters) within 0.6 x font size of its edge, belong to it and
  grow its box. Running text does not: two prose lines (3+ English words each) that read as
  consecutive lines of one paragraph, or a title-sized line (1.15 x body, 2+ words), stay in
  the reading flow. A raster image with such text on it is a background, a panel or a
  watermark: it is dropped as a figure and the text keeps its place in the outline.
- **Captions**: a run starting `Figure N` / `Fig. N` followed by punctuation or a capital
  letter ("Figure 2 shows ..." is prose) attaches to the nearest figure that overlaps it
  horizontally, below or above within 36 pt (below wins ties). An unattached caption stays
  a paragraph.

### Title and language

`title` is the PDF metadata title unless it is empty, a file name (`notes.docx`) or a tool
default (`Untitled document`, `Microsoft Word - ...`); otherwise the first level-1 heading.
`lang` is the catalog `/Lang` when it looks like a language tag, else `en`.

## Errors

`LayoutError.code` is a category, safe to log; messages never contain document text.

| Code | When |
|---|---|
| `unreadable` | empty input, not a PDF (MuPDF would otherwise open a PNG as a one-page document), damaged content, or a page that crashes or hangs MuPDF in the probe child process |
| `encrypted` | the PDF needs a user password (owner-only restrictions are fine) |
| `no_pages` | the file has no pages, or `pages=[]` |
| `page_out_of_range` | a requested page index is negative or past the end |
| `too_many_pages` | more pages requested (or in the file, with `pages=None`) than `max_pages` |

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
72 dpi (`_probe_pages`). A child that dies (any non-zero exit) or runs longer than
`PROBE_TIMEOUT_S` (60 s) makes the file `LayoutError("unreadable")`; Python exceptions in
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

What the probe does not cover: the analysis itself (Python code in this process) has no
time limit. Fragment resolution, run building, column detection and script attachment are
indexed so a crafted page of thousands of rows stays near-linear (a 16-column page of
1,760 tiny numeric rows took 66 s before this and takes about 0.3 s now; 28,000 rows take
about 5 s), but a job of 30 such pages still holds the module lock for minutes. **For
WP4/WP5:** full isolation (running `convert_pdf` for a job in a child process with a job
time limit) belongs to the worker, which owns the job lifecycle.

## Cropping, the cache and threads

PyMuPDF is not thread-safe, so every PyMuPDF call in `layout.py` runs under one module lock;
concurrent callers are safe but serialised (crop on one thread; a thread pool only helps the
model calls). `analyze` opens the document for that call only and closes it. `crop` caches
opened documents (4 at most), keyed by the identity of the bytes object and, failing that,
by its SHA-256, so cropping every element of a PDF parses it once. The cache holds user
content, so an entry unused for `DOCUMENT_CACHE_TTL_S` (120 s) is closed by a daemon timer
even if nobody calls `release`: an idle worker does not keep the last jobs' PDFs past the
retention purge. **For WP4:** `pipeline.run.convert_pdf` should still call
`layout.release(pdf_bytes)` in a `finally` when the job ends; `release` is not in the
contract table of `docs/design/beta-architecture.md` yet (that file is outside WP2).

Crops render at `dpi` on a white background; when the long side would exceed `max_side_px`,
the zoom drops so it fits (one pixel of headroom absorbs MuPDF's rounding).

Speed (this machine, 2026-10-08): a 19-page DocBook spec analyses in 0.3 s plus the probe;
a page with a 4,000-marker scatter plot takes about 1 s, almost all of it in MuPDF's path
extraction (`get_cdrawings`, which returns plain tuples; `get_drawings` and per-point
PyMuPDF objects were three times slower). A crop takes about 0.1 s.

## Known failure modes

- **Scanned pages** have no text layer: the full-page image is skipped as a background and
  the page yields nothing. OCR is out of scope for the beta.
- **Synthetic italics.** When a PDF fakes italics by slanting an upright font (Chromium does
  this when the italic face is missing), the font name and flags say upright, so an italic
  `x = 5` typed as text is not recognised as maths unless a maths font or symbol is involved.
- **Plain-text maths in the body font** (`v = d/t` typed in Word without the equation
  editor) is found inside paragraphs only through the relation rule; `d/t` alone is not.
- **A display equation line with words** (`x = 1 if y > 0`) becomes a short paragraph with
  inline maths, not one display equation.
- **A wrapped sentence whose last line is only maths** (`... the speed is` / `v = d/t.`) is
  read as a display equation.
- **Very short documents** learn the "typical line pitch" from very few lines; with one
  two-line paragraph the paragraph gap itself becomes the typical pitch and separate
  paragraphs can merge.
- **Maths inside headings and captions** stays as characters in their text (for example a
  math-italic `𝑔`); only paragraphs get inline equation elements.
- **Paragraphs split across pages** stay two elements (an element has one page and box).
- **Inline maths that wraps across lines** becomes two inline equations.
- **Tables** are not a separate element kind: cell text becomes paragraphs (read left to
  right, row by row) and drawn grids are ignored. A two-column table with long cells can
  look like text columns.
- **Equations drawn entirely as vector outlines** (some MathJax SVG prints) have no text;
  they come out as figures.
- **Drop caps** (a huge first letter) become a one-letter paragraph.
- **Hyphenated compounds across lines** (`well-` / `known`) lose the hyphen: without a
  dictionary they look like a broken word.
- **Plain body-size numbered headings with a bare integer** (`3 Forces` in body text,
  not bold) are paragraphs: at body size a bare integer is too often a quantity. They
  are found when bold (as weak evidence) or in a larger font.
- **Bold table header cells** (`Time`, `Distance`) can still become weak, NEEDS_REVIEW
  headings; numeric cells no longer do.
- **Repeated margin logos drawn as vectors** are recognised only from drawings that lie
  inside the header/footer band; a logo that extends into the body is a figure on every
  page.
- **A single prose line on a background image** (one subtitle on a slide backdrop) is
  still absorbed as a label; the image needs two paragraph lines or a title-sized line on
  it to count as a background.
- **Margin text in the body font that changes per page** (`Problem 3`, not bold, in the
  top band) is still dropped as a running header; only larger or bold text is kept.
- Numbers are tuned on Chromium, PyMuPDF-generated and DocBook/groff PDFs. Real course PDFs
  (LaTeX, Word, PowerPoint exports) need a check before the thresholds are trusted; record
  what breaks here.

## Tests and fixtures

- `tests/test_layout_analyze.py` builds small PDFs with PyMuPDF for each rule (headings,
  hyphenation, fraction bars, inline maths, figures, columns, headers, errors).
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
