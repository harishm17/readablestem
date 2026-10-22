# Chemistry held-out (small, fresh): pre-registration

> Where the gold lives (added after the run, because `tests/test_heldout.py` refuses a folder
> `data/**/openstax/chemistry` in the repository: no gold of a book that has a registered
> held-out section is committed): the five files are not in the repository. Rebuild them with the
> `gold fetch` command below, `--out ~/.cache/readablestem/chem-heldout/gold`, and check them
> against `data/chem-heldout/SHA256SUMS`; the run is `--gold` that folder. The run itself is the
> one described here.


Written and committed **before any model call on these sections** (2026-10-21). The result is
appended below ("Result") after the one run; nothing above that line is edited afterwards.

Why: the registered held-out set (`proof/`, 15 sections) has three chemistry sections and was
used (the 0.041 of `docs/remeasurement-2026-10-21.md`); the development sets were tuned on
(`docs/chemistry.md`). This is a small check on sections nobody has looked at, for the chemistry
work of branch `fix/chemistry-accuracy` (the chemistry scorer and the fixes in
`docs/chemistry.md`, "Accuracy").

## The sections

Five sections of OpenStax *Chemistry* (the first edition, slug `chemistry`, CC BY 4.0, retired;
archive 20260604.144757, the archive of the registered set; Chemistry 2e is CC BY-NC-SA and is
not used). Fetched with `readablestem gold fetch --book chemistry --allow-retired --date
2026-10-21` into `data/chem-heldout/gold/`; manifest SHA-256
`c7e95d82a6bfb5e0b1cf49898ab42a91a4408dce3ecb4f983169b2783b57e3f3`.

| Section | Gold equations | What it is |
|---|---|---|
| 4-1 Writing and balancing chemical equations | 65 | formulas, states, arrows, coefficients |
| 4-2 Classifying chemical reactions | 73 | ions, net ionic equations, redox |
| 15-1 Precipitation and dissolution | 154 | ions, solubility products, concentrations |
| 17-2 Galvanic cells | 15 | half-reactions, cell notation |
| 21-3 Radioactive decay | 70 | nuclides, decay equations |
| total | 377 | |

Not used before: none of them is a registered held-out section (those are 7-3, 14-7 and 19-3 of
the same book, and the other twelve of other books), none is a section of the development set
(`data/dev-v2/selection.json`: the Atoms First book, sections 1-4, 2-3, 12-3, 14-1, 16-3, 16-4,
17-3, 18-2, 18-12, 20-2 and the key terms), and none was opened, read or run before this
document. The first edition and the Atoms First edition share text, so the sections were chosen
for topics the development set does not cover (it has no balancing, no precipitation, no
galvanic cells or decay chains); the 8-gram overlap with the development text was **not**
measured (the measure of `proof/selection/features.py` needs the held-out archive cache), and
that is a limit of this check.

## What is run

- `uv run readablestem eval pipeline --task document --gold data/chem-heldout/gold --provider
  anthropic` with the settings of `docs/chemistry.md` ("What it did on the development sets"):
  `claude-opus-5-5` and `claude-haiku-5-5`, default thresholds, tables off (production), second
  check off (`CLAUDE_SECOND_CHECK=off`), `--max-usd 3`.
- **Baseline**: the code of `origin/main` at `f206326`, recorded live into one answers file.
- **After**: the code of this branch at the commit named in "Frozen code" below, scored from the
  same answers file; a request the changed code asks differently (a different crop or LaTeX) is a
  live call, appended to a copy of the file.
- Each is run **once**. No setting, prompt or code changes after seeing a number. A failed run
  (a crash, a cap) is repeated only as the same command, and said so.

## What is measured

On the gold equations of the five sections (every class, 377):

1. primary `exact_lenient`, end to end (the repository's primary score; unchanged);
2. the symbols scorer, credited (secondary);
3. the chemistry scorer `exact_chem` (`eval/equations_chem.py`; secondary, chemistry gold only);
4. the outcome classes of `scripts/dev_chem_classes.py` (right, scorer artefact, input, not read,
   span, wrong reading, undetected: table / x sign / other);
5. detection recall and precision; cost per PDF page.

Reported for baseline and after, both scorers, as measured, including a fall.

## Predictions, written down

- The primary score stays low (about 0.05 to 0.20) for both: the chemistry gold is OpenStax
  MathML, as in the development sections, where the primary was 0.13 and `exact_chem` 0.54 of
  the gold.
- `exact_chem` is several times the primary score (at least 3 times).
- After minus baseline is small: the fixes (labelled arrows set by TeX, two-part arrow labels,
  `\middle|` in cell notation) touch few equations; a difference of 0 to 4 equations on
  `exact_chem` is expected, and a fall of more than 2 equations would be a regression to explain.
- Formulas in table cells (section 4-1 has 7 tables) stay undetected: tables are off.

## Frozen code

Branch `fix/chemistry-accuracy`, commit `410dab79f94ef435e7c448b4c6274f4946bed35c` (code, tests,
scripts and docs/chemistry.md; the held-out gold and this document are added after it and change no
code). Baseline: `origin/main` at `f206326`.

