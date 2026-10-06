# Common tasks. Everything runs through uv; no global installs needed.
.PHONY: sync test test-network lint fmt check gold gold-licenses eval-plumbing eval-latex-check

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
