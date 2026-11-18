"""Runs inside the olmOCR-Bench checker's environment (`readablestem eval parsing-bench --fetch`
builds it), not the harness's.

    python -I olmocr_check.py BENCH_DIR MARKDOWN_DIR OUT.json FILE.jsonl [FILE.jsonl ...]

The benchmark's own checker (`olmocr.bench.tests`, the pinned release) applied to the markdown
files `MARKDOWN_DIR/<pdf path without .pdf>.md` that exist: one per page of a one-page PDF,
named as the PDF is in the benchmark's files. As `olmocr.bench.benchmark` does, every PDF
gets one baseline check unless its file lists one. A check whose markdown is missing is
reported as `no_output` and is the caller's to count as dropped.

Writes one row per check: id, pdf, type, passed, the checker's explanation (which quotes the
document's text, so the file stays out of the repository). The math checks are not loaded
here: they render each equation in a browser, which this run does not have.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    bench, markdown, out, files = (
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        sys.argv[4:],
    )
    from olmocr.bench.tests import BaselineTest, load_tests

    tests = []
    for name in files:
        tests += [(Path(name).stem, test) for test in load_tests(str(bench / "bench_data" / name))]
    listed = {test.pdf for _, test in tests if test.type == "baseline"}
    for pdf in sorted({test.pdf for _, test in tests} - listed):
        tests.append(
            (
                "baseline",
                BaselineTest(id=f"{pdf}_baseline", pdf=pdf, page=1, type="baseline"),
            )
        )
    rows = []
    for source, test in tests:
        path = markdown / (test.pdf.removesuffix(".pdf") + ".md")
        row = {"id": test.id, "pdf": test.pdf, "type": test.type, "file": source}
        if not path.is_file():
            rows.append(row | {"passed": None, "explanation": "no_output"})
            continue
        try:
            passed, explanation = test.run(path.read_text(encoding="utf-8"))
        except Exception as exc:
            passed, explanation = False, f"checker raised {type(exc).__name__}"
        rows.append(row | {"passed": bool(passed), "explanation": explanation})
    out.write_text(json.dumps(rows), encoding="utf-8")
    print(json.dumps({"checks": len(rows)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
