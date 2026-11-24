"""The reading-order scorer: a page read as labeled is clean, and each kind of fault is named.
`readablestem eval order`: what a replay keeps of it, and the bar a page made wrong fails."""

from __future__ import annotations

import html
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from readablestem.cli.main import main
from readablestem.eval import corpus, measure, order
from readablestem.eval.measure import Converted
from readablestem.gold import layout_gold
from readablestem.pipeline import assemble, layout

ENTRIES = corpus.load_manifest()
MADE_HERE = [e for e in ENTRIES.values() if e.gold and e.kind != "public" and (e.own)]


def _as_labeled(page: dict) -> list[dict]:
    """The page read exactly as the gold says: one element a block, nothing dropped read."""
    return [
        {"kind": b["role"], "text": b["text"]} for b in page["blocks"] if b["role"] != "dropped"
    ]


def _newsletter() -> tuple[dict, list[dict], list[int]]:
    """A three-column page, its elements as labeled, and where its long paragraphs are."""
    page = corpus.load_gold(ENTRIES["s01_newsletter_3col"])["pages"][0]
    elements = _as_labeled(page)
    long = [i for i, e in enumerate(elements) if len(e["text"].split()) >= 30]
    assert len(long) >= 3
    return page, elements, long


@pytest.mark.parametrize("entry", MADE_HERE, ids=lambda e: e.id)
def test_a_page_read_as_labeled_is_clean(entry: corpus.Entry) -> None:
    gold = corpus.load_gold(entry)
    for row in order.score_document(gold, {p["page"]: _as_labeled(p) for p in gold["pages"]}):
        assert row["clean"] and row["right"], (entry.id, row["page"], row["detail"])
        assert row["faultless"], (entry.id, row["page"], row["detail"])
        assert row["broken"] == row["xjoin"] == 0 and row["opairs_ok"] == row["opairs"]


def test_two_blocks_read_in_the_wrong_order_are_an_order_fault() -> None:
    page, elements, long = _newsletter()
    a, b = long[0], long[1]
    elements[a], elements[b] = elements[b], elements[a]
    row = order.score_page(page, elements)
    assert row["opairs_ok"] < row["opairs"] and not row["clean"]
    assert row["broken"] == 0 and row["found"] == row["blocks"]


def test_two_columns_read_line_by_line_across_are_broken_blocks() -> None:
    page, elements, long = _newsletter()
    a, b = long[0], long[1]
    left, right = elements[a]["text"].split(), elements[b]["text"].split()
    across = []
    for at in range(0, max(len(left), len(right)), 7):
        across += left[at : at + 7] + right[at : at + 7]
    elements[a] = {"kind": "body", "text": " ".join(across)}
    del elements[b]
    row = order.score_page(page, elements)
    assert row["broken"] >= 2 and not row["clean"]
    assert row["flow_ok"] < row["flow_tot"]


def test_a_sidebar_joined_to_a_far_paragraph_is_a_cross_join() -> None:
    page, elements, long = _newsletter()
    a, far = long[0], long[-1]
    assert far - a > 1
    elements[a] = {"kind": "body", "text": elements[a]["text"] + " " + elements[far]["text"]}
    del elements[far]
    row = order.score_page(page, elements)
    assert row["xjoin"] == 1 and not row["clean"]


def test_a_running_header_that_is_read_counts_as_leaked() -> None:
    page, elements, _ = _newsletter()
    header = next(b for b in page["blocks"] if b["role"] == "dropped")
    before = order.score_page(page, elements)["leaked"]
    row = order.score_page(page, [{"kind": "p", "text": header["text"]}, *elements])
    assert row["leaked"] == before + 1
    assert row["furniture"] == 1 and row["right"] and not row["faultless"]


_PARAGRAPH = (
    "Every resistor above absolute zero makes a small random voltage, because its charge "
    "carriers move about with the heat of the material."
)
_NEXT = (
    "The size of that voltage grows with the square root of the resistance, of the "
    "temperature and of the bandwidth the meter listens to."
)
_CAPTIONS = (
    "Figure 1: the noise voltage against resistance at room temperature.",
    "Figure 2: the same measurement with the resistor cooled in liquid nitrogen.",
)


def _report_page() -> tuple[dict, list[dict]]:
    """A page of a heading, two paragraphs and two captioned pictures, and its elements
    as a reader that gets it right gives them."""
    roles = ("heading", "body", "body", "figure", "caption", "figure", "caption")
    texts = ("Thermal noise in a resistor", _PARAGRAPH, _NEXT, "", _CAPTIONS[0], "", _CAPTIONS[1])
    page = {
        "page": 1,
        "blocks": [{"role": role, "text": text} for role, text in zip(roles, texts, strict=True)],
    }
    elements = [
        {"kind": "heading", "text": texts[0]},
        {"kind": "paragraph", "text": _PARAGRAPH},
        {"kind": "paragraph", "text": _NEXT},
        {"kind": "figure", "text": _CAPTIONS[0]},
        {"kind": "figure", "text": _CAPTIONS[1]},
    ]
    assert order.score_page(page, elements)["faultless"]
    return page, elements


def test_a_line_of_the_margin_that_is_read_is_furniture() -> None:
    page, elements = _report_page()
    read = [*elements, {"kind": "paragraph", "text": "Page 12 of 40"}]
    row = order.score_page(page, read)
    assert (row["stray"], row["furniture"]) == (1, 0)  # no one said it is a margin line
    row = order.score_page(page | {"margins": ["Page 12 of 40"]}, read)
    assert (row["stray"], row["furniture"]) == (1, 1) and row["right"] and not row["faultless"]


def test_a_paragraph_read_as_two_elements_is_cut_unless_a_formula_stands_between() -> None:
    page, elements = _report_page()
    said = _PARAGRAPH.split()
    halves = [{"kind": "paragraph", "text": " ".join(part)} for part in (said[:10], said[10:])]
    row = order.score_page(page, [elements[0], *halves, *elements[2:]])
    assert row["cut"] == 1 and row["right"] and not row["faultless"]
    formula = {"kind": "equation", "text": "V = 4 k T R"}
    row = order.score_page(page, [elements[0], halves[0], formula, halves[1], *elements[2:]])
    assert row["cut"] == 0 and row["faultless"]


def test_two_blocks_read_as_one_element_are_merged_and_a_heading_among_them_is_named() -> None:
    page, elements = _report_page()
    both = {"kind": "paragraph", "text": _PARAGRAPH + " " + _NEXT}
    row = order.score_page(page, [elements[0], both, *elements[3:]])
    assert (row["merged"], row["heading_merged"]) == (1, 0) and not row["faultless"]
    titled = {"kind": "paragraph", "text": elements[0]["text"] + " " + _PARAGRAPH}
    row = order.score_page(page, [titled, *elements[2:]])
    assert (row["merged"], row["heading_merged"]) == (1, 1) and not row["faultless"]


def test_a_caption_heard_with_the_other_picture_is_on_the_wrong_one() -> None:
    page, elements = _report_page()
    assert order.score_page(page, elements)["caption_judged"] == 2
    pictures = [{"kind": "figure", "text": ""}, {"kind": "figure", "text": ""}]
    captions = [{"kind": "paragraph", "text": caption} for caption in _CAPTIONS]
    # Both captions after both pictures: the first one is heard beside the second picture.
    row = order.score_page(page, [*elements[:3], *pictures, *captions])
    assert row["caption_wrong"] == 1 and not row["faultless"]


def test_a_page_missing_from_the_output_finds_nothing() -> None:
    gold = corpus.load_gold(ENTRIES["s01_newsletter_3col"])
    (row,) = order.score_document(gold, {})
    assert row["found"] == 0 and row["lost"] == row["blocks"] and not row["clean"]


def test_student_html_is_heard_by_page_without_hidden_text() -> None:
    document = f"""<html><body><nav><p>Skip to content</p></nav><main>
      <h1>cells.pdf</h1>
      <h1 id="p1-e0">Cell structure</h1>
      <p>Cells have <span aria-hidden="true">decorative</span> membranes.</p>
      <p>Water is <math><mi>H</mi><msub><mi></mi><mn>2</mn></msub><mi>O</mi></math> and
        <img src="b.png" alt="E = mc2{assemble.EQUATION_TEXT_ALT_SUFFIX}"> holds.</p>
      <p class="flag-note readablestem" role="note">This equation is shown as an image.</p>
      <ul><li>Organelles<ul><li>Nucleus</li></ul></li></ul>
      <hr role="doc-pagebreak" id="page-2" aria-label="Page 2">
      <table><caption>Sizes</caption><tr><th>Part</th><td>5 nm</td></tr></table>
      <math display="block"><mi>x</mi><mo>=</mo><mn>2</mn></math>
      <figure><img src="a.png" alt="A cell"><figcaption>Figure 1: a cell</figcaption></figure>
      <script>var x = 1;</script>
    </main><footer><p>Made with care</p></footer></body></html>"""
    pages = order.elements_from_html(document)
    # The title the writer adds and its notes are not the document's words.
    first = [(e["kind"], " ".join(e["text"].split())) for e in pages[1]]
    assert first == [
        ("h1", "Cell structure"),
        ("p", "Cells have membranes."),
        # A formula's letters stand side by side, and an equation left as a picture is
        # heard as the PDF's own text of it.
        ("p", "Water is H2O and E = mc2 holds."),
        ("li", "Organelles"),
        ("li", "Nucleus"),
    ]
    # A formula on a line of its own and a picture are each one element.
    assert [(e["kind"], e["text"].split()) for e in pages[2]] == [
        ("table", ["Sizes", "Part", "5", "nm"]),
        ("math", ["x=2"]),
        ("figure", []),
        ("figcaption", ["Figure", "1:", "a", "cell"]),
    ]


def test_a_layout_dump_is_heard_with_inline_equations_and_captions_in_place() -> None:
    records = [
        {"id": "e2", "kind": "paragraph", "page": 1, "order": 1, "text": "so ⟦q1⟧ holds"},
        {"id": "q1", "kind": "equation", "page": 1, "order": 2, "text": "E = m c 2"},
        {"id": "e1", "kind": "heading", "page": 1, "order": 0, "text": "Energy"},
        {"id": "f1", "kind": "figure", "page": 2, "order": 0, "text": "", "caption": "Figure 1"},
    ]
    pages = order.elements_from_layout(records)
    assert [" ".join(e["text"].split()) for e in pages[1]] == ["Energy", "so E = m c 2 holds"]
    assert pages[2][0]["text"].split() == ["Figure", "1"]


def test_what_a_replay_keeps_of_the_order_holds_none_of_the_documents_words() -> None:
    entry = ENTRIES["s01_newsletter_3col"]
    pdf = corpus.document(entry)
    labeled = _as_labeled(corpus.load_gold(entry, pdf)["pages"][0])
    page = "".join(f"<p>{html.escape(element['text'])}</p>" for element in labeled)
    result = SimpleNamespace(elements=layout.analyze(pdf).elements, html=f"<main>{page}</main>")

    kept = measure.order_rows(entry.id, pdf, result)

    assert kept is not None and set(kept) == {"elements", "html"}
    (heard,), (held,) = kept["html"], kept["elements"]
    assert heard["right"] and heard["faultless"] and held["found"] > 0
    words = {key for row in (heard, held) for key, value in row.items() if isinstance(value, str)}
    assert words == {"id"}  # the entry's name: no fault's text is kept
    assert measure.order_rows("w_lecture_5111", pdf, result) is None  # a file with no gold


def _replayed(entry_id: str, *, out_of_order: bool = False) -> Converted:
    """A corpus file as a replay keeps its order: read as labeled, or with the first two
    long paragraphs of its first page the other way round."""
    gold = corpus.load_gold(ENTRIES[entry_id])
    pages = {page["page"]: _as_labeled(page) for page in gold["pages"]}
    if out_of_order:
        first = pages[1]
        a, b = [i for i, e in enumerate(first) if len(e["text"].split()) >= 30][:2]
        first[a], first[b] = first[b], first[a]
    rows = [
        {key: value for key, value in row.items() if key != "detail"}
        for row in order.score_document(gold, pages)
    ]
    return Converted(entry_id, pages=len(rows), order={"html": rows, "elements": rows})


def test_a_page_that_was_right_and_is_not_fails_the_bar(tmp_path, capsys) -> None:
    def kept(name: str, *run: Converted) -> str:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"runs": [[done.as_dict() for done in run]]}))
        return str(path)

    def eval_order(*args: str) -> int:
        return main(["eval", "order", *args])

    before, after = str(tmp_path / "before.json"), str(tmp_path / "after.json")
    glossary = _replayed("s07_glossary")
    good = kept("good", _replayed("s01_newsletter_3col"), glossary)
    bad = kept("bad", _replayed("s01_newsletter_3col", out_of_order=True), glossary)
    assert eval_order("--from", good, "--out", before) == 0

    assert eval_order("--from", bad, "--out", after, "--against", before) == 1
    assert "s01_newsletter_3col page 1" in capsys.readouterr().out
    was, now = (json.loads(Path(path).read_text())["metrics"] for path in (before, after))
    made_here = "self-made"
    assert was["sources"]["html"]["groups"][made_here]["right"] == 3
    assert now["sources"]["html"]["groups"][made_here]["right"] == 2
    # The other way round a page became right, which the bar allows and the report names.
    assert eval_order("--from", good, "--against", after) == 0
    assert "became right: s01_newsletter_3col page 1" in capsys.readouterr().out
    # A record scored by another version of the scorer counts other things.
    older = json.loads(Path(before).read_text())
    older["metrics"]["scorer"] -= 1
    Path(before).write_text(json.dumps(older))
    assert eval_order("--from", good, "--against", before) == 2


def test_a_layout_gold_set_is_scored_with_no_model_and_a_held_out_one_only_for_a_release(
    tmp_path,
) -> None:
    entry = ENTRIES["s01_newsletter_3col"]
    root = tmp_path / "set"
    (root / "files").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "files" / "news.pdf").write_bytes(corpus.document(entry))
    gold = corpus.load_gold(entry) | {"id": "news", "kind": "made here", "source": "a test"}
    (root / "gold" / "news.json").write_text(json.dumps(gold))
    # A file the reader refuses is named as not measured, and the others are still scored.
    (root / "files" / "torn.pdf").write_bytes(b"%PDF-1.4 torn")
    (root / "gold" / "torn.json").write_text(json.dumps(gold | {"id": "torn"}))

    def eval_order(side: str, *args: str, written: str = "") -> tuple[int, dict]:
        written = written or side
        registry, out = tmp_path / f"{written}.json", tmp_path / f"{written}-record.json"
        listed = layout_gold.registry_of(root, "tiny", "a test", side)
        registry.write_text(json.dumps(listed | {"side": written}))
        named = ["--set", "tiny", "--root", str(root), "--registry", str(registry)]
        code = main(["eval", "order", *named, "--out", str(out), *args])
        return code, json.loads(out.read_text()) if out.is_file() else {}

    code, record = eval_order("development")
    assert code == 0 and record["set"] == "tiny" and record["split"]["name"] == "development"
    student = record["metrics"]["sources"]["html"]
    assert student["all"]["pages"] == 1 and set(student["groups"]) == {"made here"}
    assert [page["page"] for page in record["metrics"]["pages"]] == [1]
    assert record["metrics"]["uploads"]["not_measured"] == ["torn"]

    # A held-out set is not scored while the reader is worked on, and its record names no page.
    assert eval_order("held-out") == (2, {})
    code, record = eval_order("held-out", "--for-release")
    assert code == 0 and record["split"]["name"] == "held-out"
    assert record["metrics"]["sources"]["html"]["all"]["pages"] == 1
    assert record["metrics"]["pages"] == []
    # A registry with any other word for its side is refused, for a release too.
    assert eval_order("held-out", "--for-release", written="heldout") == (2, {})
