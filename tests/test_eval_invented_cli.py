"""`readablestem eval invented ...` end to end on small files (offline)."""

from __future__ import annotations

import base64
import io
import json

import pymupdf
import pytest
from PIL import Image

from readablestem.cli import eval_cli
from readablestem.cli.main import main
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
