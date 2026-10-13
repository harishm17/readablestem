"""The registered files in `proof/` can be checked from the repository alone.

`proof/REGISTERED.md` says what can and cannot be checked. These tests do the checks that can
be done offline: every registered file has the SHA-256 in `PREREG.sha256`, the seeded draw
reproduces the committed draw, and the sections that survived the page rules are the held-out
set's sections.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from a11y_stem.proof import registry

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / "proof"
HASH_FILES = ("PREREG.sha256", "PREREG-after-review.sha256")


def _lines(name: str) -> list[tuple[str, str]]:
    out = []
    for line in (PROOF / name).read_text(encoding="utf-8").splitlines():
        digest, _, path = line.partition("  ")
        if digest and path:
            out.append((digest, path))
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", HASH_FILES)
def test_every_registered_file_in_the_repository_has_the_registered_hash(name):
    checked = 0
    for digest, rel in _lines(name):
        if rel.startswith("heldout-gold/"):  # the answer key is fetched, not committed
            continue
        path = PROOF / rel
        assert path.is_file(), f"{rel} is listed in {name} but is not in the repository"
        assert _sha(path) == digest, f"{rel} differs from its registered hash"
        checked += 1
    assert checked >= 1


def test_the_first_hash_file_lists_the_registration_and_the_four_first_addenda():
    listed = {rel for _, rel in _lines("PREREG.sha256")}
    expected = {"PREREGISTRATION.md", "PARSERS-PLAN.md"} | {
        f"PREREGISTRATION-ADDENDUM-{n}.md" for n in (1, 2, 3, 4)
    }
    assert expected <= listed
    assert {"selection/draw.py", "selection/features.py", "selection/rules-before-draw.sha256"} <= (
        listed
    )


def test_the_registration_hash_is_the_one_the_set_the_code_and_the_readme_cite():
    registered = dict((rel, d) for d, rel in _lines("PREREG.sha256"))["PREREGISTRATION.md"]
    pset = registry.resolve_set(ROOT, "heldout-2026-10")
    assert pset.prereg is not None and pset.prereg.sha256 == registered
    assert registered in (ROOT / "README.md").read_text(encoding="utf-8")
    assert registered in (ROOT / "src/a11y_stem/gold/heldout.py").read_text(encoding="utf-8")


def test_the_seeded_draw_reproduces_the_committed_draw(tmp_path):
    out = tmp_path / "draw.json"
    done = subprocess.run(
        [sys.executable, "-I", "draw.py", "features.json", str(out)],
        cwd=PROOF / "selection",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == (PROOF / "selection" / "draw.json").read_bytes()
    seed_text = json.loads(out.read_text())["seed_text"]
    assert seed_text == "a11y-stem held-out OpenStax set, pre-registered 2026-10-13"


def test_the_sections_that_survived_the_page_rules_are_the_held_out_sections():
    budget = json.loads((PROOF / "selection" / "budget.json").read_text())
    assert budget["ok"] is True
    chosen = {f"{s['book']}/{s['slug']}" for s in budget["selected"]}
    pset = registry.resolve_set(ROOT, "heldout-2026-10")
    assert chosen == {s.id for s in pset.sections}
    assert len(chosen) == 15
    assert 150 <= budget["total_pages"] <= 250


def test_the_rules_were_hashed_before_the_draw_and_the_files_still_match_them():
    # rules-before-draw.sha256 lists the draw-time hashes of the scripts and of features.json;
    # budget.py was edited after the draw (logged in the same file), the others were not.
    listed = {}
    for line in (PROOF / "selection" / "rules-before-draw.sha256").read_text().splitlines():
        digest, _, rel = line.partition("  ")
        if len(digest) == 64 and rel:
            listed.setdefault(rel, digest)
    for rel in ("features.py", "draw.py", "features.json"):
        assert _sha(PROOF / "selection" / rel) == listed[rel], rel
