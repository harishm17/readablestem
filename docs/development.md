# Development notes

Reference for working on the code. The README is the short version; `docs/README.md` is
the index to everything else.

## Setup and commands

Requires [uv](https://docs.astral.sh/uv/). Python 3.12+ (`.python-version` pins 3.12 locally;
CI also runs 3.14). The browser tests and the eval renderer print pages with the fonts
`eval/render.py` names: install `fonts-noto-core` (Noto Sans Math) and `fonts-liberation`
on Debian or Ubuntu, as CI does. Without them Chromium falls back to other fonts and some
printed pages read differently.

| Command | What it does |
|---|---|
| `uv sync` | Create `.venv` and install locked dependencies |
| `make test` | `uv run pytest` (offline) |
| `make test-network` | Live OpenStax API test (`pytest -m network`) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | Format and auto-fix |
| `make ci-local` | What CI's `lint-test` job runs (ruff, ty, pytest), on 3.12 and again on 3.14 (`.local/venv-3.14`). Run it before pushing |
| `make gold` | Fetch the sample gold set (3 sections of `physics`, chapter 2) into `data/gold/` |
| `make gold-licenses` | Write every OpenStax book's license to `data/openstax-licenses.csv` |
| `make reproduce` | Offline reproduction: gold files against their manifests, committed eval reports against the gold they were scored on, the scorer's ceiling, then the committed numbers. No key, no network, no browser |
| `make reproduce-rerun` | The same, plus a re-run of the headings and tables tasks (no model) compared with the committed reports; needs network on the first run, Chromium and the fonts above |
| `make eval-plumbing` | Plumbing check only: scores each gold equation against itself, so it always prints 1.0. Does **not** test normalisation |
| `make eval-latex-check` | Scorer check: known-correct LaTeX for every sample equation (`tests/fixtures/sample_latex_predictions.jsonl`) must match leniently; also runs in CI via `pytest` |
| `make proof` | Replay the recorded run in `proof/bundles/` and write `PROOF.md` (no key, no network, no cost). `proof-check`, `proof-sources`, `proof-live`: see docs/proof.md |

CLI:

```
a11y-stem gold fetch --book SLUG --pages N [--chapter C] [--page SLUG ...] [--out DIR] [--date YYYY-MM-DD] [--no-cache] [--allow-retired]
a11y-stem gold licenses [--cc-by-only] [--csv PATH]
a11y-stem gold manifest [--out DIR]
a11y-stem gold fetch --set heldout --allow-retired [--date 2026-10-13] [--heldout-dir DIR]   # the pre-registered held-out set (docs/eval.md)
a11y-stem gold verify --set heldout [--gold DIR] [--heldout-dir DIR]
a11y-stem eval equations --gold PATH (--pred PRED.jsonl | --self-check) [--display all|block|inline] [--report OUT.json]
a11y-stem parse FILE.pdf [--pages 1,2] [--json]
a11y-stem proof [--set ID] [--check | --verify-sources | --live --max-usd N] [--out PATH]
```

Default paths (`data/gold`, `.cache/openstax`) resolve against the project root (the
nearest parent directory whose `pyproject.toml` names `a11y-stem`), so commands work from
any subdirectory. Outside a checkout they fall back to the working directory. Bad input
(missing file, page out of range, malformed JSONL) prints `error: ...` and exits 2.

## Layout

| Path | Contents |
|---|---|
| `src/a11y_stem/gold/openstax.py` | OpenStax fetcher: book lookup, CC BY gate, page selection, extraction, JSONL + manifest |
| `src/a11y_stem/gold/heldout.py` | the pre-registered held-out set: 15 sections, registered hashes, pinned fetch, `verify_gold` (see docs/eval.md) |
| `src/a11y_stem/eval/equations.py` | MathML normalisation, exact match (strict/lenient), TEDS, LaTeX -> MathML |
| `src/a11y_stem/eval/ted.py` | Zhang-Shasha tree edit distance |
| `src/a11y_stem/eval/gold_scoring.py` | Scores a predictions JSONL against the gold set |
| `src/a11y_stem/pipeline/parse.py` | the quick line-level view behind `a11y-stem parse` (a debugging aid) and `equation_score`, which the layout step reuses |
| `src/a11y_stem/pipeline/` | the conversion pipeline (layout, steps, assemble, run); see docs/layout.md, docs/steps.md, docs/tables.md, docs/output.md |
| `src/a11y_stem/web/`, `src/a11y_stem/ops/` | the hosted app and its cron, retention and reports; see docs/web.md, docs/operations.md |
| `src/a11y_stem/cli/main.py` | `a11y-stem` command |
| `src/a11y_stem/proof/`, `proof/` | the one-command reproduction: code, proof sets, recorded runs; see docs/proof.md |
| `data/gold/` | Committed sample gold set, `manifest.json`, `ATTRIBUTION.md` |
| `docs/decisions/` | Architecture decision records |
| `private-inputs/` | **Git-ignored.** Your own course PDFs and any outputs from them |

## Gold set format

One JSONL file per page: `data/gold/openstax/<book>/<page>.jsonl`, plus `_book.json`
(book metadata and license) per book. `manifest.json` lists every file with its SHA-256 and
counts; it is rebuilt after each fetch.

Fields on every record: `id`, `kind` (`equation` | `figure` | `table`), `book_slug`,
`book_title`, `book_uuid`, `book_version`, `archive_version`, `page_slug`, `page_title`,
`page_uuid`, `source_url` (public page, with `#element-id` when there is one),
`content_url` (archive JSON), `license_name`, `license_url`, `book_state` (OpenStax CMS
state, normally `live`), `retrieved_at`,
`index_on_page`, `element_id`, `context` (enclosing note/example class, e.g.
`worked-example`, or null).

| Kind | Extra fields |
|---|---|
| equation | `display` (block/inline), `mathml` (as published, including `semantics` and Content MathML annotations) |
| figure | `figure_number`, `caption`, `caption_has_math`, `images[]` with `url`, `media_type`, `alt`, `alt_is_empty`, `width`, `height` |
| table | `table_number`, `caption`, `summary`, `role`, `is_layout`, `has_thead`, `header_cells[]` (`row`, `col`, `scope`, `text`), `n_rows`, `n_cols`, `html` |

Images are not downloaded; `images[].url` points at the OpenStax archive.

## Prediction format for `eval equations`

JSON lines with the gold `id` and either `latex` or `mathml`:

```
{"id": "openstax/physics/2-2-speed-and-velocity/eq-001", "latex": "\\Delta t = t_f - t_0"}
```

Missing predictions count as failures. Output: `exact_strict`, `exact_lenient`,
`mean_teds`, `errors`, plus `unknown_ids` / `unknown_ids_first` (prediction ids that match
no gold record, e.g. typos). See the `equations.py` docstring for what each normalisation
does.

### Scorer check on the sample (2026-10-06)

`tests/fixtures/sample_latex_predictions.jsonl` holds correct LaTeX for each of the 30
sample equations (a scorer test input written from the gold MathML, not a gold label).
With it:

| | exact_strict | exact_lenient | mean TEDS |
|---|---|---|---|
| Before the mstyle fix (commit 03a537c) | 0.27 | 0.33 | 0.72 |
| Now | 0.43 | 0.83 | 0.87 |

The 5 remaining lenient misses are known gaps, kept as strict xfails in
`tests/test_gold_sample.py`: OpenStax packs operators into text (`<mtext>time =</mtext>`,
`<mtext>m/s)(46 s)</mtext>`, an `=` column written as `<mtext>=</mtext>`). Whether lenient
mode should split operators out of text is an open scoring decision (ADR 0001, question 3).
Strict misses are mostly `<mtext>Δ</mtext>` (OpenStax) vs `<mi>Δ</mi>` (LaTeX `\Delta`).

Found while writing it: latex2mathml 3.81.1 does not render `\begin{aligned}` as a table
(it emits a flat row with a literal `&`). OpenStax multi-line equations are 3-column
`mtable`s, which `\begin{array}{rcl}` reproduces. The vision-step prompt (or a converter
step) needs to account for this.

## Rules that the code and repo enforce

- `private-inputs/`, `.env` and `.cache/` are git-ignored. Course PDFs never leave the machine.
- `gold fetch` refuses any book unless both the archive and the CMS license are plain CC BY
  (a missing license in either place is refused).
- `gold fetch` refuses books whose OpenStax state is not `live` (retired, deprecated,
  unlisted) unless you pass `--allow-retired`. Open decision: ADR 0001, question 2.
- The held-out set (`--set heldout`, docs/eval.md "Held-out set") is never written inside
  the repository: its gold, caches and reports go to `~/.cache/a11y-stem/heldout`, and a
  `--set heldout` command refuses a path inside the repository, `--fake`, and gold that
  is not the registered gold.
- No secrets in the repo; `.env.example` holds placeholder names only. CI runs gitleaks.
