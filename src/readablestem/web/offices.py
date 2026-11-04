"""The page for accessibility offices (/for-accessibility-offices) and its one-page summary.

The facts on both come from the settings, through the same list the privacy page is written
from (`web.recipients`), so the offices page, the summary and the privacy notice cannot
disagree about who receives a document or what is switched on.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from markupsafe import Markup

from readablestem.pipeline import export_docx
from readablestem.web.recipients import privacy_context

if TYPE_CHECKING:
    from readablestem.config import Settings

PAGE_PATH = "/for-accessibility-offices"
SUMMARY_PATH = "/for-accessibility-offices/summary"
SUMMARY_FILENAME = "readablestem-for-accessibility-offices.html"
SUMMARY_CSS = Path(__file__).resolve().parent / "static" / "offices-summary.css"


def offices_context(settings: Settings, *, tracer_on: bool = False) -> dict[str, Any]:
    """The variables both templates render with, besides the page-wide ones."""
    context = privacy_context(settings, tracer_on=tracer_on)
    recipients = context["recipients"]
    model_companies = [r.name for r in recipients if r.gets_documents]
    context |= {
        "active": "offices",
        "page_path": PAGE_PATH,
        "summary_path": SUMMARY_PATH,
        "summary_filename": SUMMARY_FILENAME,
        # The companies that receive pictures cut from a document, in the order the privacy
        # page lists them, and the one that gets the model requests.
        "model_companies": model_companies,
        "model_names": " and ".join(model_companies),
        "model_company": recipients[0].name,
        # Word downloads need pandoc on the server; the data flow names the formats.
        "word_export": export_docx.available(),
    }
    return context


def summary_style_tag() -> Markup:
    """The summary's stylesheet as an inline <style>: the file is downloaded on its own, so it
    cannot link the site's CSS."""
    return Markup("<style>\n{}</style>").format(Markup(SUMMARY_CSS.read_text(encoding="utf-8")))
