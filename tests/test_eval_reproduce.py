"""`python -m readablestem.eval.reproduce`: the offline reproduction command (make reproduce).

The checks run on the repository's own committed gold data and reports. The failure paths
use small copies in tmp_path, so a tampered file or a stale report must be reported and
change the exit status.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from readablestem.eval import reproduce as R

ROOT = Path(__file__).resolve().parents[1]


def _copy_tree(tmp_path: Path) -> Path:
    """A minimal project: the gold sample, the committed reports and the scorer fixture."""
    root = tmp_path / "project"
    shutil.copytree(ROOT / "data" / "gold", root / "data" / "gold")
    shutil.copytree(ROOT / "data" / "eval", root / "data" / "eval")
    (root / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy(ROOT / R.SCORER_CHECK, root / R.SCORER_CHECK)
    return root


def test_the_repository_passes_every_offline_check():
    checks = R.run_checks(ROOT)
    assert [c.name for c in checks if not c.ok] == []
    assert len(checks) == 4


def test_main_prints_the_committed_numbers_and_exits_zero(capsys):
    assert R.main([]) == 0
    out = capsys.readouterr().out
    assert "All checks passed." in out
    assert "exact_lenient = 0.767" in out
    assert "not re-run here" in out
    assert "held-out" in out  # the limit is stated beside the numbers
    for banned in ("compliant", "verified", "guarantee"):
        assert banned not in out.lower()


def test_a_changed_gold_file_is_reported(tmp_path):
    root = _copy_tree(tmp_path)
    victim = next((root / "data" / "gold" / "openstax").glob("*/*.jsonl"))
    victim.write_text(victim.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    check = R.check_gold_manifest(root / "data" / "gold", "data/gold")
    assert not check.ok
    assert any(line.startswith("changed: ") for line in check.extra)
    # An unlisted gold file is reported too.
    stray = victim.with_name("stray.jsonl")
    shutil.copy(ROOT / "data" / "gold" / "openstax" / "physics" / victim.name, stray)
    assert any(
        line == "not in manifest: openstax/physics/stray.jsonl"
        for line in R.check_gold_manifest(root / "data" / "gold", "data/gold").extra
    )


def test_a_report_scored_against_other_gold_is_stale(tmp_path):
    root = _copy_tree(tmp_path)
    path = root / "data" / "eval" / "latest-equations.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["dataset"]["manifest_sha256"] = "0" * 64
    path.write_text(json.dumps(report), encoding="utf-8")
    check = R.check_reports_use_this_gold(root)
    assert not check.ok
    assert any(
        line.startswith("latest-equations.json: report has 000000000000") for line in check.extra
    )


def test_compare_reports_flags_a_changed_headline_metric_only():
    committed = json.loads((ROOT / "data/eval/latest-headings.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(committed))
    assert R.compare_reports(committed, fresh) == []
    fresh["metrics"]["recall"] = 0.9
    fresh["metrics"]["per_page"] = []  # not a headline metric: ignored
    diffs = R.compare_reports(committed, fresh)
    assert [d.split(":")[0] for d in diffs] == ["recall"]
