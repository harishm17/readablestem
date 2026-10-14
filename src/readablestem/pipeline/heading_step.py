"""Heading fixes. Deterministic: no model call.

Turns `Kind.HEADING` elements into `Fix(kind=HEADING, heading=HeadingFix(level, text))`.
Levels come from layout clustering (`layout.heading_levels`); this step repairs the outline
so it never skips a level going down (h1 -> h3 becomes h1 -> h2) and the first heading is
h1 or h2, and decides which headings need a person's decision.

Evidence and confidence (also in docs/layout.md):

| Evidence (from layout `Element.reasons`)          | Base | Strong? |
|---------------------------------------------------|------|---------|
| larger font (>= 1.15 x body text)                 | 0.90 | yes     |
| ... plus a section number ("2.3", "Chapter 2")    | 0.95 | yes     |
| tagged heading (the PDF's own tags: H1-H6)       | 0.90 | yes     |
| ... plus a section number                         | 0.95 | yes     |
| bold body-size line, or a standalone numbered line | 0.60 | no      |
| ... plus a section number                         | 0.88 | yes     |

At body size, layout only gives the "numbered section" reason for a real section shape
(`layout.section_heading_number`: "2.3 Units of Speed", "Chapter 2"; not "2 kg",
"12 Metres", "0.5 s" or "10 October 2026"), so quantities and dates are never accepted as
numbered headings.

Penalties: all caps -0.15, fewer than 4 letters -0.20, list-style number ("1.", "A)")
without a larger font -0.15, outline repaired -0.03. A heading is *certain* only with
strong evidence and none of the first three penalties (confidence >= 0.85, the default
`Settings.auto_accept_confidence`). Check codes are content-free categories
("heading.level_repaired", "heading.weak_evidence").

Review tiers ("meaning-level only" policy, 2026-10-08; sentence rule 2026-10-14, T2-7).
Every heading is written as a heading either way; the tier only says whether a person
must decide:

- certain: ACCEPTED, review "none" ("optional" when the outline repair moved it);
- uncertain and on its own: ACCEPTED, review "optional" (its checks are the notes): a
  single heading with weak evidence, or a level, is cheap to fix later;
- a sentence: weak evidence (bold at body size) and the line reads as a sentence or a
  lead-in (ends in a full stop, a question mark or a colon, three words or more):
  NEEDS_REVIEW, review "required", check "heading.sentence_like". Whether "Given a
  binomial, write it in expanded form." is a heading or a bold instruction is a meaning
  question, and every wrong guess the dev-v2 headings run made sat in the optional bucket
  (backlog A5);
- uncertain and repeated: when the same label (normalised text) with the same style
  (font size, weight, evidence, level) is uncertain two or more times ("Teacher Support"
  as a small bold line in every box), the copies make one group: NEEDS_REVIEW, review
  "required", sharing `group_key` ("hg-" + the first one's element id) and `group_size`.
  The review screen asks once: keep them all as headings at that level, choose another
  level, or make them all plain bold text. One wrong guess repeated eleven times is worth
  a decision; a label used once is not. (T2-7 tried making groups optional notes; the
  development sets disagreed and one decision per group was kept: docs/layout.md "Review
  tiers" has the measurements.)

Bold run-in labels ("Solution", "Discussion", "Procedure" in an OpenStax worked example)
are not headings in OpenStax's gold outline, but no structural rule separated them from
real box headings reliably (docs/layout.md, "Run-in labels"), so they are not guessed
at: on their own they are optional notes, repeated they form a group like any label.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from readablestem.core.types import (
    HEADING_REPEATS_ABOVE,
    Check,
    Element,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from readablestem.pipeline.layout import (
    REASON_LARGER_FONT,
    REASON_LIST_NUMBER,
    REASON_NUMBERED,
    heading_levels,
    tagged_level,
)

ACCEPT_AT = 0.85  # matches the default Settings.auto_accept_confidence
MIN_LETTERS = 4  # fewer letters than this ("Ex.", "A") is too little to trust
MIN_GROUP = 2  # uncertain copies of one label that make a group decision
GROUP_PREFIX = "hg-"
MIN_SENTENCE_WORDS = 3  # a shorter line ("Answer:", "Definition.") is a label, not a sentence
SENTENCE_ENDS = ".?:"
_CLOSERS = "\"'\u2019\u201d)]"  # closing quotes and brackets after the final mark


def heading_fixes(elements: list[Element]) -> list[Fix]:
    """One fix per heading element, in reading order, with a repaired outline.

    A heading whose text is the same as the heading directly above it (nothing between
    them), at any level, is not a second heading: it gets a fix that writes it as bold
    text (`repeated_above`), and takes no part in the outline, the levels or the groups.
    OpenStax prints a box's label and its title as two lines ("Teacher Support" twice);
    a screen-reader user who navigates by headings should hear it once. The same text
    with content between the two copies is a real second heading and stays one."""
    repeats = repeated_above(elements)
    headings = sorted(
        (e for e in elements if e.kind == Kind.HEADING and e.id not in repeats),
        key=lambda e: e.order,
    )
    raw = heading_levels(headings)
    levels = repair_outline([raw[h.id] for h in headings])
    fixes = [_fix(h, raw[h.id], level) for h, level in zip(headings, levels, strict=True)]
    group_repeated(headings, fixes)
    bold = [_bold_text_fix(e) for e in elements if e.id in repeats]
    order = {e.id: e.order for e in elements}
    return sorted([*fixes, *bold], key=lambda fix: order[fix.element_id])


def repeated_above(elements: Sequence[Element]) -> set[str]:
    """Ids of the headings that repeat the heading directly above them.

    "Directly above" is the previous element in reading order (any page). The text is the
    same after `label_key` (case, spacing and punctuation aside); the level does not
    matter, so "Teacher Support" at h2 over "Teacher Support" at h3 is one heading (T1-13
    first required the same level too, because the development set's gold counts both
    lines as headings; the student-facing outline won, see docs/eval.md 2026-10-14). A
    third copy repeats the second, so a run of copies keeps only the first as a heading.
    A label with no letters or digits ("-") never counts."""
    found: set[str] = set()
    ordered = sorted(elements, key=lambda e: e.order)
    for above, element in pairwise(ordered):
        if above.kind != Kind.HEADING or element.kind != Kind.HEADING:
            continue
        label = label_key(element.text or "")
        if label and label == label_key(above.text or ""):
            found.add(element.id)
    return found


def _bold_text_fix(element: Element) -> Fix:
    """Accepted, with no heading: the assembler writes the text bold, in a paragraph."""
    return Fix(
        element_id=element.id,
        kind=Kind.HEADING,
        status=Status.ACCEPTED,
        reason=HEADING_REPEATS_ABOVE,
        review="none",
    )


def repair_outline(levels: Sequence[int]) -> list[int]:
    """Levels that never step down by more than one; the first heading is h1 or h2.

    Siblings stay siblings: each heading goes one level below the nearest earlier heading
    with a smaller raw level, so h1, h3, h3, h2, h3 becomes h1, h2, h2, h2, h3. A repair
    only ever raises a heading (smaller number); going back up the outline is always fine.
    """
    repaired: list[int] = []
    open_parents: list[tuple[int, int]] = []  # (raw level, repaired level)
    for raw in levels:
        while open_parents and open_parents[-1][0] >= raw:
            open_parents.pop()
        level = open_parents[-1][1] + 1 if open_parents else min(raw, 2)
        open_parents.append((raw, level))
        repaired.append(level)
    return repaired


def label_key(text: str) -> str:
    """A heading's label for grouping: NFKC, case-folded, letters and digits only."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def style_key(element: Element, level: int) -> tuple[float | None, bool, bool, int]:
    """What makes two copies of a label look the same: font size to the half point,
    weight, whether the font is larger than body text, and the proposed level."""
    size = round(element.font_size * 2) / 2 if element.font_size else None
    return (size, element.bold, REASON_LARGER_FONT in element.reasons, level)


def group_repeated(headings: Sequence[Element], fixes: Sequence[Fix]) -> None:
    """Turn repeated uncertain labels into one required decision per label (in place).

    Uncertain = the optional single headings `_fix` made with a failed evidence or
    shape check (not the certain ones whose only note is the outline repair, and not the
    sentence-like ones, which are required decisions on their own).
    """
    groups: dict[tuple, list[Fix]] = {}
    for element, fix in zip(headings, fixes, strict=True):
        if fix.heading is None or fix.review != "optional" or not _uncertain(fix):
            continue
        key = (label_key(fix.heading.text), style_key(element, fix.heading.level))
        groups.setdefault(key, []).append(fix)
    for members in groups.values():
        if len(members) < MIN_GROUP:
            continue
        group_key = GROUP_PREFIX + members[0].element_id
        for fix in members:
            fix.status = Status.NEEDS_REVIEW
            fix.review = "required"
            fix.group_key = group_key
            fix.group_size = len(members)


def sentence_like(text: str) -> bool:
    """Does a line read as a sentence or a lead-in rather than a title: it ends in a full
    stop, a question mark or a colon (closing quotes and brackets aside) and has at least
    `MIN_SENTENCE_WORDS` words?"""
    stripped = text.rstrip().rstrip(_CLOSERS).rstrip()
    return stripped[-1:] in SENTENCE_ENDS and len(text.split()) >= MIN_SENTENCE_WORDS


UNCERTAIN_CODES = frozenset(
    {"heading.weak_evidence", "heading.all_caps", "heading.very_short", "heading.list_like"}
)


def _uncertain(fix: Fix) -> bool:
    return any(c.code in UNCERTAIN_CODES for c in fix.failed_checks)


@dataclass(frozen=True)
class _Evidence:
    larger_font: bool
    numbered: bool
    list_like: bool
    all_caps: bool
    very_short: bool
    tagged: bool = False  # the PDF's own tags call it a heading (`pipeline.tagged`)

    @property
    def strong(self) -> bool:
        return self.larger_font or self.numbered or self.tagged

    @property
    def maybe_list_item(self) -> bool:
        """A body-size "1. Measure the cart" reads like a list item; in a large font,
        "1. Introduction" is an ordinary heading style."""
        return self.list_like and not (self.larger_font or self.tagged)

    @property
    def weak_flags(self) -> bool:
        return self.all_caps or self.very_short or self.maybe_list_item

    def confidence(self, repaired: bool) -> float:
        if self.larger_font or self.tagged:
            value = 0.95 if self.numbered else 0.90
        else:
            value = 0.88 if self.numbered else 0.60
        value -= 0.15 * self.all_caps + 0.20 * self.very_short + 0.15 * self.maybe_list_item
        value -= 0.03 * repaired
        return round(min(0.99, max(0.05, value)), 2)


def _evidence(element: Element, text: str) -> _Evidence:
    """Evidence from layout's reason codes; a heading without any is weak by default."""
    reasons = set(element.reasons)
    letters = [c for c in text if c.isalpha()]
    return _Evidence(
        larger_font=REASON_LARGER_FONT in reasons,
        numbered=REASON_NUMBERED in reasons,
        list_like=REASON_LIST_NUMBER in reasons,
        all_caps=len(letters) >= MIN_LETTERS and all(c.isupper() for c in letters),
        very_short=len(letters) < MIN_LETTERS,
        tagged=tagged_level(element) is not None,
    )


def _check(name: str, passed: bool, code: str) -> Check:
    return Check(name=name, passed=passed, severity="warning", code=None if passed else code)


def _fix(element: Element, raw_level: int, level: int) -> Fix:
    text = " ".join((element.text or "").split())
    if not text:
        return Fix(
            element_id=element.id,
            kind=Kind.HEADING,
            status=Status.ABSTAINED,
            reason="heading.empty_text",
            checks=[Check(name="heading.text", passed=False, code="heading.empty_text")],
            review="required",
        )
    evidence = _evidence(element, text)
    repaired = level != raw_level
    checks = [
        _check("heading.outline", not repaired, "heading.level_repaired"),
        _check("heading.evidence", evidence.strong, "heading.weak_evidence"),
        _check("heading.all_caps", not evidence.all_caps, "heading.all_caps"),
        _check("heading.length", not evidence.very_short, "heading.very_short"),
        _check("heading.list_like", not evidence.maybe_list_item, "heading.list_like"),
    ]
    sentence = not evidence.strong and sentence_like(text)
    checks.append(_check("heading.sentence", not sentence, "heading.sentence_like"))
    # Accepted either way, except a sentence-like line with weak evidence (a decision);
    # any other failed check is an optional note.
    return Fix(
        element_id=element.id,
        kind=Kind.HEADING,
        status=Status.NEEDS_REVIEW if sentence else Status.ACCEPTED,
        confidence=evidence.confidence(repaired),
        heading=HeadingFix(level=level, text=text),
        checks=checks,
        review="required"
        if sentence
        else "optional"
        if any(not c.passed for c in checks)
        else "none",
    )
