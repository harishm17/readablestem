"""Who receives what a person uploads: the one list the public pages are written from.

`recipients(settings)` is built from the settings, so the privacy page, the account deletion
notes and the launch checks (`ops.launch`) cannot disagree about it: the model provider
(`MODEL_PROVIDER`), each handwriting reader that is on (`MATH_READER_*_ENABLED`; the reader's
company may or may not be the model provider), the host, and tracing when it is on.
`privacy_context` is what the /privacy route renders; `notice_text` renders that same page for
the launch checks and strips it to text, so they test what a visitor would read.

Adding a recipient here (a new provider, a new reader) changes the page; the privacy template
loops over this list and names every entry. Provider-specific retention wording lives in
`templates/_recipients.html`, keyed by the recipient's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2

from readablestem.web import office_convert
from readablestem.web.events import EVENT_RETENTION_DAYS

if TYPE_CHECKING:
    from readablestem.config import Settings

MODEL_COMPANIES = {"anthropic": "Anthropic", "openai": "OpenAI"}
READER_COMPANIES = {"claude": "Anthropic", "openai": "OpenAI"}


@dataclass(frozen=True)
class Recipient:
    """One company that receives something. `purposes` holds "model" (images and nearby text
    of equations and figures), "handwriting" (a crop of a handwritten expression, read a
    second time), "hosting" or "tracing"."""

    name: str
    purposes: tuple[str, ...]

    @property
    def gets_documents(self) -> bool:
        """Receives pictures cut from a person's document (a model company)."""
        return "model" in self.purposes or "handwriting" in self.purposes


def recipients(settings: Settings, *, tracer_on: bool = False) -> list[Recipient]:
    """Every recipient the settings turn on, model provider first, one entry per company."""
    purposes: dict[str, list[str]] = {MODEL_COMPANIES[settings.model_provider]: ["model"]}
    for reader in settings.math_readers_on:
        purposes.setdefault(READER_COMPANIES[reader], []).append("handwriting")
    found = [Recipient(name, tuple(p)) for name, p in purposes.items()]
    found.append(Recipient("Railway", ("hosting",)))
    if tracer_on:
        found.append(Recipient("Langfuse", ("tracing",)))
    return found


def model_vendor(settings: Settings) -> str:
    """The company that gets pictures of equations and figures: the recipient with the
    "model" purpose, so a line that names it stays true if the provider changes."""
    return next(r.name for r in recipients(settings) if "model" in r.purposes)


def privacy_context(settings: Settings, *, tracer_on: bool = False) -> dict[str, Any]:
    """The variables /privacy renders with (besides the page-wide ones `deps.render` adds,
    which `notice_text` supplies for itself)."""
    return {
        "active": "privacy",
        # The page names Langfuse only when traces are actually sent there, and usage events
        # only when they are recorded.
        "tracing": tracer_on,
        "events_on": settings.events_enabled,
        "event_days": EVENT_RETENTION_DAYS,
        # What the content features send and keep, only when they are on.
        "tables_on": settings.feature_tables,
        "notation_on": settings.feature_notation_memory,
        "image_pages_on": settings.feature_image_pages,
        "microsoft_on": settings.ms_sign_in_enabled,
        # Word and PowerPoint uploads are taken (T3-30): the page says how they are handled.
        "office_on": bool(office_convert.input_kinds(settings)),
        "email_login_on": settings.email_login_enabled,
        # Which companies receive pictures and nearby text, and the rest of the page's list.
        "recipients": recipients(settings, tracer_on=tracer_on),
    }


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_text(page: str) -> str:
    """The visible text of a page: tags and comments dropped, entities decoded, blanks
    collapsed."""
    parser = _Text()
    parser.feed(page)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def notice_text(
    settings: Settings, *, tracer_on: bool = False, templates_dir: Path | None = None
) -> str | None:
    """The privacy page as a visitor would read it with `settings`, as plain text; None when
    the template does not exist. Rendered from the real template with the route's own
    context (`privacy_context`) and the page-wide values it reads, so a provider that the page
    would not name is not named here either. `templates_dir`: another folder of templates
    (a test's stand-in)."""
    from readablestem.web.app import TEMPLATES_DIR, make_templates

    folder = templates_dir or TEMPLATES_DIR
    if not (folder / "privacy.html").is_file():
        return None
    env = make_templates().env.overlay(
        loader=jinja2.FileSystemLoader(folder), undefined=jinja2.ChainableUndefined
    )
    context: dict[str, Any] = {
        **privacy_context(settings, tracer_on=tracer_on),
        "contact_email": settings.contact_email,
        "retention_hours": settings.retention_hours,
        "opt_in_days": settings.opt_in_retention_days,
        "signup_open": settings.signup_open,
        "analytics_on": settings.analytics_enabled,
        "analytics_events": [],
        "courses_enabled": settings.feature_notation_memory,
        "source_url": settings.source_url,
        "feedback_url": settings.feedback_url,
    }
    return html_text(env.get_template("privacy.html").render(context))
