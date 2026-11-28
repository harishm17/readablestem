"""The olmOCR-Bench adapter (`eval.olmocr_bench`): the student page as the markdown the
benchmark's own checker reads. Six hand-made pages with known checks, scored by the real
checker when this machine has its environment (`readablestem eval parsing-bench --fetch`)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pymupdf
import pytest

from readablestem.eval import olmocr_bench as bench
from readablestem.eval.order_report import OrderAnswers

WRITERS = (
    '<section class="notice readablestem"><p>This version has not been reviewed by a '
    "person.</p></section>"
    '<nav class="toc"><ul><li>Contents of the page</li></ul></nav>'
)


def page(body: str) -> str:
    about = '<section class="about readablestem"><p>About this version of the page.</p></section>'
    return f"<html><body><main>{WRITERS}{body}{about}</main></body></html>"


# name -> (student page, the benchmark check on it, whether it must pass)
PAGES = {
    "order_right": (
        page('<h1 id="a">Title</h1><p>Left column opens here.</p><p>Right column closes here.</p>'),
        {
            "type": "order",
            "before": "Left column opens",
            "after": "Right column closes",
            "max_diffs": 1,
        },
        True,
    ),
    "order_wrong": (
        page('<h1 id="a">Title</h1><p>Right column closes here.</p><p>Left column opens here.</p>'),
        {
            "type": "order",
            "before": "Left column opens",
            "after": "Right column closes",
            "max_diffs": 1,
        },
        False,
    ),
    "head_left_in": (
        page('<h1 id="a">Title</h1><p>Journal of Tests, volume 12</p><p>Body of the page.</p>'),
        {
            "type": "absent",
            "text": "Journal of Tests, volume 12",
            "max_diffs": 2,
            "case_sensitive": False,
            "first_n": None,
            "last_n": None,
        },
        False,
    ),
    "ligature": (
        page('<h1 id="a">T</h1><p>The eﬃcient ﬁlter of the sys­tem.</p>'),
        {
            "type": "present",
            "text": "The efficient filter of the system.",
            "max_diffs": 0,
            "case_sensitive": True,
            "first_n": None,
            "last_n": None,
        },
        True,
    ),
    "numbered": (
        page(
            '<h1 id="a">T</h1><ol start="9"><li>Chang RJ, Nakamura RM, Judd HI.</li>'
            "<li>Coviello AD, Legro RS, and so on…</li></ol>"
        ),
        {
            "type": "present",
            "text": "10. Coviello AD, Legro RS, and so on...",
            "max_diffs": 0,
            "case_sensitive": True,
            "first_n": None,
            "last_n": None,
        },
        True,
    ),
    "table": (
        page(
            "<table><tr><th>Name</th><th>Mass</th></tr><tr><td>Iron</td><td>55.8</td></tr></table>"
        ),
        {"type": "table", "cell": "55.8", "left": "Iron", "up": "Mass", "max_diffs": 0},
        True,
    ),
}


def test_the_page_is_read_as_the_student_hears_it() -> None:
    text = bench.html_to_markdown(PAGES["table"][0] + PAGES["ligature"][0])
    assert "<td>55.8</td>" in text
    assert "efficient filter of the system." in text
    for added in ("not been reviewed", "Contents of the page", "About this version"):
        assert added not in text


@pytest.mark.skipif(
    not bench.checker_python(bench.default_folder()).is_file(), reason="no checker environment"
)
def test_the_checker_scores_the_five_pages_as_built(tmp_path: Path) -> None:
    folder = tmp_path / "olmocr-bench"
    (folder / "bench_data").mkdir(parents=True)
    os.symlink(bench.checker_python(bench.default_folder()).parents[1], tmp_path / "checker-env")
    kinds = {
        "order": "multi_column",
        "absent": "headers_footers",
        "present": "long_tiny_text",
        "table": "table_tests",
    }
    files: dict[str, list[str]] = {}
    for name, (html, check, _) in PAGES.items():
        pdf = f"x/{name}.pdf"
        row = {"pdf": pdf, "page": 1, "id": name, "checked": "verified", "url": None} | check
        files.setdefault(kinds[check["type"]], []).append(json.dumps(row))
        markdown = tmp_path / "run" / "markdown" / "x" / f"{name}.md"
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(bench.html_to_markdown(html), encoding="utf-8")
    for category in bench.CATEGORIES:
        lines = files.get(category.name, [])
        if not category.skipped:
            (folder / "bench_data" / f"{category.name}.jsonl").write_text("\n".join(lines) + "\n")
    rows = bench.run_checker(
        folder, tmp_path / "run", [c for c in bench.CATEGORIES if not c.skipped]
    )
    got = {row["id"]: row["passed"] for row in rows if row["type"] != "baseline"}
    assert got == {name: wanted for name, (_, _, wanted) in PAGES.items()}
    assert sum(got.values()) == 4


def test_a_page_order_answer_the_recording_lacks_is_counted_and_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No `--live`: the recording is all there is, so a client built to ask the model is a fault.
    def no_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("the provider was asked")

    monkeypatch.setattr("readablestem.models.make_live_client", no_client)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "First paragraph of the page, long enough to be text.")
    page.insert_text((72, 130), "Second paragraph of the page, also long enough to read.")
    recording = tmp_path / "answers.jsonl"
    recording.write_text("")

    done = bench.convert_page(document.tobytes(), OrderAnswers(str(recording)))

    asked = done["model_order"]
    assert done["error"] == "" and not done["scan"]
    assert (asked["pages"], asked["answered_live"], asked["usd"]) == (1, 0, 0)
    assert asked["missed"] > 0  # requests: a refused one is asked once more, and missed again
    assert recording.read_text() == ""
    summary = bench.order_summary({"x.pdf": done})
    assert (summary["on"], summary["answered"], summary["missed"], summary["failed"]) == (
        True,
        0,
        1,
        0,
    )
    assert bench.order_summary({"x.pdf": bench.convert_page(document.tobytes())})["on"] is False


def test_a_live_run_asks_again_the_pages_an_earlier_cap_left_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live run stopped by its cap writes the rest of its pages with `missed`; resuming it
    # must ask those again, and leave the answered ones alone.
    category = bench.CATEGORIES[0]
    pdfs = tmp_path / "bench" / "bench_data" / "pdfs" / category.folder
    pages = tmp_path / "run" / "pages" / category.folder
    pdfs.mkdir(parents=True)
    pages.mkdir(parents=True)
    for name, missed in (("answered", 0), ("missed", 1)):
        (pdfs / f"{name}.pdf").write_bytes(b"%PDF")
        (pages / f"{name}.json").write_text(json.dumps({"model_order": {"missed": missed}}))
    converted: list[str] = []
    monkeypatch.setattr(
        bench,
        "_convert_file",
        lambda pdf, page, answers=None: converted.append(Path(pdf).stem) or "",
    )

    class Live:
        live = True

    assert (
        bench.convert_all(
            tmp_path / "bench", tmp_path / "run", [category], at_once=1, answers=Live()
        )
        == 1
    )
    assert converted == ["missed"]
