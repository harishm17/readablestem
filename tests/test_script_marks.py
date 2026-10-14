"""pipeline.script_marks: raised and lowered runs travel as private marks inside text and
are cut out once, as offsets into the final text."""

from __future__ import annotations

from readablestem.core.types import ScriptRun
from readablestem.pipeline import script_marks as marks


def run(start: int, end: int, kind: str) -> ScriptRun:
    return ScriptRun(start=start, end=end, kind=kind)  # type: ignore[arg-type]


def test_wrap_puts_each_run_between_its_marks_and_split_takes_them_out():
    text = marks.wrap(list("H2O Na+"), [None, "sub", None, None, None, None, "sup"])
    assert marks.has_marks(text)
    assert marks.split(text) == ("H2O Na+", [run(1, 2, "sub"), run(6, 7, "sup")])


def test_text_without_marks_is_only_stripped():
    assert marks.split("  plain text ") == ("plain text", [])


def test_offsets_follow_the_stripped_text():
    text = "  " + marks.wrap(list("x2"), [None, "sup"]) + "  "
    assert marks.split(text) == ("x2", [run(1, 2, "sup")])


def test_a_run_that_is_empty_or_blank_after_stripping_is_dropped():
    text = "ab" + marks.SUP_OPEN + " " + marks.SUP_CLOSE
    assert marks.split(text) == ("ab", [])
    assert marks.split(marks.SUB_OPEN + marks.SUB_CLOSE + "ab") == ("ab", [])


def test_marks_that_do_not_pair_are_ignored():
    assert marks.split("a" + marks.SUP_CLOSE + "b" + marks.SUB_OPEN + "c") == ("abc", [])


def test_a_mark_already_in_the_characters_is_dropped():
    text = marks.wrap(["a", marks.SUP_OPEN, "b"], [None, None, "sub"])
    assert marks.split(text) == ("ab", [run(1, 2, "sub")])
    assert marks.strip_marks(text) == "ab"
