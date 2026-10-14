"""The finished HTML file in a form Canvas keeps: a zip with `index.html`, an `images/` folder
and a README, and, when the course number is known, a page to paste into a Canvas Page.

Why: the file the pipeline writes is one self-contained page (docs/output.md). Canvas's
sanitizer (gems/canvas_sanitize in instructure/canvas-lms; the allowlist is encoded in
tests/fixtures/canvas/canvas_sanitize_allowlist.json) removes `<style>`, `<svg>`, `<main>`,
`tabindex` and `headers`, and any `<img>` whose source is not http, https or relative, which
is what every `data:` image is. This module reads the finished HTML, never the pipeline's
steps or its output, and writes two things from it:

- `index.html`: the same document with no `<style>` block. The rules of the stylesheet that
  matter are written into `style` attributes (Canvas keeps `style` on every element, but
  only the properties it lists), and each `data:` image becomes `images/image-NN.png`
  beside it. It is a whole page for opening from a folder or a web server, or for linking
  from Canvas Files.
- `canvas-page.html`, only when the Canvas course number is given: the body of the same
  document, without the page wrapper and the elements Canvas would remove, with pictures at
  `/courses/<number>/file_contents/course%20files/<folder>/images/image-NN.png`, the address
  Canvas gives a file by its folder path (`file_contents/*file_path` in config/routes.rb
  and `Folder.find_attachment_in_context_with_path`). The instructor uploads the zip into a
  Files folder with that name, expands it, and pastes this into the Page's HTML editor.

The text, MathML, alt text, headings, lists, tables and links are not touched. Presentation
is: a rule is carried over only if its property is one Canvas keeps (`CANVAS_PROPERTIES`;
tests check this set against the encoded allowlist), and what cannot be carried is counted
in `Notes`, so the README and the tests can say it. Nothing here edits, adds or removes
content.
"""

from __future__ import annotations

import base64
import hashlib
import re
import textwrap
import unicodedata
import zipfile
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from io import BytesIO

# Canvas stores a Page's body in a column of at most this many characters
# (`maximum_long_text_length` = 500 KB - 1, config/initializers/active_record.rb;
# WikiPage validates :body with it). A longer page is not offered for pasting.
CANVAS_PAGE_LIMIT = 500 * 1024 - 1

# The CSS properties this module writes into `style` attributes: those of the pipeline's
# own stylesheet (assemble.STYLE and the feature CSS) that Canvas also keeps. Every one is
# in the sanitizer's `css_properties`; a property of the stylesheet that is not here is
# dropped and recorded in `Notes.dropped_css`.
CANVAS_PROPERTIES = frozenset(
    [
        "align-items",
        "background",
        "border",
        "border-bottom-width",
        "border-collapse",
        "border-radius",
        "border-top",
        "clip",
        "color",
        "cursor",
        "display",
        "flex",
        "flex-wrap",
        "font",
        "font-family",
        "font-size",
        "gap",
        "height",
        "line-height",
        "list-style",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-top",
        "max-width",
        "min-width",
        "overflow",
        "overflow-x",
        "overflow-y",
        "padding",
        "padding-bottom",
        "padding-left",
        "position",
        "text-align",
        "text-indent",
        "vertical-align",
        "white-space",
        "width",
    ]
)
# A property Canvas drops, written as one it keeps (same look, other spelling).
_REWRITES = {("clip-path", "inset(50%)"): ("clip", "rect(0,0,0,0)")}
# Rules on these bare elements are the page's typography; Canvas has its own for a Page,
# so they are left to it in the paste version (the standalone page has none and keeps them).
_BASELINE_TAGS = frozenset(
    ["body", "main", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li"]
)
_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_DATA_URI = re.compile(r"data:(?P<type>[A-Za-z0-9.+/-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+)\Z")
_COURSE_NUMBER = re.compile(r"[0-9]{1,20}\Z")
# The fonts a `<math>` element asks for, in order (T3-8). Canvas keeps `font-family` in a
# `style` attribute but has no `@font-face` or style block, so the stack can only name fonts
# the reader's computer has. Native MathML in Chrome, Edge, Firefox and Safari needs one with
# an OpenType MATH table; this list is the families that have one, most widely installed
# last-resort first: Latin Modern Math and STIX Two Math (TeX distributions, Linux, recent
# macOS), Cambria Math (Windows, Office for Mac), Noto Sans Math, Libertinus, TeX Gyre,
# XITS, Asana and DejaVu (Linux packages), then the browser's own `math` family.
MATH_FONT_STACK = (
    "'Latin Modern Math','STIX Two Math','Cambria Math','Noto Sans Math','Libertinus Math',"
    "'TeX Gyre Pagella Math','XITS Math','Asana Math','DejaVu Math TeX Gyre',math"
)
_MATH_RULE = f"\nmath {{ font-family: {MATH_FONT_STACK}; }}\n"
HIDDEN_CLASS = "visually-hidden"
CANVAS_HIDDEN_CLASS = "screenreader-only"  # Canvas's own class for the same thing


class CanvasExportError(ValueError):
    """The document cannot be exported (an image of a kind this module does not write)."""


def valid_course_number(text: str) -> bool:
    """A Canvas course number as it appears in `/courses/<number>`: digits only."""
    return bool(_COURSE_NUMBER.match(text))


def folder_name(title: str, job_id: str) -> str:
    """The Files folder the zip is expanded into: ASCII letters, digits and hyphens, so no
    character in it can be mistyped, case-folded or encoded differently on the way."""
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    words = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:40].strip("-")
    return f"{words or 'document'}-{job_id[:6]}"


# ----------------------------------------------------------------------------- the tree


@dataclass(eq=False)
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)
    parent: _Node | None = None

    def walk(self):
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.walk()

    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


class _Parser(HTMLParser):
    """The finished HTML (this pipeline's own, well formed) as a tree. Comments and the
    doctype are not kept; text is kept as text and escaped again when written."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document")
        self.stack = [self.root]

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> _Node:
        node = _Node(tag, {k: v or "" for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._open(tag, attrs)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _parse(html: str) -> _Node:
    parser = _Parser()
    parser.feed(html)
    parser.close()
    return parser.root


def _write(node: _Node | str) -> str:
    if isinstance(node, str):
        return escape(node, quote=False)
    attrs = "".join(f' {k}="{escape(v, quote=True)}"' for k, v in node.attrs.items())
    if node.tag in _VOID:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{''.join(_write(c) for c in node.children)}</{node.tag}>"


# ----------------------------------------------------------------------------- the stylesheet


@dataclass(frozen=True)
class _Compound:
    tag: str | None
    classes: tuple[str, ...]
    attrs: tuple[tuple[str, str | None], ...]
    ids: tuple[str, ...]


@dataclass(frozen=True)
class _Rule:
    # Compounds from the leftmost, each with the combinator that joins it to the one before
    # it (" " descendant, ">" child; "" for the first).
    parts: tuple[tuple[str, _Compound], ...]
    declarations: tuple[tuple[str, str], ...]
    specificity: tuple[int, int, int]
    order: int

    @property
    def baseline(self) -> bool:
        """Only bare element names, and the subject one of the page's typography."""
        bare = all(not (c.classes or c.attrs or c.ids) for _, c in self.parts)
        return bare and self.parts[-1][1].tag in _BASELINE_TAGS


_COMPOUND = re.compile(r"(?P<tag>[A-Za-z][\w-]*|\*)?(?P<rest>(?:\.[\w-]+|#[\w-]+|\[[^\]]+\])*)\Z")
_ATTR = re.compile(r'\[\s*([\w-]+)\s*(?:=\s*(?:"([^"]*)"|([^\]\s]*))\s*)?\]')


def _parse_compound(text: str) -> _Compound | None:
    """None for what is not carried: pseudo-classes and pseudo-elements."""
    match = _COMPOUND.match(text)
    if not match:
        return None
    rest = match.group("rest")
    attrs = tuple(
        (m.group(1), m.group(2) if m.group(2) is not None else m.group(3))
        for m in _ATTR.finditer(rest)
    )
    plain = _ATTR.sub("", rest)
    return _Compound(
        None if match.group("tag") in (None, "*") else match.group("tag"),
        tuple(re.findall(r"\.([\w-]+)", plain)),
        attrs,
        tuple(re.findall(r"#([\w-]+)", plain)),
    )


def _parse_selector(text: str) -> tuple[tuple[str, _Compound], ...] | None:
    tokens = re.split(r"\s*(>)\s*|\s+", text.strip())
    parts: list[tuple[str, _Compound]] = []
    combinator = ""
    for token in tokens:
        if token is None or token == "":
            continue
        if token == ">":
            combinator = ">"
            continue
        compound = _parse_compound(token)
        if compound is None:
            return None
        parts.append((combinator or (" " if parts else ""), compound))
        combinator = ""
    return tuple(parts) or None


def _declarations(block: str) -> list[tuple[str, str]]:
    out, depth, start = [], 0, 0
    for i, ch in enumerate([*block, ";"]):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth == 0:
            piece = block[start:i]
            start = i + 1
            name, colon, value = piece.partition(":")
            if colon and name.strip():
                out.append((name.strip().lower(), re.sub(r"\s+", " ", value).strip()))
    return out


def _strip_at_rules(css: str) -> tuple[str, list[str]]:
    """The stylesheet without its `@media` and other at-rule blocks, and their names."""
    out, skipped, i = [], [], 0
    while i < len(css):
        if css[i] != "@":
            out.append(css[i])
            i += 1
            continue
        end = css.index("{", i)
        skipped.append(css[i:end].strip())
        depth = 0
        for j in range(end, len(css)):
            depth += (css[j] == "{") - (css[j] == "}")
            if depth == 0:
                i = j + 1
                break
        else:
            i = len(css)
    return "".join(out), skipped


def _parse_css(css: str, notes: Notes) -> list[_Rule]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css, skipped = _strip_at_rules(css)
    notes.uncarried_selectors.update(skipped)
    rules: list[_Rule] = []
    for selectors, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        declarations = tuple(_declarations(block))
        for selector in selectors.split(","):
            parts = _parse_selector(selector)
            if parts is None:
                notes.uncarried_selectors.add(" ".join(selector.split()))
                continue
            ids = sum(len(c.ids) for _, c in parts)
            classes = sum(len(c.classes) + len(c.attrs) for _, c in parts)
            tags = sum(c.tag is not None for _, c in parts)
            rules.append(_Rule(parts, declarations, (ids, classes, tags), len(rules)))
    return rules


def _matches_compound(node: _Node, c: _Compound) -> bool:
    if c.tag is not None and node.tag != c.tag:
        return False
    if any(cls not in node.classes() for cls in c.classes):
        return False
    if any(node.attrs.get("id") != i for i in c.ids):
        return False
    for name, value in c.attrs:
        if name not in node.attrs or (value is not None and node.attrs[name] != value):
            return False
    return True


def _matches(node: _Node, parts: tuple[tuple[str, _Compound], ...]) -> bool:
    if not _matches_compound(node, parts[-1][1]):
        return False
    if len(parts) == 1:
        return True
    combinator, rest = parts[-1][0], parts[:-1]
    parent = node.parent
    if combinator == ">":
        return parent is not None and _matches(parent, rest)
    while parent is not None:
        if _matches(parent, rest):
            return True
        parent = parent.parent
    return False


# ----------------------------------------------------------------------------- the export


@dataclass
class Notes:
    """What the export had to leave out, for the README and the tests."""

    images: int = 0
    # CSS properties of the stylesheet that Canvas does not keep, so were not written.
    dropped_css: set[str] = field(default_factory=set)
    # Rules (selectors, at-rules) that cannot be written as an inline style: `:focus-visible`,
    # `@media`. Browsers' own focus rings and print/responsive rules.
    uncarried_selectors: set[str] = field(default_factory=set)
    # Tables that use `headers` to link cells to headers: Canvas removes the attribute.
    tables_with_headers: int = 0
    # Scroll boxes (wide tables and equations) that are keyboard focusable by `tabindex`,
    # which Canvas removes.
    scroll_boxes: int = 0
    headings_shifted: bool = False
    page_too_large: bool = False


@dataclass
class CanvasExport:
    folder: str
    title: str
    lang: str
    index_html: str
    page_html: str | None
    images: dict[str, bytes]  # "images/image-01.png" -> bytes
    notes: Notes
    course_number: str | None = None


def _styles_of(root: _Node) -> str:
    return "\n".join(
        "".join(c for c in n.children if isinstance(c, str))
        for n in root.walk()
        if n.tag == "style"
    )


def _find(root: _Node, tag: str) -> _Node | None:
    return next((n for n in root.walk() if n.tag == tag), None)


def _inline_styles(root: _Node, rules: list[_Rule], notes: Notes, *, page: bool) -> None:
    """Write the carried rules into each element's `style` attribute, in place."""
    for node in list(root.walk()):
        if node.tag in ("style", "head", "title", "meta", "html"):
            continue
        matched = [r for r in rules if _matches(node, r.parts) and not (page and r.baseline)]
        matched.sort(key=lambda r: (r.specificity, r.order))
        merged: dict[str, str] = {}
        own = _declarations(node.attrs.get("style", ""))
        for name, value in [*(d for r in matched for d in r.declarations), *own]:
            name, value = _REWRITES.get((name, value.replace(" ", "")), (name, value))
            if name not in CANVAS_PROPERTIES:
                notes.dropped_css.add(name)
                continue
            merged.pop(name, None)  # the later value goes last, as the cascade decides
            merged[name] = value
        if merged:
            node.attrs["style"] = ";".join(f"{k}:{v}" for k, v in merged.items()) + ";"
        else:
            node.attrs.pop("style", None)


def _extract_images(root: _Node, notes: Notes, src_for) -> dict[str, bytes]:
    """Each `data:` image becomes a file; the same picture used twice is one file."""
    files: dict[str, bytes] = {}
    by_digest: dict[str, str] = {}
    for img in (n for n in root.walk() if n.tag == "img"):
        match = _DATA_URI.match(img.attrs.get("src", ""))
        if not match:
            continue  # an http(s) or relative source stays as it is
        extension = _IMAGE_TYPES.get(match.group("type").lower())
        if extension is None:
            raise CanvasExportError(f"an image of type {match.group('type')} cannot be exported")
        data = base64.b64decode(re.sub(r"\s+", "", match.group("data")))
        digest = hashlib.sha256(data).hexdigest()
        if digest not in by_digest:
            by_digest[digest] = f"images/image-{len(by_digest) + 1:02d}.{extension}"
            files[by_digest[digest]] = data
        img.attrs["src"] = src_for(by_digest[digest])
    notes.images = len(files)
    return files


def _canvas_page(root: _Node, lang: str, notes: Notes) -> str:
    """The body as it goes into a Page: no wrapper elements Canvas removes, no attributes
    it removes, headings one level down (the Page's title is the top heading in Canvas)."""
    body = _find(root, "body")
    top = list(body.children if body else root.children)
    parts: list[_Node | str] = []
    for child in top:
        if isinstance(child, _Node) and child.tag == "main":
            parts.extend(child.children)
        else:
            parts.append(child)
    headings = [n for n in root.walk() if re.fullmatch(r"h[1-6]", n.tag)]
    if headings and not any(h.tag == "h6" for h in headings):
        for h in headings:
            h.tag = f"h{int(h.tag[1]) + 1}"
        notes.headings_shifted = True
    inner = "".join(_write(p) for p in parts).strip("\n")
    return f'<div lang="{escape(lang, quote=True)}">\n{inner}\n</div>\n'


def export_canvas(
    html: str, *, title: str, folder: str, course_number: str | None = None
) -> CanvasExport:
    """The Canvas export of a finished document. `folder` is the Files folder the zip is
    expanded into (`folder_name`); `course_number` (digits) is needed for the paste page."""
    if course_number is not None and not valid_course_number(course_number):
        raise CanvasExportError("a course number is digits only")

    notes = Notes()
    standalone = _parse(html)
    rules = _parse_css(_styles_of(standalone) + _MATH_RULE, notes)
    root_tag = _find(standalone, "html")
    lang = (root_tag.attrs.get("lang") if root_tag else None) or "en"
    notes.tables_with_headers = sum(
        1
        for t in standalone.walk()
        if t.tag == "table" and any("headers" in c.attrs for c in t.walk())
    )
    notes.scroll_boxes = sum(1 for n in standalone.walk() if "tabindex" in n.attrs)

    # The paste version is its own copy of the tree: it differs in image addresses, in
    # which rules are carried, and in what Canvas removes anyway.
    page_tree = _parse(html) if course_number else None

    images = _extract_images(standalone, notes, lambda name: name)
    for node in standalone.walk():
        if HIDDEN_CLASS in node.classes():
            node.attrs["class"] = f"{node.attrs['class']} {CANVAS_HIDDEN_CLASS}"
    for style in [n for n in standalone.walk() if n.tag == "style"]:
        assert style.parent is not None
        style.parent.children.remove(style)
    _inline_styles(standalone, rules, notes, page=False)
    index_html = "<!doctype html>\n" + _write(root_tag) + "\n" if root_tag else _write(standalone)

    page_html = None
    if page_tree is not None:
        base = f"/courses/{course_number}/file_contents/course%20files/{folder}/"
        _extract_images(page_tree, Notes(), lambda name: base + name)
        for node in page_tree.walk():
            if HIDDEN_CLASS in node.classes():
                node.attrs["class"] = f"{node.attrs['class']} {CANVAS_HIDDEN_CLASS}"
            node.attrs.pop("tabindex", None)
            node.attrs.pop("headers", None)
        _inline_styles(page_tree, rules, Notes(), page=True)
        page_html = _canvas_page(page_tree, lang, notes)
        if len(page_html) > CANVAS_PAGE_LIMIT:
            notes.page_too_large, page_html = True, None

    return CanvasExport(folder, title, lang, index_html, page_html, images, notes, course_number)


# ----------------------------------------------------------------------------- the zip

ZIP_TIME = (2020, 1, 1, 0, 0, 0)  # fixed, so the same document gives the same bytes


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _wrap(text: str, indent: str, hang: str = "") -> list[str]:
    return textwrap.wrap(text, width=84, initial_indent=indent, subsequent_indent=indent + hang)


def readme(export: CanvasExport) -> str:
    """The instructor's instructions, plain text, written for exactly this export."""
    n = export.notes
    paste = export.page_html is not None
    lines = [
        f"Canvas version of: {' '.join(export.title.split())}",
        "Made by a11y-stem, a tool that assists with accessibility. Read the result through",
        "once before you share it.",
        "",
        "WHAT IS IN THIS ZIP",
        "  index.html         the whole document as one web page. Canvas removes style blocks,",
        "                     so its styles are written into the page itself.",
        f"  images/            the {_plural(n.images, 'picture')} the page uses, as files.",
        "                     Canvas removes pictures stored inside a page.",
    ]
    if paste:
        lines.append("  canvas-page.html   the same content, ready to paste into a Canvas Page.")
    lines += ["  README.txt         this file.", ""]
    if paste:
        lines += [
            "POST IT AS A CANVAS PAGE",
            "  1. In your course, open Files and make a new folder named exactly:",
            f"         {export.folder}",
            "  2. Open that folder, upload this zip file, and choose to expand it when Canvas",
            "     asks. Wait until the images folder is listed.",
            "  3. Open Pages and make a new page with a title.",
            *(
                [
                    "     Canvas shows the title as the top heading, so the headings in the page",
                    "     start one level lower than in the PDF.",
                ]
                if n.headings_shifted
                else []
            ),
            "  4. Switch the editor to its HTML view (the </> button).",
            "  5. Open canvas-page.html in a plain text editor (Notepad, TextEdit), copy all of",
            "     it, paste it into the HTML view, and save.",
            "  6. Look at the page as a student would (Student View). Check a picture and an",
            "     equation.",
            *_wrap(
                f"The pictures are found by the course number ({export.course_number}) and the "
                "folder name above. If they do not show, check both. If you rename the folder or "
                "copy the course, make the zip again with the new number.",
                "  ",
            ),
            "",
            "OR LINK THE WEB PAGE",
            *_wrap(
                "Expand the zip in a Files folder and link index.html from a module or a page. "
                "We have not confirmed that Canvas shows the pictures of an .html file in Files: "
                "if they are missing, use the Canvas page above. index.html also works from a "
                "folder on your computer and from any web server.",
                "  ",
            ),
        ]
    else:
        lines += [
            "POST IT IN CANVAS",
            *_wrap(
                "index.html is a whole web page and needs the images folder beside it. In your "
                "course, open Files, make a new folder, open it, upload this zip file and choose "
                "to expand it. Then link index.html from a module or a page. We have not "
                "confirmed that Canvas shows the pictures of an .html file in Files, so open the "
                "link as a student would (Student View) and check a picture.",
                "  ",
            ),
            "",
            *_wrap(
                "To paste the content into a Canvas Page instead, download the zip again from "
                "the job page and enter your Canvas course number (the number after /courses/ "
                "in the address of your course). You then also get canvas-page.html, and these "
                "instructions explain how to use it.",
                "  ",
            ),
        ]
        if n.page_too_large:
            lines += [
                "",
                *_wrap(
                    "A Canvas Page holds about 500 KB of text and this document is longer, so no "
                    "page to paste is included, even with a course number.",
                    "  ",
                ),
            ]
    changes = []
    if n.tables_with_headers:
        changes.append(
            f"{_plural(n.tables_with_headers, 'table')} link cells to their headers with an "
            "attribute Canvas removes from a pasted page, so a screen reader there may not say "
            "which row and column a value belongs to. index.html keeps the links."
        )
    if n.scroll_boxes:
        changes.append(
            f"Wide tables and equations sit in a box that scrolls sideways ({n.scroll_boxes} "
            "here). Canvas removes the setting that lets a keyboard user move into a box, "
            "so in a pasted page they may need a mouse or touch to scroll it. index.html keeps "
            "the setting."
        )
    changes.append("Bold labels (such as “Not converted.”) show in normal weight.")
    if changes:
        lines += ["", "WHAT CANVAS STILL CHANGES"]
        for change in changes:
            lines += _wrap("- " + change, "  ", "  ")
    lines += [
        "",
        *_wrap(
            "These steps follow Canvas's published source code. They have not been tried in your "
            "Canvas, which may differ, so check one picture and one equation after posting.",
            "",
        ),
        "",
    ]
    return "\n".join(lines)


def build_zip(export: CanvasExport) -> bytes:
    """index.html, the optional paste page, README.txt and images/, in a fixed order with a
    fixed timestamp."""
    entries: list[tuple[str, bytes]] = [("index.html", export.index_html.encode("utf-8"))]
    if export.page_html is not None:
        entries.append(("canvas-page.html", export.page_html.encode("utf-8")))
    entries.append(("README.txt", readme(export).encode("utf-8")))
    entries += [(name, data) for name, data in sorted(export.images.items())]
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3  # unix, whatever machine built it
            archive.writestr(info, data)
    return out.getvalue()
