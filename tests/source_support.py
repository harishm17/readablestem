"""Shared helpers for the LaTeX source tests: fixtures, a PNG, zips, and a LaTeXML check.

LaTeXML is the distribution's package (Dockerfile, `INSTALL_LATEXML`); where it is not installed
the tests that run it skip, and `READABLESTEM_LATEXML_ROOT` can point at unpacked packages
(docs/development.md, "LaTeX source tests")."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from readablestem.core import sandbox
from readablestem.source import latexml

FIXTURES = Path(__file__).parent / "fixtures" / "source"

# CI's `latex source` job sets this: there a missing LaTeXML or sandbox is a failure to see, not
# a skip to scroll past.
REQUIRED = bool(os.environ.get("READABLESTEM_REQUIRE_SANDBOX_TESTS"))

needs_latexml = pytest.mark.skipif(
    not REQUIRED and not (latexml.installed() and sandbox.available()),
    reason="needs LaTeXML and a working sandbox (bubblewrap or Landlock)",
)


def png_bytes(width: int = 40, height: int = 30, colour: tuple[int, int, int] = (200, 30, 30)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


def make_zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
