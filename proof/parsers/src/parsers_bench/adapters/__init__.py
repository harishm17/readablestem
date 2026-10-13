"""One adapter per tool: native output in, `Intermediate` out.

`adapt_run` reads a run directory written by `runners.run_arm`, picks the arm's adapter, and
records what the runner measured. A run that crashed, timed out or wrote nothing becomes a
document with that status and no content: the scorer then counts every gold item of the
section as missed (PREREG section 9), and nothing is silently dropped.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from parsers_bench import intermediate, runners
from parsers_bench.adapters import docling, mineru, pymupdf4llm
from parsers_bench.arms import Arm
from parsers_bench.intermediate import Intermediate


def adapt_run(arm: Arm, run_dir: Path, *, section: str) -> Intermediate:
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    native = runners.native_output(arm, run_dir / "native", Path(run["native_output"]).stem)
    status = run["status"]
    if status != "ok":
        doc = Intermediate(arm=arm.id, section=section)
        doc.notes.append(f"the run ended with status {status}; no output was read")
    else:
        try:
            doc = _read(arm, native, section)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            doc = Intermediate(arm=arm.id, section=section)
            status = "crashed"
            doc.notes.append(f"the native output could not be read: {type(exc).__name__}")
        else:
            counts = doc.counts()
            if not (
                counts["blocks"]
                or counts["headings"]
                or counts["equations"]
                or counts["figures"]
                or counts["tables"]
            ):
                status = "empty"
    doc.arm = arm.id
    doc.status = status
    doc.seconds = run["wall_seconds"]
    doc.versions = {**doc.versions, **run["versions"]}
    doc.settings = run["settings"]
    html = run_dir / "native" / f"{native.stem}.html"
    doc.html_path = str(html.relative_to(run_dir)) if html.is_file() else None
    markdown = export_markdown(arm, run_dir, native) if status == "ok" else None
    doc.markdown_path = str(markdown.relative_to(run_dir)) if markdown else None
    problems = intermediate.validate(doc)
    if problems:
        raise ValueError(f"{arm.id}: intermediate form is not well formed: {problems[:3]}")
    return doc


def export_markdown(arm: Arm, run_dir: Path, native: Path) -> Path | None:
    """The tool's own Markdown as a plain file, for scorers that read Markdown (the held-out
    invented-content reader takes a parser's Markdown). Docling and PyMuPDF4LLM write it
    directly; MinerU's is inside the zip and is extracted next to it."""
    if arm.id == "mineru-basic":
        target = run_dir / "native" / f"{native.stem}.md"
        try:
            with zipfile.ZipFile(native) as archive:
                target.write_bytes(archive.read("markdown.md"))
        except (KeyError, OSError, zipfile.BadZipFile):
            return None
        return target
    path = run_dir / "native" / f"{native.stem}.md"
    return path if path.is_file() else None


def _read(arm: Arm, native: Path, section: str) -> Intermediate:
    if arm.id in ("docling", "docling-formula"):
        return docling.adapt_file(native, section=section)
    if arm.id == "mineru-basic":
        return mineru.adapt_zip(native, section=section)
    if arm.id == "pymupdf4llm":
        return pymupdf4llm.adapt_file(native, section=section)
    raise ValueError(f"no adapter for {arm.id}")
