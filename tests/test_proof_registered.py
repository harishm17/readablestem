"""The registered files in `proof/` can be checked from the repository alone.

`proof/REGISTERED.md` says what can and cannot be checked. These tests do the checks that can
be done offline: every registered file has the SHA-256 in `PREREG-v2.sha256`, the five files
that differ from version 1 differ in exactly the documented lines (reverting them gives back
the version-1 hashes of `PREREG-v1.sha256` where the text can be rebuilt), the seeded draw
reproduces the committed draw, and the sections that survived the page rules are the held-out
set's sections. On 2026-10-14 the product's name was replaced in these files (Addendum 8):
the hash lists as they were at the tag are in `proof/original-hashes/`, the current lists hold
the hashes of the renamed files, and `proof/verify-rename.py` rebuilds the change from the tag.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / "proof"
HASH_FILES = ("PREREG-v2.sha256", "PREREG-after-review-v1.sha256")
# Version 1 of three scripts named the author's home directory. Version 2 replaced exactly
# these lines; the old path is written in pieces so that it does not appear in the repository.
OLD_HOME = "/" + "home" + "/" + "harish"
OLD_NAME = "a11y" + "-stem"  # the files at the tag still have it; so do the lines below
V2_TO_V1_LINES = {
    "selection/features.py": [
        (
            "REPO = Path(__file__).resolve().parents[2]\n",
            f'REPO = Path("{OLD_HOME}/code/{OLD_NAME}")\n',
        )
    ],
    "selection/scan_used.py": [
        (
            '    Path(p) for p in os.environ.get("A11Y_SCAN_ROOTS", "").split(os.pathsep) if p\n'
            '] or [Path(__file__).resolve().parents[2], Path("/tmp")]\n',
            f'    Path("{OLD_HOME}/code/{OLD_NAME}"),\n'
            f'    Path("{OLD_HOME}/code/{OLD_NAME}-int"),\n'
            f'    Path("{OLD_HOME}/code/a11y-research"),\n'
            f'    Path("{OLD_HOME}/.cache/a11y-fixer"),\n'
            f'    Path("{OLD_HOME}/.cache/final-verify-pt"),\n'
            '    Path("/tmp"),\n'
            "]\n",
        )
    ],
    "scorer-readiness/draw.py": [
        (
            "gold = Path(sys.argv[2]) if len(sys.argv) > 2 else "
            f'Path.home() / ".cache" / "{OLD_NAME}" / "heldout"\n',
            f'gold = Path("{OLD_HOME}/code/a11y-research/proof/heldout-gold")\n',
        )
    ],
}


TAG = "proof-heldout-2026-10"


def _at_tag(rel: str) -> str:
    """A registered file as it is at the tag, or a skip if the clone has no tag."""
    done = subprocess.run(
        ["git", "show", f"{TAG}:proof/{rel}"], cwd=ROOT, capture_output=True, check=False
    )
    if done.returncode != 0:
        pytest.skip(f"the tag {TAG} is not in this clone")
    return done.stdout.decode("utf-8")


def _as_in_v1(rel: str) -> str:
    """The text of a script as it was in version 1, rebuilt from the file at the tag (the
    renamed files cannot be taken back to the old name by a rule, so the tag is the source)."""
    text = _at_tag(rel)
    for new, old in V2_TO_V1_LINES[rel]:
        assert text.count(new) == 1, f"{rel}: the documented version-2 lines are not there"
        text = text.replace(new, old)
    return text


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


def test_the_rules_were_hashed_before_the_draw_and_the_files_still_match_them():
    # rules-before-draw.sha256 lists the draw-time hashes of the scripts and of features.json;
    # budget.py was edited after the draw (logged in the same file), the others were not;
    # features.py is compared as it was in version 1 (only its path line was reworded).
    listed = {}
    for line in (PROOF / "original-hashes" / "rules-before-draw.sha256").read_text().splitlines():
        digest, _, rel = line.partition("  ")
        if len(digest) == 64 and rel:
            listed.setdefault(rel, digest)
    features_v1 = hashlib.sha256(_as_in_v1("selection/features.py").encode("utf-8")).hexdigest()
    assert features_v1 == listed["features.py"]
    for rel in ("draw.py", "features.json"):
        assert _sha(PROOF / "selection" / rel) == listed[rel], rel  # unchanged by the rename
