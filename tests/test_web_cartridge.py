"""T3-8: the Common Cartridge download of a finished job (`/jobs/{id}/download/cartridge`).
The package itself is tested in test_export_cartridge.py."""

from __future__ import annotations

from fixtures.web.support import BOB, job_id_from, login, make_pdf, upload
from readablestem.pipeline import export_cartridge
from readablestem.web.security import VIEW_CSP
from test_web_canvas_download import finished_job, folder_of, ready_job, web, zip_of  # noqa: F401


def test_a_finished_job_downloads_as_a_common_cartridge(web):  # noqa: F811
    job_id = finished_job(web)
    response = web.browser.get(f"/jobs/{job_id}/download/cartridge")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        f'attachment; filename="{folder_of(job_id)}.imscc"'
    )
    assert response.headers["content-security-policy"] == VIEW_CSP
    assert export_cartridge.check_cartridge(response.content) == []
    archive = zip_of(response)
    assert archive.namelist()[0] == "imsmanifest.xml"
    page = archive.read(f"web_resources/{folder_of(job_id)}/index.html").decode()
    assert "SECRET-DOC-TEXT" in page and "<style" not in page


def test_the_cartridge_is_owner_only_and_a_get(web):  # noqa: F811
    job_id = finished_job(web)
    bob = web.client()
    login(bob, BOB)
    assert bob.get(f"/jobs/{job_id}/download/cartridge").status_code == 404
    assert web.client().get(f"/jobs/{job_id}/download/cartridge").status_code in (303, 401)
    assert web.browser.get("/jobs/nonexistent/download/cartridge").status_code == 404
    assert web.browser.post(f"/jobs/{job_id}/download/cartridge").status_code == 405
    waiting = job_id_from(upload(web.browser, make_pdf(1)))
    assert web.browser.get(f"/jobs/{waiting}/download/cartridge").status_code == 409


def test_the_job_page_offers_the_cartridge_with_a_plain_link(web):  # noqa: F811
    job_id = ready_job(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert f'href="/jobs/{job_id}/download/cartridge"' in page
    assert "Not yet tried in a live course" in page
