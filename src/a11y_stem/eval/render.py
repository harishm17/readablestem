"""Turn OpenStax gold data back into the inputs the pipeline sees: PNGs and printed PDFs.

The gold set holds the answers (MathML, alt text, headings). To measure the pipeline we
need the question: what a course PDF would look like. Headless Chromium renders MathML
Core natively, so it can

- draw one gold equation as a tightly cropped PNG (input for `equation_step`), and
- print a whole OpenStax page as a plain PDF (input for `layout`, `heading_step` and
  `run.convert_pdf`, and the page behind the public demo).

The printed page deliberately leaves the answers out of the PDF: images get `alt=""`, the
PDF is untagged, and no MathML annotation is kept, so a pipeline reading the PDF cannot
copy OpenStax's alt text or MathML back out of the file.

Caches (all under `.cache/`, git-ignored): equation PNGs keyed by the SHA-256 of the
MathML, figure images keyed by the SHA-256 of their URL, page PDFs keyed by the SHA-256
of the printable HTML. Playwright is imported lazily so the module (and every offline
test) works without the `browser` dependency group.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Literal
from urllib.parse import urljoin

import defusedxml.ElementTree as SafeET

from a11y_stem.core.mathchars import styled_char
from a11y_stem.gold.openstax import USER_AGENT

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
OPENSTAX_ORIGIN = "https://openstax.org/"
DEFAULT_CACHE = Path(".cache")  # relative to the working directory; the CLI passes paths
# Bump when the printable layout or the equation page changes, so caches miss.
RENDER_VERSION = "r2"  # r2: mathvariant drawn as Unicode math letters
Paper = Literal["Letter", "A4"]


class RenderError(RuntimeError):
    """Input that cannot be rendered (bad MathML, page XHTML missing, image refused)."""


# --------------------------------------------------------------------------- MathML

# Presentation MathML only. Content MathML and annotations are dropped: Chromium does not
# draw them, and in a printed page they would only carry the answer key along.
MATHML_ELEMENTS = {
    "math", "mrow", "mi", "mn", "mo", "mtext", "ms", "mspace", "msub", "msup", "msubsup",
    "munder", "mover", "munderover", "mfrac", "msqrt", "mroot", "mstyle", "merror",
    "mpadded", "mphantom", "mfenced", "menclose", "mtable", "mtr", "mtd", "mlabeledtr",
    "mmultiscripts", "mprescripts", "none", "semantics", "maligngroup", "malignmark",
}  # fmt: skip
MATHML_ATTRS = {
    "display", "mathvariant", "mathsize", "mathcolor", "displaystyle", "scriptlevel",
    "linethickness", "notation", "fence", "separator", "stretchy", "symmetric", "largeop",
    "movablelimits", "accent", "accentunder", "form", "lspace", "rspace", "width",
    "height", "depth", "columnalign", "rowalign", "columnspacing", "rowspacing",
    "columnlines", "rowlines", "frame", "rowspan", "columnspan", "open", "close",
    "separators", "align", "minsize", "maxsize", "voffset",
}  # fmt: skip


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _clean_math_tree(elem: ET.Element) -> ET.Element | None:
    tag = _local(elem.tag)
    if tag not in MATHML_ELEMENTS:
        return None
    if tag == "semantics":  # render the presentation child only
        for child in elem:
            if _local(child.tag) in MATHML_ELEMENTS:
                return _clean_math_tree(child)
        return None
    attrs = {k: v for k, v in elem.attrib.items() if not k.startswith("{") and k in MATHML_ATTRS}
    out = ET.Element(tag, attrs)
    out.text = elem.text
    for child in elem:
        cleaned = _clean_math_tree(child)
        if cleaned is not None:
            cleaned.tail = child.tail
            out.append(cleaned)
        elif child.tail:  # keep text that followed a dropped element
            if len(out):
                out[-1].tail = (out[-1].tail or "") + child.tail
            else:
                out.text = (out.text or "") + child.tail
    return out


# MathML Core (what Chromium implements) only honours mathvariant="normal". OpenStax marks
# every vector as <mstyle mathvariant="bold">, which Chromium would draw as plain italic,
# so the model would be scored against a style it was never shown. Resolve mathvariant
# (inherited, as MathML specifies) into Unicode mathematical alphanumerics instead: bold
# d becomes U+1D41D, which every math font draws bold and the scorer maps back to
# mathvariant="bold".
_TOKEN_TAGS = {"mi", "mn", "mo", "mtext", "ms"}


def _apply_mathvariant(elem: ET.Element, inherited: str | None = None) -> None:
    """Push mathvariant down to tokens and rewrite their text as styled code points."""
    own = elem.attrib.pop("mathvariant", None)
    variant = own or inherited
    tag = _local(elem.tag)
    if tag in _TOKEN_TAGS:
        if variant == "normal" and tag == "mi":
            elem.set("mathvariant", "normal")  # the one value MathML Core supports
        elif variant and variant != "normal" and elem.text:
            elem.text = "".join(styled_char(ch, variant) for ch in elem.text)
        return
    for child in elem:
        _apply_mathvariant(child, variant)


def clean_math_element(elem: ET.Element) -> ET.Element:
    """Allowlisted copy of a parsed <math> tree: MathML elements and styling attributes only.

    Scripts, links, event handlers and foreign markup cannot survive this, so a MathML
    string from anywhere (gold, or a model prediction) is safe to put in a page.
    `mathvariant` is resolved into Unicode math letters so Chromium draws it.
    """
    if _local(elem.tag) != "math":
        raise RenderError("not a <math> element")
    cleaned = _clean_math_tree(elem)
    if cleaned is None:  # pragma: no cover - <math> is always allowed
        raise RenderError("empty MathML")
    cleaned.tail = None
    _apply_mathvariant(cleaned)
    return cleaned


def clean_mathml(mathml: str) -> str:
    """Parse MathML safely (defusedxml) and re-serialise the allowlisted tree."""
    try:
        root = SafeET.fromstring(mathml.strip())
    except ET.ParseError as exc:
        raise RenderError("MathML is not well-formed XML") from exc
    except Exception as exc:  # defusedxml refuses entity tricks with its own error types
        raise RenderError("MathML refused by the safe parser") from exc
    cleaned = clean_math_element(root)
    cleaned.set("xmlns", MATHML_NS)
    return ET.tostring(cleaned, encoding="unicode")


EQUATION_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>
html, body {{ margin: 0; padding: 0; background: #fff; color: #000; }}
#eq {{ display: inline-block; padding: {pad}px; background: #fff; font-size: 20px;
       font-family: "Liberation Serif", "DejaVu Serif", serif; }}
math {{ font-family: "Noto Sans Math", "STIX Two Math", "Latin Modern Math", math; }}
</style></head><body><div id="eq">{math}</div></body></html>"""


# --------------------------------------------------------------------------- browser


def _is_playwright_error(exc: BaseException) -> bool:
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError:  # pragma: no cover - depends on installed groups
        return False
    return isinstance(exc, PlaywrightError)


INSTALL_HINT = (
    ". The browser is not installed: run `uv run playwright install --with-deps chromium` "
    "and install the fonts (`fonts-noto-core fonts-liberation`; docs/development.md)"
)


def _is_missing_browser(exc: BaseException) -> bool:
    """Playwright's "Executable doesn't exist ... run playwright install" failure. Only this
    is recognised, and the message itself is not repeated (other Playwright messages can carry
    page content)."""
    text = str(exc)
    return "Executable doesn't exist" in text or "playwright install" in text


@contextmanager
def _chromium_errors(action: str) -> Generator[None]:
    """Playwright failures (a timeout, a browser that will not start) as RenderError.

    Callers then handle one error type for "this input could not be built", instead of
    a Playwright timeout escaping as a traceback.
    """
    try:
        yield
    except Exception as exc:
        if _is_playwright_error(exc):
            hint = INSTALL_HINT if _is_missing_browser(exc) else ""
            raise RenderError(f"Chromium could not {action} ({type(exc).__name__}){hint}") from exc
        raise


class ChromiumRenderer:
    """One headless Chromium shared by many renders (a launch costs ~0.5 s).

    The browser starts on the first render, not on construction, so a run whose inputs
    are all cached never launches it. Call `close()` (or use `with`) when done; a closed
    renderer starts again on its next render.

    It holds a sync Playwright session open in the calling thread, and Playwright refuses
    to start a second sync session in a thread that has one. Close it before anything
    else in the thread starts its own (axe-core in `html_checks.axe_checks` does).

    JavaScript is off and the context is offline: every page we render is static and
    self-contained (images are inlined as data: URIs), so nothing can call out.
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None

    def __enter__(self) -> ChromiumRenderer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def started(self) -> bool:
        return self._browser is not None

    def _start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on installed groups
            raise RenderError(
                "Playwright is not installed: uv sync --group browser && "
                "uv run playwright install chromium"
            ) from exc
        with _chromium_errors("start"):
            self._playwright = sync_playwright().start()
            try:
                self._browser = self._playwright.chromium.launch()
            except Exception:
                self._playwright.stop()
                self._playwright = None
                raise

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = self._playwright = None

    def _context(self, scale: float = 1) -> Any:
        if self._browser is None:
            self._start()
        with _chromium_errors("open a page"):
            return self._browser.new_context(
                java_script_enabled=False, offline=True, device_scale_factor=scale
            )

    def equation_png(self, mathml: str, *, scale: int = 2, pad_px: int = 4) -> bytes:
        page_html = EQUATION_PAGE.format(pad=pad_px, math=clean_mathml(mathml))
        context = self._context(scale)
        try:
            with _chromium_errors("render the equation"):
                page = context.new_page()
                page.set_content(page_html, wait_until="load")
                return page.locator("#eq").screenshot(animations="disabled", caret="hide")
        finally:
            context.close()

    def equation_pdf(self, mathml: str, *, pad_px: int = 4) -> bytes:
        """The equation page printed to PDF: what a PDF's text layer holds for it."""
        return self.pdf(EQUATION_PAGE.format(pad=pad_px, math=clean_mathml(mathml)))

    def pdf(self, page_html: str, *, paper: Paper = "Letter", footer_html: str = "") -> bytes:
        context = self._context()
        try:
            with _chromium_errors("print the page"):
                page = context.new_page()
                page.set_content(page_html, wait_until="load")
                page.emulate_media(media="print")
                return page.pdf(
                    format=paper,
                    print_background=True,
                    margin={"top": "18mm", "bottom": "20mm", "left": "18mm", "right": "18mm"},
                    display_header_footer=bool(footer_html),
                    header_template="<span></span>",
                    footer_template=footer_html or "<span></span>",
                    tagged=False,  # an untagged PDF, like most course PDFs (and no alt leak)
                    outline=False,
                )
        finally:
            context.close()


def render_equation_png(
    mathml: str, *, scale: int = 2, renderer: ChromiumRenderer | None = None
) -> bytes:
    """PNG of one equation: tight crop of the <math> box, white background, 4 px padding.

    `scale` is the device pixel ratio (2 gives ~40 px per em, close to a 200 dpi crop of
    a printed equation). Pass an open `renderer` to reuse one browser for many calls.
    """
    if renderer is not None:
        return renderer.equation_png(mathml, scale=scale)
    with ChromiumRenderer() as own:
        return own.equation_png(mathml, scale=scale)


def _sha256(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def render_equations(
    gold_records: Iterable[dict],
    cache_dir: Path,
    *,
    scale: int = 2,
    renderer: ChromiumRenderer | None = None,
) -> dict[str, bytes]:
    """Gold record id -> PNG for every equation record, rendering only cache misses.

    The cache key is the SHA-256 of the MathML (display mode is part of the MathML), plus
    the scale and RENDER_VERSION in the file name. Chromium is only launched on a miss.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, bytes] = {}
    misses: list[tuple[dict, Path]] = []
    for record in gold_records:
        if record.get("kind") != "equation":
            continue
        path = cache_dir / f"{_sha256(record['mathml'])}-{scale}x-{RENDER_VERSION}.png"
        if path.exists():
            out[record["id"]] = path.read_bytes()
        else:
            misses.append((record, path))
    if not misses:
        return out

    def render_all(active: ChromiumRenderer) -> None:
        for record, path in misses:
            png = active.equation_png(record["mathml"], scale=scale)
            path.write_bytes(png)
            out[record["id"]] = png

    if renderer is not None:
        render_all(renderer)
    else:
        with ChromiumRenderer() as own:
            render_all(own)
    return out


def pdf_text(pdf: bytes) -> str:
    """A PDF's text layer as one line: reading order, control characters dropped and
    whitespace collapsed, the way `layout` cleans the text it puts on an element."""
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = " ".join(page.get_text("text", sort=True) for page in doc)
    text = "".join(ch for ch in text if ch.isspace() or unicodedata.category(ch) != "Cc")
    return " ".join(text.split())


def render_equation_text(
    mathml: str, cache_dir: Path, *, renderer: ChromiumRenderer | None = None
) -> str | None:
    """The text layer a printed PDF has for this equation (None when it has none).

    In a real document `layout` reads it from the PDF and `equation_step` cross-checks
    the model's transcription against it, so the equations eval must pass it too.
    Chromium prints MathML as positioned glyphs, so the text comes out the way the
    document eval's printed pages hold it: math-italic letters (U+1D400 block), and
    fractions flattened out of order (numerator, then the left-hand side, then the
    denominator). Cached as text under the MathML's SHA-256.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_sha256(mathml)}-text-{RENDER_VERSION}.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        if renderer is not None:
            pdf = renderer.equation_pdf(mathml)
        else:
            with ChromiumRenderer() as own:
                pdf = own.equation_pdf(mathml)
        text = pdf_text(pdf)
        path.write_text(text, encoding="utf-8")
    return text or None


# --------------------------------------------------------------------------- images


def png_bytes(data: bytes, *, max_side_px: int = 2048) -> bytes:
    """Any raster PyMuPDF reads (JPEG, PNG, GIF...) -> PNG, long side capped, no alpha.

    The vision steps take PNG (`VisionRequest.images`), and the cap matches what
    `layout.crop` sends, so a figure costs the same in the eval as in a real document.
    """
    import pymupdf

    try:
        pix = pymupdf.Pixmap(data)
    except Exception as exc:  # PyMuPDF raises its own types for unreadable images
        raise RenderError("image could not be decoded") from exc
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):  # CMYK and friends -> RGB
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    longest = max(pix.width, pix.height)
    if longest > max_side_px:
        factor = longest / max_side_px
        pix = pymupdf.Pixmap(pix, int(pix.width / factor), int(pix.height / factor), None)
    return pix.tobytes("png")


Getter = Callable[[str], bytes]


def _http_get(url: str, timeout: float = 30.0, retries: int = 2) -> bytes:
    """GET with the gold fetcher's user agent; retries timeouts and 5xx/429 with backoff."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(2**attempt)
    name = type(last).__name__ if last else "unknown"
    raise RenderError(f"image download failed ({name})") from last


@dataclass
class ImageCache:
    """OpenStax figure images on disk, keyed by the SHA-256 of their URL.

    Only https://openstax.org/ URLs are fetched: gold records point there, and refusing
    anything else means a malformed record cannot make the eval fetch arbitrary hosts.
    """

    cache_dir: Path
    get: Getter = _http_get
    downloads: int = 0

    def raw(self, url: str) -> bytes:
        if not url.startswith(OPENSTAX_ORIGIN):
            raise RenderError("only openstax.org images are fetched")
        path = self.cache_dir / _sha256(url)
        if path.exists():
            return path.read_bytes()
        data = self.get(url)
        self.downloads += 1
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def png(self, url: str) -> bytes:
        return png_bytes(self.raw(url))

    def data_uri(self, url: str) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png(url)).decode("ascii")


# ----------------------------------------------------------------------------- pages


@dataclass
class PageSource:
    """One OpenStax page: its archive XHTML plus what the attribution needs."""

    book_slug: str
    book_title: str
    page_slug: str
    page_title: str
    page_url: str
    content_url: str
    license_name: str
    license_url: str
    xhtml: str
    authors: list[str] = field(default_factory=list)

    @property
    def license_short(self) -> str:
        match = re.search(r"/licenses/([a-z-]+)/(\d+(?:\.\d+)?)", self.license_url)
        return f"CC {match.group(1).upper()} {match.group(2)}" if match else self.license_name


def page_source_from_records(
    records: list[dict], fetcher: Any, *, authors: list[str] | None = None
) -> PageSource:
    """Page metadata from its gold records; the XHTML from the archive (normally cached).

    `fetcher` is a `gold.openstax.HttpFetcher` with a cache dir: `gold fetch` already
    stored every gold page's archive JSON there, so this is offline after a fetch.
    """
    if not records:
        raise RenderError("no gold records for this page")
    first = records[0]
    try:
        payload = fetcher.get_json(first["content_url"])
    except Exception as exc:  # GoldError, network errors
        raise RenderError(
            f"page XHTML for {first['page_slug']} is not cached and could not be fetched"
        ) from exc
    page_url = first["source_url"].split("#", 1)[0]
    return PageSource(
        book_slug=first["book_slug"],
        book_title=first["book_title"],
        page_slug=first["page_slug"],
        page_title=first["page_title"],
        page_url=page_url,
        content_url=first["content_url"],
        license_name=first["license_name"],
        license_url=first["license_url"],
        xhtml=payload["content"],
        authors=list(authors or []),
    )


def page_source_from_openstax(book_slug: str, page_slug: str, fetcher: Any) -> PageSource:
    """Look a page up live (for pages outside the gold set, e.g. the demo).

    Applies the same gates as `gold fetch`: CC BY in both the archive and the CMS, and a
    live book. The demo republishes the page, so this is a license requirement.
    """
    from a11y_stem.gold import openstax

    book = openstax.resolve_book(book_slug, fetcher)
    openstax.check_public_license(book)
    openstax.check_book_state(book)
    (page,) = openstax.select_pages(openstax.iter_pages(book.tree), 1, slugs=[page_slug])
    payload = fetcher.get_json(book.content_url(page.uuid))
    return PageSource(
        book_slug=book.slug,
        book_title=book.title,
        page_slug=page.slug,
        page_title=page.title,
        page_url=book.page_url(page.slug),
        content_url=book.content_url(page.uuid),
        license_name=book.license_name or "",
        license_url=book.license_url or "",
        xhtml=payload["content"],
        authors=list(book.authors),
    )


def book_authors(gold_dir: Path, book_slug: str) -> list[str]:
    """Senior authors from the gold set's `_book.json` (empty if not there)."""
    path = gold_dir / "openstax" / book_slug / "_book.json"
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("authors") or [])


# Printable HTML: an allowlist transform of the archive XHTML.
BLOCK_KEEP = {"p", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead",
              "tbody", "tfoot", "tr", "th", "td", "caption", "blockquote", "pre", "dl",
              "dt", "dd"}  # fmt: skip
INLINE_KEEP = {"em", "strong", "b", "i", "u", "sub", "sup", "code", "br", "small"}
DROP = {"head", "script", "style", "iframe", "object", "embed", "video", "audio", "nav",
        "button", "input", "form", "select", "textarea", "noscript", "template"}  # fmt: skip
KEEP_ATTRS = {"colspan", "rowspan", "type", "start", "scope"}
BOX_TYPES = {"note", "example", "exercise", "injected-exercise", "solution", "problem",
             "commentary"}  # fmt: skip

PRINT_CSS = """
@page { margin: 18mm 18mm 20mm 18mm; }
html { background: #fff; color: #000; }
body { font-family: "Liberation Serif", "DejaVu Serif", serif; font-size: 11pt;
       line-height: 1.4; margin: 0; }
math { font-family: "Noto Sans Math", "STIX Two Math", "Latin Modern Math", math; }
h1 { font-size: 22pt; margin: 0 0 10pt; }
h2 { font-size: 18pt; margin: 14pt 0 8pt; }
h3 { font-size: 14pt; margin: 12pt 0 6pt; }
h4 { font-size: 12pt; margin: 10pt 0 4pt; }
h5, h6 { font-size: 11pt; margin: 8pt 0 4pt; }
h1, h2, h3, h4, h5, h6 { font-family: "Liberation Sans", "DejaVu Sans", sans-serif;
       font-weight: bold; break-after: avoid; }
p { margin: 0 0 6pt; }
.box { border: 0.75pt solid #555; padding: 4pt 8pt; margin: 8pt 0; break-inside: avoid; }
.equation { display: flex; align-items: center; justify-content: center; margin: 6pt 0; }
.equation .number { margin-left: 2em; }
figure { margin: 10pt 0; text-align: center; break-inside: avoid; }
figure img { max-width: 100%; max-height: 9cm; }
figcaption { text-align: left; font-size: 10pt; margin-top: 4pt; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 0.5pt solid #777; padding: 2pt 6pt; vertical-align: top; }
.label { font-weight: bold; }
footer { margin-top: 18pt; border-top: 0.75pt solid #555; padding-top: 6pt;
         font-size: 9pt; }
"""

# Table styles for the tables eval (`table_style`): the same page with its tables ruled
# three common ways, a full grid, booktabs (rules above, below and under the header row
# only) and no rules at all, so a detector is not measured on grids alone. Added after
# PRINT_CSS; None keeps PRINT_CSS's light grid. The CSS is table-bench's (2026-10-08),
# so its prints and gold pages can be reproduced.
TableStyle = Literal["grid", "booktabs", "norules"]
TABLE_STYLE_CSS: dict[str, str] = {
    "grid": "table { break-inside: avoid; }",
    "booktabs": (
        "table { break-inside: avoid; border-top: 1.2pt solid #000; "
        "border-bottom: 1.2pt solid #000; } "
        "th, td { border: none; padding: 2pt 8pt; } "
        "table > :first-child > tr:first-child > * { border-bottom: 0.6pt solid #000; }"
    ),
    "norules": (
        "table { break-inside: avoid; } th, td { border: none !important; padding: 2pt 10pt; }"
    ),
}

ImageResolver = Callable[[str], str | None]  # absolute image URL -> src (or None to omit)


def _append_text(parent: ET.Element, text: str | None) -> None:
    if not text:
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _classes(elem: ET.Element) -> set[str]:
    return set((elem.get("class") or "").split())


class _Printer:
    """Walks archive XHTML and emits a plain printable tree (see `printable_html`)."""

    def __init__(self, content_url: str, resolve_image: ImageResolver) -> None:
        self.content_url = content_url
        self.resolve_image = resolve_image

    def children(self, src: ET.Element, out: ET.Element) -> None:
        _append_text(out, src.text)
        for child in src:
            self.node(child, out)
            _append_text(out, child.tail)

    def node(self, src: ET.Element, out: ET.Element) -> None:
        tag = _local(src.tag)
        if tag == "math":
            math = clean_math_element(src)
            out.append(math)
            return
        if tag in DROP or not tag:
            return
        if tag == "img":
            src_url = src.get("src")
            resolved = self.resolve_image(urljoin(self.content_url, src_url)) if src_url else None
            if resolved:
                # alt="" on purpose: the PDF must not carry OpenStax's alt text (the answer).
                ET.SubElement(out, "img", {"src": resolved, "alt": ""})
            return
        data_type = src.get("data-type") or ""
        classes = _classes(src)
        if tag == "figure" or (tag == "div" and "os-figure" in classes):
            figure = ET.SubElement(out, "figure")
            self.figure_body(src, figure)
            return
        if "os-caption-container" in classes or tag == "figcaption":
            caption = ET.SubElement(out, "figcaption")
            self.children(src, caption)
            return
        if data_type == "equation":
            box = ET.SubElement(out, "div", {"class": "equation"})
            self.children(src, box)
            return
        if "os-equation-number" in classes:
            number = ET.SubElement(out, "span", {"class": "number"})
            self.children(src, number)
            return
        if data_type == "title" and tag in {"div", "span"}:
            label = ET.SubElement(out, "p", {"class": "label"})
            self.children(src, label)
            return
        if tag in BLOCK_KEEP or tag in INLINE_KEEP:
            attrs = {k: v for k, v in src.attrib.items() if k in KEEP_ATTRS}
            new = ET.SubElement(out, tag, attrs)
            self.children(src, new)
            return
        if tag in {"div", "section", "aside", "article", "header", "footer"}:
            attrs = {"class": "box"} if data_type in BOX_TYPES else {}
            new = ET.SubElement(out, "div", attrs)
            self.children(src, new)
            return
        # span, a, q, cite, and anything unknown: keep the text, drop the element.
        self.children(src, out)

    def figure_body(self, src: ET.Element, figure: ET.Element) -> None:
        """os-figure wrappers and <figure> both become one <figure>; captions inside it."""
        _append_text(figure, src.text)
        for child in src:
            tag = _local(child.tag)
            if tag == "figure":
                self.figure_body(child, figure)
            else:
                self.node(child, figure)
            _append_text(figure, child.tail)


def _content_root(xhtml: str) -> ET.Element:
    try:
        root = SafeET.fromstring(xhtml)
    except Exception as exc:
        raise RenderError("page XHTML could not be parsed") from exc
    for elem in root.iter():
        if elem.get("data-type") == "page":
            return elem
    body = next((e for e in root.iter() if _local(e.tag) == "body"), None)
    if body is None:
        raise RenderError("page XHTML has no body")
    return body


# Equation context: what `run.convert_pdf` sends with each equation as context_text.
CONTEXT_CHARS = 600  # same window as pipeline.run.CONTEXT_CHARS (about half each side)
TEXT_BLOCK_TAGS = {"p", "li", "dt", "dd", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
_THIS_EQUATION = ""  # private-use stand-in while whitespace is collapsed


def _is_text_block(elem: ET.Element) -> bool:
    """Elements a PDF layout reads as paragraphs or headings (box titles print as text)."""
    tag = _local(elem.tag)
    return tag in TEXT_BLOCK_TAGS or (tag in {"div", "span"} and elem.get("data-type") == "title")


def _flat_text(elem: ET.Element, this: ET.Element | None = None) -> str:
    """Text of `elem`, whitespace collapsed, each <math> as "[equation]" (`this` one as
    the stand-in marker), like the paragraph text convert_pdf shows a model."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        tag = _local(node.tag)
        if tag == "math":
            parts.append(_THIS_EQUATION if node is this else "[equation]")
            return
        if tag in DROP:
            return
        parts.append(node.text or "")
        for child in node:
            walk(child)
            parts.append(child.tail or "")

    walk(elem)
    return " ".join("".join(parts).split())


def _token_text(elem: ET.Element) -> str:
    return "".join("".join(elem.itertext()).split())


def math_tokens(mathml: str) -> str:
    """All text in a MathML string, whitespace removed: identifies one equation."""
    try:
        return _token_text(SafeET.fromstring(mathml))
    except Exception as exc:
        raise RenderError("MathML could not be parsed") from exc


def _head(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "... " + text[-limit:].lstrip()


def equation_contexts(xhtml: str) -> list[tuple[str, str | None]]:
    """(token text, context) for each <math> on a page, in document order.

    Document order is the gold set's `index_on_page`; the token text lets a caller check
    that it has the right equation. The context is what `run.convert_pdf` would send
    with the equation (`pipeline.run.context_texts`), read from the page source instead
    of a PDF layout: an inline equation inside a text block gets up to 300 characters of
    that block either side of "[this equation]"; a display equation, or an inline one
    alone in its block, gets the end of the previous text block and the start of the
    next, one per line around "[this equation]". Other equations show as "[equation]".
    None when there is no text around it.
    """
    try:
        root = SafeET.fromstring(xhtml)
    except Exception as exc:
        raise RenderError("page XHTML could not be parsed") from exc
    parents = {child: parent for parent in root.iter() for child in parent}
    order = {elem: i for i, elem in enumerate(root.iter())}

    def ancestors(elem: ET.Element) -> Iterator[ET.Element]:
        node = parents.get(elem)
        while node is not None:
            yield node
            node = parents.get(node)

    # Innermost text blocks only: a <li> holding <p>s reads as those paragraphs.
    holds_block: set[ET.Element] = set()
    for elem in root.iter():
        if _is_text_block(elem):
            for node in ancestors(elem):
                if node in holds_block:
                    break
                holds_block.add(node)
    blocks = [
        (order[elem], elem, text)
        for elem in root.iter()
        if _is_text_block(elem) and elem not in holds_block and (text := _flat_text(elem))
    ]
    half = CONTEXT_CHARS // 2

    def context(math: ET.Element) -> str | None:
        above = list(ancestors(math))
        boxed = any(node.get("data-type") == "equation" for node in above)
        display = math.get("display") or ("block" if boxed else "inline")
        block = next((node for node in above if _is_text_block(node)), None)
        if display == "inline" and block is not None:
            before, _, after = _flat_text(block, math).partition(_THIS_EQUATION)
            if before.strip() or after.strip():
                return f"{_tail(before, half)}[this equation]{_head(after, half)}".strip()
        enclosing = set(above)
        at = order[math]
        previous = next(
            (text for o, elem, text in reversed(blocks) if o < at and elem not in enclosing),
            None,
        )
        following = next((text for o, _, text in blocks if o > at), None)
        if previous is None and following is None:
            return None
        parts = [_tail(previous, half)] if previous else []
        parts.append("[this equation]")
        parts += [_head(following, half)] if following else []
        return "\n".join(parts)

    return [(_token_text(m), context(m)) for m in root.iter() if _local(m.tag) == "math"]


def attribution_lines(page: PageSource) -> list[tuple[str, str | None]]:
    """(text, link) pairs for the footer: what OpenStax's CC BY terms ask for."""
    authors = ", ".join(page.authors) if page.authors else "OpenStax authors"
    return [
        (f"“{page.page_title}” from {page.book_title} by {authors}, OpenStax.", None),
        (f"Licensed under {page.license_name} ({page.license_short}).", page.license_url),
        ("Original page:", page.page_url),
        ("Access for free at openstax.org.", None),
        (
            "Changes: reformatted as a printable page for evaluating a11y-stem; image alt "
            "text left out of this print; no wording edited.",
            None,
        ),
        ("a11y-stem is not affiliated with or endorsed by OpenStax.", None),
    ]


def printable_html(
    page: PageSource, resolve_image: ImageResolver, *, table_style: TableStyle | None = None
) -> str:
    """A simple printable HTML version of an OpenStax page, with an attribution footer.

    Keeps headings (at their published levels), paragraphs, lists, tables, MathML,
    figures with captions, and note/example boxes. Drops scripts, styles, iframes and
    links (their text stays). Pure function: images come from `resolve_image`.
    `table_style` rules the tables one of the `TABLE_STYLE_CSS` ways (tables eval).
    """
    printer = _Printer(page.content_url, resolve_image)
    doc = ET.Element("html", {"lang": "en"})
    head = ET.SubElement(doc, "head")
    ET.SubElement(head, "meta", {"charset": "utf-8"})
    ET.SubElement(head, "title").text = page.page_title
    extra = TABLE_STYLE_CSS[table_style] + "\n" if table_style else ""
    ET.SubElement(head, "style").text = PRINT_CSS + extra
    body = ET.SubElement(doc, "body")
    main = ET.SubElement(body, "main")
    printer.children(_content_root(page.xhtml), main)
    footer = ET.SubElement(body, "footer")
    for text, link in attribution_lines(page):
        line = ET.SubElement(footer, "p")
        line.text = text
        if link:
            line.text += " "
            ET.SubElement(line, "a", {"href": link}).text = link
    return "<!doctype html>\n" + ET.tostring(doc, encoding="unicode", method="html")


def footer_template(page: PageSource) -> str:
    """Running footer on every printed page (OpenStax asks print copies to carry it)."""
    text = html.escape(
        f"Access for free at openstax.org. {page.book_title} (OpenStax), {page.license_short}."
    )
    return (
        '<div style="font-size:8px;width:100%;text-align:center;color:#333;">'
        f'{text} Page <span class="pageNumber"></span> of <span class="totalPages"></span>'
        "</div>"
    )


def render_page_pdf(
    page: PageSource | Sequence[dict],
    out_path: Path | None = None,
    *,
    images: ImageCache | None = None,
    renderer: ChromiumRenderer | None = None,
    paper: Paper = "Letter",
    cache_dir: Path | None = None,
    fetcher: Any = None,
    authors: list[str] | None = None,
    table_style: TableStyle | None = None,
) -> bytes:
    """Print a page to PDF (headings, paragraphs, MathML, figures, captions, attribution).

    `page` is a `PageSource`, or one page's gold records, whose archive XHTML is then
    read through `fetcher` (default: the `gold fetch` cache in .cache/openstax); pass
    `authors` for the attribution in that case. Images are downloaded into `images`
    (default .cache/eval/images) once and inlined. With `cache_dir`, the PDF is cached
    under the SHA-256 of the printable HTML + paper size, so re-running an eval does not
    re-print unchanged pages. Writes `out_path` when given; returns the bytes.
    `table_style` (grid, booktabs, norules) rules the tables for the tables eval.
    """
    if not isinstance(page, PageSource):
        if fetcher is None:
            from a11y_stem.gold.openstax import HttpFetcher

            fetcher = HttpFetcher(cache_dir=DEFAULT_CACHE / "openstax")
        page = page_source_from_records(list(page), fetcher, authors=authors)
    images = images or ImageCache(DEFAULT_CACHE / "eval" / "images")
    page_html = printable_html(page, images.data_uri, table_style=table_style)
    footer = footer_template(page)
    cached: Path | None = None
    if cache_dir is not None:
        key = _sha256(f"{RENDER_VERSION}\n{paper}\n{footer}\n{page_html}")
        cached = cache_dir / f"{page.book_slug}-{page.page_slug}-{key[:16]}.pdf"
    if cached is not None and cached.exists():
        pdf = cached.read_bytes()
    else:
        if renderer is not None:
            pdf = renderer.pdf(page_html, paper=paper, footer_html=footer)
        else:
            with ChromiumRenderer() as own:
                pdf = own.pdf(page_html, paper=paper, footer_html=footer)
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(pdf)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pdf)
    return pdf


# ------------------------------------------------------------------------------ demo

_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


# Marks a demo built with the offline client; `ops.launch` refuses to call it finished.
PLACEHOLDER_ATTR = 'data-a11y-stem-demo="placeholder"'


def demo_note_html(
    page: PageSource, *, date: str, source_url: str | None = None, placeholder: bool = False
) -> str:
    """Short accessible note placed above the converted page in the public demo.

    It links back to the app and, when `source_url` is given, to the source code (the
    AGPL notice every page of the service carries). A `placeholder` demo (built with the
    offline client) says so in a marked paragraph.
    """
    esc = html.escape
    authors = ", ".join(page.authors) if page.authors else "OpenStax authors"
    fake = (
        f"<p {PLACEHOLDER_ATTR}><strong>Placeholder output.</strong> This demo was built "
        "with the offline test client: the equations and descriptions below are "
        "placeholders, not real model output.</p>\n"
        if placeholder
        else ""
    )
    source = (
        f' a11y-stem is free, open-source software; its complete <a href="'
        f'{esc(source_url, quote=True)}">source code is on GitHub</a> (it uses PyMuPDF under the '
        "GNU Affero General Public License v3)."
        if source_url
        else ""
    )
    # Descriptive link text (WCAG 2.4.4) instead of raw URLs, and overflow-wrap so nothing
    # in the note can widen the page at 320 CSS px (WCAG 1.4.10).
    return (
        '<aside aria-label="About this demo" style="border:2px solid #1a4f8b;'
        "padding:0.75em 1em;margin:0 0 1.5em;background:#fff;color:#000;"
        'overflow-wrap:anywhere;">\n'
        + fake
        + "<p><strong>Precomputed demo.</strong> This is a11y-stem's output for a PDF print "
        f"of “{esc(page.page_title)}” from <cite>{esc(page.book_title)}</cite> by "
        f"{esc(authors)} (OpenStax), converted on {esc(date)}. Equations, headings and "
        "image descriptions below are the tool's proposals; the ones marked for review "
        "have not been checked by a person. a11y-stem assists with accessibility work; "
        "it does not replace review.</p>\n"
        f'<p>Read <a href="{esc(page.page_url, quote=True)}">the original page on '
        "openstax.org</a>. Licensed under "
        f'<a href="{esc(page.license_url, quote=True)}">{esc(page.license_name)}</a> '
        f"({esc(page.license_short)}). Access for free at openstax.org. a11y-stem is not "
        "affiliated with or endorsed by OpenStax.</p>\n"
        f'<p><a href="/">Back to a11y-stem</a>.{source}</p>\n'
        "</aside>\n"
    )


def wrap_demo_html(
    converted_html: str,
    page: PageSource,
    *,
    date: str,
    source_url: str | None = None,
    placeholder: bool = False,
) -> str:
    """Insert the demo note at the start of <body>; wrap a bare fragment in a document."""
    note = demo_note_html(page, date=date, source_url=source_url, placeholder=placeholder)
    match = _BODY_OPEN.search(converted_html)
    if match:
        return converted_html[: match.end()] + "\n" + note + converted_html[match.end() :]
    title = html.escape(f"Demo: {page.page_title}")
    return (
        f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{title}'
        f"</title></head><body>\n{note}{converted_html}\n</body></html>\n"
    )
