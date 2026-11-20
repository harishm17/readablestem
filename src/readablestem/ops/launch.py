"""What still stands between the beta and its first real users, checked from the repo.

`launch_blockers()` lists unfinished public copy (a `TODO(Harish)` or a "Draft" note on a
page anyone can open), a missing or placeholder demo, and a missing eval baseline (the CI
gate skips without one). Given the settings, it also lists what a setting turns on that
the public pages do not describe yet (Microsoft sign-in and the privacy notice; open
sign-up, which makes an account at the first sign-in, and the privacy notice that must say
so and how to delete it; a handwriting reader, which sends crops to a model provider, and the
notice that must name that provider), and whether the production settings are complete and
the spend limits fit the model provider
(`ops.limits`: the same errors that stop a production start, plus warnings). A Microsoft
sign-in left half set is listed too: a client id or secret alone is refused, tenants alone
would leave the button quietly off.
`readablestem ops launch-check` prints the list and exits 1 while it is not empty; the web
app logs `launch_blockers` with the count at a production start.

It does not refuse to start: a staging deploy runs with ENV=prod and must work before the
copy is final. Each item names the file to change; none of it is document content.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from readablestem.config import Settings

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
DEMO_PAGE = WEB_DIR / "demo" / "index.html"
# Pages anyone can open without signing in.
PUBLIC_TEMPLATES = (
    "landing.html",
    "demo_pending.html",
    "privacy.html",
    "terms.html",
    "limits.html",
    "help_index.html",
    "help_review.html",
    "help_posting.html",
    "help_latex.html",
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
# What the privacy notice must say while analytics is on (settings.analytics_enabled): that
# PostHog gets page views, and not the sentence that there are no analytics. The same check
# stops a production start (`Settings.check_production`).
ANALYTICS_NOTICE_WORD = "PostHog"
NO_ANALYTICS_CLAIM = "no analytics"
# Written into a demo built with the offline client (`readablestem demo build --fake`);
# the same string as eval.render.PLACEHOLDER_ATTR (kept here so this module stays light).
FAKE_DEMO_MARKER = 'data-readablestem-demo="placeholder"'


def rendered_notice(settings: Settings) -> str | None:
    """The privacy page as a visitor would read it with `settings` (rendered from the real
    template, tags stripped), or None when the template is missing. Every check below reads
    this rather than the template's source, so a sentence that a template condition hides
    for these settings does not count as said."""
    from readablestem.web.recipients import notice_text

    return notice_text(settings, templates_dir=TEMPLATES_DIR)


def analytics_notice_problem(settings: Settings) -> str | None:
    """Why the privacy notice cannot stand with analytics on, or None when it describes it."""
    text = rendered_notice(settings)
    if text is None:
        return "web/templates/privacy.html is missing"
    if NO_ANALYTICS_CLAIM in text.lower():
        return 'the privacy notice still says there are "no analytics"'
    if ANALYTICS_NOTICE_WORD not in text:
        return f"the privacy notice does not name {ANALYTICS_NOTICE_WORD}"
    return None


def unnamed_recipients(settings: Settings) -> list[tuple[str, str]] | None:
    """(company, what it receives) for each company that gets pictures from a document with
    these settings (the model provider and each handwriting reader that is on; the list is
    `web.recipients.recipients`) that the rendered privacy notice does not name. None when
    the template is missing."""
    from readablestem.web.recipients import recipients

    text = rendered_notice(settings)
    if text is None:
        return None
    found = []
    for r in recipients(settings):
        if not r.gets_documents or r.name in text:
            continue
        what = []
        if "model" in r.purposes:
            what.append(f"MODEL_PROVIDER={settings.model_provider} sends document images")
        if "handwriting" in r.purposes:
            what.append("a handwriting reader sends crops of handwritten expressions")
        found.append((r.name, " and ".join(what)))
    return found


def recipients_notice_problem(settings: Settings) -> str | None:
    """Why the privacy notice does not name every company that receives pictures from a
    document with these settings, or None when it does."""
    missing = unnamed_recipients(settings)
    if missing is None:
        return "web/templates/privacy.html is missing"
    if missing:
        return "the privacy notice does not name " + ", ".join(name for name, _ in missing)
    return None


def sent_material_problems(settings: Settings, *, today: date | None = None) -> list[str]:
    """What stops these settings from sending what they send: for each kind of material that
    is on (`web.recipients.sent_material`), the rendered privacy notice must state it in its
    own words, the rendered vendor line must carry its words, and the row must be dated with
    the day it was first stated in public, not later than `today` (UTC). A setting that sends
    something new therefore cannot start in production before the page says so with its date
    (privacy.html, "Changes to this notice"). Empty when all is stated."""
    from readablestem.web.recipients import html_text, sent_material, vendor_line_text

    notice = rendered_notice(settings)
    if notice is None:
        return ["web/templates/privacy.html is missing"]
    vendor = vendor_line_text(settings, templates_dir=TEMPLATES_DIR)
    if vendor is None:
        return ["web/templates/_vendor_line.html is missing"]
    today = today or datetime.now(UTC).date()
    problems = []
    for m in sent_material(settings):
        switches = [s.upper() for s in m.switches if getattr(settings, s)] or ["always on"]
        sends = f"{' and '.join(switches)} sends {m.label.lower()}"
        if html_text(m.what) not in notice or html_text(m.stored) not in notice:
            problems.append(
                f"web/templates/privacy.html: {sends} but the privacy notice does not say so"
            )
        for _slot, words in m.vendor:
            if words.format(to=" and ".join(m.to)) not in vendor:
                problems.append(
                    f"web/templates/_vendor_line.html: {sends} but the vendor line does not say so"
                )
                break
        if m.stated is None:
            problems.append(
                f"web/recipients.py: {sends} but its row has no date it was first stated in public"
            )
        elif m.stated > today:
            problems.append(
                f"web/recipients.py: {sends} but its row is dated {m.stated.isoformat()}, "
                "a day still to come"
            )
        elif m.stated_text not in notice:
            problems.append(
                f"web/templates/privacy.html: {sends} but the notice does not show the day "
                f"it was first stated ({m.stated_text})"
            )
    return problems


def privacy_claim_problems(settings: Settings) -> list[str]:
    """The privacy notice's statements about what is stored and when it is deleted, each
    checked against the setting or the code it describes (until a register of claims exists,
    this is that register for the notice): the retention clocks (`RETENTION_HOURS`, the
    7-day choice of `web.retention`, `OPT_IN_RETENTION_DAYS`), the backups
    (`web.offices.BACKUP_DAYS`), the usage notes (`web.events`: kept `EVENT_RETENTION_DAYS`,
    the review outcomes and the counts their totals need), the problem reports
    (`web.reports`: `CLOSED_DAYS` and `MAX_DAYS`), the courses kept with
    FEATURE_NOTATION_MEMORY and the names kept for a LaTeX source. What is sent is
    `sent_material_problems`; who receives it is `unnamed_recipients`. Empty when every
    statement is there."""
    from readablestem.web import events, offices, reports, retention
    from readablestem.web.app import SESSION_MAX_AGE_S, STATIC_DIR
    from readablestem.web.office_convert import input_kinds
    from readablestem.web.recipients import listen_kept_documents

    notice = rendered_notice(settings)
    if notice is None:
        return ["web/templates/privacy.html is missing"]
    claims = [
        (True, f"{settings.retention_hours} hours after you last worked on it", "RETENTION_HOURS"),
        (True, f"{retention.WEEK_DAYS} days after you last worked on it", "web.retention"),
        (True, f"{settings.opt_in_retention_days} days after upload", "OPT_IN_RETENTION_DAYS"),
        (True, f"kept for about {offices.BACKUP_DAYS} days", "web.offices.BACKUP_DAYS"),
        (True, "who made each review decision and when", "web.db.ReviewEdit (web.record)"),
        (
            settings.events_enabled,
            f"deleted after {events.EVENT_RETENTION_DAYS} days",
            "EVENTS_ENABLED (web.events)",
        ),
        (
            settings.events_enabled,
            f"at least {events.MIN_ACCOUNTS} accounts and {events.MIN_DOCUMENTS} documents",
            "EVENTS_ENABLED (web.events.outcome_cells)",
        ),
        (True, f"for {reports.CLOSED_DAYS} days after that", "web.reports.CLOSED_DAYS"),
        (True, f"deleted {reports.MAX_DAYS} days after you send it", "web.reports.MAX_DAYS"),
        (
            settings.feature_notation_memory,
            "Your courses and their notation",
            "FEATURE_NOTATION_MEMORY",
        ),
        (
            settings.feature_latex_source,
            "the names of the files, packages and commands LaTeX reported a problem with",
            "FEATURE_LATEX_SOURCE (Job.error_detail)",
        ),
        # What is kept of a source file, and what is only made and removed. The tool compiles no
        # PDF of a LaTeX source in a job (`source.tex_compile` is called by none), so the notice
        # names no stored PDF for it: the file or zip as uploaded (`web.intake`, the "upload"
        # blob) and a temporary folder for the build (`source.convert`).
        (
            settings.feature_latex_source,
            "the LaTeX .tex file or the zip of the LaTeX project",
            "FEATURE_LATEX_SOURCE (web.intake, the upload blob; no compiled PDF is stored)",
        ),
        (
            settings.feature_latex_source,
            "a LaTeX build",
            "FEATURE_LATEX_SOURCE (source.convert: a temporary folder removed when the build ends)",
        ),
        (
            True,
            "the Word export",
            "web.routes.jobs (the Word download; export_docx makes and removes a folder)",
        ),
        # Word and PowerPoint: a file turned into a PDF is not kept, only the PDF
        # (`routes.jobs._office_to_pdf`); a .docx the reader takes is kept as uploaded
        # (`routes.jobs._place_word`).
        (
            bool(input_kinds(settings)),
            "the Word or PowerPoint file itself is not kept",
            "OFFICE_INPUT_ENABLED (routes.jobs: the stored upload is the PDF made from it)",
        ),
        (
            settings.feature_word_source,
            "the Word .docx file",
            "FEATURE_WORD_SOURCE (routes.jobs._place_word stores the file as uploaded)",
        ),
        (
            settings.feature_word_source,
            "reading a Word file",
            "FEATURE_WORD_SOURCE (source.pandoc: a temporary folder removed when the read ends)",
        ),
        # What the model's answers leave behind: the conversion result, kept with the document
        # (`Job.result_json`), and the answers about whole pages that "Try again" reuses
        # (`web.step_cache`: `StepRecord`, deleted with the document by `ops.retention`).
        (
            True,
            "the conversion result (proposed LaTeX, MathML, alt text, headings",
            "web.db.Job.result_json",
        ),
        (
            True,
            "the model's answers for the steps a conversion has finished",
            "web.step_cache (StepRecord: kept with the document, deleted with it)",
        ),
        (
            bool(input_kinds(settings)),
            "Word and PowerPoint conversion",
            "OFFICE_INPUT_ENABLED (web.office_convert: a temporary folder removed at once)",
        ),
        # What Listen keeps, in the browser only: the number of documents is read from the
        # player's own code (static/listen.js `KEEP`).
        (
            True,
            f"for the last {listen_kept_documents()} documents you listened to",
            "static/listen.js KEEP",
        ),
        (
            True,
            "never its text, and it is not sent to the server",
            "static/listen.js (`remember` keeps ids; the player makes no request)",
        ),
        (
            True,
            f"(for up to {SESSION_MAX_AGE_S // 86400} days)",
            "web.app.SESSION_MAX_AGE_S",
        ),
        # Terms and takedown: the account can be deleted by the person (`routes.account`), and
        # a request to us goes to the contact address (`Settings.contact_email`, required in
        # production).
        (
            True,
            '"Delete my account"). To have your data deleted by us',
            "web.routes.account (POST /account/delete)",
        ),
        (
            bool(settings.contact_email),
            f"email {settings.contact_email}",
            "CONTACT_EMAIL (Settings.check_production requires it)",
        ),
    ]
    problems = [
        f"web/templates/privacy.html: does not say {words!r} ({source})"
        for on, words, source in claims
        if on and words not in notice
    ]
    # "It is not sent to the server": the player's own code makes no request.
    if re.search(
        r"\bfetch\(|XMLHttpRequest|sendBeacon", (STATIC_DIR / "listen.js").read_text("utf-8")
    ):
        problems.append(
            "web/static/listen.js makes a request but the privacy notice says what Listen keeps "
            "in the browser is not sent to the server"
        )
    return problems


def second_reader_problem(settings: Settings) -> str | None:
    """Why `IMAGE_PAGES_SECOND_READER` cannot work with these settings, or None. Only the local
    reader runs in the conversion child (no keys, no network); a Claude or OpenAI reader needs
    the parent to read crops for the child, which is not built, so choosing one would
    silently read nothing. The local reader needs its model files in place: without them every
    equation would only get a "no second reading" notice."""
    name = settings.image_pages_second_reader
    if name == "off":
        return None
    if name != "local":
        return (
            f"IMAGE_PAGES_SECOND_READER={name} is not built: only 'local' runs in the "
            'conversion child (docs/math-readers.md, "Which reader runs where")'
        )
    from readablestem.models.math_readers.local_reader import LocalReader

    if settings.math_reader_local_dir is None:
        return "IMAGE_PAGES_SECOND_READER=local but MATH_READER_LOCAL_DIR is not set"
    if not LocalReader.from_settings(settings).available():
        return (
            "IMAGE_PAGES_SECOND_READER=local but the model files are not in "
            "MATH_READER_LOCAL_DIR (build with INSTALL_MATH_READER=true)"
        )
    return None


def launch_blockers(
    repo_root: Path | None = None, *, settings: Settings | None = None
) -> list[str]:
    """Plain-language list of unfinished items, empty when the beta is ready to share."""
    items: list[str] = []
    if settings is not None:
        items.extend(_settings_items(settings))
    notice = rendered_notice(settings) if settings is not None else None
    if settings is not None and notice is not None:
        # A feature that sends or keeps something new needs its words in the notice.
        for on, word, what in (
            (settings.ms_sign_in_enabled, "Microsoft", "Microsoft sign-in is on (MS_CLIENT_ID)"),
            (
                settings.analytics_enabled,
                ANALYTICS_NOTICE_WORD,
                "ANALYTICS_ENABLED sends page views to PostHog",
            ),
        ):
            if on and word not in notice:
                items.append(
                    f"web/templates/privacy.html: {what} but the privacy notice does not say so"
                )
        for name, what in unnamed_recipients(settings) or []:
            items.append(
                f"web/templates/privacy.html: {what} to {name} but the privacy notice does "
                f"not name {name}"
            )
        # What is sent (FEATURE_TABLES, FEATURE_NOTATION_MEMORY and every other sending
        # setting) and what is stored and deleted: each statement checked against its source.
        items.extend(sent_material_problems(settings))
        items.extend(privacy_claim_problems(settings))
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
        items.append("web/demo/index.html: no demo yet (readablestem demo build)")
    elif FAKE_DEMO_MARKER in DEMO_PAGE.read_text(encoding="utf-8"):
        items.append("web/demo/index.html: placeholder output from --fake; rebuild it")
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
    if settings.env == "prod" and settings.trusted_proxy_hops == 0:
        items.append(
            "settings: TRUSTED_PROXY_HOPS=0 ignores X-Forwarded-For, so behind the host's "
            "proxy every visitor shares one rate-limit bucket (1 on Railway alone, 2 with "
            "Cloudflare's proxy in front)"
        )
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
    # A client id without its secret (or the reverse) is refused above. Tenants alone are
    # not refused: the sign-in is simply off, which looks like a working deploy.
    if settings.ms_allowed_tenants and not (
        _set(settings.ms_client_id) or _set(settings.ms_client_secret)
    ):
        items.append(
            "settings: MS_ALLOWED_TENANTS is set but MS_CLIENT_ID and MS_CLIENT_SECRET are "
            "not, so Microsoft sign-in is off"
        )
    return items


def _set(value: object) -> bool:
    from readablestem.config import _blank

    return not _blank(value)
