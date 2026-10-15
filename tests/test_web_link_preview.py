"""Link previews: every page carries a description, Open Graph and Twitter card tags,
with absolute addresses when the site address is known."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from fixtures.web.support import WebEnv
from readablestem.web.app import make_templates

STATIC = Path(__file__).resolve().parent.parent / "src" / "readablestem" / "web" / "static"


def test_the_preview_image_is_1200_by_630():
    with Image.open(STATIC / "social-card.png") as img:
        assert img.size == (1200, 630)


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


def test_preview_addresses_are_absolute_when_the_site_address_is_known():
    env = make_templates("https://readablestem.com/").env
    assert env.globals["site_url"] == "https://readablestem.com"
