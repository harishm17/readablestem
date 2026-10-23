# Registration: handwriting, measured once (2026-10-21)

Written and hashed before any model call (`REGISTRATION.sha256`). Nothing below changes after a
number is seen. The write-up, with the results, is [docs/eval-handwriting.md](../../docs/eval-handwriting.md).

## The question

[docs/eval-image-pages.md](../../docs/eval-image-pages.md) says: "Handwriting is measured only by
eye on four pages. There is no handwriting answer key." This builds one and measures the image
page pass (ADR 0007) on it once, so that the owner can decide whether the app may advertise
handwriting. "Advertise" means any wording a user can read that says or implies that handwritten
pages are converted (the landing page, the about note, the upload option's label). The decision
rule is below, fixed now.

## The set

**Source.** Google's MathWriting dataset (Gervais, Fadeeva and Maksai, 2024, arXiv:2404.10690; Google LLC;
https://github.com/google-research/google-research/tree/master/mathwriting), archive
`mathwriting-2024.tgz`, SHA-256 `f1977f120b0efa8abf715ac544b0596b213b099133da99b5e64f946730929269`.
Human ink only (`inkCreationMethod` = `human`; the dataset's synthetic ink is not used): people
shown a rendered LaTeX expression and asked to copy it on a touchscreen or with a digital pen, so
that the label (`normalizedLabel`) is what the writer was asked to write. **Licence: CC BY-NC-SA
4.0.** Because of NC and SA the data is not committed and not redistributed here: the repository
holds the builder (`src/readablestem/eval/handwriting_set.py`, `scripts/handwriting_eval.py`) and
`data/handwriting/manifest.json` (sample identifiers, SHA-256 of every ink file, of every page
picture and of every gold file). The set is fetched and rebuilt locally with `fetch`, `build` and
`verify`; `verify` rebuilds from the archive and compares byte for byte with the manifest. The
results page quotes a small number of labels for illustration, with this attribution. This is an
evaluation of a free accessibility tool and no model is trained on the data; the owner should
confirm that this use is "non-commercial" for the purposes of the licence before repeating it.

**Other sources considered and not used.** CROHME (ICDAR 2019 package, TC11): its site says "freely
available only for research purpose without any commercial use" with no licence file I could find,
so redistribution terms are unclear and nothing is gained over MathWriting for a first
measurement. No openly licensed scanned handwritten lecture notes with a typed transcript were
found (searched 2026-10-21). So there is one source; the per-source breakdown is by the dataset's
own splits (below) and by strata of the set.

**Parts (dev and held-out are different data).** Dev is expressions from MathWriting's `valid`
split, held-out from its `test` split. The dataset keeps every expression in exactly one split, so
the two share no expression. In each split the ink files are put in a seeded order (SHA-256 of
`handwriting-2026-10-21`, the split name and the sample id) and the first 500 that pass the label
and conversion filters below make the pool; pages are composed from the pool in that order.

**Filters.** A label is used when: it is at most 150 characters; it has no `\text`, `\mbox` or
`\hbox`; it has no run of three or more plain letters outside LaTeX commands (a writer who was
shown `sqrt(2)` wrote four letters); it converts to MathML by the pipeline's own LaTeX path
(`equation_step.clean_latex`, `validate.safe_latex_to_mathml`, as the real-world gold does) with
no unknown command; it has 3 to 40 MathML tokens (single symbols are left out); the ink has at
least two strokes; no two expressions share a label. An expression is also eligible to be set
inline when it has no sum, product, integral, limit or environment, at most 16 tokens, is at least
1.4 times wider than tall, and is at most 3.4 letter-heights tall.

**Pages.** US Letter at 300 dpi, two pages to a document (a unit is a two-page PDF). Per page:
up to seven blocks, each either (55%) a short typed sentence with one or two handwritten
expressions set inside it, in a serif font ("Since [ink] we obtain [ink] directly."), or (45%) a
handwritten display expression on a line of its own, centred or after a typed label ("3.",
"(b)"). The first page of a unit has a typed heading. The ink is drawn at pen width (4 to 8 px) in
black, dark blue or graphite, with the ink scaled so that the letters are 44 to 56 px high inline
and 62 to 86 px displayed. One unit in three is on ruled paper. The page is then degraded by the
recipe of `eval/scan.py` (resampled to 150 to 200 dpi, rotated by up to 1.5 degrees, shaded, tinted,
grain, blur, JPEG at quality 55 to 75; scan seed 20261021 plus the unit's number) and set in an
image-only PDF with no text layer. The gold is one record per expression in reading order, in the
document task's own format: `display` is `block` for an expression on its own line and `inline` for
one inside a sentence, the LaTeX is the label, the MathML is made by the pipeline's own path.

| | Dev (MathWriting `valid`) | Held-out (MathWriting `test`) |
|---|---|---|
| Units / PDF pages | 5 / 10 | 12 / 24 |
| Gold equations | 75 (32 display, 43 inline) | 205 (56 display, 149 inline) |
| Tokens 3-7 / 8-15 / 16+ | 42 / 32 / 1 | 51 / 141 / 13 |
| Ink: black / blue / graphite | 15 / 46 / 14 | 72 / 101 / 32 |
| Plain / ruled paper | 57 / 18 | 120 / 85 |

Manifest: `data/handwriting/manifest.json`, SHA-256
`81dcbe862ec01e10ebf6fdddfac8276d2800d1fe422e612c93af139b6d9f8676`.

**What the set is not.** The ink is MathWriting's: people copying a rendered expression on a
touchscreen, often fast and sloppy, so that some of it does not show what its label says (a
writer's scribble, a missing stroke). The label is the writer's task, not a reading of the ink, so
a reader that reads the ink faithfully can be marked wrong. The ink is not paper handwriting
photographed or scanned; it is drawn on a page and then degraded like a scan. The words around
the inline expressions are typed, not handwritten (no open handwritten text with transcripts was
available), and the prose is not scored. There is no handwritten prose, no diagram, no table, no
crossing-out, no multi-line derivation. Claude may have seen MathWriting in training; that cannot
be checked.

## What is run, once

- Pipeline: the production pipeline at this branch's base (`origin/main`), `FEATURE_IMAGE_PAGES=true`,
  page reader `claude-opus-5-5` (`IMAGE_PAGES_MODEL`), effort at its default (`low`), 200 dpi, the
  equation step with `claude-opus-5-5` main and `claude-haiku-5-5` cheap, no second automated
  check (`CLAUDE_SECOND_CHECK=off`): the settings of the held-out scans in
  docs/eval-image-pages.md. The prompt (`models/prompts/image_page.md`) is hashed here and is not
  changed.
- Driver: `scripts/handwriting_eval.py run --part dev|heldout --max-usd N`, the document task,
  one section (unit) at a time (`--section-workers 1`, so a cap stops at most one unit's work),
  answers recorded to a file (`--answers`) and every table scored by replay
  (`--answers-offline`, `report`). A marker file stops a second live run of a part.
- Order: dev first, then held-out. The dev run only shows that the harness works (pages are read,
  the gold joins the answers). The pipeline, prompt and settings are not tuned on dev or on
  held-out: nothing about them changes whatever a number says. A defect of the harness (the builder,
  the driver, the report; never the model's behaviour) found on dev may be fixed before the
  held-out run if the fix is written down here as a dated addendum, with the registered files
  hashed again, before the held-out run. A fix that changes the held-out pages or gold is not
  allowed; the held-out pages are frozen by the manifest above.
- Spend: **$8.00 at list price in total** (the cost recorded on each call of the answers files).
  Dev is capped at $2.00; held-out at $8.00 minus what dev actually spent. No run is repeated to
  confirm another and nothing is re-run to finish. If a cap stops the run, the units that finished
  are scored and the units not finished are said so; no rule below is applied if fewer than 8 of
  the 12 held-out units finished (the write-up says "no decision").

## What is reported

The numbers come from the document task's report (`report.json`) and `scripts/handwriting_eval.py
report`, for held-out and, separately, dev. Gold equations are the denominator of every rate.

**Primary: equations right end to end, `exact_lenient`** (`metrics.end_to_end_exact_lenient`):
the share of gold equations for which the pipeline's output has an aligned equation whose
normalised MathML tree equals the label's. It is the metric of docs/eval-image-pages.md. Interval:
95% cluster bootstrap over units (10,000 resamples, seed 20261021), reported beside the point
estimate. The held-out point estimate is the primary number; dev is not pooled with it.

**Secondary.**

1. Equations right end to end on the symbols score (`secondary_symbols.end_to_end_exact_with_credit`;
   letters, digits and operators equal, notation-only differences forgiven), and detection recall
   and precision.
2. **Flags.** Display equations are always a required decision (no text layer to check them
   against), an inline expression rides with its paragraph, which is required only when the model
   calls it `medium` or `low` or writes `[illegible]` in it (ADR 0007, "Trust"). Reported:
   required decisions and optional notes per page; the share of gold equations whose item is
   required; flag precision and recall (`equation_flags`); display equations whose crop and page
   readings agree and are wrong; `[illegible]` markers; pages kept as images or fallen back.
3. **Silent wrong equations**: a gold equation that was matched, is wrong, and whose item is not a
   required decision. Counted on the symbols score (`equation_flags.all_symbols.wrong_not_asked`)
   and, beside it, on the primary (`equation_flags.all.wrong_not_asked`). Beside both: output
   equations with no gold partner that are not asked about, and gold equations that nothing was
   aligned to (a very bad reading can fail to align and so is not "matched"; those numbers are
   reported so that a small silent-wrong count cannot hide behind them).
4. **Cost**: dollars per page for the page read and the whole pipeline, seconds per page.
5. Strata of held-out (descriptive, no tests): display and inline, pen colour, plain and ruled
   paper, expression length (3-7, 8-15, 16+ tokens), unit.
6. By-eye notes on the worst failures (labelled as such, after the numbers): whether the label is
   what the ink shows. They do not enter any rule.

## Decision rule

**Handwriting may be advertised if, on held-out, the primary is at least 0.50 and silent wrong
equations are 0**, given that every display equation is a required decision and inline
expressions ride with their paragraph. Otherwise it may not be advertised, and the current wording
(scanned or handwritten pages converted as an option, every converted equation and table needing
the user's check) stays as it is.

Reasons for the one adjustment of the proposed rule, written before the run: "silent wrong" is
counted on the **symbols** score, not the strict one, because the strict score fails notation a
reader cannot see (49 of the 113 matched display equations "wrong" by `exact_lenient` in
docs/eval-image-pages.md were wrong in no visible way); the strict count is reported beside it so
that either reading can be taken. The threshold 0.50 is kept on the primary, as proposed, and is
not softened by the interval: a point estimate at or above 0.50 passes the rule and the write-up
gives the interval next to it; one below fails it. There is no partial credit, no second chance
and no new threshold after the run; if the rule fails, the write-up may report what a different
design would change (for example inline expressions as required items), labelled as not
measured and not part of the decision.

## Limits stated in advance

One run per part; 12 held-out units, 205 gold equations, 34 pages; one source and one writer
population (MathWriting's contributors) and one page composition; the label is the writer's task,
not a transcription of the ink; typed words around the handwriting; synthetic scanning; the model
may have seen the data; spend capped at $8 and a cap may stop the run. A pass would mean "reads
this kind of handwriting this well, with this review load", not "reads handwriting".
