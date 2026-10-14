"""The two lines an instructor sets on the job page for the student file: the title students
hear (T1-7) and a contact line for its footer (T1-17).

Both are typed text that ends up in the student's HTML, so both are checked here and escaped
again where they are written (`pipeline.assemble`). They are document-derived: shown to their
owner only, stored on the job (`Job.title_override`, `Job.contact_line`), deleted with it and
never logged.

- **Title** (`clean_title`): one line, at most `MAX_TITLE` characters, no control or
  invisible format characters. Empty means "use the title read from the PDF".
- **Contact** (`clean_contact`): an email address or a web address (http or https), nothing
  else, at most `MAX_CONTACT` characters. Empty means no contact line.
- `effective_title`: what the page, the list and the download name call the document.
"""

from __future__ import annotations

import re
import unicodedata

from readablestem.pipeline import assemble

MAX_TITLE = 200
MAX_CONTACT = 254

TITLE_TOO_LONG = f"Use a title of {MAX_TITLE} characters or fewer."
CONTACT_BAD = (
    "Enter an email address such as name@example.edu, or a web address that starts with "
    "https:// or http://."
)
CONTACT_TOO_LONG = f"Use a contact line of {MAX_CONTACT} characters or fewer."

_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&*+=?^_{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_WEB = re.compile(r"https?://[^\s/<>\"']+\.[^\s/<>\"']+[^\s<>\"']*")


class Invalid(ValueError):
    """A line that cannot be used; the message says what to change."""


def _plain(text: str) -> str:
    """One line: control and invisible format characters (bidi controls, zero-width spaces)
    removed, whitespace collapsed."""
    kept = [
        " " if ch.isspace() else ch
        for ch in text
        if not unicodedata.category(ch).startswith("C") or ch.isspace()
    ]
    return " ".join("".join(kept).split())


def clean_title(raw: str) -> str:
    """The title as it will be stored; "" for none. Raises `Invalid` when too long."""
    text = _plain(raw)
    if len(text) > MAX_TITLE:
        raise Invalid(TITLE_TOO_LONG)
    return text


def clean_contact(raw: str) -> str:
    """The contact line as it will be stored; "" for none. Raises `Invalid`."""
    text = "".join(raw.split())  # an address has no spaces: a stray one is a typo
    if not text:
        return ""
    if len(text) > MAX_CONTACT:
        raise Invalid(CONTACT_TOO_LONG)
    if text.lower().startswith("mailto:"):
        text = text[len("mailto:") :]
    if _EMAIL.fullmatch(text) or _WEB.fullmatch(text):
        return text
    raise Invalid(CONTACT_BAD)


def suggested(result_title: str | None, heading_texts: list[str]) -> str:
    """The title the file would have without the instructor's choice."""
    return assemble.suggested_title(result_title, heading_texts)


def effective_title(override: str | None, result_title: object, filename: str) -> str:
    """What a list or a heading calls the document: the instructor's title, else the PDF's
    title without its section number, else the file name."""
    if override and override.strip():
        return override.strip()
    if isinstance(result_title, str) and result_title.strip():
        text = assemble.strip_section_number(" ".join(result_title.split()))
        generic = text.casefold().rstrip(".:") in assemble.GENERIC_TITLES
        if text and text != assemble.DEFAULT_TITLE and not generic:
            return text
    return filename
