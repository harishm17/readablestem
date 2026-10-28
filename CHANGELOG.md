# Changelog

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
