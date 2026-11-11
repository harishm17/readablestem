"""Stable element ids (`core.uid`, WS9.1) through the pipeline's public entry.

Generated three-page PDFs go through `convert_pdf` with the offline client: the same file
twice gives the same uids, and a small change to one element (a paragraph added, one
paragraph's words changed, one of two identical equations removed) changes the uids of that
element only, while the positional ids after it on its page move.
"""

from __future__ import annotations

import re

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core import uid
from readablestem.core.types import ConversionOptions, DocumentResult, Element, Kind
from readablestem.models import OFFLINE_DEFAULTS
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline.run import convert_pdf

SENTENCE = "It keeps going at a steady pace until the brake stops it."


def build(*, added: bool = False, edited: bool = False, one_equation: bool = False) -> bytes:
    """Three pages, each a heading, three paragraphs, two identical display equations and a
    captioned figure. `added`: a paragraph more at the top of page 3. `edited`: one word of
    page 2's middle paragraph changed. `one_equation`: page 1's second equation left out."""
    doc = pymupdf.open()
    for number in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 80), f"Section {number + 1} Motion", fontsize=18, fontname="hebo")
        y = 120
        paragraphs = [f"Paragraph {i} on page {number + 1} is about the cart." for i in range(3)]
        if added and number == 2:
            paragraphs.insert(0, "A new paragraph says the track is level.")
        if edited and number == 1:
            paragraphs[1] = paragraphs[1].replace("cart", "trolley")
        for words in paragraphs:
            page.insert_text((72, y), words, fontsize=11)
            page.insert_text((72, y + 16), SENTENCE, fontsize=11)
            y += 46
        for n in range(2):
            if not (one_equation and number == 0 and n == 1):
                page.insert_htmlbox(
                    pymupdf.Rect(200, y, 420, y + 30), "<i>v</i> = <i>d</i> / <i>t</i>"
                )
            y += 50
        box = pymupdf.Rect(150, y, 350, y + 100)
        page.draw_rect(box, color=(0, 0, 1), fill=(0.8, 0.8, 1))
        page.insert_text((150, y + 115), f"Figure {number + 1} A cart.", fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def convert(pdf: bytes) -> DocumentResult:
    client = FakeVisionClient(defaults=dict(OFFLINE_DEFAULTS))
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    return convert_pdf(pdf, settings=settings, client=client, options=ConversionOptions()).result


def uids(result: DocumentResult) -> dict[str, Element]:
    found = uid.assign(result)
    return {found.by_id[e.id]: e for e in result.elements}


@pytest.fixture(scope="module")
def base() -> DocumentResult:
    return convert(build())


def test_the_same_file_twice_gives_the_same_uids_and_no_text_in_them(base):
    again = convert(build())
    assert [e.id for e in again.elements] == [e.id for e in base.elements]
    first, second = uids(base), uids(again)
    assert list(first) == list(second)
    assert len(first) == len(base.elements)  # one uid per element, never shared
    assert all(re.fullmatch(r"u1-[0-9a-f]{16}", u) for u in first)
    # Identical items on one page (the two equations) still get two uids.
    equations = [u for u, e in first.items() if e.kind is Kind.EQUATION and e.page == 0]
    assert len(equations) == 2


@pytest.mark.parametrize(
    ("variant", "gone", "new", "renamed_page"),
    [
        # A paragraph added at the top of page 3: one new uid; every positional id after it
        # on page 3 moves.
        ({"added": True}, 0, 1, 2),
        # One word of a paragraph on page 2: its uid changes, nothing else does.
        ({"edited": True}, 1, 1, None),
        # The second of page 1's two identical equations removed: only its uid goes, and
        # the figure after it, renamed by position, keeps its uid.
        ({"one_equation": True}, 1, 0, 0),
    ],
    ids=["paragraph added", "words changed", "equal equation removed"],
)
def test_a_change_to_one_element_changes_only_its_uid(base, variant, gone, new, renamed_page):
    before, after = uids(base), uids(convert(build(**variant)))
    assert len(set(before) - set(after)) == gone
    assert len(set(after) - set(before)) == new
    kept = set(before) & set(after)
    assert len(kept) == len(before) - gone
    for u in kept:
        assert after[u].kind is before[u].kind and after[u].page == before[u].page
    moved = {before[u].page for u in kept if before[u].id != after[u].id}
    assert moved == (set() if renamed_page is None else {renamed_page})


def test_a_uid_of_an_earlier_version_still_names_its_element(base, monkeypatch):
    """When the fingerprint changes, the version goes up and the old uids become aliases:
    a link made with a version-1 uid still reaches its element, never another one."""
    old = uid.assign(base)
    monkeypatch.setitem(uid.FINGERPRINTS, 2, lambda e: uid.fingerprint(e) + "|v2")
    now = uid.assign(base)
    for element in base.elements:
        current = now.uid(element.id)
        assert current is not None and current.startswith("u2-")
        assert now.resolve(old.by_id[element.id]) == element.id
        assert now.aliases[old.by_id[element.id]] == current
        assert now.aliases[element.id] == current
    assert now.resolve("u1-" + "0" * 16) is None
    assert now.resolve("u3-" + "0" * 16) is None
