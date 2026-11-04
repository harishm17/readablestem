# Changelog

## Unreleased: design polish (branch design/d6-polish)

- Fixes from the Impeccable audit and critique of the redesign: paragraphs, hints and list items are capped at 75ch;
  Sign out, document-row links and check boxes meet the 24px target floor (header buttons 44px); the review screen's
  desk colour is lighter so its links have 7.3:1; the current step on the review screen keeps the two-tone focus
  ring; the three fonts the first view needs are preloaded (no layout shift on a phone); buttons and the page tools'
  forms never overflow at 320px or 200% zoom; the file chooser button is styled as a secondary button; the
  comparison table keeps its topic column in view and tints the ReadableSTEM column; a few legacy radii and an
  off-token colour moved onto the tokens. The focus-style test reads the new tokens, light and dark.

## Unreleased: comparison table and showcase on the tokens (branch design/d4-compare-showcase)

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

## Unreleased: landing page and proof strip (branch design/d2-landing)

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

## Unreleased: design system (branch design/d1-tokens-components)

- Site design tokens and components (`static/tokens.css`, `static/app.css`, `DESIGN.md`,
  `PRODUCT.md`): self-hosted Source Sans 3, Source Serif 4 and Source Code Pro, one accent
  (`#1c46ad`), all text at least 7:1 in light and in dark (follows the browser's setting),
  nothing under 14px, a 4px grid, 4px and 8px radii (no pills), a 75rem page and a 68ch reading
  column on pages people read. The header has a Sign in button and folds into a Menu disclosure on
  a narrow screen; the footer is four columns (Product, Trust, Guides, Source) with the same
  links as before. Forced-colours and reduced-motion rules cover the new components. The app's
  side-stripe notices and cards became full borders. `/style` (noindex, not in the sitemap) shows
  every component. Docs: `docs/site.md` "Design system", `static/vendor/README.md`.
## Unreleased: keyboard walk and draft ACR

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
## Unreleased: development baseline (branch eval/dev-baseline-2026-11-03)

- Measurement report `docs/eval-dev-baseline-2026-11-03.md`: ReadableSTEM at pipeline 0.5.3 with production flags on the 25
  real-world development papers, against the recorded Opus (6 papers) and Haiku (25) chatbot answers. One
  run, $0.1186 of model spend; no code, prompt or setting changed.
## Unreleased: figures spot check

- `eval figures-spotcheck` (`eval/figures_spotcheck.py`, docs/eval.md "Figure spot check"): offline,
  no model call. From a figures run with long descriptions on, writes a blind sheet of every
  long-judge fail plus a seeded sample of 10 passes for a person to label, and scores the labels
  against a key (judge fails confirmed, sampled passes the person would fail, each with n and a
  Wilson interval). Tests: `tests/test_eval_figures_spotcheck.py`.
## Unreleased: fair queue

- The worker takes queued jobs round-robin by person (`web/fair_queue.py`, docs/web.md "Worker"):
  fewest attempts first as before, then the owner served longest ago, then oldest. Someone with ten
  queued documents no longer keeps another person's single one waiting behind all ten; a person
  alone is served in the same order as before. The job page's "documents ahead of yours" replays the
  same order. Tests: `tests/test_web_fair_queue.py`, one Postgres test in `tests/test_web_postgres.py`.
## Unreleased: Word and EPUB in "Download all" (branch export/w3-download-all-formats)

- "Download all" (My documents and the course page) and "Combine parts" offer Word (.docx, where pandoc is
  installed) and EPUB as well as HTML: `?format=docx` or `?format=epub` on `/app/download`,
  `/courses/{id}/download` and `/jobs/{id}/combine`. Same wording and pattern as the single downloads
  ("Other file types"). A Word zip holds at most 10 documents (each is a pandoc run); a document that cannot
  be made in the file type is left out and counted in `contents.txt`. Each document counts as downloaded in
  that file type, so the review record lists it. The HTML zip no longer restarts the deletion clock of ready
  documents it left out for the 25-document cap. docs/output.md, docs/web.md, docs/security.md.

## Unreleased: slide decks (branch layout/beamer-slides)

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
## Unreleased: redirects built from owned rows (branch sec/redirects-from-owned-job)

- Every redirect in the review, edit, bulk, course, job-settings, feedback and job routes takes its job or course id
  from the row the ownership check returned, never from the request's path or form. `write_review` and
  `write_structure` return that id, and the `ReviewStale` and `ReviewInvalid` they raise carry it. The item a form
  names is looked up in the stored result before it is put in an address. No change for a visitor; it removes the
  CodeQL `py/url-redirection` alerts at the root. Test: `tests/test_web_redirects_owned.py`.

## Unreleased: symbol inventory (branch hw/symbol-inventory)

- Symbols screen (`GET/POST /jobs/{id}/symbols`, `web/inventory.py`, `routes/inventory.py`): for a document
  with handwritten equations, every symbol read in them grouped with the ones that look alike, with counts,
  small pictures and places; one symbol can be changed to another of its family everywhere at once, with
  checkboxes to leave places out. Applied as one write through the edit checks per equation, never an accept
  (each equation stays a required decision), one `relabel` review record per equation shown as one row in the
  review record, one Undo. Linked from the review screen and the list view. docs/web.md, docs/eval-handwriting.md.
## Unreleased: Word and EPUB downloads (branch export/word-and-buttons)

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
## Unreleased: wording and a page for accessibility offices (branch trust/wording-and-offices)

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
## Unreleased: page witness, why it changed nothing (branch eq/witness-diagnosis)

- Page witness (off: `FEATURE_PAGE_WITNESS`): when the crop's reading and the witness's tie on the letters, digits
  and Greek letters of the glyphs (23 of the 24 disagreements of the E1 dev run did, so the crop's reading always
  stayed), the slashes in the glyphs now break the tie (`witness_reconcile.marks_dice`): a written `-2/3` beats a
  drawn fraction, and the other way round. Both readings are still shown and the formula is still a required
  decision. Re-scored on the recorded E1 answers (12 dev papers, 1,096 gold, $0): exact 0.753 to 0.755, silent
  wrong 8.58 per 100 gold and decisions a page 4.35 unchanged. `scripts/eq_dev_run.py` has an offline arm
  `witness-sonnet-r2` for it and passes the two flags `realworld_run` gained. docs/eval-equation-round5.md.

## Unreleased: page witness dev run (branch eq/e1-witness-dev)

- `scripts/eq_dev_run.py` (new): the page witness on or off over twelve development papers, crop answers
  replayed, with a report and a pair-by-pair `witness` summary; `scripts/realworld_run.py rs` takes
  `--section-workers` and `--concurrency`. Result in docs/eval-equation-round5.md: no accuracy or
  detection gain with Sonnet as the witness, silent errors down 2.1 per 100 gold, one more decision a page,
  +2.4 cents a page. The flag stays off.
- Fix: the page witness's calls had the client's 60 s timeout, so a dense page timed out and was charged
  its whole reservation; `PAGE_WITNESS_TIMEOUT_S` (default 240) is set on the request.
- Fix: `eval pipeline` left the witness's cost out of an item's `cost_usd`; items now carry `page_witness`
  and `page_witness_cost_usd`, and the cost block `page_witness_usd` and `page_witness_per_pdf_page_usd`.
## Unreleased: calibrated flag policy (branch eq/flag-policy)

- Flag policy (off: `FLAG_POLICY`): `pipeline/flag_policy.py` turns an accepted equation that nobody is asked
  about into a required decision when a small logistic model over content-free features (second reading, notes,
  extension past the span, size, notation classes) puts it with the readings that turned out wrong. It only adds
  decisions, makes no model call, and its weights and threshold are constants fitted on the development splits
  (`flag-policy-1`; `eval/flag_features.py` dumps the features of recorded runs and fits them at $0). New check
  `equation.flag_policy`, code `flag_policy`, with a review sentence. `scripts/realworld_run.py rs` takes
  `--flag-policy`. Replayed on the recorded runs: on fresh2 to fresh4 (validation, 45 papers, 5,018 gold) silent wrong
  8.83 to 7.77 per 100 gold, flag recall 0.29 to 0.38, decisions a page 3.55 to 3.92; the goal (silent 4 per 100 at
  4 decisions a page) is not reachable with these features. docs/eval-flag-policy.md, docs/steps.md.
## Unreleased: both readings on the review card (branch eq/review-both-readings)

- Review card: a typed equation the page witness read differently (`witness_mismatch`,
  `witness_extent_differs`) shows "The other reading" under the proposal: its formula, how it is read
  aloud, why the item is asked, **Use the other reading**, and the symbols that differ as the same
  chips handwritten cards have (`web/reading_chips.py`, built on `reading_alternatives.from_reading`).
  Ordinary edits (checks, Undo), none preselected, audited as `page_witness_choice`. Nothing shows with
  `FEATURE_PAGE_WITNESS` off. Documented in docs/web.md and docs/steps.md.
## Unreleased: glyph checks for equations (branch eq/bag-extended-extent)

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

## Unreleased: page witness (branch eq/page-witness)

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
## Unreleased: pen strokes check handwritten vector pages (branch hw/stroke-checks)

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

## Unreleased: agreement oracle on the recorded chatbot answers (branch eq/agreement-oracle)

- `scripts/eq_oracle_agreement.py` and a D2 section of docs/eval-equation-diagnostics.md: how often
  a second whole-page reading (the recorded Opus chatbot answers) is right where ours is wrong, how
  often our reading is right when the two agree under `math_canonical`, how well disagreement
  flags our wrong readings, and how many of our undetected equations it found. Measurement only,
  offline, no change to the product.

## Unreleased: a second reader's reading and choices on the review card (branch hw/reconcile-and-chips)

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
## Unreleased: a local handwriting reader (branch hw/local-reader)

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
## Unreleased: page type reads per-sample pen ink (branch hw/page-type-dotted-ink)

- `page_type` now calls vector handwriting drawn as one small filled disc per pen sample
  `handwritten` (it was `vector_no_text`). Its ink analysis uses `ink_strokes`'s own rule for a
  stroke made of samples (`sample_run_end`, `dot_grid_members`) instead of a second one. Dot-grid
  paper, scatter-plot markers and stippled shading are still not handwriting. On the 846-page
  labelled corpus no page changes its kind. Documented in docs/layout.md.

## Unreleased: other readings of a formula as choices on its symbols (branch hw/schema-prompt)

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
## Unreleased: a vector-ink handwriting dev set (branch hw/vector-devset)

- `scripts/handwriting_accuracy.py build-vector` and `eval/handwriting_vector.py` build a dev set of
  MathWriting expressions drawn into PDFs as vector ink (stroked paths, filled per-sample shapes,
  filled pen outlines), on plain and ruled paper, some with a typed header, with gold LaTeX per
  expression and a manifest with a rebuild hash. The pages stay in `~/.cache/readablestem`; the
  ink is CC BY-NC-SA. No model is called. Documented in docs/eval-handwriting.md.

## Unreleased: the MathML shape for screen readers (branch mathml/screenreader-shape)

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

## Unreleased: chemistry species check against the text layer (branch chem/species-check)

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
## Unreleased: chemistry spans in the layout (branch chem/layout-spans)

- Chemistry lists are cut into their items: `CO3 2−, F−` is two inline equations and
  `(a) A ⇌ B; (b) C ⇌ D` one inline equation per reaction (without the list marker), not one span
  or one display line. Half-reaction labels (`anode (oxidation):`) stay with their reaction when
  they are set far from it. `E°cell` and `E°cathode` are whole (the degree sign was read inside the
  lowered label and cut the equation in two). A label over or under an arrow, of any length and
  with a long arrow, is part of the reaction (`chem.Word.over_arrow`). On the 143 PDFs of
  `scripts/layout_identity.py` 135 are identical and the 8 that differ are chemistry sections;
  details and the replay in [docs/chemistry.md](docs/chemistry.md) ("Spans") and
  [docs/layout.md](docs/layout.md). Tests: `tests/test_layout_chemistry.py`.
## Unreleased: speech under screen-reader engines (branch sr/speech-oracle)

- `scripts/screenreader_speech.py` and `eval/speech_oracle.py` run any set's MathML (ours and the
  gold's: OpenStax, authored, chemistry, handwriting, arXiv) through MathCAT 0.7.7 (NVDA 2026.1,
  JAWS; ClearSpeak and SimpleSpeak) and Speech Rule Engine 4.1.5 (MathJax, ChromeVox). Per
  equation: the spoken strings, whether MathCAT recognised chemistry, a word diff. Per set: the
  share of ours that sounds like the gold under each engine, with an interval, and the worst
  classes. `eval pipeline --speech` adds the aggregates to an equations or document report.
  `scripts/speech_oracles/build.sh` builds the engines (rustup into `~/.cargo`, no sudo); without
  them everything skips with a message. Offline, $0. See `docs/eval.md`, `docs/chemistry.md`.
## Unreleased: `\ce` subset and `\ce` on every LaTeX path (branch chem/ce-subset)

- The `\ce{...}` subset (`pipeline/chem.py`) now reads a condition in a state symbol
  (`(aq, 0.10 M)`), the excited state of a nuclide (`^{60}_{28}Ni^*`) and gas and precipitate marks (`CO2 ^`, `AgCl v`);
  every other construct is still refused.
- `\ce` is accepted on every path that takes LaTeX: the review screen's edit box (equation and
  table cell), scanned pages and their second reading, table cells' HTML and page-math spans, all
  through `equation_step.read_latex`. A `\ce` outside the subset fails the LaTeX check with the new
  code `unsupported_ce` (was `unknown_command`) and the edit box says so in words. See
  [docs/chemistry.md](docs/chemistry.md).
## Unreleased: chemistry speech fixes

- The review screen's "read aloud roughly as" text (and the checks that compare readings) now
  reads a state with conditions, `(aq, 0.10 M)`, as "aqueous, 0.10 molar"; a redox couple
  `Cu²⁺/Cu` as "C u 2 plus slash C u" (it said "divided by"); the arrow in its own cell of a
  half-reaction table as "reacts to form" (it said "right arrow" with the species cut off from it);
  an excited nuclide `⁶⁰₂₈Ni*` as "nickel 60, atomic number 28, excited state"; and units after a
  decimal in a chemistry row consistently (`0.000 V − 0.0592 V` both "volts"; `mol L⁻¹ s⁻¹` as one unit).
  Speech for sets that are not chemistry is unchanged (`docs/chemistry.md`, "Reading it aloud").
## Unreleased: handwriting error classifier (branch hw/error-analysis)

- `scripts/handwriting_errors.py` classifies every handwriting error in the recorded answers: kind
  (case, Greek against Latin, other letter, digit against letter, `z` against `2`, `x` against `×`,
  minus against dash, lost or added script, fraction, root, structure, left out), edit distance in
  symbols, and whether the product's speech code reads the answer and the label alike. Text table
  plus JSON with examples per arm; tests in `tests/test_handwriting_errors.py`; usage in
  docs/eval-handwriting.md. It calls no model and changes no product behaviour.
## Unreleased: vector ink to strokes (branch hw/ink-strokes)

- New `pipeline/ink_strokes.py`: reads a PDF page's vector ink into ordered strokes (stroked
  paths, one filled shape per pen sample, filled outlines), keeps colour and paint order, drops
  page furniture (ruled and squared paper, dot grids, dashed lines, frames, backgrounds), groups
  strokes into lines and regions, and writes each region as normalised point lists or InkML.
  It uses `page_type`'s caps and `InkBudget` and groups with a grid hash (near linear), so a
  hostile page cannot stall a worker. Nothing in the pipeline calls it yet, so conversions are
  unchanged. What is read and the limits:
  [docs/layout.md](docs/layout.md), "Vector ink and strokes" (`tests/test_ink_strokes.py`).
## Unreleased: text layers that do not cover the page, and the maths rule as a guard (branch fix/handwriting-skip-and-flags)

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
## Unreleased: eval runs keep their recordings; chemistry report fields (branch chem/eval-metric)

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
## Unreleased: a kit for collecting hand-copied OpenStax examples (branch hw/copy-set-kit)

- `scripts/handwriting_copy_set.py` and `data/handwriting-copy/README.md`: a kit for a small set of
  real iPad handwriting whose answer key nobody types. It draws OpenStax worked examples with a
  written, seeded rule behind a CC BY licence gate (the committed table, then the live archive and CMS
  records), writes each volunteer a brief, matches the PDFs they return to passages (counts only in
  reports; a folder inside the repository is refused), builds the gold from the passage's published
  MathML, and writes a manifest in which a page is published only if its writer's yes is recorded; the
  other pages are counts. A person must look at every page (`review`) before it can be published; published PDFs are rebuilt with page content only and checked, failing closed; unpublished pages can be purged. Tests in
  `tests/test_handwriting_copy_set.py`; no page has been collected yet, so there is no result.
## Unreleased: reader B adapters for handwritten expressions (branch hw/math-readers)

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
## Unreleased: handwriting harness for private handwritten notes

- `scripts/handwriting_own.py`: intake of PDFs from a folder outside the repo, a hash-based dev and
  held-out split registered privately, a blank answer-key form served on 127.0.0.1
  (`scripts/handwriting_gold_form.html`, no model draft) and a `run` command capped by `--max-usd`
  (at most 2.00 in total across restarts). `run` sends the PDFs to the model provider. Every command
  refuses a path inside a git repository; `.gitignore` guards the usual private folder names.
  Use: [docs/eval-handwriting.md](docs/eval-handwriting.md), "Your own handwritten notes".
## Unreleased: canonical comparator for two readings (branch eq/math-canonical)

- `pipeline/math_canonical.py`: one comparator for "is this the same formula" (tree, notation-neutral,
  spoken-equal, symbols) with a reason when it is not. The registered scorer scripts are untouched;
  tests check the library agrees with them. `tests/test_realworld_registration.py` runs
  `realworld_register.py --check` for every registered set. No product behaviour changes yet
  ([docs/steps.md](docs/steps.md), "Comparing two readings").
## Unreleased

- Equation diagnostics on the recorded real-world runs, no model and no cost: `scripts/eq_diag_tables.py` (why maths in
  table cells is missed), `scripts/eq_diag_glyphs.py` (glyph evidence of undetected and span-wrong equations),
  `scripts/eq_diag_unflagged.py` (why wrong equations with other symbols were not flagged). Counts and sample sizes in
  [docs/eval-equation-diagnostics.md](docs/eval-equation-diagnostics.md). No product behaviour changes.
## Unreleased: finished steps are kept when a configuration error ends the run

- When a configuration error (a model that is not set up) ended a conversion while other
  steps were still being answered, those steps were not kept for "Try again" if the error
  reached the scheduler first; they are now (`pipeline/run.py`, `_Run.collect`). The tests
  for this and for the worker and child-process suites no longer depend on timing luck, and
  the end-to-end content test renders from the stored result the way the review screen does
  (page markers included).

## Unreleased: conversions run in a child process (branch fix/worker-child-process)

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
## Unreleased: faster CI

- The pytest job is split into four shards balanced by recorded durations (`--shard i/4`,
  `tests/shard_durations.json`), with a check that every test ran in exactly one shard. Python
  3.14 runs the full suite on main, nightly and on dependency or workflow changes, and a smoke
  (ruff, ty, import of every module, one shard in eight) on other pull requests. The browser job
  runs in two shards of three xdist workers each and caches Chromium and axe-core; the axe-core download is written
  atomically. Pull request runs cancel when superseded, main runs do not. Job names changed:
  `ruff + ty + pytest (Python 3.x)` is now `ruff + ty (Python 3.x)` plus `pytest (...)` shards; a
  `CI passed` job summarises them. See [docs/development.md](docs/development.md), "CI".

## Unreleased: dependency and CI hardening

- Locked dependencies upgraded for known advisories: pillow 12.3.0 to 13.0.0 (floor now `>=13.0`),
  certifi to 2026.10.23, joserfc to 1.7.7.
- CI: a `dependency-audit` job (`pip-audit` over the exported lock), a CodeQL workflow (Python and
  GitHub Actions; push to main, pull requests, weekly), `alembic upgrade head` plus `alembic check` in
  the `postgres` job, which now runs Postgres 18 (the backup image's major). Zero collected tests in the
  `postgres` and `browser` jobs now fails them. The main `pytest` step runs in parallel (`-n auto`).
- `make test-fast`; pytest-xdist and pytest-timeout in the dev group. The review-UX browser tests wait
  with Playwright's `expect` and have a 120 s per-test timeout.
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
## Unreleased: security hardening

- The `/m/` analytics relay is rate limited per address (`analytics`, 300 per 10 minutes) and no
  longer forwards the visitor's address to PostHog.
- Production refuses `ANALYTICS_ENABLED=true` until the privacy notice names PostHog and no
  longer says there are no analytics. The notice text is unchanged.
- Production requires a `CRON_TOKEN` of at least 32 characters.
- A one-time `proxy_hops_unexpected` log line when `X-Forwarded-For` has more entries than
  `TRUSTED_PROXY_HOPS + 1`; `launch-check` flags `TRUSTED_PROXY_HOPS=0` in production.
- `SESSION_SECRET_PREVIOUS`: earlier session secrets accepted for verification only, so the
  secret can be rotated without signing everyone out ([docs/security.md](docs/security.md)).
## Unreleased: review at scale, finish notice, guides, caching

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
## Unreleased

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
### Figure gold v2 and a draft pre-registration for long descriptions

- `data/gold-figures-v2/`: 50 figures (14 data charts, 14 diagrams, 10 photos and illustrations, 12 chemistry
  structures) from CC BY OpenStax books, drawn by a seeded rule with licence and exclusion gates
  (`gold/figures_v2.py`, `scripts/figures_v2_pool.py`, `scripts/figures_v2_build.py`); each has its source,
  licence and a reference long description, and 4 of the charts a reference data table. 45 figures come from
  retired editions ([decisions/0001-scope.md](docs/decisions/0001-scope.md), question 2). No model was run.
- `gold/figures_v2_arxiv.py` reads plotted data from pgfplots TeX; 100 seeded CC BY papers held no usable
  chart. `gold/figures_v2_table.py` scores table cells against a reference table.
- [docs/eval-figures-long.md](docs/eval-figures-long.md): the set, and a draft pre-registration (arms, bars,
  budget cap, judge calibration with human labels) awaiting sign-off.
### EPUB export

- `readablestem export epub FILE.html OUT.epub`: an EPUB 3 from the finished HTML (`pipeline/export_epub.py`): XHTML with native MathML, images as files with their alt text, a contents list from the headings, a page list from page markers when present, language, title and accessibility metadata. No `conformsTo`; the summary makes no conformance claim; a feature is claimed only when every item has it. Scripts, frames, forms, remote pictures and `url()` styles are not carried. EPUBCheck and DAISY Ace run on it in `.github/workflows/epub.yml` and locally when installed ([docs/output.md](docs/output.md), "The EPUB export"). No route or button yet.
### Documentation

- `docs/screen-reader-test.md`: a 15-minute VoiceOver (Safari) protocol for the demo, a converted file and the review screen, with a results table that maps to WCAG 2.2 criteria.
### Microsoft sign-in configuration

- `.railway/railway.ts` declares `MS_CLIENT_ID`, `MS_CLIENT_SECRET` and `MS_ALLOWED_TENANTS` on the web service as `preserve()`, so values set with `railway variable set` survive `railway config apply`. `docs/deploy-railway.md` has a "Microsoft sign-in on Railway" section with the app registration, the secret's expiry, the allowed directory ids and the exact commands.
- `readablestem ops launch-check` also lists `MS_ALLOWED_TENANTS` set without a client id and secret (the sign-in would be quietly off). A client id or secret alone was already refused.

### Layout structure: bookmarks, numbered and run-in headings, joins (2026-11-02, branch fix/layout-structure)

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
### Navigation in the output, and combining the parts of a long source (branch feat/output-navigation)

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
### Monitoring and recovery

- The daily backup keeps a dump only after it has been proved: gzip reads it back, it ends with pg_dump's completion line, it restores with errors fatal, every live table is in the restore and `alembic_version` matches. A failure prints `backup_failed reason=...`, removes the file and fails the run. The command is a readable bash script (`set -euo pipefail`) with tests (`tests/test_railway_backup_script.py`). `railway config apply` is needed to put it live.
- Alerts without a new vendor: `ops_heartbeat` (migration `e1a7c3b95d24`), written by the backup and by `readablestem ops cron`; public content-free `GET /healthz/ops` (503 naming the late task); `uptime.yml` now checks `/readyz` every 5 minutes and `/healthz/ops` every 3 hours. GitHub disables scheduled workflows after 60 days with no commit to a public repository.
- Erasing an account writes a content-free line to `erasure_ledger` (hash of the user id, time); `readablestem ops replay-erasures [--ledger-csv FILE]` erases again whatever a restored backup brought back.
- Per-person daily spend: `quota_days.spent_usd`, shown by pseudonym on `/admin`.
- The day's cap on new accounts logs `signup_cap_reached` once a day and shows a notice on `/admin`.
- Database pool: 5 plus 5 connections per process (was 3 plus 2).

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
