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
  ... the document, in reading order ...
  <section id="about-conversion" ...>About this conversion</section>
</main>
</body>
</html>
```

There are no scripts and nothing is loaded from elsewhere: images are `data:` URIs and the
stylesheet is inline. The file opens offline, can be pasted into an LMS page or emailed,
and opening it never sends a request anywhere. `html_checks.static_checks` fails the
`external_resource` rule if that ever changes.

- `lang` comes from the PDF (`/Lang`, else `en`). Anything that is not a language tag
  falls back to `en`, so a crafted PDF cannot inject attributes.
- `<title>` is the PDF title or the first level-1 heading, else "Converted document".
- Every element keeps its pipeline id (`id="p3-e7"`), so a report or the review screen can
  link to `#p3-e7`. An id is used once even if layout produced a duplicate.
- If two fixes name the same element, the first one wins, as in `DocumentResult.fix_for`.

## How each element is written

| Element | Converted | Not converted (ABSTAINED, FAILED, REJECTED, no fix) |
|---|---|---|
| Heading | `<h1>`-`<h6>` from the heading fix | `<p>` with the original text |
| Paragraph | `<p>`, inline equations placed at their markers | (no fix) |
| Inline equation | `<math display="inline">` inside the paragraph | the crop inline, outlined, with "(equation not converted)" |
| Block equation | `<div class="equation"><math display="block">` | `<div class="flagged">`: the crop plus a note |
| Figure | `<figure><img alt="..."><figcaption>` | `<div class="flagged">`: the figure with alt "Figure without a description", plus a note |
| Decorative figure | `<div class="decorative"><img alt="">` (a `<figure>` if it has a caption) | |

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
  where they are handled.

## Inline equations

Layout writes a marker such as `⟦p1-e7⟧` (`core.types.inline_marker`) into the paragraph
text where an inline equation sits, and gives the equation `parent_id` = the paragraph.
The assembler replaces each marker with the equation's MathML, or with the flagged crop.

- Markers are honoured in paragraphs, headings and figure captions.
- An equation whose marker is not found is rendered at its own position as a block, so
  nothing is lost.
- A marker with no matching equation is left as text. That only happens if layout has a
  bug, and leaving it visible makes the bug easy to spot.

## MathML is rebuilt, never pasted

MathML in the output is re-serialised by `assemble.sanitize_mathml`, never inserted as a
string (rule 2 of the architecture doc):

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

Levels come from the heading fixes (WP2), which already repair the outline. The assembler
clamps levels once more so the output never skips down a level (h1 then h3 becomes h1
then h2): a reviewer rejecting a heading can reopen a gap the heading step had closed.
The first heading keeps its level. Rejected headings become paragraphs.

## About this conversion

The last section of `<main>` contains:

- "This file was produced with a11y-stem, which assists with accessibility; review it
  before publishing."
- counts per kind and status (e.g. "Equations (5): 2 accepted, 1 not converted (model
  unsure), ..."),
- how many items are flagged and what the flags look like,
- an optional plain-text `source_note`, such as an OpenStax attribution.

Model cost is not in the HTML: the file is meant for students. Cost is in the result JSON
(`DocumentResult.stats`) and the CLI summary.

Text the tool writes (notes, labels, this section) carries `class="a11y-stem"`. The
`banned_wording` check reads only that text, so course content that legitimately uses
words like "compliance" (beam flexibility in mechanics) is not flagged.

## Styling

About 25 lines of inline CSS:

- system font at 1.125rem with 1.6 line height, a 42rem column, near-black on white
  (about 17:1 contrast);
- images at their printed size (PDF points x 96/72) and never wider than the column;
- block maths keep the browser's own `display: block math` and centring (overriding
  `display` on `<math>` breaks MathML layout);
- a visible `:focus-visible` outline and a print rule that keeps flagged boxes together.

There is no dark mode yet.

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
| `html.math_display` | `math_missing_display` (warning) |
| `html.self_contained` | `external_resource` |
| `html.wording` | `banned_wording` |

`html_checks.axe_checks(html)` runs the vendored axe-core 4.14.0
(`src/a11y_stem/pipeline/vendor/README.md`) in headless Chromium with the `wcag2a`,
`wcag2aa` and `wcag21aa` rules. The page runs with networking off. Each violation
becomes `Check(name="axe.<rule>", code="<rule>")`, an error for serious or critical
impact and a warning otherwise; only rule ids and impact leave the browser, never page
content. Without Playwright or its Chromium it returns one
`Check(name="axe", passed=True, severity="warning", code="skipped")`. A run takes about
a second.

`DocumentResult` has no field for these yet, so `convert_pdf` returns them on
`run.PipelineOutput.checks` (a subclass of `ConversionOutput`). The CLI prints them and
writes them to `--report`. Anyone holding only a `DocumentResult` can recompute them:
`static_checks(result.html)` is deterministic and fast.

## How `convert_pdf` fills it in

`pipeline/run.py`:

1. `layout.analyze` (raises `LayoutError` for unreadable, encrypted or too-long PDFs).
2. `heading_step.heading_fixes` on all elements. If it raises, every heading becomes
   FAILED `internal_error` and renders as a paragraph.
3. Page by page, on the calling thread (PyMuPDF is not thread-safe): crop each equation
   and figure from its own bbox (inline equations included), then queue its step on a
   `ThreadPoolExecutor(concurrency)`. Cropping page N+1 overlaps the model calls for
   page N.
4. Each step gets ~600 characters of neighbouring text as untrusted `context_text`: for an
   inline equation, its paragraph around the marker (`[this equation]` marks the spot);
   otherwise the end of the previous text element and the start of the next. Other
   markers become `[equation]`.
5. A step that raises becomes FAILED `internal_error`; a crop that fails, FAILED
   `crop_error`. Only the exception type is logged, never its message (it may quote the
   document).
6. The first `BudgetExceeded` (or a step returning reason `budget_<scope>`) stops new model
   work: queued steps are cancelled, and everything not yet processed becomes FAILED
   `budget_<scope>` with its crop kept. Calls a step made before the refusal were billed,
   so a recording wrapper around the client keeps them on the FAILED fix.
7. `progress(pages_done, pages_total)` runs on the calling thread when all of a page's
   steps have finished. An exception from it aborts the run.
8. `render_html`, then `static_checks`.

Stats: document `latency_ms` is wall-clock time for the whole run; page `latency_ms` is
the summed time inside that page's steps (pages overlap in the pool, so per-page wall time
would mislead). `prompt_versions` are the versions the calls actually used, else the
step modules' `PROMPT_VERSION`. Tracer spans (`document`, `headings`, `page`, `step`,
`assemble`) carry ids, pages, models, costs, statuses and reason codes only.

## CLI

```
a11y-stem convert FILE.pdf --out OUT.html (--max-usd X | --fake) [--pages 1,3] [--report OUT.json] [--axe]
```

`--max-usd` caps this run with an in-memory spend guard; `MAX_USD_PER_JOB` and
`MAX_MODEL_CALLS_PER_JOB` cap it too. `--fake` uses the offline fake client. Progress
("page 1/1 done") goes to stderr; the summary on stdout is content-free. A run with
`--fake --report notes.json --axe` (fake layout and steps, as in the CLI tests):

```
Pages:       1
Elements:    4 (1 headings, 1 paragraphs, 2 equations, 0 figures)
Fixes:
  kind        accepted
  heading            1
  equation           2
Model calls: 2
Cost:        $0.0044 (fake client, nothing billed)
Time:        0.0 s
Checks:      static 10/10 passed
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

## Engineering limits (input for the known-limits copy)

TODO(Harish): turn these into the public known-limits copy.

- A very wide display equation widens the page instead of scrolling inside its own box (a
  scroll box would need `tabindex` to be keyboard-reachable on every equation).
- NEEDS_REVIEW items look like converted ones in an export made before review.
- Tables are not rebuilt; a table that layout reports as a figure becomes an image with
  alt text.
- If one equation marker appears twice, the equation is rendered twice (the second
  without an id).
