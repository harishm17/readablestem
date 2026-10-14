# Common tasks. Everything runs through uv; no global installs needed.
.PHONY: site sync test test-network lint fmt check ci-local gold gold-licenses eval-plumbing eval-latex-check \
	table-model fuzz-tables proof proof-check proof-list proof-sources proof-live proof-add-chatbot proof-add-parsers proof-interim reproduce reproduce-rerun

# Sample gold set: 3 numbered sections of chapter 2 of OpenStax Physics (CC BY 4.0).
# Harish picks the real gold-set pages; override with e.g. make gold GOLD_CHAPTER=5.
GOLD_BOOK ?= physics
GOLD_PAGES ?= 3
GOLD_CHAPTER ?= 2
SAMPLE_PREDICTIONS = tests/fixtures/sample_latex_predictions.jsonl

sync:
	uv sync

# One command for a reader: gold files against their manifests, committed reports against
# the gold they were scored on, the scorer's ceiling, then the committed numbers. No API
# key, no network, no browser; a few seconds (eval/reproduce.py, docs/eval.md).
reproduce:
	uv run python -m a11y_stem.eval.reproduce

# The same, plus a re-run of the two tasks that call no model (headings, tables in local
# mode) compared with the committed reports. Needs network on the first run, Chromium
# (uv run playwright install chromium) and the fonts in docs/development.md.
reproduce-rerun:
	uv run python -m a11y_stem.eval.reproduce --rerun-offline

test:
	uv run pytest

test-network:  ## live OpenStax API check (skipped by default)
	uv run pytest -m network

# The SLANet-plus table-structure model (7.5 MB), SHA-256 checked, into
# ~/.cache/a11y-stem/models (or TABLE_MODEL_DIR). Without it tables use PyMuPDF's grid.
table-model:
	uv run python -m a11y_stem.pipeline.table_model

# Upgrade check before any PyMuPDF bump: byte mutants of the repo's own fixtures through
# the probe and the table detector; fails if a file passes the probe and then kills the
# table step (docs/tables.md "Upgrade check"). Takes a few minutes.
fuzz-tables:
	uv run pytest -q -s -m slow tests/test_tables_fuzz.py

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

check: lint test

# What CI's lint-test job runs, on both of its Python versions, before you push. The 3.14
# environment lives in .local/venv-3.14 (ignored); a fault only newer interpreters
# reject (e.g. a docstring that 3.13+ cannot compile) shows up here instead of in CI.
CI_314 = UV_PROJECT_ENVIRONMENT=.local/venv-3.14
ci-local:
	uv sync --locked --all-extras --group browser
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check src
	uv run pytest -q
	$(CI_314) uv sync --locked --all-extras --group browser --python 3.14
	$(CI_314) uv run --python 3.14 ty check src
	$(CI_314) uv run --python 3.14 pytest -q

gold:
	uv run a11y-stem gold fetch --book $(GOLD_BOOK) --pages $(GOLD_PAGES) $(if $(GOLD_CHAPTER),--chapter $(GOLD_CHAPTER))

gold-licenses:
	uv run a11y-stem gold licenses --csv data/openstax-licenses.csv

# Pipeline plumbing only: scores each gold equation against itself, so it prints 1.0
# even if normalisation is broken. It does NOT test the scorer. Use eval-latex-check.
eval-plumbing:
	uv run a11y-stem eval equations --gold data/gold --self-check

# Scorer check: known-correct LaTeX for every sample equation must match leniently
# (known gaps are strict xfails). Then prints the summary for the same predictions.
eval-latex-check:
	uv run pytest -q tests/test_gold_sample.py
	uv run a11y-stem eval equations --gold data/gold --pred $(SAMPLE_PREDICTIONS)

# The pre-registered held-out set (docs/eval.md "Held-out set"). Its files go to
# ~/.cache/a11y-stem/heldout, never into the repository. heldout-gold rebuilds the
# registered gold byte for byte from OpenStax's pinned archive (retired first editions,
# hence --allow-retired: ADR 0001 question 2) and checks it; no model is called.
.PHONY: heldout-gold heldout-verify heldout-pdfs
heldout-gold:
	uv run a11y-stem gold fetch --set heldout --allow-retired --date 2026-10-13

heldout-verify:
	uv run a11y-stem gold verify --set heldout

heldout-pdfs:  ## print the 15 sections and check the page counts (242)
	uv run --group browser a11y-stem eval render --set heldout --all

# One-command reproduction of the public proof (docs/proof.md). `make proof` replays the
# recorded run committed under proof/bundles/: no key, no network, no cost, same bytes
# every time, and it writes PROOF.md. The other targets need the browser group
# (`uv sync --group browser`, `uv run playwright install --with-deps chromium`).
#   SET=<id>       another proof set (make proof-list)
#   BUNDLE=<dir>   another recorded run
#   MAX_USD=<n>    hard spend cap for proof-live (required there)
#   ENV_FILE=<f>   env file holding OPENAI_API_KEY (ANTHROPIC_API_KEY with --provider
#                  anthropic, via PROOF_EXTRA) for proof-live (never printed)
#   RECORD=<dir>   where proof-live writes the new recording (default: outside the repo)
PROOF_ARGS = $(if $(SET),--set $(SET)) $(if $(BUNDLE),--bundle $(BUNDLE)) $(PROOF_EXTRA)

proof:  ## replay the recorded run and write PROOF.md (no key, no network, no cost)
	uv run a11y-stem proof $(PROOF_ARGS)

proof-check:  ## check the pins and the recorded run's hashes; run nothing
	uv run a11y-stem proof --check $(PROOF_ARGS)

proof-list:
	uv run a11y-stem proof --list

# Needs network and Chromium: `uv sync --group browser` and
# `uv run playwright install --with-deps chromium` (docs/development.md for the fonts).
# Writes proof/PROOF-sources.md (OUT=<file> to change), never PROOF.md: this check replaces
# the report with one that holds the fetch and print results, which would leave the tree dirty.
proof-sources:  ## also fetch the answer key from OpenStax and print the PDFs again (network, Chromium)
	uv run --group browser a11y-stem proof --verify-sources --out $(or $(OUT),proof/PROOF-sources.md) $(PROOF_ARGS)

# A live run of the default set records about $0.30 and stops without a result below that:
# use MAX_USD=0.50 or more. Needs Chromium like proof-sources.
proof-live:  ## re-call the models (costs money, needs Chromium): make proof-live MAX_USD=2 ENV_FILE=.env
	@test -n "$(MAX_USD)" || { echo "usage: make proof-live MAX_USD=<dollars> [ENV_FILE=.env] [SET=<id>]"; exit 2; }
	uv run $(if $(ENV_FILE),--env-file $(ENV_FILE)) --group browser a11y-stem proof --live \
		--max-usd $(MAX_USD) $(if $(RECORD),--record $(RECORD)) $(PROOF_ARGS)


proof-add-chatbot:  ## record the chatbot arms into a recorded run: make proof-add-chatbot MAX_USD=10 ENV_FILE=.env
	@test -n "$(MAX_USD)" || { echo "usage: make proof-add-chatbot MAX_USD=<dollars> [ENV_FILE=.env] [SET=<id>]"; exit 2; }
	uv run $(if $(ENV_FILE),--env-file $(ENV_FILE)) --group browser a11y-stem proof --add-systems chatbot \
		--max-usd $(MAX_USD) $(PROOF_ARGS)

proof-interim:  ## the held-out results that need no model (headings, parsers): needs `make heldout-gold`
	uv run python proof/interim/report.py --gold $${HELDOUT_GOLD:-$${XDG_CACHE_HOME:-$$HOME/.cache}/a11y-stem/heldout/gold}

proof-add-parsers:  ## record the open parsers (hours, resumable; run `make -C proof/parsers setup` once)
	uv run a11y-stem proof --add-systems parsers $(PROOF_ARGS)

# The static reviewer demo (docs/site.md): plain HTML written from committed results and
# PROOF.md into site/_build (OUT=<dir> for another folder). No key, no network, no model
# call, no cost; the same inputs give the same bytes. Opening site/_build/index.html in a
# browser is enough to look at it.
site:  ## build the static demo site into site/_build
	uv run python site/build.py $(if $(OUT),--out $(OUT))
