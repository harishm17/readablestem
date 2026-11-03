"""The "Report this item" link on a review card.

It opens the feedback path the footer already offers (`FEEDBACK_URL`, else an email to
`CONTACT_EMAIL`) with the job and the item named, so a report says where the problem is
without the reviewer typing ids. Only the two ids go into the address: no text of the
document, no alt text, no LaTeX. The reviewer writes what is wrong. When neither path is
configured there is no link (and no new place where a report could go).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from readablestem.config import Settings

SUBJECT = "ReadableSTEM report: an item on the review screen"
BODY = "Job: {job}\nItem: {item}\n\nWhat is wrong (please do not paste text from your document):\n"


def item_url(settings: Settings, job_id: str, item_id: str) -> str | None:
    """Where "Report this item" goes for one item, or None when no path is set up."""
    if settings.feedback_url:
        parts = urlsplit(settings.feedback_url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query += [("job", job_id), ("item", item_id)]
        return urlunsplit(parts._replace(query=urlencode(query)))
    if settings.contact_email:
        body = BODY.format(job=job_id, item=item_id)
        return f"mailto:{settings.contact_email}?subject={quote(SUBJECT)}&body={quote(body)}"
    return None
