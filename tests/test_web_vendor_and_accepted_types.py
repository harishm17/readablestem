"""One statement of who gets the pictures and what the site takes (D-008, D-009, X1-1), and the
continuity bullet on the offices page.

The vendor line is built from the same recipient list as the privacy page. The accepted-types
phrase comes from one helper (`web.office_convert.accepted_types`) and is rendered by one
include; every page and refusal that states what is taken must say the same thing under the
same installation.
"""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

import pytest

from fixtures import office
from fixtures.web.support import ALICE, WebEnv, csrf_from, login, make_pdf
from readablestem.web import office_convert, offices, seo

ANTHROPIC_LINE = (
    "Pictures of the equations, figures and tables in your file, with a little nearby text, go to "
    "Anthropic's Claude models to propose readings, and so do whole pages you ask to have read "
    "as scans. They are not used to train models."
)
OPENAI_LINE = (
    "Pictures of the equations, figures and tables in your file, with a little nearby text, go to "
    "OpenAI's models to propose readings, and so do whole pages you ask to have read as "
    "scans. They are not used to train models."
)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

NO_OFFICE_NOTE = "For a Word or PowerPoint file, save a copy as a PDF first."
PAGES = ["/", "/limits", seo.ADA_GUIDE]


def text_of(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    import html as html_lib

    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", html)).split())


def fake_soffice(tmp_path: Path) -> str:
    """A stand-in `soffice` (no registry folder, so it is taken to have Word and PowerPoint)."""
    pdf = tmp_path / "made.pdf"
    pdf.write_bytes(make_pdf(1, "From Word"))
    script = tmp_path / "soffice"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nargs = sys.argv[1:]\n"
        "out = args[args.index('--outdir') + 1]\n"
        f"open(out + '/input.pdf', 'wb').write(open({str(pdf)!r}, 'rb').read())\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def production_like(tmp_path: Path) -> WebEnv:
    """Office input off, LaTeX source on: what production takes."""
    return WebEnv(tmp_path, office_input_enabled=False, feature_latex_source=True)


def with_office(tmp_path: Path) -> WebEnv:
    """Word and PowerPoint taken, LaTeX source off."""
    return WebEnv(
        tmp_path,
        office_input_enabled=True,
        soffice_path=fake_soffice(tmp_path),
        feature_latex_source=False,
    )


def signed_in(env: WebEnv, client):
    env.allow(ALICE)
    login(client, ALICE)
    return client


# ------------------------------------------------------------------ the helper


@pytest.mark.parametrize(
    ("kinds", "latex", "what", "note"),
    [
        ((), False, "a PDF file", NO_OFFICE_NOTE),
        ((), True, "a PDF or LaTeX file", NO_OFFICE_NOTE),
        (("docx", "pptx"), False, "a PDF, Word (.docx) or PowerPoint (.pptx) file", ""),
        (("docx", "pptx"), True, "a PDF, LaTeX, Word (.docx) or PowerPoint (.pptx) file", ""),
        (("docx",), False, "a PDF or Word (.docx) file", ""),
    ],
)
def test_the_helper_words_what_this_installation_takes(kinds, latex, what, note):
    accepted = office_convert.accepted_types(kinds, latex)
    assert (accepted.what, accepted.note) == (what, note)
    assert accepted.sentence == f"You can upload {what}." + (f" {note}" if note else "")


# ------------------------------------------------------------------ the vendor line


def test_the_vendor_line_names_anthropic_beside_the_upload_button_and_on_the_landing_page(
    tmp_path,
):
    env = WebEnv(tmp_path, model_provider="anthropic")
    with env.running() as client:
        signed_in(env, client)
        app = client.get("/app").text
        landing = client.get("/").text
    form = app.split('<form method="post" action="/jobs"', 1)[1].split("</form>", 1)[0]
    after_button = form.split("Upload and convert", 1)[1]
    assert text_of(after_button).count(ANTHROPIC_LINE) == 1
    assert ANTHROPIC_LINE in text_of(after_button)  # next to the button, not in a <details>
    assert 'href="/privacy#who-else-sees">How your documents are handled</a>' in after_button
    answer = landing.split('id="documents">', 1)[1].split("</details>", 1)[0]
    assert ANTHROPIC_LINE in text_of(answer)
    assert "Uploads are never used to train models." not in app


def test_the_vendor_line_follows_the_provider(tmp_path):
    env = WebEnv(tmp_path, model_provider="openai")
    with env.running() as client:
        signed_in(env, client)
        app = text_of(client.get("/app").text)
        landing = text_of(client.get("/").text)
    assert OPENAI_LINE in app and OPENAI_LINE in landing
    assert "Anthropic's Claude models" not in app


def test_the_privacy_page_has_the_anchor_the_line_links_to(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        page = client.get("/privacy").text
    assert re.search(r'<h2 id="who-else-sees">Who else sees your documents</h2>', page)


# ------------------------------------------------------------------ continuity


def test_the_offices_page_says_what_happens_if_the_service_closes(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        page = client.get(offices.PAGE_PATH).text
    section = page.split('id="continuity"', 1)[1]
    text = text_of(section)
    assert (
        "If the hosted service ever closes, a notice goes on this page first. Stored documents "
        "are then deleted on their usual schedule (see Privacy), so no copy is left behind."
    ) in text
    assert text.index("Nothing is locked in") < text.index("If the hosted service ever closes")
    assert '<a href="/privacy">Privacy</a>' in section


# ------------------------------------------------------------------ accepted types, everywhere


def test_every_page_states_the_same_types_with_office_off_and_latex_on(tmp_path):
    env = production_like(tmp_path)
    sentence = f"You can upload a PDF or LaTeX file. {NO_OFFICE_NOTE}"
    with env.running() as client:
        signed_in(env, client)
        for path in [*PAGES, "/app"]:
            assert sentence in text_of(client.get(path).text), path
        form = client.get("/app").text
    assert 'data-accepted-what="a PDF or LaTeX file"' in form
    assert f'data-accepted-note="{NO_OFFICE_NOTE}"' in form


def test_every_page_states_the_same_types_with_office_on_and_latex_off(tmp_path):
    env = with_office(tmp_path)
    sentence = "You can upload a PDF, Word (.docx) or PowerPoint (.pptx) file."
    with env.running() as client:
        signed_in(env, client)
        for path in [*PAGES, "/app"]:
            text = text_of(client.get(path).text)
            assert sentence in text, path
            assert NO_OFFICE_NOTE not in text, path
        form = client.get("/app").text
    assert 'data-accepted-what="a PDF, Word (.docx) or PowerPoint (.pptx) file"' in form
    assert 'data-accepted-note=""' in form


def test_the_pre_submit_script_builds_its_refusal_from_the_servers_words():
    script = (Path(office_convert.__file__).parent / "static" / "app.js").read_text()
    assert "data-accepted-what" in script and "data-accepted-note" in script
    assert "Choose a PDF file." not in script and "is not a PDF." not in script


# ------------------------------------------------------------------ the refusal


def test_a_docx_in_production_reads_like_the_pages(tmp_path):
    env = production_like(tmp_path)
    with env.running() as client:
        signed_in(env, client)
        token = csrf_from(client.get("/app").text)
        response = client.post(
            "/jobs",
            data={"csrf_token": token},
            files={"pdf": ("b2.docx", office.docx(), DOCX)},
        )
    assert response.status_code in {415, 422}
    assert (
        "b2.docx is not a PDF or LaTeX file. For a Word or PowerPoint file, save a copy as a "
        "PDF first."
    ) in text_of(response.text)


def test_a_text_file_named_docx_gets_the_same_words(tmp_path):
    env = production_like(tmp_path)
    with env.running() as client:
        signed_in(env, client)
        token = csrf_from(client.get("/app").text)
        response = client.post(
            "/jobs", data={"csrf_token": token}, files={"pdf": ("notes.docx", b"just text", DOCX)}
        )
    assert response.status_code == 415
    assert (
        "notes.docx is not a PDF or LaTeX file. For a Word or PowerPoint file, save a copy as a "
        "PDF first."
    ) in text_of(response.text)


def test_with_office_on_the_refusal_names_the_office_types(tmp_path):
    env = with_office(tmp_path)
    with env.running() as client:
        signed_in(env, client)
        token = csrf_from(client.get("/app").text)
        response = client.post(
            "/jobs",
            data={"csrf_token": token},
            files={"pdf": ("data.xlsx", b"just text", "application/octet-stream")},
        )
    assert response.status_code == 415
    text = text_of(response.text)
    assert "data.xlsx is not a PDF, Word (.docx) or PowerPoint (.pptx) file." in text
    assert NO_OFFICE_NOTE not in text
