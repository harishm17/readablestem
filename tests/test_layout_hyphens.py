"""A hyphen at the end of a line: a broken word loses it, a compound keeps it.

"veloc-" / "ity" is one word; "well-" / "known" is a compound that would read "wellknown"
without its hyphen. `pipeline/hyphens.py` tells them apart with a list of English words
(`pipeline/data/words-en.txt.gz`, SCOWL; source and licence in `pipeline/data/README.md`).
"""

from __future__ import annotations

from readablestem.pipeline import hyphens
from readablestem.pipeline.layout import _line_break


def test_a_soft_hyphen_always_goes_and_other_line_ends_are_joined_with_a_space():
    assert _line_break("a soft­", "hyphen") == ("a soft", "")
    assert _line_break("ends here", "and goes on") == ("ends here", " ")
    assert _line_break("a range 5-", "7 m") == ("a range 5-", "")  # one number, one token
    assert _line_break("a range 5-", "x m") == ("a range 5-", " ")  # no letter before the hyphen
    assert _line_break("a dash \u2013", "like this") == ("a dash \u2013", " ")


def test_a_missing_word_list_joins_as_it_always_did(monkeypatch):
    monkeypatch.setattr(hyphens, "words", lambda: frozenset())
    assert _line_break("the well-", "known result") == ("the well", "")
    assert _line_break("an x-", "ray") == ("an x-", "")  # a single letter needs no list


def test_a_break_inside_an_address_the_file_links_on_both_lines_joins_without_a_space():
    """Nothing in "...PhysRevLett.1" / "23.4567" says the address goes on, so it was joined
    with a space; the link annotations the file has over both lines (one per line, as hyperref
    writes them) say it. A named link over two lines ("Smith" / "Jones") keeps its space."""
    import pymupdf

    from readablestem.pipeline import layout

    address = "https://doi.org/10.1103/PhysRevLett.123.4567"
    lines = [
        "The results of this experiment are described in full in the paper by the group at",
        "https://doi.org/10.1103/PhysRevLett.1",
        "23.4567 for anyone who wants the data behind the figures and the tables in the text of",
        "this report, which was written by Smith",
        "Jones and the rest of the group.",
    ]
    links = [(1, lines[1], address), (2, lines[2].split()[0], address)]
    links += [
        (3, "Smith", "https://example.org/people/x"),
        (4, "Jones", "https://example.org/people/x"),
    ]

    def converted(linked: bool) -> str:
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + 14 * i), line, fontsize=11)
        for i, part, uri in links if linked else []:
            width = pymupdf.get_text_length(part, fontsize=11)
            rect = pymupdf.Rect(72, 100 + 14 * i - 9, 72 + width, 100 + 14 * i + 3)
            page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": uri})
        data = doc.tobytes()
        doc.close()
        return " ".join(e.text or "" for e in layout.analyze(data).elements)

    assert "PhysRevLett.1 23.4567" in converted(linked=False)
    text = converted(linked=True)
    assert "PhysRevLett.123.4567 for anyone" in text
    assert "Smith Jones" in text
