"""Listen as a student in a real browser (Playwright Chromium), with a stubbed
`speechSynthesis` so no sound is needed: Play, Pause, Stop and next work, the item being spoken
is marked, voices on the device are the default and online ones need a choice, the page works
without speech, "Hear this" plays the proposal and the PDF's reading, the landing hero plays
its two readings, and the pages pass axe (WCAG 2.2 AA and best practice).

Run with: uv run pytest -m browser tests/test_web_listen_browser.py
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from fixtures.web.support import ALICE, WebEnv, fake_output, make_pdf
from readablestem.web.db import Job, session_scope
from test_web_browser import WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

WIDE = {"width": 1280, "height": 900}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}

HTML = (
    '<!doctype html><html lang="en"><head><title>Kinematics notes</title></head><body><main>'
    "<h1>Kinematics</h1><p>The speed is "
    '<math id="p1-e2"><mi>v</mi><mo>=</mo><mi>d</mi><mo>/</mo><mi>t</mi></math>.</p>'
    "<h2>Average speed</h2><p>It is distance over time.</p>"
    '<div class="equation" id="p1-e3"><math display="block"><mi>x</mi><mo>+</mo><mn>1</mn>'
    "</math></div><ul><li>one</li><li>two</li></ul></main></body></html>"
)

# A speech engine that records what it is asked to say. `voices` is set per test; an
# utterance ends when the test says so (`__end()`), or on its own with `__auto`.
STUB = """
(() => {
  window.__spoken = []; window.__cancels = 0; window.__paused = false; window.__scrolls = [];
  window.__voices = window.__voices || [
    {name: 'Local Voice', lang: 'en-US', localService: true, voiceURI: 'local', default: true},
    {name: 'Cloud Voice', lang: 'en-US', localService: false, voiceURI: 'cloud', default: false},
  ];
  let current = null;
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  const synth = {
    speaking: false, paused: false,
    getVoices: () => window.__voices,
    addEventListener() {},
    speak(u) {
      window.__spoken.push({text: u.text, rate: u.rate, voice: u.voice ? u.voice.name : null});
      current = u; synth.speaking = true;
      if (window.__auto) setTimeout(() => { if (current === u) window.__end(); }, 5);
    },
    cancel() {
      const u = current; current = null; synth.speaking = false; synth.paused = false;
      window.__cancels += 1;
      if (u && u.onerror) setTimeout(() => u.onerror({error: 'canceled'}), 0);
    },
    pause() { synth.paused = true; window.__paused = true; },
    resume() { synth.paused = false; window.__paused = false; },
  };
  Object.defineProperty(window, 'speechSynthesis', {value: synth, configurable: true});
  window.__end = () => { const u = current; current = null; synth.speaking = false;
    if (u && u.onend) u.onend({}); };
  const scroll = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (o) {
    window.__scrolls.push(o); return scroll.call(this, o);
  };
})();
"""
ONLINE_ONLY = (
    "window.__voices = [{name: 'Cloud Voice', lang: 'en-US', localService: false, "
    "voiceURI: 'cloud', default: false}];\n"
)
NO_SPEECH = "Object.defineProperty(window, 'speechSynthesis', {value: undefined});"


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[WebEnv]:
    import uvicorn

    env = WebEnv(tmp_path_factory.mktemp("listen-browser"), per_user_daily_page_quota=1000)
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


@pytest.fixture(scope="module")
def job(site, browser) -> str:
    site.converter.output = fake_output()
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, site.base, ALICE)
    page.goto(f"{site.base}/app")
    page.get_by_label("PDF file").set_input_files(
        files=[{"name": "notes.pdf", "mimeType": "application/pdf", "buffer": make_pdf(1)}]
    )
    page.get_by_role("button", name="Upload and convert").click()
    page.wait_for_url(f"{site.base}/jobs/*")
    job_id = page.url.rsplit("/", 1)[-1]
    context.close()
    assert site.run_worker() == job_id
    with session_scope(site.ctx.factory) as session:
        row = session.get(Job, job_id)
        row.html = HTML
        row.html_stale = False
    return job_id


def open_listen(browser, site, path, *, init: str = STUB, signed_in=True, **options):
    context = browser.new_context(viewport=WIDE, **options)
    context.add_init_script(init)
    page = context.new_page()
    if signed_in:
        sign_in(page, site.base, ALICE)
    page.goto(site.base + path)
    return context, page


def spoken(page) -> list[str]:
    return page.evaluate("window.__spoken.map(s => s.text)")


def status(page) -> str:
    page.wait_for_timeout(120)
    return page.locator("[data-listen-status]").inner_text()


def current(page) -> str:
    return page.locator(".listen-item.is-current").inner_text().replace("\n", " ")


def test_play_pause_next_and_stop_work_with_a_stubbed_voice(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    play, pause = page.get_by_role("button", name="Play"), page.get_by_role("button", name="Pause")
    stop = page.get_by_role("button", name="Stop")
    assert pause.is_disabled() and stop.is_disabled()

    play.click()
    assert spoken(page) == ["Page title: Kinematics notes"]
    assert page.locator(".listen-item.is-current").count() == 1
    assert "Page title" in current(page)
    assert page.locator(".listen-item.is-current").get_attribute("aria-current") == "true"
    assert status(page) == "Playing."
    assert play.is_disabled() and pause.is_enabled()
    assert page.evaluate("window.__spoken[0].voice") == "Local Voice"

    page.evaluate("window.__end()")  # the voice finishes an item: the next is spoken
    assert spoken(page)[1] == "Page language: English"

    page.get_by_role("button", name="Next item").click()
    assert spoken(page)[-1] == "Main landmark"
    assert "Main landmark" in current(page)
    assert "Item 3 of" in page.locator("[data-listen-position]").inner_text()

    pause.click()
    assert page.evaluate("window.__paused") is True
    assert status(page) == "Paused."
    play.click()
    assert page.evaluate("window.__paused") is False
    assert status(page) == "Playing."

    page.get_by_role("button", name="Next heading").click()
    assert spoken(page)[-1] == "Heading level 1: Kinematics"
    page.get_by_role("button", name="Next heading").click()
    assert spoken(page)[-1] == "Heading level 2: Average speed"
    page.get_by_role("button", name="Previous item").click()
    assert "The speed is v equals d over t." in spoken(page)[-1]
    page.get_by_role("button", name="Next equation").click()
    assert spoken(page)[-1] == "Equation: x plus 1"

    stop.click()
    assert status(page) == "Stopped."
    assert page.locator(".listen-item.is-current").count() == 0
    assert pause.is_disabled() and stop.is_disabled() and play.is_enabled()
    context.close()


def test_the_whole_transcript_plays_through_to_the_end(site, browser, job):
    context, page = open_listen(
        browser, site, f"/jobs/{job}/listen", init="window.__auto = true;\n" + STUB, bypass_csp=True
    )
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.get_by_role("button", name="Play").click()
    page.wait_for_function("window.__spoken.length >= 5", timeout=5000)
    items = page.locator("[data-listen-item]").count()
    assert status(page) in {"Playing.", "Finished."}
    page.wait_for_function(
        "document.querySelector('[data-listen-status]').textContent === 'Finished.'", timeout=5000
    )
    assert len(spoken(page)) == items
    context.close()


def test_speed_and_voice_are_used(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.get_by_label("Speed").select_option("1.5")
    page.get_by_role("button", name="Play").click()
    assert page.evaluate("window.__spoken.at(-1).rate") == 1.5
    groups = page.locator("#listen-voice optgroup").evaluate_all("els => els.map(e => e.label)")
    assert groups[0] == "On this device" and groups[1].startswith("Online")
    context.close()


def test_keyboard_shortcuts_work_inside_the_page_and_ignore_modifiers(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.get_by_role("button", name="Play").focus()
    page.keyboard.press("h")
    assert spoken(page)[-1] == "Heading level 1: Kinematics"
    page.keyboard.press("]")
    assert spoken(page)[-1].startswith("The speed is")
    page.keyboard.press("Control+h")  # a browser shortcut is left alone
    assert spoken(page)[-1].startswith("The speed is")
    page.keyboard.press("p")  # nothing was playing: p plays ...
    assert status(page) == "Playing."
    page.keyboard.press("p")  # ... and pauses
    assert page.evaluate("window.__paused") is True
    page.keyboard.press("s")
    assert status(page) == "Stopped."
    page.get_by_label("Speed").focus()
    page.keyboard.press("h")  # in a menu the letter selects, it is not a command
    assert page.locator(".listen-item.is-current").count() == 0
    context.close()


def test_a_screen_reader_user_is_not_interrupted(site, browser, job):
    """Nothing is spoken until Play, focus is never moved by the player, and the status line
    is a polite live region (not assertive, not one line per item)."""
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    assert spoken(page) == []
    assert page.locator("[data-listen-status]").get_attribute("role") == "status"
    page.get_by_role("button", name="Play").click()
    page.evaluate("window.__end()")
    page.evaluate("window.__end()")
    assert page.evaluate("document.activeElement.textContent.trim()") == "Play"
    assert status(page) == "Playing."  # not "Item 2 of ...": the voice says the item
    context.close()


def test_scrolling_follows_prefers_reduced_motion(site, browser, job):
    for reduce, expected in (("reduce", "auto"), ("no-preference", "smooth")):
        context, page = open_listen(browser, site, f"/jobs/{job}/listen", reduced_motion=reduce)
        page.wait_for_selector("[data-listen-controls]:not([hidden])")
        page.get_by_role("button", name="Play").click()
        assert page.evaluate("window.__scrolls.at(-1).behavior") == expected
        context.close()


def test_online_voices_alone_need_an_explicit_choice(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen", init=ONLINE_ONLY + STUB)
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    note = page.locator("[data-listen-voice-note]")
    assert "only online voices" in note.inner_text()
    assert page.get_by_role("button", name="Play").is_disabled()
    page.get_by_role("button", name="Next item").is_disabled()
    assert spoken(page) == []
    page.get_by_label("Voice").select_option("cloud")
    assert "online" in note.inner_text()
    page.get_by_role("button", name="Play").click()
    assert page.evaluate("window.__spoken[0].voice") == "Cloud Voice"
    context.close()


def test_without_speech_the_transcript_still_reads(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen", init=NO_SPEECH)
    page.wait_for_selector("[data-listen-unsupported]:not([hidden])")
    assert page.locator("[data-listen-controls]").is_hidden()
    assert "cannot speak" in page.locator("[data-listen-unsupported]").inner_text()
    assert "Heading level 1" in page.locator("[data-listen-transcript]").inner_text()
    context.close()


def test_without_javascript_the_transcript_is_text_and_the_controls_stay_hidden(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen", java_script_enabled=False)
    assert page.locator("[data-listen-controls]").is_hidden()
    assert "Playing needs JavaScript" in page.locator("noscript").inner_text()
    assert (
        "The speed is v equals d over t." in page.locator("[data-listen-transcript]").inner_text()
    )
    context.close()


def test_hear_this_on_the_review_screen_plays_the_proposal_then_the_pdf(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/review?item=p1-e3")
    button = page.locator("[data-hear]").first
    button.wait_for(state="visible")
    assert button.inner_text().startswith("Hear this")
    button.click()
    text = spoken(page)
    assert text and text[0].startswith("Converted page: x plus 1")
    assert "PDF text: nothing" in " ".join(text)
    assert button.inner_text().startswith("Stop")
    button.click()  # a second press stops it
    assert button.inner_text().startswith("Hear this")
    context.close()


def test_hear_this_with_only_online_voices_asks_before_speaking(site, browser, job):
    context, page = open_listen(
        browser, site, f"/jobs/{job}/review?item=p1-e3", init=ONLINE_ONLY + STUB
    )
    button = page.locator("[data-hear]").first
    button.wait_for(state="visible")
    button.click()
    assert spoken(page) == []
    page.wait_for_timeout(150)
    assert "only online voices" in page.locator("[data-hear-note]").first.inner_text()
    button.click()
    assert spoken(page) and page.evaluate("window.__spoken[0].voice") == "Cloud Voice"
    context.close()


def test_hear_this_is_not_shown_without_speech(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/review?item=p1-e3", init=NO_SPEECH)
    page.wait_for_load_state("networkidle")
    assert page.locator("[data-hear]:visible").count() == 0
    context.close()


def test_the_landing_hero_plays_both_readings(site, browser):
    context, page = open_listen(browser, site, "/", signed_in=False)
    pdf = page.get_by_role("button", name="Play the PDF reading")
    pdf.wait_for(state="visible")
    pdf.click()
    assert spoken(page)[0].startswith("In the PDF a screen reader says: capital delta bold d")
    page.get_by_role("button", name="Play the converted reading").click()
    assert spoken(page)[-1] == (
        "On the converted page a screen reader says: capital delta bold d over capital delta t"
    )
    context.close()


def test_the_demo_listen_page_is_public_and_plays(site, browser):
    context, page = open_listen(browser, site, "/demo/listen", signed_in=False)
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.get_by_role("button", name="Next equation").click()
    assert spoken(page)[-1].startswith("Graphs in this text have perpendicular axes")
    assert "y equals m x plus b" in current(page)
    context.close()


@pytest.mark.parametrize("viewport", [WIDE, NARROW], ids=["1280", "320"])
@pytest.mark.parametrize("where", ["job", "demo", "review", "landing"])
def test_the_pages_pass_axe(site, browser, job, axe_source, where, viewport):
    path = {
        "job": f"/jobs/{job}/listen",
        "demo": "/demo/listen",
        "review": f"/jobs/{job}/review?item=p1-e3",
        "landing": "/",
    }[where]
    context = browser.new_context(bypass_csp=True, viewport=viewport)
    context.add_init_script(STUB)
    page = context.new_page()
    if where in {"job", "review"}:
        sign_in(page, site.base, ALICE)
    page.goto(site.base + path)
    page.wait_for_load_state("networkidle")
    if where in {"job", "demo"}:
        page.get_by_role("button", name="Play").click()  # the highlighted state too
    page.add_script_tag(content=axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    violations = [v for v in results["violations"] if v["impact"] in STRICT]
    context.close()
    assert not violations, describe(violations)
