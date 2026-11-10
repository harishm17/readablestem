"""The tables gold in the repo: the 13 OpenStax pages (CC BY), their reference pages,
the authored documents (MIT), `gold fetch` keeping table records only, and the three
table print styles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from readablestem.eval import render
from readablestem.gold.openstax import render_attribution
from test_openstax import CC_BY

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold" / "tables"
FIXTURE_PAGE = Path(__file__).parent / "fixtures" / "openstax_page.xhtml"  # two tables


def test_the_manifest_and_attribution_match_the_files():
    manifest = json.loads((GOLD / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["totals"] == {"table": 64}
    for entry in manifest["files"]:
        data = (GOLD / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    attribution = (GOLD / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert attribution == render_attribution(manifest)
    assert "contain tables extracted" in attribution
    assert "Barbara Illowsky" in attribution and "Paul Peter Urone" in attribution


# ------------------------------------------------------------------------- authored


# --------------------------------------------------------------------- gold fetch


# ------------------------------------------------------------------------ print styles


def page_source() -> render.PageSource:
    return render.PageSource(
        book_slug="physics",
        book_title="Physics",
        page_slug="2-2-speed",
        page_title="2.2 Speed",
        page_url="https://openstax.org/books/physics/pages/2-2-speed",
        content_url="https://openstax.org/apps/archive/x/contents/b@v:p.json",
        license_name="Creative Commons Attribution License",
        license_url=CC_BY,
        xhtml=FIXTURE_PAGE.read_text(encoding="utf-8"),
    )
