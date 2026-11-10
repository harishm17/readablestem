"""eval/notation_flags.py (the course evidence's extra style flags, counted offline) and
`eval pipeline --notation` (the equations task with a course notation in force)."""

from __future__ import annotations

import pytest

from readablestem.cli import eval_cli
from readablestem.eval import notation_flags
from readablestem.pipeline import equation_step, options
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_eval_cli import fake_inputs as fake_inputs  # fixture, re-exported for pytest
from test_eval_cli import gold as gold  # fixture, re-exported for pytest
from test_eval_support import fake_transcribe, settings

BOLD_V = "\U0001d42f"
ITALIC_V = "\U0001d463"
ITALIC_T = "\U0001d461"


def mathml(latex: str) -> str:
    return safe_latex_to_mathml(latex, "block")


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
