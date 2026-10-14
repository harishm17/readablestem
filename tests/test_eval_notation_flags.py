"""eval/notation_flags.py (the course evidence's extra style flags, counted offline) and
`eval pipeline --notation` (the equations task with a course notation in force)."""

from __future__ import annotations

import json

import pytest

from readablestem.cli import eval_cli
from readablestem.cli.main import main
from readablestem.eval import notation_flags
from readablestem.pipeline import equation_step, options
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_eval_cli import fake_inputs as fake_inputs  # fixture, re-exported for pytest
from test_eval_cli import gold as gold  # fixture, re-exported for pytest
from test_eval_cli import pipeline_args
from test_eval_support import fake_transcribe, settings

BOLD_V = "\U0001d42f"
ITALIC_V = "\U0001d463"
ITALIC_T = "\U0001d461"


def mathml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "block")


def test_unstyled_removes_every_math_style():
    assert notation_flags.unstyled(f"{BOLD_V} = d/{ITALIC_T}") == "v = d/t"


def test_flags_are_counted_with_and_without_the_styles_of_the_text_layer():
    rows = [
        # A bold v read plain: the printed layer already says so; stripped, the course does.
        (f"{BOLD_V} = 5", mathml("v = 5"), True),
        # A plain v (speed) read plain: right, but stripped the course questions it.
        (f"{ITALIC_V} = 5", mathml("v = 5"), False),
        # Bold kept: nothing to say either way.
        (f"{BOLD_V} = 5", mathml(r"\mathbf{v} = 5"), False),
    ]
    printed = notation_flags.count_flags(rows, frozenset({"v"}), strip=False)
    assert (printed.items, printed.wrong) == (3, 1)
    assert (printed.flagged_before, printed.flagged_after) == (1, 1)
    assert (printed.new_true, printed.new_false) == (0, 0)
    stripped = notation_flags.count_flags(rows, frozenset({"v"}), strip=True)
    assert (stripped.flagged_before, stripped.flagged_after) == (0, 2)
    assert (stripped.new_true, stripped.new_false) == (1, 1)


def test_rows_come_from_an_items_file_and_the_render_cache(tmp_path, monkeypatch):
    from readablestem.eval import pipeline_eval, render

    records = [{"id": "g/eq-0", "kind": "equation", "mathml": "<math><mi>v</mi></math>"}]
    monkeypatch.setattr(pipeline_eval, "load_gold", lambda _: records)
    monkeypatch.setattr(render, "render_equation_text", lambda m, c: f"{BOLD_V} = 5")
    items = tmp_path / "items.jsonl"
    rows = [
        {"id": "g/eq-0", "pred_mathml": "<math><mi>v</mi></math>", "exact_lenient": False},
        {"id": "g/eq-0", "pred_mathml": None, "exact_lenient": False},  # no proposal
        {"id": "other", "pred_mathml": "<math/>", "exact_lenient": True},  # not in gold
    ]
    items.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    loaded = notation_flags.load_rows(items, tmp_path, tmp_path)
    assert loaded == [(f"{BOLD_V} = 5", "<math><mi>v</mi></math>", True)]


# --------------------------------------------------------------------------- the CLI


@pytest.fixture
def seen(monkeypatch, fake_inputs):
    """The options in force at each transcription (offline settings, fake inputs)."""
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings())
    found = []

    def recording(*args, **kwargs):
        found.append(options.current())
        return fake_transcribe(*args, **kwargs)

    monkeypatch.setattr(equation_step, "transcribe", recording)
    return found


def test_the_equations_task_runs_with_the_course_notation(gold, tmp_path, seen):
    args = pipeline_args(gold, tmp_path, "--fake", "--notation", "bold d", "v=other")
    assert main(args) == 0
    assert seen and all(o.notation_memory for o in seen)
    assert [(e.symbol, e.meaning) for e in seen[0].notation] == [
        ("bold d", "vector"),
        ("v", "other"),
    ]
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["notation"] == [
        {"canonical": "\U0001d41d", "meaning": "vector"},
        {"canonical": "v", "meaning": "other"},
    ]
    assert options.current().notation == []  # nothing left in force after the run


def test_without_notation_the_run_is_as_before(gold, tmp_path, seen):
    assert main(pipeline_args(gold, tmp_path, "--fake")) == 0
    assert seen and not any(o.notation_memory or o.notation for o in seen)
    assert "notation" not in json.loads((tmp_path / "report.json").read_text())


@pytest.mark.parametrize("spec", ["the speed thing", "bold d=velocity"])
def test_a_notation_entry_that_cannot_be_read_stops_the_run(gold, tmp_path, seen, spec, capsys):
    assert main(pipeline_args(gold, tmp_path, "--fake", "--notation", spec)) == 2
    assert "not a notation entry" in capsys.readouterr().err
    assert not seen


def test_notation_is_for_the_equations_task_only(gold, tmp_path, seen, capsys):
    args = pipeline_args(gold, tmp_path, "--fake", "--notation", "bold d")
    args[args.index("equations")] = "headings"
    assert main(args) == 2
    assert "equations task only" in capsys.readouterr().err
