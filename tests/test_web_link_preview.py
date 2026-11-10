"""Link previews: every page carries a description, Open Graph and Twitter card tags,
with absolute addresses when the site address is known."""

from __future__ import annotations

import re
from pathlib import Path

from fixtures.web.support import WebEnv

STATIC = Path(__file__).resolve().parent.parent / "src" / "readablestem" / "web" / "static"


def test_the_landing_page_has_preview_tags(tmp_path):
    with WebEnv(tmp_path).running() as client:
        html = client.get("/").text
    for prop in ("og:title", "og:description", "og:image", "og:url", "og:type"):
        assert f'property="{prop}"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert 'name="description"' in html
    title = re.search(r'property="og:title" content="([^"]*)"', html).group(1)
    assert title.endswith("| ReadableSTEM") and "Accessible HTML" in title
    image = re.search(r'property="og:image" content="([^"]*)"', html).group(1)
    assert re.search(r"/static/social-card(\.[0-9a-f]+)?\.png$", image)


def test_error_pages_carry_no_page_address(tmp_path):
    # A 404 must not echo the requested address: it would make the 404 for someone else's
    # job differ from the 404 for a made-up one.
    with WebEnv(tmp_path).running() as client:
        html = client.get("/no-such-page").text
    assert 'rel="canonical"' not in html and 'property="og:url"' not in html
    assert 'property="og:title"' in html
