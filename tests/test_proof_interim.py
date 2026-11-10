"""The interim held-out report (`proof/interim/report.py`): what it shows and what it refuses."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INTERIM = ROOT / "proof" / "interim"
REPORT = INTERIM / "INTERIM-heldout-2026-10.md"
TAG = "proof-heldout-2026-10"  # the Makefile's PROOF_TAG (tests/test_proof_pin.py keeps them equal)


def _load():
    spec = importlib.util.spec_from_file_location("interim_report", INTERIM / "report.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report():
    return _load()


def _tag_gold_dir() -> Path | None:
    """The answer key as the code at the tag fetches it (`make heldout-gold-at-tag`), or None.
    It is not the folder `make heldout-gold` fills: the rename changed the key's text."""
    explicit = os.environ.get("HELDOUT_GOLD_AT_TAG")
    base = os.environ.get("XDG_CACHE_HOME")
    cache = (Path(base) if base else Path.home() / ".cache") / "readablestem" / "at-tag"
    folders = [Path(explicit)] if explicit else sorted(cache.glob("*/heldout/gold"))
    return next((f for f in folders if (f / "manifest.json").is_file()), None)


def _skip_or_fail(reason: str) -> None:
    if os.environ.get("PROOF_INTERIM_REQUIRE_GOLD"):  # set by .github/workflows/proof-interim.yml
        pytest.fail(reason)
    pytest.skip(reason)


def test_another_answer_key_is_refused(report, tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="not the registered held-out answer key"):
        report.build(INTERIM / "heldout-2026-10", tmp_path)


def test_recall_is_split_by_where_the_gold_heading_sits(report):
    items = [
        {
            "pairs": [
                {"gold_context": None},
                {"gold_context": "example"},
                {"gold_context": "note"},
            ],
            "missed": [{"context": None}, {"context": "solution"}, {"context": "solution"}],
        }
    ]
    assert report.heading_recall_split(items) == {"free": (1, 2), "boxed": (2, 4)}


def test_extra_headings_that_are_only_a_number_are_counted(report):
    items = [
        {
            "extra": [
                {"text": "142."},
                {"text": "7"},
                {"text": "(12)"},
                {"text": "Example 4"},
                {"text": "Section 1.5 Exercises"},
            ]
        }
    ]
    assert report.bare_number_extras(items) == (5, 3)


def test_the_development_numbers_the_report_cites_are_in_the_development_report():
    text = (ROOT / "proof" / "PROOF-development-sample.md").read_text(encoding="utf-8")
    assert "| Heading flags: precision | 0.5556 |" in text
    assert "| Heading flags: recall | 0.7778 |" in text
    assert "precision 0.5556 and recall 0.7778" in REPORT.read_text(encoding="utf-8")


def test_the_readme_numbers_are_the_reports_numbers():
    """The numbers the README quotes from the held-out run are the ones in PROOF.md."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    proof = (ROOT / "PROOF.md").read_text(encoding="utf-8")
    pairs = {
        "0.504": "0.5037",  # ReadableSTEM, equations through the whole pipeline
        "0.622": "0.6216",  # the Opus chatbot
        "0.961": "0.9613",  # heading F1
        "0.971": "0.9710",  # figure judge pass rate
        "1.76 required decisions": "1.7624",
        "p = 0.0073 and 0.0088": "0.0073",
        "$36.21": None,
    }
    for shown, needle in pairs.items():
        assert shown in readme, shown
        if needle is not None:
            assert needle in proof, needle
    assert "0.0088" in proof and "higher by point estimate" in proof
    assert "make heldout-gold" in readme or "INTERIM-heldout-2026-10.md" in readme


def test_the_report_is_what_the_tag_produces_from_the_committed_data(tmp_path):
    """Replay the report at the tag, as `make proof` replays the recorded run.

    The committed report is a registered record (Addendum 5 pins its SHA-256), so it is
    reproduced by the code that made it, not by this checkout's. Run on this checkout it
    differs, for two known reasons: the scorer converts each parser's LaTeX with the
    production converter, which the chemistry work changed on purpose (mineru-basic's exact
    equations 238 -> 241 of 1,369), and the rename of Addendum 8 changed the footer of the
    parsers' outputs but not the printed PDFs (coverage 32,906 -> 32,696 of 45,467). Run at
    the tag, with the key that the tag's own code fetches, the file is reproduced byte for
    byte. That the committed file is the tag's file with only the name renewed is checked
    by `proof/verify-rename.py` (tests/test_proof_registered.py)."""
    tagged = subprocess.run(
        ["git", "show", f"{TAG}:proof/interim/{REPORT.name}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if tagged.returncode != 0:
        _skip_or_fail(f"the tag {TAG} is not in this clone (fetch tags)")
    gold = _tag_gold_dir()
    if gold is None:
        _skip_or_fail("the held-out key at the tag is not fetched (make heldout-gold-at-tag)")
        return
    archive = tmp_path / "tag.tar"
    subprocess.run(["git", "archive", "-o", str(archive), TAG], cwd=ROOT, check=True)
    tree = tmp_path / "tree"
    with tarfile.open(archive) as tar:
        tar.extractall(tree, filter="data")
    out = tmp_path / "report.md"
    done = subprocess.run(
        [sys.executable, "proof/interim/report.py", "--gold", str(gold), "--out", str(out)],
        cwd=tree,
        env={**os.environ, "PYTHONPATH": str(tree / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_bytes() == tagged.stdout


def test_addendum_5_names_the_hash_of_the_committed_report():
    import hashlib

    digest = hashlib.sha256(REPORT.read_bytes()).hexdigest()
    addendum = (ROOT / "proof" / "PREREGISTRATION-ADDENDUM-5.md").read_text(encoding="utf-8")
    assert digest in addendum, "rebuild the report, then update Addendum 5 and its hash"
