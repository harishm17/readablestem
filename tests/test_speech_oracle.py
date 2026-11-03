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
needs_both = pytest.mark.skipif(
    bool(so.missing_engines()), reason=f"speech oracles not built: run {so.BUILD_SCRIPT}"
)


def math(body: str) -> str:
    return f"<math {NS}>{body}</math>"


WATER_MI = math(
    '<msub><mi mathvariant="normal">H</mi><mn>2</mn></msub><mi mathvariant="normal">O</mi>'
)
WATER_MTEXT = math("<mtext>H</mtext><msub><mtext></mtext><mn>2</mn></msub><mtext>O</mtext>")
HALF = math("<mfrac><mn>1</mn><mn>2</mn></mfrac>")
THIRD = math("<mfrac><mn>1</mn><mn>3</mn></mfrac>")
ROOT2 = math("<msqrt><mn>2</mn></msqrt>")
SQUARED = math("<msup><mi>x</mi><mn>2</mn></msup>")
SALT_MI = math("<mi>Na</mi><mi>Cl</mi><mo>(</mo><mi>aq</mi><mo>)</mo>")
SALT_MTEXT = math("<mi>Na</mi><mi>Cl</mi><mtext>(aq)</mtext>")
TABLE = math("<mtable><mtr><mtd><mn>1</mn></mtd></mtr><mtr><mtd><mn>2</mn></mtd></mtr></mtable>")
BROKEN = "<math><mi>"


# ------------------------------------------------------------------------ pure, no engines


def test_normalise_ignores_case_pauses_and_spacing() -> None:
    assert so.normalise("Cap H,  sub 2.") == so.normalise("cap h sub 2")
    assert so.normalise("x squared") != so.normalise("x cubed")


def test_word_diff_names_the_words_that_differ() -> None:
    assert so.word_diff("x 2", "x squared") == "gold [squared] ours [2]"
    assert so.word_diff("a b", "a, b.") == ""


def test_shape_class_orders_chemistry_before_structure() -> None:
    assert so.shape_class(HALF, None) == "fraction"
    assert so.shape_class(HALF, True) == "chemistry"
    assert so.shape_class(TABLE, None) == "table or matrix"
    assert so.shape_class(ROOT2, None) == "root"
    assert so.shape_class(SQUARED, None) == "scripts"
    assert so.shape_class(math("<mi>x</mi>"), None) == "plain"


def test_source_of_names_the_book_or_document() -> None:
    assert so.source_of("openstax/chemistry-atoms-first/1-4-measurements/eq-003") == (
        "chemistry-atoms-first"
    )
    assert so.source_of("authored/latex-chemistry/eq-000") == "latex-chemistry"
    assert so.source_of("handwriting/hw-dev-01/eq-000") == "handwriting"


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


def test_missing_engines_say_how_to_build(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SPEECH_ORACLES_DIR", str(tmp_path))
    gone = so.missing_engines()
    assert set(gone) == {"mathcat", "sre"}
    assert all(so.BUILD_SCRIPT in message for message in gone.values())
    assert so.available_engines() == ()
    assert "skipped" in (so.skip_message() or "")
    assert so.report_for_run("equations", [], tmp_path) == {"skipped": so.skip_message()}


def test_script_skips_cleanly_without_the_engines(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("SPEECH_ORACLES_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "screenreader_speech", ROOT / "scripts" / "screenreader_speech.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--mathml", HALF]) == 0
    assert "speech oracles skipped" in capsys.readouterr().err


# ------------------------------------------------------------------- with the real engines


@needs_mathcat
def test_mathcat_reads_a_fraction_and_flags_chemistry() -> None:
    readings = so.read_all([HALF, WATER_MI], ["mathcat:ClearSpeak", "mathcat:SimpleSpeak"])
    assert "half" in readings[HALF].speech["mathcat:ClearSpeak"]
    assert readings[HALF].chemistry is False
    assert readings[WATER_MI].chemistry is True
    assert set(readings[HALF].speech) == {"mathcat:ClearSpeak", "mathcat:SimpleSpeak"}


@needs_mathcat
def test_mathcat_says_aqueous_for_a_state_in_mi_but_not_in_a_long_mtext() -> None:
    """The shape finding of docs/chemistry.md: `(aq)` as an `mtext` is not recognised."""
    readings = so.read_all([SALT_MI, SALT_MTEXT], ["mathcat:ClearSpeak"])
    assert "aqueous" in readings[SALT_MI].speech["mathcat:ClearSpeak"]
    assert "aqueous" not in readings[SALT_MTEXT].speech["mathcat:ClearSpeak"]


@needs_mathcat
def test_mathcat_reports_unparseable_mathml_as_an_error_not_a_crash() -> None:
    reading = so.read_all([BROKEN, HALF], ["mathcat:ClearSpeak"])[BROKEN]
    assert "mathcat" in reading.errors and not reading.speech


@needs_sre
def test_sre_reads_a_root_in_both_domains() -> None:
    reading = so.read_all([ROOT2], ["sre:clearspeak", "sre:mathspeak"])[ROOT2]
    assert "root" in reading.speech["sre:clearspeak"].lower()
    assert "root" in reading.speech["sre:mathspeak"].lower()
    assert reading.chemistry is None  # SRE has no chemistry rules; MathCAT was not asked


@needs_both
def test_compare_finds_same_different_and_missing() -> None:
    pairs = [
        so.Pair("same", HALF, HALF, "s"),
        so.Pair("different", HALF, THIRD, "s"),
        so.Pair("missing", SQUARED, None, "s"),
        so.Pair("chem-shape", SALT_MI, SALT_MTEXT, "s"),
    ]
    results = {r.pair.id: r for r in so.compare(pairs)}
    assert all(results["same"].equal.values()) and len(results["same"].equal) == 4
    assert not any(results["different"].equal.values())
    assert "third" in results["different"].diff["mathcat:ClearSpeak"]
    assert not any(results["missing"].equal.values())
    assert results["missing"].as_row()["has_proposal"] is False
    assert results["chem-shape"].equal["mathcat:ClearSpeak"] is False
    assert results["chem-shape"].cls == "chemistry"
    row = results["different"].as_row()
    assert row["speech"]["sre:mathspeak"]["gold"] and row["speech"]["sre:mathspeak"]["ours"]


@needs_both
def test_report_for_run_has_aggregates_only(tmp_path: Path) -> None:
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    records = [
        {"kind": "equation", "id": "openstax/b/p/eq-000", "mathml": HALF},
        {"kind": "equation", "id": "openstax/b/p/eq-001", "mathml": WATER_MI},
    ]
    (gold_dir / "b.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    items = [
        {"id": "openstax/b/p/eq-000", "pred_mathml": HALF},
        {"id": "openstax/b/p/eq-001", "pred_mathml": WATER_MTEXT},
    ]
    block = so.report_for_run("equations", items, gold_dir)
    assert block["n_pairs"] == 2
    assert block["engines"]["mathcat:ClearSpeak"]["n"] == 2
    assert block["chemistry"]["gold_recognised"]["equal"] == 1
    text = json.dumps(block)
    assert "openstax/b/p/eq-000" not in text and "half" not in text  # no ids or speech strings


@needs_both
def test_script_writes_rows_and_report(tmp_path: Path, capsys) -> None:
    spec = importlib.util.spec_from_file_location(
        "screenreader_speech", ROOT / "scripts" / "screenreader_speech.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pairs = tmp_path / "pairs.jsonl"
    rows = [
        {"id": "a", "gold": HALF, "ours": HALF},
        {"id": "b", "gold": HALF, "ours": THIRD},
        {"id": "c", "gold": ROOT2, "ours": None},
    ]
    pairs.write_text("\n".join(json.dumps(r) for r in rows))
    out, report = tmp_path / "rows.jsonl", tmp_path / "report.json"
    code = module.main(["--pairs", str(pairs), "--out", str(out), "--report", str(report)])
    assert code == 0
    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["id"] for r in written] == ["a", "b", "c"]
    assert written[0]["speech_equal"]["mathcat:ClearSpeak"] is True
    summary = json.loads(report.read_text())
    assert summary["engines"]["mathcat:ClearSpeak"]["equal"] == 1
    assert "Speech under screen-reader engines" in capsys.readouterr().out


def test_eval_hook_is_for_equations_and_documents_and_skips_without_engines(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from readablestem.cli import eval_cli

    monkeypatch.setenv("SPEECH_ORACLES_DIR", str(tmp_path))
    assert eval_cli._speech_block("figures", [], tmp_path) is None
    assert "equations and document" in capsys.readouterr().out
    block = eval_cli._speech_block("document", [], tmp_path)
    assert block is not None and "run scripts/speech_oracles/build.sh" in block["skipped"]
    assert eval_cli._speech_lines(block) == ""
