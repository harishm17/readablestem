"""Rebuild the LaTeX layout fixtures: every *.tex in this folder -> a PDF beside it.

Usage:
    uv run python tests/fixtures/layout/latex/make_pdfs.py [--tectonic PATH]

Needs Tectonic (https://tectonic-typesetting.github.io), a self-contained TeX engine:
download a release binary, e.g.

    gh release download tectonic@0.17.0 -R tectonic-typesetting/tectonic \
        -p 'tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz' -D /tmp/tectonic
    tar xzf /tmp/tectonic/*.tar.gz -C /tmp/tectonic

and pass `--tectonic /tmp/tectonic/tectonic` (or put it on PATH, or set TECTONIC).
The first run downloads the TeX files it needs into Tectonic's cache.

Builds are deterministic (`-Z deterministic-mode`, fixed SOURCE_DATE_EPOCH), so a
rebuild with the same Tectonic version gives byte-identical PDFs. The .tex files are the
source of truth; the PDFs are committed so the test run needs no TeX. Commit rebuilt PDFs
only after `uv run pytest tests/test_layout_latex.py` passes on them, and keep each under
300 KB (the build fails otherwise).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_DATE_EPOCH = "1791331200"  # 2026-10-07 00:00 UTC
MAX_BYTES = 300_000


def build(tex: Path, tectonic: str) -> Path:
    """Compile `tex` in a scratch folder and copy the PDF next to it."""
    env = {**os.environ, "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}
    with tempfile.TemporaryDirectory(prefix="a11y-latex-") as scratch:
        work = Path(scratch)
        shutil.copy2(tex, work / tex.name)
        subprocess.run(
            [tectonic, "-Z", "deterministic-mode", "--chatter", "minimal", tex.name],
            cwd=work,
            env=env,
            check=True,
        )
        target = tex.with_suffix(".pdf")
        shutil.copyfile(work / target.name, target)
    size = target.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"{target.name} is {size} bytes; keep fixtures under {MAX_BYTES}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--tectonic", default=os.environ.get("TECTONIC") or "tectonic")
    parser.add_argument("names", nargs="*", help="fixture names (default: all)")
    args = parser.parse_args(argv)
    if shutil.which(args.tectonic) is None:
        print(f"Tectonic not found: {args.tectonic} (see this script's docstring)")
        return 2
    sources = sorted(HERE.glob("*.tex"))
    if args.names:
        sources = [s for s in sources if s.stem in args.names]
    for tex in sources:
        pdf = build(tex, args.tectonic)
        print(f"wrote {pdf.relative_to(HERE)} ({pdf.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
