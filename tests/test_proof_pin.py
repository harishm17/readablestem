"""The public reproduction is pinned to a tag; every place that names it names the same one.

The recorded held-out run replays only through the pipeline as it was when it was recorded.
The Makefile, CI and the documents all point at that tag; if one is renamed alone the
instructions stop working without any test failing. This test needs no git history.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG_SHAPE = re.compile(r"proof-[a-z]+-\d{4}-\d{2}")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _makefile_tag() -> str:
    found = re.search(r"^PROOF_TAG\s*=\s*(\S+)\s*$", _read("Makefile"), re.MULTILINE)
    assert found, "Makefile defines no PROOF_TAG"
    return found.group(1)


def test_makefile_tag_has_the_expected_shape() -> None:
    assert TAG_SHAPE.fullmatch(_makefile_tag())


def test_ci_replays_at_the_makefile_tag() -> None:
    ci = _read(".github/workflows/ci.yml")
    refs = re.findall(r"^\s*ref:\s*(\S+)\s*$", ci, re.MULTILINE)
    assert _makefile_tag() in refs
    # Every tag-shaped name in the workflow is that tag: no stale second one.
    assert set(TAG_SHAPE.findall(ci)) == {_makefile_tag()}


def test_documents_name_the_makefile_tag_and_the_command() -> None:
    tag = _makefile_tag()
    for path in ("README.md", "docs/proof.md"):
        text = _read(path)
        assert f"git checkout {tag}" in text, f"{path} does not say how to reproduce from the tag"
        # No other tag-shaped name in the reproduction instructions.
        stale = set(re.findall(r"git checkout (proof-[\w.-]+)", text)) - {tag}
        assert not stale, f"{path} names another tag: {stale}"


def test_make_proof_points_at_the_tag_when_it_fails() -> None:
    proof = _read("Makefile").split("\nproof:", 1)[1].split("\n\n", 1)[0]
    assert "git checkout $(PROOF_TAG) && make proof" in proof
    assert "exit $$status" in proof  # the failure stays a failure
