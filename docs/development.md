# Development notes

Reference for working on the code. The public README is separate and Harish writes it.

## Setup and commands

Requires [uv](https://docs.astral.sh/uv/). Python 3.12+ (`.python-version` pins 3.12 locally;
CI also runs 3.14).

| Command | What it does |
|---|---|
| `uv sync` | Create `.venv` and install locked dependencies |
| `make test` | `uv run pytest` (offline) |
| `make test-network` | Live OpenStax API test (`pytest -m network`) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | Format and auto-fix |
| `make gold` | Fetch the sample gold set (3 sections of `physics`, chapter 2) into `data/gold/` |
| `make gold-licenses` | Write every OpenStax book's license to `data/openstax-licenses.csv` |
| `make eval-selfcheck` | Score the gold equations against themselves (must be 1.0 everywhere) |

CLI:

```
a11y-stem gold fetch --book SLUG --pages N [--chapter C] [--page SLUG ...] [--out DIR] [--date YYYY-MM-DD]
a11y-stem gold licenses [--cc-by-only] [--csv PATH]
a11y-stem gold manifest [--out DIR]
a11y-stem eval equations --gold PATH (--pred PRED.jsonl | --self-check) [--display all|block|inline] [--report OUT.json]
a11y-stem parse FILE.pdf [--pages 1,2] [--json]
```

## Layout

| Path | Contents |
|---|---|
| `src/a11y_stem/gold/openstax.py` | OpenStax fetcher: book lookup, CC BY gate, page selection, extraction, JSONL + manifest |
| `src/a11y_stem/eval/equations.py` | MathML normalisation, exact match (strict/lenient), TEDS, LaTeX -> MathML |
| `src/a11y_stem/eval/ted.py` | Zhang-Shasha tree edit distance |
| `src/a11y_stem/eval/gold_scoring.py` | Scores a predictions JSONL against the gold set |
| `src/a11y_stem/pipeline/parse.py` | PDF -> elements stub (PyMuPDF); vision step is a TODO |
| `src/a11y_stem/cli/main.py` | `a11y-stem` command |
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
`content_url` (archive JSON), `license_name`, `license_url`, `retrieved_at`,
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
`mean_teds`, `errors` (see the `equations.py` docstring for what each normalisation does).

## Rules that the code and repo enforce

- `private-inputs/`, `.env` and `.cache/` are git-ignored. Course PDFs never leave the machine.
- `gold fetch` refuses any book whose archive or CMS license is not plain CC BY.
- No secrets in the repo; `.env.example` holds placeholder names only. CI runs gitleaks.
