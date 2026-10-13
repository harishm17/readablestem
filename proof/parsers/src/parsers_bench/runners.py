"""One runner per arm: build the command, run it in the arm's own environment, keep the
tool's native output untouched, and write a content-free `run.json` beside it.

Layout of one run directory (`<out>/<arm>/<section>/run<N>/`):

    native/      what the tool wrote, as it wrote it (JSON, HTML, Markdown, zip, images)
    tool.log     the tool's stdout and stderr (scratch; may hold file names, never committed)
    run.json     arm, versions, settings, input hash and page count, status, wall time per
                 page, CPU time, peak memory, load average, host: counts and codes only

The adapters (`adapters/`) turn `native/` into the shared intermediate form afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parsers_bench import measure
from parsers_bench.arms import RUNNERS_DIR, Arm, mineru_home

DEFAULT_TIMEOUT_SECONDS = 30 * 60  # PREREG section 9: a 30-minute timeout is a section failure


@dataclass(frozen=True)
class RunResult:
    run_dir: Path
    native_dir: Path
    run_json: Path
    record: dict


def command(arm: Arm, pdf: Path, native_dir: Path) -> tuple[list[str], dict[str, str]]:
    """The exact command and the environment variables it needs, per arm."""
    bin_dir = arm.env_dir / "bin"
    env: dict[str, str] = {}
    if arm.id in ("docling", "docling-formula"):
        argv = [str(bin_dir / "docling")]
        if arm.id == "docling-formula":
            argv.append("--enrich-formula")
        argv += [
            "--to",
            "json",
            "--to",
            "html",
            "--to",
            "md",
            "--output",
            str(native_dir),
            str(pdf),
        ]
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    elif arm.id == "mineru-basic":
        argv = [
            str(bin_dir / "mineru-kit"),
            "parse",
            str(pdf),
            "-o",
            str(native_dir),
            "--tier",
            "basic",
            "--format",
            "zip",
        ]
        env["MINERU_HOME"] = str(mineru_home())
    elif arm.id == "pymupdf4llm":
        argv = [
            str(bin_dir / "python"),
            str(RUNNERS_DIR / "pymupdf4llm_runner.py"),
            str(pdf),
            str(native_dir / f"{pdf.stem}.md"),
        ]
    else:  # pragma: no cover - guarded by arms.get
        raise ValueError(arm.id)
    return argv, env


def native_output(arm: Arm, native_dir: Path, stem: str) -> Path:
    """The file the adapter reads."""
    if arm.id in ("docling", "docling-formula"):
        return native_dir / f"{stem}.json"
    if arm.id == "mineru-basic":
        return native_dir / f"{stem}.zip"
    return native_dir / f"{stem}.md"


def versions(arm: Arm) -> dict[str, str]:
    """Installed versions of the arm's key packages, read from its own environment."""
    code = (
        "import importlib.metadata as m, json, sys\n"
        "out = {}\n"
        "for name in sys.argv[1:]:\n"
        "    try:\n"
        "        out[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n"
        "        out[name] = 'not installed'\n"
        "print(json.dumps(out))\n"
    )
    python = arm.env_dir / "bin" / "python"
    done = subprocess.run(
        [str(python), "-c", code, *arm.packages],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def pdf_facts(pdf: Path) -> dict[str, Any]:
    import pymupdf

    with pymupdf.open(pdf) as document:
        pages = document.page_count
    return {
        "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "input_bytes": pdf.stat().st_size,
        "pages": pages,
    }


def run_arm(
    arm: Arm,
    pdf: Path,
    run_dir: Path,
    *,
    section: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    offline: bool = False,
) -> RunResult:
    """Convert `pdf` once with `arm`, in a fresh process, and record how it went."""
    if not (arm.env_dir / "bin").is_dir():
        raise SystemExit(
            f"the {arm.project} environment is missing: run `parsers-bench setup {arm.id}`"
        )
    native_dir = run_dir / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    argv, extra_env = command(arm, pdf, native_dir)
    env = {**os.environ, **{k: v for k, v in extra_env.items() if v != ""}}
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
    env.pop("VIRTUAL_ENV", None)
    measurement = measure.run_measured(
        argv, env=env, log_path=run_dir / "tool.log", timeout_seconds=timeout_seconds
    )
    facts = pdf_facts(pdf)
    expected = native_output(arm, native_dir, pdf.stem)
    status = measurement.status
    if status == "ok" and (not expected.is_file() or expected.stat().st_size == 0):
        status = "empty"
    pages = facts["pages"]
    record = {
        "arm": arm.id,
        "plan_id": arm.plan_id,
        "section": section,
        "status": status,
        "settings": arm.settings,
        "versions": versions(arm),
        "input": facts,
        "native_output": expected.name,
        "wall_seconds": measurement.wall_seconds,
        "wall_seconds_per_page": round(measurement.wall_seconds / pages, 3) if pages else None,
        "measurement": measurement.to_dict(),
        "host": measure.host_info(),
        "offline": offline,
        "api_cost_usd": 0.0,
        "api_calls": 0,
    }
    run_json = run_dir / "run.json"
    run_json.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return RunResult(run_dir, native_dir, run_json, record)
