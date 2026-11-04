"""Pages and headers around review at scale: the guides list, the sitemap's canonical
addresses, the landing page's expectations, "Upload a revised version" where people look
for it, the finish notice's markup, and the larger link targets on the review screen's page.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.web import marks, seo

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def flat(text: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", text)).split())


# --------------------------------------------------------------------------- guides


@pytest.fixture
def env(tmp_path):
    with WebEnv(tmp_path).running() as client:
        yield client


def test_the_guides_index_lists_every_guide_with_its_date(env):
    page = env.get("/guides")
    assert page.status_code == 200
    for guide in seo.GUIDES:
        assert f'<a href="{guide.path}">{guide.title}</a>' in page.text
        assert guide.summary in unescape(page.text)
    assert "<h1>Guides</h1>" in page.text
    assert 'rel="canonical"' in page.text and 'name="robots"' not in page.text  # indexable
    assert 'aria-current="page"' in page.text  # the footer link names this page


def test_the_index_is_in_the_sitemap_llms_txt_and_every_footer_and_guide_links_back(env):
    sitemap = ET.fromstring(env.get("/sitemap.xml").content)
    locs = [e.text for e in sitemap.findall("s:url/s:loc", NS)]
    assert any(loc.endswith("/guides") for loc in locs)
    llms = env.get("/llms.txt").text
    assert "[All guides](" in llms and "/guides)" in llms
    assert 'href="/guides"' in env.get("/limits").text
    for guide in seo.GUIDES:
        assert 'href="/guides"' in env.get(guide.path).text


def test_an_unlisted_guide_cannot_be_added_without_a_public_page():
    paths = {page.path for page in seo.PUBLIC_PAGES}
    assert all(guide.path in paths for guide in seo.GUIDES) and seo.GUIDES_INDEX in paths


# --------------------------------------------------------------------------- sitemap


@pytest.fixture
def showcase(tmp_path, monkeypatch):
    root = tmp_path / "showcase"
    for name in ("index.html", "method/index.html", "examples/index.html", "examples/a/index.html"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"<!doctype html><html><head><title>{name}</title></head><body></body></html>"
        )
    (root / "examples/a/converted.html").write_text("<html><head><title>c</title></head></html>")
    monkeypatch.setattr(seo, "SHOWCASE_DIR", root)
    with WebEnv(tmp_path / "app").running() as client:
        yield client


def test_every_showcase_url_in_the_sitemap_is_the_pages_own_canonical(showcase):
    sitemap = ET.fromstring(showcase.get("/sitemap.xml").content)
    locs = [e.text for e in sitemap.findall("s:url/s:loc", NS)]
    showcase_locs = [loc for loc in locs if "/showcase/" in loc]
    assert showcase_locs and not any("index.html" in loc for loc in showcase_locs)
    assert any(loc.endswith("/showcase/") for loc in showcase_locs)
    for loc in showcase_locs:
        path = "/" + loc.split("/", 3)[3]
        page = showcase.get(path)
        assert page.status_code == 200, path
        assert f'<link rel="canonical" href="{loc}">' in page.text, path
        assert f'"url":"{loc}"' in page.text.replace(" ", ""), path


def test_showcase_pages_have_an_etag_and_answer_304(showcase):
    first = showcase.get("/showcase/method/")
    assert first.headers["cache-control"] == "public, max-age=3600"
    again = showcase.get("/showcase/method/", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_showcase_canonical_turns_an_index_file_into_its_folder():
    assert seo.showcase_canonical("/showcase/index.html") == "/showcase/"
    assert seo.showcase_canonical("/showcase/method/index.html") == "/showcase/method/"
    assert seo.showcase_canonical("/showcase/x/page.html") == "/showcase/x/page.html"


# --------------------------------------------------------------------------- landing


def test_the_landing_page_says_what_review_costs_from_the_settings(tmp_path):
    env = WebEnv(tmp_path, signup_mode="open", open_tier_daily_page_quota=17, max_pages_per_job=30)
    with env.running() as client:
        text = flat(client.get("/").text)
        limits = flat(client.get("/limits").text)
    assert "about 1.8 decisions per page" in text
    assert "real course PDFs may need more" in text
    assert "Upload up to 30 pages at a time" in text  # max_pages_per_job, not written in
    assert "New accounts have daily limits: 17 pages a day" in text
    assert "deleted 24 hours after you last work on them" in text
    # The planning detail lives on /limits.
    assert "all the equations, or all the figures with alt text, in one step" in limits


def test_the_landing_page_lists_only_the_features_this_server_has_on(tmp_path):
    off = WebEnv(tmp_path / "off")
    with off.running() as client:
        plain = flat(client.get("/").text)
    assert "Scanned and handwritten pages, when you tick a box" not in plain
    assert "Tables, rebuilt as real tables" not in plain
    assert "longer description for a chart" not in plain
    assert "Your course's notation" not in plain
    on = WebEnv(
        tmp_path / "on",
        feature_tables=True,
        feature_image_pages=True,
        feature_long_descriptions=True,
        feature_notation_memory=True,
    )
    with on.running() as client:
        full = flat(client.get("/").text)
    assert "Scanned and handwritten pages, when you tick a box at upload" in full
    assert "Tables, rebuilt as real tables with header cells" in full
    assert "a longer description for a chart or a diagram" in full
    assert "Your course's notation" in full


def test_the_quota_is_read_from_the_settings_not_written_in_the_page(tmp_path):
    for quota in (5, 40):
        env = WebEnv(tmp_path / str(quota), signup_mode="open", open_tier_daily_page_quota=quota)
        with env.running() as client:
            text = flat(client.get("/").text)
        assert f"New accounts have daily limits: {quota} pages a day" in text


def test_an_invite_only_landing_page_does_not_quote_a_new_accounts_quota(tmp_path):
    env = WebEnv(tmp_path, signup_mode="allowlist", open_tier_daily_page_quota=17)
    with env.running() as client:
        text = flat(client.get("/").text)
    assert "New accounts have daily limits" not in text and "about 1.8 decisions per page" in text


def test_the_numbers_are_the_ones_the_limits_page_gives(env):
    assert "about 1.8 per page" in flat(env.get("/limits").text)
    assert "about 1.8 decisions per page" in flat(env.get("/").text)


# --------------------------------------------------------------------------- revised version


@pytest.fixture
def finished(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def test_the_results_page_offers_a_revised_version_with_decisions_open_and_when_ready(finished):
    link = f'href="/app?revises={finished.job_id}#upload-heading"'
    page = finished.browser.get(f"/jobs/{finished.job_id}").text
    assert page.count(link) == 2  # in the actions, and in its own section lower down
    assert 'data-role="revise"' in page


def test_my_documents_has_the_link_on_each_finished_row_only(finished):
    queued = job_id_from(upload(finished.browser, make_pdf(1)))
    page = finished.browser.get("/app").text
    assert f'id="rev-{finished.job_id}"' in page
    assert f'href="/app?revises={finished.job_id}#upload-heading"' in page
    assert f'id="rev-{queued}"' not in page  # not converted yet
    row = page[page.index(f'id="rev-{finished.job_id}"') :][:400]
    assert "Upload a revised version" in row and "visually-hidden" in row


def test_the_revision_form_opens_from_the_link(finished):
    page = finished.browser.get(f"/app?revises={finished.job_id}").text
    assert "Upload a revised version" in page and 'name="revises"' in page


# --------------------------------------------------------------------------- finish notice


def test_a_running_job_page_has_the_hidden_notify_box_and_a_finished_one_does_not(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, make_pdf(1)))
        page = client.get(f"/jobs/{job_id}").text
        assert re.search(r'<div id="notify-box"[^>]*\bhidden\b', page)
        assert '<button type="button" id="notify-button"' in page
        assert 'id="notify-state" role="status"' in page
        assert 'aria-describedby="notify-hint"' in page
        env.run_worker()
        done = client.get(f"/jobs/{job_id}").text
        assert "notify-box" not in done


def test_the_status_fragment_of_a_finished_job_says_so_in_the_live_region(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, make_pdf(1)))
        running = client.get(f"/jobs/{job_id}/status", headers={"HX-Request": "true"})
        assert "data-finished" not in running.text and "has finished" not in running.text
        env.run_worker()
        done = client.get(f"/jobs/{job_id}/status", headers={"HX-Request": "true"})
        assert "data-finished=" in done.text and "has finished" not in done.text
        assert re.search(r'data-title="[^"]+: ', done.text)
        full = client.get(f"/jobs/{job_id}").text
        assert "data-finished=" in full  # a page opened later says nothing more


# --------------------------------------------------------------------------- targets


LETTER = (612.0, 792.0)


def test_a_mark_with_room_grows_to_44_px_and_one_between_neighbours_stays_24():
    alone = marks.place({"a": (100.0, 100.0, 140.0, 112.0)}, LETTER)["a"]
    comfort = marks.comfortable_pt(LETTER[0])
    assert alone.link[2] - alone.link[0] >= comfort - 1e-6
    assert alone.link[3] - alone.link[1] >= comfort - 1e-6
    # The frame is the item's own box, not the larger link.
    assert alone.frame[2] - alone.frame[0] < 60
    # A column of inline equations one line apart: neighbours claim the room.
    boxes = {f"e{i}": (100.0, 100.0 + 32 * i, 130.0, 110.0 + 32 * i) for i in range(12)}
    crowded = marks.place(boxes, LETTER)
    assert marks.overlapping(crowded) == []
    side = marks.min_target_pt(LETTER[0])
    for mark in crowded.values():
        assert mark.link[2] - mark.link[0] >= side - 1e-6
        assert mark.link[3] - mark.link[1] >= side - 1e-6


def test_growing_never_creates_an_overlap_and_stays_on_the_page():
    boxes = {
        f"e{i}": (
            10.0 + (i % 6) * 95,
            20.0 + (i // 6) * 28,
            60.0 + (i % 6) * 95,
            32.0 + (i // 6) * 28,
        )
        for i in range(24)
    }
    placed = marks.place(boxes, LETTER)
    assert marks.overlapping(placed) == []
    for mark in placed.values():
        x0, y0, x1, y1 = mark.link
        assert x0 >= 0 and y0 >= 0 and x1 <= LETTER[0] and y1 <= LETTER[1]
        ex0, ey0, ex1, ey1 = mark.element
        assert x0 <= (ex0 + ex1) / 2 <= x1 and y0 <= (ey0 + ey1) / 2 <= y1  # still on its item


def test_the_stylesheet_gives_undo_a_44_px_target():
    css = (
        seo.Path(__file__).resolve().parent.parent / "src/readablestem/web/static/app.css"
    ).read_text()
    assert re.search(r"\.undo-form \.link-button \{[^}]*min-height: 44px; min-width: 44px", css)
