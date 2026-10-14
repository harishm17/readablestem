# Parser comparison harness. Run from proof/parsers (or: make -C proof/parsers <target>).
# Nothing here touches the app's environment; the arms live in ~/.cache/a11y-proof/parsers-envs.
.PHONY: sync setup plumbing proof test lint

sync:       ## the harness itself (needs the ReadableSTEM checkout above this folder)
	uv sync

setup: sync  ## one environment per parser from arms/*/uv.lock, plus MinerU's weights
	uv run parsers-bench setup

# Two development pages (OpenStax Physics 2.3, CC BY 4.0, from the committed demo PDF)
# through every arm that is set up: convert twice, adapt, score. Plumbing only.
# Needs a quiet box if you read the times: check `uptime` first.
plumbing: sync
	uv run parsers-bench plumbing

test: sync
	uv run pytest

lint: sync
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check src

# Called by `readablestem proof --live` and by hand. PDF_DIR holds <book>__<page>.pdf, OUT_DIR gets
# <arm>/<book>__<page>.{json,md}. ARMS="docling mineru-basic" limits the arms; WORK_DIR is scratch.
WORK_DIR ?= $(HOME)/.cache/a11y-proof/parsers-proof-work
proof: sync
	@test -n "$(PDF_DIR)" -a -n "$(OUT_DIR)" || { echo "usage: make proof PDF_DIR=<dir> OUT_DIR=<dir> [ARMS=...]"; exit 2; }
	uv run parsers-bench proof "$(PDF_DIR)" "$(OUT_DIR)" --work-dir "$(WORK_DIR)" $(if $(ARMS),--arms $(ARMS))
