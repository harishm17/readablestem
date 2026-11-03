#!/usr/bin/env bash
# Build the two screen-reader speech oracles used by scripts/screenreader_speech.py.
#
#   MathCAT (the speech engine in NVDA 2026.1 and JAWS), from the tagged source release, plus
#   the small batch tool in ./mathcat_batch, built with the Rust toolchain pinned in
#   mathcat_batch/rust-toolchain.toml.
#   Speech Rule Engine (the engine in MathJax and ChromeVox), from npm, installed from the
#   committed sre/package-lock.json with `npm ci --ignore-scripts`.
#
# Versions and checksums live in versions.env (read by the Python side too). Everything lands in
# .local/speech-oracles/ (git-ignored), or in $SPEECH_ORACLES_DIR. Re-running is safe: finished
# parts are skipped. Needs git, node and npm; Rust comes from an existing rustup or cargo, or is
# installed into ~/.cargo (no sudo) from a rustup-init whose SHA-256 is pinned in versions.env.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
. "$here/versions.env"
root="$(cd "$here/../.." && pwd)"
out="${SPEECH_ORACLES_DIR:-$root/.local/speech-oracles}"
mkdir -p "$out"
export PATH="$HOME/.cargo/bin:$PATH"

install_rustup() {
  local arch triple expected tmp
  arch="$(uname -m)"
  case "$arch" in
    x86_64) triple=x86_64-unknown-linux-gnu; expected="$RUSTUP_SHA256_x86_64" ;;
    aarch64 | arm64) triple=aarch64-unknown-linux-gnu; expected="$RUSTUP_SHA256_aarch64" ;;
    *)
      echo "No pinned rustup-init for $arch; install Rust yourself, then re-run" >&2
      exit 1
      ;;
  esac
  tmp="$(mktemp -d)"
  curl --proto '=https' --tlsv1.2 -sSfL -o "$tmp/rustup-init" \
    "https://static.rust-lang.org/rustup/archive/$RUSTUP_VERSION/$triple/rustup-init"
  echo "$expected  $tmp/rustup-init" | sha256sum -c - >/dev/null \
    || { echo "rustup-init checksum mismatch; refusing to run it" >&2; exit 1; }
  chmod +x "$tmp/rustup-init"
  echo "Installing rustup $RUSTUP_VERSION into ~/.cargo (no sudo)"
  "$tmp/rustup-init" -y --profile minimal --no-modify-path --default-toolchain none
  rm -f "$tmp/rustup-init"
  rmdir "$tmp"
}

build_mathcat() {
  if [ -x "$out/mathcat-batch" ] && [ -d "$out/MathCAT/Rules" ]; then
    echo "MathCAT: already built"
    return
  fi
  if ! command -v cargo >/dev/null 2>&1; then
    command -v rustup >/dev/null 2>&1 || install_rustup
  fi
  if [ ! -d "$out/MathCAT/.git" ]; then
    git clone --depth 1 --branch "$MATHCAT_TAG" https://github.com/NSoiffer/MathCAT "$out/MathCAT"
  fi
  got="$(git -C "$out/MathCAT" rev-parse HEAD)"
  if [ "$got" != "$MATHCAT_SHA" ]; then
    echo "MathCAT $MATHCAT_TAG is $got, expected $MATHCAT_SHA; refusing to build" >&2
    exit 1
  fi
  # rust-toolchain.toml in mathcat_batch pins the compiler; rustup fetches it if missing.
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
  cp "$here/sre/package.json" "$here/sre/package-lock.json" "$out/sre/"
  (cd "$out/sre" && npm ci --ignore-scripts --no-audit --no-fund)
  echo "SRE: installed $SRE_VERSION in $out/sre"
}

build_mathcat
build_sre
