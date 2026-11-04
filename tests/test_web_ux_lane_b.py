"""Upload, job and My documents wording and structure (ux/upload-job-documents): one
retention wording, the compact app footer, the viewer's text, the retry sentence."""

from __future__ import annotations

import re

from fixtures.web.support import ALICE, WebEnv, login, make_pdf, upload
from readablestem.web import retry


def flat(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_retry_says_what_the_button_will_do_not_that_it_happened():
    offer = retry.offer(status="failed", error_code="timeout", result_json=None, retries=0)
    assert offer is not None
    assert offer.explain.startswith("Try again puts your document back in the queue.")
    assert "queued again" not in offer.explain


def test_signed_in_pages_have_the_compact_footer_and_public_pages_the_full_one(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        public = client.get("/privacy").text
        assert "footer-cols" in public and "site-footer-compact" not in public
        login(client, ALICE)
        app = client.get("/app").text
        assert "site-footer-compact" in app and "footer-cols" not in app
        for link in ("/privacy", "/terms", "/limits", "/help/posting", "/status"):
            assert f'href="{link}"' in app.split("site-footer-compact", 1)[1]
        # a public page stays the full footer even when someone is signed in
        assert "footer-cols" in client.get("/privacy").text


def test_the_upload_form_keeps_one_retention_wording_and_separates_its_groups(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
        assert '<label for="keep">Keep for 7 days</label>' in page
        assert "Keep it for 7 days" not in page and "Keep 7 more days" not in page
        convert, keep = page.index("How to convert it"), page.index("How long to keep it")
        assert page.index('id="exam"') > convert and page.index('id="keep"') > keep > convert
        assert "<details" in page  # the long explanations are disclosed


def test_a_returning_user_has_a_compact_sample_link_beside_convert_a_pdf(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        upload(client, make_pdf(1))
        env.run_worker()
        page = client.get("/app").text
        row = page.split('id="upload-heading"', 1)[1].split("</div>", 1)[0]
        assert "Practise on the sample" in flat(row)
        assert 'id="sample-heading"' not in page  # no second, full sample section
