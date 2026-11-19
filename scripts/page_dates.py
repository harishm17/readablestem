"""Write `web/page_dates.json`: the day each public page's text last changed (WS12.7).

The sitemap's `lastmod`, the pages' `dateModified` and the guides' "Last updated" line read this
file. The date of a page is the newest git commit date of any file in `PublicPage.files`
(web/seo.py); a file with no commit yet counts as changed today. The Docker image has no git
history, so the file is committed: run this script after changing a public page, and
`tests/test_web_seo.py` fails if a page changed after its date.

    uv run python scripts/page_dates.py
"""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

from readablestem.web import seo

ROOT = Path(__file__).resolve().parents[1]


def last_change(files: tuple[str, ...]) -> str | None:
    """The newest day any of these files (or folders) changed, or None if none exists."""
    existing = [name for name in files if (ROOT / name).exists()]
    if not existing:
        return None
    days = []
    for name in existing:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", name],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", name],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        days.append(datetime.date.today().isoformat() if dirty or not out else out)
    return max(days)


def main() -> None:
    dates = {}
    for page in seo.PUBLIC_PAGES:
        day = last_change(page.files)
        if day is None:
            raise SystemExit(f"no file of {page.path} exists: {page.files}")
        dates[page.path] = day
    day = last_change(seo.SHOWCASE_FILES)
    assert day is not None
    dates[seo.SHOWCASE_KEY] = day
    seo.PAGE_DATES_FILE.write_text(json.dumps(dates, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
