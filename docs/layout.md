# Layout analysis (`pipeline/layout.py`, `pipeline/heading_step.py`)

Step 1 of the conversion: a PDF becomes a list of `Element`s (headings, paragraphs, list
items, display and inline equations, figures, data tables) in reading order, with no model
call. Everything later
(equation transcription, alt text, HTML assembly) works from these elements and from crops
of their boxes. The code is deterministic: the same PDF always gives the same elements.

## Contract

| Function | What it returns |
|---|---|
| `analyze(pdf_bytes, *, pages=None, max_pages=None) -> Layout` | elements of the requested 0-based pages (all by default), `page_count`, `pages`, `page_sizes`, `title`, `lang` |
| `crop(pdf_bytes, element, *, dpi=200, pad=2.0, max_side_px=2048) -> bytes` | PNG of the element box grown by `pad` points, clipped to the page, long side capped |
| `release(pdf_bytes=None)` | drops one cached document (or all); call it in a `finally` when a job finishes |
| `heading_levels(elements) -> dict[id, level]` | raw outline level per heading (before repair) |
| `page_glyphs(page) -> list[Glyph]` | every visible character of an open page with its box (display space) and span colour, for the text pass |
| `heading_step.heading_fixes(elements) -> list[Fix]` | one `HeadingFix` per heading, outline repaired, with checks and confidence |
| `text_pass.apply(pdf_bytes, elements, *, pages, layout_lang, colour) -> TextPassResult` | the elements with links, colour runs and passage languages, the document language and its source, and the pass's notices (see "The text pass") |

Element conventions:

- **Ids** are `p{page+1}-e{index}`, `index` counting from 0 per page in reading order.
  An inline equation gets the id right after its paragraph (`p1-e3` paragraph, `p1-e4` and
  `p1-e5` its inline equations, `p1-e6` the next block).
- **`order`** is the position in the whole document (0, 1, 2, ... across pages).
- **Inline equations** have `display="inline"` and `parent_id` = the paragraph (or list
  item) id; the parent's `text` contains `core.types.inline_marker(id)` (`⟦p1-e4⟧`) where
  the maths was. Display equations have `display="block"` and no parent.
- **Equation numbers** ("2.4", "(2.4)", "[2.4]", "Eq. 2.4") of a display equation are in
  `equation_label`, as printed, and are not in its `text` or `bbox`, so they are not in
  the crop the model transcribes.
- **List items** are `Kind.LIST_ITEM` with `list_marker` (as printed: "•", "a.", "(iii)",
  a drawn bullet reported as "•", "◦" or "▪"), `list_ordered` and `list_level` (1 = top);
  `text` does not include the marker.
- **Boxes** are PDF points in display space: the page's `/Rotate` is applied, so a
  landscape page reports `(792, 612)` in `page_sizes` and `crop` uses the boxes as they are.
  The origin is the top-left of the CropBox; content outside it is ignored and figure boxes
  are clipped to the page.
- **`reasons`** are short content-free evidence strings (`"math font"`, `"fraction bar"`,
  `"larger font"`, `"raster image"`, ...). They never contain document text.
- **Text** is copied from the PDF with whitespace collapsed and invisible characters
  (zero-width and control characters) removed. Text nobody sees on the page is never
  content: render mode 3, 5 % opacity or less, or painted over by an image or a filled
  shape ("Hidden text and page types" below). The inline-marker brackets `⟦` `⟧` (U+27E6/U+27E7) are replaced by the
  look-alikes `〚` `〛` (U+301A/U+301B) in every piece of document text, so a PDF cannot
  fake a marker: every marker in an element's `text` is a real inline child of it. Word's
  private-use Symbol bullet (U+F0B7) becomes `•`.
- Figures carry the nearby `"Figure N ..."` text in `caption` and any labels drawn inside
  or right next to them (axis titles, tick labels) in `text`.
- **Tables** (since pipeline 0.4.0) are `Kind.TABLE`: `table` is the grid built from the
  PDF (`TableGrid`, source "local"), `caption` the `"Table N ..."` text printed above or
  below it (also in `table.caption`), `text` the cells' text row by row, and `bbox` the
  table without its caption. `reasons` say how it was found and built (`"ruled grid"`,
  `"aligned text grid"`, `"box extended along rules"`, `"SLANet-plus grid"`,
  `"drawn cell grid"`, `"PyMuPDF grid"`, `"continued from previous page"`). How tables
  are found: [tables.md](tables.md).

## How a page is read

0. **Tables first.** On every analysed page the data tables are found
   (`pipeline/tables.py`, [tables.md](tables.md)) before anything below: their lines,
   rules and `"Table N"` caption leave the page, and each comes back as one TABLE element
   placed in reading order like a figure. So a table's numbers are not a display
   equation, its bold header cells not headings, its drawn grid not a figure, and its
   columns not text columns. This happens whatever `FEATURE_TABLES` says; with the flag
   off the table step keeps the table as an image with a required decision. A grid of
   prose (one column, or a term and its long definition) is not claimed and is read as
   before.
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
   text width (two table columns are not text columns). Lines for that test are boxes that
   overlap vertically by half the smaller height: TeX splits a line with inline maths into
   pieces whose bottom edges differ, and measured piece by piece a full LaTeX column
   looked like a column of short cells. Margin-band lines are ignored here.
4. **Rows.** PyMuPDF splits one visual line into several "lines" (justified text, font
   changes in inline maths). Fragments on the same baseline are joined, never across the
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
   `Figure N` caption. A row continuing a list item never starts left of the item's
   marker (so the paragraph after a list is not swallowed by its last item). A change
   between bold and regular, or into or out of heading size, ends a run. A single
   display-equation row that starts at the paragraph's left margin at the usual line
   pitch is the last line of the sentence above (`... exactly one solution,` / `x =
   A⁻¹b.`), not a display equation: its maths becomes inline equations of the paragraph. PyMuPDF's own blocks are not used: they glue a caption to the next
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
font -0.15, outline repaired -0.03. A heading is *certain* with strong evidence and none of
the first three penalties (so its confidence is >= 0.85, the default
`AUTO_ACCEPT_CONFIDENCE`). Checks (all warnings): `heading.outline` /
`heading.level_repaired`, `heading.evidence` / `heading.weak_evidence`, `heading.all_caps`,
`heading.length` / `heading.very_short`, `heading.list_like`. An empty heading text gives
ABSTAINED with reason `heading.empty_text` (review "required").

**Review tiers** (docs/design/beta-architecture.md "Review tiers"). Every heading is
written as a heading; the tier only says whether a person must decide:

| Heading | Status | `Fix.review` |
|---|---|---|
| certain | ACCEPTED | `none` (`optional` when the outline repair moved it) |
| uncertain, its label used once | ACCEPTED | `optional`: its failed checks are the notes |
| uncertain, the same label and style two or more times | NEEDS_REVIEW | `required`, one decision for the group (`group_key`, `group_size`) |

Same label: NFKC, case-folded, letters and digits only ("Teacher Support:" = "teacher
support"). Same style: font size to the half point, bold or not, larger-font evidence or
not, and the same proposed level. Certain copies of the label are never in a group. On
the three gold pages this gives 9 decisions in 43 PDF pages (one per repeated label:
"Teacher Support", "Solution", "Discussion", "Procedure", "Distance vs. Displacement")
instead of 61 headings to review.

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
as a heading), so neither rule is used. Labels stay headings: alone they are
optional notes, repeated they are one group decision ("Solution" x4 on page 2.2). A real
OpenStax PDF, or a course PDF with worked examples, is the evidence that could change
this.

**Known-plain letters** (`Element.regular_letters`, equations only). The letters of an
equation's text that the PDF draws, not bold, in a font known to have a bold face
(`_REGULAR_FACE`: Computer Modern CMR/CMMI/CMTI/CMSS, Latin Modern, cm-super, txfonts and
pxfonts, Times, Nimbus, Liberation, DejaVu, Arial, Helvetica, Calibri, Cambria, Georgia,
Palatino, Garamond, Charter, Noto, Source, STIX, XITS, TeX Gyre, Libertine, ...). A bold
reading of such a letter is a scalar read as a vector, which the equation step treats as
meaning-level. Fonts that cannot show a letter's style in the text layer are left out on
purpose: MSBM (double-struck), EUSM and RSFS (script), EUFM (fraktur), CMSY
(calligraphic), Symbol. A letter that is already a styled maths code point says its own
style and is not listed. On the two-column handout: `𝐯 ∈ Rn` gives `n` (R is MSBM), the
bold omega gives nothing.

### Lists

A row starts a list item when it begins with a marker followed by the item's text
within 3 x font size:

- a bullet glyph (`•` `◦` `▪` `▫` `■` `□` `●` `○` `‣` `⁃` `➢` `✓` `►` `▸` `▶` `▹` `◆`,
  Word's private-use Symbol bullet, beamer's AMS triangle `▶`);
- a bullet drawn as a small shape (at most 0.6 x font size) just left of the row at its
  height: Chromium draws CSS disc markers as paths. Filled curves are "•", outlines "◦",
  filled squares "▪";
- a line-start dash or star (`-` `–` `—` `*` `∗`, LaTeX's second and third levels) or a
  middle dot (`·`, Word's Symbol bullet as extracted). A dash followed by a digit or a
  decimal point (`- 40 m/s`, Word's `– 5 °C`), or set in a maths font, is the sign of a
  value, not a bullet: the sign stays in the text;
- an ordered marker: `1.` `2)` `(3)`, `a.` `b)` `(c)`, `A.`, `iv.` `(ii)` `IV)`. OpenStax's
  "3 ." (number and full stop in separate spans) is read as "3.".

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
paragraphs. Headings are decided first: "1. Introduction" in a large font is a heading.

The item text starts after the marker; continuation lines join the item (a hanging
indent is fine). **Levels** come from the left edge of the item text, which is the same
for every item of one level even when the markers are right-aligned (LaTeX's "(i)",
"(ii)", "(iii)"): consecutive items in reading order form one list, a deeper indent
(more than 0.6 x body size) opens a level, a shallower one closes levels until one
matches. The indent is measured from the left edge of the item's column on its page (the
leftmost x where at least three rows start), so a list that goes on in the next column or
on a page with another margin keeps its levels. A display equation inside an item does
not end the list; any other block does.
A list item's row is never a display equation ("b. - 40 m/s" had become one): maths in
it becomes inline equations with the item as their parent.

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
- **Display equations** are built from maths rows in two steps (`_equation_regions`):
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
     centred on their own (`time = d / v` over `d = v × time`), two numbered equations
     (each row of a numbered `align`) and equations at paragraph spacing stay apart.

  The box covers every row and the rules the equation uses (no rule of a neighbouring
  equation). A single row of at most two characters without a maths character (`>4`,
  `+8`) is not an equation.
- **Equation numbers.** A row that reads as an equation number and touches no rule goes
  to the line it sits beside (`_number_beside`, nearest vertical centre first), unless
  that line ends with a relation or operator (`x =   2.4`: the number is its right-hand
  side). It becomes `equation_label` and stays out of the text and box. A number merged
  into a maths row (`_merge_baselines` joins pieces up to 4 font sizes apart: `= 2000 m −
  525 m ,   2.8`) is first cut off when it stands at least one font size clear of maths
  (or of the comma that ends it) that does not end in a relation; a prefixed number
  (`Eq. 2.4`, `Equation (2.4)`) is cut off whole, measured from before the prefix. A
  number inside the maths that is not beside any line stays part of it.
- **Inline equations** inside paragraph rows: a stretch of maths-capable characters
  (maths characters, digits, operators, brackets, single letters, sub/superscripts, a decimal
  point or a comma inside brackets), trimmed of spaces, trailing operators and punctuation,
  unbalanced brackets and a leading or trailing English `a`/`A`/`I`. It never starts with
  an operator that cannot start an expression (`/ * ^ = <` ...: `/h)] = 40` is the tail
  of a plain-text formula cut by a line break) or ends with one (including `−` and `×`).
  A bar `|` is kept at either end when the run has a pair of them (`|x| < 1`, `|r|ⁿ`);
  an unpaired bar at an edge is dropped.
  It is kept only with evidence: a maths character; an italic single letter with an
  operator (a leading sign does not count: `-49 m` with an italic unit is a quantity) or
  script or Greek letter; a single letter with a sub/superscript (`x²`); a signed number
  with a real minus sign (`−3`); or a relation with operands on both sides (`x = 5`, not
  markup such as `<a><b/>`). Without a maths character the run's brackets must also
  open and close in order. A lone italic word, `[3]`, `10-12` and `2019` are not maths.
  TeX's spacing accents (the hat of `x̂`, tilde, bar, dot) do not end a run; maths
  function names (`sin`, `lim`, `proj`, `rank`, `diag` ...) are not English words. List
  bullets (`•`, `▪`, Word's private-use Symbol bullet U+F0B7, and a `·` or `∙` that
  starts a line) are never maths, even in the Symbol font Word draws them in.
- When `,` `.` `;` `:` or `?` follows an inline equation, its box's right edge moves left by
  up to `CROP_PAD` (crop's default padding, 2 pt) so the padded crop ends where the
  punctuation starts and still holds every glyph. The mark stays in the paragraph after
  the marker; if the crop showed it, the model transcribed it as well ("d0 , ,").
  `equation_step` also strips such a mark from the end of inline LaTeX.

### TeX and beamer

Checked on `tests/fixtures/layout/latex/` (Tectonic, Computer Modern and beamer's Latin
Modern Sans):

- CMMI, CMSY, CMEX and CMBSY are maths fonts; CMBX, CMB, CMMIB (`\bm`, `\boldsymbol`)
  and CMBSY are bold, so bold vectors keep their weight in the text layer (`𝐯`).
- TeX's `\neq` is a zero-width negation slash (U+0338) over `=`; text cleaning joins it
  into `≠` (and `≮`, `∉`, ...). The text-font capital Delta (OT1 CMR) extracts as U+2206
  and counts as a maths symbol.
- MuPDF's superscript flag is set on any span that starts above the previous one, which
  in TeX output is most of a line after an inline fraction, so a character is a script
  only if it is smaller, or flagged and raised by at least 0.2 x font size.
- Beamer sets maths letters in Latin Modern Sans Oblique, a text font. Short italic runs
  (at most three letters) are variables, not words, when the row also has upright
  letters, or has a relation sign and no longer italic run: "at" in `v = v₀ + at` is
  not the English word. Beamer captions have no number ("Figure: ...") and still attach.

### Bold letters in prose

Only with `FEATURE_NOTATION_MEMORY` on (the run's `ConversionOptions.notation_memory`);
with it off nothing here runs and the output is as before. A single bold letter in
running text ("the projection of **b** onto **a**") is a vector the text would otherwise
give as a plain letter, heard as the article "a" (UX review MF2). Layout makes it an
inline equation, and the equation step fixes it without a model call
(docs/steps.md "Course notation").

Two functions in `layout.py`, nothing else in the page flow changes:

1. `_bold_letter_runs`, per paragraph row, beside the inline maths runs:
   - a maths run that is one bold letter (a bold omega in CMMIB, a maths bold code point)
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
   with evidence: the same bold letter in the document's maths (a display or inline
   equation: `bold in the document's maths`), or, for a small letter, another bold letter
   that is maths in the same paragraph (`maths in the paragraph`). Any other candidate
   goes back into the text as the plain letter it was.

Running text is paragraphs and list items (the blocks that may hold inline maths), so a
bold letter in a heading or a caption is never touched; bold words and labels are left
alone, not raised as notes. The evidence is the element's last reason (content-free,
like every reason).

Measured on 2026-10-09 (`uv run python -m a11y_stem.eval.bold_letters --openstax`;
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
handout, 10 letters were kept by the same bold letter in its displayed maths and the omega
by its maths font; on OpenStax, 7 by the document's maths and 2 by a bold letter nearby.
With a course notation listing bold d, v, a, x and s, the same 9 are found, each by the
course. Not covered: PDFs whose font does not mark bold (no candidate is seen at all
there), and bold letters in captions. A bold answer letter in a document whose same bold
letter is a vector elsewhere ("The correct answer to question 3 is **b**") is still made
maths by the document's evidence; the equation step's own tiers then decide it.



### Figures

- **Raster images** from `page.get_image_info()`, skipped when either side is under 24 px
  or drawn under 12 pt (bullets, spacers), or when the image covers 85 % of the page or more
  (backgrounds; a scanned page never gets here: it is kept whole as its image, "Hidden
  text and page types" below).
- **Vector figures**: page furniture is set aside first: a frame round the page (one
  rectangle outline at least 85 % of the page both ways, or a page-long rule within 15 % of
  an edge), a background fill (85 % of the page's area or more) and a band of colour along
  an edge (a slide template's title bar, at most 15 % of the page deep). A frame's box
  holds everything on the page, so clustered with the rest it made every figure on a page
  with a drawn border one "page border", dropped with no decision. Then drawings closer
  than 6 pt to each other are clustered, and clusters whose boxes overlap are joined (the
  markers of a sparse scatter plot are further apart than 6 pt but sit inside one plot).
  A cluster is a figure when it is at least 24 x 24 pt, is not only horizontal/vertical
  rules (separators, underlines, table grids, the long separate lines of ruled paper), and
  is not a box or table around text. A cluster that still fills the page (85 % both ways:
  a full-page plot, a slide's chart) is a figure when it has three curves or two bars and
  no running text inside; else it is a decorated border or a background drawn behind
  text, left out as before. On a page with ink (any `ink_*` reason of its page type) such
  a drawing is never a figure for the model, as it may be a page of notes: the safety net
  keeps the page image instead (on a typed page with handwriting, `mixed_ink`, the
  handwriting is cut out already and the rest is left out). A cluster without meaningful
  curves (a plotted line covering 5 % of its area, or at least three curved paths such as
  markers) and without bar-chart bars, with two or more text rows inside, is a box or
  table around text. A rounded rectangle (at least 60 % of its outline straight horizontal and
  vertical lines) is a box, not a curve, so a rounded callout around text is not a figure.
  One tick icon in a table cell does not make the table a figure. A small mark just left
  of a text row at its height is a drawn list bullet, not a curve of a figure. Equation
  rules are taken out first.
- Overlapping figures merge (a photo with arrows drawn on it). Text inside a figure, and
  short labels (at most 25 characters) within 0.6 x font size of its edge or across it (a
  tick label half inside the box), belong to it and grow its box. A maths row is a label
  only when it is at most three glyphs (an axis variable `x` in CMMI). Running text does not: two prose lines (3+ English words each) that read as
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
`lang` is the catalog `/Lang` when it looks like a language tag, else `en`. The text pass
decides the document's language from there (it detects one when the PDF has none).

### Code blocks and footnotes (notices)

`Layout.notices` carries two layout facts the job page lists under "Not handled in this
file" (review "none": no decision; `_layout_notices`):

- `code_block`: a paragraph or list item with at least 12 glyphs, 80 % of them in a
  monospaced font (MuPDF's flag, or a name such as Courier, cmtt, Consolas). Its text is
  kept, but not its line breaks and indentation, which decide what belongs to a Python
  loop's body.
- `footnote`: a paragraph or list item in the bottom 40 % of the page, in a smaller font
  than the body, that starts with a digit, `*`, a dagger or `§` printed raised in the
  text above it on the same page. It is kept where it falls, not linked to its mark.

## Hidden text and page types (`pipeline/page_type.py`, pipeline 0.4.0)

**Why.** A PDF can carry text nobody sees. An iPad notes app writes an invisible layer of
handwriting-recognition guesses over the ink (fill opacity 0, an Apple ".SF" font); an
OCR program writes its guesses over a scan in render mode 3, or paints the page image
over visible text. Read as content, a real 4-page set of iPad lecture notes became 26
headings and 72 paragraphs of garbage, titled with a 35 pt scrawl, with no maths, no
model call and 0 decisions: "Finished. The accessible HTML is ready." OCR'd scans fell
into the same hole, and the app's own advice for scans ("run OCR and upload again")
produced exactly those files. There is **no handwriting or scan conversion** (decided
2026-10-09): such pages are kept as page images, marked "Not converted", with a decision.

**What is never content** (`_page_rows`, the same extraction as before, no second one):

- a span in render mode 3 (MuPDF's char flags show neither fill nor stroke);
- a span at 5 % opacity or less (rawdict `alpha` <= 13), unless it is text used as a
  clip (render mode 7, a gradient- or picture-filled title: MuPDF gives it alpha 0 with
  the CLIPPED flag), `page_type.span_hidden`. Clip text is visible only when something
  is painted through it: it goes through the render test below, and a clip nothing was
  painted through counts as invisible;
- a visible character painted first and then covered by an image, a shading, a filled
  shape or a stroke at least 3 pt wide ("covered": ABBYY-style text under the page image,
  a black box over a line, a white bar or a marker scribble over it). Annotations count:
  a box, highlight or text box drawn with Acrobat's or Preview's tools over a line is the
  most common fake redaction. Paint order comes from `get_bboxlog` and `get_texttrace`
  (only read when a visible span lies inside an image, a shading, an opaque fill or a
  thick stroke); a cover must hold the middle 60 % of the glyph, so an underline or a
  strikeout is not one; and the text counts as covered only when removing it (a
  text-only redaction on a one-page copy, its annotations made page content first, as a
  redaction deletes the ones it touches) does not change the rendered page there by more
  than 1 % of its pixels. That render is the opacity check: soft masks, colour-key and
  stencil masks, constant alpha, blend modes and a yellow highlight all let the text
  show, and then it is kept. Up to 24 runs are tested per page, the rest follow them.

The text pass's glyphs (`page_glyphs`) skip the same invisible spans.

**Page types.** `page_type.classify` reads the rawdict and the drawings layout already
extracts (`_read_page`; image boxes come from MuPDF's image blocks, which decode no
image) and returns a kind, the characters it may trust, where its content would have to
come from, and content-free reason codes. It is detect v4 from the research, ported with
the design review's changes (`CLASSIFIER_VERSION` "page-type-v5.2"):

| Kind | What it is | What the app does |
|---|---|---|
| `born_digital` | visible text, nothing hidden | reads it as before |
| `mixed_hidden_text` | visible text plus invisible or covered words | reads the visible text; notice `hidden_text_ignored` (optional) with the count |
| `mixed_ink` | visible text plus handwriting | reads the text; the ink is kept out of the figures and each region of it (strokes within 24 pt, at most 6 regions) becomes a FIGURE kept as an image (`handwriting_on_page`) |
| `handwritten`, `handwritten_ocr` | vector ink, no or only a hidden text layer | the page is kept as its image (`handwritten_page`): no elements |
| `scan_no_text`, `scan_ocr` | a picture of a page, no or only a hidden text layer | the page is kept as its image (`scanned_page`): no elements. With a little visible typed text and nothing hidden (a typed header on a scan, a title slide on a photo) it is not called a scan: its visible text is read, and the page is also kept as its image (`unread_page`) |
| `vector_no_text` | drawings, little or no text | read as before (figures); kept as an image (`unread_page`) when it has less than a line (40 characters) of visible text and its ink could not be analysed in time; its visible text is read, its drawings are not sent to the figure model |
| `blank` | nothing painted, or paper rules alone | nothing |

The decisions in the rules, with the measured values next to each threshold in the code:

- 40 visible characters make a page "have a text layer". Ink is "clean" when exact
  repeats are at most 35 % of the complex shapes, filled strokes share one pen width
  (80 % within 0.75-1.33 x the median of their colour), and at most half the strokes have
  a same-size twin. A page of writing has 40 or more clean strokes in 3 or more rows; a
  typed page with 12 strokes in a row has some handwriting. With no visible text at all,
  5 clean strokes in a row are already a short note; under a typed title (1 to 39
  visible characters) the page needs 12 strokes or a second row, as a row of five
  machine-drawn curves (five waves on a slide, five inductor coils) is not handwriting.
  A handwritten page with visible typed text and no hidden layer is kept as an image
  when converted, but never refuses the upload: it may be machine-drawn.
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
- **A scan is a picture of a page** (C1.5): images cover 85 % of the page, or half of it
  with no visible text at all. A slide title over a photo is a typed page with a figure.
  Hidden text with no picture of a page over vector ink (clean, or rows of writing the
  checks rejected, or ink not analysed) is handwriting (`invisible_without_image`). Over
  machine-drawn vector content (text turned into outlines, a diagram: what an OCR program
  writes over a vector page with no text layer) the page is `vector_no_text`: the hidden
  layer is dropped and the drawings are read as figures.
- **Time budget** (C1.6): the ink analysis (pure Python) gets 10 s per document
  (`INK_BUDGET_S`), and a page with more than 200,000 path items is not analysed
  (`INK_MAX_ITEMS`); such a page says `ink_not_analysed`. Measured on this machine,
  pinned to one core: the upload probe on a 19.6 MB file of 17 pages of dense synthetic
  ink (no text layer) took 13.8 s alone and 17.4 s with another busy process on the same
  core, under the probe's 30 s limit; the file was accepted, because pages not analysed
  in time never refuse an upload. Not measured on Cloud Run's 1 vCPU.

**The safety net** (C1.1, independent of the page types): after layout, a page keeps its
page image, with a decision (`unread_page`), when something painted on it was read into
nothing. The elements read from it stay; the image comes after them. It fires when:

- the page has no element and something painted outside the header and footer bands (an
  image or a drawing at least 12 pt on a side, or 3 smaller marks). Rules, paper rules, a
  frame round the page, a background fill and a band of colour along an edge (a blank
  slide of a template) do not count;
- a drawing filling the page was left out because the page has ink the checks did not
  confirm (rejected or not analysed; `layout._vector_figures`): a page of notes under a
  typed title, whatever the title's length;
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
`hidden_text_ignored` (optional) for those of a typed page, and one per kept image with
its reason (required: its decision is the image's fix).

**At upload** (`page_type.document_refusal`, in the probe child): the text of every page
is read first and the check stops at the first typed page, so only a PDF with none pays
for the drawings and the ink analysis. Every page certainly handwritten or scanned, or
blank, refuses it before any quota is spent: `handwritten` (hidden recognition layer),
`handwritten_no_text`, `scanned_ocr` (hidden OCR layer) or `scanned`. A full-page
picture with a few visible characters and no hidden layer (perhaps a slide), drawings
called handwriting under a typed title with no hidden layer (perhaps a diagram, a grid of
sparklines) and a page whose ink was not analysed never refuse: the conversion keeps
them as images with a decision. "Certainly" means no visible text, or a hidden layer
(`page_type._certain`). The worker fails a
result with the same rule (`worker.result_outcome`).

**What is not detected** (the text reaches the HTML, or the content is lost as before):

- white text on a white page or a white cell (needs the colour under each glyph);
- text under a shape that covers less than the middle of its glyphs, or under strokes
  thinner than 3 pt (many passes of a thin pen over a line);
- an annotation hidden from view (the Hidden or NoView flag) is not a cover, rightly:
  the text under it shows;
- very small text (0.4 pt "microtext");
- OCR text drawn visibly in place of the image (Acrobat ClearScan-like): it is trusted,
  garbled maths included;
- a full-page scan with 40 or more visible typed characters on it (a stamp, a header)
  and no hidden layer: the scan is dropped as a page background, silently, as before
  (the design review's "d1 rule" is not built: it would flag every slide with a
  full-slide background picture);
- light handwriting on a typed page (fewer than 12 strokes: a circle, a tick, a
  signature): lost or absorbed into a figure as before;
- grids of small machine-drawn curves (sparklines, small multiples of loops) can look
  like handwriting: kept as an image with a needless decision (a PDF of only such pages
  is refused at upload when they have no visible text at all);
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
- a page-filling drawing with fewer than three curves and two bars (a full-page plot of
  one line), or with running text inside it, is left out, as before: a decorated border
  or a background drawn behind text looks the same;
- a flat drawing less than 24 pt tall (a one-line circuit, a timeline) is too small to be
  a figure and is left out, as before; on a page with no other element the safety net
  keeps the page;
- typed text turned into outlines (no text layer) is read as figures where it clusters
  into blocks; lines that do not are lost, so the safety net keeps the page image too
  (one decision for the document's kept pages).

**Accuracy, honestly.** Measured with `python -m a11y_stem.eval.page_types --labels
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
- **The research corpus, 996 labelled pages** (not out of sample: every page was seen
  while detect v1-v4 were tuned): 586 real born-digital pages, the 4 pages of one real
  set of handwritten notes and 23 derivatives of them, and synthetic handwriting, scans,
  OCR layers, mixed pages and hard negatives. page-type-v5.1 gets 984 page kinds right
  (98.8 %) and 989 actions (99.3 %); detect v4 had 984 and 990. page-type-v5.2 (the
  routing review's changes, 2026-10-10) gets the same 984 and 989, with the same errors;
  on fresh set 4 (seen while v5.1 was fixed, so no longer out of sample) 29 of 31 kinds
  and actions, and on the design review's 41 adversarial pages 29 and 35, as v5.1.
  Its new cases (fake redactions drawn with annotation tools, a CropBox, square waves,
  diagrams under a title, a hidden layer over machine drawings) are tested on synthetic
  fixtures only, so v5.2 has no out-of-sample number either. One real page labelled
  born-digital now has 8 characters under a grey box and a seal painted over them
  (checked by eye: they cannot be seen), so it is called `mixed_hidden_text` and counted
  as an error against its label.

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

## The text pass (`pipeline/text_pass.py`, pipeline 0.4.0)

After layout and before any step, `run` calls `text_pass.apply` once. It reads the PDF
again for what layout does not keep (link annotations, `/Lang`, the colour of each
glyph) and adds links, cross-references, colour runs and languages to the elements. It
never changes an element's text and sends nothing to a model; URLs are never fetched. It
never fails a conversion either: a PDF it cannot read again gives no links or colours
(cross-references and the language still come from the text), and an error inside the
pass leaves the elements as they were, logging only the exception type. What the output
does with all this is in docs/output.md ("Links and cross-references", "Language",
"Coloured text").

### From the page to offsets in `Element.text`

Layout does not record where each character of an element's text was printed: the text
is cleaned, joined across lines and dehyphenated. So the pass takes the glyphs of the
page (`layout.page_glyphs`: the same raw extraction as layout's, in display space, with
the span's sRGB colour, spaces left out), keeps those whose centre lies inside the
element's box (1.5 pt slack), and aligns the element's text with them
(`text_pass.align`, `difflib.SequenceMatcher` over the characters; spaces and inline
markers are left out of the text side). The alignment is monotonic, so a dehyphenated
word, an extra glyph from another block inside a merged box or an inline equation's
glyphs only leave characters unplaced; an unplaced character never starts or ends a link
or a colour run on its own. Alignment runs only for elements a link rectangle touches,
and (with colour on) for elements that have glyphs in a run colour; text over 20,000
characters is not aligned.

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
| GoTo, or a named destination, on an analysed page | `Link(kind="internal", target_id=...)`: the element at the destination |
| GoTo to a page that was not analysed, a named action ("next page"), a link to its own element | nothing, and no notice |

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
caption ("Figure 2: ...").

hyperref links only the number of `Figure~\ref{fig}` or `\eqref{eq}`; such a PDF link is
widened to the whole reference ("Figure 1", "Equation (1)") so the link text says what
it points at, keeping the PDF's own target (`widen_internal`).

### Language

The document's language (`DocumentResult.lang`, `lang_source`):

1. the catalog `/Lang` when it is a tag whose language is a real two- or three-letter
   code (`language.document_tag`; "x-none" and "und" do not count): source "pdf";
2. else detected from the body text: source "detected" and the notice `lang_detected`;
3. else "en": source "default" and the notice `lang_assumed`.

Both notices are optional; the job page lets the instructor confirm or change the
language (`text_pass.set_document_language`, source "user").

Detection (`pipeline/language.py`) classifies words only: inline equations, numbers,
single letters (mostly variables), symbols, styled maths letters and code-like tokens
(anything with a digit, an underscore or a bracket in it) are left out. The document
needs at least 100 letters of such prose and a probability of 0.90; a language other
than English needs at least 250 letters and 0.99, and Latin and the constructed
languages (Esperanto, Volapük) are never taken from detection alone: py3langid reads a
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

| Detector | Licence | Installed size | Short academic sentences (24) |
|---|---|---|---|
| py3langid 0.4 (langid.py fork, naive Bayes on byte n-grams, 97 languages) | BSD-3-Clause | 4.5 MB model, plus NumPy | 24 right |
| lingua-py 2.2 (`lingua-language-detector`) | Apache-2.0 | about 290 MB of models | 23 right |
| fastText lid.176 | model CC BY-SA 3.0 | 126 MB (0.9 MB compressed) | not tried: a share-alike licence on the model |

The test sentences were English with maths ("Let f be continuous on [a, b]", "Office
hours: Tuesday and Thursday, 2 to 4 pm"), French, German, Spanish, Italian, Portuguese,
Dutch and a Latin tag. py3langid loads in about 0.3 s (once per process) and takes well
under a millisecond per paragraph. It unpacks its model through a temporary file; when
that cannot be written (a full temporary directory) the model is unpacked in memory
instead. If the detector still cannot run, the document gets the default language with
`lang_assumed` and no passage is marked, and the rest of the pass (links,
cross-references, colour runs) is kept: only the language depends on it.

### Colour (FEATURE_COLOUR)

With the flag on, a paragraph or list item gets `Element.colour_runs`: maximal stretches
of placed characters printed in one run colour, from the first to the last such
character, across an inline equation when the text on both sides has the colour, minus
any linked text (a link is written in the link colour), trimmed of spaces, and only with
a letter, a digit or maths in them. A colour is a run colour unless:

- it is the body colour: the commonest glyph colour of the analysed pages, when it reaches
  4.5:1 on white (a light body colour is not one, so light body text is kept and made
  darker);
- it is near black (OKLCH lightness below 0.2, whatever its tint) or a readable grey
  (chroma below 0.035 and 4.5:1 or more on white): it reads as body text;
- it is near white (lightness 0.96 or more): it was printed on a dark fill the output
  does not have, so it becomes body text.

A light grey is a run (it is a contrast problem: the output darkens it); so is any real
colour. Headings get no colour runs. Running text that names a colour ("in red", "shown
in blue", "highlighted in yellow", "the green curve", "red arrows") on or within two
paragraphs of coloured text on the same page gets the optional notice
`colour_reference`. With the flag off there are no runs and no such notices.

### Source facts

`text_pass.link_count(doc, pages)` counts the link annotations of any kind on the given
pages of an open PyMuPDF document (hold layout's lock): `SourceFacts.links`. The pass
also returns the same count for the analysed pages as `TextPassResult.annotations`.

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
72 dpi (`_probe_pages`), at most `PROBE_MAX_SIDE_PX` (2048) pixels on the long side, with
its address space capped at `PROBE_MEMORY_BYTES` (768 MB, plus twice the PDF's size):
MuPDF accepts page boxes far past the PDF maximum, and an 18000 pt page at 72 dpi was a
1 GB pixmap. The display list, where the crash is, is built whatever the zoom; a render
that runs out of memory under the cap is a Python error there and ignored like the
others. A child that dies (any non-zero exit) or runs longer than
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

PyMuPDF is not thread-safe, so every PyMuPDF call in `layout.py` runs under one module lock;
concurrent callers are safe but serialised (crop on one thread; a thread pool only helps the
model calls). `analyze` opens the document for that call only and closes it. `crop` caches
opened documents (4 at most), keyed by the identity of the bytes object and, failing that,
by its SHA-256, so cropping every element of a PDF parses it once. The cache holds user
content, so an entry unused for `DOCUMENT_CACHE_TTL_S` (120 s) is closed by a daemon timer
even if nobody calls `release`: an idle worker does not keep the last jobs' PDFs past the
retention purge. `pipeline.run.convert_pdf` also calls `layout.release(pdf_bytes)` in a
`finally` when the job ends (`release` is in the contract table of
`docs/design/beta-architecture.md`).

Crops render at `dpi` on a white background; when the long side would exceed `max_side_px`,
the zoom drops so it fits (one pixel of headroom absorbs MuPDF's rounding).

Speed (this machine, 2026-10-08): a 19-page DocBook spec analyses in 0.3 s plus the probe;
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
  `x = 5` typed as text is not recognised as maths unless a maths font or symbol is involved.
- **Plain-text maths in the body font** (`v = d/t` typed in Word without the equation
  editor) is found inside paragraphs only through the relation rule; `d/t` alone is not.
- **A display equation line with words** (`x = 1 if y > 0`) becomes a short paragraph with
  inline maths, not one display equation.
- **A CFF maths font in a Chromium print.** Chromium embeds a CFF (OpenType) maths font such
  as Latin Modern Math as unnamed Type3 glyphs, so the font name gives no maths signal. A
  display line such as `v(t) = −gt (time-dependent)` then reads as prose with inline maths
  and is merged into the paragraphs around it. A TrueType maths font that names itself
  (Noto Sans Math) is read correctly. Pinned by the strict xfail
  `tests/test_sign_check.py::test_a_display_equation_in_a_type3_maths_font_stays_display`.
- **A wrapped sentence whose last line is only maths** is read as a display equation when
  that line is indented or set off by extra space; at the paragraph's margin and line
  pitch it is inline.
- **Very short documents** learn the "typical line pitch" from very few lines; with one
  two-line paragraph the paragraph gap itself becomes the typical pitch and separate
  paragraphs can merge.
- **Maths inside headings and captions** stays as characters in their text (for example a
  math-italic `𝑔`); only paragraphs get inline equation elements.
- **Paragraphs split across pages** stay two elements (an element has one page and box).
- **Inline maths that wraps across lines** becomes two inline equations.
- **A lone bold letter** in running text (`for some nonzero vector x`, `\mathbf{x}` in
  CMBX) is not recognised as maths: one upright letter with nothing around it is too
  ambiguous.
- **Maths written as text** (OpenStax writes `d₀`, `t = 0 s`, `y = mx + b` in HTML, not
  MathML) is found and converted like any inline maths; the gold set has no MathML for
  it, so it counts against detection precision in the document eval (reported as
  `prose_math` when the prediction shows a formula: a relation with operands, a variable
  with an index or script, an italic variable with an operator; a bare quantity such as
  `-49 m` or a lone symbol is `other`).
- **Lists:** a marker on a line of its own above its text (OpenStax prints exercise
  numbers that way) is not an item: the number stays at the start of a paragraph. Bullets
  that are raster images are not recognised. A list that the PDF numbers with a gap, or
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
  they come out as figures.
- **Drop caps** (a huge first letter) become a one-letter paragraph.
- **Hyphenated compounds across lines** (`well-` / `known`) lose the hyphen: without a
  dictionary they look like a broken word.
- **Plain body-size numbered headings with a bare integer** (`3 Forces` in body text,
  not bold) are paragraphs: at body size a bare integer is too often a quantity. They
  are found when bold (as weak evidence) or in a larger font.
- **Bold table header cells** (`Time`, `Distance`) of a table the detector missed can
  still become weak headings (an optional note; a group decision if the same header
  repeats); numeric cells no longer do. In a detected table they are header cells.
- **Bold run-in labels** ("Solution", "Discussion", "Procedure") become weak headings;
  see "Run-in labels" above for why no rule removes them.
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
- `tests/test_layout_lists.py` (bullets, ordered markers, siblings, nesting, false lists),
  `tests/test_layout_equations.py` (display-equation regions and equation numbers) and
  `tests/test_layout_fragments.py` (inline fragments and the punctuation after inline
  maths) pin the layout v2 fixes from the eval pages and the smoke test.
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
- `tests/test_tables_detect.py` and `tests/test_tables_fixtures.py` cover table
  detection and how layout claims tables, on generated pages, the LaTeX
  `latex/tables.tex` (Tectonic) and a Word-style page exported by LibreOffice
  (`tests/fixtures/tables/`); see [tables.md](tables.md).
- `tests/test_layout_latex.py` checks four documents written for the tests and compiled
  by Tectonic, in `tests/fixtures/layout/latex/`: article notes (numbered equations,
  `align`, limits, inline integrals, nested itemize/enumerate, a TikZ figure), a problem
  set ((a)/A./(i) lists, a three-row derivation, `2.1`-style numbers), beamer slides
  (triangle bullets, sans-serif maths, a captioned figure) and a two-column handout
  (matrices, bold vectors, `\neq`, a numbered projection). The `.tex` files are the
  source; rebuild with `uv run python tests/fixtures/layout/latex/make_pdfs.py --tectonic
  PATH` (deterministic, byte-identical with the same Tectonic version; PDFs must stay
  under 300 KB).
- The text pass: `tests/test_text_pass.py` (alignment with inline markers, link
  rectangles, unsafe schemes, destinations, cross-reference edge cases, colour rules,
  the language and its change, on glyphs built in the test), `tests/test_text_language.py`
  (detection on short and maths-heavy text, tags), `tests/test_text_contrast.py`,
  `tests/test_text_inline.py` (the HTML), and `tests/test_text_latex.py` on
  `links_language_colour.tex` in the LaTeX fixtures folder (hyperref links, `\ref`
  cross-references, a French quotation, coloured text) and on PDFs built in the test
  (rotated pages, a named destination, `/Lang`); its `browser` tests run axe-core on the
  converted file and check that a long link wraps at 320 px.
