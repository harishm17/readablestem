"""The finished document as an EPUB 3 publication.

Like the Canvas export and the Common Cartridge, this reads the finished HTML (the file the
pipeline wrote, docs/output.md) and nothing from the pipeline's steps. It writes a ZIP:

    mimetype                      (first entry, stored, not compressed)
    META-INF/container.xml
    EPUB/package.opf              metadata, manifest, spine
    EPUB/nav.xhtml                contents from the headings, page list from page markers
    EPUB/content.xhtml            the document as XHTML
    EPUB/images/image-01.png ...  every `data:` picture, as a file

What is kept and what changes:

- The text, headings, lists, tables, links, ids and alt text are not touched. MathML stays
  native: each `<math>` gets the MathML namespace and the content document's manifest item
  is marked `mathml`. Nothing is turned into an image.
- Each `data:` image becomes a file in `images/` (one file per distinct picture); `alt` stays
  on the `<img>`.
- The page's own stylesheet is kept in the content document. Scripts are not carried.
- ARIA roles stay; the DPUB ones (`doc-pagebreak`, `doc-noteref`, `doc-footnote`,
  `doc-backlink`) also get the matching `epub:type`.
- `nav.xhtml`: a `toc` from the document's headings (nested by level), and a `page-list` from
  the page markers (`role="doc-pagebreak"`) when the document has them.

Accessibility metadata is written from what is in the document, not from a fixed list:
`schema:accessMode`, `accessibilityFeature` (structural navigation, tableOfContents, MathML,
alternativeText, longDescription, describedMath, pageNavigation), `accessibilityHazard` and
`accessibilitySummary`. `accessModeSufficient` is left out when the document still has items
flagged for a person to fix. The package never carries `dcterms:conformsTo`: the tool
assists and does not claim conformance with any standard (CONTRIBUTING.md). The tests run the
package through EPUBCheck and DAISY Ace when they are installed.
"""

from __future__ import annotations

import hashlib
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from io import BytesIO

from readablestem.pipeline.export_canvas import (
    _VOID,
    CanvasExportError,
    Notes,
    _extract_images,
    _find,
    _Node,
    _parse,
)

NS_XHTML = "http://www.w3.org/1999/xhtml"
NS_EPUB = "http://www.idpf.org/2007/ops"
NS_MATHML = "http://www.w3.org/1998/Math/MathML"
NS_SVG = "http://www.w3.org/2000/svg"
NS_OPF = "http://www.idpf.org/2007/opf"
ZIP_TIME = (2020, 1, 1, 0, 0, 0)  # fixed, so the same document gives the same bytes

CONTENT = "EPUB/content.xhtml"
NAV = "EPUB/nav.xhtml"
PACKAGE = "EPUB/package.opf"

ACCESSIBILITY_SUMMARY = (
    "Made from a PDF by ReadableSTEM, which assists with accessibility and does not certify "
    "it. Equations are MathML, pictures have text descriptions, and the headings make the "
    "contents list. Check it before students use it."
)

_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*\Z")
_HEADING = re.compile(r"h[1-6]\Z")
# A link that still means something in a book: inside the document, or to the web or a mailbox.
_KEPT_LINK = re.compile(r"(#|https?:|mailto:)", re.IGNORECASE)
_ATTR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*\Z")
# Characters XML 1.0 does not allow at all.
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
_EPUB_TYPES = {
    "doc-pagebreak": "pagebreak",
    "doc-noteref": "noteref",
    "doc-footnote": "footnote",
    "doc-backlink": "backlink",
}
# The HTML parser lowercases attribute names; these SVG ones are case-sensitive.
_SVG_ATTRS = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio"}


class EpubExportError(CanvasExportError):
    """The document cannot be written as an EPUB."""


@dataclass
class EpubExport:
    title: str
    lang: str
    identifier: str
    package: str
    nav: str
    content: str
    images: dict[str, bytes]  # "images/image-01.png" -> bytes
    features: list[str]  # the accessibilityFeature values written
    page_labels: int  # entries in the page list
    headings: int  # entries in the contents (not counting a fallback entry)


# --------------------------------------------------------------------------- the content


def _clean(text: str) -> str:
    return _XML_BAD.sub("", text)


def _text(node: _Node | str) -> str:
    """The words of a node, for a contents entry: a `<math>` counts as its `alttext` when it
    has one, else its token text."""
    if isinstance(node, str):
        return node
    if node.tag == "math" and node.attrs.get("alttext"):
        return node.attrs["alttext"]
    if node.attrs.get("aria-hidden") == "true":
        return ""
    return "".join(_text(c) for c in node.children)


def _serialize(node: _Node | str) -> str:
    """XHTML for a node. Void elements self-close; every other element is written open and
    closed (`<p></p>`), so an XHTML reader never sees an empty element it would take as open
    when the file is read as HTML."""
    if isinstance(node, str):
        return escape(_clean(node), quote=False)
    attrs = ""
    for name, value in node.attrs.items():
        if not _ATTR_NAME.match(name):
            continue
        attrs += f' {name}="{escape(_clean(value), quote=True)}"'
    if node.tag in _VOID:
        return f"<{node.tag}{attrs}/>"
    inner = "".join(_serialize(c) for c in node.children)
    if node.tag in _MATHML_EMPTY and not inner:
        return f"<{node.tag}{attrs}/>"
    return f"<{node.tag}{attrs}>{inner}</{node.tag}>"


_MATHML_EMPTY = frozenset(["mspace", "none", "mprescripts", "malignmark", "maligngroup"])


# Elements the book does not carry: they load or run something, or play something.
_DROPPED = frozenset(
    [
        *("script", "iframe", "frame", "frameset", "object", "embed", "applet"),
        *("audio", "video", "source", "track", "link", "base", "meta", "canvas", "noscript"),
    ]
)
# Forms and their controls: nothing can be submitted. The words of a form stay; controls go.
_FORM_PARTS = frozenset(["form", "fieldset", "legend", "label"])
_CONTROLS = frozenset(["input", "button", "select", "textarea", "option", "optgroup", "datalist"])
# Attributes that hold an address. A `#` address is inside the file.
_ADDRESS = ("href", "xlink:href", "src", "srcset", "action", "formaction", "poster", "data")
_CSS_IMPORT = re.compile(r"@import\b[^;]*;?", re.IGNORECASE)
_CSS_URL_DECLARATION = re.compile(r"[^;{}]*url\s*\([^)]*\)[^;{}]*;?", re.IGNORECASE)
_SCRIPT_URL = re.compile(r"\s*(javascript|vbscript|data):", re.IGNORECASE)


def _clean_css(css: str) -> str:
    """The stylesheet without `@import` and without any declaration that loads a file
    (`url(...)`): the book fetches nothing."""
    return _CSS_URL_DECLARATION.sub("", _CSS_IMPORT.sub("", css))


def _remove(node: _Node, replacement: list[_Node | str] | None = None) -> None:
    """Take `node` out of its parent, putting `replacement` where it was."""
    assert node.parent is not None
    siblings = node.parent.children
    at = siblings.index(node)
    siblings[at : at + 1] = replacement or []
    for child in replacement or []:
        if isinstance(child, _Node):
            child.parent = node.parent
    node.parent = None


def _sanitise(root: _Node, images: set[str]) -> None:
    """Take out what loads, runs, submits or plays, in place. The page is the pipeline's own,
    but text from a PDF and a reviewer's edits still went through it.

    - elements in `_DROPPED` go with their children; forms are unwrapped (their words stay)
      and their controls go, so nothing can be submitted;
    - an address attribute is kept only if it is inside the file (`#...`), a web or mail link
      on `<a>`, or a picture already in the package; `javascript:` and `data:` never stay;
    - an `<img>` still pointing anywhere but the package (remote, relative) becomes its alt
      text, so the book fetches nothing; with no alt text it goes;
    - event handlers go; `@import` and `url()` go from stylesheets and `style` attributes."""
    for node in list(root.walk()):
        if node.parent is None:
            continue  # taken out with an ancestor
        if node.tag in _DROPPED or node.tag in _CONTROLS:
            _remove(node)
        elif node.tag in _FORM_PARTS:
            _remove(node, list(node.children))
    for node in list(root.walk()):
        if node.parent is None:
            continue
        if node.tag == "style":
            node.children = [_clean_css(c) if isinstance(c, str) else c for c in node.children]
        if "style" in node.attrs:
            node.attrs["style"] = _clean_css(node.attrs["style"])
        for name in [n for n in node.attrs if n.startswith("on")]:
            del node.attrs[name]
        if node.tag == "img" and node.attrs.get("src", "") not in images:
            alt = " ".join(node.attrs.get("alt", "").split())
            _remove(node, [alt] if alt else [])
            continue
        for name in [n for n in node.attrs if n in _ADDRESS]:
            value = node.attrs[name].strip()
            if name == "src" and node.tag == "img":
                continue
            if name == "href" and node.tag == "a":
                keep = bool(_KEPT_LINK.match(value))  # others point at nothing in a book
            else:
                keep = value.startswith("#") and name in ("href", "xlink:href")
            if not keep or _SCRIPT_URL.match(value):
                del node.attrs[name]


def _prepare(root: _Node, images: set[str]) -> tuple[bool, bool]:
    """Namespaces, `epub:type` and attribute fixes, in place. Returns (has math, has svg)."""
    _sanitise(root, images)
    has_math = has_svg = False
    for node in root.walk():
        if node.tag == "math":
            has_math = True
            node.attrs["xmlns"] = NS_MATHML
            # `intent` (MathML 4, the chemistry hint for MathCAT) is not in the schema EPUB 3
            # validates against: epubcheck reports RSC-005 on it. Everything else stays.
            node.attrs.pop("intent", None)
        elif node.tag == "svg":
            has_svg = True
            node.attrs["xmlns"] = NS_SVG
            node.attrs["xmlns:xlink"] = "http://www.w3.org/1999/xlink"
        if node.tag == "svg" or _inside(node, "svg"):
            for name in [n for n in node.attrs if n in _SVG_ATTRS]:
                node.attrs[_SVG_ATTRS[name]] = node.attrs.pop(name)
        roles = node.attrs.get("role", "").split()
        epub_type = [_EPUB_TYPES[r] for r in roles if r in _EPUB_TYPES]
        if epub_type:
            node.attrs["epub:type"] = " ".join(epub_type)
    return has_math, has_svg


def _inside(node: _Node, tag: str) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.parent
    return False


def _ensure_ids(headings: list[_Node], markers: list[_Node], taken: set[str]) -> None:
    """Give a heading or page marker without an id one, so the navigation can point at it."""

    def fresh(prefix: str, n: int) -> str:
        while f"{prefix}-{n}" in taken:
            n += 1
        taken.add(f"{prefix}-{n}")
        return f"{prefix}-{n}"

    for i, node in enumerate(headings, 1):
        if not node.attrs.get("id"):
            node.attrs["id"] = fresh("heading", i)
    for i, node in enumerate(markers, 1):
        if not node.attrs.get("id"):
            node.attrs["id"] = fresh("page", i)


def _marker_label(node: _Node) -> str:
    label = " ".join((node.attrs.get("aria-label") or node.attrs.get("title") or "").split())
    return label or " ".join(_text(node).split())


# ------------------------------------------------------------------------ the navigation


def _build_toc(entries: list[tuple[int, str, str]]) -> str:
    """The contents as nested lists, built as a tree so the nesting is always well formed. A
    heading that skips a level (h1 then h3) nests one step down, not two."""

    @dataclass
    class Item:
        level: int
        link: str
        children: list[Item]

    top: list[Item] = []
    stack: list[Item] = []
    for level, element_id, text in entries:
        link = f'<a href="content.xhtml#{escape(element_id, quote=True)}">{escape(text)}</a>'
        item = Item(level, link, [])
        while stack and stack[-1].level >= level:
            stack.pop()
        (stack[-1].children if stack else top).append(item)
        stack.append(item)

    def render(items: list[Item]) -> str:
        parts = []
        for it in items:
            inner = f"<ol>{render(it.children)}</ol>" if it.children else ""
            parts.append(f"<li>{it.link}{inner}</li>")
        return "".join(parts)

    return f"<ol>{render(top)}</ol>"


def _nav_document(
    title: str, lang: str, toc: list[tuple[int, str, str]], pages: list[tuple[str, str]]
) -> str:
    lang_attr = escape(lang, quote=True)
    page_list = ""
    if pages:
        items = "".join(
            f'<li><a href="content.xhtml#{escape(i, quote=True)}">{escape(label)}</a></li>'
            for i, label in pages
        )
        page_list = (
            f'<nav epub:type="page-list" role="doc-pagelist" hidden="hidden"><ol>{items}</ol></nav>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<html xmlns="{NS_XHTML}" xmlns:epub="{NS_EPUB}" lang="{lang_attr}" '
        f'xml:lang="{lang_attr}">\n'
        f'<head><meta charset="utf-8"/><title>{escape(title)}</title></head>\n'
        f'<body><nav epub:type="toc" role="doc-toc" id="toc">'
        f"<h1>{escape(title)}</h1>{_build_toc(toc)}</nav>{page_list}</body>\n"
        "</html>\n"
    )


# ------------------------------------------------------------------------ the metadata


def _has_description(figure: _Node) -> bool:
    """A figure has a long description when a `<details class="figure-description">` is inside
    it or is the element right after it."""
    if any(n.tag == "details" and "figure-description" in n.classes() for n in figure.walk()):
        return True
    kids = figure.parent.children if figure.parent else []
    siblings = [c for c in kids if isinstance(c, _Node)]
    i = siblings.index(figure)
    nxt = siblings[i + 1] if i + 1 < len(siblings) else None
    return nxt is not None and nxt.tag == "details" and "figure-description" in nxt.classes()


def _features(
    *,
    root: _Node,
    has_math: bool,
    n_headings: int,
    n_pages: int,
) -> tuple[list[str], list[str], list[str]]:
    """(accessMode values, accessModeSufficient values, accessibilityFeature values)."""
    images = [n for n in root.walk() if n.tag == "img"]
    modes = ["textual"]
    if images:
        modes.append("visual")
    # A feature is claimed only when every item it applies to has it.
    features = []
    if n_headings:  # the one-entry fallback is not a contents list
        features += ["tableOfContents", "structuralNavigation"]
    maths = [n for n in root.walk() if n.tag == "math"]
    if has_math:
        features.append("MathML")
        if maths and all(n.attrs.get("alttext", "").strip() for n in maths):
            features.append("describedMath")
    if images and all(n.attrs.get("alt", "").strip() for n in images):
        features.append("alternativeText")
    figures = [n for n in root.walk() if n.tag == "figure"]
    if figures and all(_has_description(f) for f in figures):
        features.append("longDescription")
    if n_pages:
        features.append("pageNavigation")
    flagged = any("flag-note" in n.classes() or "data-status" in n.attrs for n in root.walk())
    sufficient = [] if flagged else ["textual"]
    return modes, sufficient, sorted(features)


def _package_document(
    *,
    identifier: str,
    title: str,
    lang: str,
    modified: str,
    modes: list[str],
    sufficient: list[str],
    features: list[str],
    images: dict[str, bytes],
    item_properties: list[str],
    paginated: bool,
) -> str:
    meta = [
        f'<dc:identifier id="pub-id">{escape(identifier)}</dc:identifier>',
        f"<dc:title>{escape(title)}</dc:title>",
        f"<dc:language>{escape(lang)}</dc:language>",
        f'<meta property="dcterms:modified">{modified}</meta>',
        *(PAGINATION_SOURCE if paginated else []),
        *(f'<meta property="schema:accessMode">{m}</meta>' for m in modes),
        *(f'<meta property="schema:accessModeSufficient">{m}</meta>' for m in sufficient),
        *(f'<meta property="schema:accessibilityFeature">{f}</meta>' for f in features),
        '<meta property="schema:accessibilityHazard">none</meta>',
        f'<meta property="schema:accessibilitySummary">{escape(ACCESSIBILITY_SUMMARY)}</meta>',
    ]
    props = f' properties="{" ".join(item_properties)}"' if item_properties else ""
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        f'<item id="content" href="content.xhtml" media-type="application/xhtml+xml"{props}/>',
    ]
    for i, name in enumerate(sorted(images), 1):
        media = _MEDIA_TYPES[name.rsplit(".", 1)[1]]
        manifest.append(f'<item id="image-{i:02d}" href="{name}" media-type="{media}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<package xmlns="{NS_OPF}" version="3.0" unique-identifier="pub-id" '
        f'xml:lang="{escape(lang, quote=True)}">\n'
        f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n  '
        + "\n  ".join(meta)
        + "\n</metadata>\n<manifest>\n  "
        + "\n  ".join(manifest)
        + '\n</manifest>\n<spine>\n  <itemref idref="content"/>\n</spine>\n</package>\n'
    )


# The page list numbers the pages of the PDF, so the package says what they belong to.
PAGINATION_SOURCE = [
    '<dc:source id="pagination-source">Source PDF</dc:source>',
    '<meta refines="#pagination-source" property="source-of">pagination</meta>',
]

CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles><rootfile full-path="EPUB/package.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles>\n'
    "</container>\n"
)


# ------------------------------------------------------------------------------- export


def export_epub(
    html: str,
    *,
    title: str | None = None,
    modified: datetime | None = None,
    identifier: str | None = None,
) -> EpubExport:
    """The EPUB parts of a finished document. `title` overrides the page's `<title>`;
    `modified` is the `dcterms:modified` time (default now, UTC); `identifier` is the
    publication's id (default a UUID made from the document, so the same document keeps the
    same one)."""
    root = _parse(html)
    html_tag = _find(root, "html")
    body = _find(root, "body")
    if html_tag is None or body is None:
        raise EpubExportError("the document has no <html> and <body>")
    lang = html_tag.attrs.get("lang", "").strip()
    lang = lang if _LANGUAGE.match(lang) else "en"
    page_title = _find(root, "title")
    title = (
        " ".join((title or (_text(page_title) if page_title else "") or "").split())
        or "Converted document"
    )

    images = _extract_images(root, Notes(), lambda name: name)
    has_math, has_svg = _prepare(root, set(images))
    stylesheets = [_text(n) for n in root.walk() if n.tag == "style"]

    taken = {n.attrs["id"] for n in body.walk() if n.attrs.get("id")}
    headings = [n for n in body.walk() if _HEADING.match(n.tag)]
    markers = [n for n in body.walk() if "doc-pagebreak" in n.attrs.get("role", "").split()]
    _ensure_ids(headings, markers, taken)

    toc = [
        (int(h.tag[1]), h.attrs["id"], " ".join(_text(h).split()))
        for h in headings
        if " ".join(_text(h).split())
    ]
    pages = [(m.attrs["id"], label) for m in markers if (label := _marker_label(m))]
    n_headings = len(toc)
    if not toc:  # a contents list must have an entry
        toc = [(1, "start", title)]
        body.children.insert(0, _Node("span", {"id": "start"}, [], body))

    modes, sufficient, features = _features(
        root=body, has_math=has_math, n_headings=n_headings, n_pages=len(pages)
    )
    lang_attr = escape(lang, quote=True)
    head = f'<meta charset="utf-8"/><title>{escape(_clean(title))}</title>' + "".join(
        f"<style>{escape(_clean(css), quote=False)}</style>" for css in stylesheets
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<html xmlns="{NS_XHTML}" xmlns:epub="{NS_EPUB}" lang="{lang_attr}" '
        f'xml:lang="{lang_attr}">\n<head>{head}</head>\n'
        f"<body>{''.join(_serialize(c) for c in body.children)}</body>\n</html>\n"
    )
    nav = _nav_document(title, lang, toc, pages)

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    identifier = identifier or f"urn:uuid:{uuid.UUID(digest[:32])}"
    stamp = (modified or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    properties = ["mathml"] if has_math else []
    if has_svg:
        properties.append("svg")
    package = _package_document(
        identifier=identifier,
        title=title,
        lang=lang,
        modified=stamp,
        modes=modes,
        sufficient=sufficient,
        features=features,
        images=images,
        item_properties=properties,
        paginated=bool(pages),
    )
    return EpubExport(
        title, lang, identifier, package, nav, content, images, features, len(pages), n_headings
    )


def build_epub(export: EpubExport) -> bytes:
    """The EPUB zip: `mimetype` first and stored, then the rest in a fixed order with a fixed
    timestamp."""
    entries: list[tuple[str, bytes]] = [
        ("META-INF/container.xml", CONTAINER.encode()),
        (PACKAGE, export.package.encode("utf-8")),
        (NAV, export.nav.encode("utf-8")),
        (CONTENT, export.content.encode("utf-8")),
        *((f"EPUB/{name}", data) for name, data in sorted(export.images.items())),
    ]
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        first = zipfile.ZipInfo("mimetype", ZIP_TIME)
        first.compress_type = zipfile.ZIP_STORED
        archive.writestr(first, b"application/epub+zip")
        for name, data in entries:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, data)
    return out.getvalue()
