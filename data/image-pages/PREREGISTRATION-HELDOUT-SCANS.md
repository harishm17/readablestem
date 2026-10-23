# Registration: the real-world held-out papers as scans (2026-10-21)

Written and hashed before the run (`REGISTRATION-HELDOUT-SCANS.sha256`). One question: how does a
page read from an image by the image page pass ([ADR 0007](../../docs/decisions/0007-image-pages.md))
compare with the same pages as typed PDFs through the current pipeline, on papers the feature was
never developed or tuned on.

## The set

The 15 held-out papers of the real-world set (71 PDF pages, 1,826 gold equations, 87 display and
1,739 inline), as built and registered on branch `feat/realworld-eval`
(`~/.cache/readablestem/realworld/gold/heldout`, its `manifest.json` hashed here). Before this
registration that set was used for typed-PDF scoring only (ReadableSTEM and two chatbot recipes);
the image page pass and its prompt were developed on `data/dev-v2` and eight public-domain
pages, never on these papers. The typed-PDF result to compare with is that branch's
`data/realworld/results-heldout.json` and `~/.cache/readablestem/realworld/runs/heldout/rs/report.json`
(ReadableSTEM, `claude-opus-5-5` main, `claude-haiku-5-5` cheap, page math off, no second check).

## What is run, once

- Input: each paper's cut PDF turned into the image-only PDF of a scan by `eval/scan.py` (hashed
  here) with **seed 20261021**. The recipe, as the file says: 300 dpi render; resampled to a
  uniformly drawn 150 to 200 dpi; rotated by a uniformly drawn angle within plus or minus 1.5
  degrees; a gentle shading towards one edge (up to 10 %) and a paper tint; Gaussian grain with
  sigma between 4 and 9 levels; a Gaussian blur of radius 0.5 to 0.9 px; JPEG at quality 55 to
  75. Each draw is seeded by the seed and the page's own pixels.
- Pipeline: `FEATURE_IMAGE_PAGES=true`, page reader `claude-opus-5-5` (`IMAGE_PAGES_MODEL`), effort
  `low`, 200 dpi, timeout 180 s; the rest as in the typed run (equation step `claude-opus-5-5`
  main and `claude-haiku-5-5` cheap, no second automated check). The prompt
  (`models/prompts/image_page.md`) is hashed here and is the one used on the development set.
- Driver: `scripts/realworld_scanned.py --max-usd 9 --tag opus`. Answers are recorded; the run is
  never repeated (a marker file stops a second live run; `--offline` replays).

## What is reported

For the image arm and, from the typed run, for the same papers: the primary `exact_lenient` end
to end and the symbols score (credited), detection recall and precision, required decisions per
PDF page (every block's fix for the image arm), cost per PDF page, seconds per page, how many pages
were read and how many stayed images, and the two-reading counts (agreed, disagreed, crop not
an equation). **Prose:** this set's answer key is equations only, so the prose measure is the recall
and precision of the words (three letters or more, no order, formulas out of the output) of the typed
PDF's own text layer: a rough measure, since that layer includes running headers, reference lists and
the text of figures, and say so. **Headings:** the set has no heading gold; no heading score.

## Rules

- Spend cap $9 (a share of the coordinator's approved $9 for this check, in addition to the $9.23
  already spent on the development material). If the cap stops the run, the papers that finished are
  scored and compared with the typed run on the same papers; nothing is run again to finish.
- No change of the code, the prompt, the settings or the seed after seeing a result of this run; a
  failure of the harness (not of the model) before any model call is fixed and said so.
- The numbers are reported whatever they are: no selection of papers or metrics afterwards.
