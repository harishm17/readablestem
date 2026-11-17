"""Reading-order gold from a source: units from the source, blocks from the PDF's lines."""

from __future__ import annotations

import json

import pymupdf
import pytest

from readablestem.cli.main import main
from readablestem.gold import layout_gold

# A page written for this test: a running header, a heading, three paragraphs in two columns
# (the second is cut by the column break), a figure caption and a footnote.
HEADER = "Physics 101 lecture notes, week four"
UNITS = [
    {"role": "heading", "text": "Thermal expansion of metal rods"},
    {
        "role": "body",
        "text": "A metal rod grows longer when it is heated. For small changes the growth "
        "follows § where the constant depends on the metal. Brass grows almost twice as "
        "much as steel for the same change in temperature.",
    },
    {
        "role": "body",
        "text": "Engineers leave gaps between the rails of a railway for this reason. "
        "Without a gap the rails would press against each other on a hot afternoon and "
        "bend sideways. A bridge rests on rollers at one end so that its deck can move "
        "freely while the seasons change.",
    },
    {
        "role": "caption",
        "text": "Figure 2. Length of the brass rod against temperature",
        "float": True,
    },
    {
        "role": "body",
        "text": "In the laboratory you will clamp one end of a rod and read the other end "
        "with a dial gauge while steam passes through a jacket around it.",
    },
    {
        "role": "footnote",
        "text": "The gauge reads to a hundredth of a millimetre at room temperature.",
    },
]
LEFT = [
    "Thermal expansion of metal rods",
    "",
    "A metal rod grows longer when it is heated.",
    "For small changes the growth follows",
    "dL = a L dT where the constant depends",
    "on the metal. Brass grows almost twice as",
    "much as steel for the same change in",
    "temperature.",
    "",
    "Engineers leave gaps between the rails of",
    "a railway for this reason. Without a gap the",
    "rails would press against each other on a",
]
RIGHT = [
    "hot afternoon and bend sideways. A bridge",
    "rests on rollers at one end so that its deck",
    "can move freely while the seasons change.",
    "",
    "Figure 2. Length of the brass rod against",
    "temperature",
    "",
    "In the laboratory you will clamp one end of",
    "a rod and read the other end with a dial",
    "gauge while steam passes through a jacket",
    "around it.",
]
FOOTNOTE = "The gauge reads to a hundredth of a millimetre at room temperature."


def page_pdf() -> pymupdf.Document:
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 40), HEADER, fontsize=9)
    for x, column in ((72, LEFT), (320, RIGHT)):
        for n, line in enumerate(column):
            if line:
                page.insert_text((x, 100 + 14 * n), line, fontsize=10)
    page.insert_text((72, 765), FOOTNOTE, fontsize=8)
    return document


@pytest.fixture(scope="module")
def blocks() -> list[dict]:
    gold = layout_gold.generated_gold("sm_test_a", page_pdf(), UNITS, source="this test")
    (page,) = gold["pages"]
    return page["blocks"]


def test_blocks_follow_the_source_and_a_column_break_cuts_a_paragraph_in_two(blocks):
    assert [(block["role"], block["unit"]) for block in blocks] == [
        ("heading", 0),
        ("body", 1),
        ("body", 2),
        ("body", 2),
        ("caption", 3),
        ("body", 4),
        ("footnote", 5),
    ]
    first, second = blocks[2], blocks[3]
    assert first["text"].startswith("Engineers leave gaps")
    assert second["text"].startswith("hot afternoon and bend sideways")
    assert second["text"].endswith("while the seasons change.")


def test_a_running_header_is_in_no_block(blocks):
    assert not any("lecture notes" in block["text"] for block in blocks)


def test_glyphs_the_source_does_not_hold_are_masked_for_the_scorer(blocks):
    paragraph = blocks[1]
    assert "dL = a L dT" in paragraph["text"]
    assert "dL" not in paragraph["mtext"]
    assert layout_gold.MASK in paragraph["mtext"]
    assert "where the constant depends" in paragraph["mtext"]


def test_a_float_caption_is_marked_so_that_its_place_is_not_scored(blocks):
    assert [bool(block.get("float")) for block in blocks] == [
        False,
        False,
        False,
        False,
        True,
        False,
        False,
    ]


def test_units_of_a_paper_come_from_the_html_latexml_makes():
    html = """
    <article class="ltx_document">
      <h1 class="ltx_title ltx_title_document">Rods and rails<span class="ltx_note
        ltx_role_footnote"><span class="ltx_note_content">Supported by a grant.</span></span></h1>
      <div class="ltx_authors">Ada Example, Northern College</div>
      <section><h2 class="ltx_title ltx_title_section">1 Introduction</h2>
        <div class="ltx_para"><p class="ltx_p">Growth follows <math alttext="x">x</math> for
          small changes.</p></div>
        <table class="ltx_equation"><tr><td>E = mc</td></tr></table>
        <ul><li class="ltx_item"><div class="ltx_para"><p class="ltx_p">Brass grows
          most.</p></div></li></ul>
        <figure class="ltx_figure"><p class="ltx_p">axis label</p>
          <figcaption>Figure 1: A heated rod.</figcaption></figure>
      </section>
      <ul><li class="ltx_bibitem">Example, A. Rods. 2020.</li></ul>
    </article>"""
    units = layout_gold.units_latexml(html)
    assert [(unit["role"], unit["text"]) for unit in units] == [
        ("heading", "Rods and rails"),
        ("footnote", "Supported by a grant."),
        ("front", "Ada Example, Northern College"),
        ("heading", "1 Introduction"),
        ("body", f"Growth follows {layout_gold.BRK} for small changes."),
        ("list", "Brass grows most."),
        ("caption", "Figure 1: A heated rod."),
        ("list", "Example, A. Rods. 2020."),
    ]
    assert units[6]["float"]


def test_units_of_a_textbook_section_come_from_the_archive_xhtml():
    xhtml = """
    <body><section>
      <h2>Thermal expansion</h2>
      <p>Most solids expand<sup><a role="doc-noteref" href="#n1">1</a></sup> when heated.</p>
      <div data-type="equation"><math><mi>x</mi></math></div>
      <ul><li>Steel expands least.</li></ul>
      <figure><img src="rod.png" alt=""/></figure>
      <div class="os-caption-container">Figure 4.2 A rod in a steam jacket.</div>
      <dl><dt>strain</dt><dd>the change in length over the length</dd></dl>
      <div class="os-index-item"><span class="os-term">thermal stress</span><a>212</a></div>
      <aside role="doc-footnote">1 Water below four degrees does not.</aside>
    </section></body>"""
    units = layout_gold.units_openstax(xhtml, "4-1-expansion")
    assert [(unit["role"], unit["text"]) for unit in units] == [
        ("heading", "Thermal expansion"),
        ("body", "Most solids expand when heated."),
        ("list", "Steel expands least."),
        ("caption", "Figure 4.2 A rod in a steam jacket."),
        ("list", "strain the change in length over the length"),
        ("list", "thermal stress"),
        ("footnote", "1 Water below four degrees does not."),
    ]
    assert all(unit["section"] == "4-1-expansion" for unit in units)


def test_a_paper_page_the_source_does_not_account_for_is_not_kept(blocks):
    page = {"page": 1, "blocks": blocks, "coverage": 0.6}
    paper = {"id": "ax_test", "kind": "paper", "source": "this test", "pages": [page]}
    assert layout_gold.keep_pages(paper) is None
    assert layout_gold.keep_pages({**paper, "kind": "selfmade"}) is not None


@pytest.fixture
def set_folder(tmp_path):
    root = tmp_path / "set"
    (root / "gold").mkdir(parents=True)
    (root / "files").mkdir()
    gold = layout_gold.generated_gold("sm_test_a", page_pdf(), UNITS, source="this test")
    (root / "gold" / "sm_test_a.json").write_text(json.dumps(gold), encoding="utf-8")
    page_pdf().save(root / "files" / "sm_test_a.pdf")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(layout_gold.registry_of(root, "t", "a test")), encoding="utf-8")
    return root, registry


def verify(root, registry) -> int:
    return main(
        ["gold", "layout", "verify", "--set", "t", "--root", str(root), "--registry", str(registry)]
    )


def test_a_set_is_checked_against_its_registry_which_holds_no_text(set_folder, capsys):
    root, registry = set_folder
    assert verify(root, registry) == 0
    assert "Engineers" not in registry.read_text(encoding="utf-8")
    path = root / "gold" / "sm_test_a.json"
    gold = json.loads(path.read_text(encoding="utf-8"))
    gold["pages"][0]["blocks"].reverse()
    path.write_text(json.dumps(gold), encoding="utf-8")
    assert verify(root, registry) == 1
    assert "sm_test_a: the gold differs" in capsys.readouterr().err


def test_a_kind_whose_checked_sample_is_under_the_bar_is_not_used_as_gold(
    set_folder, tmp_path, capsys
):
    root, _registry = set_folder
    rows = layout_gold.write_sample(root, "seed", tmp_path / "sample")
    assert {(row["id"], row["page"]) for row in rows} == {("sm_test_a", 1)}
    assert (tmp_path / "sample" / rows[0]["picture"]).is_file()
    sheet = tmp_path / "sample" / "sheet.jsonl"

    def filled(wrong: int) -> int:
        marked = [{**row, "ok": n >= wrong} for n, row in enumerate(rows)]
        sheet.write_text("".join(json.dumps(row) + "\n" for row in marked), encoding="utf-8")
        return main(["gold", "layout", "agreement", str(sheet)])

    assert main(["gold", "layout", "agreement", str(sheet)]) == 1  # nothing checked yet
    assert filled(wrong=0) == 0
    assert filled(wrong=1) == 1  # 6 of 7 blocks
    assert "not usable as gold" in capsys.readouterr().out
