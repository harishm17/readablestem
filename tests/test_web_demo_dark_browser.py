"""/demo in the dark scheme and in forced colours (a browser test).

The "About this demo" note is a white box in every scheme, so its links must carry their own
light-scheme colours: with `color-scheme: light dark` they took the dark scheme's pale blue
and measured 2.39:1 on the white (five axe `color-contrast` failures).
"""

from __future__ import annotations

import pytest

from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import axe_violations, browser, describe, site  # noqa: F401

pytestmark = pytest.mark.browser

NOTE_LINKS = 'aside[aria-label="About this demo"] a'


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_demo_passes_axe_in_both_colour_schemes(site, browser, axe_source, scheme):  # noqa: F811
    # bypass_csp only so axe can be injected; the demo is otherwise served under its CSP.
    context = browser.new_context(bypass_csp=True, color_scheme=scheme)
    page = context.new_page()
    response = page.goto(site.base + "/demo")
    assert response is not None and response.status == 200
    violations = axe_violations(page, axe_source)
    context.close()
    assert not violations, describe(violations)


def test_the_note_links_use_the_system_colours_in_forced_colours(site, browser):  # noqa: F811
    context = browser.new_context(forced_colors="active")
    page = context.new_page()
    page.goto(site.base + "/demo")
    link = page.eval_on_selector(NOTE_LINKS, "e => getComputedStyle(e).color")
    # Chromium's own LinkText, read from a probe element with the same property.
    system = page.evaluate(
        "() => { const p = document.createElement('span'); p.style.color = 'LinkText';"
        " document.body.append(p); return getComputedStyle(p).color; }"
    )
    context.close()
    assert link == system
