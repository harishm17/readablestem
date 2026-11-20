"""Review load: which fixes need a person's decision, and how many decisions that makes.

The pipeline gives every fix a review tier (`Fix.review`, "meaning-level only" policy of
2026-10-08): `required` items block "done" and are counted; `optional` items are
accepted automatically and listed as notes; `none` items need nothing. Repeated headings
with weak evidence share a `group_key` and make one decision together, so counting is in
*decisions* (units), not fixes.

`review_load` also says what people have done since, per decision (`ReviewLoad.actions`):
the one count of open, decided and never-opened items that the job page, the student
note, the record and the draft gate all read (SW7, accuracy decision A16.3). Nothing else
counts them.

Two things decide what a person is asked, and each has one place here. `tier_for` says
whether a fix is a required decision, an optional note or nothing; every counter and the
review queue call it. `SOURCES` is the one table of what can raise a required decision:
a fix names the sources that raised it (`Fix.sources`, the first one opened it), one cause is
one card (`add_source` puts a second source's name on the card that is already open), and a
name that is not in the table is refused. `risk_rank` puts a required decision in one of five
bands, and the review walks them in order (`web.queue`), stopping at the line `stopping_line`
says. The bands, highest risk first:

1. nothing usable was made for content a student cannot get another way (a formula, figure or
   table kept as it was, or a page kept as a picture);
2. what a student reads is or may be wrong (a failed check, a text doubt, an unproven
   decoration);
3. order or structure of a page (no source raises it yet);
4. the model's confidence stayed under the bar with no failed check;
5. repeated headings and other weak structure.

Everything here is content-free: the reason codes are check codes, `Fix.reason` codes or
the fixed words below, so the web app, the ops aggregates and the eval can all use them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from readablestem.core.types import (
    KEPT_ORIGINAL_STATUSES,
    DocumentResult,
    Fix,
    Kind,
    Review,
    Status,
)

# Reason for a required fix with no failed check and no Fix.reason: the model's own
# confidence stayed below the auto-accept bar (after escalation, for equations).
BELOW_ACCEPT = "below_accept"
# Reason for a member of a group of repeated headings (`Fix.group_key`).
REPEATED_LABEL = "heading.repeated_label"
# Code of the second automated check's note on a figure (`figure_step.second_check`, ADR
# 0005). Optional by policy in this version, but unlike the other notes it says the alt
# text may not match the image, so the review screen never calls it something small.
# The figure step writes it as a literal, so the message-table test can find the code;
# tests/test_web_second_check_review.py checks the two agree.
SECOND_CHECK_MISMATCH = "second_check_mismatch"


# --------------------------------------------------------------------------- sources

# A "doubt" flag is raised because a check or a reader disagreed, and counts in false flags per
# true flag. A "rule" flag is raised because a rule says a person must look whatever the readers
# say, and is counted on its own line (`web.events.SOURCE_CLASSES` is made from this table).
DOUBT = "doubt"
RULE = "rule"
PIPELINE = "pipeline"
# The risk bands of `risk_rank`, and the last one the review stops above.
BANDS = (1, 2, 3, 4, 5)
STOP_AFTER_BAND = 3


@dataclass(frozen=True)
class Source:
    """One source of required decisions: what raises it, and how its decisions count."""

    name: str
    flag: str  # DOUBT or RULE
    band: int | None  # the band of all its decisions, or None: `risk_rank` reads the fix
    reasons: frozenset[str]  # the codes it raises; empty: whatever the pipeline's checks say
    raised_by: str


# A row is never removed or renamed while a stored result names it: `Fix.sources` is checked
# against this table when a result is read, so a missing name would make an old result unreadable.
SOURCE_ROWS = (
    Source(
        PIPELINE,
        DOUBT,
        None,
        frozenset(),
        "the conversion's steps on equations, figures, tables and headings, and the reading "
        "of a scanned or handwritten page: a failed or abstained reading, a failed check, "
        "confidence under the bar, a repeated heading",
    ),
    Source(
        "text",
        DOUBT,
        2,
        frozenset({"text.rtl_order", "text.option_labels"}),
        "layout doubts a paragraph or list item: right-to-left words, or option labels "
        "(`pipeline.notices.text_decisions`)",
    ),
    Source(
        "source_read",
        RULE,
        None,
        frozenset({"source_figure_missing", "invalid_latex", "unknown_command"}),
        "a LaTeX or Word source: a picture with no file, a formula the reader could not read "
        "(`source.ltx_reader`, `source.docx_reader`)",
    ),
    Source(
        "decoration",
        RULE,
        2,
        frozenset({"decoration_unproven"}),
        "a figure called decorative that nothing proves is (`pipeline.decoration`)",
    ),
)
SOURCES: dict[str, Source] = {row.name: row for row in SOURCE_ROWS}


def sources_of(fix: Fix) -> list[str]:
    """The sources that raised this decision, the one that opened it first. A fix with none
    named is the pipeline's own flag (every result stored before sources existed)."""
    return list(fix.sources) or [PIPELINE]


def add_source(fix: Fix, name: str) -> None:
    """One cause, one card: a source that flags a fix already asked for adds its name to it
    and raises nothing new; on a fix that is not asked for yet it opens the decision. A name
    outside `SOURCES` is refused."""
    if name not in SOURCES:
        raise ValueError(f"not a source of decisions: {name}")
    if tier_for(fix) == "required":
        if name not in sources_of(fix):
            fix.sources = [*sources_of(fix), name]
    else:
        fix.sources = [name]
        fix.review = "required"


def source_class(name: str) -> str:
    """ "doubt" or "rule" for a source name; the pipeline's for a name not in the table."""
    return SOURCES[name if name in SOURCES else PIPELINE].flag


# --------------------------------------------------------------------------- tier and risk


def tier_for(fix: Fix) -> Review:
    """The tier of a fix, decided here and nowhere else: a fix is a required decision when the
    pipeline asked for one (`Fix.review`, which a review never changes) or when it is waiting
    for a person whatever its stored tier says (a result stored before tiers existed).
    Optional and none are what the pipeline stored."""
    if fix.review == "required" or fix.pending:
        return "required"
    return fix.review


def risk_rank(fix: Fix) -> int:
    """The risk band of a required decision, 1 (highest) to 5. The band fixed when the
    decision was asked (`Fix.band`, see `stamp`), else read from the fix as it is now."""
    if fix.band is not None:
        return fix.band
    row = SOURCES[sources_of(fix)[0]]
    if row.band is not None:
        return row.band
    if fix.kind is Kind.HEADING:
        return 5
    if (
        fix.kind in (Kind.EQUATION, Kind.FIGURE, Kind.TABLE)
        and fix.status in KEPT_ORIGINAL_STATUSES
    ):
        return 1
    return 4 if tier_reasons(fix) == [BELOW_ACCEPT] else 2


def stamp(fixes: Iterable[Fix]) -> None:
    """Fix the band of every required decision that has none, as the pipeline leaves it: a
    review changes the checks and the status the band is read from, and the review order must
    not move while a person works."""
    for fix in fixes:
        if fix.band is None and tier_for(fix) == "required":
            fix.band = risk_rank(fix)


@dataclass(frozen=True)
class StoppingLine:
    """Where the review can stop: the decisions of the bands above the line (1 to 3) still
    open, and those below it (4 and 5) open and in all."""

    above_open: int
    below_open: int
    below_total: int

    @property
    def reached(self) -> bool:
        """The decisions of the bands above the line are made, and some below it are left."""
        return self.above_open == 0 and self.below_open > 0


def stopping_line(fixes: Iterable[Fix]) -> StoppingLine:
    """Open decisions above and below the stopping line, each counted as one (a group of
    repeated headings is one). A decision's band is its first member's."""
    above = below = below_total = 0
    for unit in units(f for f in fixes if tier_for(f) == "required"):
        low = risk_rank(unit[0]) > STOP_AFTER_BAND
        below_total += low
        if any(f.pending for f in unit):
            below += low
            above += not low
    return StoppingLine(above, below, below_total)


def units(fixes: Iterable[Fix]) -> list[list[Fix]]:
    """Fixes as decisions: the members of one heading group together (in the order they
    come), every other fix on its own. Units keep the order of their first member."""
    out: list[list[Fix]] = []
    groups: dict[str, list[Fix]] = {}
    for fix in fixes:
        if fix.group_key:
            if fix.group_key not in groups:
                groups[fix.group_key] = []
                out.append(groups[fix.group_key])
            groups[fix.group_key].append(fix)
        else:
            out.append([fix])
    return out


def required_decisions(fixes: Iterable[Fix]) -> int:
    """Decisions the pipeline asked for (its tiers, whatever a reviewer did since)."""
    return len(units(f for f in fixes if tier_for(f) == "required"))


@dataclass(frozen=True)
class Decision:
    """One decision the pipeline asked for: where it is, what it is about and why."""

    page: int | None  # 0-based, the page of its first member; None when no element holds it
    kind: str
    reasons: tuple[str, ...]  # `tier_reasons` of its members, sorted, no repeats
    members: int = 1  # the fixes decided together (more than 1: a heading group)
    band: int = 5  # `risk_rank` of its first member
    sources: tuple[str, ...] = (PIPELINE,)  # who raised it, the opener first (`Fix.sources`)
    element: str | None = None  # the element id of its first member (the same upload, later)


def decisions(result: DocumentResult) -> list[Decision]:
    """The decisions `required_decisions` counts for a document, one row each, in order."""
    page_of = {element.id: element.page for element in result.elements}
    return [
        Decision(
            page=page_of.get(unit[0].element_id),
            kind=unit[0].kind.value,
            reasons=tuple(sorted({code for fix in unit for code in tier_reasons(fix)})),
            members=len(unit),
            band=risk_rank(unit[0]),
            sources=tuple(dict.fromkeys(name for fix in unit for name in sources_of(fix))),
            element=unit[0].element_id,
        )
        for unit in units(fix for fix in result.fixes if tier_for(fix) == "required")
    ]


def open_decisions(fixes: Iterable[Fix]) -> int:
    """Decisions still waiting now: a group counts while any member is waiting. The count
    `review_load` gives (`ReviewLoad.open_decisions`), so no two callers can differ."""
    return review_load(list(fixes)).open_decisions


def second_check_note(fix: Fix) -> bool:
    """An optional note nobody has acted on yet that comes from the second automated
    check: a possible mismatch between the alt text and the image."""
    return fix.optional_note and any(c.code == SECOND_CHECK_MISMATCH for c in fix.failed_checks)


def tier_reasons(fix: Fix) -> list[str]:
    """Why a fix has its tier, as content-free codes (sorted, no repeats).

    Required: its `Fix.reason` (failed, abstained), the group marker for a repeated
    heading, its failed checks, or `below_accept` when only the confidence held it back.
    Optional: its failed checks (the notes). None: nothing. Only a heading group is a
    repeated label: the pages kept as images with one reason ("pg-" groups) say so by
    their own reason.
    """
    tier = tier_for(fix)
    if tier == "none":
        return []
    codes = {c.code or c.name for c in fix.checks if not c.passed}
    if tier == "required":
        if fix.reason:
            codes.add(fix.reason)
        if fix.group_key and fix.kind is Kind.HEADING:
            codes.add(REPEATED_LABEL)
        if not codes:
            codes.add(BELOW_ACCEPT)
    return sorted(codes)


# What people did, per decision (`action_of`). A heading group is one decision.
OPEN = "open"  # still waiting for a person
ACCEPTED = "accepted"  # a person accepted the proposal, every check of it passing
ACCEPTED_FAILING = "accepted_failing_check"  # "Accept anyway": kept with a check failing
EDITED = "edited"  # a person changed the proposal
KEPT_IMAGE = "kept_as_image"  # a person kept the original picture
DECORATIVE = "marked_decorative"  # a person marked a figure as decoration
UNOPENED = "optional_unopened"  # accepted by the tool with a note nobody has opened
DECIDED_ACTIONS = (ACCEPTED, ACCEPTED_FAILING, EDITED, KEPT_IMAGE, DECORATIVE)
ACTIONS = (OPEN, *DECIDED_ACTIONS, UNOPENED)


def action_of(unit: Sequence[Fix]) -> str | None:
    """What happened to one decision: still `OPEN`, one of `DECIDED_ACTIONS` when a person
    decided it, `UNOPENED` for an optional note nobody acted on, or None for an item the
    tool accepted with nothing to note (nobody was asked, nobody looked)."""
    if any(f.pending for f in unit):
        return OPEN
    decided = [f for f in unit if f.decided_by_reviewer]
    if decided:
        fix = decided[0]
        if fix.kind is not Kind.HEADING and fix.status in KEPT_ORIGINAL_STATUSES:
            return KEPT_IMAGE
        if fix.figure is not None and fix.figure.decorative and fix.kind is Kind.FIGURE:
            return DECORATIVE
        if fix.status in (Status.EDITED, Status.REJECTED):  # a heading made bold text
            return EDITED
        return ACCEPTED_FAILING if any(f.failed_checks for f in decided) else ACCEPTED
    if any(f.optional_note for f in unit):
        return UNOPENED
    return None


@dataclass
class ReviewLoad:
    """Counts for one document (or many): what the pipeline asked a person to look at."""

    required_decisions: int = 0  # units: a heading group counts once
    required_fixes: int = 0
    optional: int = 0
    none: int = 0
    # Per kind (a fix's `Kind`: "equation", "figure", "heading", "table", and "paragraph" or
    # "list_item" for a page read from its picture or a text decision) and per reason code.
    required_by_kind: Counter[str] = field(default_factory=Counter)  # decisions
    optional_by_kind: Counter[str] = field(default_factory=Counter)
    required_by_reason: Counter[str] = field(default_factory=Counter)  # fixes
    # Required decisions by risk band ("1" to "5"), by the source that opened them and by the
    # class of that source ("doubt" or "rule", counted apart: decision A16.3).
    required_by_band: Counter[str] = field(default_factory=Counter)
    required_by_source: Counter[str] = field(default_factory=Counter)
    required_by_class: Counter[str] = field(default_factory=Counter)
    optional_by_reason: Counter[str] = field(default_factory=Counter)
    # What people did since, per decision (`ACTIONS`), over every fix that can wait for a
    # person, of any kind (as the tier counts above are). Not in `as_dict`: the eval reports
    # what the pipeline asked for.
    actions: Counter[str] = field(default_factory=Counter)

    @property
    def open_decisions(self) -> int:
        """Decisions still waiting for a person: what makes a file a draft."""
        return self.actions[OPEN]

    @property
    def decided(self) -> int:
        """Decisions a person made."""
        return sum(self.actions[a] for a in DECIDED_ACTIONS)

    @property
    def unopened(self) -> int:
        """Optional notes nobody opened: never counted as reviewed."""
        return self.actions[UNOPENED]

    @property
    def flagged(self) -> int:
        """Decisions the tool flagged (asked for, or noted) or a person made."""
        return sum(self.actions.values())

    def add(self, other: ReviewLoad) -> None:
        self.required_decisions += other.required_decisions
        self.required_fixes += other.required_fixes
        self.optional += other.optional
        self.none += other.none
        self.required_by_kind.update(other.required_by_kind)
        self.optional_by_kind.update(other.optional_by_kind)
        self.required_by_reason.update(other.required_by_reason)
        self.required_by_band.update(other.required_by_band)
        self.required_by_source.update(other.required_by_source)
        self.required_by_class.update(other.required_by_class)
        self.optional_by_reason.update(other.optional_by_reason)
        self.actions.update(other.actions)

    def as_dict(self) -> dict:
        return {
            "required_decisions": self.required_decisions,
            "required_fixes": self.required_fixes,
            "optional": self.optional,
            "none": self.none,
            "required_by_kind": dict(sorted(self.required_by_kind.items())),
            "optional_by_kind": dict(sorted(self.optional_by_kind.items())),
            "required_by_reason": dict(sorted(self.required_by_reason.items())),
            "required_by_band": dict(sorted(self.required_by_band.items())),
            "required_by_source": dict(sorted(self.required_by_source.items())),
            "required_by_class": dict(sorted(self.required_by_class.items())),
            "optional_by_reason": dict(sorted(self.optional_by_reason.items())),
        }


def review_load(fixes: Sequence[Fix]) -> ReviewLoad:
    """The pipeline's tiers for these fixes, whatever their kind: its `required_decisions`
    is `required_decisions(fixes)`, the number the review screen counts. And what people did
    since (`ReviewLoad.actions`)."""
    load = ReviewLoad()
    for unit in units(f for f in fixes if tier_for(f) == "required"):
        opener = sources_of(unit[0])[0]
        load.required_decisions += 1
        load.required_by_kind[unit[0].kind.value] += 1
        load.required_by_band[str(risk_rank(unit[0]))] += 1
        load.required_by_source[opener] += 1
        load.required_by_class[source_class(opener)] += 1
    for fix in fixes:
        tier = tier_for(fix)
        if tier == "required":
            load.required_fixes += 1
            load.required_by_reason.update(tier_reasons(fix))
        elif tier == "optional":
            load.optional += 1
            load.optional_by_kind[fix.kind.value] += 1
            load.optional_by_reason.update(tier_reasons(fix))
        else:
            load.none += 1
    for unit in units(fixes):
        if (action := action_of(unit)) is not None:
            load.actions[action] += 1
    return load
