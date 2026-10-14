"""CLI smoke tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from readablestem.cli import main as cli
from readablestem.cli.main import build_parser, main, project_root

GOLD_LINE = {
    "id": "openstax/physics/x/eq-000",
    "kind": "equation",
    "display": "block",
    "mathml": "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>",
    "retrieved_at": "2026-10-06",
}


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for command in ("gold", "eval", "parse"):
        assert command in out


def test_gold_fetch_requires_book():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["gold", "fetch", "--pages", "2"])
    args = build_parser().parse_args(["gold", "fetch", "--book", "physics", "--pages", "2"])
    assert (args.book, args.pages, args.allow_retired) == ("physics", 2, False)


@pytest.mark.parametrize("pages", ["0", "-1", "two"])
def test_gold_fetch_rejects_non_positive_pages(pages, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["gold", "fetch", "--book", "physics", "--pages", pages])
    assert exc.value.code == 2
    assert "--pages" in capsys.readouterr().err


def _fake_fetch(monkeypatch, tmp_path):
    """Replace the network fetch with a stub; return the fetchers the CLI built."""
    from readablestem.gold import openstax

    built = []

    class StubFetcher:
        def __init__(self, cache_dir=None):
            self.cache_dir = cache_dir
            self.requests_made = 0
            built.append(self)

    class StubBook:
        title, slug, uuid, version, state = "Physics", "physics", "u", "v", "live"
        archive_version, license_name, license_url = "a", "CC BY", "http://x"

    monkeypatch.setattr(openstax, "HttpFetcher", StubFetcher)
    monkeypatch.setattr(openstax, "fetch_gold", lambda *a, **k: (StubBook(), []))
    return built


def test_gold_fetch_no_cache_says_so(monkeypatch, tmp_path, capsys):
    built = _fake_fetch(monkeypatch, tmp_path)
    args = ["gold", "fetch", "--book", "physics", "--out", str(tmp_path), "--no-cache"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert built[0].cache_dir is None
    assert "no cache" in out and "cached in" not in out


def test_default_paths_resolve_to_project_root(monkeypatch, tmp_path):
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo / "tests")
    assert project_root() == repo
    assert cli._default(cli.GOLD_DIR) == repo / "data" / "gold"
    monkeypatch.chdir(tmp_path)  # outside any checkout: fall back to the working directory
    assert project_root() == tmp_path.resolve()


def test_eval_equations_with_latex_predictions(tmp_path: Path, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(GOLD_LINE) + "\n", encoding="utf-8")
    pred = tmp_path / "pred.jsonl"
    pred.write_text(json.dumps({"id": GOLD_LINE["id"], "latex": r"v = \frac{d}{t}"}) + "\n")
    report = tmp_path / "report.json"
    code = main(
        ["eval", "equations", "--gold", str(gold), "--pred", str(pred), "--report", str(report)]
    )
    assert code == 0
    summary = json.loads(report.read_text())["summary"]
    secondary = summary.pop("secondary_stream_v2")  # the labelled secondary scorer (T2-12)
    assert (secondary["n"], secondary["exact"]) == (1, 1.0) and "secondary" in secondary["label"]
    assert summary == {
        "n": 1,
        "exact_strict": 1.0,
        "exact_lenient": 1.0,
        "mean_teds": 1.0,
        "errors": 0,
        "unknown_ids": 0,
        "unknown_ids_first": [],
    }


def test_eval_equations_missing_prediction_counts_as_failure(tmp_path: Path, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(GOLD_LINE) + "\n", encoding="utf-8")
    pred = tmp_path / "pred.jsonl"
    pred.write_text(json.dumps({"id": "other", "latex": "x"}) + "\n")
    assert main(["eval", "equations", "--gold", str(gold), "--pred", str(pred)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["exact_strict"] == 0.0 and summary["errors"] == 1
    # The typo'd id is reported, not silently ignored.
    assert summary["unknown_ids"] == 1 and summary["unknown_ids_first"] == ["other"]


def test_eval_equations_malformed_predictions_report_file_and_line(tmp_path: Path, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(GOLD_LINE) + "\n", encoding="utf-8")
    pred = tmp_path / "pred.jsonl"
    pred.write_text(json.dumps({"id": GOLD_LINE["id"], "latex": "v"}) + "\n{not json\n")
    assert main(["eval", "equations", "--gold", str(gold), "--pred", str(pred)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and f"{pred}:2" in err


def test_eval_equations_missing_files_exit_2(tmp_path: Path, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(GOLD_LINE) + "\n", encoding="utf-8")
    missing = tmp_path / "nope.jsonl"
    assert main(["eval", "equations", "--gold", str(gold), "--pred", str(missing)]) == 2
    assert "predictions file not found" in capsys.readouterr().err
    assert main(["eval", "equations", "--gold", str(missing), "--self-check"]) == 2
    assert "gold path not found" in capsys.readouterr().err


def test_parse_command_prints_elements(tmp_path: Path, capsys):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "a = b + c", fontsize=12)
    pdf = tmp_path / "one.pdf"
    doc.save(pdf)
    doc.close()
    assert main(["parse", str(pdf), "--json"]) == 0
    elements = json.loads(capsys.readouterr().out)
    assert elements[0]["kind"] == "equation_candidate"


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ("0", "out of range"),  # used to become index -1 and parse the last page as "p0"
        ("2", "out of range"),
        ("1,x", "comma-separated"),
    ],
)
def test_parse_rejects_bad_pages(tmp_path: Path, capsys, pages, message):
    doc = pymupdf.open()
    doc.new_page()
    pdf = tmp_path / "one.pdf"
    doc.save(pdf)
    doc.close()
    assert main(["parse", str(pdf), "--pages", pages]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and message in err


def test_parse_missing_pdf_exit_2(tmp_path: Path, capsys):
    assert main(["parse", str(tmp_path / "missing.pdf")]) == 2
    assert "PDF not found" in capsys.readouterr().err
