"""The "Report this item" link on a review card.

It opens the app's own problem-report form (`routes/reports.py`, `web.reports`) with the item
named in the address, so a report says where the problem is without the reviewer typing ids.
Only the item's id is in the address: no text of the document, no alt text, no LaTeX; the
form reads the item's kind and page from the stored document. The reviewer writes what is
wrong. The link is always there for a person who may review: nothing depends on a mail
program or a configured address.
"""

from __future__ import annotations

from urllib.parse import quote


def item_url(job_id: str, item_id: str) -> str:
    """The report form for one item of one document."""
    return f"/jobs/{job_id}/report?item={quote(item_id, safe='')}"


def job_url(job_id: str) -> str:
    """The report form for the document itself (a stopped conversion)."""
    return f"/jobs/{job_id}/report"
