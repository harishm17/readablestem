"""`a11y-stem eval invented ...` end to end on small files (offline)."""

from __future__ import annotations

import base64
import csv
import io
import json

import pymupdf
import pytest
from PIL import Image

from readablestem.cli import eval_cli
from readablestem.cli.main import main
from readablestem.eval.invented import report
from test_eval_support import settings

PDF_LINES = [
    "2.1 Speed and Velocity",
    "Average speed is the distance traveled divided by the time it takes to travel it.",
    "A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.",
    "Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 1 of 1",
]


def pdf_file(tmp_path, name="section.pdf"):
    document = pymupdf.open()
    page = document.new_page()
    for i, line in enumerate(PDF_LINES):
        page.insert_text((40, 60 + 18 * i), line, fontsize=9)
    path = tmp_path / name
    document.save(path)
    document.close()
    return path


def png_uri():
    buffer = io.BytesIO()
    Image.new("RGB", (6, 4), "red").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


FAITHFUL = (
    "<h2>2.1 Speed and Velocity</h2>"
    "<p>Average speed is the distance traveled divided by the time it takes to travel it.</p>"
    "<p>A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.</p>"
)
INVENTED = "<p>Students often forget that velocity needs a direction, so always state one.</p>"


def html_file(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"<!doctype html><html lang='en'><body>{body}</body></html>")
    return path


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings())
    monkeypatch.setattr("readablestem.config.get_settings", lambda: settings())


def run(*args):
    return main(["eval", "invented", *map(str, args)])


def test_score_writes_a_content_free_report_and_prints_the_rates(tmp_path, capsys):
    pdf = pdf_file(tmp_path)
    page = html_file(tmp_path, "out.html", FAITHFUL + INVENTED)
    out = tmp_path / "report.json"
    code = run(
        "score", "--pdf", pdf, "--output", page, "--system", "toy", "--section", "s1", "--out", out
    )
    assert code == 0
    shown = capsys.readouterr().out
    assert "prose sentences" in shown and "toy" in shown
    data = json.loads(out.read_text())
    assert data["kind"] == "invented-content" and data["meta"]["system"] == "toy"
    assert data["meta"]["pdf"] == "section.pdf" and "/" not in data["meta"]["output"]
    assert data["score"]["counts"]["sentence"]["n"] == 3
    assert data["score"]["counts"]["sentence"]["unsupported"] == 1
    assert "units" not in data["score"]
    assert "Students often forget" not in out.read_text()  # content-free without --units
    assert len(data["code"]["sha256"]) == 64 and data["rules"]["sentence_min_words"] == 7


def test_units_are_written_only_on_request(tmp_path):
    pdf = pdf_file(tmp_path)
    page = html_file(tmp_path, "out.html", FAITHFUL + INVENTED)
    out = tmp_path / "report.json"
    assert (
        run("score", "--pdf", pdf, "--output", page, "--system", "toy", "--out", out, "--units")
        == 0
    )
    units = json.loads(out.read_text())["score"]["units"]
    assert any(u["unsupported"] and "Students often forget" in u["text"] for u in units)


def test_score_reads_markdown_and_a_missing_file_is_an_error(tmp_path, capsys):
    pdf = pdf_file(tmp_path)
    md = tmp_path / "out.md"
    md.write_text(
        "## 2.1 Speed and Velocity\n\n"
        "Average speed is the distance traveled divided by the time it takes to travel it.\n"
    )
    assert run("score", "--pdf", pdf, "--output", md, "--system", "parser") == 0
    assert run("score", "--pdf", pdf, "--output", tmp_path / "nope.html", "--system", "x") == 2
    assert "file not found" in capsys.readouterr().err


def test_score_with_gold_that_lacks_the_page_is_an_error(tmp_path, capsys):
    pdf = pdf_file(tmp_path)
    page = html_file(tmp_path, "out.html", FAITHFUL)
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "a.jsonl").write_text(
        json.dumps({"kind": "equation", "page_slug": "x", "index_on_page": 0, "mathml": "<math/>"})
    )
    code = run(
        "score", "--pdf", pdf, "--output", page, "--system", "t", "--gold", gold, "--pages", "y"
    )
    assert code == 2 and "no gold equations" in capsys.readouterr().err


def make_reports(tmp_path):
    pdf = pdf_file(tmp_path)
    paths = []
    for system, section, body in (
        ("toy", "s1", FAITHFUL + INVENTED),
        ("toy", "s2", FAITHFUL),
        ("real", "s1", FAITHFUL),
        ("real", "s2", FAITHFUL),
    ):
        page = html_file(tmp_path, f"{system}-{section}.html", body)
        out = tmp_path / f"{system}-{section}.json"
        assert run(
            "score", "--pdf", pdf, "--output", page, "--system", system, "--section", section,
            "--out", out, "--units",
        ) == 0  # fmt: skip
        paths.append(out)
    return pdf, paths


def test_summary_pools_systems_and_compares_them(tmp_path, capsys):
    _pdf, paths = make_reports(tmp_path)
    capsys.readouterr()
    out = tmp_path / "summary.json"
    code = run("summary", *paths, "--out", out, "--compare", "toy", "real", "--level", "0.99")
    assert code == 0
    shown = capsys.readouterr().out
    assert "### toy (2 sections, 2 outputs)" in shown and "### real" in shown
    data = json.loads(out.read_text())
    assert data["systems"]["toy"]["all_text"]["sentence"]["unsupported"] == 1
    assert data["systems"]["real"]["all_text"]["sentence"]["unsupported"] == 0
    comparison = data["comparison"]["all_text"]["sentence"]
    assert (
        comparison["difference"] == pytest.approx(1 / 5) and comparison["interval"]["level"] == 0.99
    )
    assert run("summary", *paths, "--compare", "toy", "nobody") == 2


def test_summary_refuses_a_file_that_is_not_a_report(tmp_path, capsys):
    other = tmp_path / "other.json"
    other.write_text("{}")
    assert run("summary", other) == 2
    assert "not an invented-content report" in capsys.readouterr().err


def test_fingerprint_is_stable_and_names_the_rules_files(capsys):
    assert run("fingerprint") == 0
    first = json.loads(capsys.readouterr().out)
    assert run("fingerprint") == 0
    assert json.loads(capsys.readouterr().out) == first
    assert len(first["code"]["sha256"]) == 64
    assert {"score.py", "read.py", "text.py"} <= set(first["code"]["files"])
    assert first["code"] == report.code_fingerprint()


def test_judge_needs_a_cap_unless_fake_and_reports_what_it_could_not_judge(tmp_path, capsys):
    page = html_file(
        tmp_path,
        "figs.html",
        f"<figure><img src='{png_uri()}' alt='A graph of speed.'>"
        "<figcaption>Figure 1.1 X.</figcaption></figure>"
        "<figure><img src='x.png' alt='Another graph.'></figure>",
    )
    assert run("judge", "--output", page) == 2
    assert "--max-usd is required" in capsys.readouterr().err
    out = tmp_path / "judged.json"
    assert run("judge", "--output", page, "--fake", "--sample", "5", "--out", out, "--units") == 0
    data = json.loads(out.read_text())
    assert (data["population"], data["judgeable"], data["skipped"]) == (2, 1, {"no_image": 1})
    # the fake client has no recorded answer: its call is refused, so nothing is judged
    assert data["failed_calls"] == 1 and data["rate"] is None and data["census"] is True
    assert len(data["units"]) == 1


def test_audit_sheet_and_score_round_trip(tmp_path, capsys):
    _pdf, paths = make_reports(tmp_path)
    sheet, key = tmp_path / "sheet.csv", tmp_path / "key.json"
    assert (
        run(
            "audit-sheet",
            "--reports",
            *paths,
            "--pdf-dir",
            tmp_path,
            "--sheet",
            sheet,
            "--key",
            key,
        )
        == 0
    )
    assert "toy|sentence" in capsys.readouterr().out
    with sheet.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and all(r["label"] == "" for r in rows)
    # label every unit: invented when it is the planted sentence
    for row in rows:
        row["label"] = "invented" if "Students often forget" in row["text"] else "supported"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = tmp_path / "audit.json"
    assert run("audit-score", "--key", key, "--sheet", sheet, "--out", result) == 0
    strata = json.loads(result.read_text())["strata"]
    assert strata["toy|sentence"]["precision"] == 1.0
    assert strata["toy|sentence"]["miss_rate"] == 0.0
    assert strata["real|sentence"]["population"]["flagged"] == 0
    assert (
        run(
            "audit-sheet",
            "--reports",
            paths[0],
            "--pdf-dir",
            tmp_path / "nowhere",
            "--sheet",
            sheet,
            "--key",
            key,
        )
        == 2
    )
