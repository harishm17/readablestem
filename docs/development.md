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
| Word export | `tests/test_export_docx.py` and `tests/test_web_exports.py` need pandoc (`apt install pandoc`; the tests pass with 3.1.3, 3.1.11 and 3.7) and skip, saying so, without it. They open the file with python-docx (dev group); LibreOffice, when installed, must open it too. CI installs pandoc |
| EPUB validators | `tests/test_export_epub_validators.py` skips unless EPUBCheck and DAISY Ace are installed: set `EPUBCHECK_JAR` (needs Java; `JAVA` if not on PATH) and `ACE` (for example `npx -y @daisy/ace`). CI runs them in `epub.yml` |
| `make test-fast` | Unit tests in parallel (`pytest -n auto`, pytest-xdist), without the Postgres, browser, network, live-model and slow markers |
| `make test-network` | Live OpenStax API test (`pytest -m network`) |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | Format and auto-fix |
| `make ci-local` | What CI's `static` and `pytest` jobs run (ruff, ty, pytest), on 3.12 and again on 3.14 (`.local/venv-3.14`). Run it before pushing |
| `make table-model` | Download the SLANet-plus table-structure model (7.5 MB, SHA-256 checked) into `~/.cache/readablestem/models` (or `TABLE_MODEL_DIR`); without it tables use PyMuPDF's grid |
| `make fuzz-tables` | Upgrade check before any PyMuPDF bump: byte mutants of the repository's fixtures through the probe and the table detector (`pytest -m slow`; a few minutes; docs/tables.md) |
| `make gold` | Fetch the sample gold set (3 sections of `physics`, chapter 2) into `data/gold/` |
| `make gold-licenses` | Write every OpenStax book's license to `data/openstax-licenses.csv` |
| `make reproduce` | Offline reproduction: gold files against their manifests, committed eval reports against the gold they were scored on, the scorer's ceiling, then the committed numbers. No key, no network, no browser |
| `make reproduce-rerun` | The same, plus a re-run of the headings and tables tasks (no model) compared with the committed reports; needs network on the first run, Chromium and the fonts above |
| `make eval-plumbing` | Plumbing check only: scores each gold equation against itself, so it always prints 1.0. Does **not** test normalisation |
| `make eval-latex-check` | Scorer check: known-correct LaTeX for every sample equation (`tests/fixtures/sample_latex_predictions.jsonl`) must match leniently; also runs in CI via `pytest` |
| `make proof` | Replay the recorded run in `proof/bundles/` and write `PROOF.md` (no key, no network, no cost). `proof-check`, `proof-sources`, `proof-live`: see docs/proof.md |

The course-style stratum of the fifth real-world split fetches and compiles other people's LaTeX
(`scripts/fresh5_select.py course-draw`). Downloaded TeX is untrusted: archives are unpacked and documents
compiled only through `src/readablestem/gold/safe_tex.py`, which needs [bubblewrap](https://github.com/containers/bubblewrap)
(`bwrap`; there is no unsandboxed fallback), a user-level TeX Live (`install-tl` with scheme-full into
`~/.local/texlive/<year>`, no root; `latexmk` is found there or on `PATH`) and, as the last engine,
[Tectonic](https://tectonic-typesetting.github.io/) (the official release binary in `~/.local/bin`, run from its package
cache). A compile has no network, no rc file, no shell escape and sees only the TeX Live tree, the source as a discarded
overlay and an output folder. Nothing else needs any of this; the compile tests in `tests/test_safe_tex.py` skip without it.

LaTeX source tests (`tests/test_source_*.py`, `tests/test_web_source.py`, `tests/test_sandbox.py`) run the
real LaTeXML in the real bubblewrap sandbox, with no mocks, and skip where either is missing (CI's `docker` job
builds the image with `INSTALL_LATEXML`; the sandbox cannot start inside Docker's default seccomp, so the escape
suite runs on a developer machine or a host with user namespaces). Install `latexml` and `bubblewrap` from your
distribution (`apt install latexml bubblewrap`), or, without root, unpack the packages and point at them:
`apt-get download latexml <its dependencies>`, `dpkg -x` each into one folder, and set
`READABLESTEM_LATEXML_ROOT` to it (it needs a `usr/` folder; the folder is mounted read-only in the sandbox and
Perl is pointed at it; production never sets it). `uv run readablestem ops sandbox-probe` (`READABLESTEM_SANDBOX_BACKEND=landlock` to force the Landlock backend) says whether the sandbox
works on the machine.

CLI:

```
readablestem gold fetch --book SLUG --pages N [--chapter C] [--page SLUG ...] [--out DIR] [--date YYYY-MM-DD] [--no-cache] [--allow-retired]
readablestem gold licenses [--cc-by-only] [--csv PATH]
readablestem gold manifest [--out DIR]
readablestem gold fetch --set heldout --allow-retired [--date 2026-10-13] [--heldout-dir DIR]   # the pre-registered held-out set (docs/eval.md)
readablestem gold verify --set heldout [--gold DIR] [--heldout-dir DIR]
readablestem eval equations --gold PATH (--pred PRED.jsonl | --self-check) [--display all|block|inline] [--report OUT.json]
readablestem parse FILE.pdf [--pages 1,2] [--json]
readablestem proof [--set ID] [--check | --verify-sources | --live --max-usd N] [--out PATH]
```

The other subcommands (`convert`, `export`, `demo`, `serve`, `users`, `db`, `ops`) are covered where they belong: `readablestem --help` lists them, docs/operations.md and docs/deploy-railway.md the hosted ones.

Default paths (`data/gold`, `.cache/openstax`) resolve against the project root (the
nearest parent directory whose `pyproject.toml` names `readablestem`), so commands work from
any subdirectory. Outside a checkout they fall back to the working directory. Bad input
(missing file, page out of range, malformed JSONL) prints `error: ...` and exits 2.

## CI

`.github/workflows/ci.yml` runs on every pull request, every push to `main`, nightly (03:41
UTC) and by hand. Jobs, by check name:

| Check | Notes |
|---|---|
| `plan (what runs on Python 3.14)` | Builds the pytest matrix (below) |
| `ruff + ty (Python 3.12)`, `(Python 3.14)` | ruff, ty and an import of every module; both Pythons on every run |
| `pytest (Python 3.12, shard i/6)`, `(Python 3.14, ...)` | The offline suite, in six shards |
| `every test ran in exactly one shard` | Fails if any collected test ran in no shard or in two |
| `pytest -m browser (Playwright Chromium, shard i/4)` | Browser tests, four shards of three xdist workers each |
| `CI passed` | Fails if any job failed or was cancelled; require this one in branch protection |

The other jobs (`postgres`, `docker`, `latex source`, `make proof` (on main, the nightly run, and pull requests that touch `proof/`, `PROOF.md`, `data/`, the Makefile, `src/readablestem/proof` or `cli`, or the dependency and CI files), `make site`, `pip-audit`,
`eval gate`, `gitleaks`) are described in [operations.md](operations.md) and in the comments of
`ci.yml`. Branch protection is not
configured on the repo today; before this split the checks were named
`ruff + ty + pytest (Python 3.12)` and `(Python 3.14)`.

**Python 3.14 rule.** The full suite runs on 3.14 on a push to `main`, on the nightly
schedule, on a manual run, and on a pull request that changes `pyproject.toml`, `uv.lock`,
`.python-version` or `ci.yml`. Any other pull request runs a smoke: ruff, ty and the import of
every module on 3.14, plus one shard in eight of the suite (`Python 3.14, smoke 1/8`), so an
incompatibility still shows up on the pull request. Python 3.12 always runs everything.
`make ci-local` runs the full suite on both.

**Shards.** `pytest --shard 2/6` runs the second of six shards (`tests/_sharding.py`,
hooks in `tests/conftest.py`). Test files are dealt to shards longest first, by the durations
in `tests/shard_durations.json` (tests of 0.05 s or more; every other test counts 0.02 s).
A file is never split, so module-scoped servers and browsers start once. A new file needs no
entry: it gets the default weight and lands in exactly one shard. Each shard writes a report
with `--shard-report`, and `scripts/check_shards.py` (job `every test ran in exactly one
shard`) fails if the shards collected different tests, or a collected test ran in none or in
two. Refresh the durations now and then so the shards stay level (about 10 minutes with 16
cores):

```
uv run pytest -n 4 --dist loadfile -m "not network and not live_model and not slow" \
  --shard-record tests/shard_durations.json
```

To change the shard count, edit `SHARDS` in both `plan` and `shard-coverage` in `ci.yml`
(a test checks that they agree).

**Browser tests in parallel.** `pytest -m browser -n 3 --dist loadfile --shard i/4` (four
jobs, also checked by `every test ran in exactly one shard`): each worker starts
its own uvicorn server on a free port, keeps its files under `tmp_path_factory` and runs its
own Chromium. The one shared file is the axe-core download in `.cache/axe-core`, written
atomically. A new browser test must not use a fixed port, a fixed path or another test's
state.

**Caches.** `setup-uv` caches the uv download cache (key: Python version and `uv.lock`);
Playwright's Chromium (`~/.cache/ms-playwright`) and axe-core are cached with `actions/cache`.

**Cancelling.** A newer push to a pull request cancels that pull request's earlier runs of
`ci.yml`, `codeql.yml` and `proof-interim.yml` (and `eval-guard.yml`). Runs on `main`, the
nightly run and manual runs are never cancelled.

## Speech oracles (optional)

`scripts/speech_oracles/build.sh` builds MathCAT (NVDA, JAWS) and installs Speech Rule Engine
(MathJax, ChromeVox) under `.local/speech-oracles/`, with rustup into `~/.cargo` and no sudo (pinned toolchain, pinned and checksummed rustup-init).
`scripts/screenreader_speech.py` and `eval pipeline --speech` use them to compare how our MathML
and the gold's are spoken (`docs/eval.md`, "Speech under screen-reader engines"). The tests in
`tests/test_speech_oracle.py` that speak MathML skip when the build is absent; CI does not build it.

## Benchmarks

`scripts/bench_runtime.py` measures the runtime and web work with no key, no network and no
cost: model calls are the offline client with a simulated latency (`--latency`), the
documents are the repository's own layout fixtures. `uv run python scripts/bench_runtime.py
--help` lists them; to compare with an earlier commit, put its `src/` first on
`PYTHONPATH` (`git archive <commit> src | tar -x -C /tmp/old`, then
`PYTHONPATH=/tmp/old/src uv run python scripts/bench_runtime.py ...`). Numbers depend on the
machine (these are from a 16-core machine with a load average over 20, so only the ratios
mean anything).

`scripts/bench_layout.py` times `layout.analyze` alone on synthetic pages of tens of
thousands of tiny paths (and on any `--pdf` files you name, read only), each in its own
process with its peak memory. `scripts/layout_identity.py snapshot DIR` and `compare A B`
check that a change to layout leaves the output of every fixture, development-set page and
table set exactly as it was (full elements, kept pages, page types, notices); take a
snapshot before the change (from an old `src/` as above) and one after.

| Command | Measures | Before | After |
|---|---|---|---|
| `convert --pages 12 --latency 1.5 --concurrency 4 8` | one conversion, 82 calls | 35.3 s at 4, layout 6.2 s CPU | 19.3 s at 8, layout 3.6 s CPU |
| `first` | first conversion after a start, table models loaded or not | 9.3 s | 8.2 s |
| `review --pages 12 --clicks 12` | CPU of a review click (52 crops) | 210 ms; 120 image encodes in 12 clicks | 58 ms; none |
| `documents --documents 20` | one rebuild of My documents, 20 finished jobs of 240 KB, Postgres | 256 ms, 3 statements | 6.6 ms, 2 statements |
| `spend` | database work of a model call; of an idle worker poll | 6 statements (2 INSERT, 4 UPDATE) in 4 transactions; 4 statements (2 UPDATE, 2 SELECT) in 3 | 4 UPDATE in 4 transactions; 1 SELECT in 1 |
| `static` | a first and a repeat view of a page | 3 requests and 115 KB (font 734 KB); 3 revalidations on every repeat | the same 115 KB (font 392 KB); no request on a repeat |
| `sections --sections 8 --workers 1 4` | the document task over 8 sections | 79.2 s | 27.7 s, the same items |

## Layout

| Path | Contents |
|---|---|
| `src/readablestem/gold/openstax.py` | OpenStax fetcher: book lookup, CC BY gate, page selection, extraction, JSONL + manifest |
| `src/readablestem/gold/heldout.py` | the pre-registered held-out set: 15 sections, registered hashes, pinned fetch, `verify_gold` (see docs/eval.md) |
| `src/readablestem/eval/equations.py` | MathML normalisation, exact match (strict/lenient), TEDS, LaTeX -> MathML |
| `src/readablestem/eval/ted.py` | Zhang-Shasha tree edit distance |
| `src/readablestem/eval/gold_scoring.py` | Scores a predictions JSONL against the gold set |
| `src/readablestem/pipeline/parse.py` | the quick line-level view behind `readablestem parse` (a debugging aid) and `equation_score`, which the layout step reuses |
| `src/readablestem/pipeline/` | the conversion pipeline (layout, steps, assemble, run); see docs/layout.md, docs/steps.md, docs/tables.md, docs/output.md |
| `src/readablestem/web/`, `src/readablestem/ops/` | the hosted app and its cron, retention and reports; see docs/web.md, docs/operations.md |
| `src/readablestem/cli/main.py` | `readablestem` command |
| `src/readablestem/proof/`, `proof/` | the one-command reproduction: code, proof sets, recorded runs; see docs/proof.md |
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
  the repository: its gold, caches and reports go to `~/.cache/readablestem/heldout`, and a
  `--set heldout` command refuses a path inside the repository, `--fake`, and gold that
  is not the registered gold.
- No secrets in the repo; `.env.example` holds placeholder names only. CI runs gitleaks.


## The student file in three browsers

`tests/test_student_file_browsers.py` opens the committed demo document (as the pipeline
writes it, and as the Canvas export's `index.html`) in Chromium, Firefox and WebKit at 1280
and 375 CSS px and checks that the page loads without errors, the pictures load, and the
MathML is drawn as maths (fractions stacked, scripts raised and lowered, nothing cut off).
Install the engines you want (`uv run playwright install chromium firefox webkit`; WebKit
also needs `uv run playwright install-deps webkit`) and run
`uv run pytest -m browser tests/test_student_file_browsers.py -rs`. An engine that is not
installed is skipped, and `-rs` says so. On the development box only Chromium is installed,
so the Firefox and WebKit rows are not exercised there.
