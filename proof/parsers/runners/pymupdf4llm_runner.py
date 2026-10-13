"""Runs inside the PyMuPDF4LLM arm's environment (arms/pymupdf4llm), not the harness's.

    python pymupdf4llm_runner.py INPUT.pdf OUTPUT.md

Defaults only: `to_markdown(path)`. The layout model is on because `pymupdf-layout` is
installed in that environment; images are not written. Prints one content-free line
(timings and sizes) for the harness log.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    pdf, out = Path(sys.argv[1]), Path(sys.argv[2])
    started = time.perf_counter()
    import pymupdf4llm

    imported = time.perf_counter()
    markdown = pymupdf4llm.to_markdown(str(pdf))
    converted = time.perf_counter()
    out.write_text(markdown, encoding="utf-8")
    print(
        json.dumps(
            {
                "import_seconds": round(imported - started, 3),
                "convert_seconds": round(converted - imported, 3),
                "characters": len(markdown),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
