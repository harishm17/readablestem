"""A document is read the same whatever else is being read at the same time.

What a document turns out to be (made by TeX, a slide deck, how it spells its compounds, whether
its displays are grouped) is known to the steps that read it for the length of one call, and to
nothing else: a second document read while the first is half read must not change either.
"""

import json
from pathlib import Path

from readablestem.pipeline.layout import analyze

FIXTURES = Path(__file__).parent / "fixtures" / "layout"
TEX = FIXTURES / "latex" / "display_groups.pdf"  # made by TeX; its displays are grouped
DECK = FIXTURES / "latex" / "beamer_basic.pdf"  # a slide deck made by TeX
PLAIN = FIXTURES / "lecture_notes.pdf"  # printed by a browser; nothing of the above


def _read(path: Path, **kwargs) -> str:
    result = analyze(path.read_bytes(), **kwargs)
    return json.dumps([e.model_dump(mode="json") for e in result.elements], sort_keys=True)


class _ReadsAnotherDocument:
    """The page-math hook, which layout calls in the middle of each page: here it reads
    another whole document, on the same thread, and goes on."""

    def __init__(self, other: Path, **kwargs):
        self.other, self.kwargs, self.read = other, kwargs, []

    def on_page(self, number, text_rows, ordered, rules, **_):
        self.read.append(_read(self.other, **self.kwargs))
        return text_rows

    def after_regions(self, number, text_rows):
        pass


def test_a_document_read_in_the_middle_of_another_changes_neither():
    grouped = {"display_grouping": True}
    cases = [
        (TEX, grouped, PLAIN, {}),
        (TEX, grouped, DECK, {}),
        (DECK, {}, TEX, grouped),
        (PLAIN, {}, TEX, grouped),
    ]
    assert _read(TEX, **grouped) != _read(TEX), "the fixture must depend on its own grouping"
    for outer, outer_kwargs, inner, inner_kwargs in cases:
        hook = _ReadsAnotherDocument(inner, **inner_kwargs)
        around = _read(outer, page_math=hook, **outer_kwargs)
        assert around == _read(outer, **outer_kwargs)
        assert hook.read and set(hook.read) == {_read(inner, **inner_kwargs)}
