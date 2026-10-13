#!/usr/bin/env bash
# Record the held-out proof set (proof/sets/heldout-2026-10.json), one system after the other.
#
#   ENV_FILE=/path/to/.env proof/record-heldout.sh
#
# ENV_FILE holds OPENAI_API_KEY (never printed). The account needs credit: the registration
# estimates about $27 for everything; the caps below stop each step (a11y-stem $12, the chatbot
# arms and their figure judge $10). Nothing else may call the API while this runs: a rate limit
# or an empty balance makes calls fail, and a failed call is not the system's answer.
#
# Steps, each safe to interrupt and run again:
#   1. a11y-stem, production defaults: four tasks, two repeats of three of them. `--resume`
#      reads back the runs that finished; `--keep-finished` keeps them if the cap stops the run
#      (the report then opens with an INCOMPLETE RUN banner).
#   2. the chatbot recipe (Purdue prompt on gpt-6.1-sol and gpt-6-luna, the careful prompt on
#      gpt-6.1-sol if the cap holds it), then the figure judge on what they wrote.
#   3. the open parsers (CPU, hours; sections already converted are not converted again).
#
# The result is a recorded run under $BUNDLE. Copy it to proof/bundles/heldout-2026-10/, make
# that set the default in proof/sets/index.json, and `make proof` writes PROOF.md from it.
set -euo pipefail

: "${ENV_FILE:?set ENV_FILE to the file holding OPENAI_API_KEY}"
RUN_DIR="${RUN_DIR:-$HOME/.cache/a11y-proof/heldout-run}"
WORK="${WORK:-$RUN_DIR/work}"
BUNDLE="${BUNDLE:-$RUN_DIR/bundle}"
SET_ID="${SET_ID:-heldout-2026-10}"
mkdir -p "$RUN_DIR"

echo "1/3 a11y-stem"
uv run --env-file "$ENV_FILE" --group browser a11y-stem proof --set "$SET_ID" --live --resume \
    --max-usd "${A11Y_USD:-12}" --keep-finished --confirm-signoff \
    --work "$WORK" --record "$BUNDLE" --out "$RUN_DIR/PROOF-a11y-only.md"

echo "2/3 chatbot recipe"
uv run --env-file "$ENV_FILE" --group browser a11y-stem proof --set "$SET_ID" \
    --bundle "$BUNDLE" --work "$WORK" --add-systems chatbot --max-usd "${CHAT_USD:-10}"

echo "3/3 open parsers"
uv run a11y-stem proof --set "$SET_ID" --bundle "$BUNDLE" --work "$WORK" --add-systems parsers

uv run a11y-stem proof --set "$SET_ID" --bundle "$BUNDLE" --work "$WORK" --out "$RUN_DIR/PROOF.md"
echo "recorded run: $BUNDLE; report: $RUN_DIR/PROOF.md"
