"""The inline spans of an element, in one place (content model, decision 7).

An element's `text` is one plain string; what parts of it mean is kept beside it as spans,
offsets into `text` as stored (inline markers included): links (`Element.links`), colour
runs (`colour_runs`), raised and lowered runs (`script_runs`) and marked runs (`runs`:
struck text). Every place that keeps spans through a change of the text (a reviewer's text
edit, the ids rewritten when parts are combined), that empties them, or that writes them
(`pipeline.inline_text`, the LaTeX reader) goes through this module instead of naming the
fields one by one. So a span kind added to `Element` is added here once, and no edit,
combine or writer can lose it silently by not knowing its field.

- `spans`: every span of an element, by field, for a writer;
- `moved`: every span moved to where its words are after a change of the text, with what
  was dropped because its words are gone, counted by kind;
- `cleared`: the update that takes every span off (an element whose text is gone);
- `add`: put one span on an element, in the field of its type (a reader filling spans).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from readablestem.core.types import ColourRun, Element, Link, Run, ScriptRun

# Every field of `Element` that holds spans of its text.
SPAN_FIELDS = ("links", "colour_runs", "script_runs", "runs")

type Span = Link | ColourRun | ScriptRun | Run
# Where the span [start, end) of the old text is in the new one, or None when its words are
# not all there any more (the span is then dropped: it would cover other words).
type Move = Callable[[int, int], tuple[int, int] | None]


@dataclass(frozen=True)
class Spans:
    """An element's spans, each kind in the order it was stored."""

    links: tuple[Link, ...] = ()
    colours: tuple[ColourRun, ...] = ()
    scripts: tuple[ScriptRun, ...] = ()
    runs: tuple[Run, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.links or self.colours or self.scripts or self.runs)


def spans(element: Element) -> Spans:
    return Spans(
        tuple(element.links),
        tuple(element.colour_runs),
        tuple(element.script_runs),
        tuple(element.runs),
    )


def kind_of(span: Span) -> str:
    """What a span is, as a count of dropped spans names it: "link", "colour", "script", or
    a run's own kind ("deleted")."""
    if isinstance(span, Link):
        return "link"
    if isinstance(span, ColourRun):
        return "colour"
    if isinstance(span, ScriptRun):
        return "script"
    return span.kind


def moved(
    element: Element, move: Move, *, target: Callable[[str], str] | None = None
) -> tuple[dict[str, Any], Counter[str]]:
    """Every span of `element` moved by `move`, as an update for `element.model_copy`, and
    how many were dropped by kind (`kind_of`). `target` renames the element a link points
    at (combine rewrites ids); a web link has no target and keeps its address."""
    update: dict[str, Any] = {}
    dropped: Counter[str] = Counter()
    for name in SPAN_FIELDS:
        kept = []
        for span in getattr(element, name):
            place = move(span.start, span.end)
            if place is None:
                dropped[kind_of(span)] += 1
                continue
            change: dict[str, Any] = {"start": place[0], "end": place[1]}
            if target is not None and isinstance(span, Link) and span.target_id:
                change["target_id"] = target(span.target_id)
            kept.append(span.model_copy(update=change))
        update[name] = kept
    return update, dropped


def cleared() -> dict[str, Any]:
    """The update that takes every span off an element (its text is gone)."""
    return {name: [] for name in SPAN_FIELDS}


def add(element: Element, span: Span) -> None:
    """Put `span` on `element`, in the field that holds its type."""
    if isinstance(span, Link):
        element.links.append(span)
    elif isinstance(span, ColourRun):
        element.colour_runs.append(span)
    elif isinstance(span, ScriptRun):
        element.script_runs.append(span)
    else:
        element.runs.append(span)
