# Content features: shared contracts (2026-10-09)

Status: contracts built on branch `feat/content-contracts` (from `feat/beta-app`), before
about eleven builders work on the features in parallel, one branch each. This page is
what they share: the data model, the flags, the schema, the extension points, the review
tier of every new item, and who owns which files. The product decisions behind it are
taken; this page does not reopen them. The rules of
[beta-architecture.md](beta-architecture.md) ("Rules every work package follows") apply
to every builder, plus these:

1. **Stored results keep loading.** Every new field has a default. A result stored by
   pipeline 0.3.0 loads (`tests/test_content_types.py`) and renders byte for byte the
   same (`tests/test_assemble_golden.py` holds HTML written by the 0.3.0 assembler; a
   builder that changes the output on purpose, such as the student note, regenerates it
   with `UPDATE_GOLDEN=1` and says so in its PR).
2. **Content-free codes.** Notice codes, check codes, reasons, events, source facts and
   progress are codes, ids and numbers, never document text (rule 1).
3. **Rendering reads the stored result only.** `assemble.render_html` must give the same
   file from a stored `DocumentResult` (the review screen re-renders it after every
   decision, `web/review.py rerender_html`). Options and flags change what the steps
   produce, never how a result is written.
4. **Meaning-level review only.** Only what could change what a student reads is a
   required decision; small things are optional notes; nothing else adds review load
   (tiers below).
5. **Documents without the new things are unchanged.** CSS a feature needs goes in through
   `add_style`, only for documents that use it.

## Flags

| Setting (environment) | Default | Off means |
|---|---|---|
| `FEATURE_TABLES` | false | Data tables are still detected and claimed from the text flow, but kept as an image of the table with a required decision ("table not rebuilt"). |
| `FEATURE_LONG_DESCRIPTIONS` | false | Figures get short alt text only: no long description, no chart data table, no colour-only flag. |
| `FEATURE_COLOUR` | false | Coloured text is written in the body colour (as in 0.3.0); no colour runs, no colour-reference notices. |
| `FEATURE_NOTATION_MEMORY` | false | No course notation: no hints, no spoken readings from it, no bold letters in prose turned into maths, no "remember for this course"; the courses pages are hidden. |
| `FEEDBACK_QUESTIONS_ENABLED` | true | No two questions after a download. |
| `EVENTS_ENABLED` | true | No usage events are written. |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_ALLOWED_TENANTS` | unset | Microsoft sign-in is off. It is on only with all three set (`Settings.ms_sign_in_enabled`); half of them set refuses to start in any environment (`check_production`). Tenants are directory ids, comma-separated. |

Links, cross-references and language are always on. The pipeline reads the four feature
flags through `ConversionOptions` (below); the web app reads `Settings` directly.

## Data model (`core/types.py`, pipeline 0.4.0)

### Tables

- `Kind.TABLE`; the step name "table" (`Step`, `ModelCall.step`).
- `TableCell`: `row`, `col` (0-based), `row_span`, `col_span` (>= 1); `text` (untrusted,
  plain: no inline markers); `latex` (the whole cell as LaTeX when it holds maths, words
  in `\text{}`); `mathml` (validated MathML made from `latex`); `header` ("col" labels the
  cells below, "row" the cells to its right, None a data cell); `bbox` (PDF points, page
  frame).
- `TableGrid`: `rows` x `cols` slots (at most 300 x 60, 5,000 cells), `cells`,
  `header_rows` (the top rows, written in `<thead>`), `header_cols` (the leading row-header
  columns), `caption` (only a "Table N" caption found in the PDF, never invented), `is_data`
  (False for a layout table), `source` ("local": built from the PDF's words and lines;
  "model": the table step's answer).
- **Every slot is covered by exactly one cell.** An empty printed cell is a cell with
  `text=""`; a slot no cell covers is a hole, which is a problem.
- `grid_problems(grid)` returns codes in this order, each once: `no_cells`,
  `cell_out_of_bounds`, `cells_overlap`, `grid_hole`, `headers_cover_table`,
  `header_span_into_body`, `header_below_body`, `data_in_header_rows`,
  `data_in_header_cols`. Pure and bounded. Irregular but valid tables (a row header after
  data cells, full-width section rows, nested column headers) are not problems.
- `Element.table`: the local grid layout built (source "local"). `Element.caption`: the
  "Table N" caption (layout copies it to the grid's `caption`).
- `Fix.table = TableFix(grid=...)`: the proposal. A fix with no usable table keeps the
  image.

### Figures

`FigureFix` gains `long_description` (plain text, or None), `data_table` (a `TableGrid`
of a chart's values), `data_values_from_text` (True when every number in the data table
appears in the PDF text inside the figure region; False when some were read from the
image; None without a data table) and `colour_only` (the figure tells things apart by
colour alone).

### Links, colour and language

- `Link(start, end, kind, href, target_id)` on `Element.links`. Offsets are into the
  element's `text` as stored, inline markers included, `start < end`, never inside a
  marker. `kind` "uri": `href` is http, https or mailto, enforced by the model
  (`is_safe_href`; anything else is a validation error, so it cannot reach the output);
  "internal" (a PDF link to a place in this document) and "crossref" ("Figure 2.3",
  "Equation (3)", "Table 1"): `target_id` is the element id.
- `ColourRun(start, end, rgb)` on `Element.colour_runs`: the printed sRGB colour; the
  assembler adjusts it to 4.5:1 on the output background.
- `Element.lang`: a BCP 47 tag, only when the passage differs from the document.
  `DocumentResult.lang_source`: "pdf", "detected", "default" or "user" (the instructor
  confirmed or changed it on the job page).

### Notices

`Notice(code, page, element_id, count=1, review="optional")` on
`DocumentResult.notices`: what the job page lists beyond the review decisions. The code is
content-free (`NOTICE_CODES`; each has a sentence in `web/messages.py NOTICE_MESSAGES`).
`review` places it: "required" and "none" under "Not handled in this file", "optional"
under "Worth checking". **A notice never adds a decision.** When something needs one, the
decision is the `Fix` with the same `element_id`; only fixes count towards
`required_decisions`. One notice per (code, element), or per (code, page) with `count`
for things without an element.

| Code | Review | Comes from |
|---|---|---|
| `table_not_rebuilt` | required (its fix is the decision) | a table fix with that reason |
| `scanned_page` | required (the page's FAILED figure fix, unchanged) | a figure fix with reason `scanned_page` |
| `colour_only` | optional | a figure fix with `colour_only` |
| `long_description_missing` | optional | a chart or diagram fix without `long_description`, with long descriptions on |
| `code_block`, `footnote` | none | layout facts |
| `colour_reference`, `link_scheme_dropped`, `link_text_unclear` | optional | the text pass |
| `lang_detected`, `lang_assumed` | optional (confirm on the job page) | the text pass |

Notices that follow from a fix are derived from the fixes by one function the job-page
work writes (`pipeline/notices.py`); the figures and tables work only set the fix fields
above. The text pass returns its own notices. A new code: add it to `NOTICE_CODES` and
`NOTICE_MESSAGES` in the same change.

### Source facts, progress, options

- `SourceFacts` on `DocumentResult.source_facts` (None until measured): `tagged`,
  `has_lang`, `has_title`, `bookmarks`, `images`, `images_with_alt` (tagged PDFs only),
  `equations`, `equations_flattened` (maths whose text layer loses its 2-D structure),
  `bold_maths_letters` (bold letters in maths the text layer gives as plain), `links`,
  `tables`. Numbers and flags only.
- `ProgressEvent(stage, done, total, counts)`: stages "queued", "layout", "equations",
  "figures", "tables", "assemble", "checks"; `counts` are elements found per kind. Stored
  as JSON on `jobs.progress_json`.
- `ConversionOptions(notation, tables, long_descriptions, colour, notation_memory)`;
  `ConversionOptions.from_settings(settings)` reads the flags.
  `convert_pdf(..., options=None, stage_progress=None)`: None means `from_settings`. The
  run puts the options in force (`pipeline/options.py using`), and any step reads them
  with `options.current()`, pool threads included, with no signature change. Outside a
  run, `current()` is the defaults.

### Notation

`NotationEntry(symbol, canonical, meaning, note, source)`: `symbol` as the instructor
wrote it (<= 64 chars), `canonical` from `core.notation.canonical_symbol`, `meaning` one
of vector, matrix, set, operator, constant, unit, other, `note` (<= 200), `source` "user"
or "review". `canonical_symbol` gives one key for "bold d", `\mathbf{d}` and the styled
letter (double-struck, script, fraktur, arrows and hats as combining marks, Greek names,
a few operators), None when not understood (ask the instructor to write it another way).
`canonical_from_parts(base, variant, accent)` gives the same key from MathML tokens.
Entries are user content: they reach a model only fenced as untrusted context, like
document text.

## Review tiers for the new items

| Item | Tier | How |
|---|---|---|
| Table kept as an image (flag off, or not rebuilt) | required | FAILED, reason `table_not_rebuilt`; the image stays, flagged |
| Table rebuilt, every check passed | none | ACCEPTED |
| Table whose cells fail the bijection with the PDF words after escalation | required | NEEDS_REVIEW with the proposal; rejecting keeps the image |
| Table failing grid agreement, a maths-cell check (as for equations: a symbol or sign) or header sanity after escalation | required | NEEDS_REVIEW |
| Table with only cosmetic differences (whitespace, a cosmetic maths style) | optional | ACCEPTED with a note |
| Long description written | none | |
| Long description the model was unsure of | optional | a note on the figure |
| Complex figure without a long description | optional | notice `long_description_missing` |
| Chart data table, every number in the PDF text | none | |
| Chart data table with values read from the image | required | NEEDS_REVIEW (`data_values_from_text=False`) |
| Figure telling things apart by colour alone | optional | notice `colour_only`; the figure's tier is unchanged |
| Coloured text, colour adjusted for contrast | none | |
| Text referring to colour ("shown in red") | optional | notice `colour_reference` |
| Web or email link kept; internal link; resolved cross-reference | none | |
| PDF link that is not http, https or mailto | optional | notice `link_scheme_dropped`; the text stays |
| Link text that says nothing ("click here") | optional | notice `link_text_unclear` |
| Document language from the PDF; passage marked in another language | none | |
| Document language detected / assumed | optional | notice `lang_detected` / `lang_assumed`, confirm on the job page |
| Notation hint used, bold letter in prose made maths | none | the equation step's own rules decide its tier |
| Transcription contradicting a course entry (bold d read as plain d) | required | the existing `style_mismatch` |
| Scanned page | required (unchanged) | notice `scanned_page` beside its FAILED figure fix |
| Code block, footnote | none | notice |

## Database (`web/db.py`, migration `fa3662c01396`)

- `courses` (id, user_id -> users ON DELETE CASCADE, name, created_at) and
  `notation_entries` (id, course_id -> courses ON DELETE CASCADE, symbol, canonical,
  meaning, note, source, created_at; unique (course_id, canonical)): user content.
- `events` (id, user_id -> users CASCADE, name, job_ref, code, value, created_at indexed):
  content-free by construction (`tests/test_web_content_schema.py` fixes the column set).
  `job_ref` is not a foreign key: a job's events outlive the job.
- `auth_identities` (id, user_id -> users CASCADE, provider, issuer, subject, tenant,
  created_at; unique (provider, issuer, subject)). Google sign-in is unchanged; the
  auth-ms work links identities.
- `jobs.course_id` (-> courses ON DELETE SET NULL, named `fk_jobs_course_id_courses`),
  `jobs.progress_json`, `jobs.result_version` (server default 0; bumped by every review
  write for the stale-tab guard).
- Erasure: `ops.retention.erase_user` (used by `a11y-stem users delete`) deletes jobs
  through `delete_jobs`, then every row with the user's id, without relying on cascades.
  A test lists every table with a user id: a new one must be added to `erase_user`.
- A builder that needs another migration bases it on the head at merge time
  (`tests/test_web_migrations.py` insists on one head) and adds a downgrade and tests.

## Extension points

| Seam | Where | What goes there |
|---|---|---|
| Table step | `pipeline/table_step.py rebuild(element, png, *, client, settings, context_text) -> Fix` | Already scheduled by `run` (cropped, pool thread, context "[this table]", `PROMPT_VERSION` read). Today: FAILED `table_not_rebuilt`. |
| Table HTML | `pipeline/table_html.py render_table(grid, *, id_prefix, math_display)` | The one serialiser, for tables and chart data tables. `needs_header_ids(grid)` says when headers/id are used. |
| Table element | `pipeline/assemble_tables.py render_table_element(element, fix, ctx)` | Rebuilt table in a focusable scroll box, else the image kept. `ctx` is the renderer (`RenderContext`: `id_attr`, `original`, `flag_block`, `add_style`). |
| Inline text | `pipeline/inline_text.py render_inline_text(text, *, element, equation, add_style)` | Links, colour runs. `element` is None when the text is not the element's stored text. |
| Passage attributes | `pipeline/inline_text.py passage_attrs(element)` | ` lang="..."` on `<p>`, `<li>`, `<h1>`-`<h6>`. |
| Feature CSS | `_Renderer.add_style(name, css)` | Only documents that use the feature get its CSS. |
| Options | `pipeline/options.py current()` | Read in any step. |
| Run results | `run._convert`: `notices` and `source_facts` before `DocumentResult(...)`; `stage_progress` on `_Run` | The job-page work emits events and measures facts; the text work adds its pass right after `_with_page_images` and before `_heading_fixes`. |
| Worker | `Worker._converter_kwargs` | Its own block each: `options` (notation), `stage_progress` (job-page). The test fake already accepts both. |
| Messages | `web/messages.py` | `CHECK_MESSAGES`, `REASON_MESSAGES`, `NOTICE_MESSAGES`: one commented block per builder. A new step module goes into `PIPELINE_MODULES` in `tests/test_web_review_messages.py`. |
| Erasure | `ops/retention.py erase_user` | Any new table with a user id. |

## Ownership map

Each builder owns its new modules outright and touches shared files only where listed.
"Owns" means the builder may change it freely; everything else in this repo is changed
only as listed here, or after asking the integrator.

| Builder | Owns (new or wholly theirs) | Shared files it touches (only these parts) |
|---|---|---|
| tables-detect | `pipeline/tables.py` (find_tables, SLANet-plus grid, glyph fill -> `TableGrid` source "local"), the ONNX model fetch and its checksum, its tests | `layout.py` (one call in the page flow that claims table regions and their "Table N" captions, emitting `Kind.TABLE` elements); `pyproject.toml` + `uv.lock` (onnxruntime); `Dockerfile`; `THIRD_PARTY_NOTICES.md` |
| tables-step | `pipeline/table_step.py`, `models/prompts/table.md`, `pipeline/assemble_tables.py`, its tests | `html_checks.py` (table rules in their own functions); `web/messages.py` (its block); `models/pricing.py` only if needed; `run.py` only if scheduling must change |
| tables-eval | `eval/tables.py`, the table gold under `data/gold/`, its tests | `eval/gate.py` (a tables gate), `cli/eval_cli.py` (a subcommand), `docs/eval.md` (its section) |
| tables-review | the Split View card for tables (a new partial template and its view code), its tests | `web/review.py` (`GROUP_SLUGS`: add TABLE, table edits), `web/routes/split.py` and `web/routes/review.py` (the TABLE branch), `web/queue.py`; removes the xfail marker in `tests/test_content_run.py` |
| figures | long descriptions, chart data tables and the colour-only flag in `pipeline/figure_step.py` and `models/prompts/figure.md` | `assemble.py` (`_Renderer.figure` only), `web/messages.py` (its block), the figure card body in `review_split.html` |
| text | `pipeline/text_pass.py` (links, cross-references, language, colour runs and contrast), `pipeline/inline_text.py`, its tests | `run.py` (one call after `_with_page_images`; `lang`, `lang_source`, its notices); `layout.py` only to keep span colours, in its own function; `pyproject.toml` + `uv.lock` if a language detector is needed |
| notation | `web/routes/courses.py`, `web/notation.py`, course templates, notation hints, spoken readings, "remember for this course", its tests | `equation_step.py` (hints from `options.current()`), `speech.py`, `layout.py` (bold single letters in prose, in its own function), `web/worker.py` (`options` block), the upload form's course choice (`web/routes/jobs.py` upload handler, `app.html`), the remember control in `review_split.html` |
| job-page | `pipeline/source_facts.py`, `pipeline/notices.py`, the notices list, statuses, finish line, the language confirm control, its tests | `run.py` (stage events, source facts, notices), `web/worker.py` (`stage_progress` block, `progress_json`), `web/routes/jobs.py` job page, `job.html`, `_job_status.html`, `web/views.py`, `assemble.py` (`_about_section` and the student note only) |
| ux-a | usage events (`web/events.py`) and the two feedback questions after a download, fixes outside the review screen, their tests | `base.html`, `landing.html`, `app.html` (not the course choice), `web/routes/pages.py`, the download route in `web/routes/jobs.py` |
| ux-b | review-screen UX fixes, their tests | `review_split.html`, `review.html`, the review CSS and scripts, `web/routes/split.py` (not the TABLE branch) |
| auth-ms | Microsoft sign-in (Authlib OIDC with tenant check, identities in `auth_identities`), its tests | `web/auth.py`, `login.html` and `_signin.html`, `web/app.py` (OAuth registry), `docs/web.md` sign-in section, deploy settings |

Files nobody edits without the integrator: `core/types.py` (except adding a notice code
to `NOTICE_CODES`), `web/db.py` and `migrations/`, `pipeline/table_html.py`,
`pipeline/safe_html.py`, `pipeline/options.py`, `config.py`, this page.

Merge hazards and order:

- **tables-review merges before or with tables-detect.** A detected table is a required
  decision; until the review screen lists tables, such a job can never be done.
- `layout.py` has three editors (tables-detect, text, notation): each adds its own
  functions and one call, nowhere else. `run.py` has two (text, job-page) plus
  tables-step only if needed. `assemble.py` has two (figures: `figure`; job-page:
  `_about_section`). `review_split.html` has four, each in its own block; `app.css` has
  ux-a and ux-b, each in its own section; `app.html` has ux-a and notation (the course
  choice only).
- `uv.lock` is never merged by hand: take one side, then `uv lock`.
- Suggested order: tables-review with tables-detect, then tables-step, tables-eval,
  figures, text, notation, job-page, ux-a, ux-b, auth-ms. Each rebases on the
  integration branch and runs `ruff check`, `ruff format --check`, `ty check src`, and
  the offline, `postgres` and `browser` suites before it merges.
