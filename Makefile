# Common tasks. Everything runs through uv; no global installs needed.
.PHONY: sync test test-network lint fmt check ci-local gold gold-licenses eval-plumbing eval-latex-check

# Sample gold set: 3 numbered sections of chapter 2 of OpenStax Physics (CC BY 4.0).
# Harish picks the real gold-set pages; override with e.g. make gold GOLD_CHAPTER=5.
GOLD_BOOK ?= physics
GOLD_PAGES ?= 3
GOLD_CHAPTER ?= 2
SAMPLE_PREDICTIONS = tests/fixtures/sample_latex_predictions.jsonl

sync:
	uv sync

test:
	uv run pytest

test-network:  ## live OpenStax API check (skipped by default)
	uv run pytest -m network

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
