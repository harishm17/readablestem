"""W2: EPUBCheck and DAISY Ace on the EPUB export (pipeline/export_epub.py).

Both are external programs. EPUBCheck is a Java jar (https://github.com/w3c/epubcheck);
point `EPUBCHECK_JAR` at it (and `JAVA` at a `java` if it is not on PATH), or put an
`epubcheck` command on PATH. Ace is `npx @daisy/ace`; set `ACE` to its command (or put `ace`
on PATH). A test skips, with the reason, when its tool is missing; CI installs both
(.github/workflows/epub.yml) and sets `REQUIRE_EPUB_VALIDATORS=1`, which turns a skip into a
failure. EPUBCheck must report no errors and no warnings; Ace must report no violation of
serious or critical impact. Neither result is a claim of conformance.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from readablestem.pipeline.export_epub import build_epub, export_epub
from test_export_canvas import source_html, source_ids
from test_export_epub import CHEMISTRY_PAGE, PAGE


def _skip(reason: str):
    if os.environ.get("REQUIRE_EPUB_VALIDATORS"):
        pytest.fail(reason)
    pytest.skip(reason)


def _epubcheck() -> list[str] | None:
    jar = os.environ.get("EPUBCHECK_JAR")
    java = os.environ.get("JAVA") or shutil.which("java")
    if jar and os.path.exists(jar) and java:
        return [java, "-jar", jar]
    command = shutil.which("epubcheck")
    return [command] if command else None


def _ace() -> list[str] | None:
    command = os.environ.get("ACE")
    if command:
        return command.split()
    found = shutil.which("ace")
    return [found] if found else None


DOCUMENTS = [*source_ids(), "synthetic", "chemistry"]


def _epub(tmp_path, name: str):
    html = {"synthetic": PAGE, "chemistry": CHEMISTRY_PAGE}.get(name) or source_html(name)
    data = build_epub(export_epub(html, modified=datetime(2026, 11, 2, tzinfo=UTC)))
    path = tmp_path / "book.epub"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("name", DOCUMENTS)
def test_epubcheck_finds_nothing(tmp_path, name):
    command = _epubcheck()
    if command is None:
        _skip("EPUBCheck not available: set EPUBCHECK_JAR (and Java), see this file")
        return
    book = _epub(tmp_path, name)
    run = subprocess.run(
        [*command, str(book)], capture_output=True, text=True, timeout=180, check=False
    )
    assert run.returncode == 0 and "No errors or warnings detected" in run.stdout, (
        run.stdout + run.stderr
    )[-3000:]


@pytest.mark.parametrize("name", ["golden/sample", "demo", "synthetic"])
def test_ace_finds_no_serious_violation(tmp_path, name):
    command = _ace()
    if command is None:
        _skip("DAISY Ace not available: set ACE (for example 'npx -y @daisy/ace')")
        return
    book = _epub(tmp_path, name)
    report = tmp_path / "ace"
    env = {**os.environ, "ELECTRON_DISABLE_SANDBOX": "1"}
    run = subprocess.run(
        [*command, "-o", str(report), "-f", str(book)],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )
    assert (report / "report.json").exists(), (run.stdout + run.stderr)[-3000:]
    results = json.loads((report / "report.json").read_text(encoding="utf-8"))
    serious = [
        f"{part['earl:testSubject']['url']}: {a['earl:test']['dct:title']}"
        for part in results.get("assertions", [])
        for a in part.get("assertions", [])
        if a["earl:result"]["earl:outcome"] != "passed"
        and a["earl:test"].get("earl:impact") in ("serious", "critical")
    ]
    assert not serious, serious
