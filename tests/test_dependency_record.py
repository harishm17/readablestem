"""The dependency record (`scripts/dependency_record.py`, docs/dependencies.md): what a change
of the lock, the facts or the Dockerfile must bring with it, and that the command line starts
with the packages of the image alone."""

from __future__ import annotations

import ast
import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "dependency_record", ROOT / "scripts" / "dependency_record.py"
)
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)


def test_the_record_covers_every_dependency_of_this_repository():
    assert record.check(ROOT) == []


def a_copy(folder: Path) -> Path:
    """The files the record is made of, and an empty file for each hostile-input test named."""
    for name in ("uv.lock", "Dockerfile", record.FACTS, record.RECORD):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, folder / name)
    facts = tomllib.loads((folder / record.FACTS).read_text(encoding="utf-8"))
    for entry in (*facts["python"].values(), *facts["system"].values()):
        for path in entry.get("hostile", []):
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).touch()
    return folder


def a_new_package(folder: Path) -> None:
    with (folder / "uv.lock").open("a", encoding="utf-8") as lock:
        lock.write('\n[[package]]\nname = "newly-added"\nversion = "1.0"\n')


def a_license_nobody_accepted(folder: Path) -> None:
    path = folder / record.FACTS
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("[accepted.certifi]", "[accepted.idna]"), encoding="utf-8")


def a_system_package_with_no_version(folder: Path) -> None:
    path = folder / "Dockerfile"
    text = path.read_text(encoding="utf-8")
    assert "install-recommends pandoc=" in text
    path.write_text(text.replace("install-recommends pandoc=", "install-recommends pandoc #"))


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (a_new_package, "newly-added 1.0: no row"),
        (a_license_nobody_accepted, "the license 'MPL-2.0' is not on the allowed list"),
        (a_system_package_with_no_version, "pandoc: the Dockerfile names no version"),
    ],
)
def test_a_change_the_record_does_not_cover_fails(tmp_path, change, problem):
    folder = a_copy(tmp_path)
    assert record.check(folder) == []
    change(folder)
    assert any(problem in line for line in record.check(folder))


START = """
import sys

class Absent:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {modules!r}:
            raise ModuleNotFoundError(name)

sys.meta_path.insert(0, Absent())
from readablestem.cli.main import main

main(["--help"])
"""


def test_the_command_line_starts_with_only_the_packages_of_the_image():
    # A module the command line loads at its start once imported a development package: in
    # the image every command, the build's own `ops image-facts` among them, would have failed.
    outside = record.outside_image(ROOT)
    modules = {
        module
        for module, packages in importlib.metadata.packages_distributions().items()
        if {name.lower().replace("_", "-") for name in packages} <= outside
    }
    assert {"lxml", "pytest"} <= modules
    done = subprocess.run(
        [sys.executable, "-c", START.format(modules=modules)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]


def importers(module: str) -> set[str]:
    source = ROOT / "src" / "readablestem"
    found = set()
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            if any(name == module or name.startswith(f"{module}.") for name in names):
                found.add(path.relative_to(source).as_posix())
    return found


@pytest.mark.parametrize(
    ("module", "allowed"),
    [
        # The AGPL layout add-on is accepted for table proposals and for nothing else.
        ("pymupdf.layout", {"pipeline/tables.py"}),
        # A development package: only the gold builder, a tool the image never runs, loads it.
        ("lxml", {"gold/layout_gold.py"}),
    ],
)
def test_a_package_with_a_rule_is_imported_only_where_the_rule_allows(module, allowed):
    assert importers(module) == allowed
