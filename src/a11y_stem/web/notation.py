"""Course notation in the web app: courses, their entries, and what the upload form, the
worker and the review screen need from them.

Only with `FEATURE_NOTATION_MEMORY` on (`enabled`); otherwise the courses pages answer
404 and nothing here is called. A course belongs to one user (`courses.user_id`), and
every lookup goes through `owned_course`, so another user's course is "not found".
Courses and entries are user content: course names, symbols and notes never go into
logs or events (ids and counts only), and they are erased with the user
(`ops.retention.erase_user`).

- Entries are typed on the course page in any spelling `core.notation.canonical_symbol`
  understands ("bold d", `\\mathbf{d}`, the styled letter, "double-struck R"); a symbol it
  does not understand is refused with examples of what works. One entry per symbol.
- The worker loads the job's course notation into the run's `ConversionOptions`
  (`conversion_options`); the pipeline reads it (`pipeline.notation_memory`).
- The review screen shows the entries that concern the equation on screen
  (`card_notation`) and, after a decision that settled a styled letter, offers to
  remember it for the course (`remember_offer`, source "review").
"""

from __future__ import annotations

import unicodedata
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.config import Settings
from a11y_stem.core.mathchars import STYLE_GROUPS, base_char, char_variant
from a11y_stem.core.notation import MAX_SYMBOL_CHARS, canonical_from_parts, canonical_symbol
from a11y_stem.core.types import (
    ConversionOptions,
    DocumentResult,
    Element,
    Fix,
    Kind,
    NotationEntry,
    Status,
)
from a11y_stem.pipeline import notation_memory
from a11y_stem.web.db import Course, CourseNotation, Job, ReviewEdit

MAX_NAME_CHARS = 200  # courses.name
MAX_NOTE_CHARS = 200  # notation_entries.note
MAX_COURSES = 50  # per user
MAX_ENTRIES = 200  # per course

# The meanings an entry can have (`core.types.NotationMeaning`), as the form lists them.
MEANINGS: dict[str, str] = {
    "vector": "A vector",
    "matrix": "A matrix",
    "set": "A set",
    "operator": "An operator",
    "constant": "A constant",
    "unit": "A unit",
    "other": "Something else",
}
SYMBOL_EXAMPLES = (
    "“bold d”, “\\mathbf{d}”, “\U0001d41d”, “bold omega”, “double-struck R” or “v with an arrow”"
)
# What a decision that settled a style suggests the letter means.
_REMEMBERED_MEANING = {"bold": "vector", "double-struck": "set"}


def enabled(settings: Settings) -> bool:
    return settings.feature_notation_memory


# --------------------------------------------------------------------------- errors


@dataclass(frozen=True)
class FieldError:
    field_id: str  # the form control the error summary links to
    message: str


class NotationInvalid(Exception):
    """A course or entry form that was not saved. Messages never repeat what was typed."""

    def __init__(self, errors: list[FieldError], values: dict[str, str] | None = None) -> None:
        super().__init__("; ".join(e.message for e in errors))
        self.errors = errors
        self.values = values or {}


# --------------------------------------------------------------------------- views


@dataclass(frozen=True)
class CourseView:
    id: str
    name: str
    entries: int


@dataclass(frozen=True)
class EntryView:
    id: str
    symbol: str  # as the instructor wrote it
    canonical: str  # the styled letter itself
    words: str  # "bold d"
    latex: str | None
    meaning: str
    meaning_label: str
    note: str | None
    source: str  # "user" or "review"

    @property
    def phrase(self) -> str:
        return notation_memory.phrase(self.canonical, self.meaning)


def _entry_view(row: CourseNotation) -> EntryView:
    return EntryView(
        id=row.id,
        symbol=row.symbol,
        canonical=row.canonical,
        words=notation_memory.describe(row.canonical),
        latex=notation_memory.latex_of(row.canonical),
        meaning=row.meaning,
        meaning_label=MEANINGS.get(row.meaning, row.meaning),
        note=row.note,
        source=row.source,
    )


def owned_course(session: Session, course_id: str, user_id: str) -> Course | None:
    """The user's course, or None (also for someone else's: ids cannot be probed)."""
    course = session.get(Course, course_id)
    return course if course is not None and course.user_id == user_id else None


def list_courses(session: Session, user_id: str) -> list[CourseView]:
    """The user's courses, by name, with how many entries each has."""
    counts = (
        select(CourseNotation.course_id, func.count().label("n"))
        .group_by(CourseNotation.course_id)
        .subquery()
    )
    rows = session.execute(
        select(Course.id, Course.name, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.course_id == Course.id)
        .where(Course.user_id == user_id)
        .order_by(func.lower(Course.name), Course.created_at)
    ).all()
    return [CourseView(id=r[0], name=r[1], entries=int(r[2])) for r in rows]


def entries(course: Course) -> list[EntryView]:
    """A course's entries in the order they read best: by letter, then by style."""
    rows = sorted(course.notation, key=lambda r: (_sort_key(r.canonical), r.created_at))
    return [_entry_view(r) for r in rows]


def _sort_key(canonical: str) -> tuple[str, str]:
    first = unicodedata.normalize("NFD", canonical)[:1]
    return base_char(unicodedata.normalize("NFC", first)).casefold(), canonical


# --------------------------------------------------------------------------- validation


def clean_name(name: str, *, field_id: str = "name") -> str:
    """A course name: one line, 1 to MAX_NAME_CHARS characters."""
    cleaned = " ".join("".join(ch for ch in name if unicodedata.category(ch)[0] != "C").split())
    if not cleaned:
        raise NotationInvalid([FieldError(field_id, "Enter a name for the course.")])
    if len(cleaned) > MAX_NAME_CHARS:
        message = f"Keep the course name to {MAX_NAME_CHARS} characters or fewer."
        raise NotationInvalid([FieldError(field_id, message)], {field_id: cleaned})
    return cleaned


@dataclass(frozen=True)
class EntryInput:
    symbol: str
    canonical: str
    meaning: str
    note: str | None


def parse_entry(symbol: str, meaning: str, note: str) -> EntryInput:
    """Check an entry form. Raises NotationInvalid with every problem at once."""
    symbol = " ".join(symbol.split())
    note_text = " ".join(note.split())
    values = {"symbol": symbol, "meaning": meaning, "note": note_text}
    errors: list[FieldError] = []
    canonical = None
    if not symbol:
        errors.append(FieldError("symbol", f"Enter a symbol, for example {SYMBOL_EXAMPLES}."))
    elif len(symbol) > MAX_SYMBOL_CHARS:
        message = f"Keep the symbol to {MAX_SYMBOL_CHARS} characters or fewer."
        errors.append(FieldError("symbol", message))
    else:
        canonical = canonical_symbol(symbol)
        if canonical is None:
            message = (
                "This tool cannot tell which symbol that is. Write one symbol another way, "
                "for example “bold d” or “\\mathbf{d}”."
            )
            errors.append(FieldError("symbol", message))
    if meaning not in MEANINGS:
        errors.append(FieldError("meaning", "Choose what the symbol means."))
    if len(note_text) > MAX_NOTE_CHARS:
        message = f"Keep the note to {MAX_NOTE_CHARS} characters or fewer."
        errors.append(FieldError("note", message))
    if errors or canonical is None:
        raise NotationInvalid(errors, values)
    return EntryInput(symbol, canonical, meaning, note_text or None)


# --------------------------------------------------------------------------- writes


def create_course(session: Session, user_id: str, name: str) -> Course:
    name = clean_name(name)
    count = session.scalar(select(func.count()).where(Course.user_id == user_id)) or 0
    if count >= MAX_COURSES:
        message = f"You can keep up to {MAX_COURSES} courses. Delete one you no longer use first."
        raise NotationInvalid([FieldError("name", message)], {"name": name})
    _refuse_same_name(session, user_id, name, field_id="name")
    course = Course(user_id=user_id, name=name)
    session.add(course)
    session.flush()
    return course


def rename_course(session: Session, course: Course, name: str) -> None:
    name = clean_name(name, field_id="rename")
    _refuse_same_name(session, course.user_id, name, field_id="rename", other_than=course.id)
    course.name = name


def _refuse_same_name(
    session: Session, user_id: str, name: str, *, field_id: str, other_than: str | None = None
) -> None:
    query = select(Course.id).where(
        Course.user_id == user_id, func.lower(Course.name) == name.lower()
    )
    if other_than is not None:
        query = query.where(Course.id != other_than)
    if session.scalar(query.limit(1)) is not None:
        message = "You already have a course with this name. Choose another name."
        raise NotationInvalid([FieldError(field_id, message)], {field_id: name})


def delete_course(session: Session, course: Course) -> None:
    """The course and its entries. Jobs that used it keep their results and lose only
    the link (as the foreign key's ON DELETE SET NULL does, done here explicitly)."""
    session.execute(update(Job).where(Job.course_id == course.id).values(course_id=None))
    session.execute(delete(CourseNotation).where(CourseNotation.course_id == course.id))
    session.delete(course)


def _existing(
    course: Course, canonical: str, other_than: str | None = None
) -> CourseNotation | None:
    return next(
        (r for r in course.notation if r.canonical == canonical and r.id != other_than), None
    )


def add_entry(session: Session, course: Course, entry: EntryInput, *, source: str = "user") -> None:
    if len(course.notation) >= MAX_ENTRIES:
        message = f"A course can keep up to {MAX_ENTRIES} symbols. Remove one first."
        raise NotationInvalid([FieldError("symbol", message)], _values(entry))
    same = _existing(course, entry.canonical)
    if same is not None:
        message = (
            f"This course already has {notation_memory.describe(entry.canonical)}. "
            "Edit that entry instead."
        )
        raise NotationInvalid([FieldError("symbol", message)], _values(entry))
    session.add(
        CourseNotation(
            course_id=course.id,
            symbol=entry.symbol,
            canonical=entry.canonical,
            meaning=entry.meaning,
            note=entry.note,
            source=source,
        )
    )
    session.flush()


def update_entry(session: Session, course: Course, row: CourseNotation, entry: EntryInput) -> None:
    if _existing(course, entry.canonical, other_than=row.id) is not None:
        message = (
            f"This course already has {notation_memory.describe(entry.canonical)} in another "
            "entry. Edit or remove that one instead."
        )
        raise NotationInvalid([FieldError("symbol", message)], _values(entry))
    row.symbol, row.canonical = entry.symbol, entry.canonical
    row.meaning, row.note = entry.meaning, entry.note
    session.flush()


def _values(entry: EntryInput) -> dict[str, str]:
    return {"symbol": entry.symbol, "meaning": entry.meaning, "note": entry.note or ""}


# --------------------------------------------------------------------------- conversion


def notation_entries(rows: Iterable[CourseNotation]) -> list[NotationEntry]:
    """Stored rows as the pipeline's entries (rows that no longer validate are skipped)."""
    out = []
    for row in rows:
        values = {
            "symbol": row.symbol,
            "canonical": row.canonical,
            "meaning": row.meaning,
            "note": row.note,
            "source": row.source,
        }
        try:
            out.append(NotationEntry.model_validate(values))
        except ValueError:
            continue
    return out


def conversion_options(
    factory: sessionmaker[Session], settings: Settings, job_id: str
) -> ConversionOptions | None:
    """The job's run options with its course notation; None when the feature is off or
    the job has no course with entries (the run then uses the settings' flags)."""
    if not enabled(settings):
        return None
    from a11y_stem.web.db import session_scope

    with session_scope(factory) as session:
        job = session.get(Job, job_id)
        if job is None or job.course_id is None:
            return None
        course = owned_course(session, job.course_id, job.user_id)
        if course is None:
            return None
        found = notation_entries(course.notation)
    if not found:
        return None
    return ConversionOptions.from_settings(settings).model_copy(update={"notation": found})


@contextmanager
def in_force(factory: sessionmaker[Session], settings: Settings, job_id: str) -> Generator[None]:
    """The job's course notation in force (`pipeline.options`) for checks run outside a
    conversion: a reviewer's equation edit is checked with the same course evidence as
    the model's proposal was (`web.edit_check`). Nothing changes without one."""
    found = conversion_options(factory, settings, job_id)
    if found is None:
        yield
        return
    from a11y_stem.pipeline import options

    with options.using(found):
        yield


def job_course(session: Session, job: Job) -> Course | None:
    """The course a job was converted for (only one of its owner's)."""
    if job.course_id is None:
        return None
    return owned_course(session, job.course_id, job.user_id)


# --------------------------------------------------------------------------- review


@dataclass(frozen=True)
class RememberOffer:
    """ "Remember for Physics 1: bold ω is a vector" after a decision."""

    course_id: str
    course_name: str
    symbol: str  # the notation key
    meaning: str

    @property
    def phrase(self) -> str:
        return notation_memory.phrase(self.symbol, self.meaning)


@dataclass
class CardNotation:
    """What the course notation says about the equation on screen."""

    course_name: str
    lines: list[str] = field(default_factory=list)  # "bold d is a vector"
    # Letters the course writes bold that this PDF's text cannot show bold, plain in the
    # proposal: why the style check asked (`equation_step._course_bold_unseen`).
    unseen: list[str] = field(default_factory=list)


def _bold_and_styled(mathml: str | None) -> dict[str, str]:
    """Letters a MathML proposal uses as a bold or double-struck variable, by key."""
    if not mathml:
        return {}
    from a11y_stem.pipeline.equation_step import _mathml_letters

    try:
        letters = _mathml_letters(mathml)
    except Exception:  # a stored proposal that no longer parses: nothing to offer
        return {}
    out = {}
    for letter, found in letters.items():
        if not letter.isalpha():
            continue
        if found.var_bold:
            key = canonical_from_parts(letter, "bold")
            if key is not None:
                out[key] = "bold"
        if found.styled["double-struck"]:
            key = canonical_from_parts(letter, "double-struck")
            if key is not None:
                out[key] = "double-struck"
    return out


def settled_symbols(element: Element | None, fix: Fix, edit: ReviewEdit | None) -> list[str]:
    """Styled letters a reviewer's decision settled, as notation keys.

    An edit: the bold or double-struck letters of the new LaTeX that the proposal before
    it did not have (the review screen's "Use bold ω" is such an edit). An accept: the
    bold or double-struck letters of a proposal the style check asked about
    (`style_mismatch`) that the PDF's text layer does not show in that style."""
    if fix.kind is not Kind.EQUATION or fix.equation is None or edit is None:
        return []
    after = _bold_and_styled(fix.equation.mathml)
    if edit.action == "edit" and fix.status is Status.EDITED:
        before_eq = (edit.before or {}).get("equation") or {}
        before = _bold_and_styled(before_eq.get("mathml") if isinstance(before_eq, dict) else None)
        return [key for key in after if key not in before]
    if edit.action == "accept" and fix.status is Status.ACCEPTED:
        asked = any(not c.passed and c.code == "style_mismatch" for c in fix.checks)
        if not asked:
            return []
        layer = element.text if element is not None else ""
        shown = {
            canonical_from_parts(base_char(ch), char_variant(ch))
            for ch in layer or ""
            if char_variant(ch) is not None
        }
        return [key for key in after if key not in shown]
    return []


def remember_offer(
    session: Session, settings: Settings, job: Job, result: DocumentResult, element_id: str
) -> RememberOffer | None:
    """After a decision on `element_id`: the first styled letter it settled that the
    job's course does not have yet, as an offer to remember it; else None."""
    if not enabled(settings):
        return None
    course = job_course(session, job)
    fix = result.fix_for(element_id)
    if course is None or fix is None:
        return None
    edit = session.scalar(
        select(ReviewEdit)
        .where(ReviewEdit.job_id == job.id, ReviewEdit.element_id == element_id)
        .order_by(ReviewEdit.created_at.desc(), ReviewEdit.id.desc())
        .limit(1)
    )
    element = next((e for e in result.elements if e.id == element_id), None)
    known = {r.canonical for r in course.notation}
    for key in settled_symbols(element, fix, edit):
        if key in known or len(course.notation) >= MAX_ENTRIES:
            continue
        group = STYLE_GROUPS.get(char_variant(key) or "", "")
        meaning = _REMEMBERED_MEANING.get(group)
        if meaning is not None:
            return RememberOffer(course.id, course.name, key, meaning)
    return None


def card_notation(
    session: Session, settings: Settings, job: Job, element: Element | None, fix: Fix | None
) -> CardNotation | None:
    """The course entries about the letters of an equation (in its PDF text or its
    proposal), for its review card; None without a course, entries or a match."""
    if not enabled(settings) or fix is None or fix.kind is not Kind.EQUATION:
        return None
    course = job_course(session, job)
    if course is None or not course.notation:
        return None
    from a11y_stem.pipeline.equation_step import (
        _course_bold_unseen,
        _layer_letters,
        _mathml_letters,
    )

    text = (element.text if element is not None else None) or ""
    try:
        proposal = _mathml_letters(fix.equation.mathml) if fix.equation else {}
    except Exception:  # a stored proposal that no longer parses
        proposal = {}
    letters = {base_char(ch) for ch in text if ch.isalpha()} | set(proposal)
    card = CardNotation(course.name)
    card.lines = [
        view.phrase
        for view in entries(course)
        if notation_memory.base_letter(view.canonical) in letters
    ]
    if not card.lines:
        return None
    if text:
        layer = _layer_letters(text, (element.regular_letters if element else None) or "")
        course_bold = notation_memory.course_bold_letters(notation_entries(course.notation))
        card.unseen = sorted(
            letter
            for letter in _course_bold_unseen(layer, course_bold)
            if letter in proposal
            and proposal[letter].var_plain
            and not (proposal[letter].var_bold or proposal[letter].other_bold)
        )
    return card
