"""A keyboard walk for Playwright pages: press Tab, look at where focus went.

Used by tests/test_web_keyboard_browser.py. Nothing here clicks: `walk` presses Tab (and
Shift+Tab) and reads the focused element, so what it finds is what a person using only a
keyboard meets. It checks WCAG 2.1.1 (keyboard), 2.1.2 (no keyboard trap), 2.4.1 (bypass
blocks, the skip link), 2.4.3 (focus order), 2.4.7 (focus visible) and 2.4.11 (focus not
obscured), and that every stop has an accessible name (4.1.2, the part a script can see).
What a script cannot judge (whether the order reads well, whether the name is a good one) is
left to the person's screen-reader session (docs/screen-reader-test.md).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

MAX_STOPS = 400

# One element's state right after it took focus, as the page sees it.
DESCRIBE = """() => {
  const seen = window.__kbd = window.__kbd || {map: new WeakMap(), n: 0};
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return {body: true};
  if (!seen.map.has(el)) seen.map.set(el, ++seen.n);
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) ||
    (!!cs.boxShadow && cs.boxShadow !== 'none');
  const vw = innerWidth, vh = innerHeight;
  const inView = r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 &&
    r.top < vh && r.left < vw;
  const cx = Math.min(Math.max(r.left + r.width / 2, 0), vw - 1);
  const cy = Math.min(Math.max(r.top + Math.min(r.height / 2, 8), 0), vh - 1);
  const top = document.elementFromPoint(cx, cy);
  const covered = !!top && !(el === top || el.contains(top) || top.contains(el));
  const named = (el.getAttribute('aria-label') ||
    (el.getAttribute('aria-labelledby') || '').split(/\\s+/).map(
      i => (document.getElementById(i) || {}).textContent || '').join(' ') ||
    (el.labels && el.labels.length ? el.labels[0].textContent : '') ||
    el.textContent || el.value || el.getAttribute('title') || el.getAttribute('alt') || '')
    .replace(/\\s+/g, ' ').trim();
  return {
    id: seen.map.get(el), tag: el.tagName.toLowerCase(), domId: el.id || '',
    type: el.getAttribute('type') || '', role: el.getAttribute('role') || '',
    name: named.slice(0, 70), ring, inView, covered, top: r.top + scrollY, left: r.left,
    inMain: !!el.closest('main'),
    landmark: el.closest('header') ? 1 : el.closest('main') ? 2 : el.closest('footer') ? 3 : 0,
  };
}"""

# Every control a keyboard can reach on the page, by the same ids as `DESCRIBE`: natively
# focusable things (and tabindex >= 0) that are shown, enabled and not inert, and not inside a
# closed <details> (its summary is the stop); a radio group is one stop.
TABBABLE = """() => {
  const seen = window.__kbd = window.__kbd || {map: new WeakMap(), n: 0};
  const sel = 'a[href], button, input, select, textarea, summary, iframe, ' +
    '[tabindex], [contenteditable=""], [contenteditable="true"]';
  const out = []; const radios = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (el.disabled || el.closest('[inert]') || el.getAttribute('type') === 'hidden') continue;
    if (el.hasAttribute('tabindex') && parseInt(el.getAttribute('tabindex'), 10) < 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    let hidden = false;
    for (let d = el.closest("details"); d;
         d = d.parentElement && d.parentElement.closest("details")) {
      const own = el.tagName === 'SUMMARY' && el.parentElement === d;
      if (!d.open && !own) { hidden = true; break; }
    }
    if (hidden) continue;
    if (el.type === 'radio') {
      const key = (el.form ? 'f' : 'd') + '|' + el.name;
      if (radios.has(key)) continue;
      radios.add(key);
    }
    if (!seen.map.has(el)) seen.map.set(el, ++seen.n);
    out.push({id: seen.map.get(el), tag: el.tagName.toLowerCase(), domId: el.id || '',
      name: (el.getAttribute('aria-label') || el.textContent || el.value || '')
        .replace(/\\s+/g, ' ').trim().slice(0, 50)});
  }
  return out;
}"""


@dataclass
class Walk:
    """What one pass over a page with the Tab key found."""

    stops: list[dict] = field(default_factory=list)
    tabbable: list[dict] = field(default_factory=list)
    ended: str = ""  # "left" (Tab left the page) or "wrapped" (came round to the first stop)
    looped: bool = False  # a control came round again before the page was done
    problems: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [f"{s['tag']}:{s['name'] or s['domId']}" for s in self.stops]


def focus_state(page: Any) -> dict:
    return page.evaluate(DESCRIBE)


def start_of_page(page: Any) -> None:
    """Focus at the very start of the document, as after a fresh page load: nothing focused,
    and the sequential-focus starting point at the top."""
    page.evaluate(
        "() => { if (document.activeElement) document.activeElement.blur();"
        " window.scrollTo(0, 0); getSelection().removeAllRanges();"
        " const s = document.createElement('span'); document.body.prepend(s);"
        " s.setAttribute('tabindex', '-1'); s.focus(); s.remove(); }"
    )


def check_stop(stop: dict, label: str) -> list[str]:
    where = f"{label}: {stop['tag']} {stop['name']!r}"
    found = []
    if not stop["inView"]:
        found.append(f"{where} is not in the window when focused")
    if not stop["ring"]:
        found.append(f"{where} shows no focus indicator")
    if stop["covered"]:
        found.append(f"{where} is covered by another element when focused")
    if not stop["name"]:
        found.append(f"{where} has no accessible name")
    return found


def walk(page: Any, *, label: str, shift_back: bool = True) -> Walk:
    """Tab through the page from its start, checking every stop; then Shift+Tab back."""
    out = Walk()
    start_of_page(page)
    out.tabbable = page.evaluate(TABBABLE)
    seen: set[int] = set()
    url = page.url
    for _ in range(MAX_STOPS):
        page.keyboard.press("Tab")
        state = focus_state(page)
        if page.url != url:  # WCAG 3.2.1: moving focus must not change the page
            out.problems.append(f"{label}: moving focus to a control changed the page address")
            break
        if state.get("body"):
            out.ended = "left"
            break
        if state["id"] in seen:
            if state["id"] == out.stops[0]["id"]:
                out.ended = "wrapped"
            else:
                out.looped = True
            break
        seen.add(state["id"])
        out.stops.append(state)
    if not out.ended and not out.looped:
        out.problems.append(f"{label}: focus never left the page or came round (a trap?)")
    if out.looped:
        out.problems.append(f"{label}: focus looped inside the page before reaching its end")
    for stop in out.stops:
        out.problems.extend(check_stop(stop, label))
    reached = {s["id"] for s in out.stops}
    for control in out.tabbable:
        if control["id"] not in reached:
            out.problems.append(
                f"{label}: {control['tag']} {control['name'] or control['domId']!r} "
                "is on the page but Tab never reaches it"
            )
    out.problems.extend(landmark_order(out.stops, label))
    if shift_back and len(out.stops) > 2 and out.ended:
        # Shift+Tab from where the walk ended visits the stops in reverse (after a wrap the
        # first Shift+Tab goes from the first stop to the last).
        backwards = list(reversed(out.stops if out.ended == "left" else out.stops[1:]))
        for expected in backwards:
            page.keyboard.press("Shift+Tab")
            state = focus_state(page)
            if state.get("body") or state["id"] != expected["id"]:
                found = state.get("name", "the page")
                out.problems.append(
                    f"{label}: Shift+Tab does not walk back through the same stops "
                    f"(expected {expected['name']!r}, got {found!r})"
                )
                break
    return out


def landmark_order(stops: list[dict], label: str) -> list[str]:
    """Header, then main, then footer: the page's own parts are visited in that order
    (skip links, outside every landmark, come first)."""
    found = []
    last = 0
    for stop in stops:
        rank = stop["landmark"]
        if rank == 0:
            if last:
                found.append(f"{label}: {stop['name']!r} is outside every landmark, after one")
            continue
        if rank < last:
            found.append(f"{label}: {stop['name']!r} comes after a later part of the page")
        last = max(last, rank)
    return found


def skip_link_works(page: Any, *, label: str) -> list[str]:
    """First Tab: the skip link, shown. Enter: focus is in `main`; the next Tab is a
    control of the page's own content."""
    problems = []
    start_of_page(page)
    page.keyboard.press("Tab")
    state = focus_state(page)
    if state.get("body") or "skip" not in state["name"].lower():
        return [f"{label}: the first Tab stop is {state.get('name')!r}, not the skip link"]
    if not (state["inView"] and state["ring"] and not state["covered"]):
        problems.append(f"{label}: the skip link is not visible when focused: {state}")
    page.keyboard.press("Enter")
    inside = page.evaluate(
        "() => ({main: !!document.activeElement.closest('main') ||"
        " document.activeElement.id === 'main', hash: location.hash})"
    )
    if not inside["main"] or inside["hash"] != "#main":
        problems.append(f"{label}: Enter on the skip link left focus outside main: {inside}")
    page.keyboard.press("Tab")
    after = focus_state(page)
    if after.get("body") or not after["inMain"]:
        problems.append(f"{label}: Tab after the skip link did not land in main: {after}")
    return problems


def focus_summary(page: Any) -> dict:
    """Where focus is, for assertions after an action."""
    return page.evaluate(
        """() => {
          const el = document.activeElement;
          const body = !el || el === document.body || el === document.documentElement;
          const text = body ? '' : (el.textContent || el.value || '').replace(/\\s+/g, ' ').trim();
          return {body, tag: body ? 'body' : el.tagName.toLowerCase(), id: body ? '' : el.id,
            role: body ? '' : (el.getAttribute('role') || ''), text: text.slice(0, 80),
            inMain: !body && !!el.closest('main'), path: location.pathname + location.search};
        }"""
    )


def wait_focus(page: Any, *, timeout_s: float = 5.0) -> dict:
    """Where focus is once the page has put it somewhere. Chromium applies `autofocus` (the
    error summary, a notice) at the next rendering opportunity after load, so right after a
    response focus can still be on <body>."""
    deadline = time.monotonic() + timeout_s
    state = focus_summary(page)
    while state["body"] and time.monotonic() < deadline:
        time.sleep(0.05)
        state = focus_summary(page)
    return state


def tab_to(page: Any, pattern: str, *, shift: bool = False, limit: int = 150) -> dict:
    """Press Tab (or Shift+Tab) until the focused control's name matches `pattern`; fail if
    the page's controls end first. This is how a keyboard user finds a control."""
    rx = re.compile(pattern)
    for _ in range(limit):
        page.keyboard.press("Shift+Tab" if shift else "Tab")
        state = focus_state(page)
        if not state.get("body") and rx.search(state["name"]):
            return state
    raise AssertionError(f"Tab never reached a control named {pattern!r}")


def choose_file_by_keyboard(page: Any, files: list[dict], *, timeout_ms: int = 4000) -> str:
    """Give the focused file field its files the way a keyboard user does: Space, then Enter,
    each with the chooser listener registered first. Headless Chromium under load sometimes
    does not raise the chooser for a key press; then the files are set on the focused field
    itself (after checking it is a focused file input) and "set" is returned."""
    assert page.evaluate("document.activeElement.type === 'file'"), "focus is not on a file field"
    for key in ("Space", "Enter"):
        try:
            with page.expect_file_chooser(timeout=timeout_ms) as chooser:
                page.keyboard.press(key)
        except Exception as exc:  # Playwright's TimeoutError: no chooser for this key
            if type(exc).__name__ != "TimeoutError":
                raise
            continue
        chooser.value.set_files(files)
        return key
    page.locator(":focus").set_input_files(files)
    return "set"


def activate(page: Any, key: str = "Enter", *, navigates: bool = True) -> None:
    """Press a key on the focused control; wait for the page it leads to when it navigates
    (a link, or a form that posts and redirects)."""
    if navigates:
        with page.expect_navigation():
            page.keyboard.press(key)
        page.wait_for_load_state("load")
    else:
        page.keyboard.press(key)
