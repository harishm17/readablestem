"""Fixes from the blind review of the redesign: LaTeX is never shown as speech, the signed-in
header keeps its links and does not print the address, the offices page has a table of contents,
and the choices on a review card that are not the primary one are marked as such."""

from __future__ import annotations

import pytest

from fixtures.web.support import ALICE, WebEnv, login
from readablestem.web.app import _speakable


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("v equals d over t", "v equals d over t"),
        ("capital delta bold d over capital delta t", "capital delta bold d over capital delta t"),
        ("\\omega", ""),
        ("x^2 plus 1", ""),
        ("\\frac{a}{b}", ""),
        ("a_1", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_latex_is_never_shown_as_a_spoken_reading(text, shown):
    assert _speakable(text) == shown


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    env = WebEnv(tmp_path_factory.mktemp("blind-fixes"))
    with env.running() as anon:
        env.allow(ALICE)
        out = {
            "offices": anon.get("/for-accessibility-offices").text,
            "anon_home": anon.get("/").text,
        }
        alice = env.client()
        login(alice, ALICE)
        out["app"] = alice.get("/app").text
    return out


def nav(page: str) -> str:
    return page[page.index('<nav aria-label="Main">') : page.index("</nav>")]


def test_signed_in_nav_keeps_guides_and_offices_and_the_address_is_not_printed(pages):
    links = nav(pages["app"])
    assert ">Guides</a>" in links and ">For accessibility offices</a>" in links
    assert ">My documents</a>" in links
    # the address is announced to a screen reader but is not drawn in the header
    assert '<span class="visually-hidden">Signed in as ' in pages["app"]
    assert 'class="signed-in"' not in pages["app"]


def test_signed_out_nav_has_the_same_public_links(pages):
    links = nav(pages["anon_home"])
    assert ">Guides</a>" in links and ">For accessibility offices</a>" in links


def test_offices_page_has_an_on_this_page_list_that_links_every_section(pages):
    page = pages["offices"]
    toc = page.split('aria-label="On this page"', 1)[1].split("</nav>", 1)[0]
    for anchor in (
        "data-flow",
        "subprocessors",
        "retention",
        "training",
        "ferpa",
        "not-certify",
        "pilot",
        "more",
    ):
        assert f'href="#{anchor}"' in toc and f'id="{anchor}"' in page, anchor
    # the diagram has a title, a description and the same steps as a list
    assert "<title" in page and "<desc" in page and "The same steps are listed below." in page
