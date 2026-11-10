"""The figure card on the review screen with long descriptions on: the long description to
read and edit; a chart data table whose values were read from the image offered as an
optional extra ("Add the data table"), never the decision; a table in the student file
shown as students get it, with "Remove the data table"; Accept and Reject as for any
figure. Uses the real-shaped document of fixtures.web.review_doc, with its figure
(decision 2, p1-e6: its alt text needs a decision) given a long description and a data
table whose values were read from the image."""

from __future__ import annotations

import re

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, upload
from readablestem.core.types import Check, ConversionOutput, FigureFix, ModelCall, Status
from readablestem.pipeline import figure_details
from readablestem.pipeline.figure_step import LONG_PROMPT_VERSION
from test_web_review_split import panel, post, screen, stored, text_of

ALT = "A car on a road between a house and a school."
LONG = (
    "Diagram of a road from a house on the left to a school on the right.\n"
    "A car is drawn halfway along the road, moving towards the school."
)
VALUES = Check(
    name="figure.data_values", passed=False, severity="warning", code="values_from_image"
)


def described_output(*, added: bool = False) -> ConversionOutput:
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
        data_table_added=added,
    )
    fix.checks = [VALUES]
    return output


def _card(tmp_path, output: ConversionOutput):
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


@pytest.fixture
def card(tmp_path):
    """The table read from the image, not added: offered on the card."""
    yield from _card(tmp_path, described_output())


@pytest.fixture
def added(tmp_path):
    """The same table, already added by a reviewer: in the student file."""
    yield from _card(tmp_path, described_output(added=True))


def optional_output() -> ConversionOutput:
    """The figure accepted automatically, its only note the table read from the image
    (the tier the figure step gives it: optional, never a decision)."""
    output = described_output()
    fix = output.result.fix_for("p1-e6")
    assert fix is not None
    fix.status, fix.review, fix.confidence = Status.ACCEPTED, "optional", 0.93
    return output


@pytest.fixture
def optional(tmp_path):
    yield from _card(tmp_path, optional_output())


def figure_card(env) -> str:
    return panel(screen(env, "?item=p1-e6"))


def download(env) -> str:
    return env.browser.get(f"/jobs/{env.job_id}/download").text


SHOWN = {"alt": ALT, "long_description": LONG}


# ---------------------------------------------------------------------- what is shown


OFFER = (
    "A data table was read from the chart image; the values may be off. Add it if you check them."
)


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


def test_accept_keeps_the_offered_table_out_of_the_student_file(card):
    assert post(card, "p1-e6", action="accept", **SHOWN).status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.status is Status.ACCEPTED and fix.reviewed
    assert fix.figure is not None and fix.figure.data_table is not None  # still offered
    assert not fix.figure.data_table_added
    html = download(card)
    assert "Description of Figure 1</summary>" in html
    assert "<table" not in html


def test_add_the_data_table_puts_it_in_the_file_and_keeps_the_decision_open(card):
    response = post(card, "p1-e6", action="add_data_table")
    assert response.status_code == 303
    assert response.headers["location"].endswith("?item=p1-e6")  # stays on the figure
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None and fix.figure.data_table_added
    # The alt text still waits for its decision: adding the table decides nothing else.
    assert fix.status is Status.NEEDS_REVIEW and not fix.reviewed
    html = download(card)
    assert "Data read from Figure 1; the values are approximate</caption>" in html
    page = screen(card, "?item=p1-e6")
    assert "The data table was added" in text_of(page)
    body = panel(page)
    assert "add_data_table" not in body and "Remove the data table" in body
    # Asking again changes nothing and says why.
    again = post(card, "p1-e6", action="add_data_table")
    assert again.status_code == 422
    assert "There is no data table to add" in text_of(again.text)


def test_undo_takes_the_added_table_out_again(optional):
    from fixtures.web.support import token

    assert post(optional, "p1-e6", action="add_data_table").status_code == 303
    url = f"/jobs/{optional.job_id}/review?item=p1-e6"
    page = optional.browser.get(url).text
    version = re.search(r'name="version" value="([0-9]+)"', page)
    assert version
    data = {
        "csrf_token": token(optional.browser, url),
        "version": version.group(1),
        "view": "split",
    }
    response = optional.browser.post(
        f"/jobs/{optional.job_id}/undo", data=data, follow_redirects=False
    )
    assert response.status_code == 303
    fix = stored(optional).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None and not fix.figure.data_table_added
    assert fix.optional_note
    assert "<table" not in download(optional)


def test_remove_the_data_table_keeps_the_descriptions(added):
    card = added
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
def test_accept_refuses_an_unsaved_change(added, fields, message, field):
    card = added
    response = post(card, "p1-e6", action="accept", alt=ALT, **fields)
    assert response.status_code == 422
    assert message in text_of(response.text)
    assert f'<a href="#{field}">' in response.text
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.status is Status.NEEDS_REVIEW


def test_marking_it_decorative_drops_both(card):
    response = post(card, "p1-e6", action="edit", alt="", decorative="on", long_description=LONG)
    assert response.status_code == 303
    fix = stored(card).fix_for("p1-e6")
    assert fix is not None and fix.figure is not None and fix.figure.decorative
    assert fix.figure.long_description is None and fix.figure.data_table is None
