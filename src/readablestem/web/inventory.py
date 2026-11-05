r"""The symbols of a document's handwritten equations, and changing one everywhere (H11).

A writer's notation is consistent, so a misread letter is usually misread the same way every
time: an `x` read as `X`, a `p` read as `\rho`. Fixing it equation by equation is slow, and
the instructor cannot see the pattern. The Symbols screen (`routes/inventory.py`) lists every
distinct letter and symbol the tool read in the handwritten equations, grouped with the ones
that look alike (`FAMILIES`), each with how many times it stands, in how many equations, and
where. One form per symbol changes it to another symbol of its family in every place at once:
each place has a checkbox, ticked until the reviewer unticks it to leave that place as it is, so
the form says what will change, not what to leave out. The form posts the ticked places
(`included`); `excluded` is the same choice the other way round, for a script.

What a change is:

- It edits the proposal of each equation that still waits for a decision; it never accepts
  one. Each equation stays a decision of its own (handwritten maths is always checked by a
  person, ADR 0007), with the checks that made it so (`image_page.*`) kept next to the new
  checks of the changed LaTeX.
- Each equation is checked on its own, as an ordinary edit is (`web.edit_check`: the LaTeX
  must convert, and must not differ from the PDF's text where that changes the meaning). One
  that does not pass is left as it was and counted, and the others are changed.
- It is one write: one `ReviewEdit` per changed equation (what Undo puts back), all marked
  `INVENTORY_KEY`, which the review record shows as one row (`web.record`), and one Undo.
- Equations already decided (accepted, edited, kept as an image) are listed but never
  changed: the decision was about the reading they had.

Which equations: display and inline equation items read from an image page (`IMAGE_PAGE_REASON`)
whose page is not measured as a scan (`PageType.kind` "scan_*": printed, not handwritten).
Typed documents have no such items, and their symbols are not inventoried: a typed
equation's letters come from the PDF's own text layer and are not read by a model, so there
is nothing to re-letter. Inline maths inside a paragraph read from a page image is part of
that paragraph's text and is not listed. A symbol is found in the proposal's LaTeX, by token:
a letter, a Greek or other symbol command, a digit or a bar that can be taken for a letter.
Words inside `\text{}` and similar commands are not symbols.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

from readablestem.core.types import (
    IMAGE_PAGE_REASON,
    Check,
    DocumentResult,
    Element,
    EquationFix,
    Fix,
    Kind,
)
from readablestem.web.review import (
    INVENTORY_KEY,
    RELABEL_ACTION,
    FieldError,
    ReviewChange,
    ReviewInvalid,
    item_labels,
    item_numbers,
    reading_order,
    snapshot,
)

# Symbols that look alike when handwritten, one family each. Case, Greek against Latin, a
# digit against a letter, a bar against an l. The order is the order the screen lists them.
FAMILIES: Final[tuple[tuple[str, ...], ...]] = (
    ("x", "X", "\\times", "\\chi"),
    ("p", "P", "\\rho", "\\varrho"),
    ("l", "1", "I", "|", "\\ell", "\\mid", "\\vert"),
    ("z", "Z", "2", "\\zeta"),
    ("u", "U", "\\mu", "\\cup"),
    ("v", "V", "\\nu", "\\upsilon", "\\vee"),
    ("w", "W", "\\omega", "\\varpi"),
    ("o", "O", "0", "\\circ", "\\omicron"),
    ("s", "S", "5", "\\sigma", "\\varsigma"),
    ("c", "C"),
    ("k", "K", "\\kappa"),
    ("a", "A", "\\alpha"),
    ("b", "B", "6", "\\beta"),
    ("e", "E", "\\epsilon", "\\varepsilon"),
    ("g", "q", "9", "\\gamma"),
    ("t", "T", "\\tau", "\\Gamma"),
    ("y", "Y", "\\psi"),
    ("f", "F", "\\phi", "\\varphi"),
    ("d", "D", "\\partial", "\\delta"),
    ("n", "N", "h", "H", "\\eta"),
    ("m", "M"),
    ("r", "R"),
    ("i", "j", "J", "\\iota"),
    ("\\theta", "\\vartheta", "\\Theta"),
)
GREEK = frozenset(
    [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "omicron",
        "pi",
        "varpi",
        "rho",
        "varrho",
        "sigma",
        "varsigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
    ]
)
# Commands that are symbols although they are not letters.
OTHER_SYMBOLS = frozenset({"partial", "ell", "times", "mid", "vert", "circ", "cup", "vee"})
# Commands that are not Greek letters either, for the words that name them.
NOT_GREEK = OTHER_SYMBOLS
# Commands whose braces hold words and names, not symbols.
PROSE = frozenset(
    {"text", "textrm", "textit", "textbf", "mbox", "operatorname", "label", "tag", "ref", "color"}
    | {"hspace", "vspace", "begin", "end"}
)
_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\S", re.DOTALL)
_ASCII_LETTER = re.compile(r"[A-Za-z]")
FAMILY_OF: Final[dict[str, int]] = {key: i for i, family in enumerate(FAMILIES) for key in family}
IN_A_FAMILY = frozenset(FAMILY_OF)
CONTEXT_CHARS = 18  # chars of LaTeX shown on each side of a symbol
OTHER_NAME = "Other symbols"


# --------------------------------------------------------------------------- finding symbols


@dataclass(frozen=True)
class Span:
    key: str  # the symbol's LaTeX: "x", "\rho", "|"
    start: int
    end: int
    nth: int  # the nth time this key stands in this LaTeX (1-based)


def _is_symbol(token: str) -> bool:
    if token.startswith("\\"):
        return token[1:] in GREEK or token[1:] in OTHER_SYMBOLS
    if len(token) != 1:
        return False
    return bool(_ASCII_LETTER.fullmatch(token)) or token == "|" or token in IN_A_FAMILY


def symbol_spans(latex: str) -> list[Span]:
    """The symbols of `latex` in order, each with where it is and which time it stands."""
    tokens = [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(latex)]
    found: list[Span] = []
    seen: dict[str, int] = {}
    i = 0
    while i < len(tokens):
        token, start, end = tokens[i]
        if token.startswith("\\") and token[1:] in PROSE:
            i = _skip_groups(tokens, i + 1, 2 if token == "\\begin" else 1)
            continue
        if _is_symbol(token):
            seen[token] = seen.get(token, 0) + 1
            found.append(Span(token, start, end, seen[token]))
        i += 1
    return found


def _skip_groups(tokens: list[tuple[str, int, int]], at: int, groups: int) -> int:
    """The index after `groups` brace groups starting at `at` (fewer when there are fewer)."""
    for _ in range(groups):
        if at >= len(tokens) or tokens[at][0] != "{":
            return at
        depth = 0
        while at < len(tokens):
            depth += {"{": 1, "}": -1}.get(tokens[at][0], 0)
            at += 1
            if depth == 0:
                break
    return at


def relettered(latex: str, source: str, target: str, nths: set[int]) -> str:
    """`latex` with the `nths` times `source` stands written as `target` instead. A command
    that is followed by a letter gets a space (`\rho x`, not `\rhox`)."""
    out = latex
    for span in reversed(symbol_spans(latex)):
        if span.key != source or span.nth not in nths:
            continue
        new = target
        follows = out[span.end : span.end + 1]
        if re.fullmatch(r"\\[A-Za-z]+", target) and _ASCII_LETTER.fullmatch(follows or " "):
            new += " "
        out = out[: span.start] + new + out[span.end :]
    return out


# --------------------------------------------------------------------------- names


@lru_cache(maxsize=512)
def _rendered(key: str) -> tuple[str, str]:
    """(the symbol as a character, how it is said) for `key`, from the product's own
    LaTeX conversion and speech: the glyph of `\\rho` and "rho"."""
    from readablestem.pipeline import equation_step

    mathml, _ = equation_step._convert(key, "inline")
    if mathml is None:
        return key, key
    glyph = re.sub(r"<[^>]+>", "", mathml).strip() or key
    return glyph, equation_step._speech(mathml)[0] or glyph


def name_of(key: str) -> str:
    """What the symbol is, in the words a person listening would use: "capital X", "small x",
    "Greek rho", "the digit 1", "the times sign"."""
    _, said = _rendered(key)
    if key.startswith("\\"):
        if key[1:] in NOT_GREEK:
            return said if key in {"\\ell", "\\partial"} else f"the {said} sign"
        return f"Greek {said}"
    if key.isdigit():
        return f"the digit {key}"
    if key.isalpha():
        return said if said.lower().startswith("capital") else f"small {key}"
    return said


@dataclass(frozen=True)
class Choice:
    key: str  # LaTeX, posted back
    name: str
    glyph: str


def choice_of(key: str) -> Choice:
    return Choice(key=key, name=name_of(key), glyph=_rendered(key)[0])


# --------------------------------------------------------------------------- the inventory


@dataclass(frozen=True)
class Place:
    """One time a symbol stands in one equation."""

    element_id: str
    label: str  # "Equation 3"
    page: int  # 1-based
    nth: int  # the nth time this symbol stands in this equation
    times: int  # ... of this many
    before: str  # the LaTeX just before it and just after it, to show where
    after: str
    changeable: bool  # the equation still waits for a decision
    crop: bool  # a picture of the equation is kept

    @property
    def value(self) -> str:
        """What the form posts to leave this place out."""
        return f"{self.element_id}:{self.nth}"

    @property
    def which(self) -> str:
        return f"the {_ordinal(self.nth)} one in it" if self.times > 1 else ""


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass(frozen=True)
class Row:
    """One symbol and every place it stands."""

    key: str
    name: str
    glyph: str
    places: list[Place]

    @property
    def times(self) -> int:
        return len(self.places)

    @property
    def equations(self) -> int:
        return len({p.element_id for p in self.places})

    @property
    def changeable(self) -> int:
        return sum(p.changeable for p in self.places)

    @property
    def element_id(self) -> str:
        """For ids in the page."""
        return "sym-" + "".join(c if c.isalnum() else f"_{ord(c):x}" for c in self.key)


@dataclass(frozen=True)
class Family:
    name: str
    rows: list[Row]
    members: list[Choice]  # every symbol of the family, also those not read here

    @property
    def mixed(self) -> bool:
        """More than one of the family's symbols was read: the likeliest place for a mistake."""
        return len(self.rows) > 1

    def targets(self, key: str) -> list[Choice]:
        return [m for m in self.members if m.key != key]


@dataclass(frozen=True)
class Inventory:
    families: list[Family] = field(default_factory=list)
    equations: int = 0
    waiting: int = 0  # equations that still wait for a decision

    @property
    def symbols(self) -> int:
        return sum(len(f.rows) for f in self.families)

    @property
    def places(self) -> int:
        return sum(r.times for f in self.families for r in f.rows)

    def row(self, key: str) -> tuple[Family, Row] | None:
        for family in self.families:
            for row in family.rows:
                if row.key == key:
                    return family, row
        return None


def handwritten_equations(result: DocumentResult) -> list[tuple[Element, Fix]]:
    """The equation items read from an image page that have a proposal, in the review's order,
    except those on a page measured as a scan (printed, not handwritten)."""
    scans = {p.page for p in result.page_types if p.kind.startswith("scan")}
    elements = {e.id: e for e in result.elements}
    fixes = [
        f
        for f in result.fixes
        if f.kind is Kind.EQUATION
        and f.equation is not None
        and (e := elements.get(f.element_id)) is not None
        and IMAGE_PAGE_REASON in e.reasons
        and e.page not in scans
    ]
    return [(elements[f.element_id], f) for f in reading_order(result, fixes)]


def handwritten_count(result: DocumentResult) -> int:
    """How many handwritten equations the Symbols screen would list: the review screen links to
    it when this is not 0."""
    return len(handwritten_equations(result))


def _context(latex: str, span: Span) -> tuple[str, str]:
    before = latex[max(0, span.start - CONTEXT_CHARS) : span.start].lstrip()
    after = latex[span.end : span.end + CONTEXT_CHARS].rstrip()
    lead = "…" if span.start - CONTEXT_CHARS > 0 else ""
    tail = "…" if span.end + CONTEXT_CHARS < len(latex) else ""
    return lead + before, after + tail


def _family_name(family: tuple[str, ...]) -> str:
    return ", ".join(_rendered(k)[0] for k in family)


def inventory(result: DocumentResult, crop_ids: set[str]) -> Inventory:
    """Every symbol of the handwritten equations, grouped with the ones that look alike:
    the families with more than one symbol read first, then the others, then the symbols in
    no family. Within a family, the order of the family's own list."""
    equations = handwritten_equations(result)
    numbers = item_numbers(result)
    labels = item_labels(result)
    by_key: dict[str, list[Place]] = {}
    for element, fix in equations:
        assert fix.equation is not None
        latex = fix.equation.latex
        spans = symbol_spans(latex)
        counts: dict[str, int] = {}
        for s in spans:
            counts[s.key] = counts.get(s.key, 0) + 1
        for s in spans:
            before, after = _context(latex, s)
            label = labels.get(element.id) or f"Equation {numbers.get(element.id, '')}".strip()
            by_key.setdefault(s.key, []).append(
                Place(
                    element_id=element.id,
                    label=label,
                    page=element.page + 1,
                    nth=s.nth,
                    times=counts[s.key],
                    before=before,
                    after=after,
                    changeable=fix.pending,
                    crop=element.id in crop_ids,
                )
            )
    families: list[Family] = []
    for family in FAMILIES:
        rows = [_row(k, by_key[k]) for k in family if k in by_key]
        if rows:
            families.append(Family(_family_name(family), rows, [choice_of(k) for k in family]))
    others = [_row(k, places) for k, places in by_key.items() if k not in IN_A_FAMILY]
    families.sort(key=lambda f: not f.mixed)  # stable: the families' own order within each
    if others:
        families.append(Family(OTHER_NAME, others, []))
    return Inventory(families, len(equations), sum(f.pending for _, f in equations))


def _row(key: str, places: list[Place]) -> Row:
    glyph, _ = _rendered(key)
    return Row(key=key, name=name_of(key), glyph=glyph, places=places)


# --------------------------------------------------------------------------- changing one


@dataclass
class Relettering:
    changes: list[ReviewChange]
    left: list[str]  # element ids that did not pass their checks and stay as they were
    source: str = ""
    target: str = ""


def excluded_places(values: list[str]) -> set[tuple[str, int]]:
    """The places a form names ("p1-e2:1"), left out (`exclude`) or ticked (`include`):
    well-formed ones only."""
    out: set[tuple[str, int]] = set()
    for value in values:
        element_id, _, nth = value.rpartition(":")
        if element_id and nth.isdecimal() and 0 < int(nth) < 10_000:
            out.add((element_id, int(nth)))
    return out


def _kept_checks(old: list[Check], new: list[Check]) -> list[Check]:
    """The checks of the changed equation, and from before the ones the new checks do not
    replace and that failed: what makes a handwritten equation a decision (`image_page.*`)
    stays, whatever the letters are now."""
    names = {c.name for c in new}
    return [*new, *(c for c in old if c.name not in names and not c.passed)]


def relabel(
    result: DocumentResult,
    source: str,
    target: str,
    excluded: set[tuple[str, int]],
    included: set[tuple[str, int]] | None = None,
) -> Relettering:
    """Write `source` as `target` in every place it stands in the equations still waiting,
    except `excluded`, in place. With `included` (the screen's own form: every place is ticked
    until it is unticked), only those places are changed, and a place not in it is left as it
    is. Raises ReviewInvalid when `source` is not read in these equations, `target` is not
    another symbol of its family, or nothing would change. Nothing is accepted: the status of
    each fix stays."""
    from readablestem.web import edit_check

    found = inventory(result, set()).row(source)
    family = FAMILY_OF.get(source)
    if found is None:
        raise ReviewInvalid([FieldError("symbols", "That symbol is not in these equations.")])
    row = found[1]
    if family is None or target == source or target not in FAMILIES[family]:
        raise ReviewInvalid(
            [FieldError(f"to-{row.element_id}", "Choose the symbol to change it to.")]
        )
    if included is not None:
        excluded = {(p.element_id, p.nth) for p in row.places} - included
    changes: list[ReviewChange] = []
    left: list[str] = []
    waiting = {p.element_id for p in row.places if p.changeable}
    for element, fix in handwritten_equations(result):
        if element.id not in waiting or fix.equation is None:
            continue
        mine = {p.nth for p in row.places if p.element_id == element.id}
        nths = {n for n in mine if (element.id, n) not in excluded}
        if not nths:
            continue
        latex = relettered(fix.equation.latex, source, target, nths)
        checked = edit_check.check_equation_edit(element, fix, latex)
        if checked.mathml is None or checked.meaning or checked.unchanged or checked.empty:
            left.append(element.id)
            continue
        before = snapshot(fix)
        fix.equation = EquationFix(
            latex=checked.latex, mathml=checked.mathml, speech=checked.speech,
            display=checked.display,
        )  # fmt: skip
        fix.checks = _kept_checks(fix.checks, checked.checks)
        after = snapshot(fix)
        changes.append(ReviewChange(fix=fix, action=RELABEL_ACTION, before=before, after=after))
    if not changes:
        message = (
            "Nothing was changed: no equation with this symbol passed its checks with the new "
            "symbol, and each is left as it was."
            if left
            else "Nothing to change: no place is ticked, or every one is already decided."
        )
        raise ReviewInvalid([FieldError(f"to-{row.element_id}", message)])
    for change in changes:
        change.after[INVENTORY_KEY] = {"from": source, "to": target, "equations": len(changes)}
    return Relettering(changes, left, source, target)


# --------------------------------------------------------------------------- words


def done_message(done: Relettering) -> str:
    n = len(done.changes)
    now = name_of(done.target)
    was = name_of(done.source)
    message = f"Changed {was} to {now} in {n} {'equation' if n == 1 else 'equations'}."
    if done.left:
        k = len(done.left)
        message += (
            f" {k} {'equation was' if k == 1 else 'equations were'} left as before: the "
            "change did not pass the checks there."
        )
    return message + " Each is still waiting for your decision. Undo takes all of it back."
