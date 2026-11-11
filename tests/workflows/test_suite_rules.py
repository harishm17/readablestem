"""The workflow suite's own rules, checked by reading its files (CONTRIBUTING.md).

A workflow is one file, `test_wfNN_<name>.py`, with a number of its own. It converts only
files the corpus manifest lists. An assertion that an open finding makes false is marked
with `harness.finding`, which is strict, and in no other way.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

from readablestem.eval import corpus

HERE = Path(__file__).parent
# Tests about the suite and its corpus, not workflows.
NOT_WORKFLOWS = {"test_corpus_manifest.py", "test_replay_complete.py", "test_suite_rules.py"}
WORKFLOWS = sorted(set(HERE.glob("test_*.py")) - {HERE / name for name in NOT_WORKFLOWS})
# WF1 to WF52 are the numbers the plan gives out.
NUMBERS = range(1, 53)


def calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_each_workflow_is_one_file_with_its_own_number() -> None:
    assert WORKFLOWS
    numbers = []
    for path in WORKFLOWS:
        named = re.fullmatch(r"test_wf(\d\d)_[a-z0-9_]+\.py", path.name)
        assert named, f"{path.name}: a workflow file is named test_wfNN_<name>.py"
        numbers.append(int(named[1]))
    assert set(numbers) <= set(NUMBERS)
    assert [number for number, count in Counter(numbers).items() if count > 1] == []


def test_a_workflow_converts_only_files_of_the_manifest() -> None:
    listed = set(corpus.load_manifest())
    for path in WORKFLOWS:
        asked = {
            call.args[0].value
            for call in calls(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(call.func, ast.Attribute)
            and call.func.attr in {"convert", "document"}
            and call.args
            and isinstance(call.args[0], ast.Constant)
        }
        assert asked, f"{path.name} converts no corpus file by name"
        assert asked <= listed, f"{path.name}: not in the manifest: {sorted(asked - listed)}"


def test_an_expected_failure_is_always_a_strict_finding() -> None:
    for path in sorted(HERE.glob("test_*.py")):
        names = {
            node.attr
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute)
        }
        assert "xfail" not in names, f"{path.name}: mark an open finding with harness.finding"
