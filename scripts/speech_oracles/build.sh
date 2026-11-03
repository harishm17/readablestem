#!/usr/bin/env bash
# Build the two screen-reader speech oracles used by scripts/screenreader_speech.py.
#
#   MathCAT 0.7.7 (the speech engine in NVDA 2026.1 and JAWS), from the tagged source release,
#   with rustup into ~/.cargo (no sudo), plus the small batch tool in ./mathcat_batch.
#   Speech Rule Engine 4.1.5 (the engine in MathJax and ChromeVox), from npm.
#
# Everything lands in .local/speech-oracles/ (git-ignored), or in $SPEECH_ORACLES_DIR. Re-running
# is safe: finished parts are skipped. Needs git, curl or rustup, node and npm.
set -euo pipefail

MATHCAT_TAG=v0.7.7
MATHCAT_SHA=764d24d1241b20c0973e9dbcee62d022c1f99a4a # also pinned in mathcat_batch/Cargo.toml
SRE_VERSION=4.1.5

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
out="${SPEECH_ORACLES_DIR:-$root/.local/speech-oracles}"
mkdir -p "$out"
export PATH="$HOME/.cargo/bin:$PATH"

build_mathcat() {
  if [ -x "$out/mathcat-batch" ] && [ -d "$out/MathCAT/Rules" ]; then
    echo "MathCAT: already built"
    return
  fi
  if ! command -v cargo >/dev/null 2>&1; then
    if command -v rustup >/dev/null 2>&1; then
      rustup default stable
    else
      echo "Installing rustup into ~/.cargo (no sudo)"
      curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
      export PATH="$HOME/.cargo/bin:$PATH"
    fi
  fi
  if [ ! -d "$out/MathCAT/.git" ]; then
    git clone --depth 1 --branch "$MATHCAT_TAG" https://github.com/NSoiffer/MathCAT "$out/MathCAT"
  fi
  got="$(git -C "$out/MathCAT" rev-parse HEAD)"
  if [ "$got" != "$MATHCAT_SHA" ]; then
    echo "MathCAT $MATHCAT_TAG is $got, expected $MATHCAT_SHA; refusing to build" >&2
    exit 1
  fi
  CARGO_TARGET_DIR="$out/cargo-target" cargo build --release --locked \
    --manifest-path "$here/mathcat_batch/Cargo.toml"
  cp "$out/cargo-target/release/mathcat-batch" "$out/mathcat-batch"
  echo "MathCAT: built $out/mathcat-batch"
}

build_sre() {
  if [ -d "$out/sre/node_modules/speech-rule-engine" ]; then
    echo "SRE: already installed"
    return
  fi
  mkdir -p "$out/sre"
  npm install --prefix "$out/sre" --no-audit --no-fund --save-exact "speech-rule-engine@$SRE_VERSION"
  echo "SRE: installed $SRE_VERSION in $out/sre"
}

build_mathcat
build_sre
