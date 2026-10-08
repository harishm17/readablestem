"""What still stands between the beta and its first real users, checked from the repo.

`launch_blockers()` lists unfinished public copy (a `TODO(Harish)` or a "Draft" note on a
page anyone can open), a missing or placeholder demo, and a missing eval baseline (the CI
gate skips without one). `a11y-stem ops launch-check` prints the list and exits 1 while
it is not empty; the web app logs `launch_blockers` with the count at a production start.

It does not refuse to start: a staging deploy runs with ENV=prod and must work before the
copy is final. Each item names the file to change; none of it is document content.
"""

from __future__ import annotations

from pathlib import Path

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
    "status.html",
)
# Written into a demo built with the offline client (`a11y-stem demo build --fake`);
# the same string as eval.render.PLACEHOLDER_ATTR (kept here so this module stays light).
FAKE_DEMO_MARKER = 'data-a11y-stem-demo="placeholder"'


def launch_blockers(repo_root: Path | None = None) -> list[str]:
    """Plain-language list of unfinished items, empty when the beta is ready to share."""
    items: list[str] = []
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
