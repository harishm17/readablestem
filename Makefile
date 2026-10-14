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
	uv run python -m readablestem.eval.reproduce

# The same, plus a re-run of the two tasks that call no model (headings, tables in local
# mode) compared with the committed reports. Needs network on the first run, Chromium
# (uv run playwright install chromium) and the fonts in docs/development.md.
reproduce-rerun:
	uv run python -m readablestem.eval.reproduce --rerun-offline

test:
	uv run pytest

test-network:  ## live OpenStax API check (skipped by default)
	uv run pytest -m network

# The SLANet-plus table-structure model (7.5 MB), SHA-256 checked, into
# ~/.cache/readablestem/models (or TABLE_MODEL_DIR). Without it tables use PyMuPDF's grid.
table-model:
	uv run python -m readablestem.pipeline.table_model

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
	uv run readablestem gold fetch --book $(GOLD_BOOK) --pages $(GOLD_PAGES) $(if $(GOLD_CHAPTER),--chapter $(GOLD_CHAPTER))

gold-licenses:
	uv run readablestem gold licenses --csv data/openstax-licenses.csv

# Pipeline plumbing only: scores each gold equation against itself, so it prints 1.0
# even if normalisation is broken. It does NOT test the scorer. Use eval-latex-check.
eval-plumbing:
	uv run readablestem eval equations --gold data/gold --self-check

# Scorer check: known-correct LaTeX for every sample equation must match leniently
# (known gaps are strict xfails). Then prints the summary for the same predictions.
eval-latex-check:
	uv run pytest -q tests/test_gold_sample.py
	uv run readablestem eval equations --gold data/gold --pred $(SAMPLE_PREDICTIONS)

# The pre-registered held-out set (docs/eval.md "Held-out set"). Its files go to
# ~/.cache/readablestem/heldout, never into the repository. heldout-gold rebuilds the
# registered gold byte for byte from OpenStax's pinned archive (retired first editions,
# hence --allow-retired: ADR 0001 question 2) and checks it; no model is called.
.PHONY: heldout-gold heldout-verify heldout-pdfs
heldout-gold:
	uv run readablestem gold fetch --set heldout --allow-retired --date 2026-10-13

heldout-verify:
	uv run readablestem gold verify --set heldout

heldout-pdfs:  ## print the 15 sections and check the page counts (242)
	uv run --group browser readablestem eval render --set heldout --all

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

# The recorded runs were made with the pipeline as it was at PROOF_TAG, and a replay feeds
# the recorded answers to the pipeline in this checkout: a later commit that changes a
# model request (or anything else a recording pins) cannot replay them. So the one-command
# reproduction is pinned to the tag: `git checkout $(PROOF_TAG) && make proof`. On any other
# commit a failed replay says so instead of leaving a bare traceback. `make proof-check`
# (pins and hashes, no replay) works on every commit. CI replays at the tag
# (.github/workflows/ci.yml, job proof-replay); tests/test_proof_pin.py keeps the names equal.
PROOF_TAG = proof-heldout-2026-10

proof:  ## replay the recorded run and write PROOF.md (no key, no network, no cost); reproduce at the tag
	uv run readablestem proof $(PROOF_ARGS) || { status=$$?; \
		if [ "$$(git rev-parse -q --verify HEAD 2>/dev/null)" != "$$(git rev-parse -q --verify '$(PROOF_TAG)^{commit}' 2>/dev/null)" ]; then \
			echo >&2; \
			echo "make proof failed, and this is not the commit the recorded run belongs to." >&2; \
			echo "The recording was made with the pipeline at the tag $(PROOF_TAG); this checkout's" >&2; \
			echo "pipeline can differ from it (a changed model request has no recorded answer), and" >&2; \
			echo "then a replay cannot reproduce it. That does not mean the proof is broken. To" >&2; \
			echo "reproduce it:  git checkout $(PROOF_TAG) && make proof" >&2; \
			echo "Here, 'make proof-check' still checks the pins and the recorded run's hashes." >&2; \
		fi; \
		exit $$status; }

proof-check:  ## check the pins and the recorded run's hashes; run nothing
	uv run readablestem proof --check $(PROOF_ARGS)

proof-list:
	uv run readablestem proof --list

# Needs network and Chromium: `uv sync --group browser` and
# `uv run playwright install --with-deps chromium` (docs/development.md for the fonts).
# Writes proof/PROOF-sources.md (OUT=<file> to change), never PROOF.md: this check replaces
# the report with one that holds the fetch and print results, which would leave the tree dirty.
proof-sources:  ## also fetch the answer key from OpenStax and print the PDFs again (network, Chromium)
	uv run --group browser readablestem proof --verify-sources --out $(or $(OUT),proof/PROOF-sources.md) $(PROOF_ARGS)

# A live run of the default set records about $0.30 and stops without a result below that:
# use MAX_USD=0.50 or more. Needs Chromium like proof-sources.
proof-live:  ## re-call the models (costs money, needs Chromium): make proof-live MAX_USD=2 ENV_FILE=.env
	@test -n "$(MAX_USD)" || { echo "usage: make proof-live MAX_USD=<dollars> [ENV_FILE=.env] [SET=<id>]"; exit 2; }
	uv run $(if $(ENV_FILE),--env-file $(ENV_FILE)) --group browser readablestem proof --live \
		--max-usd $(MAX_USD) $(if $(RECORD),--record $(RECORD)) $(PROOF_ARGS)


proof-add-chatbot:  ## record the chatbot arms into a recorded run: make proof-add-chatbot MAX_USD=10 ENV_FILE=.env
	@test -n "$(MAX_USD)" || { echo "usage: make proof-add-chatbot MAX_USD=<dollars> [ENV_FILE=.env] [SET=<id>]"; exit 2; }
	uv run $(if $(ENV_FILE),--env-file $(ENV_FILE)) --group browser readablestem proof --add-systems chatbot \
		--max-usd $(MAX_USD) $(PROOF_ARGS)

proof-interim:  ## the held-out results that need no model (headings, parsers): needs `make heldout-gold`
	uv run python proof/interim/report.py --gold "$${HELDOUT_GOLD:-$$(uv run python -c 'from readablestem.cli.heldout import default_root; print(default_root() / "gold")')}"

proof-add-parsers:  ## record the open parsers (hours, resumable; run `make -C proof/parsers setup` once)
	uv run readablestem proof --add-systems parsers $(PROOF_ARGS)

# The static reviewer demo (docs/site.md): plain HTML written from committed results and
# PROOF.md into site/_build (OUT=<dir> for another folder). No key, no network, no model
# call, no cost; the same inputs give the same bytes. Opening site/_build/index.html in a
# browser is enough to look at it.
site:  ## build the static demo site into site/_build
	uv run python site/build.py $(if $(OUT),--out $(OUT))
