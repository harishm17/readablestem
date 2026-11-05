"""UX round 2, lane D: the public pages and the shared chrome.

What these pin: the offices page, its one-page summary and the privacy notice are written from
one list of data (so they cannot drift); every scrollable table region can be reached by
keyboard; the short summaries that lead the long pages; the comparison guide's decision list and
its LaTeX-source wording; sign-in consent; the short footers; and the style page's labels.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from fixtures.web.support import ALICE, WebEnv, login
from readablestem.web import offices, seo
from readablestem.web.recipients import recipients

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src/readablestem/web/templates"
STATIC = ROOT / "src/readablestem/web/static"
ALL_ON = {
    "feature_tables": True,
    "feature_image_pages": True,
    "feature_notation_memory": True,
    "math_reader_openai_enabled": True,
    "model_provider": "anthropic",
    "analytics_enabled": True,
}


class Plain(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in {"style", "script"}

    def handle_endtag(self, tag):
        self.skip -= tag in {"style", "script"}

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def text_of(html: str) -> str:
    parser = Plain()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def get(tmp_path, path: str, **settings) -> str:
    env = WebEnv(tmp_path, **settings)
    with env.running() as client:
        response = client.get(path)
    assert response.status_code == 200, path
    return response.text


# ------------------------------------------------------------- one list of data


def test_the_matrix_the_company_table_and_the_summary_are_written_from_one_list(tmp_path):
    env = WebEnv(tmp_path, **ALL_ON)
    context = offices.offices_context(env.settings)
    items = context["data_items"]
    labels = [item.label for item in items]
    # The kinds of data the main page describes in prose are all rows, and so are the opted-in
    # full-page images and the course notation the summary used to leave out.
    joined = " ".join(labels)
    assert "tables" in joined and "scanned or handwritten page" in joined
    assert "course's notation" in joined and "close-up of a handwritten expression" in joined
    with env.running() as client:
        page = client.get(offices.PAGE_PATH).text
        summary = client.get(offices.SUMMARY_PATH).text
    page_text, summary_text = text_of(page), text_of(summary)
    for label in labels:
        assert label in page_text, label
        assert label in summary_text, label
    # Every company's cell on the page lists what the same list says it receives.
    table = page.split('class="recipients-table"', 1)[1].split("</table>", 1)[0]
    for r in recipients(env.settings):
        if "hosting" in r.purposes:
            continue
        for label in offices.received_by(items, r.name):
            assert label in text_of(table), (r.name, label)


def test_a_feature_that_is_off_is_in_neither_page_nor_the_summary(tmp_path):
    plain = WebEnv(tmp_path, model_provider="anthropic")
    items = offices.offices_context(plain.settings)["data_items"]
    joined = " ".join(item.label for item in items)
    assert "tables" not in joined and "scanned" not in joined and "notation" not in joined
    assert "PostHog" not in " ".join(c for item in items for c in item.sent_to)
    for path in (offices.PAGE_PATH, offices.SUMMARY_PATH):
        assert "scanned or handwritten page, only if" not in text_of(
            get(tmp_path / path[-7:], path)
        )


def test_the_model_companies_keeping_times_match_the_privacy_notices_wording():
    macro = (TEMPLATES / "_recipients.html").read_text(encoding="utf-8")
    assert "within\n    30 days" in macro or "within 30 days" in " ".join(macro.split())
    assert "30 days" in offices.COMPANY_KEEPS["Anthropic"]
    assert "API data policies" in offices.COMPANY_KEEPS["OpenAI"]


def test_the_summary_pilot_is_an_ordered_list_of_the_same_eight_steps(tmp_path):
    summary = get(tmp_path / "s", offices.SUMMARY_PATH)
    block = summary.split('<ol class="pilot">', 1)[1].split("</ol>", 1)[0]
    assert block.count("<li>") == len(offices.PILOT_STEPS) == 8
    assert not re.search(r"\b1 One course", summary)  # the old numbered paragraph
    page = get(tmp_path / "p", offices.PAGE_PATH)
    checklist = page.split('id="pilot"', 1)[1].split("</ol>", 1)[0]
    assert checklist.count("<li>") == len(offices.PILOT_STEPS)


def test_the_summary_has_no_raw_url_and_links_back_to_the_live_page(tmp_path):
    summary = get(tmp_path, offices.SUMMARY_PATH)
    assert not re.search(r"https?://[^\s\"'<>]+(?=[\s<])", re.sub(r'href="[^"]*"', "", summary))
    assert ">Open the live page</a>" in summary
    assert "print" in (STATIC / "offices-summary.css").read_text(encoding="utf-8")
    css = (STATIC / "offices-summary.css").read_text(encoding="utf-8")
    h1, h2 = (
        re.findall(r"h1 \{ font-size: ([\d.]+)rem", css),
        re.findall(r"h2 \{ font-size: ([\d.]+)rem", css),
    )
    body = 1.0
    assert float(h1[0]) >= 1.4 * float(h2[0]) >= 1.4 * 1.2 * body  # a clear step at each level


def test_the_offices_page_leads_with_the_matrix_and_the_retention_table(tmp_path):
    page = get(tmp_path, offices.PAGE_PATH, **ALL_ON)
    assert page.index('id="glance"') < page.index('class="toc"') < page.index('id="data-flow"')
    assert page.index("data-matrix") < page.index("On this page")
    assert 'class="retention-table"' in page
    assert "after the instructor last worked on it" in text_of(page)


# ----------------------------------------------------- scrollable regions by keyboard


# Tables with a fixed layout that stack into blocks below 600px (app.css, `.doc-table`): they
# never overflow, so their box is not a scrollable region and takes no tab stop.
STACKED = {"_documents.html", "_batch_rows.html"}


def test_every_scrolling_table_box_in_the_templates_is_focusable_and_named():
    """axe `scrollable-region-focusable`: a `.table-scroll` needs tabindex="0" and a group or
    region role with a name, wherever it is written (templates, the showcase site)."""
    bad = []
    for root in (TEMPLATES, ROOT / "site/templates"):
        for path in sorted(root.glob("*.html")):
            if path.name in STACKED:
                continue
            for tag in re.findall(
                r'<div[^>]*class="[^"]*\btable-scroll\b[^"]*"[^>]*>',
                path.read_text(encoding="utf-8"),
            ):
                named = "aria-label=" in tag or "aria-labelledby=" in tag
                if not (
                    'tabindex="0"' in tag and re.search(r'role="(group|region)"', tag) and named
                ):
                    bad.append((path.name, tag))
    macro = (TEMPLATES / "_table_scroll.html").read_text(encoding="utf-8")
    assert 'tabindex="0"' in macro and 'role="group"' in macro and "aria-label" in macro
    assert not bad, bad


def test_the_offices_tables_are_reachable_boxes(tmp_path):
    page = get(tmp_path, offices.PAGE_PATH)
    boxes = re.findall(r'<div class="table-scroll"[^>]*>', page)
    assert len(boxes) >= 3
    for tag in boxes:
        assert 'tabindex="0"' in tag and 'role="group"' in tag and "aria-label=" in tag


# -------------------------------------------------------- long pages open with a summary


def test_privacy_leads_with_a_retention_table_and_terms_with_a_summary(tmp_path):
    privacy = get(tmp_path / "a", "/privacy", feature_notation_memory=True)
    assert privacy.index("retention-at-a-glance") < privacy.index("<h2>Who runs this</h2>")
    table = privacy.split('class="retention-table"', 1)[1].split("</table>", 1)[0]
    assert "<strong>24 hours</strong>" in table and "Delete now" in table
    assert "Database backups" in table
    assert "made the default for new" in " ".join(text_of(privacy).split()) or (
        "can be made the default for new uploads" in text_of(privacy)
    )
    terms = get(tmp_path / "b", "/terms")
    assert terms.index('id="summary"') < terms.index("<h2>The service</h2>")
    assert "Check the output" in text_of(terms)


def test_limits_puts_the_upload_limits_before_the_evidence(tmp_path):
    page = get(tmp_path, "/limits", max_pages_per_job=77, max_upload_mb=66)
    order = [
        page.index(f'id="{x}"') for x in ("use", "handles", "not-yet", "review-time", "accuracy")
    ]
    assert order == sorted(order)
    head = page.split('id="handles"', 1)[0]
    for term in ("File types", "Size", "Daily limits", "Storage", "How long it is kept"):
        assert f"<dt>{term}</dt>" in head
    assert "Up to 77 pages and 66 MB per upload." in text_of(head)


def test_the_ada_guide_opens_with_what_to_do_this_term(tmp_path):
    page = get(tmp_path, seo.ADA_GUIDE)
    assert page.index('id="this-term"') < page.index('aria-label="On this page"')


def test_posting_help_has_an_at_a_glance_list_first(tmp_path):
    page = get(tmp_path, "/help/posting")
    assert page.index('id="glance"') < page.index('aria-label="On this page"')
    assert "Do not paste it into a page" in text_of(page.split('id="canvas-file"', 1)[0])


def test_the_guides_index_gives_each_guide_an_audience_line(tmp_path):
    page = get(tmp_path, "/guides")
    assert page.count('class="guide-for"') == len(seo.GUIDES) == 2
    assert "For instructors and accessibility staff" in page
    assert "short guide" in text_of(page)


# ------------------------------------------------------- the comparison guide


@pytest.mark.parametrize("latex", [False, True])
def test_the_comparison_leads_with_a_decision_list_and_names_latex_source(tmp_path, latex):
    page = get(tmp_path, seo.COMPARE_GUIDE, feature_latex_source=latex)
    assert "<h1>Choosing a tool for STEM course files</h1>" in page
    assert page.index('class="decision-list"') < page.index("compare-table")
    inputs = page.split('<th scope="row">Takes in</th>', 1)[1].split("</tr>", 1)[0]
    if latex:
        assert "LaTeX source (a .tex file or a zip of a LaTeX project" in inputs
        assert "converted with LaTeXML" in inputs
    else:
        assert "LaTeX project" not in inputs


def test_every_page_that_lists_inputs_says_the_same_about_latex_source(tmp_path):
    """The landing page, limits, the guides, offices and privacy all name LaTeX source when the
    feature is on and not when it is off (one flag: routes/pages.py `_features`)."""
    on = WebEnv(tmp_path / "on", feature_latex_source=True)
    off = WebEnv(tmp_path / "off")
    paths = {
        "/": "LaTeX source files (.tex)",
        "/limits": "A LaTeX file (.tex) or a zip of a LaTeX project is also taken",
        seo.COMPARE_GUIDE: "a zip of a LaTeX project",
        seo.ADA_GUIDE: "It also reads LaTeX source files",
        offices.PAGE_PATH: "a zip of a LaTeX project",
        "/privacy": "for LaTeX source, the .tex file",
    }
    with on.running() as client_on, off.running() as client_off:
        for path, phrase in paths.items():
            assert phrase in text_of(client_on.get(path).text), path
            assert phrase not in text_of(client_off.get(path).text), path


# ------------------------------------------------------------------- landing


def landing_copy(page: str) -> str:
    """The words a visitor reads before opening anything: `main` without the folded questions
    and without text that is only for assistive technology."""
    main = page.split("<main", 1)[1].split("</main>", 1)[0]
    main = re.sub(r"<details.*?</details>", "", main, flags=re.S)
    main = re.sub(r'<div class="visually-hidden">.*?</div>', "", main, flags=re.S)
    main = re.sub(r'<span class="visually-hidden">.*?</span>', "", main, flags=re.S)
    return text_of(main)


def test_the_landing_page_opens_short_and_the_evidence_is_behind_a_link(tmp_path):
    page = get(tmp_path, "/")
    assert 'class="hero-rest"' not in page
    hero = page.split('<section class="diff"', 1)[0]
    assert len(text_of(hero).split()) < 130  # the opening before "Hear the difference"
    assert 'class="proof-strip"' not in page
    assert 'href="/guides/readablestem-mathpix-latexml">All results and other tools' in page
    assert 'href="/limits#accuracy"' not in page
    assert len(landing_copy(page).split()) <= 450


def test_hear_the_difference_is_one_compact_block_with_two_readings(tmp_path):
    page = get(tmp_path, "/")
    block = page.split('<section class="diff"', 1)[1].split("</section>", 1)[0]
    assert '<h2 id="hear-title">Hear the difference</h2>' in block
    assert (
        block.count("<h3>") == 2 and "In the PDF" in block and "In the ReadableSTEM page" in block
    )
    # One crop of the equation alone (no clipped line of text, no scroll box), the live MathML,
    # the two readings as plain text, one Play button each (hidden until the script finds speech).
    assert block.count("<img ") == 1 and "<math" in block and "crop-scroll" not in block
    assert 'alt="Equation 2.4 cut from the PDF page: delta d over delta t, number 2.4."' in block
    assert "capital delta bold d over capital delta t" in block
    assert block.count("data-hear hidden") == 2 and block.count("<button") == 2
    # No badges, no boxed readings, no figure alt text, no lead-in sentence.
    for gone in (
        "tag-err",
        "tag-ok",
        "Untagged",
        "figure-alt",
        "Figure 2.11",
        "<dl",
        'class="pane',
    ):
        assert gone not in block, gone
    assert block.count("OpenStax Physics 2.3") == 1 and "CC BY 4.0" in block
    assert "A screen reader reads the PDF" not in page


# ----------------------------------------------------------- sign-in and chrome


def test_sign_in_says_an_account_is_made_right_under_the_buttons(tmp_path):
    page = get(
        tmp_path,
        "/login",
        signup_mode="open",
        dev_login_enabled=False,
        google_client_id="id",
        google_client_secret="secret",
        base_url="https://beta.example.org",
    )
    buttons = page.index("Sign in with Google")
    consent = page.index("signing in makes one")
    assert 0 < consent - buttons < 600
    text = text_of(page)
    assert "By signing in you accept the terms and the privacy notice" in text
    footer = page.split("<footer", 1)[1]
    assert footer.count("<a ") <= 5 and "footer-cols" not in page


def test_signed_in_pages_put_the_workspace_first_and_have_a_short_footer(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
    nav = page.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
    order = [
        nav.index(x)
        for x in ('href="/app"', 'href="/courses"', 'href="/account"', 'href="/guides"')
    ]
    assert order == sorted(order)
    assert 'href="/demo"' not in nav and 'href="/"' not in nav
    # The public pages stay, after the workspace and quieter.
    assert nav.index('href="/guides"') < nav.index('href="/for-accessibility-offices"')
    assert nav.count('class="nav-secondary"') == 2
    footer = page.split("<footer", 1)[1].split("</footer>", 1)[0]
    links = re.findall(r"<li><a ", footer)
    assert len(links) <= 6 and "For accessibility offices" not in footer


def test_the_public_nav_is_unchanged(tmp_path):
    page = get(tmp_path, "/privacy")
    nav = page.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
    for href in ("/", "/demo", "/guides", "/for-accessibility-offices"):
        assert f'href="{href}"' in nav


def test_footer_links_are_44px_targets():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.footer-cols a \{[^}]*min-height: 2\.75rem", css)
    assert re.search(r"\.footer-compact-links a \{[^}]*min-height: 2\.75rem", css)
    assert re.search(r"\.cancel-link[^{]*\{[^}]*min-height: 2\.75rem", css)


# ------------------------------------------------------------------ style page


def test_every_demo_control_on_the_style_page_is_labelled_an_inert_example(tmp_path):
    page = get(tmp_path, "/style")
    assert "inert example" in page.lower()
    assert page.count('class="style-example"') >= 3
    assert page.count("Inert example</p>") == page.count('class="style-example"')
    # No demonstration button is a link to nowhere: they are buttons of type "button".
    assert not re.search(r'<a class="button[^"]*" href="#', page)
    for state in ("state-light", "state-dark", "state-forced"):
        assert f'id="{state}"' in page
    for live in ('href="/app"', 'href="/status"', 'href="/guides/readablestem-mathpix-latexml"'):
        assert live in page


def test_the_style_pages_dark_preview_carries_the_dark_tokens():
    tokens = (STATIC / "tokens.css").read_text(encoding="utf-8")
    dark = tokens.split("@media (prefers-color-scheme: dark)", 1)[1].split("/* Anything drawn", 1)[
        0
    ]
    wanted = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", dark))
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    preview = css.split(".style-scheme-dark {", 1)[1].split("}", 1)[0]
    have = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", preview))
    for name, value in have.items():
        assert wanted[name] == value, name
    for name in ("--bg", "--ink", "--accent", "--ok", "--warn", "--err", "--focus-outline"):
        assert name in have


# ----------------------------------------------------------------- demo note


def test_the_demo_note_is_short_and_keeps_the_licence_and_the_teacher_support_note():
    demo = (STATIC / "demo/index.html").read_text(encoding="utf-8")
    note = demo.split('<aside aria-label="About this demo"', 1)[1].split("</aside>", 1)[0]
    visible = note.split("<details>", 1)[0]
    assert len(text_of(visible).split()) < 75
    assert "CC BY 4.0" in visible  # the attribution stays visible
    assert "<summary>Source, licence and notes</summary>" in note
    assert "Teacher Support" in note.split("<details>", 1)[1]
    assert note.count("Teacher Support") == 1  # explained once
    assert "AGPL" not in note


# ------------------------------------------------------------- the comparison chart


@pytest.mark.parametrize("path", [seo.COMPARE_GUIDE, offices.PAGE_PATH])
def test_the_chart_is_one_shared_component_with_the_numbers_in_a_real_table(tmp_path, path):
    page = get(tmp_path, path)
    assert page.count('class="proof-chart"') == 1  # the patterns are defined once per page
    chart = page.split('<figure class="proof-chart"', 1)[1].split("</figure>", 1)[0]
    # The bars are for the eye; the same numbers are a table a screen reader always reads.
    assert '<div class="chart" aria-hidden="true">' in chart
    hidden = chart.split('<div class="visually-hidden">', 1)[1].split("</table>", 1)[0]
    assert "<caption>" in hidden and hidden.count('<th scope="row">') == 6
    assert "0.840" in hidden and "$0.068" in hidden and "never asks" in hidden
    # Every bar has its system's name and number beside it, and no style attribute (the CSP).
    assert chart.count('class="chart-name"') == chart.count('class="chart-value"') == 18
    assert " style=" not in chart
    assert "ReadableSTEM and the Opus chatbot are" in " ".join(text_of(chart).split())
    assert "level" in text_of(chart) and "beat" not in text_of(chart).lower()
    assert "26 documents the tool had never seen" in text_of(chart)


def test_the_landing_chart_is_the_compact_one_with_the_same_kind_of_table(tmp_path):
    page = get(tmp_path, "/")
    assert page.count('class="proof-chart proof-chart-compact"') == 1
    chart = page.split('<figure class="proof-chart', 1)[1].split("</figure>", 1)[0]
    assert '<div class="chart" aria-hidden="true">' in chart
    hidden = chart.split('<div class="visually-hidden">', 1)[1].split("</table>", 1)[0]
    assert "<caption>" in hidden and hidden.count('<th scope="row">') == 2
    assert "0.840" in hidden and "11.9" in hidden and "$0." not in hidden
    assert chart.count('class="chart-name"') == chart.count('class="chart-value"') == 6
    assert chart.count('class="chart-row"') == 2 and " style=" not in chart
    text = " ".join(text_of(chart).split())
    assert "level with the Opus chatbot on the main score (0.840 and 0.836)" in text
    assert "beat" not in text.lower() and "Cost per page" not in text
    assert "Higher is better" in text and "Lower is better" in text
    assert "Headings and figures" not in text  # the first test's table stays on the guide
    assert "Evidence: the four measures" not in text


def test_the_chart_is_not_on_pages_that_do_not_need_it(tmp_path):
    for path in ("/privacy", "/terms", "/status", "/guides"):
        assert "proof-chart" not in get(tmp_path / path[1:], path)


def test_the_earlier_chatbot_ahead_result_is_history_not_a_headline(tmp_path):
    for path in ("/", seo.COMPARE_GUIDE):
        text = text_of(get(tmp_path / path[-5:].strip("/") or "x", path))
        assert "0.62 against 0.50" not in text and "chatbot ahead" not in text, path
    limits = text_of(get(tmp_path / "limits", "/limits"))
    assert "about half (0.50) against 0.62 for the chatbot" in limits  # kept as history there
    assert "Earlier results, kept as history" in limits


def test_the_chart_css_has_forced_colour_and_phone_rules():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    chart = css.split("The comparison chart (templates/_proof_chart.html)", 1)[1]
    assert "@media (max-width: 39.99rem)" in chart and "forced-colors: active" in chart
    assert "url(#chart-hatch)" in chart and "url(#chart-dots)" in chart  # not colour alone
