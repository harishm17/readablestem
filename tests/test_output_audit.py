"""The output audit counts what is wrong in a built file: each kind once per place, and
nothing in a file that is right."""

import json
from collections import Counter

import pytest

from readablestem.cli.main import main
from readablestem.core.types import (
    DocumentResult,
    DocumentStats,
    Element,
    FigureFix,
    Fix,
    Kind,
    Status,
)
from readablestem.eval import outputs
from readablestem.eval.measure import Converted
from readablestem.pipeline import export_docx, output_audit
from readablestem.pipeline.output_audit import CODES, Facts

# A 1 x 1 picture.
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAj"
    "CB0C8AAAAASUVORK5CYII="
)

# A page in the shape the renderer writes, with one of everything the audit reads.
PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Motion on a slope</title>
</head>
<body>
<a class="skip-link readablestem" href="#main-content">Skip to main content</a>
<span id="main-content" tabindex="-1"></span>
<main>
<div class="pagebreak readablestem"><hr role="doc-pagebreak" aria-label="Page 1" \
id="pagebreak-1"><span aria-hidden="true">Page 1</span></div>
<h1 id="p1-e0">Motion on a slope</h1>
<p id="p1-e1">A cart rolls down a smooth slope from rest.\
<a id="noteref-p2-e5" role="doc-noteref" href="#p2-e5">1</a></p>
<h2 id="p1-e2">Setting up the problem</h2>
<p id="p1-e3">The speed is <math display="inline" id="p1-e4"><mi>v</mi></math> at the \
bottom of the slope.</p>
<p id="p1-e5">Press <img src="{PNG}" alt="the red button" width="10" height="10">, then \
release the cart.</p>
<figure id="p1-e6"><img src="{PNG}" alt="A cart at the top of a slope." width="10" \
height="10"><figcaption>Figure 1. The cart before it is released.</figcaption></figure>
<p id="p1-e7">See <a href="#p1-e6">Figure 1</a> for the starting position of the cart.</p>
<div class="pagebreak readablestem"><hr role="doc-pagebreak" aria-label="Page 2" \
id="pagebreak-2"><span aria-hidden="true">Page 2</span></div>
<h2 id="p2-e0">Measured values</h2>
<div class="table-scroll" id="p2-e1" role="group" aria-label="Table" tabindex="0"><table>\
<thead><tr><th scope="col">Trial</th><th scope="col">Time in seconds</th></tr></thead>\
<tbody><tr><td>First</td><td>2.1</td></tr></tbody></table></div>
<p id="p2-e2">The first trial was the slowest of the three trials.</p>
<p id="p2-e3">The second trial was faster than the first trial.</p>
<div class="flagged" id="p2-e4" data-status="failed"><img src="{PNG}" \
alt="A hand sketch of the track." width="10" height="10">\
<p class="flag-note readablestem" role="note"><span class="flag-label">Not converted.</span> \
This sketch is shown as a picture of the original.</p></div>
<div role="doc-footnote" id="p2-e5"><p>The slope is three metres long. \
<a role="doc-backlink" href="#noteref-p2-e5">Back to the text</a></p></div>
</main>
<footer id="about-conversion" class="about readablestem">
<p>This is a web version of a PDF from your course.</p>
</footer>
</body>
</html>
"""

FACTS = Facts(
    lang="en",
    order=(
        *(f"p1-e{n}" for n in range(8)),
        *(f"p2-e{n}" for n in range(6)),
    ),
    open=frozenset(),
    kept=frozenset({"p2-e4"}),
)


def _changed(page: str, *changes: tuple[str, str]) -> str:
    for old, new in changes:
        assert page.count(old) == 1, old
        page = page.replace(old, new)
    return page


SEE = '<p id="p1-e7">See <a href="#p1-e6">Figure 1</a> for the starting position of the cart.</p>'
FIRST = '<p id="p2-e2">The first trial was the slowest of the three trials.</p>\n'
SECOND = '<p id="p2-e3">The second trial was faster than the first trial.</p>\n'

# One defect of each kind, in a file made from the page.
EXPORT_DEFECTS = (
    ('alt="A cart at the top of a slope."', 'alt="A cart \ue000 at the top."'),  # out_char
    ('<h2 id="p1-e2">Setting up the problem</h2>\n', ""),  # out_text_lost
    (  # out_text_twice
        SEE,
        SEE + "<p>See Figure 1 for the starting position of the cart.</p>",
    ),
    (FIRST + SECOND, SECOND + FIRST),  # out_order
    ('<math display="inline" id="p1-e4"><mi>v</mi></math>', "v"),  # out_math_fallback
    ('href="#p1-e6"', 'href="#nowhere"'),  # out_link_dead
    (' alt="A hand sketch of the track."', ""),  # out_alt_missing
    (  # out_table_header
        '<thead><tr><th scope="col">Trial</th><th scope="col">Time in seconds</th></tr></thead>',
        "<thead><tr><td>Trial</td><td>Time in seconds</td></tr></thead>",
    ),
    ('<h2 id="p2-e0">Measured values</h2>', '<h4 id="p2-e0">Measured values</h4>'),  # out_heading
    ('<html lang="en">', "<html>"),  # out_lang
    ("<p>The slope is three metres long.", ""),  # out_note_lost
    (f'<img src="{PNG}" alt="the red button" width="10" height="10">, then', " , then"),  # out_hole
    (  # out_notice_missing
        '<p class="flag-note readablestem" role="note"><span class="flag-label">Not converted.'
        "</span> This sketch is shown as a picture of the original.</p>",
        "",
    ),
)

# One defect of each kind the page itself can have, against the stored result.
PAGE_DEFECTS = (
    ('alt="A cart at the top of a slope."', 'alt="A cart \ue000 at the top."'),  # out_char
    ('<h2 id="p1-e2">Setting up the problem</h2>\n', ""),  # out_text_lost
    (  # out_text_twice
        SEE,
        SEE + '<p id="p1-e1">A cart rolls down a smooth slope from rest.</p>',
    ),
    (FIRST + SECOND, SECOND + FIRST),  # out_order
    (  # out_math_fallback: an equation kept as a picture that says nothing
        '<math display="inline" id="p1-e4"><mi>v</mi></math>',
        f'<span class="flagged-inline" id="p1-e4"><img src="{PNG}" alt=""></span>',
    ),
    ('href="#p1-e6"', 'href="#nowhere"'),  # out_link_dead
    (' alt="the red button"', ""),  # out_alt_missing
    (  # out_table_header
        '<thead><tr><th scope="col">Trial</th><th scope="col">Time in seconds</th></tr></thead>',
        "<thead><tr><td>Trial</td><td>Time in seconds</td></tr></thead>",
    ),
    ('<h2 id="p2-e0">Measured values</h2>', '<h4 id="p2-e0">Measured values</h4>'),  # out_heading
    ('<html lang="en">', "<html>"),  # out_lang
    ("three metres long. ", "three the -metres long. "),  # out_hole
    (  # out_notice_missing: a kept picture with nothing to say so
        '<div class="flagged" id="p2-e4" data-status="failed">',
        '<div id="p2-e4">',
    ),
    (
        '<p class="flag-note readablestem" role="note"><span class="flag-label">Not converted.'
        "</span> This sketch is shown as a picture of the original.</p>",
        "",
    ),
)


@pytest.mark.parametrize(
    ("output", "defects", "kinds"),
    [
        (output_audit.CANVAS_INDEX, EXPORT_DEFECTS, CODES),
        # A note that the page lost is an element that the page lost: `out_text_lost`.
        (output_audit.HTML, PAGE_DEFECTS, tuple(c for c in CODES if c != "out_note_lost")),
    ],
)
def test_one_defect_of_each_kind_is_one_count_of_each(output, defects, kinds):
    page = output_audit.read_html(PAGE)
    built = output_audit.read_html(_changed(PAGE, *defects), output)

    counts = Counter(count.code for count in output_audit.audit(built, page, FACTS))

    assert counts == dict.fromkeys(kinds, 1)


def test_the_built_files_of_a_page_that_is_right_have_no_count():
    audit = outputs.audit_files(PAGE, FACTS, {})

    assert audit["places"] == []
    assert set(audit["failed"]) <= (set() if export_docx.available() else {output_audit.DOCX})
    assert set(audit["sizes"]) | set(audit["failed"]) == set(output_audit.OUTPUTS)


def test_the_report_adds_up_the_places_and_keeps_a_short_recording_apart(tmp_path, capsys):
    # Two uploads as a replay measured them: a page with every defect, and a page that is
    # right but whose recording lacked answers, which is no evidence either way.
    wrong = outputs.audit_files(_changed(PAGE, *PAGE_DEFECTS), FACTS, {})
    right = outputs.audit_files(PAGE, FACTS, {})
    run = [
        Converted("w_lecture_5111", pages=2, outputs=wrong),
        Converted("w_scan_slides+image_pages", pages=1, outputs=right, missed=3),
        Converted("w_pset_801", pages=4),  # a record kept before the files were audited
    ]
    records, out = tmp_path / "records.json", tmp_path / "outputs.json"
    records.write_text(json.dumps({"runs": [[done.as_dict() for done in run]]}))

    assert main(["eval", "outputs", "--from", str(records), "--out", str(out)]) == 0

    metrics = json.loads(out.read_text())["metrics"]
    assert metrics["uploads"] == {
        "measured": 1,
        "answers_missing": {"w_scan_slides+image_pages": 3},
        "not_measured": ["w_pset_801"],
    }
    whole = metrics["whole"]
    assert (whole["uploads"], whole["pages"]) == (1, 2)
    page_kinds = [code for code in CODES if code != "out_note_lost"]
    assert [code for code in CODES if whole["counts"][code][output_audit.HTML]] == page_kinds
    assert whole["documents"]["out_notice_missing"] == 1
    assert whole["by_upload"] == [{"upload": "w_lecture_5111", "places": len(wrong["places"])}]
    assert "1 uploads whose recording lacks answers" in capsys.readouterr().out


def _figure(n: int, box, fix: Fix | None) -> tuple[Element, Fix | None]:
    element = Element(id=f"p1-e{n}", kind=Kind.FIGURE, page=0, bbox=box, order=n)
    return element, fix


def test_the_pictures_report_counts_what_was_not_converted_and_reads_the_truth_list(
    tmp_path, capsys
):
    # One page, four figures: one described, one region called handwriting, one a model
    # refused, one called handwriting that a person saw as printed. Its page has a content
    # picture with empty alt.
    described = Fix(
        element_id="p1-e0",
        kind=Kind.FIGURE,
        status=Status.ACCEPTED,
        figure=FigureFix(figure_type="photo", alt="A cart."),
    )
    kept = lambda n, reason: Fix(  # noqa: E731
        element_id=f"p1-e{n}", kind=Kind.FIGURE, status=Status.FAILED, reason=reason
    )
    pen, printed = (10, 10, 60, 40), (100, 100, 160, 140)
    parts = [
        _figure(0, (0, 200, 50, 250), described),
        _figure(1, pen, kept(1, "handwriting_on_page")),
        _figure(2, (0, 300, 50, 350), kept(2, "model_refused")),
        _figure(3, printed, kept(3, "handwriting_on_page")),
    ]
    result = DocumentResult(
        pipeline_version="test",
        elements=[element for element, _ in parts],
        fixes=[fix for _, fix in parts if fix is not None],
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        html=_changed(PAGE, ('alt="the red button"', 'alt=""')),
    )
    run = [
        Converted("r01_syllabus_16851", pages=1, outputs=outputs.audit_document(result)),
        Converted("w_pset_801", pages=4),  # a record kept before the pictures were counted
    ]
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps(
            {
                "regions": [
                    {
                        "document": "r01_syllabus_16851",
                        "page": 1,
                        "bbox": [12, 9, 58, 41],
                        "label": "pen",
                    },
                    {
                        "document": "r01_syllabus_16851",
                        "page": 1,
                        "bbox": printed,
                        "label": "not_pen",
                    },
                ]
            }
        )
    )
    records, out = tmp_path / "records.json", tmp_path / "pictures.json"
    records.write_text(json.dumps({"runs": [[done.as_dict() for done in run]]}))

    code = main(
        ["eval", "pictures", "--from", str(records), "--truth", str(truth), "--out", str(out)]
    )

    assert code == 0
    metrics = json.loads(out.read_text())["metrics"]
    audit = metrics["sets"]["audit"]["whole"]
    assert (audit["documents"], audit["images"], audit["empty_alt"]) == (1, 4, 1)
    assert audit["not_converted"] == {"handwriting_on_page": 2, "model_refused": 1}
    assert audit["handwriting_regions"] == 2
    assert metrics["uploads"]["not_counted"] == ["w_pset_801"]
    truth_counts = metrics["truth"]
    assert (truth_counts["pen"], truth_counts["not_pen"], truth_counts["unlabelled"]) == (1, 1, 0)
    assert "as something else 1" in capsys.readouterr().out
