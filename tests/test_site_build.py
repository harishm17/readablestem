"""The static demo site's build script (site/build.py): the pages it writes parse, every
link resolves inside the site, nothing is loaded from outside, the build is repeatable, and
the small parsers it relies on behave. No browser here; the axe run is in
test_site_browser.py.

site/build.py is loaded by path: a folder called `site` must not go on the import path
(it would shadow the standard library's `site` module).
"""

from __future__ import annotations

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


def test_pages_name_the_author_and_the_licences(built):
    out, _ = built
    for rel in OWN_PAGES:
        text = (out / rel).read_text(encoding="utf-8")
        assert "Harish Manoharan" in text, rel
        assert "MIT" in text and "CC BY 4.0" in text, rel


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


# ---------------------------------------------------------------------- the parsers


def test_no_shifted_heading_skips_a_level():
    shifted = build.shift_headings("<h2>a</h2><h6>b</h6>")
    assert re.findall(r"<h(\d)", shifted) == ["4", "5"]
