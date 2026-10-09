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
<meta name="generator" content="a11y-stem 0.1.0">
<title>Speed and Velocity</title>
<style>/* ~25 lines, see "Styling" */</style>
</head>
<body>
<main>
  <p class="notice a11y-stem" role="note">...</p>   (only when a notice is given)
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

The file is meant to be uploaded as a file and linked, not pasted into a page. Pasting
breaks it: Canvas's sanitizer keeps only http, https and relative image sources, so every
`data:` image (every figure and every equation kept as an image) is dropped, and it
removes the `<style>` block, `<main>` and `tabindex`, so the hidden "Equation" labels
show, printed list markers double ("1. (a)") and wide equations lose their scroll box
(read from Canvas's sanitizer allowlist, FEATURE-REVIEW "Beta 5"; not yet tried in a live
course). Moodle's HTML purifier drops `data:` images and MathML. The app's help page
`/help/posting` (`web/templates/help_posting.html`) gives the steps for Canvas (upload to
Files, link it where the PDF was, check it in Student View), says to make the HTML the
main version, with the PDF kept only as a labelled print copy (28 CFR 35.202 and the
Justice Department's fact sheet on the rule), and that it is guidance, not legal advice. The job
page links to it under the download link. TODO(Harish): try the steps in a real Canvas
course (Canvas Lite, then UTD) and update both.

## How each element is written

| Element | Converted | Not converted (ABSTAINED, FAILED, REJECTED, no fix) |
|---|---|---|
| Heading | `<h1>`-`<h6>` from the heading fix | `<p>` with the original text; a bold line a reviewer rejected ("Make them bold text") is `<p><strong>`, bold as in the PDF |
| Paragraph | `<p>`, inline equations placed at their markers | (no fix) |
| List item | `<li>` in a `<ul>`/`<ol>`, inline equations placed at their markers (see "Lists") | (no fix) |
| Inline equation | `<math display="inline">` inside the paragraph or list item (a wide one in a `<span class="equation-scroll">`, see "Wide equations") | the crop inline, outlined, with "(equation not converted)" |
| Block equation | `<div class="equation"><math display="block">`, then its number if it has one | `<div class="flagged">`: the crop, its number, a note |
| Figure | `<figure><img alt="..."><figcaption>`, then `<details class="figure-description">` when it has a long description or a data table (see "Long descriptions and chart data") | `<div class="flagged">`: the figure with alt "Figure without a description", plus a note |
| Decorative figure | `<div class="decorative"><img alt="">` (a `<figure>` if it has a caption) | |
| Table (pipeline 0.4.0) | `<div class="table-scroll" role="group" tabindex="0">` around a `<table>` (see "Tables") | `<div class="flagged">`: the image of the table with alt "Table not converted", its caption, a note (`data-reason="table_not_rebuilt"` while rebuilding tables is off) |

A trimmed excerpt of `tests/fixtures/assemble/sample_document.json` after assembly:

```html
<h1 id="p1-e0">2.2 Speed and Velocity</h1>
<p id="p1-e1">Average speed is distance over time, <math display="inline" id="p1-e2">
  <mrow><mi>v</mi><mo>=</mo><mfrac>...</mfrac></mrow></math>, where
  <span class="flagged-inline" id="p1-e3" data-status="abstained" data-reason="low_confidence">
    <img src="data:image/png;base64,..." alt="Equation not converted" width="16" height="16">
    <span class="flag-label a11y-stem" aria-hidden="true">(equation not converted)</span></span>
  is the distance travelled.</p>
<div class="equation" id="p1-e4"><math display="block">...</math></div>
<div class="flagged" id="p1-e5" data-status="failed" data-reason="budget_job_usd">
  <img src="data:image/png;base64,..." alt="Equation not converted" width="213" height="53">
  <p class="flag-note a11y-stem" role="note"><span class="flag-label">Not converted.</span>
  This equation is shown as an image of the original, so screen readers cannot read it.
  Review it before publishing.</p></div>
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
<details class="figure-description"><summary class="a11y-stem">Description of Figure
  2.3</summary><p>Line graph of the position of a car against time.</p><p>...</p>
  <div class="table-scroll" role="group" aria-labelledby="p3-e0-data-caption" tabindex="0">
  <table><caption id="p3-e0-data-caption">Data read from Figure 2.3; the values are
  approximate</caption><thead>...</thead><tbody>...</tbody></table></div></details>
```

- The `<details>` follows the `<figure>` instead of sitting in it: a `<figcaption>` must
  be the figure's first or last child, and a long description inside the figcaption
  would become the figure's accessible name, read in full on entering it.
- The summary names the figure from its caption ("Description of Figure 2.3", "Data for
  Figure 2.3" when there is only a table, "the figure" without a number), so it makes
  sense out of context in a screen reader's list of controls. It works without scripts.
- Each line of the long description is a `<p>`; everything the model wrote is escaped.
- The data table goes through `table_html.render_table` like a rebuilt table, in a box
  that scrolls sideways and takes focus, named by its caption. The caption is the tool's
  own and says where the values came from: "Data shown in Figure 2.3" when every number
  is in the PDF's text, else "Data read from Figure 2.3; the values are approximate".
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
  <span class="equation-label"><span class="visually-hidden a11y-stem">Equation </span>2.4</span></div>
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
nothing, and screen reader users hear a fixed, honest alt text ("Equation not converted",
"Figure without a description") and a note, instead of a guess read aloud as if it were
right.

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
- NEEDS_REVIEW fixes are shown as converted and not flagged here: the review screen is
  where they are handled. So are fixes accepted with an optional note
  (`Fix.review == "optional"`, docs/design/beta-architecture.md "Review tiers").

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
`#767676`, yellow a dark yellow. The output has no dark mode, so there is no second
rule. Colour spans sit inside links and may wrap maths (which takes the colour); linked
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
3. `semantics` and `maction` are replaced by their first child, which is what a browser
   shows anyway; annotations and alternatives are dropped.
4. Any other element (`script`, an HTML or SVG element, `mfenced`, a nested `<math>`)
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

## The note for students ("About this version")

The file ends with a short note for the people who read it, in a `<footer>` after
`</main>` (`id="about-conversion"`, MF7 of the UX review). It has no counts and no
review words ("needs review", "rejected"): those are for the instructor, on the job page
(docs/web.md "The job page"). In order:

- what this is: "This is a web version of a PDF from your course, made with a11y-stem, a
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
  "Needs a description": ask your instructor for a version you can read.";
- "If something is hard to read or looks wrong, tell your instructor.";
- an optional plain-text `source_note`, such as an OpenStax attribution.

The wording is `TODO(Harish)` in `pipeline/assemble.py`: whether to name the screen
readers that read MathML aloud, and whether "checked" stays (the feature review's
challenge, 10.3 row 4, would say "decided by your instructor" instead, since the tool
only knows that decisions were made). Model cost is not in the HTML: the file is meant for
students. Cost is in the result JSON (`DocumentResult.stats`) and the CLI summary.

Text the tool writes (notes, labels, the student note) carries `class="a11y-stem"`. The
`banned_wording` check reads only that text, so course content that legitimately uses
words like "compliance" (beam flexibility in mechanics) is not flagged.

## Styling

About 35 lines of inline CSS:

- system font at 1.125rem with 1.6 line height, a 42rem column, near-black on white
  (about 17:1 contrast);
- images at their printed size (PDF points x 96/72) and never wider than the column;
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

There is no dark mode yet. Documents with coloured text (FEATURE_COLOUR) get one rule
per printed colour (see "Coloured text"); documents with rebuilt tables get the table
rules.

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
(`src/a11y_stem/pipeline/vendor/README.md`) in headless Chromium with the `wcag2a`,
`wcag2aa` and `wcag21aa` rules. The page runs with networking off. Each violation
becomes `Check(name="axe.<rule>", code="<rule>")`, an error for serious or critical
impact and a warning otherwise; only rule ids and impact leave the browser, never page
content. Without Playwright or its Chromium it returns one
`Check(name="axe", passed=True, severity="warning", code="skipped")`. A run takes about
a second.

`convert_pdf` stores the static checks on `DocumentResult.html_checks` (and, for older
callers, on `run.PipelineOutput.checks`). The CLI prints them and writes them to
`--report`; the web app lists the failed ones on the job page and re-runs them after
every review decision re-renders the file. `static_checks(result.html)` is deterministic
and fast, so anyone holding only the HTML can recompute them.

## How `convert_pdf` fills it in

`pipeline/run.py`:

1. `layout.analyze` (raises `LayoutError` for unreadable, encrypted or too-long PDFs),
   then `text_pass.apply`: links, cross-references, the document and passage languages,
   and with FEATURE_COLOUR the colour runs (docs/layout.md "The text pass"). It sets
   `lang`, `lang_source` and its notices on the result, and never fails the run.
2. `heading_step.heading_fixes` on all elements. If it raises, every heading becomes
   FAILED `internal_error` and renders as a paragraph.
3. Page by page, on the calling thread (PyMuPDF is not thread-safe): crop each equation
   and figure from its own bbox (inline equations included), then queue its step on a
   `ThreadPoolExecutor(concurrency)`. Cropping page N+1 overlaps the model calls for
   page N.
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
a11y-stem convert FILE.pdf --out OUT.html (--max-usd X | --fake) [--pages 1,3] [--concurrency 4]
                  [--report OUT.json] [--axe]
```

`--max-usd` caps this run with an in-memory spend guard; `MAX_USD_PER_JOB` and
`MAX_MODEL_CALLS_PER_JOB` cap it too. Before converting, a real run checks that
OPENAI_API_KEY is set and that MAIN_MODEL and CHEAP_MODEL have prices: either problem is
`error: ...` and exit 2, and nothing is written. `--fake` uses the offline fake client
(no key needed); its answers are placeholders, so the HTML opens with the note "Offline
test run: equations and figure descriptions in this file are placeholders from a fake
model, not conversions. Do not publish it." Progress
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

`--concurrency` (default 4, at most 16) is how many equation and figure steps run at
once. Every model request first reserves its worst-case cost and settles to the real
cost when it returns, so the reservations of the steps in flight count against
`--max-usd` until they settle. A small cap therefore fills before it is spent: with
`--max-usd 0.10` and 4 steps of about $0.02 worst case each, a real run refused a call
after $0.016 of real spend. When the run cap stops a real run with more than one step at
once, the summary says so and suggests `--concurrency 1`, which reserves one call at a
time.

## Engineering limits (input for the known-limits copy)

TODO(Harish): turn these into the public known-limits copy.

- An equation wider than the screen does not wrap: it scrolls sideways inside its own
  box (a tab stop when it may not fit a phone screen).
- NEEDS_REVIEW items look like converted ones in an export made before review.
- A data table is rebuilt only with `FEATURE_TABLES` on (off by default). Off, or when
  the model cannot read it, the table stays an image with its "not converted" note and a
  required decision. A table the detector misses (an unruled table of numbers only)
  stays in the text flow, sometimes read as a display equation; a grid of key terms
  printed with rules is claimed as a table and, not being a data table, kept as an image.
- If one equation marker appears twice, the equation is rendered twice (the second
  without an id).
- A figure's long description and data table are folded: they print only if opened
  first. A chart's data table read from the image is approximate (its caption says so).
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
