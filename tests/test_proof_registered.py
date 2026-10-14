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
import re
import subprocess
import sys
from pathlib import Path

import pytest

from readablestem.proof import registry

ROOT = Path(__file__).resolve().parent.parent
PROOF = ROOT / "proof"
HASH_FILES = ("PREREG-v2.sha256", "PREREG-after-review-v1.sha256")
V1_REGISTRATION = "8127b143cdbf278f9a14650e086c70b4af7063d789b16ffdfc266399e5d4c5d9"
# Files whose text differs between version 1 and version 2 of the registration (wording only:
# requester names, an authorship line, the author's local paths).
CHANGED_IN_V2 = {
    "PREREGISTRATION.md",
    "PARSERS-PLAN.md",
    "selection/features.py",
    "selection/scan_used.py",
    "scorer-readiness/draw.py",
}
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


def test_version_1_hashes_differ_from_version_2_in_the_five_reworded_files_only():
    v1, v2 = (
        dict((r, d) for d, r in _lines("original-hashes/PREREG-v1.sha256")),
        dict((r, d) for d, r in _lines("original-hashes/PREREG-v2.sha256")),
    )
    # Addenda written after version 2 are appended to its hash list (Addendum 6, ...).
    assert list(v2)[: len(v1)] == list(v1), "version 2 hashes the same files in the same order"
    later = list(v2)[len(v1) :]
    assert all(re.fullmatch(r"PREREGISTRATION-ADDENDUM-\d+\.md", rel) for rel in later), later
    assert {rel for rel in v1 if v1[rel] != v2[rel]} == CHANGED_IN_V2
    assert v1["PREREGISTRATION.md"] == V1_REGISTRATION
    assert v2["PREREGISTRATION.md"] != V1_REGISTRATION


@pytest.mark.parametrize("rel", sorted(V2_TO_V1_LINES))
def test_reverting_the_documented_lines_gives_back_the_version_1_hash(rel):
    v1 = dict((r, d) for d, r in _lines("original-hashes/PREREG-v1.sha256"))
    assert hashlib.sha256(_as_in_v1(rel).encode("utf-8")).hexdigest() == v1[rel]


def test_the_addenda_and_the_interim_headings_cite_the_version_1_registration():
    for rel in (
        "PREREGISTRATION-ADDENDUM-1.md",
        "interim/heldout-2026-10/a11y-headings/headings.json",
    ):
        assert V1_REGISTRATION in (PROOF / rel).read_text(encoding="utf-8"), rel


def test_the_hash_file_lists_the_registration_and_the_addenda_except_the_fifth():
    listed = {rel for _, rel in _lines("PREREG-v2.sha256")}
    expected = (
        {"PREREGISTRATION.md", "PARSERS-PLAN.md"}
        | {f"PREREGISTRATION-ADDENDUM-{n}.md" for n in (1, 2, 3, 4, 6, 7, 8)}
        | {"verify-rename.py"}
    )
    assert expected <= listed
    assert {"selection/draw.py", "selection/features.py", "selection/rules-before-draw.sha256"} <= (
        listed
    )


def test_the_registration_hash_is_the_one_the_set_the_code_and_the_readme_cite():
    registered = dict((rel, d) for d, rel in _lines("PREREG-v2.sha256"))["PREREGISTRATION.md"]
    assert registered != V1_REGISTRATION
    pset = registry.resolve_set(ROOT, "heldout-2026-10")
    assert pset.prereg is not None and pset.prereg.sha256 == registered
    assert registered in (ROOT / "README.md").read_text(encoding="utf-8")
    assert registered in (ROOT / "src/readablestem/gold/heldout.py").read_text(encoding="utf-8")


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


def test_the_original_hash_lists_are_the_ones_at_the_tag():
    for name, rel in (
        ("PREREG-v1.sha256", "PREREG-v1.sha256"),
        ("PREREG-v2.sha256", "PREREG-v2.sha256"),
        ("PREREG-after-review-v1.sha256", "PREREG-after-review-v1.sha256"),
        ("rules-before-draw.sha256", "selection/rules-before-draw.sha256"),
    ):
        assert (PROOF / "original-hashes" / name).read_text(encoding="utf-8") == _at_tag(rel)


def test_the_registered_files_differ_from_the_tag_only_by_the_new_name():
    """Addendum 8: `proof/verify-rename.py` rebuilds every file from the tag and compares."""
    _at_tag("PREREG-v2.sha256")  # skips when the clone has no tag
    done = subprocess.run(
        [sys.executable, "-I", str(PROOF / "verify-rename.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith("OK:")


def _verify_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_rename", PROOF / "verify-rename.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rename_rules_on_small_examples():
    rename = _verify_module().rename_text
    old = "a11y" + "-stem"
    assert (
        rename(f"{old} is not affiliated with OpenStax.")
        == "ReadableSTEM is not affiliated with OpenStax."
    )
    assert rename(f"run `{old} gold fetch` first") == "run `readablestem gold fetch` first"
    assert rename(f"~/.cache/{old}/heldout") == "~/.cache/readablestem/heldout"
    assert rename(f"from {'a11y' + '_stem'}.eval import x") == "from readablestem.eval import x"
    seed = f"{old} held-out OpenStax set, pre-registered 2026-10-13"
    assert rename(f"seed: {seed}; by {old}") == f"seed: {seed}; by ReadableSTEM"
    assert rename("no name here") == "no name here"


def test_the_old_name_is_left_only_in_the_seeds_and_the_script_that_replaces_it():
    old = "a11y" + "-stem"
    protected = _verify_module().PROTECTED
    for path in sorted(PROOF.rglob("*")):
        if not path.is_file() or path.suffix in {".pdf", ".png", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for seed in protected:
            text = text.replace(seed, "")
        assert old not in text, f"{path.relative_to(ROOT)} still names the old product"
