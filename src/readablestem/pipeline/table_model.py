"""The SLANet-plus table-structure model file: where it lives and how it is fetched.

`pipeline.tables` recognises a table's rows, columns and spans with SLANet-plus
(PaddlePaddle, Apache-2.0), the ONNX file RapidTable 3.0.2 ships. The file (7.5 MB) is not
in the repository: the Docker build fetches it once, and developers fetch it with
`make table-model`. It is pinned by SHA-256, so a changed or truncated download is
refused instead of loaded.

Where it is looked for: `$TABLE_MODEL_DIR/slanet-plus.onnx`, by default
`~/.cache/a11y-stem/models` (`$XDG_CACHE_HOME` when set); the image sets
`TABLE_MODEL_DIR=/app/models`. When the file is missing, table detection still works and
builds grids from PyMuPDF's own table grid instead (`tables.structure` says why, without
content).

Run `python -m readablestem.pipeline.table_model [--dir DIR] [--from FILE]` to fetch it (or
to copy and check a file you already have).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

from readablestem.core.paths import cache_dir

MODEL_FILE = "slanet-plus.onnx"
# RapidTable 3.0.2's default_models.yaml ("slanet_plus"): the same URL and checksum.
MODEL_URL = "https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx"
MODEL_SHA256 = "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b"
MODEL_BYTES = 7_758_305
MODEL_DIR_ENV = "TABLE_MODEL_DIR"
_MAX_BYTES = 16 * 1024 * 1024  # refuse anything far larger than the real file
_TIMEOUT_S = 120.0


class ModelFileError(RuntimeError):
    """The model file could not be fetched or does not match its checksum."""


def model_dir() -> Path:
    """`$TABLE_MODEL_DIR`, else the user cache directory."""
    configured = os.environ.get(MODEL_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    return cache_dir("models")


def model_path() -> Path:
    return model_dir() / MODEL_FILE


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install(source: Path, target: Path) -> Path:
    """Check `source` against the pinned checksum, then move it into place atomically."""
    found = sha256_of(source)
    if found != MODEL_SHA256:
        raise ModelFileError(f"checksum mismatch: expected {MODEL_SHA256}, got {found}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.chmod(0o644)  # mkstemp makes it 0600; the app may run as another user
    os.replace(source, target)
    return target


def fetch(directory: Path | None = None, *, client: httpx.Client | None = None) -> Path:
    """Download the model into `directory` (default `model_dir()`) unless a file with the
    right checksum is already there. Returns its path; raises `ModelFileError`."""
    target = (directory or model_dir()) / MODEL_FILE
    if target.is_file() and sha256_of(target) == MODEL_SHA256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    http = client or httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True)
    fd, tmp = tempfile.mkstemp(prefix=".slanet-", suffix=".part", dir=target.parent)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out, http.stream("GET", MODEL_URL) as response:
            if response.status_code != 200:
                raise ModelFileError(f"download failed: HTTP {response.status_code}")
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ModelFileError("download is larger than the model file")
                out.write(chunk)
        return _install(Path(tmp), target)
    except httpx.HTTPError as exc:
        raise ModelFileError(f"download failed: {type(exc).__name__}") from exc
    finally:
        Path(tmp).unlink(missing_ok=True)
        if own:
            http.close()


def copy_from(source: Path, directory: Path | None = None) -> Path:
    """Install a copy of a model file you already have (checked like a download)."""
    target = (directory or model_dir()) / MODEL_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".slanet-", suffix=".part", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        return _install(Path(tmp), target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m readablestem.pipeline.table_model",
        description=f"Fetch the SLANet-plus table model ({MODEL_FILE}, SHA-256 checked).",
    )
    parser.add_argument("--dir", type=Path, default=None, help=f"default: {model_dir()}")
    parser.add_argument(
        "--from", dest="source", type=Path, default=None, help="install a local copy instead"
    )
    args = parser.parse_args(argv)
    try:
        path = copy_from(args.source, args.dir) if args.source else fetch(args.dir)
    except (ModelFileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{path} (sha256 {MODEL_SHA256[:12]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
