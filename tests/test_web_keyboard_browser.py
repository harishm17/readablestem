"""A keyboard-only walk of the main flows, in a real browser (Playwright Chromium).

Every step is a key press: nothing is clicked. A page is reached by following a link or a
redirect with Enter, except where a test opens a URL on purpose (the test says so). The one
thing a script cannot do from the keyboard is pick a file in the operating system's dialog,
so the upload tests open the chooser with Space and hand the file to it.

What `walk` (tests/fixtures/web/keyboard.py) checks on every page:

- the first Tab stop is the skip link, it is visible when focused, and Enter moves focus into
  `main`, so the next Tab is the first control of the page's own content (WCAG 2.4.1);
- every Tab stop is in the window, shows a focus indicator, is not covered by another element
  and has an accessible name (2.4.7, 2.4.11, 4.1.2);
- Tab visits every control the page has, once each, then leaves the page or comes round to the
  start: no trap, nothing a keyboard cannot reach (2.1.1, 2.1.2); Shift+Tab walks back;
- the parts of the page are visited header, main, footer (2.4.3).

After each action (a POST and its redirect, a link to another page, a change in place) the
tests say where focus lands and assert it is somewhere a keyboard user can carry on from: the
top of a new page, the heading of what is now to do, or the notice that says what happened.

Run with: uv run pytest -m browser tests/test_web_keyboard_browser.py
This file is evidence for the keyboard rows of docs/acr/ (the criteria table names its tests).
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from fixtures.web.keyboard import (
    activate,
    choose_file_by_keyboard,
    focus_state,
    focus_summary,
    skip_link_works,
    start_of_page,
    tab_to,
    wait_focus,
    walk,
)
from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ADMIN, ALICE, BOB, WebEnv, fake_output, make_pdf
from readablestem.core.types import Status
from readablestem.web.db import Job, User, session_scope
from test_web_browser import browser as browser  # fixture, re-exported for pytest
from test_web_browser import free_port
from test_web_inventory import handwritten_output

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
NARROW = {"width": 320, "height": 640}  # WCAG 1.4.10: 1280 CSS px at 400% zoom


# ---------------------------------------------------------------------------- the app


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("keyboard"), per_user_daily_page_quota=1000)
    env.converter.output = review_output()
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
    for email in (ALICE, BOB, "carol@example.org"):
        env.allow(email)
    yield env
    server.should_exit = True
    thread.join(timeout=10)


def new_context(browser, *, viewport=DESKTOP, **options):
    return browser.new_context(viewport=viewport, **options)


def setup_sign_in(page, base: str, email: str) -> None:
    """Sign in the quick way: set-up for a test that is about something else."""
    page.goto(f"{base}/dev-login")
    page.get_by_label("Email address").fill(email)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{base}/app")


def keyboard_sign_in(page, base: str, email: str) -> None:
    """Sign in with the keyboard on the development sign-in page (the test server's stand-in
    for the Google and Microsoft buttons)."""
    page.goto(f"{base}/dev-login")
    start_of_page(page)
    tab_to(page, "Email address")
    page.keyboard.type(email)
    page.keyboard.press("Tab")
    assert focus_state(page)["name"] == "Sign in"
    activate(page)


def convert(site, browser, email: str, output, *, pdf: bytes | None = None) -> str:
    """Upload and convert a document (set-up, not the thing under test)."""
    site.converter.output = output
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, email)
    page.get_by_label(re.compile("PDF file")).set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": pdf or review_pdf()}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    converted = []  # earlier tests may have left uploads queued: convert everything waiting
    while (done := site.run_worker()) is not None:
        converted.append(done)
    assert job_id in converted
    return job_id


def decided_output():
    """A converted document with every decision made: the job page offers the downloads."""
    output = fake_output()
    for fix in output.result.fixes:
        if fix.pending:
            fix.status, fix.reviewed = Status.REJECTED, True
    return output


@pytest.fixture(scope="module")
def docs(site, browser) -> dict[str, str]:
    """Alice's documents: decisions waiting, ready to share, Symbols screen, and one that is
    still converting (a row the worker leaves alone: its lease runs for a day)."""
    out = {
        "decide": convert(site, browser, ALICE, review_output()),
        "ready": convert(site, browser, ALICE, decided_output(), pdf=make_pdf(1)),
        "symbols": convert(site, browser, ALICE, handwritten_output()),
    }
    now = datetime.now(UTC)
    with session_scope(site.ctx.factory) as session:
        owner = session.query(User.id).filter(User.email == ALICE).scalar()
        running = Job(
            user_id=owner,
            status="running",
            filename="Kinematics slides.pdf",
            page_count=3,
            pages_done=1,
            retain_until=now + timedelta(hours=24),
            lease_until=now + timedelta(days=1),
            created_at=now,
        )
        session.add(running)
        session.flush()
        out["running"] = running.id
    site.converter.output = review_output()
    return out


def open_as(browser, site, path: str, email: str | None, *, viewport=DESKTOP, unfold=False):
    context = new_context(browser, viewport=viewport)
    page = context.new_page()
    if email:
        setup_sign_in(page, site.base, email)
    page.goto(site.base + path)
    page.wait_for_load_state("networkidle")
    if unfold:  # every disclosure open, as a person who opened them all would see it
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    return context, page


# ----------------------------------------------------------- the walker finds what is wrong


# ----------------------------------------------------------------- every page, by keyboard

PAGES = [
    ("sign-in (development)", "/dev-login", None, DESKTOP, False),
    ("sign-in (providers)", "/login", None, DESKTOP, False),
    ("home", "/", None, DESKTOP, False),
    ("my documents", "/app", ALICE, DESKTOP, False),
    ("my documents, new account", "/app", "carol@example.org", DESKTOP, False),
    ("job: decisions waiting", "/jobs/{decide}", ALICE, DESKTOP, False),
    ("job: converting", "/jobs/{running}", ALICE, DESKTOP, False),
    ("job: ready to share", "/jobs/{ready}", ALICE, DESKTOP, False),
    ("review (split)", "/jobs/{decide}/review", ALICE, DESKTOP, False),
    ("review (split, everything open)", "/jobs/{decide}/review", ALICE, DESKTOP, True),
    ("review (split, 390 px)", "/jobs/{decide}/review", ALICE, PHONE, True),
    ("review (split, 320 px)", "/jobs/{decide}/review", ALICE, NARROW, True),
    ("my documents (320 px)", "/app", ALICE, NARROW, False),
    ("review (list)", "/jobs/{decide}/review/list", ALICE, DESKTOP, False),
    ("review (list, 390 px)", "/jobs/{decide}/review/list", ALICE, PHONE, True),
    ("symbols", "/jobs/{symbols}/symbols?view=split", ALICE, DESKTOP, False),
    ("symbols (everything open)", "/jobs/{symbols}/symbols?view=split", ALICE, DESKTOP, True),
    ("symbols (390 px)", "/jobs/{symbols}/symbols?view=split", ALICE, PHONE, True),
    ("record", "/jobs/{ready}/record", ALICE, DESKTOP, False),
    ("listen as a student", "/jobs/{ready}/listen", ALICE, DESKTOP, False),
    ("listen as a student (320 px)", "/jobs/{ready}/listen", ALICE, NARROW, False),
    ("demo: listen as a student", "/demo/listen", None, DESKTOP, False),
    ("account", "/account", ALICE, DESKTOP, False),
    ("account: delete", "/account/delete", ALICE, DESKTOP, False),
    ("job: delete", "/jobs/{ready}/delete", ALICE, DESKTOP, False),
    ("job: questions about the file", "/jobs/{ready}/feedback", ALICE, DESKTOP, False),
    (
        "review: accept all figures",
        "/jobs/{decide}/accept/figures?view=split",
        ALICE,
        DESKTOP,
        False,
    ),
    ("home", "/", None, PHONE, False),
    ("privacy", "/privacy", None, DESKTOP, False),
    ("terms", "/terms", None, DESKTOP, False),
    ("known limits", "/limits", None, DESKTOP, False),
    ("service status", "/status", None, DESKTOP, False),
    ("posting in your course", "/help/posting", None, DESKTOP, False),
    ("guides", "/guides", None, DESKTOP, False),
    ("for accessibility offices", "/for-accessibility-offices", None, DESKTOP, False),
    ("admin", "/admin", ADMIN, DESKTOP, False),
]


@pytest.mark.parametrize(
    ("name", "path", "email", "viewport", "unfold"), PAGES, ids=[p[0] for p in PAGES]
)
def test_every_page_can_be_walked_with_the_tab_key(
    site, browser, docs, name, path, email, viewport, unfold
):
    context, page = open_as(
        browser, site, path.format(**docs), email, viewport=viewport, unfold=unfold
    )
    if "listen" in path:
        page.wait_for_timeout(1600)  # the player waits a moment for voices, then says if none
    skip = skip_link_works(page, label=name)
    found = walk(page, label=name)
    context.close()
    assert len(found.stops) >= 8, f"{name}: only {found.names()}"
    assert not skip + found.problems, "\n".join(skip + found.problems)


OUTPUT_PAGES = [
    ("demo conversion", "/demo", None),
    ("student version", "/jobs/{ready}/view", ALICE),
]


@pytest.mark.parametrize(("name", "path", "email"), OUTPUT_PAGES, ids=[p[0] for p in OUTPUT_PAGES])
def test_a_converted_document_can_be_walked_with_the_tab_key(
    site, browser, docs, name, path, email
):
    """The converted file is a document, not a page of the app: it has no site header or
    footer to skip, so no skip link is expected. Its links and tables are still reached, in
    order, with a visible focus and no trap. (The ACR covers the output separately: its
    structure is checked by the output tests, docs/output.md.)"""
    context, page = open_as(browser, site, path.format(**docs), email)
    found = walk(page, label=name)
    context.close()
    assert not found.problems, "\n".join(found.problems)


# ------------------------------------------------------------- sign-in, upload, job page


def test_sign_in_by_keyboard_and_a_mistake_is_reported_where_focus_is(site, browser):
    context = new_context(browser)
    page = context.new_page()
    page.goto(f"{site.base}/dev-login")
    start_of_page(page)
    tab_to(page, "Email address")  # nothing typed: Enter in the field sends the empty form
    activate(page, "Enter")
    state = wait_focus(page)
    assert state["role"] == "alert" and "There is a problem" in state["text"], state
    link = tab_to(page, "email")  # the summary's link goes to the field
    assert link["tag"] == "a"
    page.keyboard.press("Enter")
    assert focus_summary(page)["id"] == "email"
    page.keyboard.type(ALICE)
    with page.expect_navigation():
        page.keyboard.press("Enter")  # Enter in the field submits the form
    assert page.url == f"{site.base}/app"
    after = focus_summary(page)
    assert after["body"] or after["inMain"], after  # the top of the new page
    page.keyboard.press("Tab")
    assert "Skip" in focus_state(page)["name"]
    context.close()


def test_upload_by_keyboard_lands_on_the_job_and_a_wrong_file_is_explained(site, browser):
    context = new_context(browser)
    page = context.new_page()
    keyboard_sign_in(page, site.base, ALICE)
    assert page.url == f"{site.base}/app"
    # Nothing chosen: the server's answer, and the problem list takes focus. Its link goes
    # to the file chooser (the form now sits right under it after a refusal).
    tab_to(page, "Upload and convert")
    activate(page)
    state = wait_focus(page)
    assert state["role"] == "alert" and "There is a problem" in state["text"], state
    page.keyboard.press("Tab")  # the first link of the list
    page.keyboard.press("Enter")  # it leads to the file chooser, which takes focus
    assert focus_summary(page)["id"] == "pdf"
    # A file that is not a PDF: the page says so as soon as it is chosen (a live region), and
    # Enter on the button does not send it but puts focus on the field that has the problem.
    start_of_page(page)
    tab_to(page, "^PDF files?$")
    choose_file_by_keyboard(
        page, [{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"not a pdf"}]
    )
    page.locator("#pdf-check .error-message").wait_for()
    tab_to(page, "Upload and convert")
    page.keyboard.press("Enter")
    state = focus_summary(page)
    assert state["id"] == "pdf" and page.url == f"{site.base}/jobs", state
    assert page.get_attribute("#pdf", "aria-invalid") == "true"
    # Now a real one: the chooser by keyboard, the button by keyboard, the job page after it.
    tab_to(page, "^PDF files?$", shift=True)
    choose_file_by_keyboard(
        page, [{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": review_pdf()}]
    )
    tab_to(page, "Upload and convert")
    activate(page)
    assert re.fullmatch(r"/jobs/[0-9a-f]{32}", focus_summary(page)["path"])
    start = focus_summary(page)
    assert start["body"] or start["inMain"], start  # a new page: focus is at its top
    page.keyboard.press("Tab")
    assert "Skip" in focus_state(page)["name"]
    context.close()


def test_the_documents_list_and_the_job_status_refresh_without_taking_focus_away(
    site, browser, docs
):
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, ALICE)  # lands on the list: the converting row refreshes it
    start_of_page(page)
    tab_to(page, "^Delete Kinematics slides")  # a control inside the part that is swapped
    marker = focus_state(page)["domId"]
    page.wait_for_event("response", lambda r: "/app/documents" in r.url, timeout=15000)
    page.wait_for_load_state("networkidle")
    assert marker and focus_summary(page)["id"] == marker, "focus left the list when it refreshed"
    page.goto(f"{site.base}/jobs/{docs['running']}")
    start_of_page(page)
    tab_to(page, "Skip to main content")
    page.wait_for_event("response", lambda r: "/status" in r.url, timeout=15000)
    page.wait_for_load_state("networkidle")
    assert focus_state(page)["name"].startswith("Skip to main content")
    context.close()


# ------------------------------------------------------------------------ the review


@pytest.fixture
def fresh(site, browser) -> str:
    """A fresh document with its three decisions waiting (the tests change them)."""
    return convert(site, browser, ALICE, review_output())


def test_the_whole_review_is_decided_and_undone_by_keyboard_with_focus_on_the_next_decision(
    site, browser, fresh
):
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{fresh}")
    start_of_page(page)
    tab_to(page, r"^Review \d+ decisions?")  # the job page's way into the decisions
    activate(page)
    assert "/review" in page.url

    def lands_on_heading(text: str) -> None:
        state = focus_summary(page)
        assert state["tag"] == "h2" and state["id"] == "decision-title", state
        assert text in state["text"], state

    tab_to(page, "Accept anyway equation 1")
    activate(page)
    lands_on_heading("Decision 2 of 3: Figure 1")
    tab_to(page, "Accept figure 1")
    activate(page)
    lands_on_heading("Decision 3 of 3")
    tab_to(page, "^Make bold text")
    activate(page)
    lands_on_heading("Ready to share")
    # Undo is a button in the notice; focus goes back to the decision it reopened.
    tab_to(page, "^Undo")
    activate(page)
    lands_on_heading("Decision 3 of 3")
    # The decision before it is one Shift+Tab away from its heading.
    context.close()


def test_an_edit_is_typed_saved_and_a_refused_one_is_explained_by_keyboard(site, browser, fresh):
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{fresh}/review")
    start_of_page(page)
    tab_to(page, "Edit the LaTeX")  # a disclosure: Enter toggles it, and so does Space
    opened = "document.activeElement.parentElement.open"
    was = page.evaluate(opened)
    page.keyboard.press("Enter")
    assert page.evaluate(opened) is not was
    page.keyboard.press("Space")
    assert page.evaluate(opened) is was
    if not was:
        page.keyboard.press("Enter")
    tab_to(page, "LaTeX for equation 1")
    page.keyboard.press("Control+A")
    page.keyboard.type(r"\notacommand{x}")
    tab_to(page, "Save edit to equation 1")
    activate(page)
    state = wait_focus(page)  # a refused edit: the problem list takes focus
    assert state["role"] == "alert" and "There is a problem" in state["text"], state
    page.keyboard.press("Tab")  # its link goes to the field, which keeps what was typed
    page.keyboard.press("Enter")
    assert focus_summary(page)["tag"] == "textarea"
    context.close()


def test_a_page_is_left_out_and_put_back_by_keyboard_and_focus_stays_at_the_page_tools(
    site, browser, fresh
):
    """The page tools redirect to their own place on the page. A web address with a fragment
    makes the browser skip `autofocus` (HTML: no autofocus when the document has a target), so
    the redirect names the tools' summary, which is focusable: focus lands there, not nowhere."""
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{fresh}/review")
    start_of_page(page)
    tab_to(page, "^Change page 1")  # a disclosure: opened with Enter
    page.keyboard.press("Enter")
    tab_to(page, "^Leave page 1 out")
    activate(page)
    state = wait_focus(page)
    assert state["tag"] == "summary" and state["id"] == "page-tools-summary", state
    assert page.locator("#page-tools").get_attribute("open") is not None  # still open
    assert "Page 1 is left out" in page.locator("#page-tools").inner_text()
    tab_to(page, "^Put page 1 back")
    activate(page)
    state = wait_focus(page)
    assert state["tag"] == "summary" and state["id"] == "page-tools-summary", state
    # A line chosen to change: the link goes to the editor of that line, and focus goes with it.
    tab_to(page, r"\(change\)$")
    activate(page)
    state = wait_focus(page)
    assert state["id"] == "page-tools-edit", state
    context.close()


def test_the_list_view_decisions_are_made_by_keyboard(site, browser, fresh):
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/jobs/{fresh}/review/list")
    start_of_page(page)
    tab_to(page, "(?i)^Accept equation 1")
    activate(page)
    after = wait_focus(page)
    print("LIST AFTER ACCEPT:", after)
    assert after["inMain"], after  # on a decision's own place, not lost to the page's start
    context.close()


# ---------------------------------------------------------------------- account, delete


def test_a_document_is_deleted_by_keyboard_and_the_notice_takes_focus(site, browser):
    gone = convert(site, browser, BOB, review_output())
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, BOB)
    start_of_page(page)
    tab_to(page, "^Delete ")
    activate(page)
    assert page.url.startswith(f"{site.base}/jobs/{gone}/delete")
    assert "Delete" in page.locator("h1").inner_text()
    tab_to(page, "^Delete “")
    activate(page)
    page.wait_for_url(re.compile(r"/app"))
    state = wait_focus(page)
    assert state["role"] == "status" and "deleted" in state["text"].lower(), state
    context.close()


def test_an_account_is_deleted_by_keyboard_after_a_confirmation_page(site, browser):
    context = new_context(browser)
    page = context.new_page()
    setup_sign_in(page, site.base, "carol@example.org")
    start_of_page(page)
    tab_to(page, "^Account$")
    activate(page)
    tab_to(page, "Delete my account")
    activate(page)
    assert page.url.endswith("/account/delete")
    # "No, keep it" is a plain way back; the destructive button is a second, separate stop.
    keep = tab_to(page, "No, keep it")
    assert keep["tag"] == "a"
    tab_to(page, "^Delete my account$", shift=True)
    activate(page)
    state = wait_focus(page)  # the notice takes focus at the next rendering opportunity
    assert state["role"] == "status" and "deleted" in state["text"].lower(), state
    assert page.url == f"{site.base}/"
    context.close()


def test_the_review_screens_second_skip_link_goes_to_the_decision(site, docs, browser):
    """The two skip links of the review screen: the first goes to the main content, the second
    to the decision, and both work from the keyboard."""
    context, page = open_as(browser, site, f"/jobs/{docs['decide']}/review", ALICE)
    start_of_page(page)
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    assert focus_state(page)["name"] == "Skip to the decision"
    page.keyboard.press("Enter")
    assert focus_summary(page)["id"] == "decision-title"
    assert GROUP  # the heading group is decision 3 of this document
    context.close()
