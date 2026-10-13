# Parser comparison harness. Run from proof/parsers (or: make -C proof/parsers <target>).
# Nothing here touches the app's environment; the arms live in ~/.cache/a11y-proof/parsers-envs.
.PHONY: sync setup plumbing test lint

sync:       ## the harness itself (needs the a11y-stem checkout above this folder)
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
