"""Empty alt text only for decoration with evidence (WS6.4, finding F-246).

`alt=""` tells a screen reader there is nothing there, so a figure is written with it only
when something other than the figure model's word says it is decoration:

- `REPEATED`: the same picture (the same crop, pixel for pixel) on at least half of the
  document's pages, and on two or more: a logo or a banner, the rule layout uses for what
  repeats on the pages (`layout._MarginFilter`), here for pictures in the body;
- `SHAPE`: a rule, a band at least `RULE_RATIO` times as long as it is thick and at most
  `RULE_MAX_PT` thick;
- `PERSON`: a person marked it as decoration on the review screen ("Decorative"), or
  accepted the proposal that it is.

The rule is the same for a figure the model calls decorative and for one whose reading
failed (`settle`, run once by each converter after every step): with evidence, the figure
is decoration and its evidence is stored on its record (`FigureFix.decoration`); a figure
whose reading failed becomes decoration with an optional note that says so. Without
evidence, a figure the model calls decorative is a required decision, and the student file
writes it as "Figure not described" with its note until a person decides
(`is_decoration`, read by the renderer). Nothing with no description is silent.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, MutableMapping

from readablestem.core import triage
from readablestem.core.types import (
    IMAGE_PAGE_OMISSION_REASON,
    IMAGE_PAGE_ORIGINAL_REASON,
    KEPT_ORIGINAL_STATUSES,
    Check,
    Decoration,
    Element,
    FigureFix,
    Fix,
    Kind,
    Status,
)

REPEATED: Decoration = "repeated"
SHAPE: Decoration = "shape"
PERSON: Decoration = "person"
RULE_RATIO = 8.0  # a rule is at least this many times as long as it is thick
RULE_MAX_PT = 16.0  # ... and at most this thick (layout drops one under 12 pt)
CHECK_NAME = "figure.decoration"
DECORATION_SOURCE = "decoration"  # its row in `core.triage.SOURCES`
UNPROVEN = "decoration_unproven"  # called decorative, nothing else says so: a decision
BY_RULE = "decoration_by_rule"  # its reading failed, and it is decoration by the rule


def repeated_ids(elements: Iterable[Element], crops: Mapping[str, bytes]) -> set[str]:
    """Ids of the figures whose picture repeats (`REPEATED`)."""
    figures = [e for e in elements if e.kind is Kind.FIGURE]
    pages = {e.page for e in elements}
    by_picture: dict[str, list[Element]] = defaultdict(list)
    for element in figures:
        if png := crops.get(element.id):
            by_picture[hashlib.sha256(png).hexdigest()].append(element)
    needed = max(2, -(-len(pages) // 2))  # at least half of the pages, and two
    return {
        element.id
        for same in by_picture.values()
        if len({e.page for e in same}) >= needed
        for element in same
    }


def is_rule(element: Element) -> bool:
    """A thin band (`SHAPE`)."""
    x0, y0, x1, y1 = element.bbox
    thin, long = sorted((x1 - x0, y1 - y0))
    return 0 < thin <= RULE_MAX_PT and long >= RULE_RATIO * thin


def evidence(element: Element, fix: Fix | None, repeated: Iterable[str] = ()) -> Decoration | None:
    """Why this figure is decoration, or None when nothing but the model says so. Stored
    evidence first: a repeat is found from the crops once, by `settle`, and kept on the
    record, so every later render and count reads the same answer without the crops."""
    figure = fix.figure if fix is not None else None
    if figure is not None and figure.decoration is not None:
        return figure.decoration
    if fix is not None and figure is not None and figure.decorative and fix.decided_by_reviewer:
        return PERSON
    if element.id in repeated:
        return REPEATED
    return SHAPE if is_rule(element) else None


def is_decoration(element: Element, fix: Fix | None) -> bool:
    """Written with `alt=""` (`assemble`) and counted as decoration (`checked`): a decorative
    proposal in use, with evidence."""
    return (
        fix is not None
        and fix.status not in KEPT_ORIGINAL_STATUSES
        and fix.figure is not None
        and fix.figure.decorative
        and evidence(element, fix) is not None
    )


def _page_picture(element: Element) -> bool:
    """A page converted from its image: its original, or writing it left out. Not figures."""
    return bool({IMAGE_PAGE_ORIGINAL_REASON, IMAGE_PAGE_OMISSION_REASON} & set(element.reasons))


def settle(
    elements: Iterable[Element],
    fixes: MutableMapping[str, Fix],
    crops: Mapping[str, bytes],
    *,
    kept: Iterable[str] = (),
) -> int:
    """The rule on every figure's fix, in place, after every step (as
    `image_page.enforce_maths_required` is). `kept`: fix reasons of pictures kept as what
    they are (a scanned page, handwriting), which have their own words and are left alone.
    A person's decision is never changed. Returns how many fixes changed."""
    elements = list(elements)
    repeated = repeated_ids(elements, crops)
    kept = set(kept)
    changed = 0
    for element in elements:
        fix = fixes.get(element.id)
        if element.kind is not Kind.FIGURE or fix is None or fix.kind is not Kind.FIGURE:
            continue
        if fix.decided_by_reviewer or fix.reason in kept or _page_picture(element):
            continue
        found = evidence(element, fix, repeated)
        figure = fix.figure
        if figure is not None and figure.decorative and fix.status not in KEPT_ORIGINAL_STATUSES:
            if found is not None:
                if figure.decoration != found:
                    fix.figure = figure.model_copy(update={"decoration": found})
                    changed += 1
                continue
            triage.add_source(fix, DECORATION_SOURCE)  # before the status: it asks if not asked
            if fix.status is Status.ACCEPTED:
                fix.status = Status.NEEDS_REVIEW
            if not any(c.code == UNPROVEN for c in fix.checks):
                unproven = Check(name=CHECK_NAME, passed=False, severity="warning", code=UNPROVEN)
                fix.checks = [*fix.checks, unproven]
            changed += 1
        elif fix.status in KEPT_ORIGINAL_STATUSES and found is not None:
            # Its reading failed, and it is decoration by the rule: a note, not a decision.
            fix.status = Status.ACCEPTED
            fix.reason = None
            fix.review = "optional"
            fix.figure = FigureFix(
                figure_type=figure.figure_type if figure is not None else "other",
                alt="",
                decorative=True,
                decoration=found,
            )
            by_rule = Check(name=CHECK_NAME, passed=False, severity="warning", code=BY_RULE)
            fix.checks = [*fix.checks, by_rule]
            changed += 1
    return changed
