"""Functions, classes, methods and properties in src/ that nothing calls.

    uv run python scripts/dead_code.py

Runs vulture over the package, with tests/ and scripts/ counted as callers, and prints what
is left. Exit code 0: nothing is unused. 1: something is, and each is named with its file and
line. CI runs it in the static job (`make lint` too).

What it leaves out, and why:

- Variables and attributes. Fields of a dataclass or a pydantic model are read by the code
  that writes JSON and by templates, which vulture cannot follow, so nearly every report of
  that kind is false.
- A name a template or another packaged text file uses (`{{ view.title_note }}`): the files
  of the package that are not Python are searched for the name as a word.
- A function with a decorator that registers it (a route, a validator, a fixture, a
  command): something else holds it.
- The names in `CALLED_BY_A_BASE`: methods a base class or the interpreter calls by name.

A name that is kept for a caller outside the repository goes in `KEPT` with the reason. The
list is empty, and a new entry should be rare: a function with no caller is deleted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from vulture import Vulture

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "src" / "readablestem"
KINDS = ("function", "class", "method", "property")
# Text files of the package that name Python attributes (templates, mostly).
TEXT = (".html", ".txt", ".xml", ".js", ".j2", ".md")
REGISTERED = [
    "@*.get",
    "@*.post",
    "@*.put",
    "@*.patch",
    "@*.delete",
    "@*.head",
    "@*.api_route",
    "@*.middleware",
    "@*.exception_handler",
    "@*.listens_for",
    "@*.fixture",
    "@*.setter",
    "@field_validator",
    "@model_validator",
    "@field_serializer",
    "@model_serializer",
    "@computed_field",
    "@*.register",
]
# Called by name by a base class or by Python itself, never by this code.
CALLED_BY_A_BASE = {
    "handle_starttag": "html.parser.HTMLParser",
    "handle_startendtag": "html.parser.HTMLParser",
    "handle_endtag": "html.parser.HTMLParser",
    "handle_data": "html.parser.HTMLParser",
    "handle_entityref": "html.parser.HTMLParser",
    "handle_charref": "html.parser.HTMLParser",
    "readinto": "io.RawIOBase",
    "__getattr__": "a module's lazy attribute (PEP 562)",
    "__call__": "a Protocol's call signature",
}
# name -> why it stays with no caller in the repository.
KEPT: dict[str, str] = {}


def _words_in_text_files() -> set[str]:
    words: set[str] = set()
    for path in PACKAGE.rglob("*"):
        if path.suffix in TEXT and path.is_file():
            words.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", path.read_text(errors="replace")))
    return words


def unused() -> list[str]:
    """One line per unused name: `path:line: kind 'name'`."""
    finder = Vulture(ignore_decorators=REGISTERED)
    finder.scavenge([str(REPO / "src"), str(REPO / "tests"), str(REPO / "scripts")])
    in_text = _words_in_text_files()
    lines = []
    for item in finder.get_unused_code(min_confidence=60):
        path = Path(item.filename)
        if item.typ not in KINDS or PACKAGE not in path.parents:
            continue
        if item.name in CALLED_BY_A_BASE or item.name in KEPT or item.name in in_text:
            continue
        lines.append(f"{path.relative_to(REPO)}:{item.first_lineno}: {item.typ} '{item.name}'")
    return sorted(lines)


def main() -> int:
    found = unused()
    for line in found:
        print(line)
    if found:
        print(
            f"{len(found)} unused. Delete each, or give it a caller. "
            "See the top of scripts/dead_code.py for what counts as used.",
            file=sys.stderr,
        )
        return 1
    print("no unused function, class, method or property in src/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
