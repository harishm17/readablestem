"""Pages and headers around review at scale: the guides list, the sitemap's canonical
addresses, the landing page's expectations, "Upload a revised version" where people look
for it, the finish notice's markup, and the larger link targets on the review screen's page.
"""

from __future__ import annotations

import re
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


# --------------------------------------------------------------------------- landing


def test_the_landing_page_lists_only_the_features_this_server_has_on(tmp_path):
    off = WebEnv(tmp_path / "off")
    with off.running() as client:
        plain = flat(client.get("/").text)
    assert "scanned and handwritten pages when you ask" not in plain
    assert "tables with header cells" not in plain
    on = WebEnv(
        tmp_path / "on",
        feature_tables=True,
        feature_image_pages=True,
        feature_long_descriptions=True,
        feature_notation_memory=True,
    )
    with on.running() as client:
        full = flat(client.get("/").text)
    assert "scanned and handwritten pages when you ask" in full
    assert "tables with header cells" in full


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
    assert "New accounts have daily limits" not in text


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
    # Once, in "Settings and sharing" (ux2-lane-a): not repeated beside the review actions.
    assert page.count(link) == 1 and 'data-role="revise"' in page
    assert page.index("settings-heading") < page.index(link)


# --------------------------------------------------------------------------- finish notice


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


def test_latex_source_is_listed_only_when_its_flag_is_on(tmp_path):
    off = WebEnv(tmp_path / "off")
    with off.running() as client:
        landing, limits = flat(client.get("/").text), flat(client.get("/limits").text)
    assert "LaTeX source files (.tex) and project zips" not in landing
    assert "It works from the PDF, not from LaTeX source files." in limits
    on = WebEnv(tmp_path / "on", feature_latex_source=True)
    with on.running() as client:
        landing, limits = flat(client.get("/").text), flat(client.get("/limits").text)
    assert "LaTeX source files (.tex) and project zips" in landing
    assert "A LaTeX file (.tex) or a zip of a LaTeX project is also taken" in limits
    assert "Only PDF files are taken" not in limits
