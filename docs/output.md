# The output HTML

What `pipeline/assemble.py` writes for one PDF, why it looks that way, and which checks run
on it. The pipeline that feeds it is in `docs/design/beta-architecture.md`.

## One self-contained file

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="ReadableSTEM 0.1.0">
<title>Speed and Velocity</title>
<style>/* ~25 lines, see "Styling" */</style>
</head>
<body>
<main>
  <p class="notice readablestem" role="note">...</p>   (only when a notice is given)
  ... the document, in reading order ...
</main>
<footer id="about-conversion" ...>About this version (the note for students)</footer>
</body>
</html>
```

There are no scripts and nothing is loaded from elsewhere: images are `data:` URIs and the
stylesheet is inline. The file opens offline, can be uploaded to a course or emailed,
and opening it never sends a request anywhere. `html_checks.static_checks` fails the
`external_resource` rule if that ever changes. It must not be pasted into an LMS page
(see "Posting it in a course").

- `lang` is `DocumentResult.lang`: the PDF's `/Lang`, else the language detected from the
  text, else `en` (see "Language"). Anything that is not a language tag falls back to
  `en`, so a crafted PDF cannot inject attributes.
- Links (`<a href>`, see "Links and cross-references") load nothing: following one is
  the reader's choice, and opening the file still sends no request.
- `<title>` is the PDF title or the first level-1 heading, else "Converted document".
- Every element keeps its pipeline id (`id="p3-e7"`), so a report or the review screen can
  link to `#p3-e7`. An id is used once even if layout produced a duplicate.
- If two fixes name the same element, the first one wins, as in `DocumentResult.fix_for`.
- An optional plain-text `notice` opens `<main>` as a labelled, outlined note ("Note:
  ..."). The CLI uses it for `--fake` runs, whose answers are placeholders: the file has
  to say so before any content, because the CLI has no review step.

## Posting it in a course

The file is meant to be uploaded as a file and linked, not pasted into a page (for Canvas
pages there is a separate export, below). Pasting
breaks it: Canvas's sanitizer keeps only http, https and relative image sources, so every
`data:` image (every figure and every equation kept as an image) is dropped, and it
removes the `<style>` block, `<main>` and `tabindex`, so the hidden "Equation" labels
show, printed list markers double ("1. (a)") and wide equations lose their scroll box
(read from Canvas's sanitizer allowlist, FEATURE-REVIEW "Beta 5"; not yet tried in a live
course). Pipeline 0.4.0 output loses more: the colour of coloured text
(FEATURE_COLOUR's runs are `.tc-*` rules in `<style>`, so "the terms shown in red"
then points at nothing), the `headers` attribute (allowed on neither `td` nor `th`), so
an irregular table's header associations go, and the tab stops of wide tables' and
chart data tables' scroll boxes (`tabindex`); `details`, `summary`, `caption`,
`colgroup` and `math` survive. Moodle's HTML purifier drops `data:` images and MathML. The app's help page
`/help/posting` (`web/templates/help_posting.html`) gives the steps for Canvas (upload to
Files, link it where the PDF was, check it in Student View), says to make the HTML the
main version, with the PDF kept only as a labelled print copy (28 CFR 35.202 and the
Justice Department's fact sheet on the rule), and that it is guidance, not legal advice. The job
page links to it under the download link. The steps are stated as not yet tried in a live
Canvas course; once they have been (Canvas Lite, then UTD), update both pages and the Canvas
export's.

### The Canvas export (`pipeline/export_canvas.py`)

The paragraph above is about the file as written. For Canvas there is a second form of the
same document, made from the finished HTML alone (no pipeline step changes, and the
default file is byte for byte as before; the golden tests cover that). The job page's
"For Canvas (zip with images)" (`GET /jobs/{id}/download/canvas`, owner only; optional
`?course=<digits>`) returns a zip:

| File | What it is |
|---|---|
| `index.html` | The whole document with no `<style>` block. Each rule of the stylesheet is written into `style` attributes (selectors by tag, class, attribute, descendant and child, in cascade order), keeping only properties Canvas keeps. Every `data:` image is `images/image-NN.png` (or `.jpg`; one file per distinct picture). `tabindex` and `headers` stay, because a file is not sanitized. |
| `images/` | The pictures, as files. |
| `canvas-page.html` | Only with a course number. The same content as a fragment: no `<html>`, `<head>`, `<title>`, `<main>`, inside `<div lang>`; no `tabindex` and `headers` (Canvas removes them, so the file says what survives); rules for bare `p`, `h1`-`h6`, `ul`, `ol`, `li` left to Canvas's own styles; headings one level down (Canvas shows the page title as the top heading) unless the document has an `h6`; pictures at `/courses/<number>/file_contents/course%20files/<folder>/images/image-NN.png`. Not offered when it would exceed a Page's 500 KB body limit. |
| `README.txt` | Plain steps for the instructor, with the folder name and course number filled in, and only the "what Canvas still changes" notes that apply to this document. |

The folder name is `<ascii title>-<first 6 characters of the job id>` (`folder_name`). The
instructor makes that folder in Files, uploads the zip into it and expands it, so the
pictures sit where `canvas-page.html` says. Canvas resolves
`/courses/<id>/file_contents/<path>` through the folder tree
(`config/routes.rb`, `Folder.find_attachment_in_context_with_path`), so the address does
not depend on the file's id. Whether the pictures of an uploaded `.html` file load in
Canvas from relative addresses is not settled by Canvas's code, so the README does not
promise it and offers the Page as the route.

What Canvas decides is read from its source, not memory: the allowlist of
`gems/canvas_sanitize/lib/canvas_sanitize/canvas_sanitize.rb` in instructure/canvas-lms at
commit `c4912103269a6afa9e6957460b6d2bc6a1d8605f` (blob `1c70bcfb`, last changed
2026-03-19; unchanged on master `1c9f0bb8` on 2026-10-14), with the `sanitize` gem 7.0.0 default for what
happens to a removed element (`script`, `style`, `svg` go with their contents; `main`,
`html`, `head` and `body` are dropped and their text stays; a `<title>`'s text would show
in the page). `tests/fixtures/canvas/extract_allowlist.py` writes it to
`canvas_sanitize_allowlist.json` (elements, attributes by element, protocols, CSS
properties), and `tests/canvas_sanitizer.py` applies it. `tests/test_export_canvas.py`
runs the export of the demo result, the golden files and the LaTeX fixtures (every content
feature on) through it and asserts that the paste page loses nothing (sanitizing it changes
no byte) and that every picture keeps its alt text and an address Canvas keeps, every
equation its MathML, and headings, tables (caption, `th`, `scope`, spans), links, ids,
language and text are as in the source.

The equations carry a maths font stack in the Canvas versions (T3-8,
`export_canvas.MATH_FONT_STACK`): `font-family` on each `<math>`, written as a `style`
attribute like every other carried rule. Canvas keeps `font-family` (it is in its list) but
has no style block and no `@font-face`, so the stack can only name fonts the reader's
computer has; native MathML in Chrome, Edge, Firefox and Safari needs a font with an
OpenType MATH table, and the stack lists the families that have one, with the browser's own
`math` family last. The default student file is unchanged (the golden files cover that), so
it uses the browser's default maths font as before. If Canvas draws the equations with its
MathJax instead, the stack is not used; `docs/canvas-sandbox-test.md` has the step that
shows which happens.

### The Common Cartridge export (`pipeline/export_cartridge.py`, T3-8)

The job page also offers "Download as a Common Cartridge (.imscc)"
(`GET /jobs/{id}/download/cartridge`, owner only): an IMS Common Cartridge 1.3 package that
Canvas, Blackboard, Moodle and D2L import. It wraps the Canvas export's `index.html` and
pictures; nothing in the text, MathML, alt text, headings, tables or links is changed.

| File | What it is |
|---|---|
| `imsmanifest.xml` | Metadata (schema `IMS Common Cartridge`, version `1.3.0`, the document's title and language), one organization (a rooted hierarchy: an empty root item holding one item, titled as the document, that points at the page), and one resource of type `webcontent` listing the page and its pictures |
| `web_resources/<folder>/index.html` | The Canvas version's page (styles written into the page, the maths font stack, pictures as relative files) |
| `web_resources/<folder>/images/image-NN.png` | The pictures |

The folder is the same `<ascii title>-<first 6 characters of the job id>` as the Canvas zip,
so two documents imported into one course do not overwrite each other. The same document
gives the same bytes. `check_cartridge` checks a package against the rules of the CC 1.3
profile that apply to this content (the list is in its docstring: root element and namespace,
schema and version, a titled LOM record, one rooted-hierarchy organization, items that point
at resources, `webcontent` resources whose `href` is one of their files, every listed file
present and every file listed, relative paths with plain characters, unique XML IDs), and
`tests/test_export_cartridge.py` runs it on every document the Canvas export is tested on and
breaks each rule once to show it is noticed. It is **not** the IMS validator: the official
XML schemas are not in this repository and nothing here fetches them. Whether a particular
LMS brings the page in as a file, a page or a module item is not settled by the format; the
sandbox steps in `docs/canvas-sandbox-test.md` record what Canvas does.

What Canvas still removes, and why that is accepted:

- `tabindex` (not allowed on any element): the sideways-scroll boxes of wide tables and
  equations cannot be focused by `tabindex` in a pasted page. Chrome and Firefox make a
  scroll container reachable by keyboard when it has nothing focusable inside; Safari may
  not. The README names the count of such boxes. `index.html` keeps the attribute.
- `headers` on `td`/`th` (not allowed): an irregular table loses its explicit cell to
  header links in a pasted page; `scope`, `colspan`, `rowspan` and `caption` stay. The
  README names the count of such tables. `index.html` keeps it.
- `font-weight`, `overflow-wrap` and `caption-side` (not in Canvas's CSS list; the browser matrix test finds the one visible effect, a long unbroken web address in the source note running past a 375 px screen in the standalone `index.html`): bold on the
  "Not converted." label, table captions and figure-description summaries shows in normal
  weight, and a very long unbroken string may not wrap. The label's text and the border
  still carry the meaning ("never by colour alone").
- `:focus-visible`, `@media print`, the dark-scheme block and the narrow-screen margin of
  the about section: Canvas has its own focus rings, colours and layout.
- `.visually-hidden` uses `clip: rect(0,0,0,0)` instead of `clip-path` (not allowed), plus
  Canvas's own `screenreader-only` class, so the hidden "Equation" labels stay hidden.
- MathML is kept (all presentation elements the pipeline writes are on the list). Canvas
  draws it with its own MathJax when the page has equations (`enhance-user-content/mathml.js`);
  that happens in the browser and is not tested here.
- Not tested against a live Canvas, UT Dallas's included; the README says so.

## How each element is written

| Element | Converted | Not converted (ABSTAINED, FAILED, REJECTED, no fix) |
|---|---|---|
| Heading | `<h1>`-`<h6>` from the heading fix | `<p>` with the original text; a bold line a reviewer rejected ("Make them bold text") is `<p><strong>`, bold as in the PDF |
| Paragraph | `<p>`, inline equations placed at their markers | (no fix) |
| List item | `<li>` in a `<ul>`/`<ol>`, inline equations placed at their markers (see "Lists") | (no fix) |
| Inline equation | `<math display="inline">` inside the paragraph or list item (a wide one in a `<span class="equation-scroll">`, see "Wide equations") | the crop inline, outlined, with "(equation not converted)" |
| Block equation | `<div class="equation"><math display="block">`, then its number if it has one | `<div class="flagged">`: the crop, its number, a note |
| Program code (T2-16) | `<pre><code>` with the lines and indentation as printed, no highlighting; a block with a line over 36 characters is `tabindex="0" role="group" aria-label="Program code"` so it can be scrolled by keyboard | (no fix) |
| Glossary entries (T2-16) | consecutive entries as one `<dl>`: the term in `<dt><strong>`, the definition in `<dd>` (see docs/layout.md "Glossary entries") | (no fix) |
| Contents page (T2-17) | a list (`<ul>`, nested by indent) of the entries' titles, each a link to the heading it names when there is one; no dot leaders, no page numbers | (no fix) |
| Figure | `<figure><img alt="..."><figcaption>`, then `<details class="figure-description">` when it has a long description or a data table (see "Long descriptions and chart data") | `<div class="flagged">`: the figure with alt "Figure without a description", plus a note |
| Page kept as an image (pipeline 0.4.0: handwritten, scanned, nothing readable) and handwriting on a typed page | (never converted) | `<div class="flagged" data-reason="handwritten_page">` (or `scanned_page`, `unread_page`, `handwriting_on_page`): the page image with alt "Page 2: handwritten notes, not converted to text." ("Page 5: a scanned page, ...", "Page 3: not converted to text.", "Handwriting on page 3, not converted to text."), the label "Not converted." and a note: "This page is a picture of handwritten notes, so a screen reader cannot read it. Ask your instructor for a text version." (see "Flagged items") |
| Decorative figure | `<div class="decorative"><img alt="">` (a `<figure>` if it has a caption) | |
| Table (pipeline 0.4.0) | `<div class="table-scroll" role="group" tabindex="0">` around a `<table>` (see "Tables"); a grid that is not a table of data is its text in reading order: a grid of terms as a `<ul>` (layout), one the model calls not data as a `<dl>`, `<ul>` or paragraphs in a `<div id>` | `<div class="flagged">`: the image of the table with alt "Table not converted", its caption, a note (`data-reason="table_not_rebuilt"` while rebuilding tables is off) |

A trimmed excerpt of `tests/fixtures/assemble/sample_document.json` after assembly:

```html
<h1 id="p1-e0">2.2 Speed and Velocity</h1>
<p id="p1-e1">Average speed is distance over time, <math display="inline" id="p1-e2">
  <mrow><mi>v</mi><mo>=</mo><mfrac>...</mfrac></mrow></math>, where
  <span class="flagged-inline" id="p1-e3" data-status="abstained" data-reason="low_confidence">
    <img src="data:image/png;base64,..." alt="Equation not converted" width="16" height="16">
    <span class="flag-label readablestem" aria-hidden="true">(equation not converted)</span></span>
  is the distance travelled.</p>
<div class="equation" id="p1-e4"><math display="block">...</math></div>
<div class="flagged" id="p1-e5" data-status="failed" data-reason="budget_job_usd">
  <img src="data:image/png;base64,..." alt="Equation not converted" width="213" height="53">
  <p class="flag-note readablestem" role="note"><span class="flag-label">Not converted.</span>
  This equation is shown as an image of the original, so screen readers cannot read it.
  Ask your instructor for a version you can read.</p></div>
<figure id="p1-e6"><img src="data:image/png;base64,..." alt="Line graph of position against
  time: a straight line rising from the origin." width="437" height="267">
  <figcaption>Figure 2.5 Position against time for a cart moving at constant velocity.</figcaption></figure>
```

## Tables

`pipeline/table_html.py` (`render_table`) writes every data table: the rebuilt tables
and, later, the data tables of charts. It follows the W3C WAI tables tutorial:
`<caption>` only when the PDF had a "Table N" caption, the header rows in `<thead>`,
`<th scope="col">` (`"colgroup"` with `<colgroup>` elements for a spanning header in the
first row), `<th scope="row">`, a body split into one `<tbody>` per section with
`scope="rowgroup"` on a full-width section heading, and `headers`/`id` only when scope
cannot say which headers apply (a row header after data cells, a column header below the
header rows, or nested column headers whose spans cross). Cell text and captions are
escaped; a maths cell is MathML rebuilt by `sanitize_mathml` (its text otherwise). A
header cell that is only maths also gets its reading (`speech.mathml_to_speech`) as
visually hidden text, with the MathML hidden from assistive technology: browsers name a
`<th>` from its text, never from MathML, and screen readers announce a cell's headers by
name (the static rule `html.table_header_text` does not count MathML as header text). A
broken grid (`core.types.grid_problems`) still renders as a rectangular table.

`pipeline/assemble_tables.py` wraps a rebuilt table in a box that scrolls sideways on a
narrow screen; a table that may be wider than a 320 px screen
(`estimate_min_width_em` >= `WIDE_TABLE_EM`) is also a tab stop, named by the caption
(else "Table"), so keyboard users can scroll it, as wide equations are. A proposal without
a caption gets the element's "Table N" caption (layout took it out of the text). Any table
that was not rebuilt keeps its image, flagged. Table CSS is added only to documents with
a rebuilt table, so other documents are byte-for-byte what 0.3.0 wrote. The table step,
its checks and the width estimate's calibration: [tables.md](tables.md).

## Long descriptions and chart data

With long descriptions on (FEATURE_LONG_DESCRIPTIONS, docs/steps.md), a chart, diagram
or complex illustration can have a long description and a chart its data table
(`FigureFix.long_description`, `data_table`). Following the W3C WAI complex images
tutorial (a short alt on the image, a long description next to it), they are written
right after the figure, folded, by `pipeline/figure_details.py render_details`:

```html
<figure id="p3-e0"><img src="data:image/png;base64,..." alt="Line graph of position
  against time: a straight line rising from 400 m." width="437" height="267">
  <figcaption>Figure 2.3 ...</figcaption></figure>
<details class="figure-description"><summary class="readablestem">Description of Figure
  2.3</summary><p>Line graph of the position of a car against time.</p><p>...</p>
  <div class="table-scroll" role="group" aria-labelledby="p3-e0-data-caption" tabindex="0">
  <table><caption id="p3-e0-data-caption">Data read from Figure 2.3; the values are
  approximate</caption><thead>...</thead><tbody>...</tbody></table></div></details>
```

- The `<details>` follows the `<figure>` instead of sitting in it: a `<figcaption>` must
  be the figure's first or last child, and a long description inside the figcaption
  would become the figure's accessible name, read in full on entering it.
- The summary names the figure from its caption ("Description of Figure 2.3", "Data for
  Figure 2.3" when there is only a table); a figure without a number is named by the
  first eight words of its alt text ("Description of the figure “Line graph of the
  position of a car…”"), and only one without either is "the figure". So
  several unnumbered figures' summaries still differ in a screen reader's list of
  controls. The data table's caption names the figure the same way. It works without
  scripts.
- Each line of the long description is a `<p>`; everything the model wrote is escaped.
- The data table goes through `table_html.render_table` like a rebuilt table, in a box
  that scrolls sideways and takes focus, named by its caption. It is written only when
  `figure_details.data_table_shown`: every number is in the PDF's text ("Data shown in
  Figure 2.3"), or the values were read from the image and a reviewer chose "Add the
  data table" (`FigureFix.data_table_added`; "Data read from Figure 2.3; the values are
  approximate"). A table read from the image that nobody added stays on the fix as an
  optional extra and is not in the file (a figure with only that has no `<details>`).
- Nothing is written for a decorative figure or a proposal that is not used (rejected,
  failed, abstained). Its CSS (`DETAILS_CSS`, plus the table CSS with a table) is added
  only to documents with one.

## Figure images in the student file (UX review MF23)

Figure images are re-encoded for the student file only (`pipeline/figure_images.py`;
the stored crops and the review screen keep their PNGs). At most 1600 px on the long
side, never upscaled; photos and illustrations as JPEG at quality 82 unless the PNG is
smaller; charts, diagrams and anything else (flagged figures and scanned pages too) as
PNG unless the JPEG is at most half its size, which happens for raster charts that are
photographs or JPEG scans; an image with transparency is kept. MuPDF does the encoding,
so there is no new dependency, and the bytes depend only on the crop and the figure
type, so a stored result re-renders to the same file. Equation images are not touched.

| File | Before | After | Images |
|---|---|---|---|
| The public demo (OpenStax Physics 2.3, 7 figures; stored result re-rendered with its crops) | 2,210 KB (gzip 1,657 KB) | 666 KB (gzip 460 KB) | 3 JPEG (a photo, two raster charts), 4 PNG |
| The committed demo page, rebuilt on 0.5.0 (2026-10-14, with Claude, 25 calls, $0.105; 2026-10-09 with OpenAI and the 0.3.0 build before), written again by the Tier 3 assembler from the stored result (`readablestem demo rerender`, no model call) | 2,211 KB (gzip 1,657 KB) | 668 KB (gzip 461 KB) | the same 3 JPEG and 4 PNG |
| `tests/fixtures/layout/` PDFs converted offline (`lecture_notes`, `two_column`, `worked_example`; one small figure each) | 35, 14, 40 KB | the same | PNG kept: line art, smaller than JPEG |

A page-sized crop (1700 x 2200 px) takes about 0.15-0.3 s to re-encode, so a document of
many scanned pages takes a few seconds more to re-render after each review decision.
The UX review expected about 250 KB for the demo with WebP, which MuPDF does not write.

## Lists

Layout reports each list item as an element of its own (`Kind.LIST_ITEM`) with the marker
as printed (`list_marker`: "•", "a.", "(iii)"), `list_ordered` and `list_level`
(docs/layout.md, "Lists"). The assembler turns consecutive items back into lists:

- An item one level deeper nests inside the `<li>` before it. Levels are taken relative
  to the first item and never go more than one deeper than the item before, so every
  nested list has a parent item (a list starting at level 2, or a jump from 1 to 3,
  still nests correctly).
- At one level, items of one family share a list: bullets; numbers printed the same way
  ("1." "2."); letters or roman numerals printed the same way ("a." "b.", "(i)" "(ii)").
  A different family starts a new list. The glyph of a bullet is not kept: browsers
  choose disc, circle and square by nesting depth.
- A regular ordered list (markers counting up by one, ending in a full stop) is an `<ol>`
  with `type="a"`, `"A"`, `"i"` or `"I"` and `start` when it does not start at 1, and the
  browser prints the markers. Single letters are letters ("h. i. j." is h to j);
  otherwise roman numerals ("i. ii. iii.").
- Anything else ("(a)", "3)", a gap in the numbering, mixed case) keeps the printed
  marker as text in `<span class="list-marker">` inside a list with `list-style: none`
  and `role="list"` (Safari drops the list semantics of a list without a list style).
- A paragraph or heading between items ends the list; a numbered list that resumes
  after it gets `start`, so the numbers stay right. A display equation between two items
  is written inside the item before it (after its text, before any nested list), so the
  list and its nesting go on after it, as layout reports them. A display equation after
  the last item stays outside the list.

```html
<ol><li id="p1-e34">Compute <math ...>...</math> with the fundamental theorem.</li>
<li id="p1-e36">Show that<ol class="printed-markers" role="list">
  <li id="p1-e37"><span class="list-marker">(a)</span> <math ...>...</math>, and</li>
  <li id="p1-e39"><span class="list-marker">(b)</span> the area under ...</li></ol></li>
<li id="p1-e44">Estimate the integral in the first exercise with four strips.</li></ol>
```

## Equation numbers

A display equation's number (`Element.equation_label`, "(2.4)", "2.4", "Eq. 2.4") is not
part of its crop or its MathML: layout keeps it out (docs/layout.md). The assembler writes
it after the maths, as printed, with a visually hidden "Equation " in front so a screen
reader says "Equation (2.4)" rather than a bare number:

```html
<div class="equation equation-labelled" id="p3-e5"><math display="block">...</math>
  <span class="equation-label"><span class="visually-hidden readablestem">Equation </span>2.4</span></div>
```

The wrapper is a flex row that wraps: the maths takes the remaining width and stays
centred, the number sits at the right. The maths never shrinks below its own width, so
when both do not fit (a long equation on a 320 px screen) the number moves to the next
line, right-aligned, instead of being painted over (WCAG 1.4.10). An equation that was
not converted keeps its number too, after the original image in the flagged box.

## Wide equations

An equation cannot wrap, so one wider than the screen scrolls sideways in its own box
and the page does not (WCAG 1.4.10, reflow at 320 CSS px). Every display equation's
`<div class="equation">` scrolls when it has to (`overflow-x: auto`). A box can only be
scrolled from the keyboard if it can take focus, but a tab stop on every equation makes
a long document tedious to tab through, so only equations that may be too wide for a
phone become named tab stops:

```html
<div class="equation" id="p3-e5" tabindex="0" role="group" aria-label="Equation (2.4)">
  <math display="block">...</math>...</div>
<p>The sum <span class="equation-scroll" tabindex="0" role="group" aria-label="Equation">
  <math display="inline" id="p3-e7">...</math></span> is long.</p>
```

Which ones is an estimate from the MathML (`assemble.estimate_width_em`: token widths,
fractions as wide as their wider part, scripts at 71%, table columns): at least
`WIDE_EQUATION_EM` (11 em of the maths font), against the 14.5 em that fit in the 288 px
column of a 320 px screen. On the gold equations the estimate was within -18% and +44%
of Chromium's widths; the margin covers other maths fonts. A wide inline equation gets
its own scroll box (`.equation-scroll`, an inline block); short ones stay plain, since
a scroll box sits on the line differently. `tests/test_assemble_wide.py` checks in
Chromium at 320 px that the page does not scroll sideways, that every gold equation
that overflows is a tab stop, that arrow keys scroll it, and axe.

## Flagged items

Anything the pipeline could not convert keeps the original crop. Sighted readers lose
nothing, and screen reader users hear a fixed, honest alt text ("Figure without a
description") and a note, instead of a guess read aloud as if it were right.

An equation kept as an image (`assemble.equation_alt`) has the text the PDF holds for its
region as alt text, with a marker that says where it comes from and that nobody has checked
it: `6.0 m/s, read from the PDF text; not checked`, or `Equation (2.4): v = d / t, read from
the PDF text; not checked` when it has a number. Where layout saw fractions, roots,
exponents or several lines in the region, the marker adds that the text may run them
together (`v2 = v20 + 2ad` for v squared = v0 squared + 2ad), because the flat text layer
loses their order and size; an inline equation's exponents are not always known, so
`g = 9.8 m/s2` can be one. Without usable text (none, no letter or digit, or more than 200
characters) the alt text is `Equation (2.4), not converted` (the number as printed) or
`Equation not converted`. Nothing is written that the PDF does not hold. The visible note
and label are unchanged. In a document in another language the alt text is marked
`lang="en"`, as the other fixed words are.

- The flag is a dashed border **and** a text label, never colour alone (WCAG 1.4.1). The
  border survives forced-colours mode and printing.
- The note is a `<p role="note">`. On an inline equation the visible label is
  `aria-hidden`, because the alt text already says the same thing.
- `data-status` and `data-reason` carry content-free codes for tooling. Reasons come from
  the steps (e.g. `low_confidence`), from the run (`budget_daily`, `budget_job_usd`,
  `budget_job_calls`, `internal_error`, `crop_error`) or from assembly (`no_fix`,
  `no_output`, `mathml_rejected`).
- If there is no crop, the extracted text (if any) is shown in monospace with "The
  original image is not available."
- A page (or handwriting) kept as an image is never called a figure: its alt text names
  the page and what it is, and its note says a screen reader cannot read it and to ask
  the instructor for a text version (`assemble.KEPT_IMAGE_WORDS`, by the fix's reason,
  so a stored result renders the same; the design's draft wording is the beta wording).
  Hidden text from such a page is never in the file: layout never reads it.
- NEEDS_REVIEW fixes are shown as converted and not flagged here: the review screen is
  where they are handled. So are fixes accepted with an optional note
  (`Fix.review == "optional"`, docs/design/beta-architecture.md "Review tiers").

## Equations kept as pictures (T3-15)

An equation that was not converted is shown as the crop of the PDF
(`pipeline/equation_scale.py`). Two things are done for it:

- **Text size.** The picture is shown at the size its text would have in the file, not at
  the size it was printed: scaled by 18 CSS px (the file's text) over the PDF's text size
  at 4/3 CSS px a point. The PDF's text size is that of the paragraph an inline equation
  stands in, the equation's own size for a display equation when it is within 0.6 to 1.8
  times the body size, else the document's body size (the size most paragraph and list
  text is set in). A 10 pt handout's pictures are shown 1.35 times their printed size, a
  12 pt one's 1.125 times, a slide deck's 24 pt text brings them down to 0.56 times. The
  scale is kept between 0.5 and 2, and is 1 when the document has no text to measure by.
  Width and height are set together from the crop's pixels, so the picture keeps its
  shape (it was set from the equation's box before, which left out the 2 pt of margin
  the crop has: small pictures were squeezed).
- **Sharp.** The crop is cut at three image pixels for each CSS pixel it will take
  (`crop_dpi`: 288 dpi times the scale), not at the 200 dpi of other crops, and says so in
  its PNG header (pHYs), which is how the assembler knows to size it from its pixels. The
  model is sent the same 200 dpi crop as before (a second cut, so what the model reads is
  unchanged); the stored crop, which the review screen also shows, is the sharp one. A
  result stored before this has untagged crops and gets the printed size times the scale.

The cost is size: the 14 flagged equations of the problem-set fixture take 3 KB more each
(60 KB to 102 KB for the file). Figures, tables and pages are not changed.

## Inline equations

Layout writes a marker such as `⟦p1-e7⟧` (`core.types.inline_marker`) into the paragraph
text where an inline equation sits, and gives the equation `parent_id` = the paragraph.
The assembler replaces each marker with the equation's MathML, or with the flagged crop.

- Markers are honoured in paragraphs, list items, headings and figure captions.
- An equation whose marker is not found is rendered at its own position as a block, so
  nothing is lost.
- A marker with no matching equation is left as text. That only happens if layout has a
  bug, and leaving it visible makes the bug easy to spot.

All running text (paragraphs, list items, headings, captions) goes through
`inline_text.render_inline_text`, which places the equations and, for an element's own
stored text, writes its links and colour runs. Text without either is written exactly
as before pipeline 0.4.0.

## Links and cross-references

The text pass (docs/layout.md "The text pass") puts `Element.links` on paragraphs, list
items and headings; offsets are into the stored text, markers included.

```html
<p id="p1-e1">The <a href="https://example.org/physics/syllabus">course syllabus</a> lists
  the reading, and questions go to <a href="mailto:instructor@example.edu">the instructor
  by email</a>.</p>
<p id="p1-e10">Eliminating <math display="inline">...</math> between
  <a href="#p1-e7">Eqs. (1)</a> and <a href="#p1-e9">(2)</a> gives ...
  <a href="#p1-e12">Figure 1</a> shows the cart.</p>
...
<figure id="p1-e12"><img ...><figcaption>Figure 1: A cart on a ramp.</figcaption></figure>
```

- **Web and email links** ("uri") keep the PDF's visible text as the link text, never an
  `aria-label` in its place (WCAG 2.5.3, label in name), and are written only while
  `href` is http, https or mailto (`core.types.is_safe_href`, tested again when the
  file is written). Any other PDF link (ftp, a file, a program, javascript) is not
  written; its text stays, and the job page lists the notice `link_scheme_dropped`.
- **Links inside the document** ("internal": a PDF link to a place in it; "crossref":
  "Figure 2.3", "Eq. (3)", "Table 1" in the text) point at `#` + the target element's id.
  Figures, tables, display equations, headings, paragraphs and list items already carry
  their ids, so nothing new is written at the target.
- A link never wraps an inline equation: a wide one is a tab stop of its own, and a tab
  stop cannot sit inside a link (axe `nested-interactive`). A link across an equation is
  written in two pieces around it.
- A span that starts or ends inside a marker, reaches past the text, or overlaps an
  earlier link is skipped, so a stored result edited by hand cannot break the markup.
- Links keep the browser's link style (colour and underline), so they are not told apart
  by colour alone. A long URL as link text wraps (`overflow-wrap: break-word` on
  `<main>`), so the page still reflows at 320 px.
- Link text that does not say where it goes ("click here", a long bare URL with next to
  no text around it) is kept as printed, with the optional notice `link_text_unclear`:
  only the instructor can reword it.

## Language

`<html lang>` is the document's language. A paragraph or list item that the text pass
found confidently in another language carries its own (`Element.lang`), written by
`inline_text.passage_attrs` on its block:

```html
<html lang="en">
...
<p id="p1-e15" lang="fr">La vitesse moyenne est le quotient du déplacement par la durée
  du trajet, et elle s’exprime en mètres par seconde.</p>
```

A screen reader switches voice for that paragraph (WCAG 3.1.2), and MathCAT speaks maths
in the page's language. Where the document language came from is
`DocumentResult.lang_source`: "pdf" (`/Lang`), "detected" (from the body text, notice
`lang_detected`), "default" ("en" because nothing else was found, notice `lang_assumed`)
or "user". The job page shows the language and lets the instructor change it: the route
calls `text_pass.set_document_language(result, tag)` (a BCP 47 tag such as "fr" or
"pt-BR", or an English name such as "French"; ValueError otherwise), which sets the
language and source "user", drops the two notices and works out the passages again
against the new language, then re-renders the file with `web.review.rerender_html` and
stores both, as after a review decision.

Text the tool itself writes (the notes, "Equation" before an equation number, the about
section) is English whatever the document's language.

## Raised and lowered text

Raised and lowered text of upright text (`Element.script_runs`: H2O, Na+, g/cm3, 226Ra) is
written as `<sup>` and `<sub>` inside the paragraph, list item or heading, inside any link or
colour:

```html
<p id="p1-e2">Water is H<sub>2</sub>O, table salt is Na<sup>+</sup> and gold is 19.3 g/cm<sup>3</sup>.</p>
```

A lowered and a raised run side by side (the count and the charge of SO4 2-, the two
numbers before a nuclide's symbol) are one stacked pair, with a rule added to the document
only when it has one. The two stay in the order of the text, which is the order a screen
reader says them in (4, then 2-; the mass number, then the atomic number); the raised one is
drawn on top whichever comes first (`flex-direction: column-reverse` when the lowered one does):

```html
SO<span class="script-stack script-stack-low-first"><sub>4</sub><sup>2-</sup></span>
```

The text itself is unchanged (`H2O`), so search and copy read as before. A run is skipped
when it would start inside an inline equation's marker or overlap another. What cannot be
carried (a figure caption, a table cell's lowered text, a script with nothing before it) is
written flat, and the notice `scripts_flattened` ("Worth checking") says where.

## Coloured text (FEATURE_COLOUR)

With the flag on, text printed in a colour other than the body's keeps a colour in the
output (`Element.colour_runs`; which colours count is in docs/layout.md "Colour"). Each
printed colour gets one class, `tc-` and its hex digits, and one CSS rule added only to
documents that have it:

```html
<style>
...
.tc-d2d2d2 { color: #767676; }
.tc-ff0000 { color: #ee0000; }
</style>
...
<p id="p1-e17">In these notes, <span class="tc-ff0000">the vectors are printed in
  red</span> and scalars in black. <span class="tc-d2d2d2">A light grey hint follows
  each problem.</span></p>
```

The rule's colour reaches 4.5:1 on the white background (WCAG 1.4.3): a colour that
already does is kept exactly; any other is made darker, keeping its hue, in OKLCH
(`pipeline.contrast.with_contrast`: lightness lowered until the contrast is reached,
chroma lowered only as far as sRGB needs). So pure red becomes `#ee0000`, light grey
`#767676`, yellow a dark yellow. For a reader whose browser asks for dark colours (see
"Dark colours") each class has a second rule in the dark-scheme `@media` block: the
printed colour, lightened as far as 4.5:1 on `#121212` needs (no colour reaches 4.5:1 on
both pages). Colour spans sit inside links and may wrap maths (which takes the colour); linked
text itself keeps the link colour.

Colour alone tells a screen reader nothing, so text that refers to colour near coloured
text ("shown in red") gets the optional notice `colour_reference`: the instructor may want
to add a cue that does not depend on seeing it. With the flag off, coloured text is
written in the body colour, as in pipeline 0.3.0.

## MathML is rebuilt, never pasted

MathML in the output is re-serialised by `safe_html.sanitize_mathml` (also importable as
`assemble.sanitize_mathml`; tables use the same function), never inserted as a string
(rule 2 of the architecture doc):

1. Parse with `defusedxml` (no DTDs, entities or external references). A parse error means
   "not converted".
2. Walk the tree and write back only allowlisted presentation elements (MathML Core plus
   `menclose`) and layout attributes (`mathvariant`, `stretchy`, `columnalign`, ...).
   `href`, event handlers, `style`, `class`, `id` and colours are dropped. Attribute values
   must match `[A-Za-z0-9 .%+-]{0,64}`. All text is escaped.
3. **Bold letters.** MathML Core (Chromium) draws only `mathvariant="normal"`, so a bold or
   bold-italic token is written as Unicode mathematical bold letters instead
   (`core.mathchars.bake_bold_variants`): `\boldsymbol{\omega}` is U+1D754, `\mathbf{\omega}`
   U+1D6DA, the way `\mathbf{d}` was already U+1D41D, and the attribute (also inherited from
   an `mstyle`) is removed from the tokens that could be written so. The converter does it
   when it makes the MathML (`validate.safe_latex_to_mathml`) and the sanitiser does it
   again for MathML stored before, so every screen reader says "bold omega" and every
   browser draws it bold (`tests/test_math_bold_greek.py`, Chromium). Other variants are
   left as they were.
4. `semantics` and `maction` are replaced by their first child, which is what a browser
   shows anyway; annotations and alternatives are dropped.
5. Any other element (`script`, an HTML or SVG element, `mfenced`, a nested `<math>`)
   rejects the whole equation, which then falls back to the original image with
   `data-reason="mathml_rejected"`. Dropping just the unknown element would show maths
   with a silent hole in it, and a wrong equation read aloud is worse than an honest "not
   converted".
5. More than 64 levels deep, more than 5,000 nodes, or no text at all also rejects it.
6. `display` is set from where the equation is placed (inline in text, or block).

`tests/test_assemble_render.py` checks that latex2mathml output for fractions, roots,
arrays, sums, integrals, `\boxed` and prescripts passes through unchanged, and that the
hostile cases above are stripped or rejected.

## Headings

Levels come from the heading fixes (WP2), which already repair the outline. A reviewer
rejecting a heading (or a heading fix that failed) can reopen a gap the heading step had
closed, since rejected headings become paragraphs. So the assembler repairs the outline
once more, keeping the headings still open on a stack: a heading first closes those at
its own level or deeper, then goes at most one level below the one left open. The output
never skips down a level, siblings keep the same level, and the children of a rejected
heading move up together:

| From the fixes | Written |
|---|---|
| h1 Chapter, ~~h2 Section 1~~, h3 Sub 1.1, h3 Sub 1.2, h2 Section 2 | h1, p, h2, h2, h2 |

(Clamping to "previous heading + 1" alone would make Sub 1.2 a child of Sub 1.1.) The
first heading keeps its level; an outline with no gaps is written unchanged. The repair
is `assemble.Outline`; the review screen asks it (`would_write`) before saving or
accepting a heading level, refuses a level the output would show differently, and shows
the level the output writes when a change to an earlier heading moved it.

### One h1 (T3-13)

A file should have one top-level heading. A slide deck has one per slide, so when the
repaired outline would write two or more `<h1>` the assembler writes the document's title
as the only `<h1>` (the title students hear, see "One self-contained file": the one the
instructor typed on the job page, else the PDF's title without its section number) and
moves every heading one level down, so the slide titles are `<h2>` (capped at `<h6>`). A
file with at most one `<h1>` is written as before. `assemble.title_shift` decides it from the
repaired outline, for the renderer and for tests. The move comes after the repair above,
so the review screen's levels (`would_write`, "In the document: level N") are the levels
before it: in a slide deck its numbers are one less than the file's. The "About this
version" title is a labelled paragraph, not a heading: the `<footer>` is named by it
(`aria-labelledby`), so a screen reader user reaches it as a landmark, and it is not a
section of the document's outline.

## The note for students ("About this version")

The file ends with a short note for the people who read it, in a `<footer>` after
`</main>` (`id="about-conversion"`, MF7 of the UX review). It has no counts and no
review words ("needs review", "rejected"): those are for the instructor, on the job page
(docs/web.md "The job page"). In order:

- what this is: "This is a web version of a PDF from your course, made with ReadableSTEM, a
  tool that assists with accessibility.", plus "Its equations are written so that screen
  readers can read them aloud." when the file has converted equations;
- whether the instructor has checked it: nothing when the tool asked nothing and nobody
  changed anything; "Some parts were made by the tool and have not been checked by your
  instructor yet." while a decision is open (a draft download); "Your instructor checked
  the parts the tool was unsure about on 9 October 2026." once every decision is made.
  The date is `render_html(checked_on=...)`: the review screen passes the day of the
  re-render that follows a decision (`web/review.py rerender_html`, UTC), the language
  control the day of the last decision; the pipeline never passes one, so a result
  renders the same from storage;
- when something is shown as an image of the original: "Some parts could not be
  converted. They are shown as pictures of the original, marked "Not converted" or
  "Needs a description": ask your instructor for a version you can read.", and when whole
  pages are kept as images, which ones: "Pages 2 and 5 are pictures of handwritten or
  scanned pages and were not converted." (`assemble.kept_pages_note`);
- "If something is hard to read or looks wrong, tell your instructor.";
- an optional plain-text `source_note`, such as an OpenStax attribution.

The wording is final for the beta (docs/launch.md B7): the note does not name screen
readers, and "checked" appears only once an instructor has made a decision (the feature
review's challenge, 10.3 row 4, would say "decided by your instructor" instead, since the
tool only knows that decisions were made). Model cost is not in the HTML: the file is meant for
students. Cost is in the result JSON (`DocumentResult.stats`) and the CLI summary.

Text the tool writes (notes, labels, the student note) carries `class="readablestem"`. The
`banned_wording` check reads only that text, so course content that legitimately uses
words like "compliance" (beam flexibility in mechanics) is not flagged.

## Styling

About 35 lines of inline CSS:

- system font at 1.125rem with 1.6 line height, a 42rem column, near-black on white
  (about 17:1 contrast; for a dark scheme see "Dark colours");
- images at their printed size (PDF points x 96/72) and never wider than the column; an
  equation kept as a picture is the exception ("Equations kept as pictures" below);
- display equations scroll sideways inside their `.equation` box when wider than the
  screen (see "Wide equations");
- block maths keep the browser's own `display: block math` and centring (overriding
  `display` on `<math>` breaks MathML layout);
- lists with a hanging indent for printed markers (reset for anything nested in such an
  item, since `text-indent` is inherited), equation numbers to the right of their
  equation, and a `visually-hidden` class (clipped, not `display: none`, so screen
  readers still read it);
- a visible `:focus-visible` outline and a print rule that keeps flagged boxes together;
- only in documents that have one: a figure's folded description (a bold summary with
  the browser's own marker, no new colours) and table rules.

Documents with coloured text (FEATURE_COLOUR) get two rules per printed colour (see
"Coloured text"); documents with rebuilt tables get the table rules.

### Dark colours (T3-14)

`:root { color-scheme: light dark; }` lets the browser choose its own scheme for what the
file does not style (links, scrollbars), and a `@media screen and (prefers-color-scheme:
dark)` block sets the file's colours for a reader whose browser asks for dark: `#121212`
page, `#ececec` text (about 15:1), the dashed boxes of unconverted items and the notice
in `#ffa066` and the focus ring in `#8ab4f8` (both above 3:1 on the page), a thin
`#8c8c8c` edge on pictures (they are white-backed crops and would otherwise be a bare
white block), and dark versions of the code block and table rules (borders `#767676` and
`#a6a6a6`). MathML takes the text colour (nothing sets one on it), so equations follow the
scheme. Pictures are not inverted: a figure's colours mean something. The block is
`screen` only, so printing a file from a dark browser gives dark text on white paper.
Features add their dark rules to their own CSS (code, tables), so a document pays for
what it uses. The static check `html.text_contrast` reads both pages (a colour class
must reach 4.5:1 on white, and on `#121212` once the file has any dark rule), and
`tests/test_assemble_dark.py` runs axe in both schemes in Chromium (browser suite).

## Checks

`html_checks.static_checks(html)` returns one `Check` per rule, passed or not:

| Check | Code when it fails |
|---|---|
| `html.lang` | `missing_lang` |
| `html.title` | `missing_title` |
| `html.main` | `main_count` (not exactly one `<main>`) |
| `html.heading_order` | `heading_skip` |
| `html.heading_text` | `empty_heading` |
| `html.img_alt` | `img_missing_alt` (`alt=""` is allowed) |
| `html.unique_ids` | `duplicate_id` |
| `html.list_structure` | `list_structure` (an `<li>` outside `<ul>`/`<ol>`, or an element other than `<li>` or text directly in one; axe's `list` and `listitem` rules) |
| `html.math_display` | `math_missing_display` (warning) |
| `html.self_contained` | `external_resource` (a link's `href` is not a resource; the link rules check it) |
| `html.wording` | `banned_wording` |
| `html.table_headers` | `table_no_headers` (a data table without `<th>`; documents with a table only, like the four below) |
| `html.table_header_scope` | `th_without_scope` (unless the `<th>` has an id and its table uses `headers`) |
| `html.table_header_text` | `empty_th` |
| `html.table_caption` | `table_caption_missing` (warning: a "Table N" paragraph beside a table with no `<caption>`) |
| `html.table_layout` | `layout_table` (`role="presentation"` or `"none"` on a table) |
| `html.link_text` | `empty_link` (an `<a>` with no text or img alt in it) |
| `html.link_href` | `unsafe_link` (an `href` that is not http, https, mailto, or `#` and an id in the file) |
| `html.lang_tags` | `invalid_lang` (a `lang` value that is not a well-formed BCP 47 tag) |
| `html.text_contrast` | `low_contrast` (a `tc-` colour class below 4.5:1 on white) |

`html_checks.axe_checks(html)` runs the vendored axe-core 4.14.0
(`src/readablestem/pipeline/vendor/README.md`) in headless Chromium with the `wcag2a`,
`wcag2aa` and `wcag21aa` rules. The page runs with networking off. Each violation
becomes `Check(name="axe.<rule>", code="<rule>")`, an error for serious or critical
impact and a warning otherwise; only rule ids and impact leave the browser, never page
content. Without Playwright or its Chromium it returns one
`Check(name="axe", passed=True, severity="warning", code="skipped")`. A run takes about
a second.

`convert_pdf` stores the static checks on `DocumentResult.html_checks` (and, for older
callers, on `run.PipelineOutput.checks`). The CLI prints them and writes them to
`--report`; the web app lists the failed ones on the job page and re-runs them whenever
it makes the file again after review decisions (once for a run of decisions: web.md,
"Review screen"). `static_checks(result.html)` is deterministic
and fast, so anyone holding only the HTML can recompute them.

## How `convert_pdf` fills it in

`pipeline/run.py`:

1. `layout.analyze` (raises `LayoutError` for unreadable, encrypted or too-long PDFs).
   The table detector runs only on pages that could hold a table
   (`tables.may_hold_table`, docs/tables.md "Which pages are looked at").
2. Page by page, on the calling thread (PyMuPDF is not thread-safe): crop each equation
   and figure from its own bbox (inline equations included), then queue its step on a
   `ThreadPoolExecutor(concurrency)`. Cropping page N+1 overlaps the model calls for
   page N.
3. Once every crop is made and the steps are at work, on the same thread:
   `text_pass.apply` (links, cross-references, the document and passage languages, and
   with FEATURE_COLOUR the colour runs; docs/layout.md "The text pass"; it sets `lang`,
   `lang_source` and its notices on the result, and never fails the run) and
   `heading_step.heading_fixes` on all elements (if it raises, every heading becomes
   FAILED `internal_error` and renders as a paragraph). Neither changes anything a step
   reads or needs a model, so they run while the models answer rather than before the
   first request.
4. Each step gets ~600 characters of neighbouring text as untrusted `context_text`: for an
   inline equation, its paragraph or list item around the marker (`[this equation]`
   marks the spot); otherwise the end of the previous text element (paragraph, heading or
   list item) and the start of the next. Other
   markers become `[equation]`.
5. A step that raises becomes FAILED `internal_error`; a crop that fails, FAILED
   `crop_error`. Only the exception type is logged, never its message (it may quote the
   document). The exception is a misconfigured run: `config.ConfigError` (no
   OPENAI_API_KEY; WP1's client reads it on its first request) or
   `pricing.UnknownModelError` (an unpriced model) would fail every element the same
   way, so the first one stops the run (queued steps are cancelled, nothing new starts)
   and `convert_pdf` re-raises it. The CLI turns it into `error: ...`; the web worker
   fails the job instead of storing a document with everything flagged.
6. The first `BudgetExceeded` (or a step returning reason `budget_<scope>`) stops new model
   work: queued steps are cancelled, and everything not yet processed becomes FAILED
   `budget_<scope>` with its crop kept. Calls a step made before the refusal were billed,
   so a recording wrapper around the client keeps them on the FAILED fix.
7. `progress(pages_done, pages_total)` runs on the calling thread when all of a page's
   steps have finished, and `stage_progress(ProgressEvent)` as each stage starts and
   moves on: "layout" (when analysis starts, then with the pages and the elements found
   per kind), "equations", "figures" and "tables" (their totals first, then after every
   element: scanned pages and failed crops count as done), "assemble" and "checks". An
   exception from either aborts the run.
8. Once the last crop is made, `layout.release(pdf_bytes)` drops WP2's cached copy of
   the PDF (also when the run fails), so a long-lived worker does not keep a user's
   document in memory until newer jobs evict it.
9. `pipeline.source_facts.measure` reads the PDF once more for the job page's
   before/after card (tagged, /Lang, title, bookmarks, pictures and, in a tagged PDF,
   pictures with alt text, links; from the elements and fixes: equations, those whose
   text layer loses a fraction, root or script, bold maths letters stored as plain
   characters, tables). Numbers only; a fact it cannot read keeps its default, and it
   never fails the run.
10. `render_html` (with the caller's optional `notice` and `source_note`), then
   `static_checks`.
11. `pipeline.notices.from_fixes` derives the notices that follow from the fixes (a
   table shown as an image, a scanned page, a colour-only figure, a chart or diagram
   without a long description when long descriptions are on) and merges them with the
   others on `DocumentResult.notices` (one per code and element, or code and page with a
   count).

Stats: document `latency_ms` is wall-clock time for the whole run; page `latency_ms` is
the summed time inside that page's steps (pages overlap in the pool, so per-page wall time
would mislead). `prompt_versions` are the versions the calls actually used, else the
step modules' `PROMPT_VERSION`.

Tracer spans (`document`, `text`, `headings`, `page`, `step`, `model_call`, `assemble`) carry ids,
pages, models, token counts, costs, statuses and reason codes only. Results are added with
the span handle's `set(**attrs)` (WP6; `update` is accepted too) as scalars: counts per
status are flat keys such as `fixes_accepted`, and a budget stop is `reason`. Each model
request gets a `model_call` span opened with `model=`, which WP6 records as a Langfuse
generation (with tokens and cost), so per-request cost shows in Langfuse's cost views. A
`step` span carries `model` only when the step used one model.

## CLI

```
readablestem convert FILE.pdf --out OUT.html (--max-usd X | --fake) [--pages 1,3] [--concurrency 4]
                  [--report OUT.json] [--axe]
```

`--max-usd` caps this run with an in-memory spend guard; `MAX_USD_PER_JOB` and
`MAX_MODEL_CALLS_PER_JOB` cap it too. Before converting, a real run checks that
OPENAI_API_KEY is set and that MAIN_MODEL and CHEAP_MODEL have prices: either problem is
`error: ...` and exit 2, and nothing is written. `--fake` uses the offline fake client
(no key needed); its answers are placeholders (an equation "x", a figure "Figure
(offline placeholder)", with long descriptions on an empty one, and with tables on a
one-cell table "Table (offline placeholder)", each a decision: `models.OFFLINE_DEFAULTS`),
so the HTML opens with the note "Offline test run: equations, figure descriptions and tables in
this file are placeholders from a fake model, not conversions. Do not publish it." Progress
("page 1/1 done") goes to stderr; the summary on stdout is content-free. A run with
`--fake --report notes.json --axe` (fake layout and steps, as in the CLI tests):

```
Pages:       1
Elements:    4 (1 headings, 1 paragraphs, 0 list items, 2 equations, 0 figures)
Fixes:
  kind        accepted
  heading            1
  equation           2
Model calls: 2
Cost:        $0.0044 (fake client, nothing billed)
Time:        0.0 s
Checks:      static 15/15 passed
             axe-core no violations
wrote /tmp/wp4-demo/notes.html
wrote /tmp/wp4-demo/notes.json
```

`--report` writes `{"result": DocumentResult without html, "checks": [...], "axe": [...]}`.
It contains document text, so treat it like the HTML.

When a limit stops the run, the summary names it, e.g. `Budget: stopped early
(budget_daily: the --max-usd cap for this run)`. The run cap is an `InMemorySpendGuard`,
which reports the scope `daily` because it stands in for the web app's daily cap.
`--out` and `--report` may not be the input PDF or each other.

`--concurrency` (at most 16; default `MODEL_CONCURRENCY`, 8, lowered to what `--max-usd`
can hold) is how many equation and figure steps run at
once. Every model request first reserves its worst-case cost and settles to the real
cost when it returns, so the reservations of the steps in flight count against
`--max-usd` until they settle. A small cap therefore fills before it is spent: with
`--max-usd 0.10` and 4 steps of about $0.02 worst case each, a real run refused a call
after $0.016 of real spend. That is why the default follows the cap; a number given with
`--concurrency` is used as it is, and when the run cap then stops a real run with more
than one step at once, the summary says so and suggests `--concurrency 1`, which reserves
one call at a time.

## Engineering limits (the source of the known-limits page)

The public known-limits page (`web/templates/limits.html`) states these in plain words.
Keep the two in step when a limit changes.

- An equation wider than the screen does not wrap: it scrolls sideways inside its own
  box (a tab stop when it may not fit a phone screen).
- NEEDS_REVIEW items look like converted ones in an export made before review.
- A data table is rebuilt only with `FEATURE_TABLES` on (off by default). Off, or when
  the model cannot read it, the table stays an image with its "not converted" note and a
  required decision. A table the detector misses (an unruled table of numbers only)
  stays in the text flow, sometimes read as a display equation; a grid of key terms
  printed with rules becomes a list of its terms.
- If one equation marker appears twice, the equation is rendered twice (the second
  without an id).
- A figure's long description and data table are folded: they print only if opened
  first. A chart's data table read from the image is in the file only when a reviewer
  added it, and is approximate (its caption says so).
- A PDF link over an image or a drawing (not over text) is not kept; neither is a link
  in a figure's caption. A link whose rectangle covers only part of a word links that
  part.
- Link text is kept as printed, also when it says nothing on its own ("click here"):
  only the instructor can reword it (the job page lists it as worth checking).
- Language detection needs about 20 words of prose; a short handout of mostly maths is
  assumed to be English unless the PDF says otherwise. A passage is marked only when it
  is a whole paragraph or list item in another language (a quoted phrase inside an
  English sentence is not).
- Colour inside figures stays in the image; colour in headings is not kept.


## Review edits that reach the file (T3-5, T3-16)

- **A description in words** (`Fix.described`) is the alt text of an equation, table or page
  kept as an image, instead of the PDF's text marked not checked or the fixed words. It is
  cleaned and checked as alt text and escaped when written. The image is still labelled "Not
  converted."
- **Pages left out** (`DocumentResult.omitted`) are not in the file; the student note says
  "Page 1 of the original PDF was left out of this file." (`assemble.omitted_pages_note`), and an
  internal link or cross-reference to an element on such a page is taken off at render
  (`assemble._without_dead_links`).
- **Edited words, a paragraph made a heading, an added picture or equation** are ordinary
  elements and fixes in the stored result (a picture added by hand has the id `p3-x1` and a fix
  the reviewer made); the assembler writes them like any other.
