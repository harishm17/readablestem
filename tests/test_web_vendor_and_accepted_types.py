"""Who gets the pictures (D-008), what happens if the service closes, and the refusal of a file
type this installation does not take (D-009, X1-1).

The vendor line and the closure promise are honesty claims, so their wording is pinned. The
refusal is checked through the upload route.
"""

from __future__ import annotations

import html as html_lib
import re
import stat
import sys
from pathlib import Path

from fixtures import office
from fixtures.web.support import ALICE, WebEnv, csrf_from, login, make_pdf
from readablestem.web import offices

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


def text_of(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
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


def signed_in(env: WebEnv, client):
    env.allow(ALICE)
    login(client, ALICE)
    return client


def upload(env: WebEnv, name: str, data: bytes, content_type: str):
    with env.running() as client:
        signed_in(env, client)
        token = csrf_from(client.get("/app").text)
        return client.post(
            "/jobs", data={"csrf_token": token}, files={"pdf": (name, data, content_type)}
        )


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
    assert ANTHROPIC_LINE in text_of(after_button)
    assert ANTHROPIC_LINE in text_of(landing)


def test_the_vendor_line_follows_the_provider(tmp_path):
    env = WebEnv(tmp_path, model_provider="openai")
    with env.running() as client:
        signed_in(env, client)
        app = text_of(client.get("/app").text)
        landing = text_of(client.get("/").text)
    assert OPENAI_LINE in app and OPENAI_LINE in landing
    assert "Anthropic's Claude models" not in app


# ------------------------------------------------------------------ continuity


def test_the_offices_page_says_what_happens_if_the_service_closes(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        page = client.get(offices.PAGE_PATH).text
    assert (
        "If the hosted service ever closes, a notice goes on this page first. Stored documents "
        "are then deleted on their usual schedule (see Privacy), so no copy is left behind."
    ) in text_of(page)


# ------------------------------------------------------------------ the refusal


def test_a_docx_in_production_is_refused_with_what_to_do_instead(tmp_path):
    env = WebEnv(tmp_path, office_input_enabled=False, feature_latex_source=True)
    response = upload(env, "b2.docx", office.docx(), DOCX)
    assert response.status_code in {415, 422}
    assert f"b2.docx is not a PDF or LaTeX file. {NO_OFFICE_NOTE}" in text_of(response.text)


def test_with_office_on_the_refusal_names_the_office_types(tmp_path):
    env = WebEnv(
        tmp_path,
        office_input_enabled=True,
        soffice_path=fake_soffice(tmp_path),
        feature_latex_source=False,
    )
    response = upload(env, "data.xlsx", b"just text", "application/octet-stream")
    assert response.status_code == 415
    text = text_of(response.text)
    assert "data.xlsx is not a PDF, Word (.docx) or PowerPoint (.pptx) file." in text
    assert NO_OFFICE_NOTE not in text
