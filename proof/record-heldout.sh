#!/usr/bin/env bash
# Record the held-out proof set (proof/sets/heldout-2026-10.json), one system after the other.
#
#   ENV_FILE=/path/to/.env proof/record-heldout.sh
#   PROVIDER=anthropic ENV_FILE=/path/to/.env proof/record-heldout.sh     # Addendum 6
#
# ENV_FILE holds OPENAI_API_KEY (ANTHROPIC_API_KEY with PROVIDER=anthropic); it is never
# printed. The account needs credit: the registration estimates about $27 for everything on
# OpenAI; the caps below stop each step (ReadableSTEM $12, the chatbot arms and their figure judge
# $10; A11Y_USD and CHAT_USD change them). Nothing else may call the API while this runs: a
# rate limit or an empty balance makes calls fail, and a failed call is not the system's answer.
#
# Steps, each safe to interrupt and run again:
#   1. ReadableSTEM, production defaults: four tasks, two repeats of three of them. `--resume`
#      reads back the runs that finished; `--keep-finished` keeps them if the cap stops the run
#      (the report then opens with an INCOMPLETE RUN banner).
#   2. the chatbot recipe (Purdue prompt on gpt-6.1-sol and gpt-6-luna, the careful prompt on
#      gpt-6.1-sol if the cap holds it; with PROVIDER=anthropic the Purdue prompt on
#      claude-opus-5-5 and claude-haiku-5-5), then the figure judge on what they wrote.
#      CHAT_ARMS ("chatbot-opus-purdue chatbot-haiku-purdue") and CHAT_RUNS limit the arms
#      and the runs made of each; they are recorded in the run.
#   3. the open parsers (CPU, hours; sections already converted are not converted again).
#
# The result is a recorded run under $BUNDLE. Copy it to proof/bundles/heldout-2026-10/, make
# that set the default in proof/sets/index.json, and `make proof` writes PROOF.md from it.
set -euo pipefail

: "${ENV_FILE:?set ENV_FILE to the file holding the API key}"
PROVIDER="${PROVIDER:-openai}"
RUN_DIR="${RUN_DIR:-$HOME/.cache/a11y-proof/heldout-run}"
WORK="${WORK:-$RUN_DIR/work}"
BUNDLE="${BUNDLE:-$RUN_DIR/bundle}"
SET_ID="${SET_ID:-heldout-2026-10}"
mkdir -p "$RUN_DIR"

provider_args=()
chat_args=()
if [ "$PROVIDER" != "openai" ]; then
    provider_args=(--provider "$PROVIDER")
fi
if [ -n "${CHAT_RUNS:-}" ]; then
    chat_args+=(--chat-runs "$CHAT_RUNS")
fi
if [ -n "${CHAT_ARMS:-}" ]; then
    # shellcheck disable=SC2206
    chat_args+=(--arms $CHAT_ARMS)
fi

echo "1/3 ReadableSTEM"
uv run --env-file "$ENV_FILE" --group browser readablestem proof --set "$SET_ID" --live --resume \
    "${provider_args[@]}" \
    --max-usd "${A11Y_USD:-12}" --keep-finished --confirm-signoff \
    --work "$WORK" --record "$BUNDLE" --out "$RUN_DIR/PROOF-a11y-only.md"

echo "2/3 chatbot recipe"
uv run --env-file "$ENV_FILE" --group browser readablestem proof --set "$SET_ID" \
    "${provider_args[@]}" "${chat_args[@]}" \
    --bundle "$BUNDLE" --work "$WORK" --add-systems chatbot --max-usd "${CHAT_USD:-10}"

echo "3/3 open parsers"
uv run readablestem proof --set "$SET_ID" --bundle "$BUNDLE" --work "$WORK" --add-systems parsers

uv run readablestem proof --set "$SET_ID" --bundle "$BUNDLE" --work "$WORK" --out "$RUN_DIR/PROOF.md"
echo "recorded run: $BUNDLE; report: $RUN_DIR/PROOF.md"
