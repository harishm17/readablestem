"""The recorded run and the report committed in this repository.

The cheap half of what CI's `proof-replay` job does: the pins and the hashes of the committed
recorded run, and what the committed PROOF.md says about it. The full replay (the pipeline
and the scoring run again on the recorded answers) is `make proof`; it is not repeated here
because it takes a minute or more and CI runs it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_stem.proof import engine, registry
from a11y_stem.proof.bundle import Bundle
from a11y_stem.proof.report import wording_problems

ROOT = Path(__file__).resolve().parent.parent


def test_every_proof_set_in_the_repository_loads():
    sets = registry.list_sets(ROOT)
    assert sets, "proof/sets/ holds no proof set"
    default = registry.default_set_id(ROOT)
    assert default in {s.id for s in sets}
    for pset in sets:
        assert pset.limits, f"{pset.id}: a set must state what its numbers leave out"
        if pset.held_out:
            assert pset.prereg is not None and pset.signoff
            books = {s.book for s in pset.sections}
            assert books <= set(pset.fetch.pins), "a held-out set reads a pinned archive release"


def test_the_committed_recorded_run_matches_its_pins_and_its_hashes():
    pset = registry.resolve_set(ROOT, None)
    if not (ROOT / pset.bundle / "bundle.json").is_file():
        pytest.skip(f"no recorded run committed for {pset.id}")
    checks = engine.check_proof(engine.ProofOptions(root=ROOT))
    by_name = {c.name: c.status for c in checks}
    assert by_name["recorded run"] == "ok"
    assert by_name["gold manifest"] == "ok" and by_name["gold files"] == "ok"


def test_the_committed_proof_md_describes_the_committed_recorded_run():
    pset = registry.resolve_set(ROOT, None)
    report = ROOT / "PROOF.md"
    if not report.is_file():
        pytest.skip("no PROOF.md committed")
    text = report.read_text(encoding="utf-8")
    bundle = Bundle.open(ROOT / pset.bundle)
    assert text.startswith(f"# Proof: {pset.title}")
    assert f"| Run recorded on | {bundle.recorded_on} |" in text
    assert "Replay compared with the recorded reports: **identical**" in text
    assert "PLUMBING CHECK ONLY" not in text, "a fake run must not be committed as the proof"
    assert wording_problems(text) == []
    if not pset.held_out:
        assert "Development set, not held out" in text
