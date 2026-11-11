"""Builders of the test files the workflow suite and the evals read.

A builder is a function that takes nothing and returns the bytes of one file. It is named for
what it builds, and it is registered with `@fixture`, which gives the file its name and says
where the case came from. Nothing here copies a real course file: every page is made from
text written for the test.

Most builders give the same bytes on every run, and `recorded.json` holds the SHA-256 of
each. `tests/workflows/test_corpus_manifest.py` builds every file twice, once in this process
and once in a new one, and compares both to the record. The hashes are those of the locked
environment (PyMuPDF from `uv.lock`); a PyMuPDF upgrade that changes them re-records them in
the same change (`uv run python tests/workflows/builders/record.py`).

A few files cannot be byte-stable: an encrypted PDF carries a fresh random key each time.
Those say why in `unstable` and give a `check` that reads the bytes and raises when the
property the test needs is missing.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RECORD = Path(__file__).with_name("recorded.json")

# The modules that register fixtures, in the order the files are listed.
MODULES = ("hostile", "hostile_sources", "edge", "archives", "pages", "forms", "latex")


@dataclass(frozen=True)
class Fixture:
    name: str  # the file name a test uploads, with its extension
    make: Callable[[], bytes]
    origin: str  # which round made the case first, and when
    shows: str  # what the file is for, in one line
    unstable: str = ""  # why two runs differ, when they do
    check: Callable[[bytes], None] | None = None  # for an unstable file: raises when wrong
    slow: bool = False  # takes seconds: built only under the `slow` marker


FIXTURES: dict[str, Fixture] = {}


def fixture(
    name: str,
    *,
    origin: str,
    shows: str,
    unstable: str = "",
    check: Callable[[bytes], None] | None = None,
    slow: bool = False,
) -> Callable[[Callable[[], bytes]], Callable[[], bytes]]:
    def register(make: Callable[[], bytes]) -> Callable[[], bytes]:
        if name in FIXTURES:
            raise ValueError(f"two builders make {name}")
        if bool(unstable) != (check is not None):
            raise ValueError(f"{name}: an unstable file needs a check, and only it has one")
        FIXTURES[name] = Fixture(name, make, origin, shows, unstable, check, slow)
        return make

    return register


def load() -> dict[str, Fixture]:
    """Every registered fixture, by file name."""
    for module in MODULES:
        importlib.import_module(f"{__name__}.{module}")
    return FIXTURES


def build(name: str) -> bytes:
    """The bytes of one fixture."""
    return load()[name].make()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def recorded() -> dict[str, dict]:
    """The recorded hash and size of each byte-stable fixture."""
    return json.loads(RECORD.read_text(encoding="utf-8"))
