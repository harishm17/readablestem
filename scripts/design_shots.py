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

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv
from test_web_browser import free_port, sign_in

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("out", type=Path)
    parser.add_argument("--pages", default="")
    parser.add_argument("--views", default="1440,390")
    parser.add_argument("--schemes", default="light,dark")
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

        pages = [(n, u, False) for n, u in PUBLIC.items()] + [
            (n, u, True) for n, u in SIGNED_IN.items()
        ]
        for name, path, signed in pages:
            if only and name not in only:
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
