"""The proof run: convert every section PDF with every arm, one fresh process each.

`make proof PDF_DIR=<dir of <book>__<page>.pdf> OUT_DIR=<dir>` calls `run_set`. For each arm
and section it runs the tool (PARSERS-PLAN section 7.4: one process per section, the box
quiet if it can be, a 30-minute limit that scores the section as found nothing), and writes
what the proof's scorer reads:

    <OUT_DIR>/<arm>/<book>__<page>.json    status, wall seconds, pages, memory, versions
    <OUT_DIR>/<arm>/<book>__<page>.md      the tool's Markdown export, as it wrote it

Images embedded in the export (Docling writes them as base64) are cut down to a marker, so
the committed file is text; nothing a score reads is lost (no parser writes a text
alternative into them). Each tool's native output stays under `<work>/<arm>/<section>/`
(scratch, never committed). The JSON holds counts, codes and times only.

An arm whose environment is not set up is recorded as not run; nothing is guessed.
"""

from __future__ import annotations

import json
import os
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from parsers_bench import runners
from parsers_bench.arms import ARMS, Arm, get

IDLE_LOAD = 1.5
IDLE_WAIT_SECONDS = 20 * 60
_DATA_URI = re.compile(r"(data:image/[a-zA-Z+.-]+;base64,)[A-Za-z0-9+/=\s]+")


def section_of(pdf: Path) -> str:
    return pdf.stem.replace("__", "/", 1)


def wait_for_quiet_box(
    sleep: Callable[[float], None] = time.sleep,
    load: Callable[[], float] = lambda: os.getloadavg()[0],
    limit: float = IDLE_WAIT_SECONDS,
) -> dict[str, Any]:
    """Wait (up to `limit` seconds) for the 1-minute load average to fall under 1.5."""
    waited = 0.0
    while load() >= IDLE_LOAD and waited < limit:
        sleep(30)
        waited += 30
    now = load()
    return {"load_at_start": round(now, 2), "waited_seconds": waited, "quiet": now < IDLE_LOAD}


def markdown_of(arm: Arm, native_dir: Path, stem: str) -> str | None:
    if arm.id in ("docling", "docling-formula", "pymupdf4llm"):
        path = native_dir / f"{stem}.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None
    zpath = native_dir / f"{stem}.zip"
    if not zpath.is_file():
        return None
    with zipfile.ZipFile(zpath) as archive:
        if "markdown.md" not in archive.namelist():
            return None
        return archive.read("markdown.md").decode("utf-8")


def strip_images(markdown: str) -> str:
    return _DATA_URI.sub(r"\1omitted", markdown)


def run_set(
    pdf_dir: Path,
    out_dir: Path,
    work_dir: Path,
    *,
    arm_ids: list[str] | None = None,
    timeout_seconds: float = runners.DEFAULT_TIMEOUT_SECONDS,
    offline: bool = False,
    wait_quiet: bool = True,
    say: Callable[[str], None] = print,
) -> int:
    """Returns 0 when every arm that is set up ran on every section (a failed section is
    still recorded and is not an error here), 1 when an arm was missing."""
    arms = [get(a) for a in arm_ids] if arm_ids else list(ARMS.values())
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    missing = 0
    for arm in arms:
        if not (arm.env_dir / "bin").is_dir():
            say(f"{arm.id}: environment missing; run `parsers-bench setup {arm.id}`")
            missing += 1
            continue
        for pdf in pdfs:
            stem = pdf.stem
            meta_path = out_dir / arm.id / f"{stem}.json"
            if meta_path.is_file():
                continue  # recorded already
            quiet = wait_for_quiet_box() if wait_quiet else {"quiet": None}
            run_dir = work_dir / arm.id / stem / "run1"
            if run_dir.exists():  # an interrupted attempt: start the section clean
                for p in sorted(run_dir.rglob("*"), reverse=True):
                    p.unlink() if p.is_file() else p.rmdir()
                run_dir.rmdir()
            result = runners.run_arm(
                arm,
                pdf,
                run_dir,
                section=section_of(pdf),
                timeout_seconds=timeout_seconds,
                offline=offline,
            )
            record = result.record
            markdown = (
                markdown_of(arm, result.native_dir, pdf.stem) if record["status"] == "ok" else None
            )
            status = record["status"]
            if status == "ok" and not (markdown and markdown.strip()):
                status = "empty"
            (out_dir / arm.id).mkdir(parents=True, exist_ok=True)
            output: dict[str, Any] | None = None
            if markdown and markdown.strip():
                (out_dir / arm.id / f"{stem}.md").write_text(strip_images(markdown), "utf-8")
                output = {"file": f"{stem}.md", "format": "md"}
            meas = record["measurement"]
            meta: dict[str, Any] = {
                "arm": arm.id,
                "section": section_of(pdf),
                "status": status,
                "seconds": record["wall_seconds"],
                "pdf_pages": record["input"]["pages"],
                "cost_usd": 0.0,
                "versions": record["versions"],
                "settings": record["settings"],
                "measure": {
                    "wall_seconds_per_page": record["wall_seconds_per_page"],
                    "user_seconds": meas["user_seconds"],
                    "system_seconds": meas["system_seconds"],
                    "peak_rss_mb": round(meas["max_rss_kb"] / 1024, 1),
                    "load_before": meas["load_before"],
                    "load_after": meas["load_after"],
                    "quiet_box": quiet.get("quiet"),
                    "timeout_seconds": meas["timeout_seconds"],
                    "host": record["host"],
                    "offline": record["offline"],
                },
                "input_sha256": record["input"]["input_sha256"],
            }
            if output:
                meta["output"] = output
            meta_path.write_text(
                json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "utf-8"
            )
            say(
                f"{arm.id} {section_of(pdf)}: {status} {record['wall_seconds']} s "
                f"({record['wall_seconds_per_page']} s/page)"
            )
    return 1 if missing else 0
