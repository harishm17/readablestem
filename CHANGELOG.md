# Changelog

## Unreleased: a raised digit in a figure caption stays raised

- A figure caption was written from a plain string, so "10⁸" came out "108" and "CP³" came out
  "CP3" with only a notice that some script was flat. A raised run in a caption is now written in
  superscript characters, as a table cell's and a table caption's are
  (`script_marks.raised_characters`, used by `layout/blocks.py` and `tables._marked`): the page
  has them in `Element.caption_marked`, and `Element.caption` keeps the flat text the figure
  step is sent. Only a lowered run in a caption is still written flat and flagged
  (`scripts_flattened`).
- Listen says superscript characters as it says a raised run: "10 superscript 8".
- An equation shown as an image because it was not converted has the warning that exponents run
  together in its alt text when layout boxed a power of ten ("108" was given as the equation).
- No recording or request changes (the figure and equation steps are sent what they were). A
  stored result loads unchanged and keeps its old caption until the file is converted again.
  The pages a layout change like this one reaches need a `PIPELINE_VERSION` raise when the wave
  is released. No migration.
## Unreleased: inline formulas kept whole (WS4.8, off)

- New switch `LAYOUT_INLINE_JOINS` / `layout.analyze(inline_joins=True)`, off in code and in
  production, wired like `LAYOUT_DISPLAY_GROUPING`. Four rules behind it (`layout/inline_runs.py`,
  `layout/shared.py`): more operator names are math words; a colon or semicolon inside a bracket
  that closes on the row does not end a run; a word space next to a math word ("a = b mod n")
  does not end a run; a label with an index or ":=" ("H0:", "f :=") joins the run after it.
  On the development papers' position truth the four give 87 more whole formulas and lose none
  (8,990 to 9,077 of 11,258); with `LAYOUT_DISPLAY_GROUPING` also on, 9,118. A fifth rule, part
  labels "(a)" split off as text, was measured and not built: it changed no box once narrowed.
  With the switch off nothing changes; no version bump, nothing to migrate.
- Gold: a macro argument that starts with a letter, put after a control word in the body
  (`\lVert#1` with `A`), keeps a space between them (`gold/arxiv_tex.py`).
## Unreleased: one word for each concept (SW22)

- Every screen, message, record, export and email now uses one word for one concept, and the
  labels more than one place says are read from the new `web/strings.py` (the Jinja global
  `strings`). **Check** names only an automatic check the tool ran; what a person may look
  at is an **item** ("Optional items", "Worth a look", and the verb is "look at" or
  "compare"). **Item** is one thing the tool handled; **part** is only a page range of an
  upload, so the button is "Convert the missing items". **Post** is putting the finished file
  where students get it ("Ready to post", "before you post it"); **share** is giving named
  people access. **Document**, never "job", on the pages, in the routes' own messages and in
  the help pages. **Keep as image** is the one label (the page groups, the describe box, the
  review card), and the record and the status say "Kept as image (your choice)". "Ask the
  model to read it again" and "Model reads today: N of M" name the model read. The sample
  document has one label ("Open the sample document") and the public page is "the demo".
  The browser's own are "notifications". The consent box reads "Also let ReadableSTEM look
  at the results" (30 days; nothing about the choice changed). The AI service is "the model"
  except on the status page's provider line. The review details no longer print the raw
  reason and check codes.
- The student file's own words changed in the same pass (the "not converted" line, the
  legend, the note on a table or equation kept as an image, the record's headings), so a
  document made before this change keeps the old words until it is made again.
  `RENDER_VERSION` is owed a raise (not made here). No model request changed, no migration,
  no stored result or hash changed, and no new model read or cost.
## Unreleased: the eval gate guards the last nine constants, and smaller follow-ups

- **The eval gate guards nine more constants** (`eval/gate.py`): `table_step.NUMERIC_SHARE`,
  `page_math.MIN_PAGE_CHARS`, `page_math_reconcile.RULE_MARGIN` and `ATTACHED_LETTERS`,
  `page_witness.SPLIT_MIN_ROWS`, `witness_reconcile.MIN_GLYPH`, `image_page.RECOVERY_MARGIN`,
  `image_page_check.CROP_MARGIN_PX` and `MIN_CROP_HEIGHT_PX` had waited, marked "NOT YET
  GUARDED", for the two no-model reports to be made again. They are made again here
  (`data/eval/latest-headings.json`, `data/eval/latest-tables-local.json`: same scores, now
  holding the nine flags), so no constant that decides a request or a bar is left unguarded.
- **`eval burden` ends with its verdict.** The reasons and the note that the band check was not
  made come first; the last line printed is "The bar is met", "The bar is NOT met." or "The bar
  was not judged", so a long line above it cannot hide it.
- **Docs say what is true on main without naming a release.** `docs/deploy.md` "Roll back" and
  `docs/deploy-railway.md` give the rule for stored results (roll back across a release that
  adds a value the older code refuses only before the first document is converted by the newer
  one) and point to the changelog for which release does what; `docs/operations.md` says there
  is no per-job storage cap and why, in place of "not built".
- Tests: `tests/test_layout_lists.py`, `tests/test_region_tree.py`, `tests/test_structure.py`
  and `tests/test_web_edit_page.py` lose a dash permutation, a test of a setting's default, a
  markup pin and a dead assertion, and fold tests that set up the same page into one.

## Pipeline 0.5.14, render 10 (2026-11-19)

- `PIPELINE_VERSION` is 0.5.14 and `RENDER_VERSION` is 10: one raise each for every "Unreleased"
  entry below down to the 0.5.13 entry. What a PDF's result holds changed (a one-page file's
  page number, equation numbers, footnotes, lists and lettered titles, a table that goes on
  from the page before, algorithms, text painted where nobody can see it, the table search's
  time limit). A Word or LaTeX file's result changed too: bold, italic and code in table cells,
  struck code, a table with no header marking is a required choice, and color is counted (the
  LaTeX reader keeps it only with the setting on). The student page changed (a continued table's carried header rows, the year in
  the finish sentence's date, a sidebar's name, tab stops, nested scripts, very wide equations
  on paper, the words for a Word file's equations, speech for bars and for raised and lowered
  text). Six notice codes are new: `table_header_missing`, `table_header_carried`,
  `struck_not_kept`, `hidden_text_check`, `tables_not_searched`, `word_table_flattened`.
  Stored results load unchanged and their pages are made again. No migration.
  `data/eval/latest-headings.json`, `data/eval/latest-tables-local.json`,
  `data/eval/burden-corpus.json` and `data/eval/parsing-bench-olmocr.json` are re-made at
  0.5.14, and so is `data/eval/order-layout-dev-1.json` (137 of 267 pages right and 71
  faultless; the 0.5.10 record had 149 and 45, and docs/eval.md says what moved and that 17
  pages were lost by 0.5.13); the golden pages and the stored demo page are written again at render 10. Roll back:
  0.5.13 opens a 0.5.14 result (docs/deploy.md).

## Unreleased: fixes after the fourth and fifth reviews of the algorithm blocks (reviews 10 and 11)

Review 10 ran 48 pages of its own on the review-9 fixes, with the 171 pages of the three
reviews before it. The five fixes held on the pages they were made for; three of the new rules
were wider or narrower than they should be. Review 11 ran 75 more pages on the fixes for those
three: the code could go in, two of the fixes closed their finding only in part, and three
sentences written here were not true. Fixed (`layout/blocks.py`), each with a case in
`tests/test_structure.py` that fails without it:

- **"=" after an equation, as the first step of its block.** The rule from review 9 (the line
  must start where a step above it starts) lost the second half of an algorithm whose loop
  holds a display equation first and an "=" step after it: no step above stands at that level
  yet. The line may now also stand right of the last step when that step opened the block.
- **A long step needs a short line under it.** Under a float's caption, the last line of the
  paragraph the float cut, then a paragraph whose second line opens "for" and runs to the
  margin, was a block: the first line of that paragraph passed as a long step. The step word
  under a long step must now be on a line that ends short of the right edge (as "repeat" does);
  with no step word under it, the line is still asked for the nesting proof.
- **A centered formula is no nested step.** Running text with a line that opens "for" and ends
  in a colon ("... as follows:") over a display equation was a block: the formula counted as
  a step set inside the "for". A line more than 6 em right of the line over it no longer
  counts, and (review 11) nor does a closing line more than 6 em left of the line over it: "The
  speed of the verse is", the equation, "until the last word is read." was a block too.
  Formula lines are read for this proof only under a long line in plain words; the
  two other checks for nesting (numbers written "1.", and no caption) read text lines again,
  as before review 9.
- Corrected in the review-9 section below and in docs/layout.md: a long line after the first
  is a long step only when it is one line with a step word under it that ends short (a long
  step that wraps, two long steps in a row, one over "count = 0" or over a loop line that
  reaches the margin still need the nesting proof, and flat they are paragraphs); and "none is
  a block on the released version either" said nothing, since the released version reads no
  algorithm as a block (all of them are paragraphs or lists there).
- Left, and written in docs/layout.md. Algorithms lost: a loop under a long first line whose
  only line is a formula set more than 6 em to the right is paragraphs; pseudo-code numbered
  "1." whose only nesting is between lines 9 and 10 is a list; an "=" step first in its block
  after an equation is not picked up when the line that opens the block wraps into a second
  line that starts where it does. Prose still taken for steps under a float's caption (the
  two fixes above close their shapes only in part): a line that opens "for" and ends in a
  colon over a display equation that starts within 6 em of it (a wide equation, a narrow
  column, equations set flush left); and a short line, a paragraph of two lines whose second
  line opens with a step word and is short, then a paragraph that opens "If". Also: a
  paragraph with
  "=" after an equation is still taken where it starts within a quarter em of the start of any
  step above it, or right of a last step that opens a block, under an algorithm with a block
  no "end" closes; the bold heading of the next section right under an algorithm
  is taken as its last line; and running text under a float's caption with two
  numbered lines after it is one block.

## Unreleased: fixes after the third review of the algorithm blocks (review 9)

The review ran the 73 probe pages again, 57 of its own and 11 public papers with algorithms on
the code before and after the review-8 fixes: 16 of the 73 read better, none worse, and the
papers read the same. One of those fixes was not complete and three review-7 fixes had come
open again in narrow shapes. Fixed here (`layout/blocks.py`), each with a case in
`tests/test_structure.py` that fails without it:

- **A long step after the first is a step.** "Choose a start vector b.", a line in plain words
  out to the margin, then "repeat" or a loop with no "do": the review-8 fix asked such a page
  for nested steps and, finding none, dropped the algorithm (the version before kept it). A
  line that the next one runs on from, with a step over it and a step word under it (on a line
  that ends short of the right edge: review 10), is a long step again and asks for no more
  proof.
- **A loop that holds one formula shows the nesting.** Under a long first line the proof was
  read off the text lines only, so "for t = 1 to T do" with "b <- A b" under it proved nothing
  and the algorithm was two paragraphs. A formula on a line of its own counts as a step in
  that check.
- **"=" after a display equation resumes only where a step starts.** A sentence set in as a
  paragraph's first line ("where d = 12 is ...", "Let d = 12 be ...") under an algorithm with a
  block no "end" closes was a second block, and the first block's levels changed. The line
  must now start where a step above it starts (or, since review 10, right of a last step that
  opened the block).
- **One number is no numbering.** The text under a float's caption followed by the heading "4
  Results ..." was one block with the heading in it: one numbered line switched off the two
  checks that tell a paragraph from steps. They are skipped only from two line numbers up.
- **"9." to "10." is no indent.** Eleven numbered sentences under "Algorithm 5.1 (Euclid)" were
  a block when item 9 opened "If": typed with no tab stop, "10." sets its words a digit further
  right. Two lines whose numbers differ in length no longer prove a step set inside another.
- Left, and written in docs/layout.md: under a long *first* line in plain words, a flat
  algorithm, one whose loop lines end in no "do", "then" or colon (unless a short "return x" or
  an "end" is set back to the left) and a loop that holds two formula lines or more are still
  paragraphs; text set in under a last step
  that reaches the right edge is taken into the block; a sentence with "=" after an equation,
  set in by the indent of a step under an algorithm with a block no "end" closes, is
  still taken; where the caption ends within 3 em of the right edge of its column, a first
  step with no step word is read as the caption's second line; and a caption is a line that
  opens "Algorithm" and a number, so under "Procedure 2 ..." or "Algorithm A (...)" pseudo-code
  numbered "1." is still a list.

## Unreleased: fixes after the second review of the algorithm blocks (review 8)

The review ran 73 probe pages on the code before and after the review-7 fixes. Four of the new
rules lost real algorithms that the first version kept as one block. Fixed here
(`layout/blocks.py`), each with a case in `tests/test_structure.py` that fails without it:

- **A long first step is a step.** Under a caption, a line in plain words before the first loop
  ("Initialize the table of counts ...") that ran to the right edge lost the whole algorithm:
  every such line had to stop short. The lines before the first block word are steps again.
  Where the next line runs on from one of them, the block must show one step set further right
  under a line that opens a block and ends as one does ("do", "then", a colon), or a closing
  line set back to the left, which the running text under a float's caption does not. (Review
  9 found shapes this still lost: the entry above.)
- **Pseudo-code numbered "1." or "1)" under a caption is one block with its indentation.** It
  was a list with no indentation and a stray paragraph. Such numbers are steps under a caption
  when one step is set inside another; "Algorithm 5.1 (Euclid)" in numbered sentences is still
  a list, and with no caption such numbers are still a list's.
- **A step may go on for as many hanging lines as it needs.** An "Input:" line or a step in a
  loop that went on for three lines set under it dropped the algorithm. Lines set further right
  than where their step starts no longer count toward the two-line limit, which is there for
  the text that goes on right under an algorithm.
- **A step written with "=" resumes after a display equation** when it stands inside a block
  that is still open ("count = count + v" under a "for" that no "end" has closed). The
  algorithm was lost, or its tail became a paragraph. "where d = 12 is ..." after the last
  step is still the text going on when it starts at the left edge or every block is closed
  (review 9 found it taken when set in under a block with no "end": the entry above).
- The limit on lines a step runs on for and the margin test (`_RightEdges.firm`) had no test:
  each has one now.
- Left, and written in docs/layout.md (all but the last read the same before and after the
  review-7 fixes; the last changed with them):
  prose that opens "Algorithm 2 computes ..." or with another verb not on the sentence list;
  two short paragraphs that open "For" and "If" right under a float's caption; a sentence that
  opens "Then", "For" or "If" right after an equation that follows an algorithm; a loop line
  with no "do" or colon that reaches the right edge takes the first line of its body; a
  manuscript page with line numbers, indented paragraphs and a line that opens "until"; and,
  on a page not set to a right margin, a step wrapped with no hanging indent stays in two lines
  (on a page set to one, a short step under a step that reaches the margin is joined to it).

## Unreleased: fixes after the independent review of the algorithm blocks (review 7)

The review ran probe pages on the code before and after the algorithm change. Numbered
algorithms read better; prose and lists near an "Algorithm n" line, and algorithms with no
numbers, read worse than before it. Fixed here (`layout/blocks.py`):

- **Prose under an "Algorithm n" line stays prose, in the shapes probed.** A line that opens
  "Algorithm 2 is ..." or "Algorithm 1 shows ..." is a sentence, not a caption
  (`_CAPTION_SENTENCE`, a list of such words: other verbs are not caught). The running text
  under a float's caption ("Algorithm 2 Overview of the method", the algorithm itself a
  picture) is not taken, and neither is the text that goes on under an algorithm. (The rule
  first written for this, that each line before the first block word stop short, lost real
  algorithms; review 8 above replaced it.)
- **A list is not an algorithm.** "Algorithm 5.1 (Euclid)" written as four numbered sentences
  is four list items again (a bullet is never a step; for "1." and "2)" see review 8 above).
  With no caption, numbered lines must hold one step inside another (`_nested`), so exam
  questions numbered "1 If ...", "2 For ..." and the numbered lines of a manuscript whose
  lines start at one place are paragraphs again; lines with no numbers must hold something only code has (a
  formula, an assignment, an "end", a block word on a line that ends in "do", "then" or a
  colon), so a poem's indented lines are not taken.
- **A loop line keeps its body on the next line.** Where the steps have no numbers, the line
  under one that ran to the right edge was glued to it ("for each word w in the line do count =
  count + stress(w)"). It is not the rest of the line above when that line opens a block or
  when it holds an assignment; and, where the lines are not set to a right margin
  (`_RightEdges.firm`: any page set ragged, and a page of little but the algorithm), when it
  stands inside a block where the step above starts ("put a tea bag in the cup and pour the
  water", "wait three minutes"). A step that goes on for more than two lines is a paragraph
  and ends the rows before it.
- The eight public files the algorithm change touched read the same before and after these
  fixes (elements compared one by one), so what that change gained is kept.
- **The text after an equation is not the algorithm going on.** After a display equation an
  algorithm with no numbers goes on only at a step word or an assignment written with an arrow
  or ":="; "where d = 12 is ..." is a paragraph.
- **A comment or a short line stays in its numbered block.** A line with no number, right of the
  numbers, with the next number within two rows below it, is the rest of the step above. An
  unnumbered comment line or a step that broke early split the algorithm into two blocks with a
  stray paragraph between.
- Left, and written in docs/layout.md: the part of an algorithm on the page after a page break;
  typed pseudo-code in a proportional font with two-digit line numbers (levels one too deep
  from line 10); a page that holds only an algorithm whose first step has no step word (the
  step is read as the second line of the caption).

And in the Word reader (`source/docx_package.py`), after the same review:

- **Every strike is read.** Pandoc does not read a double strike (`w:dstrike`) or a strike a
  character style sets, so such words came out as live text, and a struck run in the "Verbatim
  Char" style came out as live code. The package step now writes each of them as `w:strike` on
  the run itself (`_keep_strikes`, `_struck_styles`: the style's own setting or that of the
  style it is based on) and takes the code style off a struck run. All are `deleted` runs. Not
  read: a strike set by a paragraph style.

## Unreleased: fixes after the independent review of this wave (review 6)

- **A one-page quiz keeps its name line and its points** (`layout/furniture.py`). The rule
  that finds a head or foot by its shape on a one-page file took any lines at the edge of the
  page that stood clear of the text and ended in a number: "Name: ... 20", "Quiz 3: Limits and
  continuity ... Points: 25", "Total marks for this paper ... 50" went with the running heads,
  counted as furniture and nothing more. Lines with words now go only with a number that says
  it is a page's ("Page 3", "Page 3 of 9", "3 of 9"); a page number alone on its line goes as
  before. A journal's head with a bare page number beside its name stays on a one-page file.
  Parsing benchmark (olmOCR-bench, headers_footers, no model, 266 pages, measured again at
  the wave's last commit): 38.6 (244 of 632 checks, 95 % interval 34.9 to 42.5), between the
  34.5 (218) of pipeline 0.5.13 as deployed and the 51.4 (325) of the rule as first built;
  the other four categories pass the same number of checks as at 0.5.13; the difference
  is the heads this rule can no longer tell from a quiz's own lines. Not built, to be
  measured: taking such lines when they are set smaller than the body text.
- **A struck-through run in a fixed-pitch font stays struck** (`source/docx_package.py`). The
  package step gave every run in such a font the code style, and code carries no strike, so
  "~~x = 5~~ x = 7" in Courier New read as one live "x = 5 x = 7". A run with `w:strike`
  keeps what it has and is a `deleted` run, as in 0.5.13 (a double strike, `w:dstrike`, was
  read by neither version: see the review-7 entry above).
- **"(i)" after an "(h)" that has sub-items goes on with the letters** (`layout/lists.py`).
  The marker was read against the innermost open level, the bullets under "(h)", so it was
  roman one: a list of one item under "(h)", which "(j)" then left. It is read against the
  open level the item stands on, after the levels right of it are closed.
- **The mark that ends a fraction goes with its equation** (`layout/drawings.py`). A comma or
  full stop set between the two lines of a fraction, beside the numerator, was a row of its
  own once its equation number was cut from it, and came out as a paragraph of one comma (two
  public papers of the development sets after this wave's change to the cut, ten before it).
  A lone mark level with a maths row and just right of it is maths with it, the rule
  `_same_line` already has for rows that are maths.
- **An italic title that opens like a surname is still a title** (`layout/lists.py`). "B.
  Offline, Online & Optimistic Learning" reads like a line of authors ("B. Smith, J. Doe"),
  so the marker was held back and the title was a paragraph between two headings. The test for
  authors is for rows not set whole in italics or bold.
- **A carried table header is said** (`pipeline/notices.py`). A part of a table that is
  written with the header row of the part before it gets the optional notice
  `table_header_carried`: the review card shows the grid stored for the part, which has no
  header row, so the notice is where a reviewer learns what students get.
- Stored results: the notice list of a document with a continued table changes, and PDFs of
  the shapes above read differently, so this goes with the wave's `PIPELINE_VERSION` raise.

## Unreleased: algorithm blocks with line numbers, wrapped steps and equations (branch fix/algorithm-blocks, O-050, WS2.6)

- Algorithms (`layout/blocks.py`: `_algorithm_groups`, `_scan`, `_is_algorithm`, `_place`): a
  line-numbered algorithm is one `<pre><code>` block whose line numbers are kept apart from the
  words (a right-aligned gutter) and whose indentation is the steps' own; a step that wraps and
  goes on at the left edge is one step (a word broken at the wrap is mended); an algorithm with no
  "Algorithm n" line is found from its steps when they are numbered or form an indent ladder with a
  closing step; a display equation in the middle leaves two blocks with the same levels, the
  equation keeping its crop and its place (a formula of one line that is a step by itself is a
  line of the block instead, its formulas inline equations: in `ax_eesssp_240306423`,
  `ax_eesssp_240306460` and `ax_statme_240307124` the equation counts moved by 1, 1 and 4).
  Numbered lists, tables with a line-number column and monospaced listings are not taken. Not
  built: an algorithm that goes over a page break is read page by page, so the part on the
  second page is a block only when it proves itself an algorithm with no caption. Four fields
  on `_Row` (`algo_level`, `algo_number`, `algo_start`, `algo_join`) carry the result.
- A paragraph that opens "Algorithm 2 shows ..." or has "Algorithm 2." at the start of a line is
  not an algorithm when some of its lines begin with "for" or "if": lines that all run on from
  one to the next are prose (`_reads_on`).
- Word export (`export_docx._lines_of`): the gap after a line number is no-break spaces too.
- PIPELINE_VERSION and RENDER_VERSION are not raised here; a raise is owed (stored documents with
  an algorithm change their block text).

## Unreleased: Listen's side panel for a large picture, and very wide equations on paper (branch fix/wide-print-and-listen-column)

- Listen (`web/listen.py`, `web/routes/listen.py`): a picture whose data is over `MAX_THING_IMAGE`
  (150,000 characters) is no longer dropped from the side panel. The panel loads it from the crop
  route the student page already serves (`/jobs/{job_id}/crops/{element_id}.png`, the same view
  access as the transcript), so the Listen page does not grow by the picture. Where there is no
  crop to point at, the panel says "This picture is on the student page." The limit is not raised.
  In the 73 stored pages measured, 32 of 810 pictures (16 pages) are over the limit, 11.7 million
  characters in all. The loaded picture is `loading="lazy"`, so a narrow window, which does not
  draw the panel, does not fetch it. No stored document changes.
  (`test_a_picture_too_large_to_copy_is_loaded_from_its_crop_by_whoever_may_see_the_page`)
- The Listen side item stays for windows of 64 rem and wider (WS23.4). Measured at 320, 390 and
  768 px, a figure is 890 to 1,030 px wide at its own size, a table can have 40 rows, and the
  sticky transport takes 58 to 106 px, so no narrow layout is added. The reasons are in `docs/web.md`.
- A very wide equation on paper (O-094, SW45 rule 6) is not changed: a named landscape page
  prints in Chromium, but the equation's scale and note are decided by container queries that see
  the portrait width, and on pages made by the assembler it still cut the equation. `docs/output.md`
  ("On paper") records the measurements. The stylesheet, the stored pages and `RENDER_VERSION` are
  unchanged.
## Unreleased: leftovers of the source readers (branch fix/source-reader-leftovers)

- A LaTeX source that makes LaTeXML say a command calls itself stops with its own sentence,
  `source_loop`, instead of the general "could not be converted" (a self-call) or the wrong "a
  brace is not closed" (a nest that never closes: `\def\a{{\a}}`). It names the command when
  LaTeXML does, says trying again will not help, and says to compile the file on one's own computer
  and look for the command (`source/report.py` `LOOPS`, `web/views.py`). Measured on six
  sources: a self-call or double call ends in 2 s and a nest in 1 s; two commands calling each
  other and a counter loop run to the 120 s limit and stop as `source_timeout`; a growing list
  stops at the 512 MB limit in 20 s with Perl's "Out of memory" and is `source_failed`, and so does
  a source of 766 KB: nothing in the log tells those apart, so they are left as they were. No
  process is left behind in any of them.
- A link inside a heading, and inside a footnote, of a Word or LaTeX source is kept like one in a
  paragraph (before, the words stayed plain). A Word footnote's web address was dropped by the
  working copy of the file (the footnotes part's own relationships were left out) and is kept now.
  Links in a table cell and a caption stay plain words: neither has a place for a link
  (`TableCell`, `Element.caption`).
- Two spaces from two parts of a LaTeX paragraph no longer shift the links and runs after them by
  one character (the reader squashed them after it had counted the offsets).
- A picture in the middle of a LaTeX sentence between two letters no longer joins the words around
  it ("Press[icon]to start" read "Pressto start"); the picture is still a figure straight after the
  paragraph.

## Unreleased: what the pages promise, and leftovers of the Word and export work (branch fix/page-promises-and-leftovers)

- The pages rule (`web/quota.py`, `PAGES_RULE`) says what cancel does: a document canceled before it
  starts, or stopped for being only scans or handwriting, gives its pages back while it is still the
  day of the upload (the count is by day, so nothing comes back after midnight UTC); any other
  failed or deleted document does not. A canceled document a worker had begun keeps its pages
  (`test_canceling_gives_the_pages_back_only_before_a_worker_began_the_document`).
- The checker's draft says "In the student's file, a link here goes to the document's contact line." where the
  student's file has the instructor link on a flag note, instead of carrying the link
  (`assemble.DRAFT_CONTACT_NOTE`). A draft is never stored; no student file changes.
- The pages read the limits and days they state from the setting or constant that holds them
  (LaTeX help, the review help's page size, the status window, the admin canary window, the
  retention days on the account, upload and landing pages), so a change to one moves the page.
- The privacy notice has claims the launch check ties to their code or setting: source files, the
  conversion result, Listen's kept documents, the sign-in days, the takedown address and a LaTeX
  build (`ops.launch.privacy_claim_problems`). The retention summary is a download for an
  office (`/privacy/retention`), written from the same settings, data list, recipients and deletion
  rules as the notice and `/security`.
- The privacy notice names the model's answers for the steps a conversion has finished, which
  are kept with the document so that "Try again" asks only for what is missing
  (`web/step_cache.py`; they were stored and deleted with the document before, and not named).
  Its sentence about temporary folders names only the steps this service runs: a LaTeX build
  and Word and PowerPoint conversion are left out where they are off. Both are launch claims.
- CI installs pandoc 3.1.11.1 from the upstream release by exact version and sha256
  (`.github/actions/pandoc`), the version production runs, in both jobs that need it.
- Docs name no model, price or version a file in the code already holds; they point to it
  (`config.py`, `ops/production.profile.toml`, `models/pricing.py`, `ops/limits.py`).
- The review reasons for a formula a Word or LaTeX source could not convert, and for a figure the
  model could not describe, no longer speak of a PDF or an image the item never had.
- The record leaves out "Pages" for a Word or LaTeX source (it showed the reading's one placeholder
  page), in the page and the CSV.
- A job page opened before the Word file was first made says what the file does with an equation
  Word cannot take and that the count shows when the page is opened again (no new polling).
- Combined files (Combine parts) have their own count of 3 on the first part, so they do not push
  that part's Word, EPUB, Canvas or Cartridge files out of the kept set (`export_store.MAX_COMBINED`).
  No stored page, result or model request changes; no `RENDER_VERSION` or `PIPELINE_VERSION` raise.
## Unreleased: operations and storage, measured (branch fix/operations-and-storage)

- Production profile: each setting that switches a behavior or picks a model has a `reason`
  (one sentence) and an `eval` (the repository path of the report that measured it, or `none`).
  Where the repository gives no reason the row says "not stated" (17 rows). /admin shows both
  next to a setting that differs. The profile's hash is unchanged (`90c66bc8ef67`): neither field
  is in it. No stored document, page or model request changes.
- Job page: while the layout runs (the longest silent stretch of a conversion: for a 100-page
  document of public papers 89 s of 167 s with nothing counted, the other steps' longest being
  23 s, measured 2026-11-19 on a loaded box) the first line reads "Reading the layout: 7 of 16
  pages" with the step's own progress bar, and its longest silent stretch is 27 s. `layout.analyze`
  takes `on_page(done, total)`, called before each page's table search; it goes through the same
  stage events and the same 2 s write limit as every other step, with no new polling. A scanned
  document's wait is in the page-reading pass, which this does not count (47 s of 107 s with
  stubbed answers). Nothing a conversion writes changes.
- Docs, measured 2026-11-19 (no code): peak memory of Word (0.41 to 0.42 GB; LibreOffice does not
  add 300 MB to it, about 190 MB and not running at the child's peak), of MuPDF with and without a
  second parser (no addition shown), and of a dense 100-page document (2.0 to 2.1 GB, which the
  "0.5 GB" figure did not cover); PowerPoint could not be measured on this box (no Impress).
  What a document stores by table, the largest row, and where a per-job cap would go (not built:
  at four times the largest measured it is above the per-person cap); the stored page's pictures
  and the step-record load (nothing built); what stops each of the three residues of the content
  budget, and the real paper the page budget refuses (budget kept: accepting it needs a 1.5 GB
  layout child and about 100 s).
- No test of flat memory across ten conversions is kept: conversion 10 was within 5% of
  conversion 1 in 7 of 9 runs, and what grows is the allocator's retained memory and SQLite's
  page cache, not Python objects.

## Unreleased: the page and the exports, leftovers (branch fix/output-leftovers)

- The count of equations written as MathML (the student note, the job page, the record and its
  CSV, the Canvas README, the EPUB metadata) is the page's own: an equation counts only when the
  sanitizer that writes the page accepts its MathML, so a refusal that shows the picture is never
  counted as MathML (`checked._page_accepts`). No stored document or recording in the corpus has
  a refusal today, so no count moves; the rule is now one.
- A kept picture's box that can scroll sideways under 40 em (a crop at least 240 CSS px wide) is a
  named tab stop ("Original picture", `role="group"`), so a keyboard user can scroll it.
  `RENDER_VERSION` is owed a raise: a stored page gets it when it is made again.
- A script inside a script outside math (`sup` in `sup`, `sub` in `sub`, the mixed pairs) is as
  large as the script it is in, not 12.5 px, in the student sheet and the app. The sizes of
  scripts inside math are measured and written in `docs/output.md`, with the reason no floor
  is set.
- Word: a picture the page marks as decoration (empty alt) is marked decorative the way Word does
  (`adec:decorative` on its `docPr`).
- "Combine parts": each part is settled for decoration as its own page is, and the file is built
  once for its parts and kept like the other exports (3 parts of 30 pages took 0.75 to 2.5 s each
  time); it goes with any part.

## Unreleased: region pictures whole on a phone and sized before they load (branch fix/review-second-batch)

- On a phone a heading's band, the lines around an item and a region cut on request are no
  longer cut to a strip with a fade: each is shown whole in the box a crop uses, at most one and
  a half times the column wide, which scrolls sideways, can be reached from the keyboard and
  says it is wider than the screen.
- Pictures cut on request (a heading's band, an item with the page around it, the repeated
  headings' copies in both views, a table's picture, the region preview of the page tools) and
  the job card's example picture carry their width and height, so the page does not move while
  they load. A heading's or a table's picture is cut to whole lines, so its height is a little
  smaller than the one given and the page moves by that once.
- The keyboard browser test runs "A" on a repeated-heading group the page advises as bold: focus
  goes to "Keep as headings" and the status line says why. The path was never broken.
- `docs/web.md` says why the list view has no shortcuts and no reordering, that the pane is a
  header's height short once the window is scrolled, and that the instructor's preview is the bare
  student file. Nothing changes in the student's page: `RENDER_VERSION` is not owed.
## Unreleased: bold, italic and code from Word and LaTeX sources, and their color counted (branch fix/emphasis-runs-from-sources)

- A Word or LaTeX source now keeps the bold, italic and code words it states, as the same
  `strong`, `emphasis` and `code` runs a PDF page makes (`<strong>`, `<em>`, `<code>` in the
  page, Word, EPUB and Listen). Word: a run's bold and italic, the highlight a run carries, the
  character style "Verbatim Char", and a run set directly in a fixed-pitch font (Courier,
  Consolas, Menlo; pandoc drops that font, so the package step gives the run the style).
  LaTeX: `\textbf`, `\emph`, `\textit`, `\texttt` and `\verb`, from LaTeXML's classes (an `\emph`
  inside an italic theorem body is a run). The PDF path's rules apply: a style on 70% or more of
  a block (a bold heading, a whole italic theorem, a bold header cell) is the block's own and
  makes no run, a run never holds an inline equation, and a heading, program code or a
  glossary entry gets none. The text of every element and every id are unchanged, so no
  recorded model request stops matching.
- A table cell of a Word or LaTeX table keeps its runs (`TableCell.runs`, optional and left out
  when empty; math cells have none) and the table writes them: `<td><strong>closed</strong>
  book</td>`. A cell edit in the review screen takes them off, like a text edit that changes
  words. A release from before this one reads a stored result that has a cell run and drops the
  run: the cell is plain text there (checked against 0.5.13).
- Color in a Word or LaTeX source is counted like a PDF's: with `FEATURE_COLOUR` off the job gets
  the optional notice `colour_used` with the number of passages. The Word reader never carries
  color (pandoc drops it), so it counts from the file's own runs and always raises the notice;
  the LaTeX reader used to keep color whatever the setting and now keeps it only with the flag
  on. `RENDER_VERSION` and `PIPELINE_VERSION` are owed a raise: a stored Word or LaTeX result
  has no runs and no `colour_used`.
- A font named for the Monotype foundry ("Monotype Corsiva", a script face) is no longer read as
  fixed-pitch because its name holds "mono": in a Word file its words were about to become code,
  and on a PDF page they already were (`docx_package._MONO`, `layout.glyphs._MONO_FONT`). Monaco
  counts as fixed-pitch in a Word file.
- `CONTENT_RUNS` off takes the runs off a Word or LaTeX table's cells too, as it does off the
  text.
## Unreleased: records, replays and small guards (branch fix/records-and-small-guards)

- "Read again" counts toward the AI service's status. A reread is the one model request the web
  process makes itself, and the status row (`/status`, the job page's "AI service not
  answering" line, `/admin/health`) was written from the worker's requests alone, so rereads that
  failed because the provider was down left it green. Each request a reread sends now tells
  `HEALTH` how it ended; with `WORKER_MODE=service` the web process writes the same times and
  outcome words to the row `provider.web` of `ops_heartbeat` (the worker rewrites `provider`
  whole at each beat), and the status page adds the two. No content is stored.
- A Word file's unread words stay on the stored result. The reader counted them and the job's
  notice said them, but the stored result held no source report for a Word file, so
  `checked.unread_of` said (0, 0) after the result was loaded again (the review page's student
  note, the owner's line with the counts). A Word file that left words or pictures unread now
  keeps a source report with those two counts. Word reading is off by default, so no stored
  result is affected.
- `eval burden` fails a record in which a decision of band 1 or 2 of the earlier record is
  optional now (named by upload and element), which the counts could not see. The rows of a
  record now hold each decision's element and band and the elements that are optional; a record
  made before them is compared by the counts alone and the output says the band check was not
  made. The kept record `data/eval/burden-corpus.json` is one of those and is not made again.
- Tests: the account deletion sent twice with one request token answers "deleted" both times.
  The two TeX compiles (`gold/safe_tex.py`, `source/tex_compile.py`) say in their docstrings why
  there are two and which a new caller uses. The Landlock busy-text test accepts `source_timeout`:
  on a loaded machine LaTeXML ran out of its 120 s (2 of 20 runs failed that way).
## Unreleased: the header of a table that runs onto the next page (branch fix/across-a-page-break)

- A table that goes on from the page before was two tables, and the second had no header row, so
  a screen reader read its cells with no column names. The page now writes each such part with
  the header rows of the part before it (all of them, merged cells included) when the part has
  no header of its own and the same columns; a part that printed its header again keeps its own,
  with no second one. Where the columns do not agree the part stays as it was and the new notice
  `table_header_missing` ("Worth checking") says it has no header row. The stored tables and the
  table step's request are unchanged, so recorded answers still match. `RENDER_VERSION` is owed a
  raise: a stored page with a continued table is written differently.
- A part that goes on with no header after a first part whose header was printed once was not
  marked "continued from previous page": the header is wider than the figures, so the first
  part's column edges sat up to 21 pt from the next page's. The edges may now differ by 6 % of
  the page width (3 % otherwise) when the part before has a header and this part has none. A
  different column count is still not marked. `PIPELINE_VERSION` is owed a raise for a document
  that has one.
- A table at the top of a page was marked as the rest of the last table of the page before even
  when that table stood in the middle of its page with text under it, and with the change above
  it would have been given that table's header. A table with text under it in its part of the
  page's width has ended (`tables._at_foot`). On the 996 corpus PDFs this takes the mark off one
  table, on a public paper (its "Table 11" was marked as the rest of "Table 10"), and no other.
- Paragraphs cut at a page break: three more cases are joined, each on evidence that the sentence
  cannot end there. A paragraph whose block began in the other column of its page and ends the
  page in a column's body text; a paragraph that ends on a comma or a semicolon, whatever letter
  the next page starts with; a next page that starts with a citation shut by punctuation
  ("[12]."). A head that is a "Table N" or "Figure N" caption never joins: the corpus run showed
  a comma tail joined to a "TABLE VII: ..." line. `PIPELINE_VERSION` is owed a raise for a
  document that has one.

## Unreleased: speech for bars, raised and lowered text in Listen, names for sidebars

- Speech: `\{x | |x|<1\}` (a plain bar before an absolute value in set braces) read "vertical
  bar" three times; the first is now "such that" and the others an absolute value. `\|A\|_F`
  (a norm with a subscript, whose second bar is a script's base) read both bars literally; it is
  now "the norm of cap A end norm sub cap F". `|A \times B|` read "the absolute value of cap A
  times cap B": bars around a cross of two things that are not numbers can be a size, a
  magnitude or a determinant-like value, so they keep "vertical bar" and the cross is named
  (`|3 \times 10^{-5}|` is still an absolute value). `SPEECH_VERSION` is 4, so a stored reading
  is said again from its MathML where it is shown (no model call). `RENDER_VERSION` is owed a
  raise: the stored page's sayings and sidebar names changed.
- Listen: raised and lowered text outside math (`g/cm<sup>3</sup>`, `x<sub>1</sub>y`, a
  footnote's mark) is said with "superscript" or "subscript" before it, and "end superscript" or
  "end subscript" after it when text goes on right behind it; the ending of an ordinal ("10th")
  adds no words. The page, Word and the EPUB are unchanged.
- A student page with more than one sidebar names them "Sidebar 1 of 3", "Sidebar 2 of 3" and so
  on (`aria-label`), so a screen reader's list of regions tells them apart; one sidebar is still
  "Sidebar". The EPUB, Canvas, the cartridge, the combined file and Listen carry the name from
  the page; Word keeps the one style "Sidebar".
## Unreleased: small wording faults, one date and one decision record (branch fix/small-words-and-records)

- The privacy notice dates the review-screen row 2026-11-19, the day the release that carries
  its sentence went live, not 2026-11-11, the day it was written.
- `readablestem convert` no longer needs `--out`: the file is named for the input, and a draft (decisions
  still open) ends in `-draft` as a web download does. With `--out` the name is kept and a line
  says the file is a draft. A command-line draft is worded for the person who checks it, as the
  web draft is.
- The Symbols screen, the reason on a page kept as a picture and the record say what reading
  scanned or handwritten pages uses, in the words of the upload form.
- A Word or LaTeX source says nothing of the exam or quiz choice on its job page or its record.
- Comments in the style sheets say "color"; `docs/deploy.md` says which reservation applies when;
  the upload form and job page of the exam choice are scanned by axe at 1280 and 375;
  `docs/decisions/0010-reading-order-rules-first.md` records why order is found by rules.
- No `PIPELINE_VERSION` or `RENDER_VERSION` change: the student file and its stored HTML are as
  they were.
## Unreleased: the table search stops on CPU time (branch fix/table-search-cpu-time)

- **The table search's limit is CPU time, and a stop says so.** The search for tables by the
  layout model, four fifths of page-reading time on the slowest pages, had a limit of 60
  seconds of wall time a document (after 3 free a search), so a busy machine stopped it where
  a quiet one did not, and the pages after the stop read as if they had no tables with only a
  log line to say so. The limit now counts the CPU time of the thread that searches, and it
  is 360 seconds: replayed from the recorded searches of two sets of arXiv papers
  (`python -m readablestem.eval.table_search`), no paper of 70 and of 91 is stopped at four
  times its cost (the first at 4.85 times); the old limit stopped 2 and 1 at twice, 24 and 24
  at four times. A stop writes the "not finished" fact `table_search` with `time_limit` for
  each page it cut short, each such page gets the notice `tables_not_searched` ("Worth
  checking": a table there may be read as plain text), and the job is not "Ready" with nothing
  to add. Evals and replays run without the limit (`TABLE_SEARCH_UNLIMITED`), and a replay in
  which the search stopped anyway is counted as failed (`table_search_stopped`), not measured.
  A document within the limit converts as before: no version bump is owed.
## Unreleased: deletes and removals ask first, the draft token, carried edits, dates (branch fix/deletes-carry-dates)

- The share page's Remove acted at once: one click took a person's access away, from a filled
  red button, with no way back. It now opens the question "Remove <address> from <title>?" as
  the answer to the same POST, as a course's People section and the Admin page do, and removes
  only with `confirm=yes`; the button is the outlined kind of every other asking button and its
  busy label says "Opening…" (`routes/sharing.revoke_share`, `share_remove_confirm.html`). A
  second click on a person already gone gets the first click's notice. Access cannot be
  undone with a notice because the person would have to be shared with again, so the question
  is the better fix.
- The job, upload and course deletes were listed as keeping an older opener. They already
  ask first on one shape of page (the question as the heading, one short paragraph of what is
  lost, two choices of the same size, "No, keep it"); `_delete_notes.html` is the "Not reached
  by deleting" note that the account deletion page uses too, and it belongs only where stored
  copies are deleted. No code changed; docs/web.md says so.
- The draft confirmation was tied to `Job.result_version`, which every review write raises. A
  write that left the file alone (answering the cut-off question) voided it, and the token
  could not tell a change a student would get from one they would not. It now names
  `Job.output_version` (raised by `output_changed`) and is signed over it (`output._signature`).
- A hand edit carried to a revision was carried once and then lost on the next revision: a
  carried edit left no snapshot, so a second revision found nothing to carry. The carried row
  now keeps the change's before and after (`worker._carry_decisions`,
  `carry_snapshot.read`), so it is carried again, and an edit that an older release carried
  without a snapshot is named as not made again instead of dropped silently.
- A revision that was canceled or failed kept its carry snapshot, and the storage report did not
  count it. A failure that offers no "Try again" now drops the snapshot (`worker._end_snapshot`);
  a canceled one keeps it, because "Convert it" and "Try again" still need it after the earlier
  document is deleted. `JobBytes.carry` counts it in `total`, in `ops storage-report`.
- The job page's Combine line said "the N finished parts" without the parts held back. It now
  reads "Combine the N ready parts (leaves out M)" and links to the combine page, and says how
  many parts are ready when fewer than two are (`JobView.combine_left_out`).
- Dates were written by hand in a dozen places (a month array in `guide_compare.html`, `%B %d`
  strings, ISO days on the Admin page and in the offices summary). `web.dates.format_day` and
  `format_time` now write them ("November 4, 2026", "00:00 UTC"), the `day` template filter
  uses them, and the pipeline's About version date and finish sentence use
  `checked_words.long_date`: "the last on <date>" now carries the year. A render bump is owed
  for the finish sentence; this branch does not raise `RENDER_VERSION`.
## Unreleased: eval records that say what made them, and small guards (branch fix/eval-stamps-and-guards)

- Every eval report and measured record now carries `provenance`: the commit, whether the tree
  had changes, the pipeline and render versions, and the versions of pandoc, LaTeXML, PyMuPDF
  and the fonts (`run_record.provenance`, one helper for every writer: `eval pipeline`,
  `eval tables`, the measured records, the ledger, the page audit and the calibrate probes).
  `eval gate` prints who made the current report; a report made before has no `provenance`
  and is still read, as "not stamped". No kept record was made again.
- The burden record counts the equations and tables with math that the check against the page
  could not compare, by cause (`no_characters`, `no_usable_characters`, `unmapped_characters`,
  `bold_letter`), and `eval burden --same` also compares how many answers each upload lacks.
- A math cell of a table that is held against nothing writes the same passed check an
  equation does (`table.math_not_compared`), with the cause. No card, no second reading.
- The proof replay's code identity takes its settings from the gate's list
  (`gate.guarded_settings`), so the two cannot differ. A bundle recorded with a shorter
  identity is compared on the keys it names.
- `eval.gate.UNGUARDED_CONSTANTS` and a test: a numeric constant of a step module that is in
  neither list fails `tests/test_eval_gate.py`. Several that decide a request or a bar are
  marked "NOT YET GUARDED" there; guarding them waits for the two no-model reports to be made
  again.
- The workflow tests' `OWED` list (29 files, 1,286 answers) is read from the replay record
  instead of kept by hand. `eval/render.py` `png_bytes` checks the pixel budget before it
  decodes. `docs/eval.md` no longer names an items file the local tables evaluation does not
  use.
- Text read without `font_codes.remap`: the PDF's own heading tags (`pipeline/tagged.py`), the
  bold letters counted for the before and after card (`source_facts._bold_letters`) and the
  old `parse` listing now read a symbol or TeX font's codes as layout does. A heading set with
  a ligature code ("effects", with its ff one control code) had its tag text "eects" and
  matched no element; a bold Symbol letter was counted as a bold plain letter.
  `page_type.probe_pages` is unchanged: it counts non-space characters, which a code map does
  not move except by a ligature's second letter.
- A page joined from several parts names the pipeline versions the parts were converted at in
  its generator tag (`ReadableSTEM 0.1.0, pipeline 0.5.11, 0.5.12`), where it named only the
  package.
## Unreleased: what was left before a Word file can be read from the file (branch fix/word-source-leftovers)

- A Word or LaTeX table with no header marking was accepted with no decision, and was read to a
  blind student as bare cells. It is now the choice a PDF table with no header has (first row,
  first column, both, or "not a table"), required, with no model call
  (`ltx_reader.headers_to_choose`). A one-row table is left as it is.
- A Word table too large for a grid was written as list items holding raw TeX. Each formula in it
  is now an equation of its row, read and checked like any other, and the table is the required
  notice `word_table_flattened`.
- `docx_reader` no longer keeps an `errors` count nobody read: a failed equation is a required
  decision where it is shown, and is counted in the reader's `Report`.
- A source's review list named "page 1" for every item and offered a page filter. It names no page
  and has no filter (`ReviewItem.page` is 0 for a source).
- The student's words for an equation of a Word file that is shown as LaTeX said it was written in
  the Word file. They now say the tool made the LaTeX from the Word equation (the note on the page
  and the sentence in the notice at the top).
- A Word file read from the file that fails says what to do: save it as a PDF in Word and upload
  that. The job page no longer links a Word failure to the LaTeX help.
- The Word file's count of equations written as words is kept with the built file and said on the
  job page beside the download.
- `RENDER_VERSION` is owed for the page note of a Word equation shown as LaTeX; none is raised here.
## Unreleased: hidden words, annotation art in the ink reader, and the record's handled lines (branch fix/hidden-words-ink-turns)

- What an annotation paints (a sticky note's icon, a highlight's box) was left out of figures and
  handwriting by layout only. The upload probe (`page_type._probe_ink`) and the stroke reader
  (`ink_strokes.extract_ink`, whose strokes go to the reader of a handwritten page) took the page's
  drawings whole: on a handwritten page a sticky note added 4 strokes and a highlight 1. Both now
  read the drawings through `without_annotation_art`, as does the page-type measurement
  (`eval/page_types.py`). A pen stroke (Ink annotation) is still read as handwriting.
- Text painted where nobody sees it (under 2 pt, or flat on the page) is counted in the stored page
  type (`PageType.unseen_chars`, written when above 0, so older records load). A kept page whose
  only hidden text was of that kind had a notice with its count but nothing on the review screen
  and the job page: they read the stored record, which did not have it (`left_out_chars`).
- A typed page with 400 or more characters of hidden or covered text (`page_type.HIDDEN_BLOCK`)
  now has the required line `hidden_text_check` instead of the optional `hidden_text_ignored`, and
  keeps a finished job at "Ready" (`states._HOLDS`). The cut comes from the corpus census
  (docs/layout.md): pages with less were debris; the pages with more had a text layer of their own
  beside drawings. Not built: a page tool that keeps those words in the file. It needs the words
  read again from the PDF as blocks, a stored decision, an undo and a place in the model's context;
  the line tells the instructor to add them to the source and upload again.
- The review record and its CSV have "Handled in this file" (section `handled`): the same lines
  as the job page, from `job_page.handled_lines`. A page saved sideways reads as such only on a job
  read after the line existed; an older job's page is right all the same (its elements are in the
  turned frame and every later reader turns the page again), so the turn is not stored.
## Unreleased: a one-page file keeps no running head, foot or page number (branch fix/one-page-furniture)

- A file of one page has no second page for a running head or foot to repeat on, so the
  repeat rule found nothing, and a head such as "Journal of Rare Diseases ... Page 6 of 11" with
  its address line under it, or a foot with its page number, stayed in the text. A page
  cut from a journal is how the benchmark gives such a page to a reader, and a student's
  one-page handout carries them just the same. `_MarginFilter.by_shape` now finds them by their
  shape (`furniture.py`): a stack of at most 4 lines in the outer 12 % of the page, a line's
  height clear of the rest of the text, none set as a heading, one of them with a page number at
  its start or end (digits or "Page 3 of 9"; a bare year is not one). A head with no page
  number of its own (a draft stamp, a title) stays, as before. What goes is counted in the
  "Left out: furniture" note, as on a longer file, and a page number is no longer lost
  to the guard of a line mate it had only because the head stayed.
- A page number alone at the foot or head of a page of any length was missed when the line
  stood between 7 % and 12 % from the edge (an A4 page's number 90 % down, past the 7 % band
  of `drops`). The same rule takes it when it stands alone with the edge to itself.
- Corpus hash run against 957c9310 (73 uploads replayed, 332 PDFs in three variants): one
  upload changes (a 5-page handout loses one paragraph, a lone "1" 92 % of the way down a page,
  now counted as furniture), and 21 PDFs change in `analyze` (papers of the development sets
  and the repository's own samples), each by the bare page numbers of 1 to 27 pages that stood
  7 % to 12 % from the edge, never a line with words. The rows taken in every one were read.
- Parsing benchmark (olmOCR-bench, no model, 747 pages, pipeline 0.5.13): headers_footers 34.5
  to 51.4 (325 of 632 checks); table_tests, long_tiny_text and baseline did not change;
  multi_column 69.2 to 69.1, two order checks of one journal article whose gold counts its
  running head as text (docs/eval-parsing-benchmark.md). On layout-dev-1 and the corpus gold
  the pages right (137 of 267, 35 of 45) and the pages that read furniture aloud (57, 8) are
  the same before and after. Not built, measured: the same rule for files of 2 or 3 pages
  takes figure labels and a line-number column on 2 of 11 such gold files. No version is
  raised here: the result of a one-page upload changes, so a release that carries this one owes
  a `PIPELINE_VERSION` raise.

## Pipeline 0.5.13, render 9 (2026-11-19)

- `PIPELINE_VERSION` is 0.5.13 and `RENDER_VERSION` is 9: one raise each for every "Unreleased"
  entry below down to the 0.5.12 entry. What a PDF's result holds changed (lists, where a block
  starts, paragraphs joined across a page, a table's box, the region tree), a paragraph can hold
  a role and a reviewer can leave one paragraph out; the student page changed (the sidebar and
  quotation wrappers, the left-out note, a part that was not converted said once, the words
  for a Word or LaTeX equation shown as its LaTeX). Stored results load unchanged and their
  pages are made again. `data/eval/latest-headings.json`, `data/eval/latest-tables-local.json`
  and `data/eval/burden-corpus.json` are re-made at 0.5.13; the golden pages and the stored
  demo page are written again at render 9. Roll back: docs/deploy.md.

## Unreleased: the review screen's names, long pages and out-of-date messages; step keys (branch fix/review-names-lines-keys)

- The side-by-side review screen said "by you" for a decision another person made. It names
  them now, with the same words as the review list (`routes/review.py` `decider_names`, used
  by `SplitBuilder.item`); the delete page no longer says "You made N decisions" when
  reviewers made some.
- On a page with more than 120 lines of text the page tools stopped at line 120: the list of
  lines and the two selects of "Add something the tool missed" now hold every line. The
  options start with the line's number, so typing the number goes to the line.
- Undo refused for an out-of-date page said what was not done and not what to do next; it
  now says to check the page and undo again.
- A step's key (`pipeline.run.step_key`) holds the conversion options its step reads: turning
  exam mode on or off no longer discards the stored answers of equations and tables. Every
  stored answer was already tied to the conversion code, so this release finds none of the
  earlier ones either way.
## Unreleased: footnotes a file names, found and linked (branch fix/footnotes-found-and-marked)

- A footnote that ended without a full stop (a note that is a URL) took the first paragraph of the
  next column in with it: the paragraph's words were part of the note and the note's box ran over
  the page. `_merge_column_continuations` no longer continues a footnote a file names.
- A file that names its footnotes but puts a destination off the note's line left the note
  unfound: the symbol notes of a title or author block (`*`, a dagger: the destination is the
  corner of the page) and a one-line note whose destination is under its line. The first is
  placed by the glyphs its link covers, when exactly one low line of small type opens with them;
  the second by the line the point lies just under (`_place_by_mark`, `_row_at`).
- A footnote's mark was not linked to the note when an inline equation had taken the glyphs just
  before it, or the mark itself (a mark beside a letter in a math font). The mark's glyphs are
  not read as math, and the context that finds the mark in the text is cut down to the placeholder
  of the equation (`_mark_spots`, `NOTE_MARK_FLAG`). The cut context counts only right after a
  placeholder's end: found anywhere else, a one-digit tail and the mark matched inside a
  placeholder's own name on one paper and would have put the link in the equation.
- Docs: docs/layout.md "Footnotes".
## Unreleased: list leftovers (branch fix/list-leftovers)

- Answer lines of an exam: a numbered series that starts again at its first value ("(a)"
  after "1.", "(i)" after "(a)") is now a level under the item above even where the texts start
  at one indent, and "(b)" goes back to the letters. A list cut by a paragraph, a figure or a
  table goes on at its levels where the next item takes up the numbering. Before, the list
  ended at the first answer word and the items after it began again at level 1
  (`lists._list_levels`, `_Level`, `_Number`, `_taken_up`).
- A line that starts with a capital letter and a full stop and then reads as names ("A. Smith,
  B. Jones", "D. S. Turaga, ...", a surname and a comma) is a list item only when the item just
  before or just after it in reading order is the next or previous letter; some line of a long
  document with the next letter was enough, so a reference list or a byline became a lettered
  list (`lists._list_markers`, `_names`). Any other capital letter keeps the old test.
- "A. Problem Descriptions" set in bold or italics on a line of its own, a title of 2 to 12
  words that does not end like a sentence, is a heading one level under its section, not a list
  item (`blocks._mark_lettered_titles`, reason "lettered title", `document.heading_levels`).
  Only over a row in another face or the first line of a numbering, and never as the next
  letter of a list: the options of a question set in italics stay its options.
- A bold line that begins with a bullet glyph stays a list item; it was read as a bold heading.
  A bullet drawn beside the row (a label such as "Solution") changes nothing.
- No change to a prompt, a model or the version numbers: a PDF's lists, headings and levels
  change, so the next pipeline raise is owed.
## Unreleased: equation numbers that stayed in the text or became paragraphs (branch fix/equation-number-leftovers)

- A word of the math split by a limit set under it ("argmin" with the limit under it, read in
  x order as "argm", limit, "in") was read as prose, so the whole line, and the number beside it,
  became a paragraph. `shared._limit_end` reads the word on past the limit, and `argmin` and
  `argmax` are math words (`_word_lengths`, `figures._prose_spans`). On public paper
  2407.13734 this was why "(39)", "(42)" and "(45)" were paragraphs of their own.
- A line of math with a few English words in it ("for every n greater than zero") that has an
  equation number beside it is a display equation now, so the number is its label
  (`display._promote_numbered_lines`; only when the line is mostly math and stands in from
  the column's text edge).
- A bracketed number set closer than a font size to a display that fills its column, or a
  number set right-aligned under its equation, was left in the equation's text or became a
  paragraph; it is the label now (`display._closes_display`, `_number_clearance`,
  `_line_over_number`, `drawings._number_under`). The crop of such an equation no longer holds
  its number, so its request to a model changes with the new picture.
- Not changed: a number set left of the math at the margin ("(10.18)" in 2412.12319), a number
  that belongs to a group of lines each starting with a word, and symbolic tags as
  cross-reference targets (none in the development parts).

## Unreleased: fixes after the independent review of this batch (branch fix/review-5)

- A part shown as a picture had its "Not converted." label hidden from a screen reader whenever
  a picture was present. When the picture's alt text gave the PDF's characters ("v = d / t, as
  written in the PDF") or a description a person wrote, nothing a listener heard said the part
  was not converted. The label is now hidden only when the alt text itself says "not converted"
  (or "not described"), for a block and for an inline equation (`assemble._alt_says_label`,
  `inline_equation`). Two golden pages change by that attribute.
- Sub-bullets whose text starts exactly on the text edge of their numbered parent made each
  numbered step one level deeper than the one before ("1)" level 1, "2)" level 2, "3)" level
  3). `lists._list_levels` now keeps the marker series of each open level and sends an item of
  another series than the innermost level back to the open level of its own series at the same
  x. Sub-bullets 3 pt or more to the right were already right.
- Speech: `|S|`, `|A \cup B|`, `\det A = |A|` and `|G:H|` were read "the absolute value of",
  which is wrong for the size of a set, a determinant and a subgroup index. A pair of single
  bars around one capital letter, or around a row with a set sign (union, intersection, set
  minus) or a colon, keeps "vertical bar" as it did before this batch
  (`speech._size_or_determinant`). `|x|`, `|x - y|` and `|a| + |b|` are still absolute values.
- Review record: it said the words of a left-out paragraph "are in the changes below", but a
  paragraph left out by a change carried over from an earlier version has a row with no words.
  The summary now lists each left-out paragraph with its page and its words, read from the
  stored paragraph (`record.Record.left_out_blocks`), and the CSV has one summary row for each
  ("Paragraph left out of the student file", with the page and the words) in place of the one
  row of page numbers. Two paragraphs on two pages no longer read "(page 1, 3)".
- Two refusals a hand-made request could get past (the screens do not offer either): a
  paragraph left out is not put back while its page is left out (it was in the student file
  under a note that said the page was out, and the page's count of left-out paragraphs went
  stale), and text is not left out of a Word or LaTeX file, whose note would have named a page
  "of the original PDF" (`page_edits.put_back_block`, `left_out_blocked`).
- The accent check asked for a decision ("An accent differs from the PDF") on a correct reading
  whose text held an accented word (`x = 1 \text{ même si } y = 0`, "años", "não"): the PDF's
  side counted the word's accent and the reading's side counted only `mover` accents.
  `equation_step._mathml_accents` now counts the accents of the reading's `mtext` and `mi`
  letters too. A hat the reading dropped is still found.
- Page editor: a paragraph left out that is past the 120th line of its page is still listed,
  with "Put it back" (the list was cut at 120 lines, so it had no way back on the screen).
- A proof replay with `--allow-drift` on a checkout of another pipeline version missed every
  recorded axe-core result, since the page's generator tag now names the pipeline version and
  the result was keyed by the whole page. The key leaves that tag out (axe-core does not read
  it); a recording made with the older key is still found (`proof/replay.axe_key`).
- `web/page_dates.json`: /demo, /security and /help/review changed on 2026-11-19.
- `data/eval/parsing-bench-olmocr.json` is measured again at pipeline 0.5.13 (commit
  `3b177567`, $0): multi_column 69.2 (68.8 at 0.5.11), headers_footers 34.5 (32.5),
  long_tiny_text 37.3 (37.3), table_tests 66.6 (51.8), baseline 100.0 (99.0). The scores and
  counts of docs/eval-parsing-benchmark.md are the new record's; its failures by cause are
  still those read on the first run.

## Unreleased: two edits on one paragraph are two carried changes (branch fix/source-equation-words)

- When a document is converted again, a paragraph the reviewer both reworded (or made a heading)
  and set apart as a sidebar or quotation, or left out, was carried and counted as one change:
  "1 change you made by hand was carried over", and the record named only the last kind. When the
  paragraph had changed and none could be made again, "1 change was not made again" listed one
  kind, so a lost role was reported as a lost wording (or a lost wording as a lost "left out").
  `carry_edits._apply_words` now returns each kind it made, and `_carry_words` records each, as a
  carried change or as one not made again. No stored data changes: the counts and the record rows
  of documents already converted again stay as they were.

## Unreleased: a Word table cell whose formula could not be read is a decision (branch fix/source-equation-words)

- A formula in a cell of a Word table that fails the product's checks (a command it does not
  allow, such as `\phantom`) stayed in the cell as TeX with no mark, no decision and no notice, and
  the job read "Ready to share". `docx_reader._table` now makes the table a required decision
  with the check `source_formula_unread`, as the LaTeX reader does for a table and the Word reader
  already did for a heading. `Report.math_failed` (counted, read by nothing) is the count it
  reads. A failed equation of its own was already told to the owner: it is an element with a
  required decision, listed as not converted. The student page is unchanged; the job is held at
  "Ready" until the owner decides. Not covered: a table too large for a grid is written as list
  items whose formulas are TeX with nothing to hold a decision (noticed, not changed).

## Unreleased: a Word or LaTeX equation shown as its LaTeX is not called an image (branch fix/source-equation-words)

- An equation of a Word or LaTeX file that could not be made into MathML is written on the student
  page as its LaTeX; there is no picture of it. The owner was still told "2 equations are shown as
  images that screen readers cannot read" (the job page, the record), the job page's equation row
  said "shown as an image", the student file's note said "the others are pictures", and a draft
  download said "an image". `checked.Picture` now says whether the part is shown as text
  (`as_text`, set from the file's origin in `checked.converted`), and each sentence reads it:
  `checked_words.not_converted_sentence` (was `as_image_sentence`), `maths_sentence`,
  `card_equations`, the record's section ("Parts not converted") and CSV row ("Shown as LaTeX
  text, not converted"), and `job_page.draft_sentence`. The count that holds the job at "Ready"
  and lists the part for the owner is unchanged: an equation a screen reader gets as raw LaTeX is
  not converted. PDFs read the same as before. This changes the student note (`maths_sentence`) of
  every Word or LaTeX document that has an equation without MathML, so it owes a `RENDER_VERSION`
  raise (not raised on this branch).

## Unreleased: a reviewer leaves one paragraph out of the student file (branch fix/review-small-items, WS19.9 in part)

- The page editor has "Leave it out" on a paragraph or list item, and the page's list shows a
  paragraph left out as "left out of the student file" with "Put it back". It is the route
  `POST /jobs/{id}/edit/{element}` with `action=leave_out` or `put_back`
  (`page_edits.leave_out_block`, `put_back_block`). A left-out paragraph is stored the way a left-out
  page is (`DocumentResult.left_out_blocks`, in the result's JSON: no migration), so the page,
  Word, EPUB, Listen, the LMS packages and the counts drop it at the one place they already read.
  Nothing disappears without a trace: the student file's note says how many paragraphs were left
  out and on which pages, the job is "Ready" and not "Ready to share" while any is out (as for a
  left-out page), and the record names the words taken out, by whom and when. Refused: a heading,
  an equation, a figure, a table, a block an open decision points at, a footnote, a block another
  element links to, a paragraph with an inline equation, and the document's last paragraph.
  Undo, the outcome counts, the stale-form rule and carry-over to a document converted again
  work as for "make heading" and the role. `RENDER_VERSION` is not raised by this item; the
  student note changes only for a document that has a paragraph left out.
- The review screen's "Keyboard shortcuts" list names only the keys the open decision can use:
  a table kept as a picture has no Accept, so A is no longer listed there (a decision with no
  next or previous one drops N or P, one with nothing to edit drops E, and a choice for a whole
  page drops A). The script reads the card itself (`listKeysTheCardCanUse` in `app.js`), so
  every kind of card is covered. No stored data changes.
- A picture that was not converted no longer says so twice to a screen reader. The label beside
  an equation, table, page or picture shown as an image ("Not converted.", a figure's "No
  description.") is `aria-hidden` when the picture's alt text already says it
  (`assemble._alt_says_label`); sighted readers still see it. Without a picture the label is
  read. This changes the written page of every document that has such a picture, so it owes a
  `RENDER_VERSION` raise (not raised on this branch). The two flagged goldens
  (`flags_off_kinematics_slides`, `flags_off_problem_set`) changed by that attribute only.
- LaTeX: a `verse` environment keeps its line ends. Each `\\` in a verse was a plain space
  ("A line of verse and its second line"); it is now the space with a "break" run over it, the
  way a PDF's kept line end is stored, so the page writes `<br>` and Listen, Word and EPUB take it
  from the same run. No new role, and the verse is still plain paragraphs (`ltx_reader.in_verse`).
  A LaTeX source with a verse converts differently, so a `PIPELINE_VERSION` raise is owed (not
  raised here); stored documents keep the space until converted again. No migration.

## Unreleased: a reviewer sets a block's role; LaTeX quotations (branch feat/set-block-role, WS19.9 in part)

- The page editor ("Change page N", under "Change the words, make a line a heading, or set a
  paragraph apart") has buttons on a plain paragraph: "Make it a sidebar", "Make it a
  quotation", and, on a paragraph that has a role, "Not a sidebar" / "Not a quotation". The card
  says in words which role a paragraph has. It is the route `POST /jobs/{id}/edit/{element}` with
  `action=role` (`page_edits.set_role`), recorded as the change `set_role` (undoable, counted
  among the changes to pages, shown on the record). It is offered only where the student page
  writes a role: not on a list item, a heading, program code, a glossary entry or a footnote,
  and the route refuses those. A document converted again carries the role over to the
  paragraph that says the same words, as it does "make heading"; one that cannot be carried is
  listed as a change that was not made again. No keyboard shortcut (the review screen's keys are
  for decisions). Verse, turn, code and lines are not built.
- LaTeX: the paragraphs of a `quote` or `quotation` environment are read with `role="quote"`
  (they were plain paragraphs), so the student page has a `<blockquote>`. `verse` is read as
  before.
- No request to a model, prompt, model id or migration changed. A LaTeX source with a quotation
  converts differently; the batch's `PIPELINE_VERSION` raise covers it.

## Unreleased: block roles, a sidebar and a quotation (branch feat/block-roles, WS16.3)

- One stored field, `Element.role` (`"aside"` or `"quote"`; left out of the JSON when empty, so
  every stored result reads as before). Every writer follows it: the page writes a role's
  paragraphs inside `<aside aria-label="Sidebar">` (with a bar at its left in the text color)
  or `<blockquote>`, adjacent neighbors in one wrapper; the Word file has the paragraph styles
  "Sidebar" and "Block Text"; the EPUB, Canvas and the cartridge keep the elements and the
  label (Canvas loses the bar); Listen says "Side notes" and "Quote" / "End quote". A Word
  source's quotation (a paragraph in the Quote style or a block quote) is read as `quote`. A
  text edit and carry-over keep the role; making a block a heading clears it.
- Not built: no layout detector sets a role on a PDF. A box rule for asides and a set-in rule
  for quotes missed the "2 real cases and no false one" bar (counts in the report of this
  item); a footnote stays a notice, code stays `preformatted`; `breaks`, `lead_end`, `dir` and
  `notes` already exist as the run kind `break`, `term_end`, WS16.6 and WS14.4.
- A document with no role writes the same page as before; no request to a model, prompt, model
  id, pipeline version, render version or migration changed.
## Unreleased: paragraphs and list items that cross a page (branch feat/page-joins)

- A paragraph or list item cut by a page break stayed two elements in cases the old test
  could not see: a figure or table on top of the next page, a page number set low on a tall
  page, a one-line tail in by its first-line indent, a tail whose last line starts a
  paragraph, a list item that goes on at its text, and a page whose margin differs from the
  one before. `_goes_on` compared the document's widest text area and any block's left edge;
  it now compares the tail and the head each in its own column (the left edge against that
  column's text edge on its own page), takes the tail from the body flow (`_flow_tail`) and
  the head as the first block that is not a picture, table or caption (`_flow_head`), and
  never joins across a display equation or a heading. A join the test doubts stays cut.
  A joined list item no longer ends its list (`lists.py`). Pipeline version bump owed:
  element ids and counts after a new join shift.
## Unreleased: four list faults left after the option grids, Word bullets and the block-start rule (branch fix/lists-follow-up)

- A matching exercise ("___ 1." to "___ 3." in a left column, "A." to "C." in a right one under
  "Match each term ...") was one item and paragraphs. A blank before a label no longer hides it
  (`_Marker.start`, `lists.lead_blank`), and two series of labels of different kinds set side by
  side under a question are read column by column as two lists one level below it
  (`blocks._matching_series`). The option grids (questions 1 to 4 of `e1_mc_columns`) did not
  change. Real cases on the corpus: one (the self-made `e1_mc_columns`, which matches its gold
  now); no other file has two such series, so the rule is as narrow as that one shape.
- A Word bullet that touches its text ("▪Grading", "▪not mandatory": MIT 6.0001 lecture 1) stayed
  a paragraph because `lists._bullet_apart` splits a glyph from its text only from 0.1 x font
  size apart. It is an item now when another row of the document starts with the same glyph, like
  a dash; a lone one stays a paragraph. Cases on the corpus: 2 rows in one private file, none in the
  public sets, no other row changed.
- In slides the sub-bullets under "1)" and "2)" sat at the level of their parent because the item
  texts are within 0.6 x body size of one another when the body is 26 pt. A marker of another
  series (bullet or number, another number form or bullet glyph) set more than half the font size
  right of the marker above opens a level (`lists._list_levels`, `MARKER_INDENT`). Items of one
  series, a grid's options and a marker 0.45 right (`ax_csro`: "(l)" and "2)", siblings) are not
  moved. Cases: about 90 items in 4 private files move down a level (74 of them sub-lists under
  "(a)" and "(b)" parts of one problem set), all read as nested; none in the public sets. A
  wrong change (`ax_csro`, 0.45) is what set the threshold at 0.5.
- A phrase in another style that goes on over the end of a list item's line ("each instruction in
  order" in bold) was cut off as a paragraph: the cause is `blocks._merge_style_splits`, which
  joined a style split to a paragraph but not to a list item (`_same_style` is as it was). A list
  item set all in bold is a heading read as an item and is not joined to the text under it.
  Public files: 6 joins in 5 files (4 the last words of an item, 2 the second line of a heading
  read as an item), all read as right; private files: 7 files, 14 joins, all right.
- Effect on stored documents and recordings: paragraphs become list items and list levels move, so
  a stored document made before is out of date (a raise of `PIPELINE_VERSION` is owed; not made
  here). A conversion now asks about different nearby text in two files: 2 more answers owed for
  `w_exam_5111` (38 to 40) and 2 for `w_slides_60001_lec1` (48 to 50); `harness.OWED` and the
  count in docs/eval.md are 1,264 now. No prompt, model setting or render version changed.

## Unreleased: the eval gate sees switches (branch fix/eval-guard-flags)

- `eval gate --require-current` refused a report made with other prompts, other models or
  another pipeline version, but not one made with other switches: a setting or a constant that
  decides what a model is asked or what a step accepts (`REGION_TREE`, `AUTO_ACCEPT_CONFIDENCE`,
  `equation_step.SKIP_ESCALATION_FROM`, `ACCEPT_SINGLE_READING`) could move and every committed
  report still passed as current although it measured other behavior. A report now records the
  values of 137 guarded switches (`flags`, from `eval.gate.guarded_flags`: the behavior
  settings of the production profile that are not named as left out, and a list of module
  constants, each read from its module), and the gate names each one that differs from the
  code, with both values. Without `--require-current` a difference is listed under the table
  like a changed prompt, with the settings of the report that production does not use.
- A report of a run that called no model (headings, tables local) that has no `flags` is
  refused; a report of a model run made before they were recorded is left as it was (its
  pipeline version already refuses it). `latest-headings.json` and `latest-tables-local.json`
  were made again at no cost with the same scores and now hold the flags. The two baselines
  (2026-10-14, pipeline 0.5.0) are not re-made: that would move the numbers they anchor.
- No prompt, model setting, pipeline version, render version, migration or recording changed.

## Unreleased: an unconverted equation's alt text warns before it gives the characters (branch fix/alt-warning-first)

- An equation kept as an image whose text layer layout saw as flattened (a fraction bar, a
  radical sign, an exponent or merged lines) had an alt text that gave the PDF's characters first
  and the caveat after ("Equation (2.2): d = 12gt2,, as written in the PDF, where fractions and
  exponents may run together"), so a screen reader said "d equals twelve g t two" before the
  warning, and a listener who stopped early heard a wrong formula as the equation. The alt text
  now says first that the equation is not converted and that the characters that follow run
  fractions and exponents together ("Equation (2.2), not converted. The characters the PDF has
  for it, with fractions and exponents run together: d = 12gt2"; "Equation not converted. ..." with
  no number), and only then gives them. A draft adds that they were not compared with the page.
  An equation with no such reason keeps its form ("t = 0, as written in the PDF"); the rule
  "the text layer or nothing" is unchanged. The evaluations that strip the tool's words to get
  the PDF's text (`eval/order.py`, `eval/olmocr_bench.py`, `eval/invented/read.py`) strip the
  new lead too. The notice at the top (`NOT_CONVERTED_WHY`) and the review screen's sentences
  for "Keep as image" now say what the alt text does. Trailing punctuation the sentence around
  an equation leaves on the characters ("12gt2,,") is not changed here. One existing test
  changed, no new test file.
- The student file's `generator` tag names the pipeline that converted the result, beside the
  version of the code that made the page ("ReadableSTEM 0.1.0, pipeline 0.5.12"). A page made
  from several results (a combined file) keeps the code's version only. No new visible text.
- A stored page says this only after it is made again: `RENDER_VERSION` is owed a raise for this
  batch (not raised here). No stored field changes, no migration, no model request or prompt
  changed.
## Unreleased: an accent the reading dropped or added is a decision (branch fix/accent-agreement)

- Equations. The letter checks leave accents out, so a reading that dropped or added a hat
  or tilde, or put a tilde for a hat passed every check and was accepted with no card
  (`equation.text_layer_accents`, code `accent_mismatch`, `equation_step.accent_check`). The
  accents of the PDF's text and glyphs under the box are compared with the reading's MathML,
  only for what measured well (`COMPARED_ACCENTS`): a hat or tilde dropped or added, and a
  tilde for a hat or a check for a breve (one kind in the place of another). Not compared:
  primes, script ℓ, bars, vector arrows, dots, acute and grave accents. A
  first version compared all of them and raised 8 flags on two public MIT OCW files (5 of them
  primes; none was a certain real catch and 4 were certain false alarms: a neighboring
  line's primes in the box, or a last prime outside it), so those kinds left. It runs after the second-reading decision and before
  `_fix`: no model request is added or changed, it applies on every acceptance path, the fix
  becomes a required decision, and bulk accept leaves it out. The review card says what differs
  ("The PDF shows a hat here; the proposal has a tilde.") and a reviewer's edit gets the same
  check; a table cell does not. Measured on the recorded readings of the development parts
  (offline, no model call): 15 flags in 7,069 accepted-or-reviewed readings that match the
  current layout, 14 real (the PDF and the page's LaTeX show the accent the reading dropped,
  added or changed), 1 false; none on the public corpus files. Regression tests: a dropped,
  added and changed accent (one test), and bulk accept leaving it out. No migration; a stored
  document keeps its checks until it is converted again.
## Unreleased: a table's box does not take the next numbered question (branch fix/table-box-takes-next-question)

- A box drawn over the next question of a numbered run was read as a table with that question
  as a row ("8. Explain why cells are small.  (4 points)" under a three-row matching grid,
  split as "E xplain"; the ruled answer box under it then grew the table over it, "box
  extended along rules"). `tables._without_captions` now cuts a first or last line off the box
  when it opens with the number that follows (precedes) the item just above (below) the box, at
  that item's left edge, and the table has no numbered rows of its own there. The question is a
  list item again; an unruled grid left with fewer than four lines of cells is text, as before
  the question made it a table. One regression test.
- A table whose box changes is sent to the table step with another picture, so its recorded
  answer no longer matches (rule 10); the count is in the branch report.
## Unreleased: one rule for where a block starts (SW34, feat/starts-a-block)

- One function, `figures._starts_block`, answers whether a line starts a block though it sits
  a line's distance below the one above (`_continues` asks it last). It holds the entry
  (lead-term) and "Keywords:" starts that were spread over `blocks._runs` and `_continues`,
  and the region tree's index entries, and adds three more that the line geometry does not
  show: a note or an affiliation (a raised digit or sign at the line start, in type smaller
  than the body, after a line that does not go on in mid-sentence); a theorem, definition,
  proof or example label (after the end of a sentence, or in bold or italic over a line that
  is not; "Remark 5.1 It ..." with no stop only in bold or italic; "Theorem 3.6 we show" and
  "See Example 5 and" go on); and the next number of a column of numbers after a line that
  stops short of the right edge (the steps of an algorithm, the lines of a code listing; a
  line number of the margin, in smaller type or set well apart from its line, is not a step). On the 333 development PDFs: 22,254 paragraphs become 22,430; the joins that these
  signals name fall from 255 to 88 raised starts (the 88 left are all in body size, line
  numbers and isotopes), from 21 to 9 theorem labels (the 9 left are references inside a
  sentence), and from 316 to 212 number starts; the cuts in mid-sentence on one page go from
  3,107 to 3,104. 30 files change; every change read is a start that was missing or neutral.
- Not built, as no case was measured: a speaker's turn ("TEACHER:") as a signal (the 11
  joins found in the development material were 3 real ones, all already cut by the lead-term
  rule), and the paragraph-end rule. Footnotes that run together are cut by the raised-mark
  signal; WS2.8 is not built. Nothing here depends on the region tree.
- Pipeline output changes for pages with notes, labelled theorems or numbered steps, so a
  PIPELINE_VERSION bump is owed when this merges. No migration.

## Before pipeline 0.5.13

Older history, up to and including pipeline 0.5.12, is in [docs/changelog-archive.md](docs/changelog-archive.md).
