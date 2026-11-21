"""The landing page's whole-product picture: the public demo page as a visitor sees it.

Run: PYTHONPATH=tests uv run python scripts/hero_screenshot.py

It starts the app on a free port, opens `/demo` in Chromium (the browser the browser tests use)
at a fixed 720 x 462 window in the light theme, scrolled to the paragraph that leads into equation
2.4 (the one "Hear the difference" on the landing page shows), and writes the window to
`web/static/hero/product.png`. Nothing is cropped or edited afterwards: the file is the window,
so the page shows the whole picture. The demo is OpenStax Physics 2.3 (CC BY 4.0), converted by
this tool, so the picture holds no private file. The app never runs this; the committed file is
what the landing page serves. Take it again when the demo page or the student page's look changes.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from fixtures.web.support import WebEnv
from test_web_browser import free_port

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "readablestem" / "web" / "static" / "hero" / "product.png"
WINDOW = {"width": 720, "height": 462}


def main() -> None:
    env = WebEnv(Path(tempfile.mkdtemp()))
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(env.app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        pass
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=WINDOW, color_scheme="light", reduced_motion="reduce")
        page.goto(f"http://127.0.0.1:{port}/demo")
        page.wait_for_load_state("networkidle")
        # The paragraph that leads into equation 2.4 starts the window.
        page.evaluate(
            "const p = document.getElementById('p3-e5').previousElementSibling;"
            "window.scrollTo(0, p.getBoundingClientRect().top + window.scrollY - 24)"
        )
        page.screenshot(path=str(OUT), full_page=False)
        browser.close()
    server.should_exit = True
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
