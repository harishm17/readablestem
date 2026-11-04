"""Beamer decks as LaTeX source (`FEATURE_BEAMER_SOURCE`): a frame is a section with its title as a
heading; an overlay step is left out when everything on it is also on the frame's last step, and
kept as "(step 1 of 2)" when it shows something the last step does not (the slide-deck rule of the
PDF path, docs/layout.md "Slide decks").

The grouping runs on XHTML written by hand (no LaTeXML needed). The spike on the five decks in
tests/fixtures/source/beamer/ runs the real LaTeXML in the real sandbox and is skipped without
them."""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.config import get_settings
from readablestem.core.types import Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.source import envelope, intake
from readablestem.source import ltx_reader as reader
from readablestem.source.convert import convert_source
from source_support import FIXTURES, make_zip, needs_latexml, png_bytes

XHTML = "http://www.w3.org/1999/xhtml"
DECKS = ("kinematics", "circuits", "calculus", "mechanics", "chemistry")
DECK = "\\documentclass{beamer}\n\\begin{document}\n\\begin{frame}x\\end{frame}\n\\end{document}"


def page(body: str) -> bytes:
    return (
        f'<?xml version="1.0"?><html xmlns="{XHTML}" lang="en"><head/><body>{body}</body></html>'
    ).encode()


def slide(frame: str, step: int, title: str, body: str) -> str:
    return (
        f'<div id="{frame}.slide{step}" class="ltx_slide">'
        f'<h2 class="ltx_title ltx_frame_title ltx_title_slide">{title}</h2>{body}</div>'
    )


def para(text: str) -> str:
    return f'<div class="ltx_para"><p class="ltx_p">{text}</p></div>'


def covered(text: str) -> str:
    return f'<span class="ltx_inline-block ltx_covered"><span class="ltx_p">{text}</span></span>'


def read(body: str, tmp_path: Path) -> reader.Reading:
    return reader.read(page(body), tmp_path, beamer=True)


def texts(result: reader.Reading) -> list[str]:
    return [e.text or "" for e in result.elements]


def test_a_frame_with_one_step_is_a_heading_and_its_content(tmp_path):
    result = read(slide("F1", 1, "Velocity", para("v is x over t.")), tmp_path)
    assert texts(result) == ["Velocity", "v is x over t."]
    heading = result.fixes[result.elements[0].id].heading
    assert heading is not None and heading.level == 2


def test_the_steps_of_a_pause_build_leave_only_the_last(tmp_path):
    body = (
        slide("F1", 1, "Rules", para(f"One. {covered('Two.')} {covered('Three.')}"))
        + slide("F1", 2, "Rules", para("One. " + covered("Three.") + " Two."))
        + slide("F1", 3, "Rules", para("One. Two. Three."))
    )
    result = read(body, tmp_path)
    assert [e.kind for e in result.elements] == [Kind.HEADING, Kind.PARAGRAPH]
    assert texts(result) == ["Rules", "One. Two. Three."]


def test_covered_text_is_never_written(tmp_path):
    result = read(slide("F1", 1, "T", para(f"Seen. {covered('Hidden.')}")), tmp_path)
    assert "Hidden" not in " ".join(texts(result))
    invisible = (
        '<span class="ltx_inline-block ltx_invisible"><span class="ltx_p">Ghost.</span></span>'
    )
    assert "Ghost" not in " ".join(texts(read(slide("F1", 1, "T", para(invisible)), tmp_path)))


def test_a_step_that_shows_something_the_last_step_does_not_is_kept_with_its_number(tmp_path):
    body = (
        slide("F1", 1, "Idea", para("Base."))
        + slide("F1", 2, "Idea", para("Base.") + para("Only on step two."))
        + slide("F1", 3, "Idea", para("Base.") + para("Last."))
    )
    result = read(body, tmp_path)
    headings = [e.text for e in result.elements if e.kind is Kind.HEADING]
    assert headings == ["Idea (step 2 of 3)", "Idea"]
    assert "Only on step two." in texts(result) and "Last." in texts(result)
    assert texts(result).count("Base.") == 2  # once for the kept step, once for the last


def test_a_step_with_a_different_formula_is_kept(tmp_path):
    def math(alt: str) -> str:
        return (
            f'<math xmlns="http://www.w3.org/1998/Math/MathML" alttext="{alt}" display="inline">'
            f"<mi>{alt}</mi></math>"
        )

    body = slide("F1", 1, "E", para(f"So {math('a')}.")) + slide(
        "F1", 2, "E", para(f"So {math('b')}.")
    )
    result = read(body, tmp_path)
    assert [e.text for e in result.elements if e.kind is Kind.HEADING] == ["E (step 1 of 2)", "E"]


def test_two_frames_are_two_groups_whatever_their_steps(tmp_path):
    body = (
        slide("F1", 1, "A", para("Same."))
        + slide("F1", 2, "A", para("Same."))
        + slide("F2", 1, "B", para("Same."))
    )
    assert [e.text for e in read(body, tmp_path).elements if e.kind is Kind.HEADING] == ["A", "B"]


def test_items_a_frame_reveals_later_are_items_of_their_own(tmp_path):
    item = (
        '<span class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span>'
        '<span class="ltx_para"><span class="ltx_p">Second.</span></span></span>'
    )
    body = slide(
        "F1",
        1,
        "L",
        '<ul class="ltx_itemize"><li class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span>'
        '<div class="ltx_para"><p class="ltx_p">First.'
        f'<span class="ltx_inline-block ltx_uncovered">{item}</span></p></div></li></ul>',
    )
    items = [e for e in read(body, tmp_path).elements if e.kind is Kind.LIST_ITEM]
    assert [e.text for e in items] == ["First.", "Second."]
    assert all(e.list_level == 1 and e.list_marker == "•" for e in items)


def test_sections_are_the_top_level_and_frames_sit_below_them(tmp_path):
    def heading(tag: str, kind: str, text: str) -> str:
        return (
            f'<{tag} class="ltx_title ltx_title_{kind}">'
            f'<span class="ltx_tag">1 </span>{text}</{tag}>'
        )

    body = (
        '<h1 class="ltx_title ltx_title_document">Deck</h1>'
        + slide("F0", 1, "Before sections", para("a"))
        + heading("h2", "section", "Part")
        + slide("S1.F1", 1, "In the part", para("b"))
        + heading("h3", "subsection", "Detail")
        + slide("S1.SS1.F1", 1, "In the detail", para("c"))
    )
    result = read(body, tmp_path)
    levels = {
        e.text: result.fixes[e.id].heading.level  # type: ignore[union-attr]
        for e in result.elements
        if e.kind is Kind.HEADING
    }
    assert levels == {
        "Deck": 1,
        "Before sections": 2,
        "Part": 1,
        "In the part": 2,
        "Detail": 2,
        "In the detail": 3,
    }
    assert result.title == "Deck"


def test_a_title_page_drawn_inside_an_svg_is_read_for_its_blocks(tmp_path):
    body = (
        '<div class="ltx_para"><p class="ltx_p"><svg xmlns="http://www.w3.org/2000/svg" height="0">'
        '<g><foreignObject><div class="ltx_titlepage">'
        '<h1 class="ltx_title ltx_title_titlepage">Deck Title</h1>'
        '<div class="ltx_authors"><span class="ltx_personname">A. Author</span></div>'
        "</div></foreignObject></g></svg></p></div>"
    )
    result = read(body, tmp_path)
    assert result.title == "Deck Title"
    assert texts(result) == ["Deck Title", "A. Author"]


def test_the_steps_are_not_grouped_when_the_reader_is_not_told_it_is_a_deck(tmp_path):
    body = slide("F1", 1, "Idea", para("Base.")) + slide("F1", 2, "Idea", para("Base."))
    result = reader.read(page(body), tmp_path)  # beamer=False: levels are the page's own
    assert [e.text for e in result.elements if e.kind is Kind.HEADING] == ["Idea"]


# --------------------------------------------------------------------------- intake


def test_a_deck_is_refused_unless_the_switch_is_on():
    with pytest.raises(intake.SourceRefused) as raised:
        intake.inspect("a.tex", DECK.encode())
    assert raised.value.code == "source_beamer"
    info = intake.inspect("a.tex", DECK.encode(), beamer_ok=True)
    assert info.beamer and info.pages == 1


def test_a_deck_counts_a_page_per_frame_or_figure():
    frames = "".join(f"\\begin{{frame}}{{T{i}}}text\\end{{frame}}" for i in range(7))
    head = "\\documentclass{beamer}\\begin{document}\\frame{\\titlepage}"
    deck = head + frames + "\\end{document}"
    assert intake.inspect("a.tex", deck.encode(), beamer_ok=True).pages == 8
    long_frame = "\\begin{frame}" + "word " * 4000 + "\\end{frame}"
    one = f"\\documentclass{{beamer}}\\begin{{document}}{long_frame}\\end{{document}}"
    assert intake.inspect("a.tex", one.encode(), beamer_ok=True).pages == 1  # not by characters


def test_the_switch_is_off_by_default():
    assert get_settings().feature_beamer_source is False


# --------------------------------------------------------------------------- the spike


def deck_blob(name: str) -> bytes:
    text = (FIXTURES / "beamer" / f"{name}.tex").read_text()
    files = {f"{name}.tex": text, "circuit.png": png_bytes()}
    return envelope.wrap("zip", f"{name}.zip", f"{name}.tex", make_zip(files))


@pytest.fixture
def beamer_on(monkeypatch):
    monkeypatch.setenv("FEATURE_BEAMER_SOURCE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@needs_latexml
@pytest.mark.parametrize("name", DECKS)
def test_the_spike_decks_convert_with_no_unknown_command(name, beamer_on):
    result = convert_source(
        deck_blob(name), settings=get_settings(), client=FakeVisionClient()
    ).result
    assert result.origin == "beamer"
    assert not [n for n in result.notices if n.code == "source_error"]  # no frame hit an error
    headings = [e for e in result.elements if e.kind is Kind.HEADING]
    assert len(headings) >= 3
    assert "web version of a slide deck" in result.html
    assert all(f.status is Status.ACCEPTED for f in result.fixes if f.kind is not Kind.FIGURE)


@needs_latexml
def test_the_spike_decks_meet_the_bar_frames_are_sections_and_under_five_percent_fail(beamer_on):
    frames = failed = 0
    for name in DECKS:
        result = convert_source(
            deck_blob(name), settings=get_settings(), client=FakeVisionClient()
        ).result
        text = (FIXTURES / "beamer" / f"{name}.tex").read_text()
        wrote = len(text.split("\\begin{frame}")) - 1 + text.count("\\frame{")
        titled = [
            e
            for e in result.elements
            if e.kind is Kind.HEADING and result.fix_for(e.id).heading.level >= 2  # type: ignore[union-attr]
        ]
        assert len(titled) >= wrote - 1  # every frame has a heading (a title page has the title)
        frames += wrote
        failed += sum(n.count for n in result.notices if n.code == "source_error")
    assert frames >= 20 and failed / frames < 0.05


@needs_latexml
def test_overlays_follow_the_slide_deck_rule_in_a_real_deck(beamer_on):
    result = convert_source(
        deck_blob("kinematics"), settings=get_settings(), client=FakeVisionClient()
    ).result
    headings = [e.text for e in result.elements if e.kind is Kind.HEADING]
    assert "Position and velocity" in headings  # three pauses, one slide
    assert [h for h in headings if h.startswith("Position and velocity")] == [
        "Position and velocity"
    ]
    assert "Constant acceleration (step 2 of 3)" in headings  # the \\only step differs
    assert headings.count("Constant acceleration") == 1
    items = [e.text for e in result.elements if e.kind is Kind.LIST_ITEM]
    assert len(items) == 3  # the three bullets, each once
