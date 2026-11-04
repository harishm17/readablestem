"""Screenshots of the web app for design review, with production-like settings.

Run: PYTHONPATH=tests uv run python scripts/design_shots.py OUT_DIR [--pages landing,compare]
     [--views 1440,390] [--schemes light,dark]

It starts the app on a free port with the test fixtures' fake converter and seeds one converted
review document, then takes full-page screenshots of the public pages and the signed-in pages at
1440x900 and 390x844, light and dark, and a forced-colours capture of each.

The settings are the ones the live beta runs with, not the development defaults: the Anthropic
provider, scanned pages, tables, long descriptions and notation memory on, open sign-up, Google
sign-in configured, a contact address, and no development sign-in link. The signed-in pages are
reached by a session made before the development login is switched off, so nothing on a page
says "Development sign-in" and reviewers see what a visitor sees. Keep these defaults: a
screenshot taken with development settings misleads (the blind review of the redesign read
"\\omega" as speech and "help@example.org" as the contact address in such shots).
"""

# ruff: noqa: E501  (the JavaScript measuring function is one long block)

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import AXE_CACHE, WCAG_TAGS, free_port, sign_in

PRODUCTION_LIKE = {
    "model_provider": "anthropic",
    "feature_image_pages": True,
    "feature_tables": True,
    "feature_long_descriptions": True,
    "feature_notation_memory": True,
    "signup_mode": "open",
    "google_client_id": "readablestem.apps.googleusercontent.com",
    "google_client_secret": "not-a-real-secret",
    "contact_email": "help@readablestem.com",
    "per_user_daily_page_quota": 1000,
}
PUBLIC = {
    "landing": "/",
    "style": "/style",
    "guides": "/guides",
    "compare": "/guides/readablestem-mathpix-latexml",
    "ada": "/guides/ada-title-ii-stem-course-materials",
    "offices": "/for-accessibility-offices",
    "posting": "/help/posting",
    "limits": "/limits",
    "privacy": "/privacy",
    "login": "/login",
    "status": "/status",
}
SIGNED_IN = {
    "app": "/app",
    "account": "/account",
    "job": "/jobs/{job}",
    "review-split": "/jobs/{job}/review",
    "review-figure": "/jobs/{job}/review?item=p1-e6",
    "review-list": "/jobs/{job}/review/list",
    "symbols": "/jobs/{job}/symbols?view=split",
}
VIEWS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}

# --check: what a screenshot cannot show. Interactive things under 24px (inline links in running
# text are exempt), text under 7:1 against its real background, text under 14px, and sideways
# overflow at 720 (200% zoom of 1440) and 320 CSS px.
MEASURE = """() => {
  const lum = (c) => { const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const parse = (s) => { const m = s.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { c: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 }; };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const p = parse(getComputedStyle(e).backgroundColor); if (p && p.a > 0.9) return p.c; } return [255, 255, 255]; };
  const out = { small: [], contrast: [], font: [] };
  const seen = new Set();
  for (const el of document.querySelectorAll("a, button, summary, input, select, textarea, [role=button]")) {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === "hidden" || el.closest("[hidden]")) continue;
    if (el.matches("a") && cs.display === "inline" && el.closest("p, li, dd, td, th, figcaption, label, summary")) continue;
    if (el.matches("input[type=hidden]") || el.classList.contains("skip-link")) continue;
    if (r.height < 24 || r.width < 24) out.small.push(`${el.tagName.toLowerCase()}.${el.className} "${(el.textContent || el.value || "").trim().slice(0, 24)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    const t = n.textContent.trim(); const el = n.parentElement;
    if (!t || !el || ["SCRIPT", "STYLE"].includes(el.tagName)) continue;
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    if (!r.width || !r.height || cs.visibility === "hidden" || el.closest(".visually-hidden, [hidden], .page-frame, .converted, svg, math, .cmp-row, .table-view, .sheet")) continue;
    const key = el.tagName + el.className + cs.color + cs.fontSize; if (seen.has(key)) continue; seen.add(key);
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el); const a = fg.a;
    const mix = fg.c.map((v, i) => v * a + bg[i] * (1 - a));
    const ratio = (Math.max(lum(mix), lum(bg)) + 0.05) / (Math.min(lum(mix), lum(bg)) + 0.05);
    if (ratio < 7) out.contrast.push(`${el.tagName.toLowerCase()}.${el.className} "${t.slice(0, 24)}" ${ratio.toFixed(2)}`);
    if (parseFloat(cs.fontSize) < 14) out.font.push(`${el.tagName.toLowerCase()}.${el.className} ${cs.fontSize}`);
  }
  return out;
}"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("out", type=Path)
    parser.add_argument("--pages", default="")
    parser.add_argument("--views", default="1440,390")
    parser.add_argument("--schemes", default="light,dark")
    parser.add_argument("--check", action="store_true", help="measure instead of screenshotting")
    args = parser.parse_args()
    only = {p for p in args.pages.split(",") if p}
    args.out.mkdir(parents=True, exist_ok=True)

    import tempfile

    env = WebEnv(Path(tempfile.mkdtemp()), **PRODUCTION_LIKE)
    env.converter.output = review_output()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(env.app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    env.allow(ALICE)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        seed = browser.new_context()
        page = seed.new_page()
        sign_in(page, base, ALICE)  # the development login, used once to make the session
        page.goto(f"{base}/app")
        page.get_by_label("PDF file").set_input_files(
            files=[
                {
                    "name": "Ch2_Kinematics_lecture_notes.pdf",
                    "mimeType": "application/pdf",
                    "buffer": review_pdf(),
                }
            ]
        )
        page.get_by_role("button", name="Upload and convert").click()
        page.wait_for_url(f"{base}/jobs/*")
        job = page.url.rsplit("/", 1)[-1]
        state = seed.storage_state()
        seed.close()
        env.run_worker()
        env.settings.dev_login_enabled = False  # no "Development sign-in" from here on

        def shoot(name: str, path: str, signed: bool, view: str, scheme: str, **options) -> None:
            context = browser.new_context(
                viewport=VIEWS[view],
                color_scheme=scheme,
                storage_state=state if signed else None,
                **options,
            )
            tab = context.new_page()
            tab.goto(base + path.replace("{job}", job))
            tab.wait_for_load_state("networkidle")
            suffix = "forced" if options.get("forced_colors") else f"{view}-{scheme}"
            tab.screenshot(path=str(args.out / f"{name}-{suffix}.png"), full_page=True)
            context.close()

        def check(name: str, path: str, signed: bool) -> None:
            axe = AXE_CACHE.read_text(encoding="utf-8")
            for view, scheme in (
                ("1440", "light"),
                ("1440", "dark"),
                ("390", "light"),
                ("390", "dark"),
            ):
                context = browser.new_context(
                    viewport=VIEWS[view],
                    color_scheme=scheme,
                    storage_state=state if signed else None,
                    bypass_csp=True,
                )
                tab = context.new_page()
                tab.goto(base + path.replace("{job}", job))
                tab.wait_for_load_state("networkidle")
                found = tab.evaluate(MEASURE)
                tab.add_script_tag(content=axe)
                axe_result = tab.evaluate(
                    "async (t) => (await axe.run(document, {runOnly: {type: 'tag', values: t}})).violations"
                    ".map(v => v.id + ' ' + v.nodes.length)",
                    WCAG_TAGS,
                )
                print(f"CHECK {name} {view} {scheme} axe={axe_result}")
                for kind in ("small", "contrast", "font"):
                    for line in found[kind][:8]:
                        print(f"   {kind}: {line}")
                context.close()
            for width in (720, 320):
                context = browser.new_context(
                    viewport={"width": width, "height": 700},
                    storage_state=state if signed else None,
                )
                tab = context.new_page()
                tab.goto(base + path.replace("{job}", job))
                tab.wait_for_load_state("networkidle")
                over = tab.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                print(f"CHECK {name} {width}px overflow={over}")
                context.close()

        pages = [(n, u, False) for n, u in PUBLIC.items()] + [
            (n, u, True) for n, u in SIGNED_IN.items()
        ]
        for name, path, signed in pages:
            if only and name not in only:
                continue
            if args.check:
                check(name, path, signed)
                continue
            for view in args.views.split(","):
                for scheme in args.schemes.split(","):
                    shoot(name, path, signed, view, scheme)
            shoot(
                name, path, signed, "1440", "light", forced_colors="active", reduced_motion="reduce"
            )
        browser.close()
    server.should_exit = True


if __name__ == "__main__":
    main()
