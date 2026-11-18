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

from fixtures.web.support import ALICE, WebEnv, as_current, fake_output, make_pdf
from readablestem.web.db import Job, session_scope
from test_web_browser import WCAG_TAGS, describe, free_port, sign_in
from test_web_browser import axe_source as axe_source  # fixture, re-exported for pytest
from test_web_browser import browser as browser  # fixture, re-exported for pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

WIDE = {"width": 1280, "height": 900}
NARROW = {"width": 320, "height": 640}
STRICT = {"moderate", "serious", "critical"}

HTML = as_current(
    '<!doctype html><html lang="en"><head><title>Kinematics notes</title></head><body><main>'
    "<h1>Kinematics</h1><p>The speed is "
    '<math id="p1-e2"><mi>v</mi><mo>=</mo><mi>d</mi><mo>/</mo><mi>t</mi></math>.</p>'
    "<h2>Average speed</h2><p>It is distance over time.</p>"
    '<div class="equation" id="p1-e3"><math display="block"><mi>x</mi><mo>+</mo><mn>1</mn>'
    "</math></div><ul><li>one</li><li>two</li></ul>"
    '<hr role="doc-pagebreak" aria-label="Page 2" id="pg2"></main></body></html>'
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
    page.locator("#pdf").set_input_files(
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
    play = page.locator("[data-listen=play]")
    stop = page.locator("[data-listen=stop]")
    page.evaluate("document.querySelectorAll('.listen-more').forEach(d => d.open = true)")
    assert play.inner_text() == "Play" and stop.is_disabled()

    play.click()  # Play starts at the lesson, past the title, the language and the bare landmark
    assert spoken(page) == ["Heading level 1: Kinematics"]
    assert page.locator(".listen-item.is-current").count() == 1
    assert "Kinematics" in current(page)
    assert page.locator(".listen-item.is-current").get_attribute("aria-current") == "true"
    assert status(page) == "Playing."
    assert play.inner_text() == "Pause"  # one button plays and pauses
    assert page.evaluate("window.__spoken[0].voice") == "Local Voice"

    page.evaluate("window.__end()")  # the voice finishes an item: the next is spoken
    assert spoken(page)[1] == "The speed is v equals d divided by t."

    page.get_by_role("button", name="Next", exact=True).click()
    assert spoken(page)[-1] == "Heading level 2: Average speed"
    assert "Item 6 of" in page.locator("[data-listen-position]").inner_text()

    play.click()  # Pause
    assert page.evaluate("window.__paused") is True
    assert status(page) == "Paused."
    assert play.inner_text() == "Play"
    play.click()
    assert page.evaluate("window.__paused") is False
    assert status(page) == "Playing."

    page.get_by_role("button", name="Previous", exact=True).click()
    assert "The speed is v equals d divided by t." in spoken(page)[-1]
    page.get_by_role("button", name="Next heading").click()
    assert spoken(page)[-1] == "Heading level 2: Average speed"
    page.get_by_role("button", name="Next equation").click()
    assert spoken(page)[-1] == "Equation: x plus 1"

    where = current(page)
    stop.click()  # Stop keeps the place; Play says where it will start and starts there
    assert status(page) == "Stopped."
    assert current(page) == where and stop.is_disabled() and play.is_enabled()
    assert play.inner_text() == "Resume from item 8 of 12"
    play.click()
    assert spoken(page)[-1] == "Equation: x plus 1"
    stop.click()
    page.get_by_role("button", name="Play from the beginning").click()  # the restart
    assert spoken(page)[-1] == "Page title: Kinematics notes"
    context.close()


def test_keyboard_shortcuts_work_inside_the_page_and_ignore_modifiers(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.evaluate("document.querySelectorAll('.listen-more').forEach(d => d.open = true)")
    page.locator("[data-listen=play]").focus()
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
    place = current(page)
    page.keyboard.press("h")  # in a menu the letter selects, it is not a command
    assert current(page) == place
    page.get_by_label("Keyboard shortcuts").uncheck()
    page.locator("[data-listen=play]").focus()
    page.keyboard.press("h")  # switched off: the letter does nothing
    assert current(page) == place
    context.close()


def test_a_screen_reader_user_is_not_interrupted(site, browser, job):
    """Nothing is spoken until Play, focus is never moved by the player, and the status line
    is a polite live region (not assertive, not one line per item)."""
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.evaluate("document.querySelectorAll('.listen-more').forEach(d => d.open = true)")
    assert spoken(page) == []
    assert page.locator("[data-listen-status]").get_attribute("role") == "status"
    page.locator("[data-listen=play]").click()
    page.evaluate("window.__end()")
    page.evaluate("window.__end()")
    assert page.evaluate("document.activeElement.textContent.trim()") == "Pause"  # focus stayed
    assert status(page) == "Playing."  # not "Item 2 of ...": the voice says the item
    context.close()


def test_the_place_and_settings_come_back_and_hold_no_text(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.evaluate("document.querySelectorAll('.listen-more').forEach(d => d.open = true)")
    page.get_by_label("Speed").select_option("1.5")
    page.get_by_label("Voice").select_option("cloud")
    # A click on an item and a Pages link in the index each set the place; nothing is spoken.
    page.locator("#item-9").click()
    assert "Item 9 of 12" in page.locator("[data-listen-position]").inner_text()
    page.locator(".listen-index").evaluate("d => d.open = true")
    page.get_by_role("link", name="Page 2").click()
    assert current(page).startswith("Page break")
    assert spoken(page) == []
    page.reload()  # a return visit offers the place and plays from it, with speed and voice kept
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    play = page.locator("[data-listen=play]")
    assert play.inner_text() == "Resume from item 12 of 12"
    assert page.get_by_label("Speed").input_value() == "1.5"
    play.click()
    assert page.evaluate("window.__spoken[0].voice") == "Cloud Voice"
    assert page.evaluate("window.__spoken[0].rate") == 1.5
    assert spoken(page)[0].startswith("Page break")
    stored = page.evaluate("JSON.stringify(Object.assign({}, localStorage))")
    for word in ("Kinematics", "speed", "distance", "Average", "equals"):
        assert word not in stored  # ids and settings only, never the document's words

    # A place whose id is gone (the document changed) falls back to the nearest earlier item
    # that still exists, and a document with no place at all starts at the lesson.
    key = page.evaluate(
        "Object.keys(localStorage).find(k => k.includes(':') && !k.endsWith('places'))"
    )
    page.evaluate(
        "([k, v]) => localStorage.setItem(k, v)",
        [key, '{"at": "gone", "back": ["gone2", "p1-e3"]}'],
    )
    page.reload()
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    assert page.locator("[data-listen=play]").inner_text() == "Resume from item 8 of 12"
    # Places of other documents do not pile up: twenty are kept, the oldest dropped.
    page.evaluate(
        """() => {
          const keys = [];
          for (let n = 0; n < 25; n += 1) {
            keys.push('readablestem.listen:doc' + n + ':1');
            localStorage.setItem(keys[n], '{"at": "x"}');
          }
          localStorage.setItem('readablestem.listen:places', JSON.stringify({keys}));
        }"""
    )
    page.locator("#item-9").click()
    kept = page.evaluate("Object.keys(localStorage).filter(k => k.includes('doc'))")
    assert len(kept) == 19 and "readablestem.listen:doc0:1" not in kept
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    assert page.locator("[data-listen=play]").inner_text() == "Play"
    context.close()


def test_a_browser_that_blocks_storage_still_plays(site, browser, job):
    blocked = (
        "Object.defineProperty(window, 'localStorage', "
        "{get() { throw new Error('blocked'); }, configurable: true});\n"
    )
    context, page = open_listen(browser, site, f"/jobs/{job}/listen", init=blocked + STUB)
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    page.locator("[data-listen=play]").click()
    assert spoken(page) == ["Heading level 1: Kinematics"]
    context.close()


def test_scrolling_follows_prefers_reduced_motion(site, browser, job):
    for reduce, expected in (("reduce", "auto"), ("no-preference", "smooth")):
        context, page = open_listen(browser, site, f"/jobs/{job}/listen", reduced_motion=reduce)
        page.wait_for_selector("[data-listen-controls]:not([hidden])")
        page.evaluate("document.querySelectorAll('.listen-more').forEach(d => d.open = true)")
        page.locator("[data-listen=play]").click()
        assert page.evaluate("window.__scrolls.at(-1).behavior") == expected
        context.close()


def test_online_voices_alone_need_an_explicit_choice(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen", init=ONLINE_ONLY + STUB)
    page.wait_for_selector("[data-listen-transport]:not([hidden])")
    # The explanation is beside Play, with the controls still folded away.
    note = page.locator("[data-listen-voice-note]")
    assert "Only online voices" in note.inner_text()
    assert note.is_visible() and not page.locator(".listen-more").evaluate("d => d.open")
    play = page.locator("[data-listen=play]")
    assert play.is_disabled()
    assert page.get_by_role("button", name="Next", exact=True).is_disabled()
    assert spoken(page) == []
    note.get_by_role("button", name="Choose a voice").click()  # opens the voice menu
    assert page.locator(".listen-more").evaluate("d => d.open")
    assert page.evaluate("document.activeElement.id") == "listen-voice"
    page.get_by_label("Voice").select_option("cloud")
    assert "Online voice" in note.inner_text()
    play.click()
    assert page.evaluate("window.__spoken[0].voice") == "Cloud Voice"
    context.close()


def test_with_no_voice_at_all_the_menu_goes_and_a_line_beside_play_says_why(site, browser, job):
    context, page = open_listen(
        browser, site, f"/jobs/{job}/listen", init="window.__voices = [];\n" + STUB
    )
    page.wait_for_selector("[data-listen-transport]:not([hidden])")
    page.wait_for_timeout(1500)  # the page waits a moment for voices to turn up
    assert page.locator("[data-listen-voice-field]").is_hidden() or not page.locator(
        ".listen-more"
    ).evaluate("d => d.open")
    note = page.locator("[data-listen-voice-note]")
    assert note.is_visible() and "No voice on this device" in note.inner_text()
    play = page.locator("[data-listen=play]")
    assert play.is_disabled()
    assert play.get_attribute("aria-describedby") == "listen-voice-note"
    # The way out is right there: a link to the transcript, which still reads.
    link = note.get_by_role("link", name="Read the transcript")
    assert link.get_attribute("href") == "#transcript-heading"
    # Beside Play: in the transport, which is in view, not in the collapsed settings.
    box_play, box_note = play.bounding_box(), note.bounding_box()
    assert box_play and box_note and 0 <= box_note["y"] - box_play["y"] < 120
    context.close()


def test_without_javascript_the_transcript_is_text_and_the_controls_stay_hidden(site, browser, job):
    context, page = open_listen(browser, site, f"/jobs/{job}/listen", java_script_enabled=False)
    assert page.locator("[data-listen-controls]").is_hidden()
    assert "Playing needs JavaScript" in page.locator("noscript").inner_text()
    assert (
        "The speed is v equals d divided by t."
        in page.locator("[data-listen-transcript]").inner_text()
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
        page.locator("[data-listen=play]").click()  # the highlighted state too
    page.add_script_tag(content=axe_source)
    results = page.evaluate(
        "async (tags) => await axe.run(document, "
        "{runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']})",
        WCAG_TAGS,
    )
    violations = [v for v in results["violations"] if v["impact"] in STRICT]
    context.close()
    assert not violations, describe(violations)


def test_the_scroll_offset_follows_the_real_height_of_the_transport(site, browser, job):
    """With larger text the transport wraps to more lines: an item reached by a link or by
    following along must still land below it, not under it."""
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.set_viewport_size({"width": 360, "height": 700})
    page.wait_for_selector("[data-listen-transport]:not([hidden])")
    page.evaluate("document.documentElement.style.fontSize = '200%'")
    page.wait_for_timeout(300)
    height = page.locator("[data-listen-transport]").evaluate(
        "e => e.getBoundingClientRect().height"
    )
    offset = page.locator("[data-listen-player]").evaluate(
        "e => parseFloat(e.style.getPropertyValue('--listen-sticky'))"
    )
    assert abs(offset - height) <= 2 and height > 90
    page.evaluate("location.hash = '#item-6'")
    page.wait_for_timeout(300)
    top = page.locator("#item-6").evaluate("e => e.getBoundingClientRect().top")
    assert top >= height - 1
    context.close()


def test_a_wide_window_shows_the_selected_equation_without_stealing_focus(site, browser, job):
    """F-465: the second column draws the selected item, is hidden from assistive technology,
    never takes focus and is not a live region; each item links to its student page place."""
    context, page = open_listen(browser, site, f"/jobs/{job}/listen")
    page.wait_for_selector("[data-listen-controls]:not([hidden])")
    panel = page.locator("[data-listen-thing-panel]")
    assert panel.get_attribute("aria-hidden") == "true" and panel.get_attribute("aria-live") is None
    assert not panel.is_visible()  # nothing selected yet
    page.locator('[data-listen-id="p1-e3"] .listen-text').click()
    assert panel.is_visible() and panel.locator("math").count() == 1
    assert page.evaluate("document.activeElement.closest('[data-listen-thing-panel]')") is None
    assert page.evaluate("document.activeElement.tagName") in {"MAIN", "BODY"}  # not the panel
    link = page.locator('[data-listen-id="p1-e3"] a.listen-place')
    assert link.get_attribute("href") == f"/jobs/{job}/view#p1-e3"
    page.locator('.listen-item[data-kinds^="heading"] .listen-text').first.click()  # no thing
    assert not panel.is_visible()
    page.set_viewport_size(NARROW)
    page.locator('[data-listen-id="p1-e3"] .listen-text').click()
    assert not panel.is_visible()  # a narrow window has the transcript alone
    context.close()
