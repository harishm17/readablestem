"""The triaged review screen in a real browser (Playwright Chromium): a heading group
card, optional checks folded away, group decisions.

- axe-core in every state of a group decision (waiting, members unfolded, optional checks
  unfolded, a refused level, after "Make all 3 bold text", after "Apply to all 3"):
  zero serious or critical violations, at 1280 and 320 CSS px;
- no sideways scrolling at 320 CSS px in any of those states;
- every button of the group card says which headings it acts on;
- group decisions work without JavaScript and land where the reviewer expects.

Run with: uv run pytest -m browser tests/test_web_review_triage_browser.py
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from collections.abc import Iterator

import pytest

from fixtures.web.support import ALICE, WebEnv, make_pdf
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import axe_violations, describe, free_port, sign_in
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_review_browser import NARROW, WIDE, open_page, sideways_overflow, unfold_everything
from test_web_review_triage import GROUP, triage_output

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

LONG_NAME = "Physics_Unit_2_Relative_Motion_Distance_and_Displacement_Teacher_Edition_FINAL.pdf"


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    """The app on a free port, converting every upload into `triage_output()`. Only the
    HTML assembler is stubbed."""
    import uvicorn

    from readablestem.pipeline import assemble

    patch = pytest.MonkeyPatch()
    patch.setattr(assemble, "render_html", lambda **kw: "<html><body>re-rendered</body></html>")
    env = WebEnv(tmp_path_factory.mktemp("triage-browser"), per_user_daily_page_quota=1000)
    env.converter.output = triage_output()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(env.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    env.base = f"http://127.0.0.1:{port}"
    env.allow(ALICE)
    yield env
    server.should_exit = True
    thread.join(timeout=10)
    patch.undo()


@pytest.fixture
def job(site, browser) -> str:
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": LONG_NAME, "mimeType": "application/pdf", "buffer": make_pdf(3)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    return job_id


def walk_group_states(page, site, job: str, check) -> None:
    url = f"{site.base}/jobs/{job}/review/list"
    page.goto(url)
    check("group waiting, optional checks folded")
    unfold_everything(page)
    check("everything unfolded")
    page.get_by_label("Or choose one level for all 3").select_option("5")
    page.get_by_role("button", name=re.compile("^Set the level for all 3")).click()
    assert page.locator(".error-summary").is_visible()
    check("refused level")
    page.goto(url)
    page.get_by_role("button", name=re.compile("^Make all 3 bold text")).click()
    page.wait_for_url(f"{url}#item-p2-e2")
    check("after make bold text")
    page.goto(f"{site.base}/jobs/{job}")
    check("job page")


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280px", "320px"])
def test_group_decision_states_have_no_serious_or_critical_axe_violations(
    site, browser, job, axe_source, viewport
):
    context, page = open_page(browser, site, viewport=viewport)
    found: dict[str, list[dict]] = {}
    widths: dict[str, int] = {}

    def check(state: str) -> None:
        found[state] = axe_violations(page, axe_source)
        widths[state] = sideways_overflow(page)

    walk_group_states(page, site, job, check)
    context.close()
    assert len(found) == 5
    failures = {state: describe(v) for state, v in found.items() if v}
    assert not failures, "\n".join(f"[{s}]\n{d}" for s, d in failures.items())
    if viewport is NARROW:
        assert widths == dict.fromkeys(widths, 0), widths


def test_group_buttons_say_what_they_act_on(site, browser, job):
    context, page = open_page(browser, site)
    page.goto(f"{site.base}/jobs/{job}/review/list")
    unfold_everything(page)
    # Names with curly quotes are YAML-quoted in the snapshot: - 'button "..."'
    names = re.findall(r"""- '?button "([^"]+)"'?""", page.locator("main").aria_snapshot())
    context.close()
    duplicates = [name for name, n in Counter(names).items() if n > 1]
    assert not duplicates, duplicates
    group = [n for n in names if "SECRET-LABEL Teacher Support" in n]
    assert "Apply to all 3 headings “SECRET-LABEL Teacher Support” at level 3" in group
    assert "Make all 3 bold text instead of headings “SECRET-LABEL Teacher Support”" in group
    assert any(n.startswith("Set the level for all 3") for n in group)
    for item in ("heading 2", "heading 3", "heading 5"):  # each one, inside the card
        assert f"Accept {item}" in names and f"Make bold text ({item})" in names


def test_group_decisions_work_without_javascript(site, browser, job):
    context, page = open_page(browser, site, javascript=False)
    url = f"{site.base}/jobs/{job}/review/list"
    page.goto(url)
    card = page.locator(f"#group-card-{GROUP}")
    assert "appears 3 times" in card.inner_text()
    card.get_by_role("button", name=re.compile("^Apply to all 3")).click()
    assert page.url == f"{url}#item-p2-e2"  # the next decision: the equation
    assert "Kept 3 headings as they are." in page.locator("#review-progress").inner_text()
    assert page.locator(f"#group-card-{GROUP}").count() == 0
    heading = page.locator("#needs-decision-title").inner_text()
    assert heading == "Needs your decision (1)"
    # The optional checks are folded, and open without JavaScript.
    optional = page.locator("#optional-checks details").first
    assert optional.get_attribute("open") is None
    optional.locator("summary").first.click()
    page.locator("#fix-title-p1-e2").click()  # the item's own summary
    assert page.locator("#fix-p1-e2").is_visible()
    context.close()
