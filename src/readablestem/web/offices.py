"""The page for accessibility offices (/for-accessibility-offices) and its one-page summary.

The facts on both come from the settings, through the same list the privacy page is written
from (`web.recipients`), so the offices page, the summary and the privacy notice cannot
disagree about who receives a document or what is switched on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markupsafe import Markup

from readablestem.pipeline import export_docx
from readablestem.web.recipients import privacy_context

if TYPE_CHECKING:
    from readablestem.config import Settings

# Daily database backups are kept this long (docs/deploy-railway.md, "Backups").
BACKUP_DAYS = 6
PAGE_PATH = "/for-accessibility-offices"
SUMMARY_PATH = "/for-accessibility-offices/summary"
SUMMARY_FILENAME = "readablestem-for-accessibility-offices.html"
SUMMARY_CSS = Path(__file__).resolve().parent / "static" / "offices-summary.css"


# The pilot checklist in short, for the one-page summary; the page words the same eight steps
# at more length (templates/offices.html, "Pilot checklist"). A test counts both.
PILOT_STEPS = (
    "Pick one course and one document, with no student records in it.",
    "Read the privacy notice with your privacy or legal contact.",
    "The instructor uploads the PDF and works through every decision on the review screen.",
    "Check the file with your own screen readers.",
    "Check it with the checker and review your office normally uses. No result is claimed.",
    "Post it in the course and open it as a student would.",
    "Download the file and the review record before the document is deleted.",
    "Tell us what was wrong or missing.",
)


@dataclass(frozen=True)
class DataItem:
    """One kind of data a conversion moves, who gets it and how long they keep it.

    The page's two tables (by kind of data, and by company) and the one-page summary are all
    written from the list `data_items` builds, so they cannot name different data, different
    companies or different times. `sent_to` holds company names from `web.recipients`, the
    host first (it stores everything below except page views); `kept` is how long, in words."""

    label: str
    sent_to: tuple[str, ...]
    kept: str


# What each model company says about keeping API requests (the long wording is in
# `templates/_recipients.html`; keep the two in step: a test compares their numbers).
COMPANY_KEEPS = {
    "Anthropic": "up to 30 days, longer if its safety systems flag the material",
    "OpenAI": "as set out in OpenAI's API data policies",
}


def data_items(context: dict[str, Any]) -> list[DataItem]:
    """The data a conversion moves, from the context `offices_context` builds (the settings'
    features, the companies that receive documents, the retention times)."""
    recipients = context["recipients"]
    model = tuple(r.name for r in recipients if "model" in r.purposes)
    readers = tuple(r.name for r in recipients if "handwriting" in r.purposes)
    hours, days = context["retention_hours"], context["opt_in_days"]
    ours = (
        f"{hours} hours after the instructor last works on it; 7 days if the instructor keeps "
        f"it; {days} days if the instructor also lets us look at the results"
    )
    same = "With us: the same schedule"

    def with_company(names: tuple[str, ...]) -> str:
        return "; ".join(
            f"{name}: {COMPANY_KEEPS[name]}" for name in names if name in COMPANY_KEEPS
        )

    parts = "equations, figures and tables" if context["tables_on"] else "equations and figures"
    items = [
        DataItem("The uploaded file and its file name", ("Railway",), ours),
        DataItem(
            f"Images of {parts} cut from the PDF, with a little nearby text",
            ("Railway", *model),
            f"{same}. {with_company(model)}",
        ),
    ]
    if context["notation_on"]:
        items.append(
            DataItem(
                "A course's notation (its symbols and what they mean), for a document uploaded "
                "to that course",
                ("Railway", *model),
                f"{same}. {with_company(model)}",
            )
        )
    if context["image_pages_on"]:
        items.append(
            DataItem(
                "A picture of each scanned or handwritten page, only if the instructor ticks "
                "“Convert scanned or handwritten pages”",
                ("Railway", *model),
                f"{same}. {with_company(model)}",
            )
        )
    if readers:
        items.append(
            DataItem(
                "A close-up of a handwritten expression, to be read a second time",
                ("Railway", *readers),
                f"{same}. {with_company(readers)}",
            )
        )
    items += [
        DataItem(
            "The result and the review decisions",
            ("Railway",),
            f"The same schedule; database backups keep a deleted document up to about {BACKUP_DAYS} days",
        ),
        DataItem(
            "The instructor's email address, name and sign-in id",
            ("Railway",),
            "Until the instructor deletes the account",
        ),
    ]
    tracing = tuple(r.name for r in recipients if "tracing" in r.purposes)
    items.append(
        DataItem(
            "Timings, counts, costs and error categories; never document text, images or "
            "file names",
            ("Railway", *tracing),
            "Railway keeps its standard logs for a limited time"
            + ("; Langfuse as set in its project" if tracing else ""),
        )
    )
    if context["analytics_on"]:
        items.append(
            DataItem(
                "Page views and a few named events, such as “uploaded a document”",
                ("PostHog",),
                "Set in the PostHog project; nobody is identified",
            )
        )
    return items


def received_by(items: list[DataItem], company: str) -> list[str]:
    """What `company` receives, from the same list: the recipient table's cells."""
    return [item.label for item in items if company in item.sent_to]


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
        "backup_days": BACKUP_DAYS,
        "pilot_steps": PILOT_STEPS,
        "analytics_on": settings.analytics_enabled,
        "retention_hours": settings.retention_hours,
        "opt_in_days": settings.opt_in_retention_days,
    }
    context["data_items"] = data_items(context)
    context["received_by"] = lambda company: received_by(context["data_items"], company)
    return context


def summary_style_tag() -> Markup:
    """The summary's stylesheet as an inline <style>: the file is downloaded on its own, so it
    cannot link the site's CSS."""
    return Markup("<style>\n{}</style>").format(Markup(SUMMARY_CSS.read_text(encoding="utf-8")))
