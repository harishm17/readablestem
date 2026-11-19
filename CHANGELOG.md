# Changelog

## Unreleased: decks are found by shape, and a file of several decks reads as several (branch feat/decks-by-shape)

- SW6 (F-254, F-526). `slides.detect` now also takes a deck from the pages alone: every page
  slide-shaped (landscape 4:3, 16:9 or 16:10, not a Letter or A4 sheet) and a title on at least
  70% of them, with or without page labels or a Beamer creator. The share was chosen from
  measured files: real decks had a title on 91-100% of their pages and files that are not decks
  on 0-33%. Overlay steps of such a deck are found by content (`_repeats`: every row and mark of
  the earlier page is on the next), so a PowerPoint-style build loses its earlier pages.
- A new deck starts where a title slide recurs (a page whose largest line is at least 1.25 times
  the ordinary title size, with at most three other rows) or where the page size changes. A
  deck's title is a heading one level under the document title and its slide titles one level
  below that; heading sizes are judged per deck (`heading_levels` groups by the `slide_deck N`
  reason). A title of several lines is read as one heading. `assemble.title_plan` is unchanged.
- Elements of a deck carry the new reasons `slide_deck N` and `slide_deck_title`.
- Not changed: the slide shape test, scanned or image-only decks, .pptx uploads.
- No migration; no model request or `RENDER_VERSION` changed. A `PIPELINE_VERSION` bump is owed
  at the next PR, because a stored document's pages and headings can change on a re-run.

## Unreleased: every form is answered once (branch feat/forms-stage-2)

- SW43, stage 2. The forms that were still plain tags now follow the one submit rule: the
  document, upload and course deletes, Keep for 7 days, the document language, "Continue with
  the pages before the empty ones", the title and contact line, the feedback questions, the
  account name and the keep default, a course symbol's edit, the Try again and Convert again
  buttons of My documents and a batch (`job.retry`), and the sign-in and sign-out forms. A second
  click gets the first answer and runs nothing (a second delete no longer says "Page not
  found", a second Keep or feedback no longer says it was already done), and the page marks the
  button busy ("Deleting…", "Saving…", "Sending…") and blocks a second submit. The sign-in and
  sign-out forms have no signed-in user for a token to belong to, so they carry the busy label
  and the block only (`FormRow.token` False). "Download the draft anyway" stays plain: its answer
  is a file, so the page would stay blocked. `course.delete_ask` is now `course.delete` (the
  question's button and the asking button share it). `FORMS` has 59 rows
  (42 before). Of 26 `<form method="post"` tags in the templates, 2 are left: the macro's own
  and the draft form.
- The out-of-date notices end with the next step. On the review screen the box now reads
  what changed, what the item is now, "What you typed is kept below." when it applies, and last
  "Look at what has changed, then choose again if you still want to." The symbols page says to
  look at the page as it is now and make the change again. "Nothing to continue", "Nothing to
  cancel" and "Nothing to try again" say there is nothing more to do and why it may be so.
- The combine page names the parts whose language is unconfirmed ("Open the parts for pages 1-4,
  pages 5-8, then confirm the language"), at most three and then "and N more".
- No migration; no model request, `PIPELINE_VERSION` or `RENDER_VERSION` changed.

## Unreleased: the icons are drawn from one SVG (branch fix/brand-icons-one-source)

- WS24.9 (F-424), as far as its triage line. The favicon, the 192 and 512 px icons and the touch
  icon were a serif "RS" on rgb(26,79,160), which is not the accent, and the social card was set
  in another typeface; none came from a source file. `static/icon.svg` is now the one source: the
  header's mark in the accent `#1c46ad` (`#8ab4ff` in dark mode). `scripts/brand_icons.py` draws
  `favicon.ico` (16, 32, 48 px, each frame from the SVG), the 192 and 512 px icons, the 180 px
  touch icon (opaque) and `social-card.png` (Source Sans 3 and Latin Modern Math, both vendored),
  with no network. `base.html` links the SVG icon with the `.ico` as the fallback; the 500 and 503
  pages and the showcase link the same. A test rebuilds every file and compares decoded pixels
  (bytes depend on the zlib). The Content-Security-Policy is unchanged: the manifest falls under
  `default-src 'self'`. Not built: a new mark; the showcase has no manifest or touch icon (it is a
  sub-site under `/showcase/`). No migration; no model request changed.
## Unreleased: the type scale checked, and the last text under 14 px (branch fix/student-type-scale-and-voices)

- WS21.5 (F-445) and WS23.2/WS23.3 (F-461, F-462) were already built in this branch's base
  (401727fb, b910b133); this entry is what a check of both found left. Measured in Chromium at
  1280 and 390 px on a page with every heading level: h3 to h6, captions, footnotes and page
  markers have the sizes the type scale gives them, and the one text still under 14 px was the
  stacked scripts of a nuclide or an ion (13.5 px, `0.75em`); they are now `0.8em` (14.4 px).
  The EPUB takes the same rule from the same sheet. F-461 and the Play-off fault were
  reproduced on the files before 3a89e21b (the transport 139 px tall in a 225 px window, Play
  disabled for a Firefox-shaped voice list) and pass on this branch; the Firefox-shaped voice
  test now has 14,805 voices. No Listen change, no model call, no migration. The change
  rides on the `RENDER_VERSION` raise already in this batch (not bumped here).
## Unreleased: links across the parts of a long file, and a LaTeX contents list that says each entry once (branch fix/combined-links-and-contents)

- WS17.2, F-515, F-516, as far as its triage line (no seven-input resolver). A book converted in
  parts could not link a contents entry for a later chapter, or "Chapter 2" or "Figure 3", to a
  target printed in another part, and the combined file left them plain words. `combine.join`
  now runs the passes that made those links in the parts once more over the joined elements
  (`_contents_links`, then `text_pass.source_links`: contents entries, typed addresses,
  references by number); links a part made are kept and move with their targets' ids. Links
  in the file on the three-part generated book of WF23: 7 whole, 3 in the parts, 3 combined
  before and 7 combined after; on a generated book with numbered figures and equations referred
  to across parts: 17 whole, 7 in the parts, 7 combined before and 17 after; no link in the
  combined page goes nowhere. Not built: a link the PDF itself made to a page of another part
  (the part does not keep it), the wrong link a contents entry gets inside a part from a
  namesake heading (F-515's first half: the layout's `_contents_links`), and a paragraph cut at
  a part boundary (SW50).
- A LaTeX contents entry that has sub-entries repeated its children's words ("Alpha Beta Gamma"
  for "Alpha") and carried a link on the first child's words, because an item with no paragraph
  took the text of the list nested in it (`ltx_reader._list`). It now says its own words and the
  sub-entries follow as items one level down. The Word reader's nested lists and an `itemize`
  in an `enumerate` were checked and were already right. On 38 of the 53 tracked LaTeX main
  files that convert (the others stop the same way before and after), 2 documents changed, 5
  rows in all, each a contents entry. No migration; no model request changed;
  `PIPELINE_VERSION` and `RENDER_VERSION` not bumped (the next bump covers it: stored combined
  files and LaTeX documents read differently).
## Unreleased: Greek words are prose and Chinese, Japanese and Thai lines join without a space (branch fix/greek-prose-cjk-joins)

- SW54c, SW54d (F-544, F-545). A Greek sentence in a text font became a row of equations; now a
  run of two or more letters of the Greek script (accented letters included) is a word, and
  prose. A lone Greek letter, Greek in a math font and a lone theta in an English sentence stay
  math; so do two Greek letters alone in a line of another language ("Δθ"), which are a word
  only beside a Greek word of three or more letters; the article "η" or "ο" next to a Greek
  word is a word of the sentence. A line that ended
  and the next one that began in Han, Kana, Thai, Lao or Khmer (or their full-width punctuation)
  were joined with a space in the middle of a word; they now join with none. Text of such
  paragraphs changes. One PIPELINE_VERSION bump is owed at the next PR.
## Unreleased: a slide page's pictures and drawings are read only when a step needs them (branch perf/image-heavy-pages)

- PF16 (F-494), as far as it reproduces. `_read_deck` read every page's slide marks (a digest of
  each picture's pixels and `_drawing_key` of each drawing) before it knew whether the file was a
  deck, and `slides._repeats` uses them only for a page of a multi-page frame whose rows are all
  on the frame's last page. They are now read when `_repeats` asks (`slides.LazyMarks`). Measured
  on generated files, one process, interleaved base and after, best of 3 (box load 7 to 12):
  `_drawing_key` 424 calls to 0 on a 2-page ink file, 400 to 0 on a one-page slide with 400 pen
  strokes, 71 to 0 on `tables.pdf`, 65 to 50 on the 13-page `slides_steps` deck (the
  steps that are compared); picture decodes in `get_image_info(hashes=True)` 2 fewer a file;
  `get_cdrawings` 2 fewer a file. Seconds a page: 6 scans at 300 dpi 0.37 to 0.33, 8 photo slides
  0.143 to 0.130, 2 pen-stroke pages 0.334 to 0.292, a text page 0.568 to 0.515, `tables.pdf`
  0.424 to 0.416; vector handwriting (3 units, 48 to 2,300 paths a page) was 0.09 to 0.40 and is
  unchanged. Not reproduced: `_drawing_key` already ran once per drawing (2,400 calls for 2,400
  paths on 4 ink pages, the audit's 2,670 on 4 pages), and the page is rendered once. A page of
  400 to 600 pen strokes that is not read as handwriting spends 3.6 to 7.7 s of its time in the
  table detector (`tables.propose`, `find_tables`), once a page: not a repeat, not changed here.
  No progress report during layout: `analyze` has no callback, so it needs new plumbing and is
  not built. `layout.analyze` of the 332 files of the corpus in three variants (996 results):
  0 differences, so no PIPELINE_VERSION bump is owed.

## Unreleased: a LaTeX file's own references are links (branch feat/source-internal-links)

- WS18.9 (F-557). LaTeXML already wrote `\ref`, `\eqref`, `\autoref`, `\cite` and each contents
  entry as a link to the id of its target, and the reader threw it away; it is now an internal
  link to the element that id became (a heading, a numbered equation, a theorem, a figure, a
  table, a bibliography entry), and the guessed link by number only fills in where the file had no
  reference. A Word hyperlink to a bookmark on a heading is linked the same way. On 45 LaTeX
  test sources, 36 `#` links became 41 internal links (5 more because a contents entry with
  sub-entries already repeats them, a fault of the list reader that is not changed here), the
  guessed links by number fell from 18 to 5, no document changed apart from links, and none
  pointed at a missing element. One
  PIPELINE_VERSION bump is owed at the next PR.
## Unreleased: a PDF cut off in the middle is no longer called whole (branch fix/cut-off-file)

- WS14.5, F-505, as far as its triage line. A six-page PDF cut at half its bytes opens only after
  MuPDF repairs it, with pages 4 to 6 empty; the page said "Ready to share. Every part the tool
  found was converted." and the student file stopped at "Page 3". A repaired file whose last
  pages are empty now gets a required notice, `file_cut_off`: the state is "Converted: the file
  looks cut off" with "Upload a fresh copy, or continue with pages 1 to 3", and after "Continue"
  (`POST /jobs/{id}/cut-off`) the state is "Ready" with the pages named. The notice stays, and the
  one summary of what was checked (`CheckedSummary.cut_off`) tells the student file, the EPUB and
  Word files, the Canvas README, the record and Listen that those pages are not in the file. A
  repaired file with content on every page gets an optional line (`file_repaired`); a file MuPDF
  did not repair is never touched. The probe at the upload door is unchanged. No migration; no
  model request changed; `PIPELINE_VERSION` and `RENDER_VERSION` not bumped (the next bump covers
  it).
## Unreleased: eight loose ends (branch fix/loose-ends-17)

- A page limit said as a fact where it is an estimate. A Word file read from the file itself, a
  LaTeX file and a project zip count as one page per 3,000 characters or picture. The daily
  allowance refusal now says "notes.docx counts as 14 pages" (a PDF still "has" its pages), and
  the documents list, the batch rows, the converting label and the job page's "Found ... on N
  pages" say the same for those files.
- A refused page edit (the document changed after the page was opened) now says what to do next.
- The account deletion page lists its download choices one under the other (the second sat to
  the right of the first at wide widths).
- The stored demo page is written again by today's assembler from its stored result
  (`readablestem demo rerender`, no model, $0); its note's summary says "license". The recorded
  durations of 1,674 tests that no longer exist are dropped from `tests/shard_durations.json`; a
  refresh with `--shard-record` is owed. `scripts/design_shots.py` finds the upload field again.
- Looked at and left: the combine page's rows (a part whose language is assumed can be combined;
  only sharing waits), the older question pages (same structure as the newer ones), the download
  of a stored original (no kind has one).
## Unreleased: struck text is on by default (branch feat/content-runs-on)

- WS16.1 and SW51a. `CONTENT_RUNS` is on in the code and in the production profile: text the page
  strikes through (a drawn line, a StrikeOut annotation, a font's strike effect; or `\sout` in a
  LaTeX source) is stored as a `deleted` run, written as `<del>` with "deleted:" and "end deleted"
  for a screen reader, `w:strike` in Word and spoken the same way in Listen, and the optional
  notice `struck_text` is raised. The switch stays for a release (the off branch goes after one
  release in production).
- Measured before turning it on: on 70-plus generated PDFs (strikes of every kind against
  underlines, grids, fractions, ruled blanks, boxes and rules) no live text was marked deleted;
  on the 73 distinct real-world development files none was; the corpus replay made the same
  requests and had the same misses (1,314) with the switch off and on.
- Two false positives and one miss fixed: a filled bar in a vector figure that the page clips
  away was read as a strike through the caption beside it (the text pass now renders the marks
  that would strike text and drops the ones that do not show), and an inline equation the page
  strikes through was left live (it is now a run on its marker). Not found: struck table cells and
  struck display equations (their elements carry no runs).
- `deleted_runs` and `deleted_runs_written` are in the trace allowlist, so the counts can be read.
- Not bumped: `PIPELINE_VERSION` and `RENDER_VERSION` (one bump is already owed).
## Unreleased: course grants, off until the list is done (branch feat/course-grants)

- WS27.1, reduced to course grants only. An owner can give a person, by the address they sign in
  with, the right to view or review every document of one course, now and later, from a People
  section on the course page (add, change, remove; at most 10 people; nothing is emailed; Remove
  asks first as the answer to the POST). The rule is `shares.role_of` alone, so every route that
  asks `job_access` follows it: a document added to the course is covered, one taken out or a
  deleted course is not, an expired document is refused before any grant is read, the address must
  be proven as for a share, rank stops at review, and the course pages and every owner-only action
  stay owner-only. The document's share page lists the people reached through its course, and
  "Shared with you" groups those documents under the course. A grantee's re-read is charged to the
  owner as a reviewer's is. Behind `FEATURE_COURSE_GRANTS` (off in code and in the production
  profile; needs `FEATURE_NOTATION_MEMORY`): off, the routes are 404 and a grant row gives no
  right. Deleting a course or erasing an account deletes the grants. One migration,
  `d3a8f1c5e692` (table `course_grants`). Not built: teams, departments, offices, "send to my
  accessibility office", hand-over of a course, usage totals. The public pages that become
  incomplete are listed in docs/deploy-railway.md, "Before `FEATURE_COURSE_GRANTS` is turned on".
  No model request changed; no pipeline or render version bump.
## Unreleased: one body size and one look per heading level (branch fix/type-scale-and-headings)

- WS24.1 and WS24.4, as far as their triage line. Measured in a browser over the app and public pages at 1280 and
  390 px. Text is now 16 px on screens people operate and 18 px on pages people read (before: 16,
  18 and 14 on operate screens, 18, 14 and 16 on read pages, set by the wrapper an element sat in).
  `.hint` is one step below text (14 and 16 px; before 14, 16 and 18). Each heading level has one
  size per kind of screen; the rules that sized a heading by where it sat (review cards, the
  review list's boxes, the job page, footer columns, panes, notices) are gone, so an h4 no longer
  looks like an h3 (F-429) and a review title is no longer as large as its h2 on a phone (F-196).
  `.card-title` is the one class for a title that must be an h2 or h3 but should look smaller.
  The comparison chart's title is a real heading. The student pages are not touched. `DESIGN.md`
  says what is true.

## Unreleased: what is stored of an uploaded file is said for every kind (branch feat/source-file-at-the-door)

- WS11.1e, as far as its triage line. Found by reading the upload, delete and purge code and by
  uploading each kind and deleting it: a PDF is stored as uploaded; a `.tex` file, a LaTeX zip and
  (with `FEATURE_WORD_SOURCE`) a Word file are stored as uploaded, in the job's `upload` row of
  `blobs`; a PowerPoint file, and a Word file the reader does not take, are stored only as the PDF
  made from them. Every copy is one `blobs` row, counted in the person's storage and removed by
  every deletion path (0 rows after each). What was wrong was the words: the privacy notice, the
  upload form, /limits, /pricing and two guides said a Word file is always turned into a PDF and
  never kept, and the delete pages said the PDF is deleted. They now say what is true for the
  kinds this server takes (`word_source` joins `latex_source` in the pages' context). A
  `.tex` file, a zip or a Word file refused by the storage limit at the moment it was stored ended
  in a server error (500); it now gets the same "You are storing ..." answer as a PDF. Not built:
  keeping the original of a PowerPoint or a PDF-route Word file (the reader cannot use it and
  it would hold the author's name and tracked changes longer than the notice says), a table of
  kinds, a new reader. No migration; no model request changed.
## Unreleased: a review action is quick on a large document (branch perf/review-action-one-card)

- PF3 (F-273, F-281), measured first, built as far as the numbers asked. On a generated 100-page
  document with 350 decisions and 3,350 elements, in a real browser, click to paint for Accept
  was 1,010 ms (worst 1,165) before, one-at-a-time view; next card 772 ms; saving an edit
  1,353 ms. Where the time went: the screen read and summed up the whole result twice (once for
  the origin check, once in the page builder) and its counts were never shown on it; the text
  of each paragraph asked for the whole document's equation numbers (64 times a screen); the
  picture and live-check routes loaded the job's summary and crop sizes they do not use; Python's
  garbage collector, at its stock threshold, passed over the 100,000 small objects of the result
  all through each write (115 of 250 ms of an Accept); and `marks.css` was asked for after every
  action though it had not changed. Now: the screen loads once and without the summary
  (`routes/review.py` `_load_versioned`, `_load_result`; `routes/split.py`), equation numbers are
  made only for a text that has an inline equation (`web/page_edits.py`), the web process raises
  the collector's threshold (`web/app.py`), and the stylesheet link carries the page's tag so a
  decision on the same page asks for nothing (`marks.css?page=N&v=<tag>`, kept as immutable; a
  link without the tag still revalidates). The answer to an action is still the screen, not one
  card: with these the actions are inside 0.5 s, and a one-card answer would not remove the
  load of the whole result that both the action and the next card need.

## Unreleased: the buttons that lose work or access ask first (branch fix/destructive-actions-confirm)

- WS24.6 (F-106, F-197, F-210, F-432), as far as its triage line: the secondary buttons that acted
  at once now open a confirmation page that names the thing and says what is lost, with a red
  button and "No, keep it". They are Remove on a course's symbol, Block and Remove next to a
  person on the Admin page, the "Block by email" form, and "Remove the link" for a class link
  (its address cannot be got back). For a symbol and a class link the opener is a link to a GET
  page and the POST is as it was. On the Admin page the question is the answer to the same POST
  without `confirm=yes` (as deleting a course asks), so a person's address is never in a URL,
  where the host's request log would keep it. Every POST is CSRF-checked and answers a repeat
  as the first click.
  Removing a symbol joins the one-submit rule (`course.remove_entry`). The class link's opener
  uses the outlined red look instead of the filled one, as the other openers do. Not built: Undo
  notices, three levels of buttons, sorting symbols by first use. Unlinking an address,
  unblocking and removing a person's access to one document stay as they were: each is put back
  in one step.
## Unreleased: modes: forced colors and dark (branch fix/modes-forced-colors-dark)

- WS24.8 and WS13.11, as far as the triage: the job status box (F-431), a boxed summary row, the
  MathML preview, the alt-text and heading proposals, the review's table of contents, the compare
  box, the optional-checks box, the count chip and the page frame had only a background, so in
  forced colors they vanished; each now has a border in the forced-colors block next to its rule.
  An invalid field (F-432) gets a thick double edge besides its red, and the filled danger button
  a thick double edge, so neither depends on color or fill. The parts list rows (F-433) put the
  action at the right edge on a 320px screen, the label wrapping in its own space. The share
  link and the class link (F-434) are read-only boxes that wrap, not one-line inputs. The review
  legend's keys (F-300) use the page's own `--pmark-*` colors on paper, so they match the marks
  in dark mode. F-301 and F-338 no longer reproduce: the paper rule already keeps those elements
  light with a frame. DESIGN.md has the four rules in its Do's.

## Unreleased: links in Word and LaTeX documents, and a combined file's languages (branch fix/source-links-and-combined-language)

- Documents made from a Word file or LaTeX get what the links work gave a PDF: an address typed
  in the text with no link ("www.example.org", "name@host.edu") and a reference by number
  ("Figure 2", "Table 1", "Equation (3)", "Section 3.2", "Theorem 2.1") become links, through
  the same two functions (`text_pass.source_links`, called in `source.convert`). The links the
  file itself has are kept as they were and never overlapped; nothing is linked inside math;
  the job page's count of links kept still counts only the file's own. LaTeX leaves a heading's
  and a caption's number out of their text, so its reader now hands the number over
  (`Reading.printed`) for the targets; without it only equations and theorems would have been
  found. A table's own caption now names a table in any document (`crossref_targets`). LaTeX's
  `\ref` links are still not kept (the reference stays as text). Not a change to any model
  request. Adds to the reasons for the one `PIPELINE_VERSION` bump owed at the pull request.
- A combined file (`pipeline/combine.py`) now writes each part in its own language: a part in
  another language than the file's has `lang` on its paragraphs, list items and headings, where
  before they were read in the file's language. The file's language is the one most parts have,
  by language subtag; a part whose language was assumed or mixed and not yet confirmed neither
  votes nor is marked. The combine page says once when such a part exists or when no language
  is held by most parts, however many parts there are. The combined file is stored nowhere
  and has no language control; the language is changed on a part, and the next file follows.
## Unreleased: the class link's follow-up fixes (branch fix/class-link-followup)

- A document kept by a class link now runs out on its own clock: while a link keeps it,
  `retain_until` is the link's last day plus the window the document goes back to (24 hours,
  7 days, or for `dev` its 30 days from upload or a day), so it no longer reads as expired for up
  to an hour after the link closes and then comes back. The hourly purge only frees the published
  copy of a document no link keeps (`links.purge_copies`, was `release_closed`);
  `purge_expired` and `aggregate_and_purge` no longer take `settings`. Removing a link still gives
  the earlier window from the removal. The job page's line and the share page say the new rule.
- Reloading the page that shows a new link's address no longer makes a second link and ends the
  first: making a link follows the one submit rule (`link.make`), so the repeat is a redirect to
  the share page. A HEAD on `/s/{token}` is answered and not counted as an open (now tested).
  The published copy was checked to have no address that needs a sign-in (every picture is a
  `data:` address). No migration.

## Unreleased: typed addresses and numbered references are links (branch feat/links-typed-and-references)

- SW52 (F-517, F-518), as far as its triage line: a web address (`https://`, `http://`, `www.`)
  or an email address typed in the text with no link in the PDF is now a link (kind "typed",
  checked by `is_safe_href`, the full stop or comma at the end of a sentence left out). "Section
  3.2", "Chapter 4", "Theorem 2.1" and the same for Lemma, Proposition, Corollary, Definition and
  Example join the references to figures, tables and equations: each links to the one heading
  or paragraph that starts with that number, and to nothing when there are none or two. A
  reference inside its own target is not linked. The model's requests are unchanged; the job
  page's count of links in the PDF leaves typed links out. A text edit keeps a computed link on
  the words that did not change, as it does for the others, and does not compute a new one.
  `PIPELINE_VERSION` is not bumped here.
## Unreleased: the document's language (branch fix/document-language)

- SW20d and SW20a (F-543, F-272, the detection half of F-350): the language of a document is
  decided by one order, with no voting between sources: the language chosen on the upload form
  ("Language of the document", "Detect it" by default; it is stated, never assumed); then the
  text, counted by script (kana is Japanese, Hangul Korean, Hebrew, Greek and Thai their own,
  Han with no kana Chinese; Latin, Cyrillic and Arabic text goes to the detector, restricted
  to the languages written in that script, with the same bar for every language, and one table
  maps "ary" and "arz" to "ar"); then the file's own /Lang, only when the text could not decide
  and its script fits the text (Word writes "en-US" into files in other languages, so /Lang no
  longer outranks the text); otherwise English, assumed. Passages are found block by block with
  the same rules. When no language holds 60 % of the letters the document is "mixed" (notice
  `lang_mixed`) and takes the largest. An assumed or mixed language is a required check: the
  job is "Converted: confirm the language", not "Ready to share", and the open item is counted,
  until the owner confirms or changes the language with the existing control; old results with
  `lang_assumed` show it at their next view, and a shared document is not un-shared. The choice
  is stored on the job and the pending upload (`language`, migration `c5d9e1a3b7f4`); it is
  not part of a step's key, and no model request reads the language, so no recorded answer
  changes. Measured on the 42 corpus files that replay
  (replays only, no model call): 4 documents change language, documents with an assumed
  language fall from 6 to 3, 2 are
  marked mixed, passages found rise from 6 to 8, and no replay answer is missing that was not
  missing before.
## Unreleased: page tools by lines, the marks as text, evidence in words (branch feat/page-tools-lines-and-evidence)

- WS19.4, as far as its triage line: a region to add (a picture or an equation) can be given by
  the lines it sits between, "Starts after line" and "Ends before line", two selects filled from
  the page's lines in reading order, with the top and the bottom of the page as ends. The box is
  worked out from those lines' boxes and the page's text margins, in the four fields' own
  coordinates, so the cut-out step and the add route are unchanged; the lines win over the
  four numbers, and the form says so. Dragging a box and moving it with the arrow keys are not
  built.
- WS19.5, as far as its triage line: under the page image, the page's marks are listed as text
  in reading order, each a link to its item with its number, kind and first words. Numbers of
  neighboring lines no longer cover each other where the margin has room: on the marks test
  page (an equation on every line, numbers shown) 11 overlapping pairs before, 0 after; with
  two equations to a line 20 of 22 pairs are left (the margin holds two numbers).
  `marks.PLACEMENT_VERSION` is 2, so a browser asks again for its marks stylesheets.
- WS19.7, as far as its triage line: an equation's card gives the sentence around it with the
  proposal read aloud in its place and, where two readings disagree, both as speech and as
  LaTeX; a figure's card gives the sentence that refers to it; where the only evidence is the
  page image the card says "This can only be checked against the page image." Handing the
  check to someone else is not built.
## Unreleased: the class link, behind `FEATURE_CLASS_LINK` (branch feat/class-link)

- WS27.2 (F-482, class half; triage: "reduce"): with `FEATURE_CLASS_LINK` on (off by default, and
  not to be turned on before docs/deploy-railway.md, "Before `FEATURE_CLASS_LINK` is turned on"), the
  owner of a ready document can make one link, at `/s/{token}`, that opens the published student
  version without a sign-in until a last day at most 180 days away. It is not a role and never
  passes through `job_access`: a token opens that one address and nothing under `/jobs/...`. The
  page has no app header or footer and sets no cookie; it carries `noindex`, `no-referrer` and
  `no-store`, and the same sandbox as `/jobs/{id}/view` (one serving function). A wrong, paused,
  removed, not-yet-open or closed token gets one identical 404 page; guesses are limited by
  address. The token is made and stored as a sign-in link's is (SHA-256 only) and shown to the
  owner once; later the share page shows that a link exists, its label, dates, open count, first
  open and published version. The link serves a copy of the student HTML kept with the document
  (a `Blob` of kind `published`), so an edit afterwards changes nothing a class sees until the
  owner chooses "Update the published copy"; a link is made, and the copy updated, only while the
  document is ready. Only the owner makes, pauses, resumes, removes or updates; a person the
  document is shared with cannot. While a link is kept the document is kept until its last day
  (no new retention mode; the job page says why); when the last link ends or is removed it goes
  back to its earlier retention with the clock starting then, and its row goes 30 days later.
  New table `job_links` (migration `b7d2f4a8c613`). With the switch off there is no section on
  the share page, `/s/...` answers 404 and no public page changes. Not built: `/s/{token}/listen`,
  Word or EPUB through a link, a link for a named person, and the privacy, terms and limits
  words (docs/deploy-railway.md lists what must change first). `PIPELINE_VERSION` and
  `RENDER_VERSION` are not bumped: no document is made differently.

## Unreleased: the table card names the cells that differ (branch feat/table-card-differing-cells)

- SW24 / WS8.20, as far as their triage lines: when the words check of a table fails
  (`table.cell_text`: `text_mismatch`, `cell_mismatch`, `row_mismatch`) or a math cell's check does
  (`table.math_symbols`, `table.math_signs`, `table.math_style` when it changes the meaning), the card
  lists the cells that differ under the check's sentence ("Row 3 (Ball), column 2 (Distance (m)): rebuilt
  table 7, PDF text 6") and marks the same cells in the table with a dashed frame and a ≠ sign. The
  failed checks were already stored by name (`Fix.checks`, content-free); the cells are worked out when the
  card is drawn, with the check's own comparison, so a spacing or spelling difference it does not count is
  not listed, and nothing new is stored. At most 8 are listed, then a count; no list when the grids cannot
  be compared cell by cell. `table.cell_reading` (a formula in a table cell that differs from the
  table's reading) had no sentence and has one. No model request, stored record or choice changes.
## Unreleased: a table's math is written the way the text's math is (branch fix/table-cell-math-one-writer)

- WS5.8 (F-303): math in a table cell is written by the same rule as an equation in the text:
  the rebuilt `<math display="inline">`, with the reading as its `alttext` when `MATHML_ALTTEXT`
  is on, and nothing hidden from assistive technology. Before, a header cell with math got a
  visually hidden spoken string and its MathML inside `aria-hidden="true"` (so a braille display
  got words, never the math), the Word file printed that hidden string as visible text beside the
  equation, and when no reading could be made the name fell back to the cell's LaTeX. Now such a
  header is named by its reading as the `aria-label` of the `<th>` (the name browsers cannot take
  from MathML, which is why a name exists at all, `db6ea689`), taken from the same reading the
  text, the review screen and Listen use (`speech.reading_for`); where no reading can be made it
  has no name and the existing `html.table_header_text` check says so; the LaTeX is never used.
  The student page, the paste version, EPUB, Canvas and Word all follow, because they are made
  from that page. No stored record, model request, `PIPELINE_VERSION` or golden file changes
  (`RENDER_VERSION` 8 is already unshipped). Not tested with a screen reader: row 21 of
  `docs/device-checks.md`.
## Unreleased: one footer and the current page from the path (branch refactor/site-frame-and-icons)

- WS13.9a, as far as its triage line: the three footers in `base.html` are one partial
  (`_footer.html`) with one list of links; the note under the full footer now labels the link to
  the source code "Source code" (it said "Open source", which is also the label of the link to
  the terms' open-source section). The navigation's current item comes from the path
  (`deps.current_section`), not from a value each route passes, so the pages of a document (its
  page, review, share, Listen, record, draft, delete, report, symbols, feedback) now mark "My
  documents"; they marked nothing. About 26 `active` values in the routes are gone.
- WS24.9, as far as its triage line: `/site.webmanifest` (name, start URL, the 192 and 512 px
  icons, theme and background from `--bg`), `<meta name="theme-color">` for light and dark, and
  `/apple-touch-icon.png` at the root (it answered 404). The 500 and 503 pages link the touch
  icon. No icon file changed. Held: a new mark, a build script for the icons, the showcase's icon
  links (its build test allows no link to another address).

## Unreleased: the stale check compares the item (branch fix/stale-check-per-item)

- SW47 (F-080, F-407, F-481), as far as its triage line: a review write is refused as stale only
  when the thing it acts on changed since its page was drawn, not when anything in the document
  did. A decision or an edit of one item, a heading group, a page group, "Read again", a table's
  cell read, accept many, accept the clean tables and relettering a symbol read the writes since
  the page's version from the `ReviewEdit` rows (each records the number its write made) and are
  refused only when one of them changed an item the write acts on (a table and the equations
  behind its cells count as one; a many-item action counts every item of its kind). The refusal
  says "This item was changed in another tab or window", or names the items for a group or a
  many-item action. Undo, a page left out or put back, a region added, "Not an equation" and a
  write the tool made itself are still refused when anything changed. Measured on the review
  fixture: five decisions on five items sent from one page drawn at version 0 stored 1 before and
  5 after. No form field, column or migration is added, and a page open before this release
  works. Held: presence, per-person undo and a refusal that names the other person.
## Unreleased: page tools, words and the way in (branch feat/page-tools-words-and-route)

- SW46 / WS19.1: a re-run or revision lists, by name and page, every page change of the person
  that it did not make again ("Page 2: words you changed"), under the existing "not made
  again" line on the job page; an equation read again is listed too, unless it was undone. The
  list is `edits_not_applied` in `carried_json` (an optional key: older rows read as empty).
  Deleting and editing words are carried as before.
- WS19.8 (F-478, F-374, F-419): an edit is called an edit. The notice after a text edit names
  the edit and no longer says all decisions are made; the undo button after a page edit reads
  "Undo this change" (after a decision it keeps its wording); the record's count line reads "N
  decisions made by a person. M changes to pages." (the record page and the CSV count the same
  things); the refusal of a removed mark reads "Keep the mark [equation 3] in the text, or
  remove the equation as well." with the real item named.
- WS19.6 (F-474, F-479): "Fix something on a page" on the job page opens the review screen in
  page mode (`?mode=page&page=N`): the page's tools are the first panel under "Page N", with no
  decision of another page above them, and that page's optional checks follow. Two clicks from
  the job page of a document with no decision. Not built: the same link in the instructor's
  preview (the preview is the bare student file with no app frame around it).
- No stored-field change, no migration, no prompt or model request changed, no version bump.
## Unreleased: a revision keeps what carry-over needs of the earlier version (branch fix/revise-snapshot-at-upload)

- WS20.2 (point 4) and WS20.3, merged by the triage: uploading a revised version now stores,
  with the new job, what carry-over reads of the earlier one (`web/carry_snapshot.py`: its
  elements and fixes, a SHA-256 in place of each crop, its structural edits, how the compared
  regions looked, its file name; a few KB for a reviewed document). If the earlier document is
  deleted or purged while the new one waits or converts, its decisions and edits still carry;
  before, they were lost with only a line on the job page. The earlier row is used when it is
  still there (it may hold later decisions), the snapshot only when it is not. The snapshot is a
  `blobs` row of the new job (no migration): removed as soon as carry-over has run, and by every
  deletion of the job. The card on the job page names the earlier file and links to nothing.
  No model call, no change to stored documents, no migration, no version bump.
## Unreleased: one source for the student sheet, and a type scale (branch refactor/student-sheet-one-source)

- WS21.1: the student page's stylesheet is one module, `pipeline/student_sheet.py`, as named
  parts (the core, then a part for each feature a page gets only when it uses it) and the
  builds made from them: the screen sheet, the EPUB cut (`student_sheet.epub`, moved from
  `export_epub`) and what the Canvas inliner may carry (`CANVAS_PROPERTIES` and its rewrites,
  moved from `export_canvas`). The constants of `assemble`, `assemble_tables`, `navigation`,
  `figure_details` and `inline_text` are gone; `colour_css` is `student_sheet.colour`. A pure
  move: 22 pages, their Canvas files and their EPUBs came out byte for byte the same (577
  files). `RENDER_VERSION` stays in `assemble` (many modules import it).
- WS21.5 (F-445): a type scale. h1 to h4 step down from 36 px to 20.7 px (h4 was the size of
  the text), h5 and h6 are the size of the text and told apart by weight and style (they were
  14.9 and 12.1 px). Captions, the notes beside a kept picture, footnotes and page markers
  are 0.9 em (16.2 px; page markers were 12.8 px). No text on the page is under 14 px outside
  sub- and superscripts and maths. Colors are unchanged. RENDER_VERSION stays 8 (not shipped).
  No model call, no stored-data change, no migration, no version bump.
## Unreleased: Listen's transport stays in view and Play is never off (branch fix/listen-transport-and-voices)

- WS23.2 (F-461): at 200% and 400% zoom on a laptop the transport stopped being sticky (a
  `max-height: 32rem` rule made it static), so after ten items it was 1,099 px (1280x450) and
  1,400 px (320x225) above the window. In a short window only a compact bar now sticks (Play or
  Pause, Previous, Next and the place; 42 px of 450, 66 px of 225, under a third), the voice
  line under it scrolls with the page, `--listen-sticky` follows what really sticks, and the
  page's `scroll-padding-top` keeps a focused link out from under it (WCAG 2.4.11). The side
  panel's sticky offset follows the same measure.
- WS23.3: a voice the browser does not report as local was always read as online, so Firefox on
  Linux (all 14,805 speech-dispatcher voices "not local") showed "Only online voices" and switched
  Play off. Voices are now local, online (not local and named as a network voice) or not known;
  Play is never off for the class. The first Play with an online or not known voice asks once
  and the second press answers, kept for the visit; the words for a not known voice say only
  that the browser does not tell the page whether it runs on this device, never that text is
  sent. "Hear this" on the review screen uses the same classes. No stored-data change, no
  migration, no version bump, no prompt or model request touched.
## Unreleased: help pages and device checks (branch feat/help-pages-and-device-checks)

- WS25.4 (F-384): a help section of four pages. `/help` lists them with a line each saying what it
  answers; `/help/review` says how to review (the kinds of item, what each button does in the
  screen's own words, the keyboard shortcuts and their switch, accepting many at once, the page
  tools, what happens to a decision left open, how to tell a document is ready to post);
  `/help/latex` says what a LaTeX file or zip may hold, what each upload and stop message means and
  what to change, from the same limits and refused lists the upload checks use; `/help/posting`
  joins the set with the same row of links at its end; `/limits` is the fourth page of the index,
  linked and not repeated. Every sentence was written from the code. A "Help" link is in the footers (not the sign-in pages'
  one-task footer), and `/help/review` is linked from the review screen and the list view,
  `/help/latex` from the upload form's LaTeX line, from a refused LaTeX upload and from a stopped
  LaTeX job. The pages are public, in the sitemap and `llms.txt` (`seo.PUBLIC_PAGES`,
  `page_dates.json`), in `ops.launch.PUBLIC_TEMPLATES`, and in the public-page, axe, reflow,
  contents-link and authz lists of the tests. No page, link or model call changes what a
  document stores or costs; no migration, no version bump (`PIPELINE_VERSION` and `RENDER_VERSION`
  are not owed: no stored output changes).
- WS25.8: `docs/device-checks.md` lists ten checks that need a real device, account or reader
  (Safari on a Mac and an iPhone, VoiceOver on both, VoiceOver on the review screen, NVDA, JAWS,
  TalkBack, EPUB in Apple Books, Word with its accessibility checker, a Canvas import, Listen in
  Spanish, Windows high contrast and 400% zoom). Each has its steps, what it gates and who can run
  it, and every result is `not done`. It replaces `docs/screen-reader-test.md` (removed; its steps
  are in D3 and D4 and its results rows are the table of criterion rows there). The links in
  `docs/README.md`, `docs/acr/` and `tests/fixtures/web/keyboard.py` point to it. No public claim
  changed.
## Unreleased: the source's own words and front matter (branch feat/source-words-and-front-matter)

- SW18.1, SW18.6, WS18.21 (F-129): one function says what the file is (`checked_words.source_noun`:
  "PDF", "LaTeX source", "LaTeX slide deck", "Word file"). The job page, the review list, the
  record's "Not checked" list, the "could not read part of the ..." lines, the equation hint and
  the first sentence of the student note use it, so a LaTeX job no longer says "PDF" where the
  file is the source. The note also says which file it is in: the EPUB, Word, Canvas and Common
  Cartridge files say "an EPUB / a Word / a Canvas / a Common Cartridge version" and not "a web
  version" (`assemble.with_medium`, on the finished page), and the EPUB drops the skip link as
  the Word and Canvas files already did. A PDF job's note is unchanged. A `RENDER_VERSION` bump
  is owed (the note's wording for stored LaTeX, slide-deck and Word documents).
- SW18.4: a source whose conversion program ran out of its own time (a macro that calls itself, a
  very large file) stops with `source_timeout` and says that trying again will not help, in place
  of the shared `timeout` that offered "Try again" and advice about "splitting the PDF". It is
  an error code, not a stored field. Memory or output limits are not told apart yet
  (`source_too_large` is not built).
- WS18.8 (F-471): "Upload a revised version" and the exam "Change" link are not offered on a source
  job, and `/app?revises=` or an upload with `revises` for one is refused in words. Revising a
  source stays held.
- SW18.3, WS18.10: the title has no `\thanks` in it; each author is a line with where they work
  and their own note; a table cell of words and a formula is read as math; `header_cols` comes
  from LaTeXML's row heads; color set on words is kept as a color run. No new stored field.
  Stored LaTeX documents render as before until they are converted again; a `PIPELINE_VERSION`
  bump is owed because the same source now converts differently. No model call, no migration,
  no prompt changed.

## Unreleased: a hidden tab does not poll (branch test/wf52-startup-and-polling)

- WF52 (F-497): a tab nobody is looking at no longer asks the server for the job's status,
  progress, the documents list or the batch rows every 3 seconds. htmx keeps polling in a
  background tab and its trigger filter needs an expression (`allowEval` is off), so
  `static/app.js` cancels a poll request while `document.hidden` is true; the timer runs on and
  the first tick after the tab is shown asks again, within one interval. Measured with the rest
  of WF52: the one-page replay is claimed 0.01 to 0.03 s after upload (wake on upload, PF14), a
  finished job's page makes 0 requests, and importing `pipeline.run` loads 0 modules of
  `anthropic` or `openai` (now a test). No model call, no stored-data change, no migration, no
  version bump.
## Unreleased: page structure and words (branch feat/page-structure-and-words)

- WS11.5 (F-116): the Contents list comes after the title, inside `<main>`, and is a closed
  `<details>` on a phone: at 390 px the title is on the first screen. From 40rem of width up,
  and on paper, the list is shown open and its summary is hidden, with no script (older
  browsers keep the closed line, which works from the keyboard). A heading that repeats under
  the same parent is one entry; the later places follow it as numbered links. The skip link
  jumps to the `<h1>`, which takes focus (`tabindex="-1"`); the empty `main-content` span is
  gone. The Canvas export follows (its paste version drops the skip link and the id).
- WS11.5 (F-114): a box for a part that is not converted says what it is ("A scanned page.",
  "Shown as an image of the original.") and keeps the link to the instructor. The explanation
  (a screen reader cannot read a picture) is said once, in the notice at the top, through
  `say`, so Word, EPUB and Listen carry it once and each box's reason where it stands.
- WS11.5 (F-118): a figure's description summary reads "Description of Figure 3.2" or
  "Description of the figure"; the data table's caption the same. Neither quotes the alt text.
  F-115 (page markers) and F-117 (link text) wait for printed page labels and the link
  resolver. No model call, no stored-data change, no migration, no version bump:
  `RENDER_VERSION` 8 already covers this unshipped batch.
## Unreleased: one MathML normal form (branch feat/mathml-normal-form)

- WS4.9 (F-235): the MathML that a document carries is written in one normal form, by one
  function, `mathml_shape.normal_form`. Five rules: a function name is one identifier (`sin`,
  `cos`, `max`, `gcd`, spelled as a run of letters or as `s` `⁢` `i` `⁢` `n`, becomes one `mi`;
  `lim` and the other limit operators become one `mo`; an italic capital run such as `Pr` is
  kept as letters); digits are never styled (`𝟏𝟎` and `<mn mathvariant="bold">` become plain
  digits; the double-struck digit symbol stays); a converter attribute is dropped where the
  engines and MathML Core do not use it (`mathvariant` on an operator or on a number);
  the prime is an operator, `<mo>′</mo>`, in the superscript; and a table that is the aligned
  rows of one equation carries `data-aligned-rows="true"`.
- Where it runs: the LaTeX writer (`validate.safe_latex_to_mathml`, last step), the page writer
  (`safe_html.sanitize_mathml`, so MathML stored by earlier versions is also written clean on the
  page and in every export) and the LaTeXML source MathML at ingest
  (`web.mathml.sanitize_mathml(normal=True)`). The bold clean-up that lived in
  `core/mathchars.py` (`bake_bold_variants`) moved into this function and is deleted there.
- Speech changes only for the function names (`c o s` is "cosine", `g c d` is "greatest common
  divisor") and for the bold decimals, which were read as styled digits and are now read as
  digits. MathML is stored in `Fix` records; stored speech and stored MathML are not rewritten,
  the page writer applies the form when the page is built. No model call, no stored-data change,
  no migration, no version bump.
## Unreleased: re-reads cached and counted (branch feat/rereads-cached-and-counted)

- WS10.2 (F-093): a read from the review screen (a marked region, a table cell, "Read again") is
  answered from the document's step records when the model has already answered the same
  question (same crop, prompt version, model, text sent): no call, no reservation, no charge, no
  unit of the day's count. The record is the one the conversion's page passes use
  (`web.step_cache`, the `q:` slots), so it is deleted with the job like the rest; no new table,
  no migration. The request a first read sends is unchanged. The unit is counted when a request is
  really sent, not before the lookup, and given back as before when nothing was read.
- Every account's reads are now counted, invited ones too (no limit): the Account page states
  the count where it states an open-tier allowance. The beside-the-button line a person a
  document was shared with sees now says the owner's allowance is the one used.
- A second "Read again" on one item returns the stored reading, not a new sample.
## Unreleased: crops fit, and accept-many is paged (branch fix/crops-fit-and-accept-many)

- WS8.13 (F-067, F-068): every crop on the review screens goes through one macro
  (`_crop.html`) that shows it at the size its element has in the page, one scale for all
  (1.5 px a PDF point), instead of shrinking it to the column, and keeps it whole: a crop wider
  than the column scrolls in its own box, which the keyboard reaches, with a line and an edge
  shadow while there is more to see. A tall figure was drawn 2.56 times too wide for its height
  in the split view; it is no longer stretched. On the accept-many page the crops of 31
  equations were shown at scales that differed 5.1 times and the confirming button came after
  7,587 px (11,704 px at 430 px wide): the scales are now equal within rounding and the button
  is at the top and the bottom of the list, which is paged 20 at a time with lazy crops. The
  button accepts the page it is on and says how many: nothing unseen is accepted, and the
  form carries the ids of the page. Nothing asked in band 1 to 3 is offered by accept-many or
  "Accept the clean tables" (a reading made again kept its old band and could pass every check).
  No model call, no stored-data change, no migration, no version bump.
## Unreleased: every output says which version it is (branch feat/output-version-id)

- WS20.1 (F-483): a document's output has a version id and a date. The job counts the changes
  that reach it (`jobs.output_version`, from 1, and `jobs.output_changed_at`); one function,
  `web.output.output_changed(job, cause)`, is the only code that marks the stored page out of
  date or makes it again for a reason a reader would see (a review decision, a page edit, the
  language, the title, the contact line, a re-run by the worker), and a share, keeping the
  document longer or a first view that only makes a waiting page do not count. The student file's
  About note ends "Version b9487b-12, November 10, 2026" (the first six characters of the job id,
  the count, and the UTC day), its head says `readablestem-version` and `dcterms.modified`, the
  Word, EPUB and Canvas files carry the note's line, and the job page shows it to the owner and a
  reviewer. The assembler takes a `Stamp` as a parameter; the command line gives none. A finished
  job's page is now made again once on its first view, so it carries its id. The privacy page
  says the count and time are stored. One migration (`a9c3e5b7d1f2`: jobs from before get version
  1 and the time of their last decision or finish). No model call, no version bump.
## Unreleased: the transcript and the speech rules (branch feat/listen-transcript-and-speech)

- WS23.4 (F-120, F-121, F-122, F-348 in part, F-371, F-465): Listen starts at the lesson. The
  skip link is an item of its own (kind `skip-link`) and, with the title, the language and the
  bare landmark that open the page, is marked `Item.before` from the items' kinds, so Play
  starts at the first content item and the words of an item no longer decide it; item ids are
  unchanged. Role words come from one string table (`web/messages.py`: `KIND_LABELS`, moved
  from `views`, and `LISTEN_LABELS`), shared with the review screen: "Main content",
  "Navigation", "Footer", "Collapsed section" in place of "Main landmark", "Content
  information landmark". A `<br>` is a pause and a quotation ends with "End quote". The page's
  title is the document's name (`documents.document_title`), then "Listen". The transcript has
  the reading width with the role as a small line above the text; a window 64rem or wider has
  a second column that draws the selected equation, table or small picture (hidden from
  assistive technology, never focused, not a live region), and every item links to its place on
  the student page.
- A capital letter in math is said "cap K" ("cap delta"), the letters of an upright name
  (`\mathrm{Var}`, `\mathrm{Hz}`) and the element symbols of a formula add no word, and in a
  chemical formula a comma separates a coefficient from its formula and one species from the
  next ("2, H 2 O"; counts stay numbers). The review screen's own sentences still say
  "capital". Speech rules now carry a version (`speech.SPEECH_VERSION`, 2;
  `EquationFix.speech_version`): a stored reading from older rules that no person wrote is made
  again from its MathML where it is read (the page, the EPUB's `describedMath`, the Word
  fallback text, the review screens, Listen, the site's examples and the output audit), with
  no model call, and a reading a person wrote (negative version) is never replaced. No spoken
  string is part of any model request. Pinned readings changed: 33 lines in the four speech
  test files, five in the Listen and review tests. No stored-data migration (the new field is
  omitted when 0). `RENDER_VERSION` and `PIPELINE_VERSION` are not touched here (the
  output-version builder owns them): the render version of this unshipped batch covers the page,
  and a `PIPELINE_VERSION` bump is owed at merge because a new conversion stores different
  readings.

## Unreleased: the worker can run in its own service (branch feat/worker-service)

- WS9.4 (F-274, F-277, F-282): `readablestem worker` runs the conversion worker with no web
  server (`web/worker_service.py`), `WORKER_SLOTS` jobs at once (default 1), and `WORKER_MODE`
  says where conversions run: `inline` (the default, exactly as before: the web process starts
  its worker thread) or `service` (the web process starts none). Two workers on one database
  never run the same job: a job is claimed by one conditional UPDATE after `FOR UPDATE SKIP
  LOCKED` on Postgres, and a job whose worker died is requeued when its lease runs out, as
  before. SIGTERM ends the running conversions' leases and exits within 20 seconds. With
  `service`, worker liveness, the AI service's recent requests and the sandbox probe are rows of
  `ops_heartbeat` (`worker.<slot>`, `provider`, `sandbox`; `web/worker_rows.py`) that `/readyz`,
  `/status`, the job page and `/admin` read, so any web process gives the same answer; inline
  nothing is written and the answers come from the process's own memory as before. The worker
  service is described in docs/deploy-railway.md and not added to `.railway/railway.ts` (an apply
  would start a second service whatever the setting says); rolling back is `WORKER_MODE=inline`.
  No model call, no change to what is sent to a model or stored, no migration (the table was
  there), no version bump.
## Unreleased: the marks stylesheet is made once for a page (branch perf/marks-per-page)

- PF1 (F-273, F-274): the review screen's `marks.css` is served for one page
  (`marks.css?page=N`) and the screen links the page it shows (and an equation card's page when
  that is another). Its `ETag` is a hash of what the page's marks are made of (the boxes of the
  fixes and of the notices that get a mark, inline or not, the page's content edges and size,
  each equation's box and font size, and `marks.PLACEMENT_VERSION`); `Cache-Control` is
  `private, no-cache`; a request with the tag is answered 304 after the same access checks and
  without placing a mark. A decision changes no tag. The check reads the stored result only
  (not the job summary and crops the screen needs). Placement compares only links that can ever
  touch, in the order every pair would have been (the same result wherever the old loop
  settled) and stops after `TRIES_PER_MARK` pairs tried for each mark where it cannot settle.
  On a generated document with b2's numbers (14 pages, 647 marks, 143 decisions) the whole
  stylesheet took 2.5 s and 173 KB on every load; a page takes 40 to 50 ms and 11 KB the first
  time and a 304 about 25 ms after. No change to how marks look, no model call, no
  stored-data change, no migration, no version bump.
## Unreleased: the scan box states its cost (branch feat/scan-option-cost)

- WS7.2 (F-014): the box "Convert scanned or handwritten pages" says what reading a page costs
  and the most one document costs ("up to $0.25 ... at most $7.50", from
  `IMAGE_PAGES_USD_PER_PAGE` and `IMAGE_PAGES_MAX_USD_PER_JOB`), inside its hint, so a screen
  reader reads it with the box. One function, `outcome.image_pages_cost`, words it for the
  form, the refusal of a PDF of only scans, the stopped job's message, `/limits` and the offer
  that WS7.8 made; with the file's count of such pages it gives the estimate for the file. The
  offer now says "up to" where it said "about": the per-page figure is a spend limit, and the
  measured cost is lower. Where the count is "at least", it gives the limits, not an estimate
  that would understate. No model call, no stored-data change, no migration, no version bump.
## Unreleased: the record is written for its reader (branch feat/record-for-its-reader)

- WS8.11 (F-145, F-419): the record page says "you" for the person reading and a name for
  anyone else (the owner's address is printed once, in "Converted by", and only to the owner);
  the review list's "Accepted by you" says "by Sam" when someone else decided, from the same
  function (`shares.names_of`). The CSV has the "by" column in the owner's file only, and its
  rows say "the owner" for the owner. Each item the record names links to its card on the
  review screen for a reader who may open it; an item no longer in the document says so. The
  privacy page says what the record holds. No model call, no stored-data change, no
  migration, no version bump.
## Unreleased: Listen keeps its place (branch feat/listen-keeps-its-place)

- SW49 (F-459, F-460, F-119): Listen remembers where the reader was. Every transcript item has
  a stable id from its element; the player keeps one state object and saves the place, speed,
  voice name, Follow along and Keyboard shortcuts in the browser (ids and settings only, 20
  documents at most, nothing saved when storage is blocked). Play says "Resume from item 47 of
  520"; Stop keeps the place; "Play from the beginning" restarts. A click on an item or an index
  link sets the place, and the index has a Pages group. The lesson's first item is marked by the
  page, not found by its label words. No model call, no stored-data change, no migration, no
  version bump.
## Unreleased: shortcuts that can be found and the review that opens at the top (branch feat/review-keys-and-first-load)

- WS8.12 (F-069, F-070): the review screen's keys (N, P, A, E) are on until the reviewer turns
  them off, with the key list beside the switch and the choice remembered; a person who turned
  them off keeps them off. A key does nothing with a modifier (Shift too) held, during input
  method composition or behind an open dialog. A key with nothing to act on says so in the
  status line the saved notice uses. A never presses "Accept anyway" (or "Accept without
  headers"): focus goes to that button and the line says why; on a page card A saves nothing
  and says the choice needs a Tab and a press. After N or P focus is on the new decision's
  heading. The choices come first after the heading on every card (equation, heading, text,
  figure, table, repeated headings, page card), so Accept is at most three Tab stops away
  (12 to 18 before).
- WS8.16 (F-423): the review opens at the top. The page pane is sized to the space under the
  header, and the script moves the page image only inside its own region, against the part
  that is in the window; the window is never moved on load (1280x720 figure: 40 px before).
  No model call, no stored-data change, no migration, no version bump.

## Unreleased: the accept-many count and the review fields (branch feat/review-identical-and-counts)

- WS8.1 (identical items decided as one write) is not built: the tracked fixtures hold no
  group, the replayed corpus cannot be converted from its recording, and the recorded
  real-world runs repeat a picture's answer key in 0.12% of equation and figure answers.
- WS8.13 (F-068): "Accept many" is offered only where at least two items qualify; the
  one-item "Accept many figures (1)" is gone. Crop sizing (F-067) and accepting by risk band
  are not built.
- WS8.6 (F-077, F-090): the split view's "What differs" shows five rows and folds the rest;
  the LaTeX box opens at six lines and grows with its text; the list view's box has the live
  check. The table card's list view names "use the grid read from the PDF" once, not twice.
  No model call, no stored-data change, no migration, no version bump.

## Unreleased: problem reports and the student's way to write to the instructor (branch feat/problem-reports)

- SW25 (F-111, F-112, F-484 in part): "Report this item" on the review cards and the stopped
  job's report line open one short in-app form (`/jobs/{id}/report`, no script needed, error
  summary with focus, the same CSRF and one-submit rule) instead of a `mailto:` or the
  `FEEDBACK_URL` address. The owner and a person with the Review right write a report; it is
  stored in a new `reports` table (migration `e4b7a1c9d352`: kind, sender, job id, item id,
  the item's kind and page, failure code, pipeline version, up to 1,000 typed characters,
  status, dates; no picture, no item text, no reply address). The form says what is stored,
  who reads it and how long, and not to type student names. Only the operator reads reports,
  at `/admin/reports`, open ones first, with a Close button. Limit: 5 per 10 minutes per
  account (`abuse.RULES["report"]`). Retention: with the sender's account, 30 days after
  closing, 180 days whatever the state (`ops.retention`); the privacy page states both numbers
  from `web/reports.py`, and `ops.launch.privacy_claim_problems` checks them. The mail address
  stays printed beside the stopped job's link. The footer's feedback link and the other
  `mailto:` places are unchanged.
- Student file: when the instructor's contact line is an email address, each note about
  something not converted (equation, picture, table, figure, page) and the footer's problem
  line carry a link that writes to the instructor: a `mailto:` whose subject is the document's
  title and the item by kind and page ("Equation on page 3"), with an empty body and no item
  content. A web address links straight there; with no contact line nothing changes. The link
  text says what it does ("Email your instructor about this equation"), and it survives the
  Word, EPUB, Canvas and Cartridge exports and the checker's draft. `RENDER_VERSION` is not
  bumped: only a document that has a contact line gains the links, when it is next rendered.
  Not built: the student report form, an owner's inbox, page reports, notify mail, the
  feedback questions (F-055).
## Unreleased: course notation, who receives it and what a document was read with (branch feat/course-words-and-applied)

- WS11.14 (F-329): the course pages and the upload form's course hint said "the model
  provider" in three typed copies, and "Only you see it" although a person who reviews a
  shared document sees the course's name and the symbols that apply to an equation. They now
  print one sentence from `web.recipients.notation_words`, which names the provider
  (`model_vendor`), says at most 24 symbols go with each equation, that the notes do not, and
  who else sees the list.
- WS11.14 (F-102, first half): the symbols a conversion was handed are kept with the result
  (`DocumentResult.notation_applied`: key and meaning) and the job page lists them ("Read
  with 6 symbols from Physics 201") for the owner and a reviewer. A job with no record says
  the course's notation was applied and lists nothing. No migration (the result is a JSON
  column; results without the field read as before), no request change, no version bump.
  Not built: "Convert again to apply", the course page's marks, starter sets, the table of
  suggestions, the lede, the double-click handling.
## Unreleased: the documents list reads no results (branch perf/documents-list-summary)

- PF5 (F-279, F-496, F-497): the documents list, the batch page, the course page, the "Delete my
  account" count and the admin queue no longer load the stored result of every finished document
  that has nothing open. `states.load` reads what the state needs of it from `jobs.state_facts`
  (the summary's "ready to share" and what remains, the converter version, and whether the result
  has any gap note or notice to word), written next to `open_decisions` whenever a result is
  stored, and `states.state_of` stays the only function that names a state. With 50 finished
  documents the list was 61 statements and 50 result loads; it is now 10 statements and none,
  the same as for 2. A document with an outcome code, an outage, unread LaTeX or a notice that
  holds it back still has its result read. One migration (`state_facts`, nullable): a row from
  before it is read once and filled the first time a list shows it. No version bump.
## Unreleased: one tier, one table of sources, a risk order (branch feat/decision-sources-and-tier)

- SW4 (F-064, F-092, as triage reduces it): `core.triage.tier_for` is the one place a tier is
  decided and every counter and the review queue call it. `risk_rank` puts a required decision in
  one of five bands (nothing usable made; may be wrong; page order; confidence only; weak
  structure), fixed when it is asked (`Fix.band`). The one-at-a-time review walks the bands in
  order, then page order, and the list view orders each kind's cards the same way. When every
  decision of bands 1 to 3 is decided and lower-risk ones are left, both views say so and that
  the document can be shared now. Not built: the per-card line saying why a card is where it is
  (it waits for the card words), sharing rules reading the line (P6b).
- WS8.22 and what WS8.23 left (as triage reduces them): `core.triage.SOURCES`, the one table of
  what can raise a required decision, today `pipeline`, `text`, `source_read` and `decoration`,
  each a doubt or a rule. A fix keeps the list of sources that raised it (`Fix.sources`);
  a second source on a decision already asked for adds its name and raises nothing; a name outside
  the table is refused. Outcome rows carry the opening source, `events.SOURCE_CLASSES` is filled
  from the table, so `readablestem ops accuracy` prints doubt flags (with false per true) and rule
  decisions on their own line. `review_load` counts by band, source and class. Not built: rows for
  the page audit, the line check, the structure reader, reports and the Word reader's rows (each
  arrives with its source), the three-position switch, the live ratio bookkeeping and the card
  words census. Stored results are unchanged (new fields are left out when empty); a stored
  document shows the same decisions in the same count, and the review order changes as above.

## Unreleased: page pictures and exports are made once (branch perf/render-once)

- PF4 (F-284): the review screen's page and region pictures are kept in a small memory cache in
  the web process (`web.page_images.RenderCache`, by job, upload and the exact render
  arguments), bounded by bytes (`PAGE_IMAGE_CACHE_MB`, default 32, 0 keeps nothing). A repeat
  loads no PDF and starts no MuPDF; the browser headers stay `no-store`; the routes' access
  check still runs first; a job's pictures leave the cache when the job is deleted or expires
  (`ops.retention.delete_jobs`).
- PF7 (F-280, F-135, F-496): a Word, EPUB, Canvas or Cartridge file is built the first time it
  is asked for and kept per job in `blobs` (kind `export`, `web.export_store`), keyed by result
  version, `RENDER_VERSION`, file type and a digest of the title, options and page. Repeat
  downloads, "Download all" and "Download everything" take it from there; a new decision or a
  release that changes rendering builds once more and the older file is deleted. Kept exports
  go with the job and are not counted against a person's stored bytes. No migration, no version
  bump. Not built: the "preparing" state and building off the request, the image size cap.
## Unreleased: the review list in parts (branch perf/review-list-paged)

- PF2: the list view (`/jobs/{id}/review/list`) shows 25 cards at a time instead of every card.
  On a generated document of 300 items a view was 1.30 MB and 40,359 DOM nodes; it is now about
  130 KB and 4,100 nodes at most. The parts are real links (`?part=`, kept filter, "Previous part",
  "Next part", "Skip to the next part of the list") and a line says which items of how many the
  view shows. `?at=<id>` shows the part that holds an item or section; the redirects after a
  decision, the notices, the job page and the review screen use it. Crop pictures get their width
  and height from the PNG so the page does not move when one loads. Bulk actions are unchanged:
  they were already over the whole document. No migration and no version bump.
## Unreleased: the state of "Download all" from the job summary (branch perf/download-state)

- PF10 (F-283): "Download all", the course zip, the batch zip and `/account/export` decide which
  documents are ready, held back or over the zip's cap from one query over the jobs' summary
  columns (`routes/bundle._ready_files`), not by loading each job. "Nothing to download yet" now
  costs 2 statements and about 8 ms at 50 documents (52 and 21 ms before); a document over the
  cap or held back costs no statement; only a document taken is read further. What the page
  offers and says is unchanged. No migration and no version bump.

## Unreleased: a reader's page, no draft for a reader, and notices that know who they are for (branch feat/reader-page-and-audience)

- SW16, F-113, F-123, F-139: a person a document was shared with to view gets a page of their
  own (`job_reader.html`): the title, who shared it, what they can do, when it will be deleted,
  Open, Listen and the download formats, and nothing about posting, Canvas, decisions, checks,
  the record or the owner's course. A reviewer keeps the owner's page without the owner-only
  blocks, and sees the same single total of open decisions; a reader is told only "The owner is
  still checking this document." (state `being_checked` in `web/states.py`). Fix: `/view` and
  `/listen` gave a reader the draft of a document with decisions open; they now ask the one
  export-state function (`output.shown`) like every download, and a reader gets the 403 page.
  Every template partial starts with a comment naming its audiences, and one test fails when a
  partial is reached by an audience it does not name. Not built: "Tell the owner about a
  problem", "Ask for the Review right" (SW25) and the version-id line (WS20.1).
- Notices carry an audience (`assemble.say`, `Words`): a sentence written to the person who
  checks the file ("Check it against the original.", "...; not checked", "Needs a description.",
  "Check it before students use it.") is only in a draft's page, made again from the stored
  result when a draft leaves (`routes.jobs.served_html`) and never stored; the student file and
  every file made from it say what is missing and whom to ask. The image-page note reads "This
  page was read from a picture of the original, so small errors are possible." (alt "Original of
  page N."), the equation alt suffix "as written in the PDF" and the figure label "No
  description." **`RENDER_VERSION` is 8**: stored pages are made again once from their stored
  results; the golden files moved with `UPDATE_GOLDEN=1`. A draft is not served from the stored
  page any more, so a test that puts a page in the row needs a document with nothing open.
- WS10.9: the owner's job page, once the job is done, says "Converting this document has cost
  $x.xx in all." from `Job.cost_usd` (after a re-run, the total of both runs), in the admin
  page's money format. Nothing while it runs; no one it was shared with sees it.
## Unreleased: retention a person can predict (branch feat/retention-predictable)

- WS20.4 (F-051): the job page says, right under the status box, the day and time the document is
  kept until and why ("24 hours after you last work on it", or the 7-day or 30-day form), with
  "Keep for 7 days" beside it, on states that have something to keep; other states keep the line in
  the Delete group. An action that restarts the clock and moves the time by an hour or more (a
  decision, a language, title or contact change, Try again) says the new time in its notice, and
  "Keep for 7 days" says the day it keeps the document until; a download has no notice and stays
  silent. The documents list marks a document with 6 hours or less left (`retention.LAST_HOURS`).
  Every one of these sentences is written in `web/retention.py` from Settings. No migration and no
  version bump.
- WS20.4 (F-050): the expired-document page never prints a negative or zero span, says the date the
  document was deleted while the row still knows it, and after the purge says only when it was
  uploaded.
## Unreleased: upload intake (branch perf/upload-intake)

- PF8: a body whose Content-Length is more than the person may still store is refused (413,
  the words of the storage refusal, "this file" for the name) before any of it is read
  (`routes.jobs._storage_refusal`). The file is held once while it is read, not twice
  (`uploads._body_of`): a file over the limit by a few bytes peaked at 2.06 times its size and now
  at 1.04. An accepted upload still peaks at 3 times its size, in the database driver (SQLite
  measured). The upload probe analyzes the ink of 50,000 path items in all, not 1,000,000
  (`page_type.PROBE_MAX_ITEMS`; the clock, `PROBE_GUARD_S`, stays 20 s and only guards against a
  hang, so the same file gets the same answer on any machine), and does not read the drawings of
  the pages after it (`page_type._probe_ink`): notes of 20 to 100 dense pages took 4.1 to 8.2 s
  and take 1.3 to 4.3 s. The count of scanned and handwritten pages, and so the choice for a file
  of mostly such pages, does not change; the refusal of a document that is only handwriting or
  scans is decided in the job when the probe could not tell, and the job then gives the person's
  pages back for that day (`worker.Worker._finish_failed`, `quota.give_back_pages`): the upload
  refuses the same file without counting a page when it can tell, so the count must not depend
  on whether it could. Not built: the start-up check of the upload gate against the web memory
  limit (no such limit is a setting). No migration and no version bump.
## Unreleased: layout is a package and keeps nothing of a document in a module (branch refactor/layout-package)

- WS14.8: `pipeline/layout.py` is the package `pipeline/layout/` (one module per stage, listed
  in docs/layout.md), and what a document turns out to be (made by TeX, a slide deck, its
  compounds, grouped displays, the ink and detector budgets) is a `_Document` passed to the
  steps instead of five module-level names set with `global`. The output of every document is
  unchanged. No migration and no version bump.

## Unreleased: accuracy in use, truthful admin messages and the pages sign-in comes back to (branch feat/admin-accuracy-and-messages)

- WS11.18: `readablestem ops accuracy` prints false flags per true flag, silent misses found per
  100 pages and decisions per page, by release, from the review outcome events
  (`events.outcome_cells`, with `events.outcome_releases`), with the small-count rule (5 accounts,
  10 documents, no account over half; admin accounts and samples left out). A flag counts in the
  ratio only when its source has a class in `events.SOURCE_CLASSES` (none has yet, so every
  flag prints as "not classified" and no ratio is claimed); rule-required decisions are on their
  own line. `/admin`'s per-person table now says "Review writes" for what it counted and adds
  "Decisions" and "Documents reviewed". Not built: the held-out and reports columns, per-profile
  sub-tables, page_read_again, a block on the page itself.
- WS11.17: every admin POST answers with what happened ("Nothing changed: ..." for an empty or
  unknown address, a repeat, an account already blocked), and none reports success when no row
  changed. The Role column is gone, so nobody is called an instructor by default.
- SW11: the course 404 says "course"; the notation refusal names the check that failed; adding
  documents to a course says why some were left out. Sign-in comes back to every GET page that
  takes the signed-in or admin user, built from the routes. A form posted with nobody signed in
  gets the signed-out page, not only a decision.
- WS27.6: `readablestem ops lookup ADDRESS` shows one account for support and no content.
  No migration and no version bump.
## Unreleased: smaller pages, a zip limit and a storage report (branch perf/delivery-and-zip-limit)

- PF6, PF9, PF17, PF18, PF19: text answers are gzip-compressed when the browser asks
  (`CompressionMiddleware`), except downloads, streamed answers and any page that printed the
  CSRF token (BREACH), which includes the signed-in form pages. The landing page goes from
  19,195 to 5,408 bytes and, with its styles and scripts, from 364,731 to 170,455 bytes
  transferred in one Lighthouse run (performance 99 to 100, first paint 1.7 s to 1.1 s).
  `/`, `/privacy` and `/limits` carry an `ETag` and answer 304 to a repeat visit for a
  signed-out visitor; `/favicon.ico` and `/demo/source.pdf` now answer 304 too (they sent the
  whole file). Stored crops on the review screen, the list view, bulk accept and the
  inventory have `width` and `height` from the file.
- The four zip routes (`/app/download`, the course zip, the batch zip, `/account/export`) share
  one per-person limit, `zip_user`, 15 per 10 minutes; the refusal is a 429 page with
  `Retry-After`.
- PF11, first step: `readablestem ops storage-report` prints bytes per table and per finished
  document (docs/operations.md). No stored data, hash, migration or version changes.
## Unreleased: the job page between upload and finished (branch feat/job-page-waiting-and-rerun)

- Migration (two revisions, both additive, `alembic upgrade head`): `a3c7e1f95b24` adds
  `jobs.cancel_requested_at`; `c5d2a8e6f1b7` adds `jobs.rerun_version`, `jobs.cap_base_usd`,
  `jobs.cap_base_calls` and `pending_uploads.scans_json`. Existing rows get NULL and 0.
- SW8: one poll contract (`web/poll.py`). A fragment (`/jobs/{id}/status`, `/progress`,
  `/app/documents`, a part's `/preview`) opened without `HX-Request` redirects to the page that
  holds it, and a job that finishes while its page is open becomes the finished page without a
  reload (the status answer carries the heading and the body out of band).
- WS11.3a: "Cancel" on a waiting or converting document (`POST /jobs/{id}/cancel`), the states
  `stopping` and `canceled`, and a `<progress>` element with a text equivalent for a step with
  counts. A queued job stops at once and its pages come back when counted today; a running one
  stops its model calls at the next reservation. No time estimate. A canceled job is converted
  again with "Convert it", which uses no try.
- WS11.3e: nothing to build beyond a sweep. There is no late-check state, `checks_out` or "Stop
  the checks" in this tree; every count and gate (job page, list, zip, combine, draft banner,
  record) already reads `core.triage.review_load`.
- WS11.3b (F-043): "Not handled in this file" lists only what the result records as found and
  not converted. Footnotes and blocks of program code are converted: they are counted under
  "Handled in this file" ("2 footnotes: kept where they fall in the text"), and the "Not
  handled" pointer in "Before you share" appears only when something is listed.
- WS10.15: a finished document made at an older `PIPELINE_VERSION` than the code's is offered
  "Convert it again" on its job page, once for each version. It uses no try and no daily pages,
  keeps the decisions, and its spend caps count from what the job had spent when it began, so
  it is not stopped by the first run's cap. No release record (`releases.toml`) is added.
- WS7.8: a file of which more than half the pages are scans or handwriting, with the box not
  checked, waits for a choice (keep them as pictures, or read them, with the price) before
  anything is converted or counted; a large file kept for its pages says its scans on the page
  where it waits. One count (`ImagePageCount`), one wording (`scans_note`).
- No stored document, recording, hash or prompt changes; `PIPELINE_VERSION` and
  `RENDER_VERSION` are not bumped. A bump is not owed by this branch.
## Unreleased: the upload door says the same thing everywhere (branch feat/upload-door-and-documents-list)

- SW12, SW23: the two upload sizes are named once and read from the settings and the zip's own limits
  (`uploads.one_document`, `one_file`, `zip_limits`), so no page or refusal types a limit: "one document
  is up to 100 pages and 50 MB", "one file is up to 500 pages and 100 MB". The zip refusal's stale
  "80 MB unpacked" now says what the intake enforces.
- WS11.1d, SW23 (F-332): one `quota.retry_after()` makes the words and the `Retry-After` header of every refusal
  that says to come back. The daily page allowance, the daily spend pause and "sign-ups full" sent
  `Retry-After: 3600` while saying "midnight UTC"; they now send the seconds to the next midnight UTC.
- WS11.1c (F-010, F-011, F-320): parts. The suggestion is sized to the pages left today and cut at the PDF's
  chapter bookmarks when it has them; the preview is whole sentences ("Part 1 (pages 1 to 60) starts now. Parts 2
  and 3 can start after <time>, when your pages reset."); each part is one label in the list; a single typed part's
  errors no longer start "The pages:"; the button follows the list. `GET /jobs/{id}/combine/parts` always
  answers with a row per part and a Review link to its first open decision, and `/combine` with fewer than two
  ready parts shows it instead of a 409. The job page's own Combine line is not changed here.
- WS11.2: the documents list's zip link counts what the zip holds ("Download 25 of 34 ready documents (zip)"),
  says the limit and the documents left out and lists the others; the Word link likewise; one delete partial
  (`_delete_notes.html`) gives the document, waiting-file, course and account delete pages the same two sentences
  (backups, the model provider's retention); a row's controls say the file's title as well (F-096, F-325, F-328).
- WS25.7: the upload form and `/limits` say to upload the LaTeX source when there is one (only where the source
  upload is on). SW2: the closed-allowance and pages-left lines read `allowance()` and `PAGES_RULE`.
- F-329, F-102: the course pages say only the owner sees the list and that its symbols and meanings go to the model
  provider with each equation; the upload form's course hint says the same; the job page's course line says which
  symbols were used (those the course had at conversion) and that later ones are not applied.
- No migration, no pipeline or render version change, no prompt or request change, no limiter change.
## Unreleased: an equation score on the printed form (branch feat/equation-score-canonical)

- F-580 (plan A1): `exact_printed`, a second equation score (`eval/equations_printed.py`) that
  compares the answer's LaTeX and the gold's after one spelling is chosen for every notation that
  prints alike (style commands, spacing, braces that only group, `\dots` against `\ldots`,
  `\dfrac` against `\frac`, the order of a prime and a subscript). It still calls wrong a
  different character, a sign, relation or operator, an accent on one side only, a prime added or
  missing, a structure difference, and bold against plain. Of the 669 accepted development
  equations `exact_lenient` calls wrong, 639 print the same by this score.
- A pair whose answer is what the file's own characters say while the gold is another region's is
  a pairing miss: counted in its own number (`pairing_miss` on a pair, `pairing_misses` in the
  document metrics) and never as right. 284 of the development pairs.
- `exact_lenient` stays for one release and is printed beside the new score in the pipeline eval
  report (equations, document, by status and route), `eval reproduce`, `eval gate` (information
  lines; no threshold or gated figure changed) and `scripts/realworld_report.py`. A record made
  before the fields reads as it was; none is re-made here.
## Unreleased: ruled tables are no longer taken for charts because of their corners (branch feat/tables-without-rules)

- F-249, measured on the olmOCR-Bench table pages at $0: the table finder refused fully ruled
  tables as a `figure`. Word and others fill a 0.5 pt square where two rules cross; 12 filled
  shapes with no text on them are the bars of a chart, and a table of 8 columns and 11 rows has
  about 100 crossings. A filled square no bigger than a rule's thickness that lies on a drawn
  rule is no longer counted as a bar (`tables._PageView.build`). Table checks passed: 51.8% to
  66.2% (444 to 567 of 857); on the development half 229 to 286 of 432, on the check half (run
  once) 215 to 281 of 425; no check changed from pass to fail. New tables: 6 on the 29-file
  corpus replay (all ruled grids, none false), 8 on the benchmark's non-table pages (all
  tables); `eval pipeline --task tables --mode local` equal to its record. Stored documents with
  such a table change when converted again, so this goes out with the next pipeline version.
  The other misses (unruled tables whose number columns do not line up, striped tables, a
  monospace print-out, ruled key-value boxes) were measured and not built: no rule told them
  from prose (docs/eval-parsing-benchmark.md).
## Unreleased: reservations from measurements, page-level calls fall back once (branch feat/spend-reservations-and-fallback)

- WS10.4 (F-059, F-219, F-345): a call's reservation is now the smaller of its worst case and a
  size measured per step and model (`models/pricing.py`, `RESERVATIONS`, from the corpus replay of
  2026-11-18: 39 uploads, 1,376 calls; no call settled above its size). On `claude-opus-5-5` an
  equation call holds $0.046 (was $0.099 to $0.103), a figure $0.065 (was $0.099 to $0.217), an
  image page $0.142 (was $0.230 to $0.267), a table $0.12 (was $0.178 to $0.212). Steps with no
  measurement (`page_math`, `page_witness`, OpenAI) keep the worst case. The recorded $0.60 run
  that billed $0.27 and left 35 equations failed held eight worst-case reservations ($0.8) at
  once; at the measured sizes it holds $0.37.
- A reservation that does not fit while other calls of the job are in flight waits for one to
  settle (at most 120 s) and is tried once more before the job is stopped. A job or run cap below
  one largest reservation for each step run at once is refused when it is set (production start,
  `launch-check`, `convert --max-usd`, `eval --max-usd`).
- A call charged at its reservation because no usage came back is stored with
  `job_calls.billed_unseen_usd`; `/admin` and `ops step-report` total those apart from settled
  spend. `jobs.held_usd` is new: `Job.settled_usd` (spent) and `Job.held_usd` (held by calls in
  flight) are available for a job's page; `jobs.cost_usd` stays spent plus held. Migration
  `9d3e5a7c1f24` (two columns, default 0).
- WS10.10 (F-221): `page_math`, `page_witness` and `image_page` ask once more, of the other model,
  after a refusal or a service error (`models/router.py`, `PAGE_RETRYABLE`); a second failure ends
  the step with its named reason. An answer that is not valid is not retried (`page_witness`
  splits the page instead). No existing request changes.
- WS10.11: `/limits` says that pages dense with equations use more of the new-account budget;
  `docs/deploy.md` gives the pool's reach at the measured median ($4 is about 725 pages at
  $0.0055 a page). The live pool value was not read.

## Unreleased: an equation that was compared with nothing says so (branch fix/equation-not-compared)

- SW57 (F-574): a reading the file's characters could not be held against (no characters in
  the region, operators and marks only, or characters the font does not map) now carries the
  passed check `equation.not_compared` with the cause as its code; so does a bold letter
  written from the file with no reading (`bold_letter`). The record says it; no card is made
  and nothing is asked again, because the count was small (53 of 8,397 accepted equations)
  and the file's characters would have fixed none of the wrong ones.
- A hat, tilde or bar that TeX sets before the first letter of an inline equation was left in
  the prose, and the equation was read without it; a bold letter under such an accent was
  written from the file as the bare letter. A run now starts at the accent. Stored documents
  change for those equations, so this goes out with the next pipeline version.
## Unreleased: platform guards, second batch (branch fix/platform-guards-2)

- Code-scanning alerts 9 to 13 (`py/path-injection`, `routes/discovery.py`): not a way out of the
  showcase folder (the `is_relative_to` check already refused `..`, an absolute path and a symlink
  out), but a request with a null byte in the address reached `Path.resolve()` and answered 500.
  The showcase now answers from a listing of the build's own files (`_showcase_files`): the
  request value only picks a key, and no path is built from it. Hidden files, `404.html` and
  anything resolving outside the build are left out of the listing.
- Sibling: `/static/<name>.<12 hex>.<ext>` with a null byte in the name reached
  `assets.content_hash` and answered 500; it is now a 404. The other places that build a path
  from a request value are bounded (`archive_intake` zip member names are cleaned and checked
  under the folder, `office_convert` names its file from a fixed pair of kinds, a source's
  `main_file` must name a file the archive holds).
- WS12.5 (F-215, F-342, F-343), parts (a), (b) and (f). (a) was already built (WS26.2): `/admin`
  shows the late backup, cron and the full storage from `ops_freshness`, the function
  `/healthz/ops` uses; it now also shows the latest result of each daily canary ("Daily checks",
  and `canaries` in `/admin/health`). (b) `readablestem ops cron` runs both canaries once per UTC
  day through `canary.run_daily`, which the HTTP route calls too, and notes their status in the
  cron heartbeat. Both stay off in production while `CLAUDE_SECOND_CHECK` is `off` and the
  provider is not OpenAI, so no spend starts. (f) `/readyz` gains `database_write`: a write that
  changes nothing, with a 1 second limit, and not ready when it fails (with a second connection
  holding the write lock, `/readyz` answered ready while every POST waited 5 to 7 seconds). The
  public `/healthz/ops` is unchanged: the status word only.

- WS26.3 (ZS-3, F-159, F-366): the per-person upload rule (`upload_user`) counts accepted files
  only. A refused upload, or a part that is not started, gives its count back
  (`RateLimiter.give_back`, from `create_job` and `convert_parts`). The per-address `upload` rule
  stays on requests. Before, 3 refused files followed by good files from one account stopped at
  the 13th good file (12 taken); now all 15 are taken. No request, prompt or stored document
  changes, so no version bump.
- WS26.6, the log part: the content-free request log line carries `forwarded_parts`, the number of
  `X-Forwarded-For` entries (a count, never an address), so one public GET with a two-entry header
  can show how Railway treats the header (3 appends, 1 replaces, 2 passes it through;
  `docs/deploy-railway.md`). The GET and the Landlock checks in the production image and in
  production are not part of this change. The probe's bound for thread tasks is not built: no
  caller needs it yet (the Word reader on `feat/word-reader` bounds pandoc by the group's resident
  memory, not by the probe's total).

## Unreleased: exam mode is in view at upload and said wherever the document is handled (branch feat/exam-mode-visible-and-used)

- WS11.3c (F-047, F-015 exam half), points 1 and 2: the "Exam or quiz" box is under the file field,
  not inside the closed "How to convert it" group. The stored choice is said on the job page (with
  "Change the exam or quiz choice" for the owner, the revised-upload form with the box ready), on
  the documents list and batch rows, in the review header, in the review record and its CSV, and
  on the job page a reviewer opens from a share; the student's page and files say nothing. One
  set of words, `web/exam.py`. No request, prompt or stored document changes, so no version bump.
- WS6.8, the part that changes no request: the conversion options reach every step already
  (`pipeline/options.py`); only the figure step acts on exam mode. The page-image reader's figure
  descriptions would need a line in `image_page.md` (proposed in the report, not made here).
## Unreleased: the eval gate measures the code as it is (branch feat/gate-on-current-pipeline)

- F-579: CI's `eval-gate` job passed on five reports made at pipeline 0.5.0 and 0.5.1 while
  the code was 0.5.11. `eval gate --require-current` now also refuses a report whose
  `pipeline_version` is not the code's. The job gates the two reports that cost nothing to make
  (headings, tables-local), both re-made at 0.5.11 by command. Equations, figures and
  document cost money to re-make (their recordings hold none of the replay: 0 of 60, 26 and
  114 requests answered), so they are historical records in the PR gate and stay gated by the
  weekly paid run (`eval-live.yml`). The job also replays the corpus's public files from the
  recorded answers when a change touches the package (`eval burden --same`): every upload must
  ask for the decisions `data/eval/burden-corpus.json` has, fewer or more, so the record is
  re-made in the same pull request as a change that moves them. The record is re-made at
  0.5.11 (45 of the 73 uploads: the others ask for answers the recording lacks, and a paid
  recording is owed). The eval guard also fires when a change touches a rule that decides acceptance
  (module constants and accept functions of the pipeline steps), found by what a module does
  and compared by parsed value; `eval-unchanged` still opts out. The README's version is
  checked against `PIPELINE_VERSION` (`tests/test_eval_docs.py`).
  No version bump, no migration.
## Unreleased: image pages are all read and always counted (branch feat/image-pages-read-and-offer)

- SW19 (WS7.6, F-268): the image page pass reads every page it was asked to read. One predicate,
  `page_type.image_kind` (scanned, handwritten, or unread pages whose content is a picture or
  ink), picks them for the upload count and the pass (`image_page.kept_reads`); a page of
  drawings whose ink the time guard did not analyze used to be kept as a picture with no model
  call. The title typed over a read page is kept (`image_page.build(typed=...)`) when the read
  leaves it out. The request for a page read is unchanged byte for byte, so the typed title is not
  passed to the model as context: that half needs a request field and prompt wording and is left
  as a proposal.
- WS7.8 (F-360): image pages are always counted, by one function (`page_type.count_image_pages`,
  with `probe_pages` for the upload) that returns scanned, handwritten and unread pages and says
  "at least" when a page could not be analyzed. A file with such pages and the box not checked
  now says how many pages are scans or handwriting, that they stay pictures, and what reading
  them would cost (`Settings.image_pages_estimate`, the figure the job's cap uses): on the job
  page right after the upload, on the batch page beside the document, and in the finished job's
  note, with "Upload it again with the box checked" (`/app?revises=<id>&image_pages=on`).
- Changes: no stored document, recording, registered hash or prompt changes; a job that asked
  for image pages reads the pages of ink the guard stopped (one more model call each, within the
  job's image page cap) and keeps the typed title of a read page. `PIPELINE_VERSION` is not
  bumped here; it is owed at merge only if a finished document that asked for image pages should
  be offered a re-run for these pages.
## Unreleased: sign-in, account, sharing, allowance, audience and date words (branch feat/account-session-sharing)

- WS11.12: one sign-in sentence for every page (`web/signin_words.py`, only the methods that are
  on); sign-in is limited per method (`login_google`, `login_microsoft`, `login_email`,
  `login_dev`) and the limit is counted after the form check, so a refused form costs nothing;
  a page with no form sets no cookie (lazy CSRF token); a provider failure at the callback is a
  400 "Sign-in did not finish"; signing out says it signs out every device; a name the person
  typed is not replaced by the provider's at the next sign-in. The emailed sign-in link is
  issued and sent after the response, so an address that has an account and one that has not
  answer in the same time (F-508 fixed), and the wait notice shows only in the browser that
  asked.
- WS11.13: the account page counts the documents storage counts (no sample, none past its time),
  keeps a name (`POST /account/name`), offers "Download everything" (`GET /account/export`: each
  ready document in each file type, the review records, courses and settings), has one "Delete
  account" button, and a saved notice takes focus; long addresses wrap at set points.
- WS11.15: sharing says what it did (added, changed, already so), shows how many of the ten
  places are used before the cap is hit, tells the person shared with when access ends, and the
  deleted-document page no longer says documents are kept longer "if you share results".
- SW2: `quota.allowance_phrase` ("0 of 150 pages used today, 150 left. Resets ...") and the rule
  sentence `PAGES_RULE`, on the account page, the admin sign-up lines, the parts chooser and the
  re-read refusal.
- SW16: `views.audience_of(role)`; Listen is worded for a reader; the owner's two questions about
  posting the file and the feedback page are the owner's alone (they were open to View).
- SW9: `web/dates.py` (`format_when`, `machine_date`): server-built dates say UTC; no stored zone.
- No migration, no `PIPELINE_VERSION` or `RENDER_VERSION` change, no change to a stored document.
## Unreleased: public pages read their claims from the run record (branch feat/public-pages-and-claims)

- SW26 / WS13.9b: every number on a public page now comes from the registered fresh5 report,
  `data/realworld/results-fresh5.json` or the code (`web/proof_facts.py`, committed as
  `proof_facts.json`; regenerate with `uv run python -m readablestem.web.proof_facts`). The
  silent-error rate, flag recall and unseen loss came from a scorer that counted notation
  differences as errors (AQ1: 55 of 86 errors were harmless). They are gone from the landing,
  limits, comparison, offices and privacy pages, the offices summary, the README and
  `docs/vendor-information.md`, and the pages say they are being scored again, with no figure.
  A result file is "the settings the live service uses" only when its `profile_sha256` equals the
  production profile's; fresh5 has no stamp and is labelled "did not record its settings".
- `tests/test_proof_facts.py` fails when a retired figure returns to a public page, the README
  summary or `docs/vendor-information.md`.
- WS12.7: the sitemap, `llms.txt` and the structured-data dates come from one list
  (`seo.PUBLIC_PAGES`) with a date per page from `git log` (`web/page_dates.json`,
  `scripts/page_dates.py`, checked by `tests/test_web_seo.py`). `/status` is listed. `/showcase`
  answers 404 where the folder is absent, not a redirect to a 404.
- WS25.1: `/pricing` (free beta, the limits, no prices). WS25.3: `/security` (what is stored, who
  receives it, retention, access, file handling, what was and was not tested, no agreement
  offered, how to report). The by-company table moved from the offices page to `/security`.
- Plainer public copy: the landing page's two "screen reader says" labels say where each reading
  comes from; the terms summary keeps what the full text does not repeat; the privacy notice
  names what restarts the delete clock and the steps that write a temporary copy.
- No stored documents, recordings, hashes or costs change. No `PIPELINE_VERSION` or
  `RENDER_VERSION` bump and no migration.
## Unreleased: ReadableSTEM on olmOCR-Bench at $0 (branch feat/public-parsing-benchmark)

- A27 (F-403): `readablestem eval parsing-bench` fetches olmOCR-Bench (pinned revision, ODC-BY)
  outside the repository, converts its one-page PDFs through the product's own path with a
  client that refuses every model request, turns the student page into markdown
  (`eval/olmocr_bench.py`) and scores five categories with the benchmark's own checker
  (`proof/parsers/runners/olmocr_check.py`, in a separate environment). Scans, equations and
  pictured text are dropped or reported as not run, with counts. First run, pipeline 0.5.11:
  order 68.8, headers and footers 32.5, long tiny text 37.3 (n 83), tables 51.8, baseline 99.0.
  The record is `data/eval/parsing-bench-olmocr.json`; the failures by cause are in
  `docs/eval-parsing-benchmark.md`.
- Eval only: no change to what a document is converted to, so no `PIPELINE_VERSION` bump.
## Unreleased: the say-once measure, in shadow (branch feat/say-once-and-page-words)

- A17 and SW60: `pipeline/say_once.py` counts, for each page, the words said by no element and
  the words said twice (by code: `no_owner`, `two_places`, `place_and_text`, `said_twice`),
  against what layout read of the page (`Layout.sources`, recorded where layout prepares its
  rows, so the margin filter, hidden text, a contents line's leaders and OpenStax level tags are
  its own rules counted as left out, with no second copy) and what the elements say. No model, no
  second read of the file, about 0.002 s a page.
- At the end of a conversion a page of 40 words or more with 15% or more of them in no element
  gets one content-free `page_missing` fact in the step facts (`Shadow.over_line`, `ShadowFact.share`),
  as every other page gets a "same". It is shown to nobody and counted in `job_shadow`. No
  notice, no card, no change to a counter a person sees. WS15.9's page-check writer is not built.
- `readablestem eval say-once` gives the corpus figures again; the figures it was set on are in
  docs/eval-say-once.md: no page of the 551 corpus and public pages is over the line, and 1 of
  3,380 wide-list pages is (a page of subscripted maths names, not a lost block).
- Changes no element, no stored document, no recording and no hash, so `PIPELINE_VERSION` is
  not bumped and no migration is added.
## Unreleased: a Word file is read from the file itself, behind `FEATURE_WORD_SOURCE` (branch feat/word-reader)

- WS18.14 and WS18.15: with `FEATURE_WORD_SOURCE` on (off by default), a `.docx` is not turned into
  a LibreOffice PDF. One gate (`source/docx_package.check`: the Office preflight plus member
  names, ratios, part sizes, XML depth, Strict Open XML left to the PDF route) runs in a child,
  then one sandboxed pandoc step (`source/pandoc.py`) reads a working copy of the file (no
  macros, embeddings, external targets or headers; pictures as placeholders), and
  `source/docx_reader.py` fills the same content model the PDF path does: headings, lists with
  levels, tables with spans and exact header rows, pictures with the author's alt text,
  footnotes, links, and every typed equation (`m:oMath`) through `check_latex` and
  `safe_latex_to_mathml`, with no model call. Pandoc's six silences (hidden text, run language,
  headers and footers, default table style as header, SmartArt and charts, title in metadata)
  are handled or counted, and every word of the file is in the tree or in the required notice
  `source_unread`. It plugs into the existing source path (envelope kind `docx`, origin `docx`).
- Turning the switch on changes what a `.docx` upload becomes (no PDF, no page images, typed
  equations without a model call, new notices); with it off nothing changes. The LibreOffice route
  stays for revisions, long files, refusals and servers without pandoc or a sandbox.
- No `PIPELINE_VERSION` bump: no PDF or LaTeX result changes, and a Word document made this way
  is a new document. No migration, no new dependency (pandoc was already in the image; the
  Dockerfile pins 3.1.11.1, the box has 3.7.0.2, and the reader gives the same reading under both
  on 75 of the 79 Word files compared; in the other four, 3.7 drops a few cells of malformed
  table grids that 3.1 keeps, and a later element number differs by one).
- Hostile-input cases for the Word gate in `tests/workflows` (inflating part, member leaving the
  package, macro part, linked picture, external entity, deep nesting, embedded object).
## Unreleased: Read again and Not an equation on a card with no proposal (branch feat/read-again-and-card-choices)

- WS8.5 stage 1 (F-061): a waiting equation's card has "Read again": the item's own picture is
  read once more (`reread.read_equation` on the existing decision route, `action=reread`) and the
  result replaces the proposal on the same item. It is one structural write, so Undo puts the
  whole fix back. A read that comes back empty escalates once by itself to the main model when
  the first read was not by it (`equation_step.transcribe(first_model=...)`), and a read that
  still reads nothing says so and costs nothing: the day's read count is given back
  (`quota.give_back_reread`), also for a refused read and for the same case in a region marked on
  the page and a table cell (siblings; the limit sentence in `reread.py` and the docs said "not
  given back"). The adjustable region and the line strip are not in this change.
- WS4.5 (F-061): an abstained equation's card offers three choices on the same route: Read
  again, Keep as a picture with my description (the existing description box, open, checked by
  the alt-text advice) and Not an equation (`action=not_equation`: the glyphs go back to the
  paragraph; recorded as `removed`; carried onto a revised file for the same picture and words).
  The LaTeX box is closed until typing is the way forward and Save edit waits until something is
  typed (script only; without it the button works as before).
- No change to the stored result shape, a model request or a prompt. `PIPELINE_VERSION` and
  `RENDER_VERSION` are not bumped: nothing here changes what a conversion or a render writes, and
  no migration was added.
## Unreleased: one title for a document (branch feat/exports-links-epub-title)

- SW14 (F-044, F-052, F-056, F-124, F-125, F-321, F-322, F-368): three title chains and callers
  that picked one or none gave a document one name in the list, another on the page, another in
  the Word and EPUB files and a fourth in Canvas, where the instructor's title was dropped. One
  function, `assemble.document_title`, now gives the title and its source (yours, the PDF's,
  the first heading, the file name, the stock title last), and every place reads it: the page's
  `<title>` (made with the file name), Word, EPUB, Canvas, Cartridge, the download and zip
  names, feedback, the record, Listen, the share page, the delete page, My documents and the
  batch table (which lead with the title and show the file name under it). A "title" that is a
  file name written out ("MIT6_0001F16_Welcome") is skipped. The title field says where the
  suggestion comes from. A page whose title is the file name is made again from its stored
  result on first use. The stored title column is recomputed whenever a result is stored; rows
  from before keep the PDF's own title until then. Stored pages made before this say "Converted
  document" until they are made again, which a `RENDER_VERSION` bump does at the next
  request: a bump is owed for this, with WS21.3.

## Unreleased: kept pictures hold their size when the window is narrow (branch feat/exports-links-epub-title)

- WS21.3 (F-441, F-442, F-443), reduced: `img { max-width: 100% }` shrank a kept picture with
  the column, so at 200% zoom or more the writing in a crop got smaller than the text beside
  it. In a window under 40 em a kept picture now keeps its printed size and its box scrolls
  sideways. An inline kept equation is an inline block with its own line height, so the line
  grows to hold it. The student sheet changed, so a document converted earlier shows the old
  sheet until it is converted again or `RENDER_VERSION` is bumped at the merge: a bump is owed.

## Unreleased: the EPUB has its own sheet, landmarks, chapters and true metadata (branch feat/exports-links-epub-title)

- WS22.4 (F-456, F-457, F-516): the book carried the page's stylesheet whole, so its dark and
  light colors, font, base size and column fought the reading system's own theme and margins.
  `export_epub.epub_sheet` leaves page colors, font, size and column to the reader and keeps the
  components' shape; a book with pictures frames them in the text's own color. `nav.xhtml` gains
  `landmarks` (the contents and the start of the text). `accessModeSufficient` is always written
  (`textual` when the page says the text is enough and nothing is flagged, else
  `textual,visual`; it was missing otherwise) and the hazard is `none` unless a GIF or WebP
  picture may move. A document past 100,000 characters is cut at top-level headings into chapter
  files, with the links, contents, page list, manifest and spine naming each file. EPUBs are made
  on request, so a document converted earlier gets this at its next download; no `RENDER_VERSION`
  bump is owed.

## Unreleased: Word links land (branch feat/exports-links-epub-title)

- WS22.2 (F-514, F-566): pandoc keeps an id as a Word bookmark on a heading, `div`, `span`,
  `section`, `table`, `pre`, list item and picture only, so a link to a paragraph, figure, list or
  cell led nowhere in Word. `export_docx._land_links` moves such an id to an empty span at the
  start of the element; a link still without a bookmark in the built file is written as its
  words and counted (`DocxExport.links_unlinked`). A footnote is a real Word footnote: the
  mark was an empty note beside a body paragraph with a dead "Back to the text" link; the
  filter moves the note's words to the mark and writes neither the block nor the link. Word
  files are made on request, so a document converted earlier gets this at its next download; no
  `RENDER_VERSION` bump is owed.
  F-581: before pandoc 3.2 a figure's id is no Word bookmark, so under production's 3.1.11.1 a
  link to a figure with a picture was written as its words. The filter now wraps the figure in a div
  that has the id, which every version writes as a bookmark.
  The output audit no longer counts a note's "Back to the text" link as words of the note, which
  a Word footnote leaves to Word (it had reported 11 notes lost in Word files).

## Unreleased: one equation cannot fail the Word file (branch feat/exports-links-epub-title)

- WS22.3 (F-502): one equation pandoc cannot write as an Office equation (a bold `"` in a
  MathML token leaves raw TeX in the text; an empty `<math>` leaves nothing) made the whole
  download fail with "cannot be made as a Word file". The page now wraps each `<math>` in a
  span, and the pandoc filter (`pipeline/data/export_docx.lua`) tries every equation once with
  the writer that makes the file; one it cannot write is the equation's reading and "This
  equation could not be written as a Word equation; it is complete on the web page" in the
  "Not Converted" style, never raw TeX. `DocxExport.not_word` names the element ids; the CLI
  prints them and the route logs the count. The check that every equation is in the file, or
  said, stays. Word files are made on request, so a document converted earlier gets this at its
  next download; no `RENDER_VERSION` bump is owed. The job page's note and a link to the item
  wait for an export state the app does not store yet.

## Unreleased: a rule beside a maths sign, a column or an axis is no blank (branch fix/legend-sample-not-blank)

- SW36 follow-up: a drawn rule was written as a blank to fill in ("_____") when the nearest
  glyph on its baseline was not text of its row. Three cases, one change in
  `layout._mark_blanks`: a row of only the pieces of a big math sign (a radical, a brace) is
  skipped (`_sign_pieces`); a rule is no blank when the gutter, an upright line or the side of
  a frame stands between it and each word beside it (`_parted`: the other column's line, a
  tick label across a plot's axis from a legend's sample). Over 277 development files the
  drawn blanks go from 203 to 121: 82 false blanks leave 17 papers, and the 117 blanks of the
  12 forms and worksheets are unchanged. Four false blanks remain (fraction bars and overlines
  whose other parts sit in other rows).
- Changes the text of a PDF that had such a blank, so `PIPELINE_VERSION` needs a bump at merge.
## Unreleased: negated relations convert, empty arguments fail, and two prints are not said alike (branch fix/notation-and-speech)

- Negated relations: `\not` before `=`, `<`, `>` or a relation or arrow command, and the `\n...`
  commands (`\nleq`, `\nless`, `\ngeq`, `\nsubseteq`, `\ncong`, `\nsim`, `\nparallel`, `\nrightarrow`, ...)
  pass `validate.check_latex` (`\not` before anything else is `bad_argument`). The tree shape
  (`mathml_shape.shape_general`) joins the converter's zero-width slash and relation into one sign: the
  single Unicode character where there is one (`≠ ∉ ≰ ⊄`), the relation and U+0338 otherwise. Each is
  said by what it says ("is not an element of", "is neither less than nor equal to"), as MathCAT and
  Speech Rule Engine do. `\not=` was read "a ⧸ equals b"; it is now "a not equal to b".
- An empty `\frac{}{}`, `\sqrt{}`, base or script is the check `mathml.structure`, code
  `empty_argument`, with its own sentence on the review screen (`web/messages.py`).
- `\textbf{F}` of one letter keeps its bold ("bold capital F"; it was the plain text "F").
- `±`, `:`, `…`, `⟨` and `⟩` that the converter writes as identifiers are operators in the tree
  (`mathml_shape`), measured against Chromium's MathML widths; `′` and `%` stay as they are, since
  setting them as operators moves nothing a reader sees or hears.
- Speech (`pipeline/speech.py`): `\mid` is "given" between the arguments of `P(...)` and `E[...]`, "such
  that" in set braces and "divides" only for divisibility; a centered dot or a cross between vectors
  is "dot" and "cross" (the vector test of `signs.py`), "times" between numbers and scalars; `A^T`
  "transpose", `x^*` "star", `\dagger`, `\sin^{-1}` "inverse sine", `0^+` "0 plus"; `\sim` before a
  distribution "is distributed as"; `\setminus` "set minus"; `\mathit{diff}` is the word.
- Two different prints were said alike: `\frac{1}{2m}v^2` and `\frac{1}{2}mv^2`, `\frac{a}{bc}` and
  `\frac{a}{b}c`, `x_{iy}` and `x_i y`, `a_{nb}` and `a_n b`, `a\frac{b}{2}` and `\frac{ab}{2}`,
  `a_{ab}^2` and `a_a b^2`, `e^{-x}` and `e^{-}x`. A short fraction, subscript or sign a plain token
  follows now says where it ends, and a fraction after a plain token takes the long form. A search
  over 36,000 small formulas finds no pair left that differs in print and not in words, except those
  listed in docs/steps.md ("Speech"). Two spellings of one print are said alike: bars and signs the
  converter leaves as identifiers read as operators, as does a transposed run of letters.
- The spoken report (`scripts/realworld_report.py --spoken`) counts, beside the spoken score, the pairs
  the spoken rule moved from wrong to right and how many of them are one print.
- Changes what a conversion stores (an equation that passed with an empty part is now refused; every
  reading changes words), so `PIPELINE_VERSION` needs a bump at merge. Stored readings keep their
  words until the document is converted again (docs/steps.md, "Speech"). The registration of fresh5
  names `speech.py`, `validate.py`, `mathml_shape.py` and `rw_spoken.py`, whose hashes this changes;
  it already differed at the base on `math_canonical.py`.
## Unreleased: one label rule by distance, and three vanishing shapes (branch fix/figure-labels-vanishing-shapes)

`PIPELINE_VERSION` needs a bump at merge. Three workflow replays ask a model for answers
their recordings lack (r15_openstax_chem_ch3 2, r19_poster_codesign 4, r20_newsletter_drp 2):
they need re-recording, which costs a model run.

- SW40 (with WS6.12's reaches): a figure's labels were found by five reaches that disagreed
  (`LABEL_REACH`, `AXIS_LABEL_REACH`, `_absorb_labels`, `_absorb_outer_labels` and the dropped
  rows' `_figure_labels` take, with a test by kind of row). `_label_owners` is now the one
  rule: a short row of any kind and turning, within `LABEL_REACH` (1.0) x its font size of the
  figure's nearest drawn stroke (`_stroke_gap`: the strokes, not the box), or of a label the
  figure holds, or mostly inside its drawing, and no line of running text, a caption or code,
  is its label. It runs until stable; a row two figures could claim goes to the nearer. Turned
  rows reach 2.5 times as far (`LABEL_TURNED`) and hold 40 characters. A picture has no
  strokes: only the rows on it are its labels. A triangle's sides and angle, a circuit's part
  names, "R1 = 4 ohm" and a graph's tick labels are labels of their figure, not equations and
  stray paragraphs.
- A row that starts like a list item or a numbered title ("5. Blocks and springs": a marker
  of one or two characters and a word of three letters) is never a figure's label: a title
  beside a figure was taken as its label and lost as a heading (`_starts_like_item`; chosen
  by a count on the tracked files and the corpus, see docs/layout.md). A slide's footer bar
  set in from the page's edge is furniture like one at the edge (`_edge_band`): before, a
  code line or a total above it was its label and each of the deck's pages kept an empty
  figure for the bar. A caption's tail line is the caption's own, and a table's header rule
  alone is not a timeline's ruler (`thin_ruler` needs ticks of more than 1 pt).
- WS6.15 step 1: a timeline's ruler (a thin line with ticks) fell under the 24 pt a figure
  needs on both sides, so its drawing was dropped and its labels became a paragraph: a long
  ruler with ticks is a figure (`thin_ruler`). A free-body diagram's force arrows, each thin
  and 20 pt off the box, were lost: a small drawing joins a figure within `LEFTOVER_REACH`.
  A circuit's parts are found by the label rule. Step 2 (a count of drawings no element
  claims) is not built.

## Unreleased: a drawing set as glyphs is a figure (branch fix/glyph-drawn-figures)

- WS6.11 (F-550): LaTeX's picture environment sets slanted lines, arrowheads and arcs as glyphs
  of the fonts `line10`, `linew10`, `lcircle10` and `lcirclew10`, and xy-pic does the same with
  its `xy...` fonts (`pipeline/glyph_strokes.py`). Layout read them as text: a triangle's slanted
  side was a paragraph of 17 quote marks, its labels seven equations, and no figure was found.
  `_text_blocks` now takes those glyphs out of the text, and `_read_page` adds each as a stroke
  to the page's drawings (the segment a thin straight glyph draws, else its outline, from the
  page's SVG), so the figure clustering finds the drawing and no paragraph or equation is made
  of it. A font is a drawing font only by its whole name after the subset prefix. No other
  font is read this way: none of the corpus or test PDFs has a bare-stroke glyph in another font.
- Changes the elements of a PDF with such a drawing, so `PIPELINE_VERSION` needs a bump at merge.
  The new figure has no recorded description, so a replay of such a file asks the model for one.
## Unreleased: MathTime Pro 2 fonts are read as what they draw (branch fix/mathtime-font-tables)

- Text: the MathTime Pro 2 fonts (MT2MIT, MT2MIS, MT2MIF, MT2BMIT, MT2SYT, MT2SYS, MT2SYF, MT2SYAT,
  MT2EXA) have rows in `pipeline/font_codes.py`, keyed on the font and what its glyph is read as,
  and on the glyph's name for a glyph the reader gives as U+FFFD (`glyph_names.recover` asks the
  table first). Parentheses, "+" and "=", Greek letters, big delimiters, sums and the pieces of
  built-up brackets are no longer other characters or gap marks. The fonts count as maths fonts
  (`parse.MATH_FONT`, and layout's math italic, bold italic and maths symbol face tests). Element
  text changes on documents with these fonts, so `PIPELINE_VERSION` needs a bump at merge.

## Unreleased: right-to-left text and run-together answer options are a decision (branch feat/text-decisions)

- WS16.6 part 1: `rtl_text` and `option_labels` raise a decision on the paragraph or list
  item, not only a required notice: its own `Fix` (`NEEDS_REVIEW`, `review="required"`, reason
  `text.rtl_order` or `text.option_labels`), the shape a scanned page's paragraph has
  (`notices.text_decisions`, made in `run.py` after layout). The review screen has a card for
  it and a list-item kind table (`list-items`): the words as the PDF gives them (`dir="auto"`
  and the element's `lang`), "Looks right", a change of the words, and the page tools.
  Its notice is derived from the pending decision and goes when it is made. A decision counts
  everywhere one does (triage, the checked summary, the job state, the documents list, the
  review record and the CSV exports), and a decision on the same words carries over to a
  revised upload (`carry.py`). The job is "Needs your decisions", then "Ready to share".
- A result stored before has the element's reason and the bare required notice, and no decision:
  it loads as it did, listed under "Not handled in this file" and held at "Ready"
  (`states._HOLDS`); no migration. A block with both flags gets one decision (`text.rtl_order`)
  and the other flag keeps its bare notice and its hold.
- Not built: `text.join_doubt`, `text.tag_disagrees`, the page record, the document card and
  the per-source switch (WS8.22); no WS16.5 field that nothing fills. Changes the output of a
  PDF with such text, so `PIPELINE_VERSION` needs a bump at merge.
## Unreleased: blanks and boxes are tokens, form fields are read, border boxes are rules (branch fix/blanks-fields-border-rules)

- SW36 (reduced): one classifier of what a rule near a baseline is (`pipeline/baseline_rules`:
  strike, underline, blank or rule), asked by the struck-text test and by the new blank test.
  A rule drawn in a gap of a text row or after its last word is written as five underscores, a
  typed run of three or more underscores is the same, and both are `blank` runs; U+2610,
  U+2611 and U+2612 are `checkbox` runs with their state. The outputs say them in words
  ("blank", "Checkbox, checked:") and keep the printed characters hidden from a screen reader.
  `RunKind` gains `blank` and `checkbox`. No flag. A rule that meets the edge of another drawn
  path (the plate of a battery) or has text over or under it (a fraction bar, an underbrace)
  is not a blank. Blank equation lines keep the five underscores in the equation's text
  without a run: only paragraphs, list items and headings carry runs. `RENDER_VERSION` is unchanged: the new spans
  carry classes and no style, and a stored result has no such runs.
- SW39 (reduced): the fields of a fillable PDF are read (`layout.take_form_fields`): a text
  field is a blank or its value, a choice field its value, a check box or radio button a box
  with its state, a signature field a named blank. Their appearance is taken off the page
  copy first, so a field's value is no stray paragraph and its picture no figure with empty
  alt. The document gets the notice `form_fields` (Not handled in this file): the converted
  file cannot be filled in. Fields are described text, not inputs. A radio group is not one
  list, and a field's own tooltip is not used. No flag (`FORM_FIELDS` is not built).
- SW37 (reduced): a drawing of thin filled rectangles only (Word's cell borders, painted as
  one drawing) is paper rules, and a filled shape's thin boxes are rules in the table finder:
  a 4 by 3 table drawn so is one table and no figure, and a chart of filled bars stays a
  figure. Borders thicker than 1.6 pt are still bars. A column of check boxes beside their
  labels is a checklist for the table finder, not a table of boxes.
- `PIPELINE_VERSION` is not changed here.
## Unreleased: entries of a glossary, an index and a bibliography are blocks of their own, a contents page with no leaders is a contents list (branch fix/entries-contents-pages)

- SW34b: an entry of a glossary, an index or a bibliography starts a block of its own, by three
  signals that need no heading over them: a lead term (bold, italic or small capitals) at a
  line's start, ending in a period or a colon (`_lead_end`, `_starts_entry`); a bold or larger
  single capital alone on its line whose next line starts with that letter, when three or more
  different letters of one size and face do so (`_letter_rows`: a heading one level under the
  section; the order of the columns does not matter); and a hanging indent shared by three or
  more wrapped entries (`_hanging_entries`: a ragged right edge counts when the next line's
  first word would not have fitted, and a one-line entry between wrapped ones is an entry).
  Three or more lead-term blocks in a row are one definition list through the existing writer
  only when their terms run in alphabetical order (`_listed_terms`): a run of "Remark 2.",
  "Case 1:" or "Simon:" lines stays paragraphs. A term keeps its length when its entry runs
  over a page break. No new flag. Measured with layout only on 277 files (28 corpus files, the
  tracked files, and the fetched layout set and real-world papers the scripts name; no
  held-out folder), the branch's first version against its last: 46 files change. 20 files
  lose definition lists whose terms were not alphabetical (one had 68); the glossary the work
  was built on goes from 1 term to 22; six OpenStax books gain terms (one from 1 to 29); papers
  gain paragraphs where a reference list is now split by entry (2 to 28 a file; 6 read, all
  split at the references, and one numbered listing of a prompt is split line by line, which
  is wrong); no entry is all term any more. Index letters found rise in the OpenStax books
  (16 to 21, 6 to 13 and 5 to 7 in three of them) and the 3 panel labels A, B and C of one
  figure are no longer headings. Still wrong: an index set in two or three columns of short
  lines is read across its columns (the same before this branch; WS15.1), and a lone capital
  at the foot of a column can still be taken for a letter heading.
- WS2.7: a contents list with no leaders is found by its shape (`_shape_entries`): three or more
  lines in one column that end in a page number (or are followed by a line that is only the
  number), the numbers at one right edge, or six lines or more with each number after its
  title, not decreasing, under a "Contents" heading or on one of the first three pages. Each
  line is an entry that keeps its number as its last words; the section heading right after
  the list is not taken in. It is tried before a grid is taken for a table
  (`_contents_lines` in `_claim_tables`), so the reason `contents entry` fires and no table
  is made of the list. The entry links to its heading by its title with the number left out.
  Measured: 5 files change (174 list items and 10 headings fewer in a 900-page corpus file, 4
  development papers with 25 headings fewer in all), no table. Not read: a list of fewer than
  six lines with no common edge, a title that wraps onto a second line, a two-column page.
- SW34a follow-up: the 18 joins the indented-first-line rule makes in the development files
  (not the corpus), read one by one: 12 right, 6 wrong (two centered author and affiliation
  lines, two lines that open with a "[n]" reference number, a label in a diagram, one pair of
  ▷ items). Two wrong classes fixed in `_continues` (lines centered on one another are not an
  indent; a line that opens with a reference number starts the next reference; one test); the
  diagram label and the ▷ pair are left. None of the 113 files changes.
- `PIPELINE_VERSION` is not bumped here. The kinds on contents pages and entries change, so
  stored heading decisions on such pages are stale until it is.

## Unreleased: nothing is cut on paper, long descriptions print open, the maths font stack, the words of a picture kept from a page (branch fix/wide-content-print-math-font)

- SW45 (reduced), rules 5 and 6: on paper a table wraps inside the sheet, code wraps, and an
  equation is scaled to fit the sheet down to 70% of its size; one still too wide prints a
  note that it is complete on the web page (`assemble.print_width_bucket`, the `pw` classes,
  the print block of the stylesheet). Before, the scroll box of a wide table or equation was
  cut at the edge of the sheet with no mark (F-435). A code block with a line of 25 or more
  characters is a named tab stop (the line was 36; a block scrolls at 30 characters at 320 px
  and at 25 with text spacing, F-448). A long equation stays a scroll box on screen; no width
  calibration. Word, EPUB and Canvas are unaffected (the rules are print CSS; Canvas has no
  style block). `RENDER_VERSION` 7, so stored pages are made again when opened.
- WS21.2 (reduced to its wave 1a rules): a figure's folded long description prints open
  (`figure_details.DETAILS_CSS`, `::details-content`), where before paper had only the summary
  (F-436); in a browser without `::details-content` the summary says "open this description
  before printing". Only the print block of today's sheet; the print build (breaks, the
  contents list, link addresses, page markers, 12 pt text) waits for WS21.1. Word, EPUB and
  Canvas are unaffected.
- WS21.4 (reduced): the student file's `math` asks for the maths font stack Canvas already
  used, from one constant (`pipeline/math_font.MATH_FONT_STACK`), where it asked for none and
  a device without a maths font drew a row of letters; this helps a device that has one of
  the named fonts and changes nothing on one with none. The EPUB of a document with maths
  carries the vendored Latin Modern Math 1.959 (GUST Font License, unmodified, already in the
  repository for the review screen) as a font file with its license and manifest beside it,
  and asks for an installed copy first. Not built: the subset embedded in each page (a
  `data:` font needs a `font-src` in the page policy, fontTools and brotli at run time, and
  WS21.1), the face comparison, the chemistry attribute, the prime, the spoken fallback. The
  downloaded HTML and `/view` get the stack only; Canvas cannot carry a font; Word uses
  Office's.
- WS21.9 parts 1 and 4: the words of a picture kept from a page are one table
  (`assemble.KEPT_IMAGE_WORDS`: alt text, label and note by the fix's reason). A part kept as
  `handwriting_on_page` (which a box of vector lines was also named, before the reader had pen
  evidence) said "This is a picture of handwriting on the page" to a student; every stored
  result now says "Shown as a picture, not converted to text" there, in the note, the alt text
  and the statement's link ("Part 2 not converted: picture, page 1"), and never "handwriting",
  in the downloaded HTML, Word, EPUB and Canvas alike, since all are made from that page. A
  stored page is re-rendered by `RENDER_VERSION`. Alt text is never made at render, and what
  a person wrote for a picture still replaces ours (checked in
  `test_a_part_kept_as_a_picture_never_says_handwriting_and_a_persons_words_win`). Not built:
  a drawing's own reason and words, labels said once, the larger picture, checked and
  estimated chart values (WS21.9 parts 2, 3, 5), the short label with the full text once in the
  top notice (WS11.5). The review screen's and the job page's "handwriting" words wait for P1
  and P5.
## Unreleased: text is judged from the page it is on, and a sideways page is turned (branch fix/visible-text-sideways-pen)

- WS14.2: text a reader cannot see is left out of the rows. A span under 2 pt is hidden
  (`page_type.span_hidden`), and so is a span whose box on the rendered page is flat, its lightest
  and darkest pixels under a contrast ratio of 1.25 apart (white on white, navy on a navy box,
  black on a black box: `page_type._flat_spans`, added to the covered characters, counted in
  `hidden_text_ignored`). The page is rendered once, at 100 dpi, and only when it has a span that
  could be flat (text lighter than 2:1 against white, or text over an image or an opaque fill),
  and never when a picture on it is over the pixel budget; the box is judged with one pixel of
  margin (a hairline minus sign is thinner than a pixel), and a space paints nothing and is not
  counted. The notice's tier is unchanged: this branch has no tier between "optional" and
  "required". Text that is painted and cannot be seen is counted apart from text in render mode 3
  (`TextStats.unseen_chars`): it is not the mark of an OCR layer, so a typed page whose white
  text is longer than its visible text stays a typed page (F-573), and the hostile file
  `injection-white.pdf` converts with the white text left out and never sent.
- WS14.3: a page saved sideways with no /Rotate is turned before it is read
  (`page_type.turn_page`): when 80 % or more of its 60 or more visible characters run up the
  page it is turned 90 degrees, down the page 270, so its rows are horizontal and its headings,
  order, tables, crops and page size follow. The crop cache, the text pass, tagged figures, the
  source facts, the page labels and the review page image read the page the same way.
  Element boxes on such a page are in the turned frame, as on a page with /Rotate, and a table on
  it is found upright (`TableGrid.turned` is 0; `tables._page_turn` still turns a table set
  sideways alone on an upright page). The real-document table scoring maps the labelled boxes
  into that frame (`eval.tables.labelled_as_read`).
## Unreleased: paragraphs across indents and columns, drawings of fragments, options and right-to-left text (branch fix/paragraphs-joins-fragments)

- SW34a: a paragraph is not cut after its indented first line. While a run holds only that
  line, the next line may start up to 4 em to its left when the first runs to the right edge
  of its column (`layout._RightEdges`), is no list item or caption and does not end a
  sentence. A two-line paragraph whose second line falls short stays cut. No flag and no
  shadow run. On the fetched development corpus (28 files), with SW50a: 60 fewer paragraphs
  (3 of them the page joins below) and 2 fewer words, both a hyphen at a line end that a cut
  had kept and the join repaired, or a label that is now a list marker.
- SW50a: a paragraph that ends a page in one column goes on in the other column of the next
  page (`layout._goes_on`, `edges`): the head may sit at the left edge of another body block
  of its page, and across columns only when the tail has two or more rows. Joined across
  pages on the corpus: 10 before, 13 after; on the tracked PDFs 6 joins, each read by hand
  and right.
- WS2.9a: text fragments among short strokes (a drawn structure whose strokes no drawing
  cluster took) are one figure with the reason `drawing_fragments`, their rows kept as its
  labels (`layout._fragment_figures`). Contents pages (leader dots), derivations (a fraction
  bar or two) and any region with prose are not taken. The corpus and the tracked PDFs have
  none; the test page is a generated structure.
- WS2.4a: answer options that reading order put in one line are one item each; a block of
  several rows that still holds later labels keeps them and is flagged `option_labels`,
  listed as a required notice (`notices.from_reasons`). Two tracked blocks of
  `e1_mc_columns.pdf` are flagged; one corpus list item is split.
- SW54a: a paragraph or list item with two or more right-to-left words is flagged
  `rtl_text` (a required notice) until the reorder (SW54b, on hold) exists, and the two
  letters of a lam-alef ligature, which MuPDF returns reversed, are put back in order
  (`layout._swap_lam_alef`: "دوال" is "دولا", "األكسجين" is "الأكسجين" in `d1_scripts.pdf` and
  `d3_arabic_doc.pdf`). Six right-to-left blocks on the tracked PDFs, all flagged.
- No text decision exists yet (WS16.6), so options and right-to-left text raise a required
  notice and no fix: a paragraph fix would offer "keep as image" where only scanned-page text
  has an image to keep. The decision waits for WS16.6. `PIPELINE_VERSION` is not changed here.
## Unreleased: pictures found as LaTeX finds them, one sandboxed TeX compile, the Office census (branch feat/source-pictures-tex-office-census)

- SW18.2: a LaTeX source picture is found the way LaTeX does. `source.figures.find` takes the first
  of LaTeXML's `candidates` that is inside the project and of a kind that is read (`.pdf .png
  .jpg .jpeg .gif`), else the written name with those suffixes in that order, as written and in
  capitals; `figure_png` decides PDF or image from the bytes and no longer from the suffix of the
  name. `\includegraphics{fig1}` with `fig1.png` in the project was a figure with no picture (a
  required decision) before. `.eps` and `.svg` stay unread. Changes the output of such a source,
  so `PIPELINE_VERSION` needs a bump at merge.
- WS18.22: `source/tex_compile.compile_tex(tex, project=None)`: one sandboxed `pdflatex` run
  (LaTeXML's limits, no shell escape, TeX's own file rules refusing paths outside the work folder,
  font generation off, no network) returning a PDF or a named failure. No caller yet. The profile
  fact `tex_engine` is not added: nothing reads it yet.
- WS18.13: a Word or PowerPoint upload says what the PDF route did with it. The preflight
  returns a census of counts (`web.office_convert.Census`), stored on the job (`jobs.office_json`,
  and `pending_uploads.office_json` for the parts of a large file; migration `4e8a1c7d9b35`). A
  Word file with open tracked changes gets the required notice `office_tracked_changes` and is
  "Ready", not "Ready to share"; speaker notes, hidden slides and comments are optional notices
  with counts; the job page's "How this file was read" says how the file was read and how many
  typed equations were read as pictures. The CLI adds the same notices. Text moved with
  tracking on (LibreOffice prints it where it was and where it went) and tracked table cells
  count as tracked changes; a part that is not well formed refuses the file
  (`office_failed`) and is never counted as far as it goes.
- The notices that keep a finished job at "Ready" and never "Ready to share" are named in
  `web/states.py`: `office_tracked_changes`, and `rtl_text` and `option_labels`, which are
  required notices with no decision behind them until the text decision (WS16.6) exists. A
  job with one of the two could say "Ready to share" before.

## Unreleased: footnotes the file names, the equation scorer reads the output as stated, the second check off (branch fix/footnotes-the-file-names)

- WS2.8 (g), footnotes: a file that names its footnotes is read from the file. hyperref writes a
  destination `Hfootnote.N` about 2 pt above each footnote's line and a link to it from the mark;
  `pipeline.named_links.read` is the one place that reads destinations and links from PyMuPDF
  (SW52 can reuse it). The line a destination points at starts a footnote and the link's glyphs
  are its mark. In such a file a block it does not name is not made a footnote by place and
  size (the line numbers of a code listing in small type were 40 footnotes in one file), a
  footnote set in a mono font (a URL) is a footnote and not a code block, and a file that
  names none behaves as before. On the 13 development files that state footnotes (94 stated):
  found 59 before, 87 after.
- WS2.8 (d): a raised note number is set off from the word it touches at the glyph, where the
  row is cut (`layout._mark_notes`, `SET_OFF_FLAG`), not by repairing the string afterwards:
  "2Feehly" is "2 Feehly", the mark in the text is the note's link or a separate mark when no
  note is found, and a raised digit that ends a heading is a note mark, not one of its words.
- The document task's equation scorer reads the output as the student's file states it
  (`pipeline_eval.document_page_item`): the maths cells of rebuilt tables, and cells of text
  with raised or lowered characters, are equations of the page (`with_table_maths`; the old
  second reading `table_maths_reading` is the one reading now), and maths written as text with
  raised or lowered characters (`Element.script_runs`, `x²`, `^(..)`) is read as `<msup>` and
  `<msub>`, not as character order (`equations_v2.read_scripts`, `mathml_of_text`). The
  primary scorer's rules are unchanged. Scores of recorded document runs from before this are
  not comparable with new ones.
- WS10.5: `claude_second_check` is `off` by default (it was `shadow`): the check changed no
  output and was about 16% of the spend. `.railway/railway.ts` still declares the setting, so
  production can turn it on for a measured run. Docs and tests that assumed `shadow` set it.

## Unreleased: running heads by place, and a faster start (branch fix/running-heads-and-startup)

- SW5 rule 3: a running head or foot whose words change with the section (textbook heads:
  the chapter title on one side, the section title on the other, a page number beside it)
  was kept in the body, because only its exact text was looked for. `layout._margin_filter`
  now also finds a small line that sits at the same place (height within 2 pt, size within
  0.5 pt) on at least half of the left pages, or of the right pages, and drops it when the
  line is a head's or a foot's by more than its place: a number on it counts up with the
  pages, or its words stand there on another page. The place alone is not enough: the last
  line of a footnote, a one-line footnote and a slide's source line stand at one height in
  small type on page after page, and they stay (so does a row that goes on from the text
  next to it, and a row that opens with a raised mark). The place is searched in the top and bottom 10% of the page
  (`MARGIN_PLACE_BAND`); an exact repeat is still searched in 7%, so a body line that
  repeats word for word at 8% stays. A repeated heading such as "Solution" or "Example" is as big as the body, so it stays; a bare number
  that shares a line with a kept line (a table cell) stays. On the 10 OpenStax development
  excerpts, pages with a running head in the output went from 195 of 199 to 15.
- Source facts: the bold letters of a page are read once for all of its equations, not once
  per equation (`source_facts._bold_letters`); a letter is counted in the box its centre is in.
- Start-up: importing `pipeline.run` no longer loads the `anthropic` and `openai` SDKs (the
  HTTP math reader shares the retry constants through `models.base`), which is most of what a
  conversion process spent before its first page. The worker looks for a job as soon as one
  is queued by an upload, a source upload or a new part (`Worker.wake`), not at the next poll.
## Unreleased: the About note inside `<main>`, one Word filter (branch fix/about-in-main-and-job-summary, WS22.1)

- The student note "About this version" is written inside `<main>` (a labelled `<section>`, a
  region landmark, still no heading), so Word, EPUB, Canvas and Listen's transcript get it from
  the same markup, not from one patch each. `RENDER_VERSION` is 5: a stored page is made again
  once with the note in its new place. The page's outline, skip link and in-page link to the
  note are as before; the axe scans are clean in both colour schemes.
- The Word export's text changes made inside pandoc (page markers, the skip link, a figure
  with no picture) are one Lua filter, `pipeline/data/export_docx.lua`. The Word text of the
  stored fixtures is unchanged. The `<main>` unwrap, the long-description line and the
  title paragraph stay in Python: pandoc drops those elements before a filter sees them, and
  its file title and the title paragraph come from one field.

## Unreleased: polls and job routes stop reading the stored result (PF5)

- F-279, F-283, F-496, F-497: a job's row was read whole, so every status poll, every download
  check and every job route parsed the stored result and page (2.6 MB of JSON and 1.5 MB of
  HTML on the large stored job we measure with), even to say "converting". `jobs.result_json`
  and `jobs.html` are now deferred columns, loaded only by the route that reads them. A new
  column, `jobs.html_version` (migration `7b1d3e9a4c28`, nullable, filled on first sight for
  rows from before it), holds the render version of the stored page, so "is there a page" and
  "is it from an older renderer" no longer need the page; the draft gate reads the stored
  `jobs.open_decisions`. The state and summary of a job that is not finished no longer read its
  result. A finished job's page and poll still read the result, which its state is made from.
  Tests that hand a job row out of its session load the large columns with it
  (`db.LARGE_COLUMNS`).

## Unreleased: HEAD on downloads takes nothing (branch fix/about-in-main-and-job-summary, WS11.16)

- F-138: a HEAD request to a download or export was served as a GET, so it built the file,
  restarted the deletion clock and counted as a download. It now gets the GET's status and
  content headers (type, file name, content security policy) and no body, after the same access
  and draft checks, and builds, restarts and records nothing. The routes: the HTML, Canvas,
  Common Cartridge, Word and EPUB downloads, "Combine parts", "Download all", the course zip and
  the batch zip. A HEAD has no Content-Length, and the zips answer 200 without going through
  the documents, so a HEAD cannot tell that a GET would find nothing ready (a 409).
- F-137: `docs/security.md` said the Canvas, Cartridge, Word and EPUB downloads are for the
  owner only; the code lets a person the document is shared with, even as a viewer, take them
  (through the same draft gate). The docs now say what the code does.
## Unreleased: the browser suite passes again, and what the table page test cannot do (branch fix/table-gate-and-browser-suite)

- Four keyboard tests (wide equations, the described figure, the wide table) pressed Tab twice
  and expected the content; the notice that opens a page nobody has reviewed puts its own
  link first. They press Tab until the target has focus. The narrow table is still not a tab
  stop, and a wide table or equation still scrolls in its own focusable box with the page
  not scrolling sideways.
- Two tests expected "Converted" in the tab title of a finished job; the title is the state
  table's label (`web/states.py`) followed by the document name, as the status box sets it.
- The one-page summary for accessibility offices ran 110 pt onto a second Letter page (50 pt on
  A4) after its table gained a row per kind of material sent to a model. Its print margins,
  type size and spacing are tighter: one page on both, with room to spare.
- The regenerated-fixtures test prints with the machine's fonts, which differ (TeX Live's Latin
  Modern Math is picked ahead of Noto Sans Math), and it fails the same way on main. It skips,
  naming the fonts, when the print does not use the committed PDFs' fonts.
- docs/tables.md records why the table page test cannot be made tighter by the length of a
  rule (see "Which pages are looked at"); the test itself is unchanged.
## Unreleased: characters by glyph name, and a table caption keeps its raised marks (branch fix/characters-by-glyph-name, SW56 steps 2 and 7)

- A glyph the file's own table does not decode is read by the name the font gives it. TeX's
  extension fonts (CMEX10, CMEX9, txex, LMMathExtension10) have no table for their sum and
  integral signs, radicals, big brackets and the pieces of built-up ones, so the text layer
  gave U+FFFD or one of Adobe's private-use codes ("�ab f'(x)", "det A = ����"). The name is
  read from the embedded font program with MuPDF, through PyMuPDF (no new dependency), and
  looked up in `pipeline/data/glyph-names.tsv`, the names of TeX's extension fonts, generated
  by `scripts/make_glyph_names.py` from the LaTeX Project's `glyphtounicode-cmex.tex` (LPPL
  1.3c; source, changes and hash in `pipeline/data/README.md`). A sized variant is its base character; a built-up piece is
  the Unicode piece character. The ladder is the file's own table, the name, the font table
  (`font_codes`), then a gap. A raw character says which in `"how"` (`"name"`, `"font"`,
  `"gap"`).
- A glyph still undecoded is `output_chars.GAP` (U+2BD1), not U+FFFD and not dropped. It is
  not private use. The output filter and the lint treat it as they do U+FFFD (the page says
  "[unreadable character]"); the sign check and the glyph reconciler read it as an unnamed
  glyph. U+FFFD still fails the lint. The private-use brace pieces are no longer in
  `_OPENING_BRACES`: no glyph reaches it as one.
- A row of numbers that lies inside the box of a maths row belongs to it. The second row of a
  matrix or determinant sits inside the box of the first row's tall bracket, and was left
  beside the equation as a paragraph or a heading of its own ("1 3"); on the 132 PDFs kept in
  the repository, 11 such rows in 7 files are now in their equations and nothing else moved.
- A table caption is joined as a cell is: its raised and lowered glyphs stay raised and
  lowered ("CP³", not "CP3"), in the layout output and in the HTML. A repeated header on the
  next page is found with the same marks.

## Unreleased: struck text kept as struck (branch feat/run-list, WS16.1 and SW51a)

- F-509: text struck through on the page was read as live text ("October 3 October 10"
  with the first date struck read as two dates). `Element.runs` holds runs of an element's
  text with a meaning of their own; the one kind is `deleted`. The text pass marks glyphs
  crossed by a thin horizontal rule or bar between 25% and 65% of their height (no thicker
  than 0.12 of the font size), or covered by a StrikeOut annotation; underlines and rules
  under blanks are left alone. The LaTeX reader turns ulem `\sout` and `\xout` into the
  same runs. Struck text is kept, never removed.
- The output writes a struck run as `<del>` with a visually hidden "deleted: " before and
  "end deleted" after, and one `del` rule (a line-through that survives forced colors and
  the dark page). HTML, EPUB, Canvas and the Common Cartridge carry the same markup; Word
  shows the strike and the two phrases as words; Listen says them.
- Each element with struck text gets the optional notice `struck_text` ("Worth checking",
  with its page; no required decision). A text edit that changes struck words takes the run
  off and its message counts it; the notice goes with the last run.
- `core/inline.py` is the one place that moves, clears and adds spans: a page edit
  (`page_edits.remap_spans`), combining parts (`combine._element`), the writer
  (`inline_text.render_inline_text`), the LaTeX reader and a kept image page go through it,
  so a span kind added to `Element` is not lost by code that names the fields one by one.
- `CONTENT_RUNS` (off; production profile and `.railway/railway.ts`): off is the shadow
  rollout, where struck runs are counted in the trace (`deleted_runs`,
  `deleted_runs_written`) but not stored or written. Results without runs are stored and
  written byte for byte as before (no migration; the stored-demo golden file is unchanged),
  and a result with runs loads in the previous release, which writes the words plain.

## Unreleased: the LaTeX reader leaves nothing out unsaid (branch feat/latex-reader-drops)

- A picture outside a float (centered, in a sentence, in a list item, in a table cell) was
  dropped with nothing said. It is now a figure straight after the block that holds it, through
  the figure step; a picture in a cell makes its table a required decision, since the cell
  reads as empty. A picture inside a sentence follows the sentence until Element v2 (WS16).
- A float reads its tables or pictures and then everything else in it: the words beside a
  picture, a note under a table, every tabular of a table float. A `\captionof` caption goes to
  the picture before it. `\intertext` is a paragraph between the equations; `\date` is a line.
  A Beamer `\framesubtitle` (a block holding its words with no paragraph inside) was dropped
  and is now a paragraph under the frame's heading.
- A formula LaTeX could not read that a table, a heading or a caption shows as LaTeX code makes
  that item a required decision, with a sentence naming the command. The same command in a
  description or the authors, where no item can hold a decision, stops the job.
- The reader counts what no rule read and no entry of its skip list covers (`SourceReport`
  `unread_words`, `unread_pictures`, the first 12 words). Either above 0 is the required notice
  `source_unread`, and the finished job carries the gap note "Parts not read": it is "Partly
  converted" on the job page and in the documents list, never "Ready to share". The 45 constructs of the reader probe and every fixture read with
  nothing left out.
- The stop rule: a `\bibliography` whose `.bib` is not in the upload stops as
  `source_missing_file` naming `refs.bib`; a package with no binding whose command is the log's
  first error is named as the cause even when a syntax error follows (an mhchem charge in the
  text); a `^`, `_`, `&` or `#` in running text stops as `source_syntax_error` with a sentence
  that names the character and what to write instead (`&` and `#` stopped as `source_failed`).
- Pipeline 0.5.11.
## Unreleased: lines broken inside a token (branch fix/line-end-joins)

- Text: a number or an address broken at a line end is joined without a space. A digit, a hyphen
  and a digit keep the hyphen (`(2012-08-` / `17)` is one date); a line that ends in `/`, `.`, `_`,
  `-` or `=` inside an address goes on from it (`project/` / `memspectrum/`; a full stop joins
  only a host still short of its suffix, so a sentence that ends in an address keeps its space);
  a non-breaking hyphen (U+2011) after a letter or digit is a compound that must not break
  (`easy-` / `to-use` came out with a space). One function (`hyphens.joins_inside_token`) for
  paragraph, heading, caption, list-item and table-cell text, and for a table row split by a page
  break. Element text changes on the affected paragraphs, so their step keys change and stored
  answers for them are asked again. The page's link annotations are not read for addresses yet.
- Wingdings bullets (WS2.2a) were already built by the font code tables below; this branch adds
  the arrow and the remaining bullets to the test of them.
## Unreleased: one H1, and no empty alt text on content (branch fix/one-h1-no-empty-alt)

- The student page has exactly one `<h1>` and says its title once (SW6c, F-255, F-568). When
  the title is the first top-level heading (case, punctuation, spacing and the section number
  aside), that heading is the H1 and nothing is added; a single top-level heading is the H1;
  otherwise the title is added as the H1 and the headings move down under it. `<title>` is the
  H1's text, so the job page's "title students hear", the contents list, the EPUB, Listen and
  the Word file (which drops its title paragraph on the same comparison) all agree. Stored
  heading levels do not change.
- A picture gets empty alt text only as decoration with evidence (WS6.4, F-246): it repeats on
  the pages, it is a rule by its shape, or a person decided. A figure only the model calls
  decorative is a required decision, written as "Figure not described" (the words for every
  figure without a description, before "Figure without a description") in the page, EPUB,
  Canvas, Word and Listen until someone decides; a figure whose reading failed is decoration
  when the evidence says so. The evidence is stored on the figure (`FigureFix.decoration`), and
  accepting a decorative figure or checking "Decorative" stores the reviewer as its evidence.
  The new output check `html.img_alt_content` fails on empty alt text outside decoration, and
  the EPUB's `alternativeText` claim now allows decoration.
- `assemble.RENDER_VERSION` is 5, so pages made before are made again when next opened; a
  stored figure that was decoration without evidence becomes a decision then.
  `PIPELINE_VERSION` stays 0.5.11 (the same unreleased wave; its comment says what moved).
- Both this branch and `fix/about-in-main-and-job-summary` raised `RENDER_VERSION` from 4 to 5
  for different changes; with both merged it is 6, so a stored page made at 4 or at either 5 is
  made again once on its next open.
## Unreleased: one submit rule, larger touch targets, a quiet live check (branch feat/one-submit-rule)

- SW43, stage 1: the review, create and convert forms (31 form keys, 72 forms) are answered
  once however often they are sent. Each carries a request token minted when it is drawn
  (`form(key)` in `templates/components/forms.html`); the handler (`@submit_once`,
  `web/forms.py`) stores the outcome in the new table `request_tokens` (migration
  `8b3d5f0e2a71`) in the action's own transaction, and the same token again gets that outcome
  and runs nothing, also when the two requests arrive together. Another person's token
  replays nothing. A POST without a token works as before. The rows go at sign-out, with the
  account, and after 7 days; they hold a random token, the form and the outcome, no content.
- A repeat with a new token for something already done answers as the first click did:
  "Already saved" for a review decision the item has, the course for an Add course of a name
  you have, a notice for a symbol the course has with that meaning, success for a share
  already removed, a document already waiting after Try again, and a link already made.
- While a form is on its way the pressed button says so ("Saving…"), is marked busy and
  `aria-disabled`, a polite live region says the same, and a second click sends nothing
  (`static/app.js`); the upload form's own guard now uses it. `controls.css` has pressed and
  busy looks, with forced-colors and reduced-motion rules.
- SW17 (F-288): `--target-min` (24px) and `--target-touch` (44px) in `tokens.css`; with a
  coarse pointer the page chips, the Previous and Next page links and every `.link-button`
  are at least 44 by 44 CSS px.
- WS8.15 (F-088): the live LaTeX check answers a signed-out session with 204 and an empty
  body before it checks the CSRF token; signed in, a wrong token is still refused with 403.

## Unreleased: what is sent to a model, from one list (branch feat/sent-material)

- `web.recipients.sent_material(settings)` lists each kind of material sent to a model: the
  setting that turns it on, who receives it, what is kept from the answer and the day the privacy
  notice first named it. The upload and landing vendor line, the privacy notice, the offices page,
  its summary and drawing, the scanned-pages box and the account deletion notes render from it,
  so a setting that sends something new changes all of them at once. With tables and scans on
  (production), the vendor line keeps its old sentence; notation adds a clause.
- A production start is refused (and `readablestem ops launch-check` lists it) while a sending
  setting is on whose row the privacy notice does not state, whose row has no date, or whose date
  is still to come. Pictures of whole math pages (`FEATURE_PAGE_WITNESS`, `FEATURE_PAGE_MATH`)
  have no date yet, so they cannot start in production until one is set.
- The privacy notice's storage statements (the RETENTION_HOURS and one-week deletion, opt-in days, backup
  days, event retention and the small-count rule, courses, LaTeX problem names) are checked
  against the code at a production start. The notice now names the review-outcome notes and the
  rule under which their totals are read, and lists the day each kind of material was first named.
- `docs/vendor-information.md`, `docs/security.md` and the README name each sending setting.

## Unreleased: LaTeX classes and packages, read or refused (branch feat/latex-class-support)

- A LaTeX source in the `exam` class is refused at upload (`source_unsupported_class`): LaTeXML
  has no binding for it and its fallback printed the solutions and named the correct choice. A
  source that loads `csquotes` is refused the same way (`source_unsupported_package`): its
  binding reads the package's own TeX, which is hidden, and loading it alone gave 24 errors.
  The answer says to compile the version students should see and upload the PDF; nothing is
  queued or counted. The conversion child refuses the same sources, and a project folder in a
  batch zip gets the same answer. The upload help, /limits and the comparison guide name both.
- `source/support.py` lists every class and package as bound (LaTeXML 0.8.8's 43 class and
  356 package bindings, compared with the installed folder by a test), read without a binding
  (`scrartcl`, `scrreprt`, `scrbook`, `memoir`, `extarticle` as an article, each held by a test
  that converts it with no error; `babel`, whose commands stop the job when used) or refused.
  A class read as an article is no longer named as the cause when a job stops.
- The image build writes TeX Live's package names (from texdoc's data in `texlive-base`) beside
  the table and records their number as the image fact `tex_names`. A job stopped for a
  package's commands then says whether the package is one this tool does not read yet (upload
  the PDF) or the instructor's own, whose file in the upload is not read (copy the definitions
  into the source). Without the list the sentence is the one it was.

## Unreleased: one statement of what was checked (branch feat/checked-summary)

- `pipeline/checked.py` (`CheckedSummary`) holds what was converted by kind, the structure
  checks that ran and failed, what people did per decision (`core.triage.review_load`) and
  what is never checked; `pipeline/checked_words.py` makes every sentence about it. The
  student note, a new notice that opens an unreviewed page or one with pictures or pages
  left out, the job state, the job page, the before/after card, the review screen's finish,
  the record and its CSV, the EPUB's accessibility metadata and the Canvas README all print
  those sentences (SW7: F-034, F-110).
- The student note counts what a person reviewed ("Your instructor reviewed 3 of 5 parts the
  tool flagged"), never counts an optional note nobody opened, says parts kept with a check
  failing apart, and says how many parts are pictures and which pages were left out ("7 of
  8" when the page count is known). A re-render with no recorded decision (decisions
  carried from an earlier conversion) writes no date; it used to write today's.
- "Ready to share" only when nothing is a picture, nothing is left out, no structure check
  failed and nothing is open; otherwise the new state "Ready" says what remains. "Everything
  was converted" is now "Every part the tool found was converted."
- The EPUB's `accessibilitySummary` is the page's own statement, `accessModeSufficient` needs
  the page to say the text alone is enough, and a page made before the statement existed gets
  `accessibilityHazard` `unknown`. `RENDER_VERSION` is 4, so stored pages are made again when
  next opened.
## Unreleased: pipeline 0.5.11 (integration/wave-1a)

- `PIPELINE_VERSION` 0.5.11: the font code tables and the figure label crop (both below) change
  the text and the figure boxes of a PDF page, and the LaTeX class and package table (above)
  changes which LaTeX sources convert and what a stopped one records, so results and eval
  reports made from here on say so. One raise for the wave: 0.5.11 was not released when the
  table was merged. Results from 0.5.10 load unchanged. Every stored step answer was keyed on
  0.5.10, so a conversion run again asks its steps again, not only those of the pages that
  changed.

## Unreleased: one state table for a job (branch feat/job-state-table)

- `web/states.py` says a job's state from its stored facts: a key, label, tone, one-line
  message, next steps, whether it is final, and its gap notes. The job page (title, status
  box, "What you can do", the tab title and browser notice), My documents, the batch page,
  the parts list, the review screen's finish line, the record, the retry offer and the admin
  queue all print it; their own label dictionaries (`views.STATUS_LABELS`,
  `outcome.job_outcome`, `job_page.headline`) are gone.
- A finished job with decisions open is "Needs your decisions" everywhere, never
  "Converted"; a budget stop, an outage or pages kept as pictures are notes under the state
  ("Stopped early", "Service outage", "Pages kept as pictures"), and a note with nothing in it
  ("0 of N") is not shown.
- Stops say whose fault it was ("Stopped: this file cannot be converted as it is", "Stopped:
  not caused by your file", "Stopped: too large for one conversion"); an unknown stop names
  the step it stopped in and offers "Try again" while a try is left; with no tries left
  nothing offers "Try again". A stopped job no longer says its old result's decisions need
  you and is not offered "Keep for 7 days".
- My documents groups "Needs you" (decisions, then stopped documents with "Try again"),
  "Working" and "Ready". The heading over the job page's actions names the task ("Review",
  "Download") instead of repeating the state.
## Unreleased: no clock decides what a page contains (branch fix/ink-work-budget)

- **The ink analysis is bounded per page by counts of work** (plan WS14.9). It used to have
  10 seconds of wall-clock time for a whole document, so a slower or busier machine could
  type a page without its ink, and a handwritten page then read as typed. Now each page has
  its own allowance: at most 200,000 path items and 500,000 pieces of flattened path
  (`page_type.INK_MAX_ITEMS`, `INK_MAX_SEGMENTS`), the same on every machine; the corpus's
  largest page has 117,162 and 207,522. A clock stays only as a guard against a hang,
  60 seconds a page (`INK_GUARD_S`), about 40 times the slowest corpus page.
- **A page whose drawings were not analyzed says so and why.** Its stored page type keeps
  the cause (`ink_cause`: `work` or `time`; older results load without it), the job writes
  a "not finished" fact per page (`work_budget` for a count, `time_limit` for the guard, for
  both `page_type` and `ink_strokes`), a page read for its text gets the notice
  `drawings_not_analysed` under "Worth checking", and the job page no longer says
  "Everything was converted" for it.
- **The upload probe's count of scanned and handwritten pages can say "at least N".** The
  probe has a document allowance of 1,000,000 path items (`PROBE_MAX_ITEMS`) and a 20 s
  guard (`PROBE_GUARD_S`) inside its 30 s child; a page past either is counted as possibly
  handwritten, so a request to read such pages still applies.

## Unreleased: the measuring ground (branch feat/stage0-ground)

Nothing here changes what a converted document contains. It is what later changes are
measured and replayed against.

- **Test corpus and recorded answers.** A manifest of files made for the tests or fetched by
  hash, each converted through the app from recorded model answers, keyed by the question as
  sent. A replay sends nothing and costs nothing. The workflow suite (`-m workflow`) runs the
  audit's workflows on it, and a hostile-input set states for each of 36 files how its
  conversion must end.
- **Gold and evals.** Reading-order gold built from a document's source; a layout gold set
  drawn by seeded rules, and a held-out set of 51 documents no page of which was opened.
  Evals split by document, and only a held-out score can meet a bar.
- **Step facts.** Every model request and every stage of a job is kept as a content-free
  fact (`job_calls`, `job_stages`), with `readablestem ops step-report` and totals on the
  admin page.
- **Shadow steps** (`pipeline/shadow.py`). One way to run a new check or reader beside the
  conversion: it returns nothing, any failure of it is counted and goes no further, and
  what is kept is `same`, `differs` or `failed` by step and page (the `job_shadow` log
  line). No step uses it yet.
- **"Not finished" facts.** A step that stops before it has done all of its work says so:
  `{step, page, reason, done, of}` in the table `job_not_finished`, a log line, and one
  reader (`web.step_facts.not_finished`). Written by the steps the job's limit stops, the
  image pages it keeps from being read, the second reader's time and dollar share, and the
  ink analysis when its time runs out.
- **The same file gives the same document on any machine** (tests/test_determinism.py).
  Every read of a clock in `pipeline/` and `source/` is named in a table; the corpus is
  replayed at full speed and held back to a quarter of the time, and an upload may differ
  only if a step said it stopped. The first run found one: a handwritten upload lost a
  page's handwriting when the ink analysis's 10 seconds ran out with nothing recorded. The
  stop is now recorded. The clock itself is not yet replaced by a count of work.
- **Cost and speed of a page** (`readablestem eval cost`, `eval speed`): dollars a page by
  kind and by step and seconds a page by kind and stage, from a measured replay of the
  corpus under the production profile. The Sonnet cache-read price was double the published
  rate and is corrected.
- **No unused code, no error without a trace.** 26 functions and classes nothing called,
  an unused template and a limit nothing read are gone, and CI now fails for a new one
  (`scripts/dead_code.py`). A broad handler that only passes or continues fails the linter
  (ruff S110, S112); the five that did now count, log or return. When the conversion's
  child cannot read what the worker sent it, it says so: the job fails as
  `conversion_unavailable` (our side, "Try again" offered) with `conversion_child_link_failed`
  in the log, where before it looked like a crash on the person's PDF.
- **Every route has a test of what it does.** The test run notes each request that
  reaches the app, and CI fails for a route no test requests or whose tests only get
  refusals (`scripts/check_shards.py --routes`, `pytest --route-coverage`). The first run
  found one: the region preview picture, now checked.
- **"Download all" needs two.** The documents list and a course page offered "Download all
  1 ready document"; the line now appears from two ready documents up.
- **The quality ledger** (`readablestem eval ledger`): one table over the evals' kept
  records, led by the whole-document rows (silent errors per 100 pages, documents with no
  silent error, required decisions a page, false flags per true flag). Every figure is
  printed with its counts, a one-sided 95% bound and a label (held-out, development score,
  earlier release, or not measured with the proof owed). A goal is met only when the bound
  is inside it, a held-out figure worse than its release bar fails the run, and a record
  without its stamps is refused. No row has a held-out figure yet.
- **Reading order** (`readablestem eval order`): the gold pages of the corpus are scored in
  the page a student is given and in the element list, from the measured replay, with a bar
  that no page that was right is made wrong. The scorer (version 5) counts what a right page
  can still have wrong: furniture read aloud, a block cut, blocks run together, a caption on
  the wrong picture. Baseline: 58 of 78 pages right in the student page and
  33 faultless. `--set` scores a layout gold set with no model
  (149 of 267 on `layout-dev-1`); a held-out set is scored
  only with `--for-release` and its record names no page.
- **What a page asks of a reviewer** (`readablestem eval burden`): required decisions a
  page by page kind, by what is decided and by why it was asked, from the same measured
  replay, with a bar against an earlier record (no page kind asks for more). The count is
  the review screen's own (`core.triage.decisions`). Baseline on the corpus: 1.80 a page
  over 315 pages, 10.5 on dense math pages, and 56% of pages ask nothing.
- **A replay report names the steps that stopped early.** A step stopped by a clock gives
  another result on a busier machine, and a report made from such a run did not say so. The
  record of `eval cost`, `eval speed`, `eval burden` and `eval outputs` now lists every step
  that did not finish, and the printed header names them. Fixed with it: `eval cost`, `eval
  speed` and `eval burden` ended with a traceback when no upload could be measured; they now
  say so and exit with 2.
- **Every file a student is given, read back and counted** (`readablestem eval
  outputs`, `pipeline/output_audit.py`): the student page, the Word file, the EPUB, the
  two Canvas files, the Cartridge and the Listen transcript are built from a replay of
  the corpus, read from their bytes and compared, the exports with the page and the
  page with the stored result. 13 kinds of wrong place are counted, each with its page
  and element and none of the document's words. It gates nothing yet. The first run
  found three defects of the Word export that were in no list: every note's mark is a
  footnote with no words, the words written for a voice beside a formula in a table
  cell are shown next to it, and the title is said twice when the first heading differs
  from it only in case.
- **Fixed: two counters left out a page's paragraphs.** The command line's "decisions
  needed" summary, the eval's review load and the weekly report's review-load table
  counted only equations, figures, headings and tables. A page read from its picture asks
  about its paragraphs too, and the review screen counted those. Every counter now counts
  every kind of fix.
- **The page audit, in shadow** (`pipeline/page_audit.py`, `readablestem eval page-audit`):
  one request a page that sets the page's picture beside its converted text and answers
  with fault codes, element ids and boxes, never text. A page is a pass, flagged, or not
  audited with a reason; an answer that cannot be used is never a pass. It has a per-page
  ceiling and runs under the run's spend cap. Nothing reads its verdicts yet, no job runs
  it, and no live run has been recorded.
- **Dependency record** (docs/dependencies.md): versions, licenses and rules, checked by the
  suite.
- The production profile lists the page audit's three behavior settings at their code
  defaults, so its hash changes with no change to what production runs.

## Unreleased: the content budget (branch fix/content-size-budget, WS26.9, F-559)

- A PDF whose page's drawing instructions inflate past 32 MiB, or whose document's past
  128 MiB, is refused at upload with `content_too_large` (422, "too large to read"), before
  any quota is spent. The count runs through MuPDF's own decoders and stops at the budget:
  content streams and arrays of them, the forms, tiling patterns, Type3 glyphs and annotation
  appearances they draw, every filter and chain MuPDF applies, owner-password files; the
  object streams are counted before the page tree is read. The hostile set's `stream-bomb.pdf`
  (260 KB, one page of 256 MB) is refused in 0.36 s; it took 6.8 s of the upload request and
  24 s of the worker.
- `layout.analyze` makes the same check before any step reads a page, so a queued job,
  `readablestem convert` and the evals stop with `content_too_large` too, and a PDF figure of
  a LaTeX project over the budget is left without a picture. The budgets come from the
  development corpus (largest page 3.0 MiB, largest document 9.9 MiB, 2026-11-11).
- Workflow tests: `harness.ReplaySite.convert` uploads a corpus file before it skips one whose
  recorded answers are kept out of the repository, so every file present passes the upload's
  checks.

## Unreleased: what a reviewer did (branch feat/reviewer-outcome-event)

- Every review write records a content-free `decision_outcome` event beside `decision_made`:
  the element kind, the reason code that flagged it (or `none`), one of six outcomes (kept,
  edited, replaced, rejected, removed, added), origin, what raised it, single or group, the
  write mode and the release. Undo writes the same rows negated, so the sums are the
  decisions that stand. `events.outcome_cells(release)` reads them per lane with the
  small-count rules. A migration adds the eight columns and an index to `events`.
## Unreleased: the production profile (branch feat/production-profile)

- `src/readablestem/ops/production.profile.toml` says what production runs: every behaviour setting
  of `config.py` with its production value and the line of the repository it was taken from, the
  other settings named as operations settings (never valued), and the image facts the build writes
  (`readablestem ops image-facts --write` in the Dockerfile). Nobody has read the hosting dashboard
  for it yet, so it says `confirmed = false`: a production start whose settings differ from it logs
  `profile_mismatch` by setting name and starts; once confirmed, a difference refuses the start.
- `/healthz/ops` returns the profile's hash; /admin shows its name, hash and the names of the
  settings that differ; `readablestem ops profile-check` prints the same comparison.
- Eval reports record the profile (`profile` in the JSON, one line in the header): "development
  settings" unless the run's settings are the profile's (`--profile production`). A switch whose
  program the image facts do not name (unknown counts as absent) is printed as counted off.
- `.railway/railway.ts` names every behaviour setting on `web` (`preserve()`), and the docs and
  comments that repeated production values now point at the profile.
## Unreleased: copy leftovers and dev-only pages (branch fix/copy-leftovers)

- Copy: the maintainer credit (both footers, the landing page, sign-in, privacy and terms) links
  to "Support and continuity" on the offices page. The README, DESIGN.md and `docs/` use US
  spelling and US date order, the attribution table head and the stemcheck summary line too, and
  `tests/test_us_spelling.py` now reads those files and no longer lets two table cells run
  together into one word. The README and the public site say the hosted service takes PDF and
  LaTeX.
- Dev-only addresses: `/style` is mounted only where `ENV` is `dev` or `test` (`web/dev_only.py`)
  and answers 404 in production, as `/dev-login` already did. The stored demo moved from
  `web/static/demo/` to `web/demo/`, so `/static/demo/*` no longer serves it unsandboxed;
  `/demo` is its one address, and the landing page's PDF link is now `/demo/source.pdf`.
  `docs/` and `scripts/` stay out of the Docker image.
## Unreleased: stable element ids (branch feat/stable-ids, WS9.1)

- Every element has a stable id (`core/uid.py`, `u1-` and 16 hex digits) made from its kind, a
  content fingerprint, its page and its place among equal items on that page, so adding,
  removing or editing one element renames no other. Computed from the result when needed:
  stored results are not rewritten and keep every decision on its element; positional ids
  stay the label and the key, and are an alias of the uid. The version is in the prefix, and
  uids of earlier versions keep resolving.
- The routes that take an element id in their address also take a uid; one that names nothing
  is a 404.
- Decision carry-over and the carry of edits use the same fingerprint. An edit to a paragraph
  now carries when an item added above it renumbers its inline equations (it was dropped).

## Unreleased: font code tables (branch fix/font-code-tables)

- Text: codes of symbol and TeX fonts are read as what the font draws, by the font's name
  (`pipeline/font_codes.py`, one table module; docs/layout.md "Font code tables"). Symbol, Wingdings
  bullets and arrow, MT-Extra's ℓ and the APS math font's signs
  are mapped; TeX's ligatures at control codes ("effects" no longer loses "ff"), accents printed
  over a letter ("Ren´e"), and a glyph that starts several lines (a bullet from an icon font) are
  read. The private-use bullet rules that were mapped by code point alone (`_TEXT_MAP`,
  `_BULLET_GLYPHS`, the table detector's bullets) moved into it, so U+F0A7 is a small square only
  in Wingdings. Element text changes on documents with these fonts, so the step keys of their
  elements change and stored answers for them are asked again. Merged with the figure label
  crop under pipeline 0.5.11 (below).
- New setting `FONT_CODE_TABLES` (on). `false` keeps only the two old private-use bullet rules.
  Declared on `web` in `railway.ts`.

## Unreleased: a figure keeps its labels; drawings are named for what they are (branch feat/figure-label-crop)

- Layout: a vector chart takes the labels beyond its edge labels (`_absorb_outer_labels`): rows of
  spread-out tick or category labels, a rotated or centered axis title, a short quantity half over
  the drawing, up to three passes from the growing box. They no longer stay in the body as stray
  paragraphs, and the figure's crop now shows them (F-399, WS6.12 fixes 1 and 2). On the 12
  sealed generated figure documents: label kinds inside a figure 206 of 282 (122 before), printed
  labels 255 of 420 (149), no body text or caption lost, the same 54 of 60 figures found.
- Layout: a small drawing on a typed page is called handwriting only with pen evidence in the
  strokes themselves, on half its paths or more: a page-type pen stroke; a stroke that passes the
  same tests at any length (a signature); or a short straight stroke (a tick, a cross) whose lines
  all run off the axes, are not all one length, and neither meet another stroke's ends, lie
  alongside its lines nor repeat their length exactly. A hand mark over typed text still counts too. Being an open stroke no longer
  counts: a skeletal structure's bond lines now go to the figure step as a drawing instead of
  "handwriting, not converted" (F-400, WS6.10), while a grader's tick, cross or signature stays
  handwriting. The page type's pen test no longer takes a circle's arc for a pen stroke (a printed
  seal's open ring). A page-type pen stroke that is a filled glyph outline inside a typed line is a
  small drawing too (F-364).
- Figure step: labels newly in a figure change its text, so its step key changes and a re-run
  reads such figures again.

## Unreleased: copy slice (branch copy/vendor-spelling-continuity)

- Upload: one visible line beside "Upload and convert" says who receives the pictures of equations
  and figures (the recipient with the "model" purpose in `web.recipients`, so it follows
  `MODEL_PROVIDER`), links to `/privacy#who-else-sees`, and is the same include
  (`_vendor_line.html`) as the landing page's "What happens to your documents" answer. The
  duplicate hint "Uploads are never used to train models." is gone.
- Offices: the continuity section says what happens if the hosted service closes.
- File types: one helper (`office_convert.accepted_types`) and one include
  (`_accepted_types.html`) state what the installation takes (PDF, LaTeX when on, Word and
  PowerPoint only when LibreOffice is installed, with how to save a PDF when it is not). The
  upload form, `/limits`, the landing page, the ADA guide, the comparison guide, the refusal
  messages and the before-submit script all use it.
- US spelling and US date order in every visible string, with `tests/test_us_spelling.py` as a
  guard. "Tick" for a checkbox is now "check".

## Unreleased: a shorter homepage (branch ux2/home-compact)

- Landing: the before/after is one compact block, "Hear the difference": the same equation read two
  ways, in two rows (side by side from 48rem, stacked on a phone), each with a label, the equation
  on a small sheet of paper, what a screen reader says as plain text and a Play button (the same
  `data-hear` buttons, hidden without speech). The two tall cards, the "Untagged" and "MathML"
  badges, the boxed readings, the figure alt text paragraph and the sideways-scrolling PDF crop are
  gone. The crop (`scripts/hero_assets.py`) is now the equation and its number alone, cut from the
  PDF page at 4x, so nothing is clipped at 320px; the source line is one short line.
- Landing: "How it compares" is "How accurate it is": two charts (what a listener hears, and wrong
  equations nobody is asked about), their data table, one line with the sample and the registered
  wording, and one link, "All results and other tools", to the comparison guide. `proof_chart` has a
  `compact` option for this (`proof_facts.chart` rows carry a `headline` flag); the comparison guide
  and the offices page keep the full chart. The page's copy went from 571 to about 300 words visible
  before anything is opened.

## Unreleased: fixes from the final read of the public pages (branch ux2/final-pass)

- Listen: a sentence with inline maths directly in a list item, table cell or definition stays one
  item. Before, the maths (its MathML children are not "inline" tags) ended the paragraph, so
  "Remember in calculating change (Δ) we always use…" was read as three items. Display maths in a
  list item still stands as its own equation.
- Terms: three template blocks (title, description, body class) carried a stray `</details>`, which
  showed in the browser tab, the search snippet and the body's class list. A test now checks every
  page for markup in those three places.
- Posting: the at-a-glance line says Blackboard, Moodle and D2L are not tried yet, as the section
  below it does.
- Offices: the at-a-glance table says a deleted document can stay in a backup for up to about a
  week, as the retention table and the privacy notice do.
- Limits: "What it handles, with limits" and "What it does not do" are open when the page loads.
- Status: each state has a symbol beside its words (✓ fine, ⚠ needs attention, – not known); the
  symbol is hidden from screen readers because the words already say it.

## Unreleased: front-end code consolidation (branch ux2/lane-e)

- Stylesheets: each component's base, responsive and forced-colour rules are in one place in
  `app.css`, the blocks named for the work that added them are gone, and the button forced-colour
  rules and the danger outline are `controls.css`'s alone (`DESIGN.md`, "Stylesheets"). Rendering is
  unchanged except on a phone, where the file-name cell of My documents is no longer cut to 40% of the
  row (a rule meant for wide tables was overriding the phone layout).
- `tokens.css`: named layers (`--z-raised`, `--z-raised-top`, `--z-sticky`, `--z-fixed`, `--z-skip`),
  `--sticky-offset` and `--listen-sticky`, used by the review screen, tables and the listening player;
  the light palette for white paper is `--paper-*`, aliased by the paper scopes, the landing sheet, the
  diagram caption and the table header instead of repeating hex values.
- `app.js`: times swapped in by htmx are localised from the inserted nodes only (no rescan of the page);
  the Symbols screen announces the ticked-places count politely (a visually hidden status) when no
  replacement is chosen yet; when a draft cannot be stored (storage full) a status says "This draft could
  not be saved on this device." and the older draft is dropped.
- Tests: scoped localisation, the symbol-count status and the draft-storage failure in Chromium; layers,
  paper tokens and one-place rules in `tests/test_design_tokens.py`.

## Unreleased: the review screens, list view, Symbols and bulk accept (branch ux2/lane-b)

- Review (split view and list): the choices and what each does are one block (the recommended
  repair and Accept first; "Keep as image" apart, named for what students get); the card's details,
  codes and report link are folded into "More about this item". Phone: a Decision / Page bar,
  "Fit to width" and "Enlarge" for the picture of the PDF. Shared markup of the two views is in
  `_review_shared.html`.
- Figures: **Save and accept**, **Save, decide later** (saved, still waiting) and **Accept**; alt-text
  help for the kind of figure; "Keep without a description" needs a tick box saying what it means for
  students (the server refuses it without; `FIGURE_KEEP_ACK`).
- Tables: findings name the part of the table (a check with no sentence of its own no longer says only
  that "a check did not pass") and each has its correction; headers, a cell and the grid read from the
  PDF are one folded place; "Keep as image" and "This grid is not a table" are apart from the repair.
- Repeated headings can be decided one by one on the review screen.
- List view: the first decision comes first; sections, filter and accept-many are one folded disclosure;
  compact rows from the second decision.
- Symbols: places are ticked until unticked (`include`, `places=chosen`; `exclude` still works);
  "Change all" waits for a replacement, a live status line says what will change, and the empty screen
  says when it becomes useful with a small example. Bulk accept: shorter opening, and with nothing to
  accept it names the next step.
- Less copy on these screens: shorter headline, hints, consequences and the bulk titles ("Accept
  equations that passed every check", "Accept figures with generated alt text").
## Unreleased: upload, My documents, the job page and downloads, from the second independent review (branch ux2/lane-a)

- Copy on these pages is about a third to a half shorter (upload form, My documents, job page in
  each state, the stopped and deleted pages): one idea per sentence, detail only where it protects
  the reader (retention times, what is sent where, what a refusal cost).
- Upload: the file field and its button are the form; "How to convert it" and "How long to keep it"
  are one line each (saying what is set) until opened. The scan box says first
  that a PDF of only scans is refused without it and a mixed PDF keeps its scanned pages as
  pictures; "Tick it every time" is now "Make this the default in Account"; "Main file of a zip"
  sits in a disclosure and appears only with LaTeX source on. After a refusal the form comes back
  before the documents list with every choice kept, the error summary takes focus and its
  link goes to the file chooser, right under it. LaTeX source that is not available says so and offers the PDF route; a scan refusal
  where the scan option is offered no longer says the tool cannot convert it.
- My documents: rows lead with the uploaded file name (a part with its pages; the generated title
  under it; a title you set leads), one next step and Delete per row ("Upload a revised version" is
  on the document's page), "Upload another file" beside the heading; a first-time visitor gets the
  form first, then the sample.
- Job page: five groups with links to them (Review and download, For students, Settings and
  sharing, Record, Delete); the seven tiles are one line with "Counts in detail"; sharing says it
  is for working with a colleague and not how students get the file; the headline says "before you
  give it to students"; retention is explained once, in the Delete group, with the real deletion
  date, that your own work restarts the time and that people you share with do not extend it.
- Downloads: "Where will you post it?" lists the web page (recommended), Canvas, Word and EPUB, each
  with its own link; the posting steps and the checks are said once.
- Waiting and running: elapsed time ("Waiting for 3 minutes."), a qualified estimate, and a plain
  "taking longer than usual" note with no percentage; the batch page shares the part.
- Stopped: the reason is in the status box once, and "What you can do" gives Try again or safe ways
  on (upload again, My documents, service status, email); scanned and handwritten messages depend
  on whether the scan option is offered.
- Deleted and unavailable documents: "kept N hours in all" instead of "after upload", the current
  retention times, "Upload the PDF again"; a document that cannot be opened gets the same
  help text whatever the reason. Privacy and Limits name the saved Account default.
## Unreleased: second UX round, lane C (branch ux2/lane-c)

Listening, page picking, sharing, account and deletion, courses, batch, confirmations and error pages. The copy on
each of these pages is cut to roughly half or less; every fact that protects the user (what is deleted, kept or shared)
stays, in fewer words and next to the action.

- Listening: Play (it becomes Pause), Previous and Next are the transport; stop, jump, speed, voice and keys are under
  "More controls". Play starts at the lesson ("Play from the beginning" is under More controls) and the title, notices
  and licence are folded under "Before the lesson". Why Play is off (no voice, only online voices) is a line right
  under it with "Read the transcript". A compact index of headings and equations, a text and kind filter, hairlines
  instead of a box per item, and a scroll offset that follows the transport's measured height (tested at 200% text).
- Choosing pages: two page fields with "Add part" and "Remove", the parts as a list with page counts and one summary
  line (also after an error, marking the part that is wrong); the typed box is under "Type the pages instead"
  (`ranges.preview`, `GET /uploads/{id}/preview`, `static/parts.js`). The daily quota is said once.
- Sharing: the page leads with add a person, copy the link, send it yourself; when access ends is beside Share and
  beside Copy link; the exact-address rule is under "Help with sharing"; an error summary sits directly above the form.
- Account: how long documents are kept comes before the limits, with its own save button; deleting is a short list.
  Account deletion leads with download links, a short "not reached by deleting" list and the button; the long
  provider and backup prose is one link to the privacy notice.
- Courses: documents and symbols per course with a "Set up notation" or "Edit notation" action; an editable starter set
  of common bold vectors and matrices (`POST /courses/{id}/starter`); adding documents already converted to a course
  (`POST /courses/{id}/documents`), which says plainly that they are not converted again; notation, documents and
  rename or delete are separate sections.
- Confirmations (document, account, course, discarded upload): the button names what it deletes ("Delete “Lecture 3”"),
  "No, keep it" is a button of the same size, and the footer is compact.
- Batch: a next-step line (what to do, and how long ago it was uploaded), a clearer "Download all" note, and documents
  with the same title lead with their file names.
- Error pages: 500 and 503 carry the wordmark, a My documents link, the status page and a contact link; the 404 says
  what a missing document or course can mean without saying which.
- Tests: `tests/test_web_ux2_lane_c.py`, and the listening, page-picking, keyboard and course browser tests.
## Unreleased: UX round 2, lane D: public pages and the shared chrome (branch ux2/lane-d)

- Offices page: a table of data by kind (what is sent where, for how long) now leads the page, a
  retention table replaces the retention bullets, and every scrolling table is a focusable, named
  box (fixes axe `scrollable-region-focusable` at 320px; `_table_scroll.html`). The by-kind table,
  the by-company table and the downloadable one-page summary are all written from one list
  (`offices.data_items`), so the summary now includes tables, opted-in full-page images, course
  notation and handwriting close-ups when they are on. The summary's pilot is an ordered list, its
  headings step down in size, it has no raw URLs, links back to the live page and still prints on
  one page.
- Privacy opens with a default-and-exceptions retention table and says the 7-day choice can be
  the default in Account. Terms open with a short summary. Known limits open with what you can
  upload, then what it handles, review time and accuracy. Posting help and the ADA guide open
  with a short list.
- Comparison guide: shorter title ("Choosing a tool for STEM course files"), a short decision list
  before the table, and LaTeX source in the Inputs cell; the landing, limits, ADA, offices and
  privacy pages name LaTeX source under the same flag. The guides index gives each guide an
  audience line.
- Landing: a headline, one line, the real before/after, a comparison chart (ReadableSTEM, the Opus
  chatbot and the Haiku chatbot on the newest registered test, drawn from
  `data/realworld/results-fresh5.json` by `web/proof_facts.py`, with a data table behind it, and the
  first test's heading and figure counts), three lines on what it handles, and the questions
  folded; under half the words shown before. The earlier chatbot-ahead result is history on
  `/limits`, in PROOF.md and the README, not on the landing or comparison pages. The same chart
  is on the comparison guide and the offices page (`_proof_chart.html`).
- Long public pages (offices, privacy, terms, limits, the ADA guide, posting help, the comparison
  guide) open with a summary or table and fold their detail into disclosures under the same
  headings.
- Demo: the note above the paper is two short paragraphs with the licence, original page and the
  Teacher Support explanation in a disclosure (the committed demo file carries the new note; the
  conversion itself is unchanged).
- Status: "No recent requests; availability untested" or "Recent requests were answered", the
  time of the last request, an optional one-minute refresh (`?refresh=1`), retention prose removed.
- Sign-in: the account-creation consent sits under the buttons; sign-in pages use a five-link
  footer. Signed-in pages put My documents, Courses and Account first and have a footer of six
  links; footer and cancel links are 44px targets.
- `/style`: every demonstration is labelled an inert example, the light, dark and forced-colour
  states are shown, and examples link to the pages where they are in use.

## Unreleased: the Landlock launcher counts the user's tasks as the kernel does (branch latex/nproc-hidden-tasks)

- The program's task count is bounded by the supervisor's check (every 0.05 s) and the caller's
  poll, not only by `RLIMIT_NPROC`, which gives room when other tasks of the user exit after it was
  worked out: over the allowance the tree is killed (`over_tasks`); the probe checks it
  (`sandbox_task_growth`).
- Fixed: on Railway the launcher set `RLIMIT_NPROC` from the tasks visible in `/proc`, below
  the kernel's count of the user's tasks (some are in other pid namespaces), so every fork
  failed and the probe's LaTeXML run timed out and its group-memory test never started. The
  count is now asked of the kernel (`fork_fits` search), the busy check uses the same question,
  and the limit is never set above the hard limit. A failed probe prints LaTeXML's step, exit,
  whether it timed out, the sanitised end of its own output for the probe's fixture, and why the
  group-memory test did not run. No change to what the sandbox allows.

## Unreleased: the Landlock launcher starts where the web process is pid 1 (branch latex/launcher-railway)

- The image's ENTRYPOINT is now `tini`, so the web app is no longer pid 1 and the orphans and
  defunct launchers left when the sandbox's caller is killed are reaped; on a stop the Landlock
  supervisor kills and reaps the program's tree itself (SIGTERM first, the group kill as
  backstop); the probe shows `init_process=`.
- Fixed: the launcher exited silently when its parent was pid 1, which is the web process in
  Railway's container, so the Landlock backend never started there (`landlock_step=no_report`).
  It now checks that its parent is the process that started it (`parent_pid`), and every
  supervisor-side check has a step code (`caller_gone`, `supervisor_gone`, `count_tasks`,
  `launcher_exception`); if the subreaper right is refused the process group is killed instead.
  Found by running the probe in a podman container as uid 10001 with a read-only root and the
  default seccomp profile (docs/deploy-railway.md). No change to what the sandbox allows.

## Unreleased: fifth fresh split, registered result (branch eval/fresh5-report)
## Unreleased

- LaTeX source is on in production (2026-11-04 13:53 UTC): `FEATURE_LATEX_SOURCE=true` after Railway's start-up sandbox
  probe passed on the Landlock backend (tini as pid 1; isolation, memory, task, task-growth and real-LaTeXML checks
  ok). `FEATURE_BEAMER_SOURCE` and `FEATURE_BATCH_ZIP` stay off until real decks and course sources have been tried.
  Docs, the compare guide, `/limits`, the landing page and the upload form say what is taken: a `.tex` file or a zip of a
  LaTeX project, the PDF still the main path, not Beamer source yet.

## Pipeline 0.5.9 (2026-11-04)

Everything that was listed as unreleased up to 2026-11-04, grouped by area (newest entry first within an area, as it was
written). Each entry keeps the branch it came from. The pipeline is version 0.5.9. LaTeX source, Beamer and zip-batch upload
are in the code and off on readablestem.com until the sandbox check passes there.
A setting an entry calls "off" is off by default in the code; which ones production turns on is in
[docs/deploy-railway.md](docs/deploy-railway.md), "Feature flags in production".

### Equations and speech

#### Equation reading push (branch eq/read-push)

- Pipeline 0.5.9. New setting `EQUATION_GLYPH_RECONCILE` (off; needs `EQUATION_GLYPH_CHECKS`):
  an equation's reading is corrected from the PDF's own glyphs where they leave one answer: one
  named lookalike symbol in a TeX-made PDF, a unit the line context added after the box, a printed
  slash kept as a slash, a display's equation number taken out of its LaTeX, `\mathscr` for an RSFS
  or EUSM face, and `restyle_bold_italic` for a table's maths cells. A correction is a note on the
  fix (`equation.pdf_correction`, review "optional") and the model's reading is kept
  (`EquationFix.model_latex`). No request or prompt changes (`docs/steps.md`, "Corrections from
  the glyphs"; `docs/eval-dev-baseline-2026-11-03.md`, "Reading push").

#### Display grouping (branch eq/display-grouping)

- New setting `LAYOUT_DISPLAY_GROUPING` (off; output byte-identical to before when off, no pipeline bump until it is turned on).
  When on, layout joins pieces of one printed display: two formulas with a connective word between them
  ("P_A = ... and P_B = ...", "0 < p < 1 for all i") are one display element holding the word and
  the one equation number, where the word was a paragraph read after both equations; the right
  half of a long aligned row and a row that starts with "=" join the display above them; a
  trailing ellipsis ("j = 1, 2, 3, ...", "0 > s_1 > s_2 > ...") stays in an inline formula.
  Conservative: any other word, two numbered equations, or a fraction far from the row keep the old
  reading. `docs/layout.md` "One printed display, one element"; `scripts/layout_identity.py` lists
  what changed on 149 repository PDFs and the development, fresh and held-out papers.

#### Equation findings of the 0.5.7 baseline (branch eq/fix-0507-findings)

- Pipeline 0.5.8. arXiv PDFs built by arXiv's own `tex2pdf` ("arXiv GenPDF (tex2pdf:...)") and VTeX
  files are TeX-made again (0.5.7's whole-word producer match had lost them, so they got no unit
  trimming, no table-cell maths and no short-formula rules). Every distinct producer and creator in
  the repo's PDF corpora is a test case (`tests/test_producer_hint.py`).
- A table cell of only maths-symbol glyphs (a lone `\checkmark`, `\times`) is an inline cell
  equation, with the same one-glyph-one-cell and text-equality checks as other cell maths
  (`docs/tables.md`). Bullets and Dingbats are not taken.
- A cell equation's line crop and glyph context are cut to its own column, not the table's width
  (the next column's "(5)" was read into `S^3`).

#### TeX producers (branch eq/tex-producer-xetex, on eq/inline-simple-maths)

- `page_type.producer_hint` calls a PDF TeX-made when its producer or creator names TeX as a whole word
  (`page_type.TEX_PRODUCER`): pdfTeX, XeTeX and XeLaTeX (`xdvipdfmx`, offered by Overleaf), LuaTeX, LuaHBTeX, dvips,
  dvipdfm(x), Tectonic, and "LaTeX" or "TeX" in a creator (Ghostscript alone is not). It matched the substring "tex",
  so XeLaTeX and Tectonic files missed every TeX rule (the unit after a number, index punctuation, table-cell maths,
  short formulas) while Quartz ("PDFContext", a Mac-saved Word file) and iText were taken for TeX. The Beamer check of
  `slides.py` is a whole word too. Every caller of `producer_hint` (layout, the page-type refusal and image-page counts,
  the page-type eval) takes the value from this one function.
- Layout identity over 149 PDFs: 136 identical, 13 different, every one a TeX-made file read by the TeX rules now (7 only
  record the producer; 6 change: units leave their formulas, table cells gain equation elements); letters and digits of
  every page equal before and after. The golden `flags_off_problem_set.html` is regenerated: its `t = 4.0 s` formulas no
  longer hold the unit.
- Tests: `tests/test_producer_hint.py`.
- Pipeline version 0.5.6 to 0.5.7; results from 0.5.6 load unchanged.

#### Short inline maths from glyphs (branch eq/inline-simple-maths, on eq/t10-table-cell-maths)

- A relation set in a maths font before a number (`\approx 0.14`, `\geq 2`, `\geq 90\%`) is one inline formula in a
  TeX-made PDF (`glyph_math.relation_runs`); the run finder had dropped the relation as the tail of a cut formula and the
  number had no evidence of its own. Nothing else was added: a number with a percent sign (a trial rule predicted 20 and
  aligned 4 on dev2), a number with a unit, an equation reference, a bare number and an upright letter have no glyph that
  tells the author's maths mode from text, so they are not taken (docs/eval-equation-diagnostics.md, "Simple inline
  maths"; docs/layout.md "Short formulas the runs miss"). Page numbers, citations, list markers, years and figure numbers
  are never taken: `tests/fixtures/layout/latex/short_maths.pdf`.
- Measured offline (layout alone, no model): 6 gold equations found that were not and none lost, in 3 of 85 papers;
  recall 0.844 to 0.846 on dev2 (2,153 inline gold), 0.765 to 0.766 on held-out and fresh2 to 4, precision unchanged; the
  148 PDFs of `scripts/layout_identity.py` are identical. `scripts/eq_short_missed.py` says why short inline gold is
  missed (what the glyphs there are); `scripts/eq_span_edges.py` prints precision by evidence.
- Pipeline version 0.5.5 to 0.5.6; results from 0.5.5 load unchanged.

#### Maths in table cells (branch eq/t10-table-cell-maths, on eq/t11-glyph-spans)

- A table cell that is one formula by its glyphs (TeX-made PDFs; the rules that find inline maths in prose, applied to the
  cell alone) is an inline equation element of the table (`layout._cell_maths`, `TableCell.equation_id`). The equation step
  reads its crop with its checks and its place in the review, and the page writes the reading into the table's cell when
  the table step wrote that cell as text (`pipeline/table_math.py`, `assemble.render_html`); a cell the model already
  wrote as maths, a failed reading and a table kept as a picture are left as they are. With tables off, `run.py` drops
  the cell equations. docs/tables.md "Maths cells from glyphs".
- Measured offline (layout alone, no model): gold equations typed in a float found 661 to 892 of 2,153 over the five
  development and replay parts; overall inline recall 0.731 to 0.759 on held-out and fresh2 to 4 (6,444 gold), precision
  0.897 to 0.891 (one paper, `68.8±0.7`, holds most of the fall); 146 of 148 layout-identity PDFs identical, the two
  that differ are TeX-made (the Beamer table deck gains 6 equation elements, `real-tables/2407.13734` 4; no text lost).
- A cell and its equation are edited as one (`web.review._written_through`): LaTeX typed on the table card is written to the
  equation with the same checks and an equation edit to the cell, as recorded changes of one write that Undo takes back
  together; the card marks such a cell "(also an equation)". `tests/test_web_table_cell_equation.py`.
- `scripts/eq_span_edges.py` also counts the cell equations (predicted, aligned to a gold equation).
- Tests: `tests/test_table_math.py`; `tests/test_layout_beamer_math.py` (the table-cell test now asserts the equation
  elements; the inline count of the table deck stays a strict xfail: its two cells with a number set in maths mode and a
  text-font unit have no glyph that says they are maths).
- Pipeline version 0.5.4 to 0.5.5: `TableCell.equation_id` is new (left out of the JSON when empty, so a record without it
  is unchanged); results from 0.5.4 load unchanged.

#### Inline span edges from glyphs (branch eq/t11-glyph-spans)

- Inline formulas of TeX-made PDFs start and end where the glyphs say (`pipeline/glyph_math.py`, docs/layout.md "Inline
  span edges from glyphs"): a comma, colon or semicolon set as an index stays in the formula (`\rho_{m,q}(x)` was `q(x)`),
  a radical is not cut from its radicand, a known upright operator name touching its bracket (`\Pr(A)`, `\mathrm{supp}(\psi)`)
  and a percent sign after a number are in the formula, and the unit after a number (`24h`, `m^3 kg^{-1}`) is out of it.
  Nothing becomes a formula that was not one, so no text is lost: letters and digits of all 148 PDFs of
  `scripts/layout_identity.py` are equal before and after, 146 PDFs identical, 2 TeX papers differ.
- Measured with the new `scripts/eq_span_edges.py` (layout alone, no model): inline span edge errors on the development
  gold 4.7% to 3.8% (dev2, 2,153 inline gold), 4.5% to 4.0% on held-out and fresh2 to 4 (6,444, replay only), precision
  0.893 to 0.902, recall 0.730 to 0.734 (docs/eval-equation-diagnostics.md, "Inline span edges"). No model read the
  changed spans.
- Tests: `tests/test_glyph_math.py`.
- Pipeline version 0.5.3 to 0.5.4: an inline formula's crop, id and marker text can change; results from 0.5.3 load
  unchanged.

#### Page witness, why it changed nothing (branch eq/witness-diagnosis)

- Page witness (off: `FEATURE_PAGE_WITNESS`): when the crop's reading and the witness's tie on the letters, digits
  and Greek letters of the glyphs (23 of the 24 disagreements of the E1 dev run did, so the crop's reading always
  stayed), the slashes in the glyphs now break the tie (`witness_reconcile.marks_dice`): a written `-2/3` beats a
  drawn fraction, and the other way round. Both readings are still shown and the formula is still a required
  decision. Re-scored on the recorded E1 answers (12 dev papers, 1,096 gold, $0): exact 0.753 to 0.755, silent
  wrong 8.58 per 100 gold and decisions a page 4.35 unchanged. `scripts/eq_dev_run.py` has an offline arm
  `witness-sonnet-r2` for it and passes the two flags `realworld_run` gained. docs/eval-equation-round5.md.

#### Page witness dev run (branch eq/e1-witness-dev)

- `scripts/eq_dev_run.py` (new): the page witness on or off over twelve development papers, crop answers
  replayed, with a report and a pair-by-pair `witness` summary; `scripts/realworld_run.py rs` takes
  `--section-workers` and `--concurrency`. Result in docs/eval-equation-round5.md: no accuracy or
  detection gain with Sonnet as the witness, silent errors down 2.1 per 100 gold, one more decision a page,
  +2.4 cents a page. The flag stays off.
- Fix: the page witness's calls had the client's 60 s timeout, so a dense page timed out and was charged
  its whole reservation; `PAGE_WITNESS_TIMEOUT_S` (default 240) is set on the request.
- Fix: `eval pipeline` left the witness's cost out of an item's `cost_usd`; items now carry `page_witness`
  and `page_witness_cost_usd`, and the cost block `page_witness_usd` and `page_witness_per_pdf_page_usd`.

#### Calibrated flag policy (branch eq/flag-policy)

- Flag policy (off: `FLAG_POLICY`): `pipeline/flag_policy.py` turns an accepted equation that nobody is asked
  about into a required decision when a small logistic model over content-free features (second reading, notes,
  extension past the span, size, notation classes) puts it with the readings that turned out wrong. It only adds
  decisions, makes no model call, and its weights and threshold are constants fitted on the development splits
  (`flag-policy-1`; `eval/flag_features.py` dumps the features of recorded runs and fits them at $0). New check
  `equation.flag_policy`, code `flag_policy`, with a review sentence. `scripts/realworld_run.py rs` takes
  `--flag-policy`. Replayed on the recorded runs: on fresh2 to fresh4 (validation, 45 papers, 5,018 gold) silent wrong
  8.83 to 7.77 per 100 gold, flag recall 0.29 to 0.38, decisions a page 3.55 to 3.92; the goal (silent 4 per 100 at
  4 decisions a page) is not reachable with these features. docs/eval-flag-policy.md, docs/steps.md.

#### Both readings on the review card (branch eq/review-both-readings)

- Review card: a typed equation the page witness read differently (`witness_mismatch`,
  `witness_extent_differs`) shows "The other reading" under the proposal: its formula, how it is read
  aloud, why the item is asked, **Use the other reading**, and the symbols that differ as the same
  chips handwritten cards have (`web/reading_chips.py`, built on `reading_alternatives.from_reading`).
  Ordinary edits (checks, Undo), none preselected, audited as `page_witness_choice`. Nothing shows with
  `FEATURE_PAGE_WITNESS` off. Documented in docs/web.md and docs/steps.md.

#### Glyph checks for equations (branch eq/bag-extended-extent)

- `EQUATION_GLYPH_CHECKS` (off): the glyphs under an equation's box, and for an inline equation read with
  its line the glyphs beside it, are kept with the element (`Element.glyphs`, `core.types.GlyphContext`; read
  by `layout.line_glyphs`, which shares the line crop's region with `crop_line` through `_line_clip`).
- A reading that goes past the box (`EQUATION_LINE_CONTEXT`) is compared with the glyphs it covers, not the
  box's text (`text_layer_compare.extend_text_layer`, `equation_step.reading_layer`): symbols, numbers, case,
  style, signs and chemistry checks, a reviewer's edit (`web.edit_check`), the review screen's compare table
  (`web.compare`) and the eval's "supported" test all use that one layer. A wrong letter in the extended part
  is still a decision.
- New check `equation.glyph_style`, code `glyph_style_mismatch` (meaning-level, a required decision): a letter's
  style in the reading's LaTeX against its glyph's font (italic against upright, bold against bold italic, script,
  double-struck; `text_layer_compare.compare_letter_styles`). New review message and compare-table row.
- `\mathbf` is written `\boldsymbol` for a letter the glyphs say is bold italic (`restyle_bold_italic`, the
  bold-letter fix of running text), including Latin Modern's `LMMathItalic10-Bold`, whose text-layer code point
  says only "bold". Measured by replay of the recorded fresh4 run in docs/eval-equation-glyph-checks.md.
- `scripts/realworld_run.py rs --glyph-checks` turns the setting on for a replay.

#### Page witness (branch eq/page-witness)

- Page witness (off: `FEATURE_PAGE_WITNESS`): one call per maths page to `PAGE_WITNESS_MODEL`
  (`claude-opus-5-5`, output limit 12,288) reads every formula on the page, table cells, captions and
  figure labels included, each anchored in the text layer (`pipeline/page_witness.py`, prompt
  `page_witness.md`; dense pages in windows, a refusal recorded, budget reserved per call).
  `pipeline/witness_reconcile.py` lays it against layout's equations by extent (within one glyph).
  Agreement is a note only: it lifts no confidence and skips no second reading, because on pooled gold
  (7,390 equations) P(correct | agree) was 94.8%, under the 97% needed. A disagreement is a required
  decision with the reading nearer the extent's glyphs first and the other in `EquationFix.other_latex`;
  a formula only the witness read, with maths-glyph evidence and clear of other elements, is a new
  element and a required decision. New fields: `Element.witness_latex`, `witness_status`,
  `EquationFix.other_latex`, `other_source`, `DocumentResult.page_witness`. Documented in docs/steps.md. Not measured (E1 is a later step).

#### The MathML shape for screen readers (branch mathml/screenreader-shape)

- Chemistry MathML is shaped for MathCAT (NVDA, JAWS) at the one place MathML is made from LaTeX
  (`validate.safe_latex_to_mathml`, new `pipeline/chem_mathml.py`): one `mi` per element
  symbol with its count or charge on it, a state as `(` `aq` `)` (MathCAT does not take a four-
  character `mtext` apart, so `(aq)` was read "(aq)"), a nuclide as `mmultiscripts`, a label over
  an arrow directly in the `mover`. A neutron, proton, positron, alpha, beta or gamma with prefix
  numbers, and an excited nuclide, keep the converter's shape (MathCAT has no rule for them); a
  state with a condition stays one `mtext`; the gas and precipitate marks are `↑` `↓`. Only a row with evidence of chemistry is changed: a lone `H`,
  `No`, `Pa` or `\mathrm{d}x` stays as it was.
- `intent=":chemical-formula"` and `":chemical-equation"` on `<math>` for an equation that is all
  chemistry. These are the only `intent` values the allowlists accept (`validate.check_mathml`,
  `safe_html.sanitize_mathml`, the review screen's `web/mathml.py`), only on the root; any other
  value is refused or dropped (`tests/test_chem_mathml.py`). New `core/mathml_intent.py`.
- All maths (new `pipeline/mathml_shape.py`): `^\circ` is the degree sign U+00B0, not the ring
  operator that Speech Rule Engine reads "composed with"; `\overline` and `\underline` use the macron
  and the low line, not U+2015 ("quotation dash"); `\xrightarrow` has no `mstyle` or `mpadded`
  around the arrow (MathCAT failed with "Pattern match/replacement failure" on it, with the new
  symbols). Matrices, cases, limits and sums were already read right by both engines.
- Measured with MathCAT 0.7.7 and Speech Rule Engine 4.1.5 on the recorded dev run of 2026-10-26
  (no model call): of the correct dev outputs that are a formula or reaction on its own, MathCAT
  recognised 136 of 153 before and 153 of 153 now; states read as words (36 of 36 where the gold's
  reading has one, 34 before); speech equal to the gold on the chemistry set 133 to 136, on
  OpenStax algebra 736 to 736 (the gold's own reading is the difference), and Speech Rule Engine
  equal to the gold falls on chemistry (61 to 35 clearspeak, 83 to 45 mathspeak). A symbol has no
  explicit style when `intent` is on (Speech Rule Engine reads `mathvariant="normal"` aloud: "normal"
  in 64 of 318 chemistry readings, 199 with the style always; the standalone file draws it upright
  with `math[intent] mi { text-transform: none }`). Chem scorer unchanged (293 of 443); the
  symbols scorer unchanged at 1,289 after it stopped counting an upright symbol and an `mtext`
  one as different (its drop of 75 equations was that artefact); the primary 827 to 827. Details,
  the four spellings compared and what MathCAT does not read as chemistry:
  [docs/chemistry.md](docs/chemistry.md), "MathML shape".
- Pipeline version 0.5.1 to 0.5.2. Review carry now compares an equation's LaTeX and display, not
  its MathML and reading (`web/carry.py`), so a revised upload keeps its accepted equations when
  only the MathML is made another way; fingerprints (the picture's hash) are unchanged. The step
  cache key holds the version, so a re-converted file asks the model again for equations.
- A bare word of capitals (`ON`, `IN`, `OK`, `PH`, `HIV`, `V_{\text{IN}}`) is not chemistry; the shaping
  needs a count, a charge, a state, a coefficient, a lowercase formula word (`NaCl`), a counted bracket
  group or an arrow with such a species. The EPUB export drops `intent` (epubcheck 5.4.0 reports
  RSC-005 on it; the EPUB validator tests have a chemistry page). The chemistry check's flat text puts
  a space after an arrow's label, so `->(Fe) 2NH3` no longer reads `Fe2NH3`. A carried accept keeps the
  new fix's equation (shape and reading) and copies only the decision; an edit is copied whole. The
  review screen draws `intent` symbols upright (`web/static/app.css`).
- `scripts/dev_converter_effect.py --shape-only`: the effect of this change alone on the scorers.
  `equations_symbols` reads `mmultiscripts` as the gold's prefix scripts and an upright element
  symbol like an `mtext` one; the primary scorer reads `mmultiscripts` as the answer keys' shape.
  The five golden HTML files gain one stylesheet line.

#### Canonical comparator for two readings (branch eq/math-canonical)

- `pipeline/math_canonical.py`: one comparator for "is this the same formula" (tree, notation-neutral,
  spoken-equal, symbols) with a reason when it is not. The registered scorer scripts are untouched;
  tests check the library agrees with them. `tests/test_realworld_registration.py` runs
  `realworld_register.py --check` for every registered set. No product behaviour changes yet
  ([docs/steps.md](docs/steps.md), "Comparing two readings").

#### 

- Equation diagnostics on the recorded real-world runs, no model and no cost: `scripts/eq_diag_tables.py` (why maths in
  table cells is missed), `scripts/eq_diag_glyphs.py` (glyph evidence of undetected and span-wrong equations),
  `scripts/eq_diag_unflagged.py` (why wrong equations with other symbols were not flagged). Counts and sample sizes in
  [docs/eval-equation-diagnostics.md](docs/eval-equation-diagnostics.md). No product behaviour changes.

### Chemistry

#### Chemistry species check against the text layer (branch chem/species-check)

- New check `equation.text_layer_species` (code `species_mismatch`, `pipeline/chem_check.py`): a
  chemistry reading's species (coefficient, formula with counts, charge, state) and arrows are
  compared, in order, with the PDF's text layer. It finds a subscript read as a coefficient, a
  flipped or dropped charge sign, a changed state or arrow, another element or count, a
  neighbour word taken into a formula and a reaction cut mid-way. A failure is a required
  decision like the other meaning-level checks (confidence x 0.6, a cheap answer escalates).
  It runs only on a text layer with chemistry-only evidence, so mathematics is unchanged. A
  reviewer's edit gets it too, and the compare table has "Species" rows. Numbers and the
  replay: [docs/steps.md](docs/steps.md), "Chemistry species check";
  `scripts/chem_check_replay.py`; tests in `tests/test_chem_check.py`.

#### Chemistry spans in the layout (branch chem/layout-spans)

- Chemistry lists are cut into their items: `CO3 2−, F−` is two inline equations and
  `(a) A ⇌ B; (b) C ⇌ D` one inline equation per reaction (without the list marker), not one span
  or one display line. Half-reaction labels (`anode (oxidation):`) stay with their reaction when
  they are set far from it. `E°cell` and `E°cathode` are whole (the degree sign was read inside the
  lowered label and cut the equation in two). A label over or under an arrow, of any length and
  with a long arrow, is part of the reaction (`chem.Word.over_arrow`). On the 143 PDFs of
  `scripts/layout_identity.py` 135 are identical and the 8 that differ are chemistry sections;
  details and the replay in [docs/chemistry.md](docs/chemistry.md) ("Spans") and
  [docs/layout.md](docs/layout.md). Tests: `tests/test_layout_chemistry.py`.

#### `\ce` subset and `\ce` on every LaTeX path (branch chem/ce-subset)

- The `\ce{...}` subset (`pipeline/chem.py`) now reads a condition in a state symbol
  (`(aq, 0.10 M)`), the excited state of a nuclide (`^{60}_{28}Ni^*`) and gas and precipitate marks (`CO2 ^`, `AgCl v`);
  every other construct is still refused.
- `\ce` is accepted on every path that takes LaTeX: the review screen's edit box (equation and
  table cell), scanned pages and their second reading, table cells' HTML and page-math spans, all
  through `equation_step.read_latex`. A `\ce` outside the subset fails the LaTeX check with the new
  code `unsupported_ce` (was `unknown_command`) and the edit box says so in words. See
  [docs/chemistry.md](docs/chemistry.md).

#### Chemistry speech fixes

- The review screen's "read aloud roughly as" text (and the checks that compare readings) now
  reads a state with conditions, `(aq, 0.10 M)`, as "aqueous, 0.10 molar"; a redox couple
  `Cu²⁺/Cu` as "C u 2 plus slash C u" (it said "divided by"); the arrow in its own cell of a
  half-reaction table as "reacts to form" (it said "right arrow" with the species cut off from it);
  an excited nuclide `⁶⁰₂₈Ni*` as "nickel 60, atomic number 28, excited state"; and units after a
  decimal in a chemistry row consistently (`0.000 V − 0.0592 V` both "volts"; `mol L⁻¹ s⁻¹` as one unit).
  Speech for sets that are not chemistry is unchanged (`docs/chemistry.md`, "Reading it aloud").

### Layout and output

#### Layout structure: bookmarks, numbered and run-in headings, joins (2026-11-02, branch fix/layout-structure)

- The PDF's outline (bookmarks) is the first signal for headings when it matches the page text
  (`pipeline/outline.py`): a line that is a bookmark's title becomes a heading at the bookmark's level
  (a tag still wins), the heading takes the bookmark's text, and an outline that does not match, or is
  flat where the type is not, is ignored. dev-v2 heading level accuracy 0.898 to 0.914 (28 documents,
  683 gold headings).
- Plain body-size numbered lines in sequence (`3 Forces`, `4 Energy`, with a paragraph between) are
  headings, as weak evidence; one alone stays a paragraph. Repeated bold run-in labels (`Solution`,
  `Answer`, `Discussion`, `Procedure`, `Proof`, `Hint`) set in from their column's edge are bold text,
  not headings: original gold set heading precision 0.850 to 0.914 at the same recall.
  A paragraph that is a bookmark's title becomes a heading only if it is short, has no sentence end and is
  bold, larger or set off by space; the plain ones are weak evidence (an optional note), and an outline
  that bookmarks most of a document's paragraphs promotes none. Numbered exercises (`1 Find the final
  speed`) are never headings; a numbered sequence that does not start at 1 or 2, or has a long title, is
  a decision (new check text in `web/messages.py`).
- Math-italic and bold variable letters in a heading or a caption's plain text are plain letters; the
  number sets and letterlike symbols stay.
- A paragraph (not a list item) that goes on over a page break is one element (`Element.last_page` is its
  last page, and the page markers of the pages it runs over are written after it); inline maths that wraps at a relation
  sign is one equation; at a line break a hyphenated compound follows the document's own spelling
  (`eigen-vector` written elsewhere keeps its hyphen, `antiderivative` written whole elsewhere loses
  it). Details and what stays open: [docs/layout.md](docs/layout.md), "Known failure modes".
- Pipeline version 0.5.0 to 0.5.1 (ids shift after a join; review carry matches by content, so revised
  uploads still carry what is unchanged).
- Offline heading, document and structure evals before and after: [docs/eval.md](docs/eval.md),
  "Structure fixes, 2026-11-02".

- Tables, structure (`pipeline/tables.py`; [docs/tables.md](docs/tables.md), "Known misses"): the rest of an unruled
  table that a page break carried over is found by its repeated header and joined (a split row is merged, the
  repeated header dropped); a booktabs table with text in only one column is kept; an unruled box that holds two
  tables is split at a caption or a clear gap; a booktabs table read without the model file (a fallback: production has it, so production
  grids are unchanged) no longer gets a column for a cell with a gap in it. A new "Table N" caption over a repeated
  header is a new table, not a continuation. Maths in cells is unchanged: it needs the equation step's model calls.
- Local table eval, gold (45 documents, 197 gold tables): with the model file TEDS 0.941 and detection recall and
  precision 0.985 and 0.985, unchanged; without it TEDS 0.912 to 0.928. The continued-table, sparse-table and split-box fixes change nothing on any PDF
  of the repository's fixtures, the three real-document sets and the eval's prints (128 PDFs, 245 tables); the
  fixes are pinned by generated pages. Real-document sets unchanged (25, 39 and 58 labelled tables, precision 1.000).
  `make fuzz-tables`: 300 of 300 mutants ok, as before.

#### Navigation in the output, and combining the parts of a long source (branch feat/output-navigation)

- The converted file now has a skip link, a contents list (`<nav aria-label="Contents">`, nested links to the
  headings, from 3 headings, and not when the PDF has a contents page of its own), page markers at each source
  page boundary (`role="doc-pagebreak"`, "Page N": the PDF's page label, else the number printed in the margin;
  a page with neither says "PDF page N" when other pages have a label, "Page N" when none does) and footnote links both ways (`doc-noteref`, `doc-footnote`, `doc-backlink`)
  (`pipeline/navigation.py`, `pipeline/page_labels.py`; docs/output.md "Navigation"). Every heading has an id.
  Every converted file changes by the skip link; golden files regenerated.
- Canvas page to paste: the skip link is left out (its target, `<main>`, is removed by Canvas); the contents list,
  markers and note links are kept. The README in the zip says so. The Common Cartridge page is the zip's
  `index.html`, unchanged.
- axe in `html_checks.axe_checks` also runs the `wcag22aa` tag; no output fixture failed it.
- "Combine parts": one HTML file from a person's own finished, still-kept parts of the same long source, in page
  order, with one contents list, continuous page markers and ids that cannot clash; parts with a decision open are held
  back, overlapping parts keep the newest, and the download restarts the parts' clocks like "Download all"
  (`GET /jobs/{id}/combine`,
  `pipeline/combine.py`, `web/combine.py`; docs/output.md "Combining the parts of a long source"). A part cut from a
  long PDF keeps its pages' labels.
- The committed demo (`web/static/demo/index.html`) was not regenerated: that needs a paid conversion run. Its
  golden test renders the stored result and was regenerated.
- `eval-live.yml` runs weekly (Monday 06:17 UTC) and by hand, for the production provider (Claude):
  equations, headings, figures, document and tables, each with its own hard `--max-usd` (total at most
  $2.95 a week, about $0.70 expected), each report gated with `eval gate --require-current` against its
  committed baseline (tables: floors), reports uploaded as artifacts and never committed. It needs the
  secret `ANTHROPIC_API_KEY` (was `OPENAI_API_KEY`, equations only); a scheduled run without it skips
  with a notice. Chromium fonts are installed as in `ci.yml`.
- `eval-guard.yml` and `scripts/eval_guard.py`: a pull request that changes a model constant in
  `config.py` or a prompt under `models/prompts/` fails without the matching `data/eval/latest-*.json`.
  Opt-out: label `eval-unchanged` or commit trailer `Eval-Unchanged: <reason>`.
- Docs: `docs/eval.md` says which pipeline version and date made each committed report (0.5.0, Claude,
  2026-10-14 and 2026-10-21; it said 0.4.0) and tells `PIPELINE_VERSION` (`pipeline/run.py`, what reports
  record) from the package `__version__` (0.1.0); `tests/test_eval_docs.py` keeps the table true. The 0.4.0
  results sections are labelled as historical and are not rewritten.

### Scans and handwriting

#### Symbol inventory (branch hw/symbol-inventory)

- Symbols screen (`GET/POST /jobs/{id}/symbols`, `web/inventory.py`, `routes/inventory.py`): for a document
  with handwritten equations, every symbol read in them grouped with the ones that look alike, with counts,
  small pictures and places; one symbol can be changed to another of its family everywhere at once, with
  checkboxes to leave places out. Applied as one write through the edit checks per equation, never an accept
  (each equation stays a required decision), one `relabel` review record per equation shown as one row in the
  review record, one Undo. Linked from the review screen and the list view. docs/web.md, docs/eval-handwriting.md.

#### Pen strokes check handwritten vector pages (branch hw/stroke-checks)

- `pipeline/image_page_vector.py` (new): on a page `page_type` calls handwritten and `ink_strokes`
  analysed, the image page pass now (1) draws the picture of each display equation, prose block
  and left-out stretch from the strokes alone (300 dpi, black on white, cut to the ink) for the
  model, the review screen and the student file; (2) checks each equation's reading against the
  strokes: a letter in the other case (c o s u v w x z), a script lost, added or moved; (3)
  compares the page's stroke groups with the blocks' boxes, so ink the reading left out becomes
  the existing omission decision, including pale ink. Checks add failed warnings
  (`image_page.case_check`, `image_page.script_check`; sentences in `web/messages.py`) to the
  equation's fix and never change its required decision. Pages with no strokes, and every scan,
  are read as before.
- Settings `IMAGE_PAGES_VECTOR_CROPS` and `IMAGE_PAGES_VECTOR_CHECKS`, `VECTOR_CROPS` off until measured, `VECTOR_CHECKS` on. `layout.page_ink`
  reads a page's strokes under the document lock; `run.recrop` can take the pages' kinds so a stored
  vector page is drawn the way it was converted.
- `scripts/handwriting_vector_checks.py` measures the checks on built vector sets at no cost
  (put-in errors in the gold readings). Held-out (318 expressions): raised scripts taken out are
  named 27 of 65 times at 12 of 172 false alarms, a case flip 21 of 37 at 6 of 25, a dropped
  block 314 of 318 at 0 of 54 pages (the raster check: 290 of 318 at 11 of 54, and 0 on pale ink).
  Not measured: real iPad ink, or the reader on the drawn pictures (docs/eval-handwriting.md,
  "Checks from strokes").

#### A second reader's reading and choices on the review card (branch hw/reconcile-and-chips)

- `pipeline/image_page_reconcile.py`: with `IMAGE_PAGES_SECOND_READER` set (`local`, `claude` or
  `openai`; default `off`, and the reader must also be enabled), the crop of each handwritten display
  equation is read by that reader and compared with the page's reading. Agreement is a note and the
  item stays a required decision; a difference is a note and, where it is a few symbols, fills
  `Element.alternatives`; an unavailable reader, a spent time share (`IMAGE_PAGES_SECOND_READER_TIME_S`)
  or a spent reader budget leaves the item as it was and adds one notice for the document
  (`second_reader_unavailable`, `second_reader_budget`) instead of a line on every equation. A read
  is not started unless it fits the share, which never outlasts the job's own time limit (it now
  reaches the child). The reader's own spend limit is never a failed fix, so it cannot stop the job.
  In the hosted conversion child only the local reader can run: a production start is refused with
  `claude` or `openai`, or with `local` and no model files (also in `ops launch-check`). The child's
  counts come back in `ImagePagesSummary.second_reader` and the worker logs `second_reader_job`.
- Image-page equations and tables are explicitly never part of a bulk accept.
- A chosen reading is recorded in the review record as a choice.
- Review card: a handwritten equation whose second reading differed shows one-click choices
  ("Use capital X") under the proposal; each posts an ordinary edit (checks, record, Undo), none is
  chosen for the person, and nothing changes while there are no alternatives. Keyboard, 44 px targets,
  forced-colors and axe are tested.
- The two reader prompts moved under `models/prompts/` (`math_reader.md`, `math_reader_claude.md`),
  text unchanged, so the eval guard sees them (mapped to `latest-image-pages.json`).
- New check messages for the agree and differ notes (`image_page.second_reader`) and two notice messages.
- Not measured: how often the choices contain the right reading, or whether decisions per page fall
  (docs/eval-handwriting.md).

#### A local handwriting reader (branch hw/local-reader)

- New reader B, `local` (`models/math_readers/local_reader.py`): TexTeller on CPU with ONNX
  Runtime, no key, no network, nothing leaves the server, so it is not a recipient and the
  privacy page and production check are unchanged. Off (`MATH_READER_LOCAL_ENABLED=false`),
  not called by the pipeline, built by `build_readers` like the Claude and OpenAI readers.
  Settings: `MATH_READER_LOCAL_DIR`, `_THREADS` (2), `_TIME_S` (20, a read past it returns no
  candidates with the outcome `timeout`).
- Model files by `python -m readablestem.models.math_readers.local_weights` and, in the image,
  by `--build-arg INSTALL_MATH_READER=true` (default off): five files at a pinned revision, each
  refused unless its SHA-256 matches, the three ONNX files rewritten with int8 weights (0.6 GB
  instead of 2.1 GB; about 1.1 GB resident in the conversion child, address space 2.1 GB under
  the 3 GB cap). Measured on 74 expressions: 1.07 s a crop on two threads, exact 0.554 (float32
  0.649); as a flag against the page pass it catches 18 of 21 wrong readings with 15 right ones
  flagged (the page pass's required review: 10 and 17). The weights are Apache-2.0; the training
  data and the practice set overlap in ways `docs/math-readers.md` lists.

#### Page type reads per-sample pen ink (branch hw/page-type-dotted-ink)

- `page_type` now calls vector handwriting drawn as one small filled disc per pen sample
  `handwritten` (it was `vector_no_text`). Its ink analysis uses `ink_strokes`'s own rule for a
  stroke made of samples (`sample_run_end`, `dot_grid_members`) instead of a second one. Dot-grid
  paper, scatter-plot markers and stippled shading are still not handwriting. On the 846-page
  labelled corpus no page changes its kind. Documented in docs/layout.md.

#### Other readings of a formula as choices on its symbols (branch hw/schema-prompt)

- `Element.alternatives` (`ReadingAlternatives`, `SymbolAlternative` in `core/types.py`): the doubtful
  symbols of a reading as spans of the stored LaTeX, each with other LaTeX it may be, for a review to
  offer as choices. Optional and empty everywhere today; results stored before it load as they were.
- `pipeline/reading_alternatives.py` makes it from any other reading of the same expression
  (`from_reading`, for a second reader) or from named doubts (`resolve_doubts`): spans are found in
  the LaTeX as stored, an option is kept only when it converts, at most 3 doubts of 3 options.
- Eval guard: the image page prompts, `IMAGE_PAGES_MODEL` and `ANTHROPIC_EFFORT_IMAGE_PAGES` need
  `data/eval/latest-image-pages.json` (the document task never calls the image page pass). No such
  report is committed yet.
- No prompt changed. A prompt that made the page reader list its own doubts was measured and not
  shipped: on 74 expressions it read 44 exactly against 49 for the current prompt, 50 carried
  alternatives, and the right reading was one choice away for 7 of 18 one-symbol errors. docs/eval-handwriting.md,
  "Doubtful symbols: tried, not shipped".

#### A vector-ink handwriting dev set (branch hw/vector-devset)

- `scripts/handwriting_accuracy.py build-vector` and `eval/handwriting_vector.py` build a dev set of
  MathWriting expressions drawn into PDFs as vector ink (stroked paths, filled per-sample shapes,
  filled pen outlines), on plain and ruled paper, some with a typed header, with gold LaTeX per
  expression and a manifest with a rebuild hash. The pages stay in `~/.cache/readablestem`; the
  ink is CC BY-NC-SA. No model is called. Documented in docs/eval-handwriting.md.

#### Handwriting error classifier (branch hw/error-analysis)

- `scripts/handwriting_errors.py` classifies every handwriting error in the recorded answers: kind
  (case, Greek against Latin, other letter, digit against letter, `z` against `2`, `x` against `×`,
  minus against dash, lost or added script, fraction, root, structure, left out), edit distance in
  symbols, and whether the product's speech code reads the answer and the label alike. Text table
  plus JSON with examples per arm; tests in `tests/test_handwriting_errors.py`; usage in
  docs/eval-handwriting.md. It calls no model and changes no product behaviour.

#### Vector ink to strokes (branch hw/ink-strokes)

- New `pipeline/ink_strokes.py`: reads a PDF page's vector ink into ordered strokes (stroked
  paths, one filled shape per pen sample, filled outlines), keeps colour and paint order, drops
  page furniture (ruled and squared paper, dot grids, dashed lines, frames, backgrounds), groups
  strokes into lines and regions, and writes each region as normalised point lists or InkML.
  It uses `page_type`'s caps and `InkBudget` and groups with a grid hash (near linear), so a
  hostile page cannot stall a worker. Nothing in the pipeline calls it yet, so conversions are
  unchanged. What is read and the limits:
  [docs/layout.md](docs/layout.md), "Vector ink and strokes" (`tests/test_ink_strokes.py`).

#### Text layers that do not cover the page, and the maths rule as a guard (branch fix/handwriting-skip-and-flags)

- A visible text layer no longer makes a page "typed" when it does not cover the page's content
  (`page_type.TEXT_NOT_COVERING`, classifier page-type-v5.5). A watermark, a header, a stamp or a page
  number over a page-sized picture (a photographed notebook page) made the page typed: it skipped the image page pass and came out "Not converted"
  with only that text. Three of five real photographed pages hit it. Such a page is now scanned or
  handwritten and read by the pass when asked; its visible text is still read when the pass is off, and
  the pass's reading replaces it when on (`page_type.keeps_text`, `layout._analyze_document`,
  `run.convert_pdf`). A background picture repeated on the pages, a plain one, or one with nothing on
  it is not content. Details, thresholds and what was measured: docs/layout.md, "Hidden text and page
  types"; ADR 0007 point 13. `tests/test_page_type_text_coverage.py`.
- `scripts/page_type_corpus_compare.py` compares the classifier with and without these rules on a
  labelled corpus (0 pages worse of 846 on the author's corpus; a test runs it when the corpus is there).
- Production refuses `FEATURE_IMAGE_PAGES` or `IMAGE_PAGES_ACCEPT_AGREED` with
  `IMAGE_PAGES_MATHS_ALWAYS_REQUIRED=false` (`Settings.check_production`).
- The review card of a page that was tried from an image and stayed an image says why (`reading_note`).
- `image_page.enforce_maths_required` runs after every step: any equation or table read from an image
  whose governing decision is not a required one is lifted to one (a no-op when every path kept
  ADR 0007 point 12). A practice-set run that seemed to show 14 of 25 and 16 of 30 wrong readings not
  flagged counted an inline formula's own `review`; counted by its paragraph's decision, as the eval
  does, all were asked about (replayed at $0: docs/eval-handwriting.md, "Note, 2026-11-03").
  `tests/test_image_page_maths_required.py`.
- Copy: `limits.html`, README, docs/vendor-information.md (list in the pull request).

#### A kit for collecting hand-copied OpenStax examples (branch hw/copy-set-kit)

- `scripts/handwriting_copy_set.py` and `data/handwriting-copy/README.md`: a kit for a small set of
  real iPad handwriting whose answer key nobody types. It draws OpenStax worked examples with a
  written, seeded rule behind a CC BY licence gate (the committed table, then the live archive and CMS
  records), writes each volunteer a brief, matches the PDFs they return to passages (counts only in
  reports; a folder inside the repository is refused), builds the gold from the passage's published
  MathML, and writes a manifest in which a page is published only if its writer's yes is recorded; the
  other pages are counts. A person must look at every page (`review`) before it can be published; published PDFs are rebuilt with page content only and checked, failing closed; unpublished pages can be purged. Tests in
  `tests/test_handwriting_copy_set.py`; no page has been collected yet, so there is no result.

#### Reader B adapters for handwritten expressions (branch hw/math-readers)

- `models/math_readers/`: one small `MathReader` protocol (a PNG crop in, LaTeX candidates with
  reported confidences out) and two adapters, a Claude reader built to differ from the page pass
  (another model, its own instructions, one high-resolution crop, no page text) and an OpenAI
  vision reader (`store: false`). Worst-case reservation before every send, timeout, retries,
  refusal and error mapping as in the step clients, no content in logs. Settings
  `MATH_READER_CLAUDE_*` and `MATH_READER_OPENAI_*`, all off; nothing in the pipeline calls them
  yet (`docs/math-readers.md`).
- One list of who receives what (`web/recipients.py`) now writes the privacy page (its provider
  items, "Where it is processed" and the account deletion notes), so a reader's company is named as
  soon as the reader is on, whichever `MODEL_PROVIDER` is set. A production start is refused while a
  reader is on and the rendered page does not name every company that receives pictures; the
  launch checks (model provider, analytics, tables, notation, Microsoft, sign-up) now read the
  rendered page, not the template source. `MATH_READER_MAX_USD_PER_JOB` caps all readers of a job
  together. The vendor sheet and `docs/security.md` are updated to match.

#### Handwriting harness for private handwritten notes

- `scripts/handwriting_own.py`: intake of PDFs from a folder outside the repo, a hash-based dev and
  held-out split registered privately, a blank answer-key form served on 127.0.0.1
  (`scripts/handwriting_gold_form.html`, no model draft) and a `run` command capped by `--max-usd`
  (at most 2.00 in total across restarts). `run` sends the PDFs to the model provider. Every command
  refuses a path inside a git repository; `.gitignore` guards the usual private folder names.
  Use: [docs/eval-handwriting.md](docs/eval-handwriting.md), "Your own handwritten notes".

### LaTeX source, slides and batch upload

#### Which Landlock step failed (branch latex/probe-step-codes)

- New: when the Landlock launcher fails to start, it reports the failing setup step as a fixed
  code and the errno's name; `ops sandbox-probe`, the `sandbox_probe` log line and
  `/healthz/ops` show `landlock_step=`, `landlock_errno=`, `landlock_exit=` and
  `can_<call>=yes|no` for each system call the launcher needs, tried once in a throwaway child
  under the container's own seccomp filter. Information only: no change to what the sandbox
  allows.

#### Landlock sandbox backend (branch latex/landlock-sandbox)

- New: a second sandbox for LaTeX source where bubblewrap cannot start (no user namespaces,
  as on Railway): Landlock (read and execute on the system trees, read on the input, read and
  write on the output and a private scratch folder, nothing else, not `/proc`) with a strict
  seccomp filter (no sockets, no `setsid`, no `landlock_*`), `no_new_privs`, rlimits, the
  one-at-a-time lock, a polled memory cap for the whole process group, and a launcher that is
  the subreaper and dies with its caller. Chosen automatically; the same probe gates it
  (`backend=bwrap|landlock`, new in-sandbox checks). The web process and the conversion child
  call `PR_SET_DUMPABLE=0`. `tests/test_source_escapes.py` runs on both backends.
- Hardened after review: Landlock ABI 6 is required (signal and abstract-socket scopes); the
  strict filter also denies calls that name another process of the user (`prlimit64` and the
  scheduler calls except on pid 0, `setpriority`, `ioprio_set`, pidfds, `kcmp`), memory and disk
  outside the counters (`memfd_create`, System V and POSIX IPC, `execveat` of an open file), and
  changes to file mode, owner, times and extended attributes; unlinked-but-open files count
  against the output budget and the entries in the writable folders are capped; the task bound
  is 4 x 512 MB and the probe fails over `SOURCE_SANDBOX_MAX_TOTAL_MB`; the memory poll is
  every 0.1 s; the supervisor watches its caller's pid instead of `PR_SET_PDEATHSIG`.
- Final review: `source_busy` is decided by the launcher's own observation (the user's task
  count at the limit while the program has not used its allowance, written to a marker the
  program cannot reach), no longer from the program's output, which a document could spoof.
- Second review: `fallocate` denied, the output budget counts blocks and deleted files that are
  still mapped, a fork that fails for lack of room fails the conversion as `source_busy`
  (new message: "The server is busy right now ... Try again in a few minutes, or upload the
  PDF instead."); running a written file through the dynamic loader is documented as a
  residual (the loader's execute right cannot be withheld).

#### Sandbox probe at start-up (branch latex/probe-at-startup)

- New: when the web process starts and LaTeXML is in the image it runs the sandbox probe
  (`--require-latexml`) in the background, logs `sandbox_probe result=PASS|FAIL ...` once, and
  shows the result on `/healthz/ops` under `sandbox` (state, checks, bound_mb, cgroup, seccomp,
  checked_at; `not_installed` when there is no LaTeXML). `FEATURE_LATEX_SOURCE`,
  `FEATURE_BEAMER_SOURCE` and a `FEATURE_BATCH_ZIP` zip with a LaTeX project take effect only
  while the last probe passed: otherwise the existing 503 `source_unavailable` answer. Settings
  `SANDBOX_PROBE_AT_STARTUP` and `SANDBOX_PROBE_TIMEOUT_S`.

#### Zip intake and tiered batch limits (branch batch/b2-zip-intake)

- A `.zip` of documents is a batch (`FEATURE_BATCH_ZIP`, off by default, `web/batch_intake.py`): each PDF and each
  folder with a main `.tex` file (a LaTeX project, re-zipped and checked again by the LaTeX source path) becomes a
  document of its own through the ordinary upload checks; a zip with a main file at its root is one project; strays
  are left out; a zip with no document, a link, a bad path, a nested zip, EPS or SVG, or over the limits (the LaTeX
  source's, with 80 MB unpacked) is refused. The zip counts once for the upload rate limit.
- Memory: the unpacked budget of a batch is one per request (twice the upload limit, shared by every zip in it, not
  80 MB per zip), the documents of a zip are read one at a time when their turn comes, a PDF over the upload limit
  is refused by its header unread, and documents past the person's limit are named, never read (a zip met after
  the limit is refused unopened). `batch_intake.split` returns lazy `Member`s.
- Limits by tier: one upload takes 30 documents for invited people and pilots and 10 for an account made by open
  sign-up (`access.files_per_upload`), and the same number is the cap on documents waiting or converting at once
  (it was 10 for everyone). The upload form and the messages say the number for the person.
- The batch page `/app/batch/{id}` (`routes/batch.py`) replaces the results page of an upload of several files
  (`/app/upload/{id}` redirects to it): a row per document with its status now, a polite live summary line, polling
  with 204 while nothing changed, "Download all" (`/app/batch/{id}/download`) once nothing is waiting, the large
  files and the refusals as before. `documents.documents` and `list_version` take a list of job ids.
- Tests: `tests/test_batch_intake.py`, `tests/test_web_batch.py`, the batch page in `tests/test_web_jobs_browser.py`
  (axe at 1280 and 375); the tests that fixed 10 now use the tier.

#### Beamer source (branch latex/e2-beamer-source)

- Beamer decks within LaTeX source (`FEATURE_BEAMER_SOURCE`, off by default, needs `FEATURE_LATEX_SOURCE`): frames
  become headings with their content (sections the top level, frames below them), an overlay step is written only
  when it shows something the frame's last step does not ("(step 2 of 3)"; the rule of the PDF slide decks), text a
  step covers is not written, a list item a pause reveals is an item of its own, a title page drawn by Beamer is read
  for its blocks. `source/beamer_compat.tex` gives LaTeXML 0.8.8's binding the block, column and `\institute`
  definitions it lacks (read first, read-only in the sandbox). A deck counts a page per frame. Spike on five decks
  written for it: 20 frames, none with an unknown command (3 had one without the compat file); no
  real deck tried. `DocumentResult.origin` `beamer`, the student note says "a slide deck". Tests:
  `tests/test_source_beamer.py`; fixtures `tests/fixtures/source/beamer/`.

#### LaTeX source upload (branch latex/e1-source-intake)

- LaTeX source (`FEATURE_LATEX_SOURCE`, off by default; ADR 0008): a `.tex` file or a `.zip` of a project is
  converted by LaTeXML in the bubblewrap sandbox of the sandbox probe change, into the document a PDF becomes. Equations
  come from the source (LaTeXML's MathML through the sanitiser, the source's own LaTeX), figures use the
  project's pictures and the ordinary figure step, tables become grids, headings, lists and links are read as
  written. A formula LaTeXML cannot read and a figure with no picture are required decisions; a source that cannot be
  converted fails the job with "Upload the PDF instead" and keeps nothing. The review is the list view. New
  `web/archive_intake.py` (zip limits: 20 MB, 100 MB unpacked, 500 entries, no links, absolute or `..` paths,
  nested zips, EPS or SVG; a header that lies is caught), `source/` (`intake`, `lint`, `envelope`, `latexml`,
  `ltx_reader`, `figures`, `convert`), a one-line hook in `pipeline/run.py` (`convert_pdf`), `DocumentResult.origin`,
  `render_html(origin=)` (the student note says "a LaTeX document"), `INSTALL_LATEXML` and `FEATURE_LATEX_SOURCE` in
  `railway.ts`. LaTeXML runs with `kpsewhich` hidden in the sandbox: where TeX Live's base is installed (the image) it
  otherwise read raw TeX for packages and spent over a minute failing on `siunitx`. Beamer decks are refused until their own change. Docs: docs/web.md "LaTeX source",
  docs/security.md "Running uploaded TeX", docs/development.md. Tests: `tests/test_archive_intake.py`,
  `test_source_intake.py`, `test_source_reader.py`, `test_source_convert.py`, `test_source_escapes.py` (hostile
  files through the real sandbox), `test_source_child.py`, `test_web_source.py`.

#### Sandbox probe (branch latex/x0-sandbox-probe)

- `readablestem ops sandbox-probe`: checks on the running image that the sandbox for uploaded TeX
  works (bubblewrap, user namespaces, no network, empty environment, hidden `/proc/1/environ`,
  memory, wall-clock and output caps), times a tiny LaTeXML run when LaTeXML is installed, reports
  the kernel's seccomp mode, Landlock ABI and same-UID `/proc/<pid>/environ` readability, and
  prints PASS or FAIL (exit 1). Nothing it prints is document content. Docs: docs/operations.md,
  "Sandbox probe"; docs/security.md, "Running uploaded TeX".
- The sandbox joins its cgroup before bubblewrap starts (a wrapper, not a move afterwards, so no child escapes it),
  and without a cgroup the total is bounded by arithmetic: LaTeXML gets 4 tasks (it needs 3) and 512 MB of address
  space each (`source.latexml.LIMITS`), one source conversion runs at a time on the machine (`sandbox.exclusive`),
  and `ops sandbox-probe` prints `sandbox_total_bound_mb` and fails `--require-latexml` over
  `SOURCE_SANDBOX_MAX_TOTAL_MB` (2048). The upload comment now assumes a 2 GB web service.
- The sandbox also caps tasks and open files (`prlimit --nproc --nofile`), puts a seccomp filter in front of the
  program (`core/seccomp.py`, built without libseccomp), counts the program's own output against the output budget,
  and uses a cgroup v2 memory and pids limit for the whole group when the container allows one; the probe reports
  `sandbox_task_cap`, `sandbox_file_cap`, `sandbox_seccomp` and `sandbox_cgroup`. docs/security.md says what is
  capped per process and what in total.
- New `core/sandbox.py` (the bubblewrap runner the probe tests and a later LaTeX upload uses) and
  `source/latexml.py` (the `latexml` and `latexmlpost` calls). Nothing in the app calls them yet.
  Tests: `tests/test_sandbox.py`, `tests/test_ops_sandbox_probe.py`.

#### LaTeXML in the image (branch latex/x1-latexml-image)

- Dockerfile: `ARG INSTALL_LATEXML=false` also installs `bubblewrap` and `util-linux` (the sandbox and `prlimit`; the
  build fails without them). It installs the distribution's `latexml` (0.8.8) with TeX
  Live's base and ImageMagick, and replaces ImageMagick's `policy.xml` with a deny-by-default one
  (PNG, JPEG, GIF only; PS, EPS, PDF, XPS, URL and `@file` denied by name:
  `src/readablestem/source/imagemagick-policy.xml`). Measured on the pinned base: +270 MB
  (324.3 MB to 594.4 MB). Off by default; nothing in the app uses it yet. `INSTALL_LATEXML` is
  declared on `web` in `.railway/railway.ts`; CI builds once with the argument and checks the
  versions and the policy. Docs: docs/deploy-railway.md, "LaTeXML"; THIRD_PARTY_NOTICES.md.
  Tests: `tests/test_latexml_image.py`.

#### Slide decks (branch layout/beamer-slides)

- Slide decks (`pipeline/slides.py`, docs/layout.md "Slide decks"): a Beamer file, or a slide-shaped
  file whose pages share labels, is read frame by frame. An overlay step whose every line and
  picture is also on the frame's last page is not read at all (no crop, no model call, not in the
  output); a step that differs (`\only`) stays and its heading says "(step 1 of 2)". Frame titles
  are headings again (they were dropped as running headers), a theme's headline text and bar and a
  lone slide number are dropped, two-column frames are read left then right, and a bullet that
  wraps onto a line of maths ("... zero at" / "x = 0.") is not a display equation. On the Beamer
  fixtures: `beamer_overlay` 6 pages to 2, the new `slides_steps` 13 to 6; no line of a last step is lost.
- Fix (every PDF): a ligature's characters (`ff`, `fi`, `ffi`) come out in order. MuPDF boxes the second
  letter with no width, and everything that sorts by position put it after the next letter ("Stifnf ess",
  "Ofcfi e", "Specifci"). Found on the repo's PDFs: table cells of five files.
- Fix: a PDF page label that the PDF writes as UTF-16 (xdvipdfmx, so every Tectonic deck) is read
  (`page_labels.pdf_label`); it was refused as "<FEFF0031>" and the page markers fell back to the
  footline number.
- Tests: `tests/test_layout_slides.py` and `tests/fixtures/layout/latex/slides_steps.tex` with its
  PDF; six of the strict xfails of `tests/test_layout_beamer_math.py` now pass (one test was reworded: a frame's title is a heading once).
- Pipeline version 0.5.2 to 0.5.3: deck output and ligature text change; results from 0.5.2 load
  unchanged.

### Web app, sharing and exports

#### Upload, job page and My documents (branch ux/upload-job-documents)

- A person a document was shared with who can only view it is no longer told "N decisions need
  you": the status says the decisions are still open and only the owner or a reviewer can make
  them (a reviewer is told they can review them). The results tile reads "Decisions made" and,
  for a viewer, "Decisions still open". In My documents a document whose owner has no name is
  "shared with you" instead of "shared by The owner".
- "Try again" says what it will do ("puts your document back in the queue"); the confirmation
  comes after it worked.
- Times in My documents stay in the reader's time zone after the list refreshes itself.
- One wording for keeping a document, "Keep for 7 days", on the upload form, job page, Account
  page, notices and the privacy page. The job page shows the deletion time once, under the
  review and download actions, with the day "Keep for 7 days" would delete it. Delete entry
  points (a document, a course, an account, a waiting file) have a red outline style
  (`button-danger-outline`, `danger-link`), the confirmation pages keep the filled button.
- Upload form: shorter, in three groups (file, how to convert it, how long to keep it) with one
  line of consequence beside each option and the long text in disclosures. A refused scanned PDF
  says to tick "Convert scanned or handwritten pages" and choose the file again.
- Choosing pages: sizes under 0.1 MB show in KB (`pending.size_text`), the range examples follow
  the active page limit and the file, and a suggestion cut off at 20 parts says which pages it
  leaves out.
- My documents has a compact "Practise on the sample" link beside "Convert a PDF"; signed-in
  pages get a compact footer of trust and help links (public pages keep the full footer).

#### Sharing, guides, demo and status polish (branch ux/sharing-guides-demo)

- The "About this demo" note has its own light-scheme, visited, hover, focus and forced-colour
  link colours (it is a white box in every scheme; its links measured 2.39:1 in the dark scheme).
  The generator (`eval/render.py`, `DEMO_NOTE_CSS`) and the committed demo carry the same rules; the
  student output is unchanged. `/demo` is now axe-checked in the dark scheme
  (`tests/test_web_demo_dark_browser.py`).
- Sharing page: View and Review are whole-row choices (44 px or more, native radios, each with
  what it allows); a "Copy link" button with a spoken confirmation (the address stays a
  selectable field); the document's deletion time and a link to keep it longer beside the address;
  sign-in wording from the enabled providers; "the same address, letter case aside" instead of "exactly
  as written"; "Remove" is a danger button.
- The comparison guide shows, under 52rem, one collapsed native disclosure per topic (ReadableSTEM's answer in the summary; opened, every tool with its source and date); the
  table stays for wide screens. ADA, comparison and posting guides get the offices page's "On this
  page" contents (`templates/_contents.html`) and stable heading ids.
- Status page: "Checked at ...", a "Check again" link and what the 10-minute AI service
  observation means.

#### Review round fixes (branch ux/review-round-fixes)

- Bulk accept: the button that accepts comes after the list of originals and proposals, which now asks you to compare each proposal with the image of the original; Cancel and Undo are unchanged.
- Review split: on a wide screen the row of choices (Accept, Keep as image) sticks to the bottom of the window while the decision is longer than the window; on a phone the page has a "Back to the decision" link and says it scrolls sideways, with edge shadows where more of it is. The toolbar above the page image is `.page-bar` (it shared `.page-tools` with the "Change page" disclosure).
- "Hear this" on an equation with no usable stored reading speaks it from its MathML, as the transcript does.
- Review list: with more than three waiting in a group each is a compact row (what it is, page, status); one editor is open at a time when a row is opened by hand; the first, and any just worked on, starts open.
- Symbols: beside "Change all" a line says how many places it changes and how many are left as they are, and follows the places ticked to be left out.
- Listening: a compact transport (Play, Pause, Stop, previous and next, next heading and equation, Start at the lesson, Follow along) stays in view while the transcript scrolls; Follow along is on by default, is remembered in the browser and scrolls without animation under reduced motion; speed, voice and shortcuts are in a disclosure; a "Skip to the lesson" link works without scripts. Start at the lesson skips the title, language, notices and licence.
- Code: `controls.css` holds the buttons for the app, the listening player and the showcase (listen.css no longer repeats them); spacing and type sizes in `app.css` are on the tokens; the unavailable sans weight 600 is 700.

#### Listen as a student (branch preview/listen-as-student)

- New: an instructor can hear roughly what a screen reader will say for a converted document
  before sharing it. `GET /jobs/{id}/listen` (owners and anyone it is shared with) and
  `GET /demo/listen` (public) show a transcript built from the final HTML, in reading order
  (landmarks, headings with their level, lists, links, equations spoken by the same speech rules
  as the review screen, figures, tables with their headers, pictures kept as images, language
  changes; never LaTeX), with an optional Play, Pause, Stop, speed, previous and next item, next
  heading and next equation, using the browser's own voice (a voice on the device by default;
  online voices only on request). The job page, the review screen (a "Hear this" button
  beside an equation plays the proposal and how the PDF's text layer reads), the landing hero,
  the demo, the showcase examples and `/help/posting` offer it. Not part of any download.
  See docs/web.md, "Listen as a student".

#### Higher upload limits (branch limits/raise-upload-limits)

- The limits now fit together. Before, a new account could convert 20 pages a day while one upload
  could hold 30. Defaults: `MAX_PAGES_PER_JOB` 30 to 100, `MAX_UPLOAD_MB` 20 to 50,
  `MAX_LARGE_UPLOAD_MB` 30 to 100 (also the most one upload request carries, files together),
  `MAX_SOURCE_PAGES` 300 to 500, `OPEN_TIER_DAILY_PAGE_QUOTA` 20 to 150, `MAX_USD_PER_JOB` 2.00 (OpenAI)
  and 3.00 (Claude) to 6.00 for both, `MAX_MODEL_CALLS_PER_JOB` 400 to 1200 (our densest 100-page test
  document makes 652 calls). Not changed: `DAILY_SPEND_CAP_USD`, `OPEN_TIER_DAILY_SPEND_CAP_USD`,
  `IMAGE_PAGES_*` caps, `CONVERSION_MEMORY_MB` and the job time limit (a 100-page document converts well
  inside both: 31 s and 494 MB resident with fake models on one CPU, 193 s with 2 s of simulated model
  latency a call; with 51 MB of pictures 44 s and 745 MB; docs/web.md, "The conversion runs in a child
  process"). Production environment values (`PER_USER_DAILY_PAGE_QUOTA`, `OPEN_TIER_DAILY_PAGE_QUOTA`,
  `MAX_USD_PER_JOB`) are set at deploy, not in code: a `MAX_USD_PER_JOB` of 4.70 set for tables is now
  below the $5.71 that 100 pages need (docs/deploy.md, "Spend limits").
- Stored-bytes caps, because uploads are in Postgres on a 5000 MB volume and one person could now store 2.2 GB: `STORAGE_PER_USER_MB` (300) and `STORAGE_OPEN_TIER_MB` (250) per person, counted with the quota checks in the job's transaction; `STORAGE_GLOBAL_MAX_MB` (2000, not 3000: the nightly backup check restores a second copy on the same volume) for the service, which turns uploads away with a 503 and puts `storage` in `/healthz/ops`. The hourly cron runs a plain `VACUUM` of the upload tables after a purge. The limits page names the cap. Declared in `.railway/railway.ts` (`webOnlyVariables`).
- `ops launch-check` and the start-up check gain two warnings: an open-tier daily quota under one
  full-size document, and `IMAGE_PAGES_MAX_USD_PER_JOB` under `MAX_USD_PER_JOB`.
- Word and PowerPoint files: the ZIP checks scale with the upload limit (500 MB unpacked, 250 MB an
  entry; were 200 and 100). A 100-page Word file became a PDF in 1.4 s here (PowerPoint could not be timed:
  Impress is not installed on the box).
- The parts page and its error message use `1-10` as the example range instead of `1-30`, which read
  as a limit. docs/web.md, docs/deploy.md, docs/deploy-railway.md (the service should be allowed 2 GB; a 95
  MB upload to confirm after the deploy), docs/security.md, docs/operations.md (how much the uploads
  weigh in the database), README, vendor sheet, `.env.example`, `deploy/deploy.env.example`.

#### Sharing with named people (branch share/g1-named-people)

- An owner can share one finished document with named people by email address (`/jobs/{id}/share`,
  `web/shares.py`, `routes/sharing.py`; migration `b7112167eb0d`, table `job_shares`). They must sign in with
  that exact address (matched in lower case and in full; no domain rule, no public link, nothing is emailed:
  the owner copies the address). Two rights: `view` (job page, preview, downloads, review record) and
  `review` (also the review screen and every decision and edit, each recorded under the reviewer's name in the
  review record). The owner alone can delete, retry, change settings, upload a revision, combine parts, share
  further or remove a share; removing one works on the next request. A shared document is listed under "Shared
  with you" on My documents.
- One access check, `routes.jobs.job_access(session, job_id, user, need)` with `need` `view`, `review` or
  `owner`, replaces `owned_job` at every call site (about 50). A stranger or a removed person still gets the same
  404 as for a made-up id; a shared person asking for more than their right gets a 403 page.
- Quotas and spend stay the owner's: a model re-read a reviewer asks for counts against the owner's day allowance
  and the document's caps. A viewer's download does not restart the owner's deletion clock.
- Deleting a document deletes its shares; erasing an account deletes the shares that name its address.
- A share applies only to a sign-in that proved the address (Google's verified email, Microsoft's `xms_edov`, or
  an emailed single-use link sent to that address; migration `ecb0b22515e0` adds `login_links.email`). **After
  this deploy, people who use a shared document sign in once more** (sessions from before it carry no proof);
  until they do, the document's address shows a page telling them to sign in again.
- Wording: the privacy notice, the accessibility-office page, the landing page and the job page no longer say
  that only the owner can see a document. docs/web.md, docs/security.md. Test: `tests/test_job_shares.py` (every
  job route against the owner, a viewer, a reviewer, a removed person and a stranger).

#### Fair queue

- The worker takes queued jobs round-robin by person (`web/fair_queue.py`, docs/web.md "Worker"):
  fewest attempts first as before, then the owner served longest ago, then oldest. Someone with ten
  queued documents no longer keeps another person's single one waiting behind all ten; a person
  alone is served in the same order as before. The job page's "documents ahead of yours" replays the
  same order. Tests: `tests/test_web_fair_queue.py`, one Postgres test in `tests/test_web_postgres.py`.

#### Word and EPUB in "Download all" (branch export/w3-download-all-formats)

- "Download all" (My documents and the course page) and "Combine parts" offer Word (.docx, where pandoc is
  installed) and EPUB as well as HTML: `?format=docx` or `?format=epub` on `/app/download`,
  `/courses/{id}/download` and `/jobs/{id}/combine`. Same wording and pattern as the single downloads
  ("Other file types"). A Word zip holds at most 10 documents (each is a pandoc run); a document that cannot
  be made in the file type is left out and counted in `contents.txt`. Each document counts as downloaded in
  that file type, so the review record lists it. The HTML zip no longer restarts the deletion clock of ready
  documents it left out for the 25-document cap. docs/output.md, docs/web.md, docs/security.md.

#### Word and EPUB downloads (branch export/word-and-buttons)

- Word export (`pipeline/export_docx.py`, `readablestem export docx FILE.html OUT.docx`): the finished HTML through
  pandoc in a child process (60 s, minimal environment, CPU, size and heap limits), then checked and fixed in
  `word/document.xml`. Equations are native Word equations (OMML; the file must hold as many as the HTML has
  `<math>`, else the export fails), headings are Heading 1 to 6, each picture's alt text is written where Word reads it
  (pandoc wrote the picture's address there), a table's header row repeats and is marked `w:tblHeader`, lists and links
  are Word's, the language and title are set, a long description follows its figure, page markers are left out and a
  figure without a picture is paragraphs, not a layout table. Remote pictures, scripts and forms are not carried
  (the same sanitiser as the EPUB). docs/output.md, "The Word export".
- `GET /jobs/{id}/download.docx` and `/download.epub` (`web/routes/exports.py`) and an "Other file types" block on the
  job page's finish line (plain links; the Word one only when pandoc is installed). Both follow the HTML download:
  owner only, clock restarts, recorded as a download (with the decisions that were open), and offered only when no
  decision is open. At most two Word files are made at once; a third request waits up to 20 s, then gets a 503 asking
  to try again. "Download all" stays HTML only.
- Usage events: a `download_format` event (`html`, `canvas`, `cartridge`, `docx`, `epub`) beside each `downloaded`; the
  review record and its CSV list the file types downloaded, and its "what was not checked" says nobody has checked the
  Word and EPUB files in Word or a reading app.
- Fix: the Canvas zip and the Common Cartridge downloads did not restart the deletion clock, though docs/security.md
  says a download does; they do now. The two questions after a download also open for the Common Cartridge link.
- Docker image: pandoc from the distribution (about 200 MB on disk), always installed; `python-docx` joins the dev
  group for the tests; THIRD_PARTY_NOTICES.md lists both.

#### Review at scale, finish notice, guides, caching

- Review: "Accept all remaining equations whose checks passed" and "Accept all figures that have
  generated alt text", each after a list of what would be accepted; one write, one Undo
  (`web/bulk_accept.py`). The list view's decisions can be filtered by kind and by page. Every card
  has "Report this item" (job and item ids only). Pages kept as images have the documented "Leave
  this page out of the HTML" button. Undo is 44 px, and the marks on the page image grow to 44 px
  where there is room ([docs/web.md](docs/web.md), "Review at scale").
- Job page: the tab title changes and an opt-in browser notice shows when a conversion finishes.
  "Upload a revised version" is on the results page and on each finished row of My documents.
- The landing page says what review costs (decisions per page, a full job, a new account's day,
  deletion after 24 hours). `/guides` lists the guides; the sitemap names the showcase pages by their
  canonical address.
- `/demo`, the showcase pages and unhashed static files are served with an ETag and a one hour
  `max-age` instead of `no-store` or `no-cache`.

#### EPUB export

- `readablestem export epub FILE.html OUT.epub`: an EPUB 3 from the finished HTML (`pipeline/export_epub.py`): XHTML with native MathML, images as files with their alt text, a contents list from the headings, a page list from page markers when present, language, title and accessibility metadata. No `conformsTo`; the summary makes no conformance claim; a feature is claimed only when every item has it. Scripts, frames, forms, remote pictures and `url()` styles are not carried. EPUBCheck and DAISY Ace run on it in `.github/workflows/epub.yml` and locally when installed ([docs/output.md](docs/output.md), "The EPUB export"). No route or button yet.

### Site, design and copy

#### Accuracy claims on the registered fresh5 result (2026-11-04)

- The landing page, `/limits`, the comparison guide, the page for accessibility offices and its summary, the
  vendor information sheet and `llms.txt` quote the fresh5 result in place of the October 2026 OpenStax numbers
  (version 0.4.0), which stay on `/limits` and in `PROOF.md` as history. Registered wording: no difference this set
  can detect from the Opus chatbot on the primary score (0.840 against 0.836, 26 documents, 1,715 equations);
  silent errors 5.0 against 9.6 per 100 (goal 4.0 not met); flag recall 0.46 (goal 0.60 not met); chemistry (3
  sections) not a success. The tiles come from `docs/eval-equation-fresh5.md` through `web/proof_facts.py`.

#### Site copy rewrite (branch content/site-rewrite)

- Every public and signed-in page says what the service does today. The landing page lists what it
  handles (Beamer decks frame by frame, chemistry, and tables, long descriptions, scanned and
  handwritten pages and course notation only where their flags are on, `routes/pages.py`
  `_features`), what you download (HTML, Word, EPUB, Canvas zip, Common Cartridge, the review
  record) and a short accuracy block that says it does not yet beat the chatbot on equations.
- /limits holds every measured result with its sample size: the registered held-out test first,
  the real-world held-out papers, the not-blind re-measurement, the development baseline
  (labelled development), chemistry and handwriting. The old Physics chapter 2 sample is gone.
- /for-accessibility-offices gains an "At a glance" list, names the downloads and sharing, and
  says a conformance report is in preparation (not published). /privacy describes PostHog
  analytics when `ANALYTICS_ENABLED` is on. "Also let the developer look" is now "Also let us
  look at the results" everywhere. The upload hint is a list. Guides, README, the showcase,
  `docs/vendor-information.md` and llms.txt are updated to match.
- Second round, from a blind review: the landing page's lists are one line an item in plain
  words (MathML, F1, model judge, held-out and Common Cartridge are glossed or left to
  /limits; the proof strip takes plain labels through `plain`), a line says what "exactly
  right" means, the chatbot comparison is its own sentence, and one decisions-per-page figure
  per kind of document (textbook 1.8, LaTeX papers 5.6, handwriting 5.7) is given on the
  landing page and /limits alike, with the job page's exact count. The calls to action are
  "Sign in and convert a PDF" and the demo; the two FAQs a newcomer needs open by default. The
  upload form's limits sentence, scanned-pages label and keep options are plainer, and the
  redundant "Convert another PDF" link is gone. The ADA guide starts with "What to do this
  term". On the review screen "Edit the LaTeX" is closed when a one-click fix is offered, and
  the compare box says "at the same scale". `scripts/design_shots.py` uses the production
  limits.
- Footer: "Built and maintained by Harish Manoharan. Open source · Contact". The offices page
  has "Support and continuity" (open source and self-hostable, every result downloadable, no
  service-level agreement, how to report a problem).
- Retention: an account setting "Keep my documents for 7 days by default"
  (`users.keep_week_default`, migration `5d8e1f2a7c39`, `POST /account/keep-default`) starts the
  upload form's 7-day box ticked; each upload still decides. The job page says near the top when
  the document will be deleted, with a one-click "Keep it for 7 days". Retention wording says the
  clock restarts on each decision or download.

#### Design polish (branch design/d6-polish)

- Fixes from the Impeccable audit and critique of the redesign: paragraphs, hints and list items are capped at 75ch;
  Sign out, document-row links and check boxes meet the 24px target floor (header buttons 44px); the review screen's
  desk colour is lighter so its links have 7.3:1; the current step on the review screen keeps the two-tone focus
  ring; the three fonts the first view needs are preloaded (no layout shift on a phone); buttons and the page tools'
  forms never overflow at 320px or 200% zoom; the file chooser button is styled as a secondary button; the
  comparison table keeps its topic column in view and tints the ReadableSTEM column; a few legacy radii and an
  off-token colour moved onto the tokens. The focus-style test reads the new tokens, light and dark.

#### Comparison table and showcase on the tokens (branch design/d4-compare-showcase)

- The comparison guide (`/guides/readablestem-mathpix-latexml`) now compares five tools: ReadableSTEM, Mathpix,
  Aelira, general chatbots and LaTeXML, on what each takes in and gives back, its maths, what it automates,
  review by a person, where the file goes, cost and licence, a published test of equations and its version.
  Every cell names its source and the day it was read (Aelira's own page and Mathpix's pages for them; the
  chatbot column is the recipe tested in PROOF.md, not a claim about any product); accuracy appears only where
  we measured it (ReadableSTEM and the chatbot recipe). `tests/test_compare_table.py` checks the sources and
  that no cell claims conformance. The title, `/guides`, the footer label and `llms.txt` name the five tools.
- The static showcase (`site/`) is built on the web app's tokens: `build.py` copies `tokens.css` and its
  fonts into `assets/` (no outside request; it still opens from a folder), `site.css` uses the tokens, the
  footer has the same four columns, and the showcase follows light and dark. The converted output and the PDF
  pages stay on white paper. The `/demo` page is the stored converted document and is not restyled.

#### Landing page and proof strip (branch design/d2-landing)

- Landing page: the hero shows a real passage of the demo (OpenStax Physics 2.3, page 3): the PDF crop
  and what a screen reader gets from its text layer, beside the converted paragraph with live MathML
  and the reading `pipeline/speech.py` gives it. `scripts/hero_assets.py` builds the crop, text layer,
  MathML, reading and a 7.8 KB subset of Latin Modern Math from the stored demo; `tests/test_hero_assets.py`
  regenerates them and compares. Then Upload, review, post; what you get; the numbers; try it;
  questions (who can use it, what happens to your documents) and about, in the same wording as before.
- `web/proof_facts.py` reads PROOF.md once into `proof_facts.json` (regenerate with
  `uv run python -m readablestem.web.proof_facts`); the landing page and /limits show four tiles from it, each with
  its count, the set, the date, "blind", where a general chatbot scored the same or higher, and a "How it was
  measured" link to the PROOF.md section. `tests/test_proof_facts.py` fails when the JSON and PROOF.md differ.

#### Design system (branch design/d1-tokens-components)

- Site design tokens and components (`static/tokens.css`, `static/app.css`, `DESIGN.md`,
  `PRODUCT.md`): self-hosted Source Sans 3, Source Serif 4 and Source Code Pro, one accent
  (`#1c46ad`), all text at least 7:1 in light and in dark (follows the browser's setting),
  nothing under 14px, a 4px grid, 4px and 8px radii (no pills), a 75rem page and a 68ch reading
  column on pages people read. The header has a Sign in button and folds into a Menu disclosure on
  a narrow screen; the footer is four columns (Product, Trust, Guides, Source) with the same
  links as before. Forced-colours and reduced-motion rules cover the new components. The app's
  side-stripe notices and cards became full borders. `/style` (noindex, not in the sitemap) shows
  every component. Docs: `docs/site.md` "Design system", `static/vendor/README.md`.

#### Keyboard walk and draft ACR

- New keyboard-only walk of the web app (`tests/test_web_keyboard_browser.py`, browser-marked,
  helpers in `tests/fixtures/web/keyboard.py`): Tab through every page at 1280, 390 and 320 CSS px
  (skip link, focus visible and not covered, names, no trap, no change on focus, landmark order)
  and the main flows by key alone, with where focus lands after each redirect.
  `tests/test_web_focus_style.py` checks the focus ring's colours offline.
- Fix (found by the walk): after leaving a page out, putting it back, or choosing a line to change
  in the page tools, focus stayed on the page itself because the redirect named a fragment that was
  not focusable (a browser skips `autofocus` when the address has a fragment). The redirects and
  links now name `#page-tools-summary`, and the tool sections take focus (`tabindex="-1"`).
- Draft accessibility conformance report in `docs/acr/` (VPAT 2.5Rev WCAG edition layout, WCAG 2.2
  A and AA), not published and not a claim of conformance: each row names its test or code, and
  rows that need a screen reader session are "Not yet evaluated". `tests/test_acr_draft.py` holds
  the report to its own rules.

#### Wording and a page for accessibility offices (branch trust/wording-and-offices)

- Copy: the "individual developer" and "not a company or a university" framing is gone from the landing, privacy,
  terms and login pages, `llms.txt` and the README; the landing page has an About block ("Built and maintained by
  Harish Manoharan. The code is open source."). The listed strings are in the pull request.
- Licence wording (ADR 0003 addendum): the PyMuPDF/AGPL sentence moved from every footer to the Terms
  ("Open source", `/terms#open-source`); the footer keeps the "Source code" link and gains an "Open source" link.
  The demo and showcase notes link the source without the sentence.
- New public page `/for-accessibility-offices` (`web/offices.py`): data flow with a text equivalent, the
  companies that receive data (from `web/recipients.py`, as `/privacy`), retention from the settings, no training
  and `store`, student records (FERPA), what the tool does not do, a pilot checklist, and a one-page summary to
  download (`/for-accessibility-offices/summary`). Footer link, sitemap, `llms.txt`.
- `docs/vendor-information.md` refreshed: current accuracy numbers with sample size, scanned and handwritten
  pages, files, no conformance report, no claim of conformance (kept under 900 words).

### Evaluation and proof

#### Fifth fresh split, registered result (branch eval/fresh5-report)

- `docs/eval-equation-fresh5.md`: the fresh5 run (registration PR #149). The candidate and the Opus chatbot
  show no difference this set can detect on spoken-equal end to end (0.840 against 0.836, 26 units, 1,715 gold
  equations). The candidate has fewer silent errors (5.0 against 9.6 per 100 gold), but the registered claim is
  not made, because silent errors are above 4.0 and flag recall is 0.46. Stratum D (chemistry) is not a success.
- `scripts/realworld_run.py` reads the four fresh5 strata. It adds the registered settings in a clean environment
  (`--settings`), one cap for the two ReadableSTEM arms (`--group-cap`), chatbot runs over the split in the
  registered order (`--part fresh5`), a free token-count dry mode (`--dry`) and the strong-prompt arm
  (`chatbot-opus-expert`). `scripts/fresh5_report.py` writes the registered tables.

#### Spoken-equal scorer reads one print as one speech (branch eval/spoken-scorer-fix)

- Evaluation only; the product's speech and `math_canonical.compare` are unchanged. The spoken-equal scorer
  (`scripts/rw_spoken.py`) spoke 71 of the 2,350 development gold equations differently from a reading that
  prints the same (`\mathrm{in}` against `\text{in}`, gold `\textbf` without its bold, `\min` against
  `\text{min}`, `\bigl(`, `...`, `\backslash`, scripts and accents). `math_canonical.repair_converter_gaps`
  and `speakable_latex` now write each print one way on both sides for every arm; a pair right before stays
  right. 68 of the 71 became equal; ReadableSTEM base-0508 on development 0.792 to 0.820, Opus 0.728 to 0.755,
  Haiku 0.723 to 0.749 (`docs/eval-realworld.md`). Guard tests: `tests/test_math_canonical_spoken.py`.
- `PREREGISTRATION-FRESH5.md` section 4: the primary is spoken-equal, `exact_lenient` a secondary. The scorer,
  its folding and the speech files are in `FRESH5_FILES`, to be hashed at signing.

#### Development baseline (branch eval/dev-baseline-2026-11-03)

- Measurement report `docs/eval-dev-baseline-2026-11-03.md`: ReadableSTEM at pipeline 0.5.3 with production flags on the 25
  real-world development papers, against the recorded Opus (6 papers) and Haiku (25) chatbot answers. One
  run, $0.1186 of model spend; no code, prompt or setting changed.

#### Figures spot check

- `eval figures-spotcheck` (`eval/figures_spotcheck.py`, docs/eval.md "Figure spot check"): offline,
  no model call. From a figures run with long descriptions on, writes a blind sheet of every
  long-judge fail plus a seeded sample of 10 passes for a person to label, and scores the labels
  against a key (judge fails confirmed, sampled passes the person would fail, each with n and a
  Wilson interval). Tests: `tests/test_eval_figures_spotcheck.py`.

#### Agreement oracle on the recorded chatbot answers (branch eq/agreement-oracle)

- `scripts/eq_oracle_agreement.py` and a D2 section of docs/eval-equation-diagnostics.md: how often
  a second whole-page reading (the recorded Opus chatbot answers) is right where ours is wrong, how
  often our reading is right when the two agree under `math_canonical`, how well disagreement
  flags our wrong readings, and how many of our undetected equations it found. Measurement only,
  offline, no change to the product.

#### Speech under screen-reader engines (branch sr/speech-oracle)

- `scripts/screenreader_speech.py` and `eval/speech_oracle.py` run any set's MathML (ours and the
  gold's: OpenStax, authored, chemistry, handwriting, arXiv) through MathCAT 0.7.7 (NVDA 2026.1,
  JAWS; ClearSpeak and SimpleSpeak) and Speech Rule Engine 4.1.5 (MathJax, ChromeVox). Per
  equation: the spoken strings, whether MathCAT recognised chemistry, a word diff. Per set: the
  share of ours that sounds like the gold under each engine, with an interval, and the worst
  classes. `eval pipeline --speech` adds the aggregates to an equations or document report.
  `scripts/speech_oracles/build.sh` builds the engines (rustup into `~/.cargo`, no sudo); without
  them everything skips with a message. Offline, $0. See `docs/eval.md`, `docs/chemistry.md`.

#### Eval runs keep their recordings; chemistry report fields (branch chem/eval-metric)

- Every eval run that calls a real model (`eval pipeline` on any set and task, `eval sample`,
  `eval calibrate --probes`) now writes `answers.jsonl`, `items.jsonl`, `report.json` and `run.json`
  (the commit) under `~/.cache/readablestem/<set>/runs/<timestamp>/`, and the report names the folder
  and the commit under `recording`. `--answers` and `--items` still override. Fake runs, replays and
  the sealed check part keep nothing. `scripts/dev_chatbot.py score` always writes its items.
- `scripts/dev_chem_classes.py`: reads a run folder, prints how many gold equations harm a listener,
  and with `--speech` the spoken comparison and the silent harm per 100 gold.
- Document reports with chemistry gold gain a `chemistry` block: `chem_right_excl_times` (lone `×`
  left out, reported beside the count that keeps it) and `silent_harm` (wrong, not asked, heard
  differently), counts only. Report fields only; the primary scorer, the gate and committed reports
  are unchanged, and no gate reads them.
- Recordings are private (folders 0700, files 0600). `eval prune --older-than DAYS [--set NAME] [--dry-run]`
  deletes old runs and keeps any a committed report names. `scripts/dev_chem_classes.py` refuses
  held-out and sealed run folders.

#### Fifth fresh real-world split: selection, builders and registration draft

- Fifth fresh real-world split, builder only (no system run): `scripts/fresh5_select.py` (arXiv continuation, OpenStax
  sections never used and printed to PDF, course-style LaTeX incl. Beamer decks from GitHub under CC BY, CC BY-SA
  or MIT, compiled with a user-level TeX Live and Tectonic as fallback inside a bubblewrap sandbox), `gold/course_corpus.py`, and the registration as an unsigned draft
  (`data/realworld/PREREGISTRATION-FRESH5.md`). `arxiv_gold.analyse` takes `min_equations` (default unchanged, 5). New `gold/safe_tex.py`: the one place that unpacks
  (no links or unsafe paths, size and file caps, atomic) and compiles downloaded TeX (sandbox, `-norc`, no network or shell
  escape, no environment from the parent, a cap on what a compile writes); `realworld_build.py` and
  `figures_v2_arxiv.py` unpack and compile through it too.
  Stratum D (`fresh5_select.py chemistry`): never-used OpenStax chemistry sections from the CC BY first editions, then the CC BY-NC-SA
  2e editions as a private measurement (licence read live; their PDFs and gold only in the cache, ids and hashes in the repository),
  with measured text overlap and page gates, and a chemistry-aware success rule in the draft registration; the rule fills 3 of 4 places. The used-section scan skips the selection files of B and D.
  [docs/eval-realworld.md](docs/eval-realworld.md).

#### Figure gold v2 and a draft pre-registration for long descriptions

- `data/gold-figures-v2/`: 50 figures (14 data charts, 14 diagrams, 10 photos and illustrations, 12 chemistry
  structures) from CC BY OpenStax books, drawn by a seeded rule with licence and exclusion gates
  (`gold/figures_v2.py`, `scripts/figures_v2_pool.py`, `scripts/figures_v2_build.py`); each has its source,
  licence and a reference long description, and 4 of the charts a reference data table. 45 figures come from
  retired editions ([decisions/0001-scope.md](docs/decisions/0001-scope.md), question 2). No model was run.
- `gold/figures_v2_arxiv.py` reads plotted data from pgfplots TeX; 100 seeded CC BY papers held no usable
  chart. `gold/figures_v2_table.py` scores table cells against a reference table.
- [docs/eval-figures-long.md](docs/eval-figures-long.md): the set, and a draft pre-registration (arms, bars,
  budget cap, judge calibration with human labels) awaiting sign-off.

### Security, operations and deployment

#### Redirects built from owned rows (branch sec/redirects-from-owned-job)

- Every redirect in the review, edit, bulk, course, job-settings, feedback and job routes takes its job or course id
  from the row the ownership check returned, never from the request's path or form. `write_review` and
  `write_structure` return that id, and the `ReviewStale` and `ReviewInvalid` they raise carry it. The item a form
  names is looked up in the stored result before it is put in an address. No change for a visitor; it removes the
  CodeQL `py/url-redirection` alerts at the root. Test: `tests/test_web_redirects_owned.py`.

#### Finished steps are kept when a configuration error ends the run

- When a configuration error (a model that is not set up) ended a conversion while other
  steps were still being answered, those steps were not kept for "Try again" if the error
  reached the scheduler first; they are now (`pipeline/run.py`, `_Run.collect`). The tests
  for this and for the worker and child-process suites no longer depend on timing luck, and
  the end-to-end content test renders from the stored result the way the review screen does
  (page markers included).

#### Conversions run in a child process (branch fix/worker-child-process)

- The whole conversion (`convert_pdf`) now runs in a child process the worker can kill
  (`web/isolated_convert.py`, `web/conversion_child.py`), with an address-space cap
  (`CONVERSION_MEMORY_MB`, default 3072), a CPU limit and the job's wall-clock limit. A PDF that
  passes the upload probe and still crashes, exhausts memory or hangs MuPDF now fails that one job
  (`conversion_crashed`, `conversion_oom`, `timeout`) instead of taking the web process, every
  session and the worker down, three times over. The child has no keys or database URL: model
  requests, spend reservations, progress and the step cache stay in the web process, so spend
  accounting and the database writes are as before. Results are unchanged
  (`tests/test_web_isolated_convert.py`). `PRELOAD_TABLE_MODELS` no longer loads models in the web
  process. Details and measurements: [docs/web.md](docs/web.md), "The conversion runs in a child process".

#### Faster CI

- The pytest job is split into four shards balanced by recorded durations (`--shard i/4`,
  `tests/shard_durations.json`), with a check that every test ran in exactly one shard. Python
  3.14 runs the full suite on main, nightly and on dependency or workflow changes, and a smoke
  (ruff, ty, import of every module, one shard in eight) on other pull requests. The browser job
  runs in two shards of three xdist workers each and caches Chromium and axe-core; the axe-core download is written
  atomically. Pull request runs cancel when superseded, main runs do not. Job names changed:
  `ruff + ty + pytest (Python 3.x)` is now `ruff + ty (Python 3.x)` plus `pytest (...)` shards; a
  `CI passed` job summarises them. See [docs/development.md](docs/development.md), "CI".

#### Dependency and CI hardening

- Locked dependencies upgraded for known advisories: pillow 12.3.0 to 13.0.0 (floor now `>=13.0`),
  certifi to 2026.10.23, joserfc to 1.7.7.
- CI: a `dependency-audit` job (`pip-audit` over the exported lock), a CodeQL workflow (Python and
  GitHub Actions; push to main, pull requests, weekly), `alembic upgrade head` plus `alembic check` in
  the `postgres` job, which now runs Postgres 18 (the backup image's major). Zero collected tests in the
  `postgres` and `browser` jobs now fails them. The main `pytest` step runs in parallel (`-n auto`).
- `make test-fast`; pytest-xdist and pytest-timeout in the dev group. The review-UX browser tests wait
  with Playwright's `expect` and have a 120 s per-test timeout.

#### Security hardening

- The `/m/` analytics relay is rate limited per address (`analytics`, 300 per 10 minutes) and no
  longer forwards the visitor's address to PostHog.
- Production refuses `ANALYTICS_ENABLED=true` until the privacy notice names PostHog and no
  longer says there are no analytics. The notice text is unchanged.
- Production requires a `CRON_TOKEN` of at least 32 characters.
- A one-time `proxy_hops_unexpected` log line when `X-Forwarded-For` has more entries than
  `TRUSTED_PROXY_HOPS + 1`; `launch-check` flags `TRUSTED_PROXY_HOPS=0` in production.
- `SESSION_SECRET_PREVIOUS`: earlier session secrets accepted for verification only, so the
  secret can be rotated without signing everyone out ([docs/security.md](docs/security.md)).

#### Microsoft sign-in configuration

- `.railway/railway.ts` declares `MS_CLIENT_ID`, `MS_CLIENT_SECRET` and `MS_ALLOWED_TENANTS` on the web service as `preserve()`, so values set with `railway variable set` survive `railway config apply`. `docs/deploy-railway.md` has a "Microsoft sign-in on Railway" section with the app registration, the secret's expiry, the allowed directory ids and the exact commands.
- `readablestem ops launch-check` also lists `MS_ALLOWED_TENANTS` set without a client id and secret (the sign-in would be quietly off). A client id or secret alone was already refused.

#### Monitoring and recovery

- The daily backup keeps a dump only after it has been proved: gzip reads it back, it ends with pg_dump's completion line, it restores with errors fatal, every live table is in the restore and `alembic_version` matches. A failure prints `backup_failed reason=...`, removes the file and fails the run. The command is a readable bash script (`set -euo pipefail`) with tests (`tests/test_railway_backup_script.py`). `railway config apply` is needed to put it live.
- Alerts without a new vendor: `ops_heartbeat` (migration `e1a7c3b95d24`), written by the backup and by `readablestem ops cron`; public content-free `GET /healthz/ops` (503 naming the late task); `uptime.yml` now checks `/readyz` every 5 minutes and `/healthz/ops` every 3 hours. GitHub disables scheduled workflows after 60 days with no commit to a public repository.
- Erasing an account writes a content-free line to `erasure_ledger` (hash of the user id, time); `readablestem ops replay-erasures [--ledger-csv FILE]` erases again whatever a restored backup brought back.
- Per-person daily spend: `quota_days.spent_usd`, shown by pseudonym on `/admin`.
- The day's cap on new accounts logs `signup_cap_reached` once a day and shows a notice on `/admin`.
- Database pool: 5 plus 5 connections per process (was 3 plus 2).

### Documentation

#### Full documentation refresh (2026-11-04, branch docs/full-refresh-2026-11-04)

- Every document was checked against the code and the production settings: flags and their production state
  (on: `EQUATION_GLYPH_RECONCILE`, `EQUATION_GLYPH_CHECKS`, `EQUATION_LINE_CONTEXT`, `FLAG_POLICY`, `FEATURE_TABLES`,
  `FEATURE_IMAGE_PAGES`, `FEATURE_LONG_DESCRIPTIONS`, `FEATURE_NOTATION_MEMORY`; off: `LAYOUT_DISPLAY_GROUPING` and the LaTeX,
  Beamer and zip-batch features, because the sandbox probe fails on Railway), limits, the pipeline version (0.5.9),
  deploy files (`.railway/railway.ts`, not `railway.json`), settings in `.env.example`, decision-record statuses and
  the documentation index.
- `docs/eval.md` lists the registered results newest first and the README's results section follows the same order: the
  fresh5 result (26 documents, version 0.5.9) first, the first registered result (15 OpenStax sections, version 0.4.0) as
  history. `PROOF.md` is the replayed report of that first result and stays as it is (`proof/verify-rename.py` checks it
  against its tag); the site's proof tiles read `docs/eval-equation-fresh5.md` through `web/proof_facts.py`.

#### Screen-reader test protocol

- `docs/screen-reader-test.md`: a 15-minute VoiceOver (Safari) protocol for the demo, a converted file and the review screen, with a results table that maps to WCAG 2.2 criteria.

## The interim held-out report is replayed at the tag, and CI runs it (2026-11-02, branch fix/proof-interim-repro)

- `tests/test_proof_interim.py` used to rebuild the registered report with the current scorer, and
  skipped without the held-out key, which CI never fetches. With the key it failed on main: the
  chemistry converter change moved one parser's exact equations (238 to 241 of 1,369) and the rename
  of Addendum 8 moved coverage by 210 6-grams. It now replays `report.py` at the tag
  `proof-heldout-2026-10` with the key the tag's code fetches (`make heldout-gold-at-tag`) and
  requires the registered bytes. No registered number or file changed.
- `make proof-interim` writes `proof/interim/rebuilt-here.md` instead of the registered file.
- `.github/workflows/proof-interim.yml` fetches the key and runs the test (a missing key fails) on
  pull requests touching `proof/`, the scorer modules, the dependencies or the test, and weekly.

## Product analytics through `/m/`, off by default (2026-10-31, branch analytics)

- `ANALYTICS_ENABLED` (default false): PostHog loaded from and reporting to the app's own `/m/` proxy
  (`web/analytics.py`, `web/static/analytics.js`). Page views plus `signup_completed`, `login_completed`,
  `document_uploaded`, `conversion_completed`, `result_opened`, `demo_opened`; no autocapture, replay or
  document content. The policy is unchanged. Converted documents and downloads never carry the loader.
  Not enabled in production until the privacy notice's "Cookies" section is reworded
  ([docs/web.md](docs/web.md), "Product analytics").

## Equations on real papers, round 3: inline equations read with their line (2026-10-26, branch explore/span-edges)

- Why ReadableSTEM trailed the chatbot recipe: reading A counted the chatbots' table formulas and not
  ReadableSTEM's (3.9 of 5.8 points on fresh3); read the same way, the largest remaining loss was the
  text layer cutting inline formulas at the wrong place. Reading B is primary from now on.
- `EQUATION_LINE_CONTEXT`: an inline equation is also sent as its whole line with the span boxed
  (`layout.crop_line`, prompt `equation_line.md`) and the model writes the formula the box is part of.
- Fourth registered set (15 papers, 1,600 equations, `data/realworld/PREREGISTRATION-FRESH4.md`): +2.6
  points [+1.7, +3.6] (0.586 to 0.613), silent errors 7.9 to 7.3 per 100 gold; the rule for turning it on is
  met. Haiku 0.563, Opus 0.641: ahead of Haiku by point estimate, still 2.8 points behind Opus, fewest silent
  errors of the three. Spend $12.00. [docs/eval-equation-round3.md](docs/eval-equation-round3.md).

## Equations on real papers, round 2 (2026-10-23, branch fix/equations-round2)

- Two deterministic fixes: a column's full-width line is no longer a section break and a held caption fills its
  column (`pipeline/layout.py`); a table cell of words with maths is read as words and maths
  (`pipeline/table_cells.py`).
- A third registered set (15 papers, 1,779 equations, `data/realworld/PREREGISTRATION-FRESH3.md`), one run per arm.
  Registered `exact_lenient`: ReadableSTEM 0.620, Haiku 0.648, Opus 0.678, ReadableSTEM before the fixes 0.614. The
  claim of beating both chatbots is not met; counting the maths in table cells it is 0.654 against 0.648 and 0.678.
  The silent-error limit is met (+0.056 per 100 gold against +0.5). Spend $9.73 of $16.
  [docs/eval-equation-round2.md](docs/eval-equation-round2.md).
- `scripts/rw_notation_neutral.py` (a notation-neutral secondary score), reading B in `eval/pipeline_eval.py`,
  `scripts/realworld_refresh_gold.py`.

## Tables, second round: residuals and a second held-out set (2026-10-22, branch fix/tables-round2)

- Fixed on development data only (tests in `tests/test_tables_round2.py`): bar charts of few bars
  are charts (`tables._bars`, `layout._Path.rects`); lists of definitions by condition ("... if ...")
  are tables; a ruled stack split by far-apart rules is one table; two rules with a caption over
  ragged numbers, and grids of single digits under a caption, are tables. Development effect:
  `data/real-tables` 1.000 / 1.000 (was 1.000 / 0.920), `data/real-tables-heldout` 1.000 / 0.949
  (was 0.951 / 0.923); OpenStax tables task recall 0.980 to 0.985, precision 0.985 (unchanged),
  dev-v2 unchanged; the gate passes; `layout.analyze` time per page unchanged (0.38 and 0.85 s).
- A second fresh set, `data/real-tables-heldout2/` (ten CC BY documents, 58 tables, registered with
  its rule, floors and comparison before detection, `scripts/tables_heldout2.py`): run once,
  `main` and this branch both give precision 1.000 (55/55) and full-extent recall 0.948 (55/58), so
  both floors are met and the round's fixes show no effect on it. The set is mostly surveys and
  research articles, not lecture notes. Three misses are left (a small-capital caption split by
  `layout`, a check-mark table, rotated labels). No model call, no setting.

## Railway infrastructure as code (2026-10-21)

- The Railway project is now described in `.railway/railway.ts` (imported from production, so
  `railway config plan` reports no changes) instead of `railway.json` and
  `deploy/railway.cron.json`, which Railway stops reading on 2026-12-01 and which no service read.
  Both files are deleted. The file also preserves `FEATURE_IMAGE_PAGES`, `MAX_USD_PER_PAGE` and
  `MAX_USD_PER_JOB` on web, set after the import; without them a plan would have deleted them. How to change the infrastructure: [docs/deploy-railway.md](docs/deploy-railway.md).

## Table recall on real documents (2026-10-21, branch fix/table-recall)

- On the five real documents of `data/real-tables` the detector found 14 of 25 tables (recall 0.560
  against a floor of 0.85, precision 1.000). Traced table by table (docs/tables.md, "Recall on real
  documents"): the layout model proposes ruled, shaded or header-marked grids only, so unruled lists
  and tables ruled only above and below were never proposed (or came as fragments); justified columns
  were one cell wide for the 0.8 em cell gap; a ruled box of prose under "Table 2:" was called layout
  because the caption was attached after that decision; a page of sideways text was never looked at; a
  page with over 30,000 drawn items got no proposals at all.
- Fixes in `pipeline/tables.py`: boxes made from the page's lines and rules (rule frames, text blocks)
  go through the same arbitration, the detector's boxes first; columns from strips no word crosses;
  captions count (Roman numerals "TABLE II.", a ruled box under a caption is a table, `is_layout_grid`
  after captions); a page of sideways text is analysed turned upright (`TableGrid.turned`, and
  `layout.crop` cuts the picture upright); a text cell grid as a structure (so a table found without
  the model file is not dropped); a numbered equation or reaction at the edge of a table's box is cut
  off it; guards against charts, page columns, references, numbered or bulleted lines, algorithm boxes,
  equations, callout boxes and front matter. No model call, no prompt change, no setting: the spend
  caps and `ops launch-check` are as before.
- Measured (free): real-document development set precision 1.000 (23 of 23), recall 0.920 (23 of 25);
  OpenStax tables task recall 0.909 to 0.980 and precision 0.984 to 0.985, TEDS 0.866 to 0.936; dev-v2
  recall 0.774 to 0.925, chemistry tables found 32 to 38 of 42; the gate passes. A fresh held-out set of seven
  CC BY papers (`data/real-tables-heldout/`, registered before any run) was run once: precision 0.914,
  full-extent recall 0.744, **both floors missed**, no better than the code before (0.912, 0.744): its
  misses were Roman-numeral captions, bulleted cells and charts boxed by the layout model, none of
  which the development set had shown. Those were fixed afterwards and the set then gives 0.951 and
  0.923, which is a development number.
- New: `gold/tabulars.py`, `scripts/tables_label.py` and `scripts/tables_heldout.py` (place a paper's
  LaTeX tables on its PDF and build a labelled set), `tests/test_tables_recall.py`,
  `data/real-tables-heldout/`, result files for both sets. `data/eval/latest-tables-local.json` is
  re-made. Unresolved: lists of relations (2 of 25), bar charts with few bars taken for tables by the
  layout model, bold description lists stay lists.

## Equations on real papers: root causes and fixes (2026-10-21, branch fix/equation-rootcause)

- Root-cause analysis of the 12.4-point gap to the chatbot recipe on the 25 development papers
  ([docs/eval-equation-rootcause.md](docs/eval-equation-rootcause.md); `scripts/rw_gap_table.py`): short
  variables set in an italic text font (3.9 points), maths in table cells (2.9), in captions (1.7), the edge of an inline span
  (3.6), `\bm` read as `\mathbf` (0.6). Reading the equations it found is not where it loses.
- Fixes, from the text layer and the glyph boxes, no prompt change (`equation-68ebbbf9`, the eval gate and the `latest-*`
  reports untouched): an italic letter in upright prose is a variable; a bold letter with an index is a vector; a figure's caption is cut
  like a paragraph (`Element.caption_marked`, inline equations whose parent is the figure); the parentheses of the sentence, a unit after a number, a citation
  and a trailing bracket are not part of an inline formula; a word space between operands ends it; an ellipsis list, `:=` and `H : A -> B` are one;
  a parenthesis or hyphen the run's text layer lacks, which the padded crop showed and the model wrote, is dropped; `\mathbf` is `\boldsymbol`
  where the PDF's font is bold italic. The TeX author's conventions apply to PDFs typeset by TeX only; 111 of the 126 PDFs of
  `scripts/layout_identity.py` are identical.
- Measurement: the converter changed under the gold (28 papers' gold no longer rebuilds), so a ReadableSTEM run of today and the chatbots' stored items were scored by two
  converters; `realworld_report.py --rescore-chat` reads the chatbot answers again with today's code. Every ReadableSTEM item also keeps a second reading in which the maths
  cells of a rebuilt table count as equations, as the chatbots' HTML is read (`pipeline_eval.with_table_maths`); the registered reading is unchanged. The scorer is not
  changed: two notation artefacts (styled letters, upright words) are counted for every arm in `scripts/rw_scorer_sensitivity.py` (about 1.5 points, the same for ReadableSTEM and Haiku).
- Results: development (tuned on it) 0.553 to 0.680, level with Haiku, the silent errors 12.0 to 8.8 per 100 gold. On 15 fresh papers, registered before any
  system ran (`data/realworld/PREREGISTRATION-FRESH2.md`): the fixes are worth +0.068 [+0.031, +0.116] (0.558 to 0.626), the silent errors do not rise (9.76 to 8.42, limit +0.5), ReadableSTEM is level with Haiku (0.626 against 0.622)
  and 5.6 points below Opus (0.682; 97.5% interval -0.109 to +0.007). The registered claim "clearly beats the chatbot recipe" is not met; it leaves 8.4 silent errors per 100 gold against 16.3 and 13.7. Spend $13.55 of $18.
  Unresolved: table cells (the table branch's), formulas that wrap or are typed apart, `\text` against the authors' `\mathrm` (the scorer's artefact is 4 points for the chatbots, 1.5 for ReadableSTEM, on this set).

## Maths always required on image pages (2026-10-22, branch fix/image-page-maths-required)

- Decision (owner): on a page read from an image (scanned or handwritten, the opt-in pass), every block
  that holds maths is a required review item whatever the page reader's confidence: a display equation
  (always was), and a paragraph, list item or heading with any formula in it
  (`IMAGE_PAGES_MATHS_ALWAYS_REQUIRED`, default on; `image_page._prose_fix`, `image_page.tier_headings`;
  review sentence for `image_page.maths_always_checked` in `web/messages.py`; ADR 0007 point 12).
  Prose-only blocks keep their tiers; typed pages and the typed equation step are untouched; an agreed
  second reading cannot lift such a block out.
- Measured by replay at $0: silent wrong equations 0 by construction, verified on handwriting development,
  first held-out and fresh (2 to 0 on fresh), on the 36 typed development scans (15 to 0) and on the
  scanned papers' held-out recording (46 to 0). Required decisions a page: handwriting fresh 5.79 to 5.96,
  first held-out replay 5.92 to 6.08, development 5.90 to 5.90; typed scans 3.58 to 6.44; scanned papers
  2.00 to 5.69. That is the price of "you check every item" on pages where the reader is confident.
- docs/eval-handwriting.md also states the ceiling plainly: most "wrong" equations are ambiguous ink or the
  label's notation, and reading each expression alone scores 0.61 to 0.64.

## Handwriting accuracy: root causes, display lines, omission recovery (2026-10-22, branch fix/handwriting-accuracy)

- Root causes of the 43% wrong equations of the handwriting held-out set, from the recorded answers
  (docs/eval-handwriting.md, "Root causes and fixes"; `scripts/handwriting_analysis.py`,
  `data/handwriting/error-classes.json`): of 199 wrong equations over three parts, 65% are ink (or a label)
  a reader cannot settle, 23% a notation the label did not use, 8% misreads of clear ink, 4% left out; a
  model reading each expression alone from clean ink gets 61 to 64%, as the page reading does. The
  page reader's confidence says almost nothing (`medium` is a coin flip, AUC 0.52 to 0.61).
- Fixed: a paragraph or list item that holds one formula and no words is a display equation, its
  marker its number (`image_page.lone_formula`; 35 of 56 display equations were set as inline maths;
  now 56 of 57 on the fresh set), and writing the reader left out is read from a picture of just that
  part (`image_page.recover_omissions`, `IMAGE_PAGES_RECOVER_OMISSIONS`, on) instead of only flagged.
  The image page prompt changed (`image_page-632b4d29` to `image_page-7a9e3a80`): display lines are
  equations, plain letters stay plain, every drawn mark is copied. The gate's `latest-*` reports are
  unaffected (no gated task reads this prompt).
- Added, off: a second reading of `medium` lines with a formula from a picture of the line
  (`pipeline/image_page_check.py`, prompt `image_page_crops`, `IMAGE_PAGES_SECOND_READING`), and
  acceptance of blocks whose two readings agree (`IMAGE_PAGES_ACCEPT_AGREED`). A difference is
  informative, agreement is not (it would remove about 1 decision in 140 and no silent wrong equation).
- Measured, once, on a fresh held-out set registered first (192 equations, 24 pages;
  `data/handwriting/PREREGISTRATION-ACCURACY.md`): primary 0.589 [0.521, 0.654], paired with the
  unchanged control on the units both finished +0.031 [-0.024, +0.083] (not detected); required
  decisions a page 5.79 and 5.79; **silent wrong equations 2 of 192 (2 of 161 for the control), not 0:
  the first measurement's zero did not replicate**, so the registered rule is not met. Typed scans (36
  development pages): primary and symbols unchanged, no regression beyond the noise of one run
  (docs/eval-image-pages.md, "Update"). Spend $10.31 of $12.
- Nothing changed in the typed equation step, its prompt or the scorers.

## Chemistry accuracy: the primary score was a measure of markup (2026-10-21, branch fix/chemistry-accuracy)

- Finding (docs/chemistry.md, "Accuracy"): of the 443 gold equations of the twelve development
  chemistry documents, 65.9% are written right by ReadableSTEM and the primary score (`exact_lenient`)
  says 10.8%. 244 equations that say the right chemistry are wrong on the primary only for how the
  gold spells it (token packing, script grouping, upright against italic against `mtext`, arrow
  length, box-drawing bars, numbers cut in two, a parenthesis of the sentence inside the gold); on
  the authored document 107 of 108 are right in chemistry and 4 on the primary. What is really
  missing is detection: 94 formulas in table cells (tables off), 22 lone `×` the gold counts as
  equations, 7 neighbours. 7 equations cannot be read from the printed page (it lacks the atomic
  number the gold has).
- New secondary scorer `eval/equations_chem.py` (`exact_chem`, `pred_mathml` in the document task's
  pairs), for chemistry gold only; the primary, the gate and the committed reports are unchanged.
  `scripts/dev_chem_classes.py` puts every chemistry gold equation in an outcome class, for
  ReadableSTEM and the recorded chatbot arms (`scripts/dev_chatbot.py --expand-ce` writes the
  chatbots' `\ce{...}` out for scoring).
- Fixes: a label set over a TeX arrow (`\xrightarrow{\Delta}`) no longer cuts the reaction in
  three (`layout._label_over_arrow`, `chem.ARROW_LABELS`); `\ce{->[above][below]}` is read
  (`\xrightarrow[below]{above}`); a bare `\middle|` of cell notation no longer makes the
  converter refuse the equation (`chem.tidy_cell_notation`, in `expand_ce`). Development replay (769
  of 773 requests from the recording, $0.009): chemically right 286 to 292 of 443, primary 48 to 48.
  The layout is identical on 125 of 126 PDFs (the authored chemistry document is the one that
  differs). No prompt changed.
- Held-out: five fresh sections of OpenStax Chemistry (377 equations), pre-registered before the
  run (docs/eval-chemistry-heldout.md): primary 0.135, chemically right 0.552 at the base commit and
  0.560 on this branch; $1.47.
- Not changed, with the evidence: the degree sign (`∘` for `°`, +10 on the primary, but the real-world
  gold is made by the same converter), state symbols as three tokens, the registered primary scorer.

## Omission check for scanned and handwritten pages (2026-10-21, branch feat/image-page-omission)

- A page read from an image whose reader left writing out now says so: the page picture's ink is
  compared with the regions the blocks' boxes cover (`pipeline/image_page_omission.py`, numpy only,
  no model call), and a stretch of ink no block is near becomes a figure item with a required
  decision (`image_page.omitted_region`; review sentence in `web/messages.py`; written as a picture
  marked "Not converted" until the reviewer describes it or keeps it). `ImagePageRecord.omitted`
  counts them; `IMAGE_PAGES_OMISSION_CHECK=false` turns the check off. The image page prompt is
  unchanged (same hash), so no recorded answer is invalid.
- Measured by replay at $0 (`scripts/image_page_omission_replay.py`): the handwriting page that lost
  four display equations is flagged; development pages add no decision (handwriting 10 pages, printed
  pages as scans 36); held-out: +0.083 required decisions a page on handwriting (one false flag in 23
  clean pages), +0.085 on the paper scans (5 pages flagged, 3 of them real omissions: a missing column,
  a page with one block, a skipped paragraph); the primary score and the silent wrong equations do not
  change. It does not see a wrong reading, a dropped line next to a block, or display equations read
  as inline maths (docs/eval-handwriting.md, docs/eval-image-pages.md, ADR 0007 point 8).

## Handwriting answer key and one measurement (2026-10-21, branch feat/handwriting-eval)

- A handwriting evaluation set built from real human ink (Google's MathWriting, CC BY-NC-SA 4.0, so
  not committed): 34 scanned-looking pages, 280 expressions (display on their own
  lines, inline inside typed sentences), gold in the document task's format. The repository holds
  the builder (`src/readablestem/eval/handwriting_set.py`, `scripts/handwriting_eval.py fetch|build|
  verify|run|report`) and `data/handwriting/manifest.json` (identifiers and hashes), the
  registration (`data/handwriting/PREREGISTRATION.md`) and the attribution.
- Measured once with the production image page pass (`claude-opus-5-5`, nothing tuned): held-out
  primary 0.566 [0.515, 0.618] on 205 equations, 0 silent wrong equations, $0.066 a page, $2.09
  spent; the registered rule (primary at least 0.50, silent wrong 0) is met. Findings the rule does
  not capture: 43% of equations wrong, 85% of blocks `medium` or `low` (5.7 required decisions a
  page), 35 of 56 display expressions read as inline maths, one page silently lost four equations
  (docs/eval-handwriting.md). No product code, prompt or setting changed.

## Tables on in production (2026-10-21, branch feat/tables-on)

- Decision: `FEATURE_TABLES=true`, `MAX_USD_PER_PAGE=2.20`, `MAX_USD_PER_JOB=4.70` for the Claude
  deployment (`deploy/deploy.env.example`, docs/deploy.md, docs/tables.md "The decision"); the code
  default stays off. `ops launch-check` with these: `launch_blockers=0`. The job cap the check
  needs with tables on is $4.66, not the $2.60 earlier docs gave.
- Gate floor `table_wrong_cells_not_flagged` 2 to 0 on replay ($0): the two were a table title
  kept as a heading above the box (the scorer no longer counts it as a lost cell), not a wrapped
  cell. New check for the wrapped-cell case itself: text of the table's size just under its box
  where a column starts makes the step ask a person (`table.edge_text`, `text_at_edge`; fires on
  no development table). Share of tables needing a decision unchanged: 55 of 183 (0.301).
- Real documents: `data/real-tables/`, five CC BY 4.0 lecture-note, textbook and tutorial extracts
  with 25 labelled tables; precision 1.000 (14 of 14), full-extent recall 0.560 against the 0.85
  floor: unruled lists, a rotated page and long wrapped-text tables are missed (docs/tables.md).
- `\ce` in a table cell is written out as plain LaTeX like in equations (`table_step._cell`); no
  prompt change.

## Chemistry (2026-10-21, branch feat/chemistry)

- Formulas, ions, state symbols and reactions in running text are inline equations, and a line
  that is one reaction is a display equation (`pipeline/chem.py`, `layout._with_chemistry`,
  `_reaction_row`; docs/chemistry.md, docs/layout.md). Raised and lowered text that is not
  chemistry (units, footnote marks, a letter subscript) stays `<sup>`/`<sub>`.
- The equation prompt asks for mhchem (`\ce{...}`) for chemical species; a tested subset is written
  out as plain LaTeX before the checks (`equation_step.expand_ce`), the rest is refused and the
  picture stays with a decision. `clean_latex` is unchanged (the proof applies it to recorded
  chatbot answers).
- A subscript or superscript after `(...)` or `[...]` belongs to the group (`[A]_0`, `Al_2(SO_4)_3`,
  also in maths: `(a+b)^2`), a charge or count hangs on the whole two-letter element symbol, and a
  long arrow is an operator (`validate.safe_latex_to_mathml`; docs/output.md, "Chemistry").
- Speech for chemistry (`pipeline/speech_chem.py`): "H 3 O plus", "S O 4 2 minus", "aqueous",
  "reacts to form", "is in equilibrium with", "the concentration of H 3 O plus".
- Side effect of the converter change on maths that is not chemistry, checked by replay
  (`scripts/dev_converter_effect.py`; docs/chemistry.md): +5.5 points of primary where the gold is
  written by people, and an apparent -1.1 on gold made by the old converter (the real-world arXiv set)
  that is +0.26 when the gold is converted again.
- Table headers: a header cell's text is compared without the marks of LaTeX (`d_i` is `di`), which
  was most of the apparent fall of column-header exactness with the table step on (0.792 to 0.858).
- Evaluation: `scripts/dev_chem.py` (a content score that does not depend on the gold's spelling),
  `table_cell_maths` in the document task's report (formulas in the gold's table cells found in
  the output's cells). Tables: measured on, and left off with the blockers listed
  (docs/tables.md, "Tables on, on the development sets").

## Scanned and handwritten pages read from an image (2026-10-21, branch feat/scanned-pages)

- Behind `FEATURE_IMAGE_PAGES` (off; [ADR 0007](docs/decisions/0007-image-pages.md)), pages
  `page_type` calls scanned or handwritten are read by one vision call each into headings,
  paragraphs, list items, display and inline equations, figures and tables. Every block is a review
  item (equations and tables always required, prose optional when the reader was sure and nothing
  was illegible), display equations are read again from the crop of their box, the output page
  starts with a notice and a "Show the original page" disclosure, and a page the model cannot read
  stays a kept image as before. Per-page cost is on the result (`image_pages`). With the flag off
  nothing changes. Development-set measurement per model: [docs/eval-image-pages.md](docs/eval-image-pages.md).
- The review screen lists text read from an image (accept, or keep as the picture; words are changed
  with "Change the words"). The upload probe lets a PDF of scans through when the flag is on.
- Eval: `eval pipeline --scanned` turns every page of the document task into the image-only PDF
  of a degraded scan (`eval/scan.py`), plus a prose word error rate (`eval/prose_wer.py`) and the
  image page numbers (`eval/image_pages.py`); `scripts/image_pages_probe.py` and
  `scripts/image_pages_table.py`. A model request can carry its own timeout (`VisionRequest.timeout_s`).

## Page math pass, secondary equation scores, `\right.` (2026-10-21, branch feat/page-math)

- Two layout fixes: a stray opening brace at the end of a sentence goes with its system, and
  nuclides in running text (`14C`, `235U`) are inline equations instead of raised text
  (docs/layout.md). Re-measurement of the held-out sections with the current system, not a blind
  result: [docs/remeasurement-2026-10-21.md](docs/remeasurement-2026-10-21.md); the README keeps
  the registered numbers beside it.

- Invented-content check: a secondary count `unsupported_by_rows` (rows of a derivation checked
  one at a time) beside the unchanged rule, and an audit of why the unsupported-equation rate rose
  with 0.5.0 (equation pictures are now read, and have no text to match):
  [docs/remeasurement-2026-10-21.md](docs/remeasurement-2026-10-21.md).

- Real-world evaluation set (arXiv papers, the authors' LaTeX as the answer key, a registered held-out split and a
  fresh split) and what it showed: [docs/eval-realworld.md](docs/eval-realworld.md). From it: the LaTeX allowlist accepts
  `\arg` (as an operator name), `\substack`, `\succsim`, `\colon`, `\coloneqq`, `\triangleq`, `\trianglelefteq`,
  `\gtrapprox`, `\intercal`, `\rightsquigarrow`, `\leadsto`; the page math pass has two new settings,
  `PAGE_MATH_MAX_OUTPUT_TOKENS` (4096) and `PAGE_MATH_AGREEMENT_LIFT` (true), defaults unchanged; the pass stays off.
- Page math pass (off: `FEATURE_PAGE_MATH`, [ADR 0006](docs/decisions/0006-page-math-pass.md)):
  one cheap-model call per page proposes where the equations are, quoting the PDF's text layer;
  every quote is verified against the text layer, verified spans are laid over layout's own
  decisions (layout stays the fallback), and the page's LaTeX is a second reading that the
  equation step compares with its own. Development-set results and cost:
  [docs/eval-page-math.md](docs/eval-page-math.md).
- A second labelled secondary equation score, `symbols` (tables flattened, scripts as markers),
  beside stream v2; what both say about the recorded held-out run:
  [docs/eval-secondary-metrics.md](docs/eval-secondary-metrics.md). The primary
  `exact_lenient` and every committed report are unchanged.
- `eval pipeline --sections` runs a slice of a set; `scripts/dev_buckets.py` and
  `scripts/dev_spans.py` compare runs on a development set.
- The "." of `\right.` and `\left.` is no longer taken for sentence punctuation and stripped
  from an inline equation.

## Open sign-up (2026-10-21)

- `SIGNUP_MODE=open` lets anyone with a verified Google account (or an allowed Microsoft one)
  sign in; the first sign-in makes the account. The default, `allowlist`, is unchanged. A person an
  admin blocked stays refused and is never made again. Accounts have a tier, `invited` (added by an
  admin) or `open`; the admin page shows it and can block and unblock.
- New accounts have lower daily limits: `OPEN_TIER_DAILY_PAGE_QUOTA` pages (20) and
  `OPEN_TIER_DAILY_REREADS` model reads from review (30); the service takes at most
  `MAX_NEW_ACCOUNTS_PER_DAY` (100) new accounts a day and 5 an hour per address; and their documents
  draw on `OPEN_TIER_DAILY_SPEND_CAP_USD` ($4), a pool inside the daily cap, so strangers cannot use
  up the instructors' budget. Invited people are unaffected when it is spent.
- People can delete their own account (Account page, a confirmation first). The public pages (sign-in,
  landing, privacy, terms, known limits) say what is true for the mode the server runs in, and
  `ops launch-check` checks the privacy notice says the first sign-in makes an account and how to
  delete it. Migration `a6c2e9f4b713` (additive). Details: docs/web.md, "Sign-up modes and access
  tiers".

## One heading for a doubled box title (2026-10-14)

- OpenStax prints a box's label and its title as two lines ("Teacher Support" twice), and a
  screen-reader user navigating by headings heard each twice. A heading with the same text as the
  heading directly above it, at any level, is now written as bold text (it was only the same
  level). The same text with content between the two copies stays a heading. The public demo and
  the site examples were written again from their stored results with `readablestem demo rerender`
  (no model call; it now decides the headings again too). Heading F1 on the development set goes
  from 0.934 to 0.821 because the gold counts both lines; the CI baseline was replaced on purpose
  (docs/eval.md, "Doubled box headings").

## Beta wording and repository link (2026-10-14)

- The draft wording of the privacy, terms, known-limits, landing and Canvas help pages is the beta
  wording: draft banners and `TODO(Harish)` markers are gone and `readablestem ops launch-check`
  reports no unfinished copy. Railway is named as host, daily backups are kept about 6 days (a
  deleted document can remain in a backup for up to about a week), governing law is Texas, and
  Word and PowerPoint are described only when the server can convert them. Details in
  docs/security.md.
- The source link (`SOURCE_URL` default), the README and the site point at
  https://github.com/harishm17/readablestem. The repository has to be public before the first
  sign-in (AGPL).

## Railway deploy and layout limits (2026-10-14)

- Deploy on Railway: `railway.json` (Dockerfile build, `readablestem db upgrade` before each deploy,
  `/healthz` deploy check), `deploy/railway.cron.json` (hourly `readablestem ops cron`) and
  [docs/deploy-railway.md](docs/deploy-railway.md). The Cloud Run guide stays as the alternative.
- Layout analysis no longer takes minutes and gigabytes on pages of tens of thousands of tiny
  drawings: banded clustering (same groups), a drawing cap per page (`page_type.PAGE_MAX_PATHS`,
  `PAGE_MAX_ITEMS`; such a page is kept as its image with its text, reason `unread_page`), a limit on
  the table detector, and the error code `too_complex` for an upload whose pages need too much.
  Output is unchanged on every repository fixture and development page
  (`scripts/layout_identity.py`); details in docs/layout.md.

## Name: ReadableSTEM (2026-10-14)

The product is called ReadableSTEM (readablestem.com). No pipeline, prompt, model or scoring
change.

- Python package `readablestem`; command `readablestem`.
- Page titles, headers, footer, emails, help, privacy, terms and limits pages, the student
  file's "About this version" note and the `generator` meta tag say ReadableSTEM. The class
  on the tool's own words in the HTML is `readablestem`.
- Session cookie `readablestem_session` (`__Host-readablestem_session` in production). The
  review screen's saved drafts and shortcuts (browser localStorage) start over.
- Cache folders `~/.cache/readablestem/{heldout,dev-v2,models}` and `readablestem-proof`.
- Default SQLite file `.local/readablestem.db` (or set `DATABASE_URL`).
- Deploy defaults: Cloud Run service, Artifact Registry repository `readablestem`, Cloud SQL
  instance `readablestem-db`, log-based metrics and alert policies `readablestem-*`.
- URLs: `BASE_URL` is `https://readablestem.com`; the Google redirect URI is
  `https://readablestem.com/auth/callback`.

## Pipeline 0.5.0 (2026-10-14)

Results and eval reports from 0.4.0 load unchanged; their numbers were made under the
older rules. The held-out recording (`proof-heldout-2026-10`) is a 0.4.0 run and stays one.

### Tier 1 (before the beta)

- Spend: reservations are sized for Claude (a one-page PDF can make an Opus call); model
  concurrency is a setting (`MODEL_CONCURRENCY`, held to what the caps can hold) and a 429
  pauses every request.
- Equations: leading exercise numbers are stripped from block-equation regions; bold Greek
  is written as Unicode math bold; an equation that could not be converted gets the PDF's
  own text as its alt text, marked not checked; a large display formula on a slide goes to
  the equation step, not to the headings.
- Escalation: the main model is not asked again when the only trigger is a confidence just
  under the bar with no failed check.
- Text and structure: hyphens in compounds at line ends are kept unless the joined word is
  a dictionary word; a heading that repeats the one above becomes bold text; exam mode for
  figure alt text.

### Tier 2 (accuracy)

- Layout: inline stacked fractions and binomials are one inline equation; braced systems,
  determinants and cases are one equation; a middle dot is an operator; half a fraction is
  held under the accept bar with a required decision; equations that are pictures are
  classified and need a decision.
- Prose: raised and lowered text is written as `sup` and `sub` (units, ions, nuclides);
  code blocks as `pre`/`code`, glossaries as definition lists, footnote marks linked,
  contents pages as lists of links, tagged-PDF headings and alt text as proposals.
- Checks: a second automated check on Claude (shadow by default, enforcement off) with a
  daily canary; number and letter-case checks against the PDF's text layer; speech
  readings for matrices, cases, nuclides and units; MathML alttext (setting, off).
- Heading triage: bold lines ending in `.`, `?` or `:` are required decisions; repeated
  labels in a group count once.
- Model cost (Claude): the untrusted-text notice rides in the cached system block
  (`ANTHROPIC_NOTICE_IN_SYSTEM`, on; the second check and the figure judge keep their own
  prompts). Off by default: lower effort for the equation and figure steps, downscaled
  figure crops, Haiku first for display equations, context only on escalation, per-model
  accept bars, Sonnet as the main model. `eval pipeline --answers` records and replays
  model answers; `--batch` uses Message Batches at half price (docs/eval.md "Model cost").
- Runtime: layout overlaps the model calls, the table detector runs only on pages that
  could hold a table, fewer writes in the spend guard and worker poll, WOFF2 maths font.
