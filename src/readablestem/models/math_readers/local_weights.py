"""The local reader's model files: where they come from, the checked download, and the
build-time int8 step.

Source: TexTeller (OleehyO/TexTeller on Hugging Face, Apache-2.0 on the model card and in the
code repository), the ONNX export in the repository at the revision pinned below. Every
source file is pinned by SHA-256 and refused when it differs. The sources are 2.1 GB in
float32 and need about 2 GB of resident memory, which does not fit the conversion child; the
build rewrites the three ONNX files with int8 weights (about 0.6 GB, about 1 GB resident).
That rewrite is an approximation: measured on the 74-expression practice set it reads 0.554
exact against 0.649 for the float32 files, and as a disagreement flag it catches 18 of the 21
wrong readings of the page pass against 17 (`docs/math-readers.md`).

This module uses the standard library only, so the Docker build stage can run it on its own:

    python local_weights.py --dir DIR            fetch, check, quantize into DIR
    python local_weights.py --dir DIR --from SRC use float32 files already in SRC (checked)
    python local_weights.py --dir DIR --fp32     keep the float32 files (development)

Quantizing needs `onnx` and `onnxruntime` (the build stage installs both; the application
needs only `onnxruntime`). Runtime never downloads anything.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

REVISION: Final = "7b96df06b9d81cdb129c3bef68b7250bc3e2b0ea"
BASE_URL: Final = f"https://huggingface.co/OleehyO/TexTeller/resolve/{REVISION}/"
MARKER: Final = "READY"
TIMEOUT_S: Final = 120.0

ENCODER_FILE: Final = "encoder_model.onnx"
PREFILL_FILE: Final = "decoder_model.onnx"
STEP_FILE: Final = "decoder_with_past_model.onnx"
VOCAB_FILE: Final = "vocab.json"
ADDED_TOKENS_FILE: Final = "added_tokens.json"
ONNX_FILES: Final = (ENCODER_FILE, PREFILL_FILE, STEP_FILE)
FILES: Final = (*ONNX_FILES, VOCAB_FILE, ADDED_TOKENS_FILE)


class WeightsError(RuntimeError):
    """A file could not be fetched, does not match its pin, or could not be rewritten."""


@dataclass(frozen=True)
class Source:
    name: str
    sha256: str
    size: int


SOURCES: Final = (
    Source(
        ENCODER_FILE,
        "071afc0a0c92b2ee3847612127eb9e82b1c39413725e692f4405e5ea9e265bf0",
        343_553_824,
    ),
    Source(
        PREFILL_FILE,
        "288cb8e37cdda66f725f2739efd2e7846636666a815e27533891c68389ec3659",
        908_716_081,
    ),
    Source(
        STEP_FILE,
        "ce6b93ffcabf1689d91cc17f217d9065a92af14bcce47f8aa4ae8c798c7882fd",
        833_037_201,
    ),
    Source(
        VOCAB_FILE,
        "6c5fdc7c688b7da8ca9f6abca73ff2c12c08e14386461daf9af1f0716b31e359",
        146_663,
    ),
    Source(
        ADDED_TOKENS_FILE,
        "7e88a0bc018da0461d442e43efdb9c36d0c9ccee4ee77040435a6393c4dc0f0e",
        56_003,
    ),
)

Opener = Callable[[str], IO[bytes]]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_url(url: str) -> IO[bytes]:
    return urllib.request.urlopen(url, timeout=TIMEOUT_S)


def check(path: Path, source: Source) -> None:
    """Raise `WeightsError` unless `path` is the pinned file."""
    if not path.is_file() or path.stat().st_size != source.size:
        raise WeightsError(f"{source.name}: missing or the wrong size")
    found = sha256_of(path)
    if found != source.sha256:
        raise WeightsError(f"{source.name}: checksum mismatch (got {found})")


def download(source: Source, directory: Path, *, opener: Opener = _open_url) -> Path:
    """Fetch one pinned file into `directory`; refused when larger than pinned or when the
    checksum differs."""
    target = directory / source.name
    fd, name = tempfile.mkstemp(prefix=".part-", dir=directory)
    os.close(fd)
    fd_path = Path(name)
    try:
        size = 0
        with opener(BASE_URL + source.name) as reply, fd_path.open("wb") as out:
            while chunk := reply.read(1 << 20):
                size += len(chunk)
                if size > source.size:
                    raise WeightsError(f"{source.name}: download is larger than the file")
                out.write(chunk)
        check(fd_path, source)
        fd_path.replace(target)
        return target
    except (urllib.error.URLError, OSError) as exc:
        raise WeightsError(f"{source.name}: download failed: {type(exc).__name__}") from exc
    finally:
        fd_path.unlink(missing_ok=True)


def quantize(source: Path, target: Path) -> None:
    """Rewrite one ONNX file with int8 weights (dynamic quantization of the matrix products;
    the activations stay float and are scaled at run time)."""
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:  # `onnx` is imported by it
        raise WeightsError("quantizing needs the onnx and onnxruntime packages") from exc
    try:
        quantize_dynamic(
            str(source),
            str(target),
            weight_type=QuantType.QUInt8,
            op_types_to_quantize=["MatMul"],
        )
    except Exception as exc:  # the quantizer raises many kinds; the file is the problem
        raise WeightsError(f"{source.name}: quantizing failed: {type(exc).__name__}") from exc


def ready(directory: Path) -> bool:
    return (directory / MARKER).is_file() and all((directory / f).is_file() for f in FILES)


def prepare(
    directory: Path,
    *,
    source_dir: Path | None = None,
    int8: bool = True,
    opener: Opener = _open_url,
    quantizer: Callable[[Path, Path], None] = quantize,
) -> Path:
    """Put the model files into `directory` (created) unless they are already there. Sources
    come from `source_dir` when given, else from the pinned URL; either way each is checked."""
    if ready(directory):
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".sources-", dir=directory))
    try:
        for source in SOURCES:
            if source_dir is not None:
                check(source_dir / source.name, source)
                shutil.copyfile(source_dir / source.name, work / source.name)
            else:
                download(source, work, opener=opener)
        for source in SOURCES:
            if int8 and source.name in ONNX_FILES:
                quantizer(work / source.name, directory / source.name)
            else:
                shutil.copyfile(work / source.name, directory / source.name)
            (directory / source.name).chmod(0o644)  # the app may run as another user
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (directory / MARKER).write_text(
        f"texteller {REVISION} {'int8' if int8 else 'fp32'}\n", encoding="utf-8"
    )
    return directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the local handwriting reader's model files (SHA-256 checked)."
    )
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--from", dest="source", type=Path, default=None)
    parser.add_argument("--fp32", action="store_true", help="keep float32 (2.1 GB)")
    args = parser.parse_args(argv)
    try:
        path = prepare(args.dir, source_dir=args.source, int8=not args.fp32)
    except (WeightsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201 (command-line output)
        return 1
    print(f"{path} ({(path / MARKER).read_text().strip()})")  # noqa: T201 (command-line output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
