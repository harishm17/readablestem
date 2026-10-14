"""The static demo site's build script (site/build.py): the pages it writes parse, every
link resolves inside the site, nothing is loaded from outside, the build is repeatable, and
the small parsers it relies on behave. No browser here; the axe run is in
test_site_browser.py.

site/build.py is loaded by path: a folder called `site` must not go on the import path
(it would shadow the standard library's `site` module).
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "site" / "build.py"


def load_build():
    spec = importlib.util.spec_from_file_location("site_build_for_tests", BUILD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = load_build()

OWN_PAGES = {
    "index.html",
    "examples/index.html",
    "before-after/index.html",
    "stemcheck/index.html",
    "method/index.html",
    "attribution/index.html",
    "404.html",
}
RESOURCE_ATTRS = {
    "script": "src",
    "img": "src",
    "source": "src",
    "iframe": "src",
    "embed": "src",
    "object": "data",
    "audio": "src",
    "video": "src",
    "track": "src",
    "input": "src",
}


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, list[str]]:
    out = tmp_path_factory.mktemp("site") / "out"
    return out, build.build_site(out)


class Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[tuple[str, str]] = []  # (tag, url) of things the page points to
        self.resources: list[tuple[str, str]] = []  # (tag, url) of things the page loads
        self.scripts = 0
        self.h1 = 0
        self.title = ""
        self._in_title = False
        self.lang = ""
        self.errors: list[str] = []
        self._open: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if "id" in a:
            if a["id"] in self.ids:
                self.errors.append(f"duplicate id {a['id']}")
            self.ids.add(a["id"])
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "h1":
            self.h1 += 1
        if tag == "title":
            self._in_title = True
        if tag == "script":
            self.scripts += 1
        if tag == "a" and "href" in a:
            self.links.append((tag, a["href"]))
        if tag == "link" and "href" in a:
            self.resources.append((tag, a["href"]))
        if tag in RESOURCE_ATTRS and RESOURCE_ATTRS[tag] in a:
            self.resources.append((tag, a[RESOURCE_ATTRS[tag]]))
        if tag == "img" and "srcset" in a:
            self.resources.append((tag, a["srcset"]))
        if tag == "img" and "alt" not in a:
            self.errors.append("img without alt")
        if tag not in build.VOID:
            self._open.append(tag)

    def handle_endtag(self, tag):
        if tag in build.VOID:
            return
        if tag == "title":
            self._in_title = False
        if tag in self._open:
            while self._open and self._open.pop() != tag:
                pass

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def scan(path: Path) -> Scan:
    parser = Scan()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def html_files(out: Path) -> list[Path]:
    return sorted(out.rglob("*.html"))


# ------------------------------------------------------------------------ the build


def test_every_expected_page_is_written(built):
    _out, paths = built
    assert set(paths) >= OWN_PAGES
    for example in ("physics-2-3", "physics-2-2", "physics-2-1", "fixture-lecture-notes"):
        for name in ("index.html", "converted.html", "source.pdf", "report.json"):
            assert f"examples/{example}/{name}" in paths
    assert "stemcheck/sample-report.html" in paths
    assert "assets/site.css" in paths
    assert "assets/converted.css" in paths


def test_at_least_two_and_at_most_four_examples(built):
    _out, paths = built
    pages = [p for p in paths if re.fullmatch(r"examples/[^/]+/index\.html", p)]
    assert 2 <= len(pages) <= 4


def test_every_page_parses_with_a_language_title_and_one_h1(built):
    out, _ = built
    for rel in OWN_PAGES | {str(p.relative_to(out)) for p in out.glob("examples/*/index.html")}:
        page = scan(out / rel)
        assert page.lang == "en", rel
        assert page.title.strip(), rel
        assert page.h1 == 1, rel
        assert not page.errors, (rel, page.errors)


def test_converted_pages_and_the_report_parse_without_duplicate_ids(built):
    out, _ = built
    for path in [*out.glob("examples/*/converted.html"), out / "stemcheck/sample-report.html"]:
        page = scan(path)
        assert not page.errors, (path.name, page.errors)


def test_links_resolve_inside_the_site(built):
    out, _ = built
    ids = {path: scan(path).ids for path in html_files(out)}
    broken = []
    for path in html_files(out):
        page = scan(path)
        for _tag, url in [*page.links, *page.resources]:
            parts = urlsplit(url)
            if parts.scheme in ("http", "https", "mailto", "data"):
                continue
            assert not parts.scheme, (path, url)
            target = path if not parts.path else (path.parent / unquote(parts.path)).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.is_relative_to(out.resolve()) or not target.exists():
                broken.append((str(path.relative_to(out)), url))
                continue
            if parts.fragment and target.suffix == ".html":
                known = ids.get(target) or ids.get(Path(target))
                if known is None:
                    known = scan(target).ids
                if unquote(parts.fragment) not in known:
                    broken.append((str(path.relative_to(out)), url))
    assert not broken, broken[:10]


def test_nothing_is_loaded_from_outside_the_site(built):
    out, _ = built
    outside = []
    for path in html_files(out):
        page = scan(path)
        for tag, url in page.resources:
            if url.startswith("data:"):
                continue
            if urlsplit(url).scheme or url.startswith("//"):
                outside.append((str(path.relative_to(out)), tag, url[:60]))
    assert not outside, outside[:10]


def test_the_sites_own_pages_have_no_scripts_and_no_inline_event_handlers(built):
    out, _ = built
    for rel in OWN_PAGES | {str(p.relative_to(out)) for p in out.glob("examples/*/index.html")}:
        text = (out / rel).read_text(encoding="utf-8")
        assert scan(out / rel).scripts == 0, rel
        assert not re.search(r"\son[a-z]+\s*=", text), rel


def test_css_loads_nothing_from_outside(built):
    out, _ = built
    for css in out.glob("assets/*.css"):
        text = css.read_text(encoding="utf-8")
        assert "@import" not in text
        assert not re.search(r"url\(\s*['\"]?(?!data:)", text), css.name


def test_no_fonts_trackers_or_cdns_are_named_anywhere_in_own_pages(built):
    out, _ = built
    banned = ("googleapis", "gstatic", "cloudflare", "jsdelivr", "unpkg", "cdnjs", "analytics")
    for rel in OWN_PAGES | {str(p.relative_to(out)) for p in out.glob("examples/*/index.html")}:
        text = (out / rel).read_text(encoding="utf-8").lower()
        assert not [b for b in banned if b in text], rel


def test_pages_name_the_author_and_the_licences(built):
    out, _ = built
    for rel in OWN_PAGES:
        text = (out / rel).read_text(encoding="utf-8")
        assert "Harish Manoharan" in text, rel
        assert "MIT" in text and "CC BY 4.0" in text, rel


def test_the_tone_rules_hold_in_the_sites_own_words(built):
    """The product assists; it does not certify. No page the site writes says otherwise."""
    out, _ = built
    banned = re.compile(r"\b(complian(?:t|ce)|verified|guarantee[sd]?|certified)\b", re.IGNORECASE)
    for rel in OWN_PAGES | {str(p.relative_to(out)) for p in out.glob("examples/*/index.html")}:
        text = (out / rel).read_text(encoding="utf-8")
        # What the converted excerpts say is the book's own text, not the site's.
        text = re.sub(r'<div class="converted">.*?\n      </div>', "", text, flags=re.S)
        assert not banned.findall(re.sub(r"<[^>]+>", " ", text)), rel


def test_every_openstax_page_is_credited(built):
    out, _ = built
    text = (out / "attribution/index.html").read_text(encoding="utf-8")
    for slug in ("2-1-relative-motion", "2-2-speed-and-velocity", "2-3-position-vs-time"):
        assert f"openstax.org/books/physics/pages/{slug}" in text
    assert "Paul Peter Urone" in text and "Roger Hinrichs" in text
    assert "creativecommons.org/licenses/by/4.0" in text or "CC BY 4.0" in text


def test_the_home_page_labels_the_set_its_numbers_come_from(built):
    out, _ = built
    text = (out / "index.html").read_text(encoding="utf-8")
    proof = build.read_proof(ROOT / "PROOF.md")
    assert proof.available
    assert proof.kind in ("development", "held-out")
    label = "Development set" if proof.kind == "development" else "Held-out set"
    assert label in text
    for headline in proof.headlines:
        assert headline.value in text


def test_the_build_is_repeatable(tmp_path):
    first = build.build_site(tmp_path / "a")
    second = build.build_site(tmp_path / "b")
    assert first == second
    for rel in first:
        a = hashlib.sha256((tmp_path / "a" / rel).read_bytes()).hexdigest()
        b = hashlib.sha256((tmp_path / "b" / rel).read_bytes()).hexdigest()
        assert a == b, rel


def test_the_build_replaces_its_own_folder_but_refuses_any_other(tmp_path):
    out = tmp_path / "out"
    build.build_site(out)
    build.build_site(out)  # a folder it made is replaced
    stranger = tmp_path / "work"
    stranger.mkdir()
    (stranger / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(SystemExit):
        build.build_site(stranger)
    assert (stranger / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_the_build_fits_the_light_budget(built):
    out, paths = built
    own = sum(
        (out / p).stat().st_size
        for p in paths
        if p.endswith((".html", ".css")) and "converted" not in p and "sample-report" not in p
    )
    assert own < 400_000
    for rel in OWN_PAGES:
        assert (out / rel).stat().st_size < 60_000, rel


def test_without_a_proof_file_the_home_page_says_so(tmp_path):
    out = tmp_path / "out"
    build.build_site(out, proof_path=tmp_path / "missing.md")
    assert "was found when this site was built" in (out / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------- the parsers

PROOF_SAMPLE = """# Proof: A made-up set (not held out)

> **Development set, not held out.** Words from the file.

> **Incomplete.** Something is missing.

## Status

|  |  |
|---|---|
| Proof set | `x` (development) |
| Recorded spend | $1.00 |

## Results

**Equations through the whole pipeline (document task)**

| Metric | Pooled | Count | Cluster 95% (by section) |
|---|---|---|---|
| **end_to_end_exact_lenient (all gold equations)** | 0.5000 | 5/10 | [0.1, 0.9] |
| detection_recall | 1.0000 | 10/10 | [1.0, 1.0] |

## What these numbers leave out

- First limit.
- Second limit.
"""


def test_proof_reader_takes_label_numbers_status_and_limits(tmp_path):
    path = tmp_path / "PROOF.md"
    path.write_text(PROOF_SAMPLE, encoding="utf-8")
    proof = build.read_proof(path)
    assert proof.kind == "development"
    assert proof.banners[0].startswith("Development set, not held out.")
    assert ("Recorded spend", "$1.00") in proof.status
    values = {h.metric: (h.value, h.count, h.interval) for h in proof.headlines}
    assert values["end_to_end_exact_lenient (all gold equations)"] == (
        "0.5000",
        "5/10",
        "[0.1, 0.9]",
    )
    assert values["detection_recall"][0] == "1.0000"
    assert proof.limits == ["First limit.", "Second limit."]


def test_proof_reader_labels_a_held_out_file_as_held_out(tmp_path):
    path = tmp_path / "PROOF.md"
    path.write_text(
        PROOF_SAMPLE.replace("(not held out)", "(held out)").replace(
            "> **Development set, not held out.** Words", "> **Held-out set.** Words"
        ),
        encoding="utf-8",
    )
    assert build.read_proof(path).kind == "held-out"


def test_proof_reader_leaves_out_what_it_cannot_find(tmp_path):
    path = tmp_path / "PROOF.md"
    path.write_text("# Proof: nothing here\n\nJust words.\n", encoding="utf-8")
    proof = build.read_proof(path)
    assert proof.available and proof.headlines == [] and proof.limits == []


def test_split_main_returns_the_children_of_main_and_groups_them_by_page():
    document = (
        "<html><body><aside>x</aside><main>\n"
        '<h1 id="p1-e0">T</h1>\n<p id="p1-e1">a <math id="p1-e2"><mi>x</mi></math></p>\n'
        '<ul><li id="p2-e0">b</li></ul>\n<img src="x.png" alt="">\n'
        "</main><p>after</p></body></html>"
    )
    blocks = build.split_main(document)
    assert [b[:3] for b in blocks] == ["<h1", "<p ", "<ul", "<im"]
    grouped = build.blocks_by_page(document)
    assert set(grouped) == {1, 2}
    assert len(grouped[1]) == 2 and len(grouped[2]) == 2  # the image follows page 2's list


def test_headings_are_shifted_to_nest_under_the_pages_h3():
    fragment = '<h1 id="a">A</h1><h3 id="b">B</h3><h2 id="c">C</h2>'
    shifted = build.shift_headings(fragment)
    assert re.findall(r"<h(\d)", shifted) == ["4", "5", "5"]
    assert re.findall(r"</h(\d)>", shifted) == ["4", "5", "5"]
    assert 'class="lv1"' in shifted and 'class="lv3"' in shifted


def test_no_shifted_heading_skips_a_level():
    shifted = build.shift_headings("<h2>a</h2><h6>b</h6>")
    assert re.findall(r"<h(\d)", shifted) == ["4", "5"]


def test_css_is_limited_to_the_converted_scope():
    css = (
        "body { color: #000; } h1, p { margin: 0; } "
        "@media (min-width: 40rem) { main { x: 1; } } :focus-visible { outline: 0; }"
    )
    rules = build.scope_css(css)
    joined = "\n".join(rules)
    assert ".converted {color: #000;}" in joined
    assert ".converted h1, .converted p" in joined
    assert ".converted {x: 1;}" in joined
    assert ".converted :focus-visible" in joined
    assert "body" not in joined


def test_data_images_become_files_named_by_their_hash():
    out = build.Out()
    fragment = '<img alt="" src="data:image/png;base64,AAECAw==">'
    swapped = build.extract_images(fragment, out, "examples/x")
    name = re.search(r'src="(img/[0-9a-f]{16}\.png)"', swapped)
    assert name
    assert out.files[f"examples/x/{name.group(1)}"] == b"\x00\x01\x02\x03"


def test_links_that_leave_an_excerpt_point_at_the_full_page():
    fragment = '<a href="#here">h</a> <a href="#gone">g</a>'
    swapped = build.retarget_links(fragment, {"here"}, "converted.html")
    assert 'href="#here"' in swapped and 'href="converted.html#gone"' in swapped


def test_a_stray_flagged_page_is_not_produced_for_an_empty_page_number():
    document = '<main><p id="p1-e0">only page one</p></main>'
    assert build.blocks_by_page(document).get(5) is None
