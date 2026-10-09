"""The figure card on the review screen with long descriptions on: the chart's data table
as students will get it, the long description to read and edit, and "Remove the data
table"; Accept and Reject as for any figure. Uses the real-shaped document of
fixtures.web.review_doc, with its figure (decision 2, p1-e6) given a long description
and a data table whose values were read from the image (a required decision)."""

from __future__ import annotations

import re

import pytest

from a11y_stem.core.types import Check, ConversionOutput, FigureFix, ModelCall, Status
from a11y_stem.pipeline import figure_details
from a11y_stem.pipeline.figure_step import LONG_PROMPT_VERSION
from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, upload
from test_web_review_split import panel, post, screen, stored, text_of

ALT = "A car on a road between a house and a school."
LONG = (
    "Diagram of a road from a house on the left to a school on the right.\n"
    "A car is drawn halfway along the road, moving towards the school."
)
VALUES = Check(
    name="figure.data_values", passed=False, severity="warning", code="values_from_image"
)


def described_output() -> ConversionOutput:
    output = review_output()
    fix = output.result.fix_for("p1-e6")
    assert fix is not None
    fix.figure = FigureFix(
        figure_type="chart",
        alt=ALT,
        long_description=LONG,
        data_table=figure_details.data_grid(
            ["Time (min)", "Distance (km)"], [["0", "0"], ["5", "1.5"], ["10", "3"]]
        ),
        data_values_from_text=False,
    )
    fix.checks = [VALUES]
    return output


@pytest.fixture
def card(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = described_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def figure_card(env) -> str:
    return panel(screen(env, "?item=p1-e6"))


def download(env) -> str:
    return env.browser.get(f"/jobs/{env.job_id}/download").text


SHOWN = {"alt": ALT, "long_description": LONG}


# ---------------------------------------------------------------------- what is shown


def test_the_card_shows_the_table_the_description_and_why_it_asks(card):
    body = figure_card(card)
    assert (
        '<div class="table-scroll" role="group" aria-labelledby="review-p1-e6-data-caption" '
        'tabindex="0"><table><caption id="review-p1-e6-data-caption">Data read from Figure 1; '
        "the values are approximate</caption>"
    ) in body
    assert '<th scope="row">5</th><td>1.5</td>' in body
    textarea = re.search(r'<textarea id="longdesc-p1-e6"[^>]*>([^<]*)</textarea>', body)
    assert textarea and textarea.group(1) == LONG
    assert '<label for="longdesc-p1-e6">Longer description for figure 1</label>' in body
    assert '<input type="checkbox" id="datatable-p1-e6" name="remove_data_table"' in body
    assert "Remove the data table" in body
    assert "were read from the image, not from text in the PDF" in text_of(body)
    assert "Accept" in body and "Keep without a description" in body  # Reject, named


def test_a_figure_without_them_shows_neither(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        body = figure_card(env)
    assert "longdesc-" not in body and "remove_data_table" not in body
    assert "<table" not in body


def missing_output() -> ConversionOutput:
    """The figure as a chart described with long descriptions on, left without one (the
    notice `long_description_missing`)."""
    output = review_output()
    fix = output.result.fix_for("p1-e6")
    assert fix is not None
    fix.figure = FigureFix(figure_type="chart", alt=ALT)
    call = ModelCall(step="figure", model="gpt-6.1-sol", prompt_version=LONG_PROMPT_VERSION)
    fix.calls = [call]
    return output


def test_a_chart_left_without_one_offers_an_empty_field_to_write_it(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = missing_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        body = figure_card(env)
        textarea = re.search(r'<textarea id="longdesc-p1-e6"[^>]*>([^<]*)</textarea>', body)
        assert textarea and textarea.group(1) == ""
        assert "remove_data_table" not in body and "<table" not in body
        # Accept with the box left empty is no change; with text in it, Save edit first.
        assert post(env, "p1-e6", action="accept", alt=ALT, long_description="").status_code == 303
        fix = stored(env).fix_for("p1-e6")
        assert fix is not None and fix.figure is not None
        assert fix.figure.long_description is None
        response = post(env, "p1-e6", action="edit", alt=ALT, long_description=LONG)
        assert response.status_code == 303
        fix = stored(env).fix_for("p1-e6")
        assert fix is not None and fix.figure is not None
        assert fix.figure.long_description == LONG
        assert "Description of Figure 1</summary>" in download(env)


# ---------------------------------------------------------------------- decisions


def test_accept_keeps_the_table_and_the_student_file_says_approximate(card):
    assert post(card, "p1-e6", action="accept", **SHOWN).status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.status is Status.ACCEPTED and fix.reviewed
    assert fix.figure is not None and fix.figure.data_table is not None
    html = download(card)
    assert "Description of Figure 1</summary>" in html
    assert "Data read from Figure 1; the values are approximate</caption>" in html


def test_remove_the_data_table_keeps_the_descriptions(card):
    response = post(card, "p1-e6", action="edit", remove_data_table="on", **SHOWN)
    assert response.status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.status is Status.EDITED
    assert fix.figure is not None and fix.figure.alt == ALT
    assert fix.figure.long_description == LONG
    assert fix.figure.data_table is None and fix.figure.data_values_from_text is None
    assert "figure.data_values" not in {c.name for c in fix.checks}
    html = download(card)
    assert "<table" not in html and "Description of Figure 1</summary>" in html


def test_saving_a_new_description_cleans_it(card):
    typed = "  A road from a house to a school,​ with a car on it.\r\n\r\nIt moves right.  "
    response = post(card, "p1-e6", action="edit", alt=ALT, long_description=typed)
    assert response.status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None
    assert fix.figure.long_description == (
        "A road from a house to a school, with a car on it.\nIt moves right."
    )
    assert fix.figure.data_table is not None  # kept: not asked to remove it
    assert [c.code for c in fix.failed_checks] == ["values_from_image"]  # still true of it


def test_an_empty_description_removes_it(card):
    assert post(card, "p1-e6", action="edit", alt=ALT, long_description=" ").status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None and fix.figure.long_description is None


def test_a_description_that_is_markup_is_not_saved(card):
    typed = "<script>alert(1)</script> and a link to https://example.org for more."
    response = post(card, "p1-e6", action="edit", alt=ALT, long_description=typed)
    assert response.status_code == 422
    assert '<a href="#longdesc-p1-e6">' in response.text
    body = panel(response.text)
    assert "The longer description was not saved" in text_of(body)
    assert "&lt;script&gt;alert(1)&lt;/script&gt; and a link" in body  # re-shown, escaped
    assert re.search(r'<textarea id="longdesc-p1-e6"[^>]*aria-invalid="true"', body)
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.status is Status.NEEDS_REVIEW  # nothing saved


@pytest.mark.parametrize(
    ("fields", "message", "field"),
    [
        ({"long_description": "A different description of the road and the car."},
         "You changed the longer description.", "longdesc-p1-e6"),
        ({"long_description": LONG, "remove_data_table": "on"},
         "You chose to remove the data table.", "datatable-p1-e6"),
    ],
)  # fmt: skip
def test_accept_refuses_an_unsaved_change(card, fields, message, field):
    response = post(card, "p1-e6", action="accept", alt=ALT, **fields)
    assert response.status_code == 422
    assert message in text_of(response.text)
    assert f'<a href="#{field}">' in response.text
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.status is Status.NEEDS_REVIEW


def test_accept_with_the_description_as_shown_accepts(card):
    shown = LONG.replace("\n", "\r\n") + "\r\n"  # how a browser sends the textarea back
    response = post(card, "p1-e6", action="accept", alt=ALT, long_description=shown)
    assert response.status_code == 303


def test_an_alt_edit_from_the_list_view_keeps_both(card):
    response = post(card, "p1-e6", action="edit", alt="A car on the road to school.", view="list")
    assert response.status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None
    assert fix.figure.long_description == LONG and fix.figure.data_table is not None


def test_marking_it_decorative_drops_both(card):
    response = post(card, "p1-e6", action="edit", alt="", decorative="on", long_description=LONG)
    assert response.status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None and fix.figure.decorative
    assert fix.figure.long_description is None and fix.figure.data_table is None


def test_reject_keeps_the_figure_flagged_without_its_details(card):
    assert post(card, "p1-e6", action="reject", **SHOWN).status_code == 303
    html = download(card)
    assert "<details" not in html and "Needs a description." in html
