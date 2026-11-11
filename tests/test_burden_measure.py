"""`readablestem eval burden`: the decisions a page asks of a reviewer, and the bar a
conversion that asks for more fails."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from readablestem.cli.main import main
from readablestem.eval.measure import Converted

PAGES = 10


def asked(page: int, kind: str, *reasons: str) -> dict:
    return {"page": page, "kind": kind, "reasons": list(reasons), "members": 1}


def measured(*more: dict, missed: int = 0) -> list[Converted]:
    """Two corpus files as a replay measured them: 10 ordinary pages that ask for two
    decisions (and `more`), and a 2-page scan that asks for one on each page."""
    lecture = [asked(0, "equation", "style_mismatch"), asked(3, "figure", "below_accept"), *more]
    scan = [asked(0, "figure", "page_kept_as_image"), asked(1, "figure", "page_kept_as_image")]
    return [
        Converted("w_lecture_5111", pages=PAGES, kinds=["typical"] * PAGES, decisions=lecture),
        Converted(
            "w_scan_slides+image_pages", pages=2, kinds=["scan"] * 2, decisions=scan, missed=missed
        ),
    ]


def records(tmp_path, name: str, run: list[Converted]):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"runs": [[done.as_dict() for done in run]]}))
    return path


def burden(*args) -> int:
    return main(["eval", "burden", *map(str, args)])


@pytest.mark.parametrize("command", ["cost", "speed", "burden", "outputs"])
def test_a_replay_in_which_nothing_could_be_measured_says_so(command, tmp_path, capsys):
    failed = [Converted("w_lecture_5111", pages=PAGES, error="conversion failed")]
    kept = tmp_path / "kept.json"
    kept.write_text(json.dumps({"runs": [[done.as_dict() for done in failed]] * 2}))

    code = main(["eval", command, "--from", str(kept), "--out", str(tmp_path / "out.json")])

    assert code == 2
    assert "no upload could be measured" in capsys.readouterr().err


def test_a_conversion_that_asks_for_more_fails_the_bar(tmp_path, capsys):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert burden("--from", records(tmp_path, "a", measured()), "--out", before) == 0
    # One more question on a page that already had one, for a reason not seen before.
    more = records(tmp_path, "b", measured(asked(3, "table", "table.ragged_rows")))
    assert burden("--from", more, "--out", after, "--against", before) == 1
    said = capsys.readouterr().out

    was, now = (json.loads(path.read_text())["metrics"] for path in (before, after))
    assert (was["pages"], was["decisions"], now["decisions"]) == (12, 4, 5)
    assert (was["kinds"]["typical"]["per_page"], now["kinds"]["typical"]["per_page"]) == (0.2, 0.3)
    assert was["kinds"]["scan"]["per_page"] == now["kinds"]["scan"]["per_page"] == 1.0
    # Most ordinary pages ask nothing: the median page is 0 and the document is not.
    one_page = now["kinds"]["typical"]["page"]
    assert (one_page["p50"], one_page["max"]) == (0, 2)
    assert now["items"] == {"figure": 3, "equation": 1, "table": 1}
    assert now["reasons"]["table.ragged_rows"] == {"decisions": 1, "per_100_pages": 8.33}
    assert "typical: 0.300 decisions a page, 0.200 before" in said
    # The scan did not change, and the same record against itself meets the bar.
    assert "scan: 1.000" not in said
    assert burden("--from", more, "--against", after) == 0


def test_what_was_not_measured_whole_is_left_out_and_not_judged(tmp_path, capsys):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert burden("--from", records(tmp_path, "a", measured()), "--out", before) == 0

    # A request the recording could not answer fails its element, and a failed element is
    # a decision the recording made: the upload is named and its pages are not counted.
    short = records(tmp_path, "short", measured(missed=3))
    assert burden("--from", short, "--out", after, "--against", before) == 2
    now = json.loads(after.read_text())["metrics"]
    assert now["uploads"]["answers_missing"] == {"w_scan_slides+image_pages": 3}
    assert (now["uploads"]["measured"], now["pages"], now["decisions"]) == (1, PAGES, 2)
    assert "1 now" in capsys.readouterr().out

    # A record kept before decisions were measured says nothing about them: it is not a
    # document that asks for none.
    old = measured()
    old[0] = replace(old[0], decisions=None)
    assert burden("--from", records(tmp_path, "old", old), "--out", after) == 0
    now = json.loads(after.read_text())["metrics"]
    assert now["uploads"]["not_measured"] == ["w_lecture_5111"]
    assert (now["pages"], now["decisions"]) == (2, 2)
