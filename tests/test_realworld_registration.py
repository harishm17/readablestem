"""The registered real-world sets still have the files they were hashed with.

`scripts/realworld_run.py` refuses a run when a file named by a registration changed, so
a change to any file listed in a `*_FILES` tuple of `scripts/realworld_register.py` must
fail here, in the pull request, and not at the next run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "realworld_register.py"
SETS = ["heldout", "fresh", "fresh2", "fresh3", "fresh4"]


def _module():
    spec = importlib.util.spec_from_file_location("realworld_register_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", SETS)
def test_registration_matches_the_files(name: str, monkeypatch, capsys) -> None:
    module = _module()
    files = {
        "heldout": module.FILES,
        "fresh": module.FRESH_FILES,
        "fresh2": module.FRESH2_FILES,
        "fresh3": module.FRESH3_FILES,
        "fresh4": module.FRESH4_FILES,
    }[name]
    if not all((module.DATA / f).is_file() for f in files):
        pytest.skip("a registered file is not in this checkout")
    monkeypatch.setattr(sys, "argv", ["realworld_register.py", "--check", "--set", name])
    assert module.main() == 0, capsys.readouterr().err
