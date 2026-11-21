"""`readablestem eval speed`: the seconds a page takes, and the bar a slower reader fails."""

from __future__ import annotations

import json

from readablestem.cli.main import main
from readablestem.core.types import StageFact, StepFacts
from readablestem.eval.measure import Converted

PAGES = 10


def measured(layout_s: float) -> list[Converted]:
    """Two corpus files as a replay measured them: 10 ordinary pages, and a 2-page scan."""

    def one(upload: str, kind: str, pages: int, **stages: float) -> Converted:
        facts = StepFacts(stages=[StageFact(stage=s, seconds=v) for s, v in stages.items()])
        wall = sum(stages.values())
        return Converted(
            upload, pages=pages, kinds=[kind] * pages, facts=facts, wall_s=wall, cpu_s=wall
        )

    return [
        one("w_lecture_5111", "typical", PAGES, start_up=2.0, layout=layout_s, steps=0.5,
            assemble=0.3),
        one("w_scan_slides+image_pages", "scan", 2, start_up=2.0, layout=6.0),
    ]  # fmt: skip


def records(tmp_path, name: str, *runs: list[Converted]):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"runs": [[done.as_dict() for done in run] for run in runs]}))
    return path


def speed(*args) -> int:
    return main(["eval", "speed", *map(str, args)])


def test_a_slower_layout_shows_in_its_stage_and_fails_the_bar(tmp_path, capsys):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    # Layout takes 0.5 s a page; then 0.2 s more on every page.
    assert speed("--from", records(tmp_path, "a", measured(5.0)), "--out", before) == 0
    slower = records(tmp_path, "b", measured(5.0 + 0.2 * PAGES))
    assert speed("--from", slower, "--out", after, "--against", before) == 1
    said = capsys.readouterr().out

    was, now = (json.loads(path.read_text())["metrics"] for path in (before, after))
    assert was["stages"]["layout"]["s_page"]["p50"] == 0.5
    assert now["stages"]["layout"]["s_page"]["p50"] == 0.7
    # Start-up is a job's and stays out of the seconds a page.
    assert was["kinds"]["typical"]["s_page"]["p50"] == 0.58
    assert now["kinds"]["typical"]["s_page"]["p50"] == 0.78
    assert was["start_up_s"]["p50"] == 2.0
    assert "bar: NOT met" in said and "typical: 0.780 s a page at p50, 0.580 before" in said
    # The scan did not change, and the same record against itself meets the bar.
    assert "scan:" not in said.split("bar: NOT met")[1]
    assert speed("--from", slower, "--against", after) == 0


def test_the_bar_judges_nothing_it_cannot_compare(tmp_path, capsys):
    before = tmp_path / "before.json"
    one = records(tmp_path, "a", measured(5.0))
    assert speed("--from", one, "--out", before) == 0

    # Two runs of the same code a fifth apart: the machine, not the reader.
    busy = records(tmp_path, "busy", measured(5.0), measured(6.2))
    assert speed("--from", busy, "--against", before) == 2
    assert "too busy to judge" in capsys.readouterr().out

    elsewhere = json.loads(before.read_text())
    elsewhere["machine"] = {"cpu": "another", "cores": 2}
    other = tmp_path / "other.json"
    other.write_text(json.dumps(elsewhere))
    assert speed("--from", one, "--against", other) == 2
    assert "another machine class" in capsys.readouterr().out


def test_a_conversion_is_timed_page_by_page_and_the_report_names_the_slowest_page(tmp_path, capsys):
    import pymupdf

    from readablestem.config import Settings
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline import run

    pdf = pymupdf.open()
    for number in (1, 2):
        pdf.new_page().insert_text((72, 100), f"Page {number} has a line of ordinary text.")
    output = run.convert_pdf(
        pdf.tobytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(),
    )
    assert output.facts is not None
    done = Converted(
        "w_lecture_5111", pages=2, kinds=["typical"] * 2, facts=output.facts, wall_s=1.0, cpu_s=1.0
    )
    kept = tmp_path / "speed.json"
    assert speed("--from", records(tmp_path, "a", [done]), "--out", kept) == 0
    said = capsys.readouterr().out

    trace = json.loads(kept.read_text())["metrics"]["trace"]
    assert trace["pages"] == 2
    assert {row["page"] for row in trace["slowest"]} == {1, 2}
    assert all(row["step"] in trace["steps"] and row["seconds"] > 0 for row in trace["slowest"])
    assert trace["s_page"]["max"] >= trace["s_page"]["p50"] > 0
    assert "slowest pages:" in said
