"""What still stands between the beta and its first real users, checked from the repo.

`launch_blockers()` lists unfinished public copy (a `TODO(Harish)` or a "Draft" note on a
page anyone can open), a missing or placeholder demo, and a missing eval baseline (the CI
gate skips without one). Given the settings, it also lists what a setting turns on that
the public pages do not describe yet (Microsoft sign-in and the privacy notice; open
sign-up, which makes an account at the first sign-in, and the privacy notice that must say
so and how to delete it), and whether the production settings are complete and the spend
limits fit the model provider
(`ops.limits`: the same errors that stop a production start, plus warnings).
`readablestem ops launch-check` prints the list and exits 1 while it is not empty; the web
app logs `launch_blockers` with the count at a production start.

It does not refuse to start: a staging deploy runs with ENV=prod and must work before the
copy is final. Each item names the file to change; none of it is document content.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from readablestem.config import Settings

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
    "not_allowed.html",
    "signups_full.html",
    "login_email.html",
)
# Words that say sign-in is by invitation. A public page may carry them only when it also
# looks at `signup_open`, so that with SIGNUP_MODE=open it says something else (the pages
# render from the setting: docs/web.md "Sign-up modes").
INVITE_ONLY_PHRASES = (
    "by invitation",
    "small group of instructors",
    "Not invited yet",
    "beta list",
    "your invitation",
)
SIGNUP_SWITCH = "signup_open"
# What the privacy notice must say while sign-up is open (settings.signup_open): that the
# first sign-in makes an account, and where to delete it. Matched against the template text.
OPEN_SIGNUP_NOTICE = (
    ("first time you sign in, we make an account", "that the first sign-in makes an account"),
    ("Delete my account", "how to delete the account"),
)
# Written into a demo built with the offline client (`readablestem demo build --fake`);
# the same string as eval.render.PLACEHOLDER_ATTR (kept here so this module stays light).
FAKE_DEMO_MARKER = 'data-readablestem-demo="placeholder"'


def launch_blockers(
    repo_root: Path | None = None, *, settings: Settings | None = None
) -> list[str]:
    """Plain-language list of unfinished items, empty when the beta is ready to share."""
    items: list[str] = []
    if settings is not None:
        items.extend(_settings_items(settings))
    privacy = TEMPLATES_DIR / "privacy.html"
    notice = privacy.read_text(encoding="utf-8") if privacy.is_file() else None
    if settings is not None and notice is not None:
        # A feature that sends or keeps something new needs its words in the notice.
        for on, word, what in (
            (settings.ms_sign_in_enabled, "Microsoft", "Microsoft sign-in is on (MS_CLIENT_ID)"),
            (
                settings.model_provider == "anthropic",
                "Anthropic",
                "MODEL_PROVIDER=anthropic sends document images to Anthropic",
            ),
            (
                settings.feature_tables,
                "rebuild tables",
                "FEATURE_TABLES sends tables to the model provider",
            ),
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
    if settings is not None and settings.signup_open and notice is not None:
        for phrase, what in OPEN_SIGNUP_NOTICE:
            if phrase not in notice:
                items.append(
                    f"web/templates/privacy.html: SIGNUP_MODE=open but the privacy notice "
                    f"does not say {what}"
                )
    for name in PUBLIC_TEMPLATES:
        path = TEMPLATES_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if SIGNUP_SWITCH not in text and (words := [p for p in INVITE_ONLY_PHRASES if p in text]):
            items.append(
                f"web/templates/{name}: says sign-in is by invitation ({words[0]!r}) whatever "
                f"SIGNUP_MODE is; render it from {SIGNUP_SWITCH}"
            )
        if "TODO(Harish)" in text:
            items.append(f"web/templates/{name}: still has a TODO(Harish) note")
        if 'class="draft-note"' in text:
            items.append(f"web/templates/{name}: still shows the draft note")
    if not DEMO_PAGE.is_file():
        items.append("web/static/demo/index.html: no demo yet (readablestem demo build)")
    elif FAKE_DEMO_MARKER in DEMO_PAGE.read_text(encoding="utf-8"):
        items.append("web/static/demo/index.html: placeholder output from --fake; rebuild it")
    if repo_root is not None:
        eval_dir = repo_root / "data" / "eval"
        if not list(eval_dir.glob("baseline-*.json")):
            items.append("data/eval: no baseline-<task>.json committed; the CI eval gate skips")
    return items


def _settings_items(settings: Settings) -> list[str]:
    """What is wrong with the settings themselves (the first group is `check_production`)."""
    from readablestem.config import ConfigError
    from readablestem.ops.limits import spend_findings

    items: list[str] = []
    refused = ""
    try:
        settings.check_production()
    except ConfigError as exc:
        refused = str(exc)
        items.append(f"settings: {refused}")
    items.extend(
        f"spend limits: {finding}"
        for finding in spend_findings(settings)
        if finding.text not in refused  # already named by the refusal above
    )
    return items
