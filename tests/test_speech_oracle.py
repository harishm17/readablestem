"""Screen-reader speech oracle (`eval.speech_oracle`, `scripts/screenreader_speech.py`).

The pure parts (comparison, diff, classes, aggregates, input adapters) run anywhere. The tests
that speak MathML need MathCAT and Speech Rule Engine, built by
`scripts/speech_oracles/build.sh`; they skip, with the build command, when those are absent.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from readablestem.eval import speech_oracle as so

ROOT = Path(__file__).resolve().parents[1]
NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'

needs_mathcat = pytest.mark.skipif(
    "mathcat" in so.missing_engines(), reason=f"MathCAT not built: run {so.BUILD_SCRIPT}"
)
needs_sre = pytest.mark.skipif(
    "sre" in so.missing_engines(), reason=f"SRE not installed: run {so.BUILD_SCRIPT}"
)


def math(body: str) -> str:
    return f"<math {NS}>{body}</math>"


WATER_MI = math(
    '<msub><mi mathvariant="normal">H</mi><mn>2</mn></msub><mi mathvariant="normal">O</mi>'
)
HALF = math("<mfrac><mn>1</mn><mn>2</mn></mfrac>")
THIRD = math("<mfrac><mn>1</mn><mn>3</mn></mfrac>")
ROOT2 = math("<msqrt><mn>2</mn></msqrt>")
SQUARED = math("<msup><mi>x</mi><mn>2</mn></msup>")
TABLE = math("<mtable><mtr><mtd><mn>1</mn></mtd></mtr><mtr><mtd><mn>2</mn></mtd></mtr></mtable>")
BROKEN = "<math><mi>"


# ------------------------------------------------------------------------ pure, no engines


def test_shape_class_orders_chemistry_before_structure() -> None:
    assert so.shape_class(HALF, None) == "fraction"
    assert so.shape_class(HALF, True) == "chemistry"
    assert so.shape_class(TABLE, None) == "table or matrix"
    assert so.shape_class(ROOT2, None) == "root"
    assert so.shape_class(SQUARED, None) == "scripts"
    assert so.shape_class(math("<mi>x</mi>"), None) == "plain"


def test_pairs_from_items_reads_both_task_shapes_and_missed_gold() -> None:
    gold = [
        {"kind": "equation", "id": "openstax/b/p/eq-000", "mathml": HALF},
        {"kind": "equation", "id": "openstax/b/p/eq-001", "mathml": THIRD},
        {"kind": "equation", "id": "openstax/b/p/eq-002", "mathml": ROOT2},
        {"kind": "heading", "id": "openstax/b/p/h-000"},
    ]
    document_row = {
        "id": "b/p",
        "pairs": [{"gold_id": "openstax/b/p/eq-000", "pred_mathml": HALF}],
        "missed_gold_ids": ["openstax/b/p/eq-001"],
    }
    equations_row = {"id": "openstax/b/p/eq-002", "pred_mathml": None, "pred_latex": r"\sqrt{2}"}
    pairs = so.pairs_from_items([document_row, equations_row], gold)
    by_id = {p.id: p for p in pairs}
    assert set(by_id) == {"openstax/b/p/eq-000", "openstax/b/p/eq-001", "openstax/b/p/eq-002"}
    assert by_id["openstax/b/p/eq-001"].ours is None
    assert "msqrt" in (by_id["openstax/b/p/eq-002"].ours or "")
    assert {p.source for p in pairs} == {"b"}


def test_pairs_from_jsonl_takes_latex_and_a_missing_proposal(tmp_path: Path) -> None:
    path = tmp_path / "pairs.jsonl"
    rows = [
        {"id": "a", "gold_latex": "x^2", "ours_latex": "x^{2}", "source": "hw"},
        {"id": "b", "gold": HALF, "ours": None, "class": "fraction"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    a, b = so.pairs_from_jsonl(path)
    assert "msup" in a.gold and "msup" in (a.ours or "") and a.source == "hw"
    assert b.ours is None and b.label == "fraction"
    path.write_text(json.dumps({"id": "c"}))
    with pytest.raises(ValueError, match="no gold MathML"):
        so.pairs_from_jsonl(path)


def test_summary_counts_no_proposal_as_different_and_ranks_classes(monkeypatch) -> None:
    """Aggregates over canned readings, so this needs no engine."""
    texts = {HALF: "1 half", THIRD: "1 third", ROOT2: "root 2"}

    def fake_read_all(mathml, engines=None):
        return {
            m: so.Reading(speech={"mathcat:ClearSpeak": texts.get(m, "other")}, chemistry=False)
            for m in dict.fromkeys(mathml)
        }

    monkeypatch.setattr(so, "read_all", fake_read_all)
    engines = ("mathcat:ClearSpeak",)
    pairs = [
        so.Pair("1", HALF, HALF, "s"),
        so.Pair("2", HALF, THIRD, "s"),
        so.Pair("3", ROOT2, None, "t"),
        so.Pair("4", ROOT2, ROOT2, "t"),
    ]
    results = so.compare(pairs, engines)
    assert [r.equal["mathcat:ClearSpeak"] for r in results] == [True, False, False, True]
    assert results[1].diff["mathcat:ClearSpeak"] == "gold [half] ours [third]"
    assert results[2].diff["mathcat:ClearSpeak"] == "no proposal"
    s = so.summarize(results, engines)
    e = s["engines"]["mathcat:ClearSpeak"]
    assert (e["equal"], e["n"]) == (2, 4)
    assert (e["with_proposal"]["equal"], e["with_proposal"]["n"]) == (2, 3)
    assert e["ci95"][0] < 0.5 < e["ci95"][1]
    assert s["by_source"]["mathcat:ClearSpeak"]["s"]["equal"] == 1
    assert so.top_problems(results, "mathcat:ClearSpeak")[0]["change"].startswith("gold [half]")
    assert "2 / 4" in so.format_markdown(s)


# ------------------------------------------------------------------- with the real engines


@needs_mathcat
def test_mathcat_reads_a_fraction_and_flags_chemistry() -> None:
    readings = so.read_all([HALF, WATER_MI], ["mathcat:ClearSpeak", "mathcat:SimpleSpeak"])
    assert "half" in readings[HALF].speech["mathcat:ClearSpeak"]
    assert readings[HALF].chemistry is False
    assert readings[WATER_MI].chemistry is True
    assert set(readings[HALF].speech) == {"mathcat:ClearSpeak", "mathcat:SimpleSpeak"}


@needs_mathcat
def test_mathcat_reports_unparseable_mathml_as_an_error_not_a_crash() -> None:
    reading = so.read_all([BROKEN, HALF], ["mathcat:ClearSpeak"])[BROKEN]
    assert "mathcat" in reading.errors and not reading.speech


def test_held_out_and_sealed_gold_are_refused(monkeypatch, tmp_path: Path, capsys) -> None:
    from readablestem.core.paths import cache_dir

    sealed = cache_dir("dev-v2", "check-abc")
    assert so.protected_gold(sealed)
    assert so.protected_gold(tmp_path) is None
    spec = importlib.util.spec_from_file_location(
        "screenreader_speech", ROOT / "scripts" / "screenreader_speech.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    items = tmp_path / "items.jsonl"
    items.write_text("")
    assert module.main(["--items", str(items), "--gold", str(sealed)]) == 2
    assert "error:" in capsys.readouterr().err


def test_report_block_has_no_sources_and_errors_do_not_quote_input() -> None:
    pair = so.Pair("x", HALF, HALF, source="a-book")
    result = so.PairResult(
        pair, "fraction", so.Reading({"mathcat:ClearSpeak": "1 half"}), so.Reading(), {}, {}
    )
    assert "by_source" not in so.report_block([result], ("mathcat:ClearSpeak",))
    assert so._first_line("Processing error for expression <math><mi>secret</mi></math>") == (
        "Processing error for expression"
    )


def test_a_failing_engine_names_no_input(monkeypatch) -> None:
    import subprocess

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3, "", "bad <math>secret</math>")

    monkeypatch.setattr(so.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as err:
        so._run(["/x/engine"], [{"id": "0", "mathml": "<math>secret</math>"}])
    assert "secret" not in str(err.value) and "exit code 3" in str(err.value)

    def slow(cmd, **kwargs):
        assert kwargs["timeout"] == so.RUN_TIMEOUT
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(so.subprocess, "run", slow)
    with pytest.raises(RuntimeError, match="timed out"):
        so._run(["/x/engine"], [])
