"""What still stands between the beta and its first real users, checked from the repo.

`launch_blockers()` lists unfinished public copy (a `TODO(Harish)` or a "Draft" note on a
page anyone can open), a missing or placeholder demo, and a missing eval baseline (the CI
gate skips without one). Given the settings, it also lists what a setting turns on that
the public pages do not describe yet (Microsoft sign-in and the privacy notice).
`a11y-stem ops launch-check` prints the list and exits 1 while it is not empty; the web
app logs `launch_blockers` with the count at a production start.

It does not refuse to start: a staging deploy runs with ENV=prod and must work before the
copy is final. Each item names the file to change; none of it is document content.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a11y_stem.config import Settings

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
DEMO_PAGE = WEB_DIR / "static" / "demo" / "index.html"
# Pages anyone can open without signing in.
PUBLIC_TEMPLATES = (
    "landing.html",
    "demo_pending.html",
    "privacy.html",
    "terms.html",
    "limits.html",
    "help_posting.html",
    "status.html",
    "login.html",
    "_signin.html",
    "org_not_set_up.html",
)
# Written into a demo built with the offline client (`a11y-stem demo build --fake`);
# the same string as eval.render.PLACEHOLDER_ATTR (kept here so this module stays light).
FAKE_DEMO_MARKER = 'data-a11y-stem-demo="placeholder"'


def launch_blockers(
    repo_root: Path | None = None, *, settings: Settings | None = None
) -> list[str]:
    """Plain-language list of unfinished items, empty when the beta is ready to share."""
    items: list[str] = []
    privacy = TEMPLATES_DIR / "privacy.html"
    notice = privacy.read_text(encoding="utf-8") if privacy.is_file() else None
    if settings is not None and notice is not None:
        # A feature that sends or keeps something new needs its words in the notice.
        for on, word, what in (
            (settings.ms_sign_in_enabled, "Microsoft", "Microsoft sign-in is on (MS_CLIENT_ID)"),
            (settings.feature_tables, "rebuild tables", "FEATURE_TABLES sends tables to OpenAI"),
            (
                settings.feature_notation_memory,
                "notation",
                "FEATURE_NOTATION_MEMORY keeps courses and sends their notation",
            ),
        ):
            if on and word not in notice:
                items.append(
                    f"web/templates/privacy.html: {what} but the privacy notice does not say so"
                )
    for name in PUBLIC_TEMPLATES:
        path = TEMPLATES_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "TODO(Harish)" in text:
            items.append(f"web/templates/{name}: still has a TODO(Harish) note")
        if 'class="draft-note"' in text:
            items.append(f"web/templates/{name}: still shows the draft note")
    if not DEMO_PAGE.is_file():
        items.append("web/static/demo/index.html: no demo yet (a11y-stem demo build)")
    elif FAKE_DEMO_MARKER in DEMO_PAGE.read_text(encoding="utf-8"):
        items.append("web/static/demo/index.html: placeholder output from --fake; rebuild it")
    if repo_root is not None:
        eval_dir = repo_root / "data" / "eval"
        if not list(eval_dir.glob("baseline-*.json")):
            items.append("data/eval: no baseline-<task>.json committed; the CI eval gate skips")
    return items
