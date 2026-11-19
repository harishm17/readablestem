"""The stylesheet of the student file (the HTML page a converted document becomes), in one place.

The sheet is a set of named parts and the builds made from them:

- **Screen** (`screen`): what the page carries in its `<style>`. The core is the parts that
  every page has (`CORE_PARTS`, in this order: base, headings, equation, lists, hidden text,
  figure, flag and notice, about, skip link, then the narrow-screen, print and dark blocks). The
  feature parts (`CODE`, `DEFINITIONS`, `IMAGE_PAGE`, `TABLE`, `CONTENTS`, `PAGE_MARKER`,
  `FOOTNOTE`, `ASIDE`, `FIGURE_DESCRIPTION`, `SCRIPT_STACK`, `DELETED`, `colour`, `print_fit`) are
  added only to a document that uses them (`assemble._Renderer.add_style`, once per name) and
  follow the core sorted by that name, so a document without a feature is written exactly as
  before.
- **EPUB** (`epub`): the same text without the page's colors, font family, base size and
  column, and without the dark and print blocks, so the reading system's own theme applies
  (F-456, F-457). The EPUB writer reads the sheet out of the page it is given and cuts it here.
- **Canvas** (`CANVAS_PROPERTIES`, `CANVAS_REWRITES`, `CANVAS_BASELINE_TAGS`, `CANVAS_MATH_RULE`):
  what the Canvas inliner may carry from the sheet into `style` attributes. Canvas has no style
  block, so the inliner parses the same text and writes the rules it can keep.

A rule that is in the sheet is written once, here. The parts keep the order and wording the
sheet had when it was spread over `assemble`, `assemble_tables`, `navigation`,
`figure_details` and `inline_text`, so a rule added to one of them on another branch is
re-applied to the part of the same name.
"""

from __future__ import annotations

import re

from readablestem.pipeline import contrast, math_font
from readablestem.pipeline.navigation import CONTENTS_LABEL, WIDE

# ---------------------------------------------------------------------------------------
# Screen: the core, in the order the page has always had.

BASE = """\
:root { color-scheme: light dark; }
body { margin: 0; background: #ffffff; color: #1b1b1b;
  font: 1.125rem/1.6 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
main { max-width: 42rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; overflow-wrap: break-word; }
p { margin: 0 0 1em; }
img { max-width: 100%; height: auto; }
"""
# The heading scale (F-445), in em of the 18 px text: a step at every level down to h4, and
# h4 larger than the text. h5 and h6 stay at the text's size, never under it, and are told apart
# from it and from each other by weight and style: h5 is bold, h6 is bold and italic.
HEADINGS = """\
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.6em 0 0.5em; }
h1 { font-size: 2em; }
h2 { font-size: 1.5em; }
h3 { font-size: 1.3em; }
h4 { font-size: 1.15em; }
h5 { font-size: 1em; }
h6 { font-size: 1em; font-style: italic; }
"""
EQUATION = """\
.equation { margin: 1em 0; padding: 0.25em 0; text-align: center; position: relative;
  overflow-x: auto; overflow-y: hidden; }
.equation-scroll { display: inline-block; max-width: 100%; vertical-align: middle;
  overflow-x: auto; overflow-y: hidden; }
.equation-labelled { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25em 1em; }
.equation-labelled > math { flex: 1 0 auto; }
.equation-label { flex: none; margin-left: auto; }
math { font-size: 1.1em; font-family: @MATH_FONT@; }
math[display="block"] { margin: 0 auto; }
math[intent] mi { text-transform: none; }
""".replace("@MATH_FONT@", math_font.MATH_FONT_STACK)
LISTS = """\
ul, ol { margin: 0 0 1em; padding-left: 1.75em; }
li > ul, li > ol { margin: 0.25em 0 0; }
.printed-markers { list-style: none; }
.printed-markers > li { text-indent: -1.75em; padding-left: 1.75em; }
.printed-markers > li > * { text-indent: 0; }
.list-marker { display: inline-block; min-width: 1.5em; text-indent: 0; }
"""
HIDDEN = """\
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip-path: inset(50%); white-space: nowrap; }
"""
FIGURE = """\
figure { margin: 1.5em 0; }
figure img { display: block; }
figcaption { margin-top: 0.75em; font-size: 0.9em; }
"""
# A picture kept for what could not be read as text, a notice, and the tool's note beside them.
FLAG = """\
.flagged { border: 3px dashed #8a3c00; border-radius: 4px; padding: 0.75rem; margin: 1.5em 0; }
.notice { border: 3px solid #8a3c00; border-radius: 4px; padding: 0.75rem; }
.flagged figure { margin: 0; }
.flagged > img { display: block; margin: 0 auto; }
.flagged-inline { display: inline-block; max-width: 100%; vertical-align: middle;
  line-height: 1.25; border: 2px dashed #8a3c00; padding: 0.1em 0.2em; }
.flagged-inline img { vertical-align: middle; }
.flag-note { margin: 0.75em 0 0; font-size: 0.9em; }
.flag-label { font-weight: 700; }
.original-text { font-family: ui-monospace, Menlo, Consolas, monospace; }
"""
ABOUT = """\
.about { margin: 2.5rem 0 0; padding: 0.5em 0 0; border-top: 2px solid #1b1b1b; }
.about-title { font-size: 1.5em; font-weight: 700; line-height: 1.25; margin: 1.6em 0 0.5em; }
"""
SKIP_LINK = """\
:focus-visible { outline: 3px solid #0b57d0; outline-offset: 2px; }
.skip-link { display: inline-block; margin: 0.5rem; padding: 0.5em 1em; background: #ffffff;
  color: #0b57d0; }
.skip-link:not(:focus) { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
"""
# The three blocks below cut across the components above (a rule of the kept picture, the
# skip link and the page is in each); they stay whole and in this order.
NARROW = """\
/* A kept picture holds what could not be read as text. When the column is narrow, as it is in
   a zoomed window, it keeps its size and scrolls sideways instead of shrinking with the
   column, so its writing stays as large as the text around it. */
@media screen and (max-width: 40em) {
  .flagged { overflow-x: auto; }
  .flagged > img { max-width: none; }
}
"""
PRINT = """\
@media print {
  .flagged { break-inside: avoid; } .skip-link { display: none; }
  /* Nothing is cut on paper without a mark (SW45 rule 6). The sheet is the container. An
     equation too wide even at 70% stays in its box, which clips on paper (letting it out
     would make the browser shrink the whole sheet), and prints a note. */
  main { container: sheet / inline-size; }
  .pw math { font-size: max(0.77em, min(1.1em, 100cqw / var(--pw))); }
  .pw::after { display: none; font-size: 0.8em; font-style: italic;
    content: "This equation is wider than the paper. It is complete on the web page."; }
}
"""
DARK = """\
@media screen and (prefers-color-scheme: dark) {
  body { background: #121212; color: #ececec; }
  .flagged, .notice, .flagged-inline { border-color: #ffa066; }
  .about { border-top-color: #ececec; }
  :focus-visible { outline-color: #8ab4f8; }
  .skip-link { background: #121212; color: #8ab4f8; }
  img { outline: 1px solid #8c8c8c; outline-offset: -1px; }
}
"""
CORE_PARTS = (
    BASE,
    HEADINGS,
    EQUATION,
    LISTS,
    HIDDEN,
    FIGURE,
    FLAG,
    ABOUT,
    SKIP_LINK,
    NARROW,
    PRINT,
    DARK,
)
CORE = "".join(CORE_PARTS)

# ---------------------------------------------------------------------------------------
# Screen: the parts a document gets only when it uses them.

# A code block (`pre`) and a definition list.
CODE = """\
pre { margin: 0 0 1em; padding: 0.75em 1em; background: #f3f3f3; border: 1px solid #c8c8c8;
  border-radius: 4px; overflow-x: auto; }
pre code { font: 0.9rem/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  white-space: pre; }
@media screen and (prefers-color-scheme: dark) {
  pre { background: #1f1f1f; border-color: #767676; }
}
@media print {
  pre, pre code { overflow: visible; white-space: pre-wrap; overflow-wrap: anywhere; }
}
"""
# Words in a fixed-pitch face inside a paragraph (`<code>` not in a `<pre>`), a little smaller
# than the text so the line keeps its height (the rule of `pre code` above is more specific).
INLINE_CODE = """\
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; }
"""
# Highlighted words (`<mark>`, from a highlight annotation or a coloured band behind the words):
# a pale band in the light page and a deep one in the dark page, each at 4.5:1 or more with the
# text on it, and a line under the words (3:1 against the page) so they show where colours are
# replaced, in forced colors, and on paper that prints no backgrounds.
HIGHLIGHT = """\
mark { background: #fff0a0; color: #1b1b1b; padding: 0 0.1em; border-bottom: 2px solid #7a5c00; }
@media screen and (prefers-color-scheme: dark) {
  mark { background: #5c4a00; color: #f5f5f5; border-bottom-color: #ffd34d; }
}
@media (forced-colors: active) {
  mark { background: Mark; color: MarkText; border-bottom-color: CanvasText; }
}
"""
DEFINITIONS = """\
dl { margin: 0 0 1em; }
dt { margin-top: 0.75em; }
dd { margin: 0.1em 0 0 1.5em; }
"""
# The note that opens a page read from a picture of the original.
IMAGE_PAGE = """\
.image-page-note { border: 3px solid #8a3c00; border-radius: 4px; padding: 0.75rem;
  margin: 1.5em 0; }
.image-page-note p { margin: 0 0 0.5em; }
.image-page-note details { margin: 0; }
.image-page-note summary { cursor: pointer; font-weight: 700; padding: 0.25em 0; }
.image-page-note img { display: block; margin: 0.5em auto 0; }
@media screen and (prefers-color-scheme: dark) {
  .image-page-note { border-color: #ffa066; }
}
"""
# A rebuilt table (and only a document that has one).
TABLE = """\
.table-scroll { margin: 1.5em 0; overflow-x: auto; }
table { border-collapse: collapse; }
caption { caption-side: top; text-align: left; font-weight: 700; padding: 0 0 0.4em; }
th, td { border: 1px solid #5c5c5c; padding: 0.3em 0.6em; text-align: left;
  vertical-align: top; }
thead th { border-bottom-width: 3px; }
@media screen and (prefers-color-scheme: dark) {
  th, td { border-color: #a6a6a6; }
}
@media print { .table-scroll { overflow: visible; } th, td { overflow-wrap: anywhere; } }
"""
# Added to a document's stylesheet only when it has a contents list / page markers / notes.
# `border-top`, `margin`, `padding`, `font-size`, `color`, `text-align` and `display` are
# properties Canvas keeps, so the Canvas export carries these rules into `style` attributes.
#
# The list is a closed `<details>` under `WIDE`, so on a phone it is one line below the title.
# From `WIDE` up, and on paper, where the browser can draw a closed `<details>`' content
# (`::details-content`), the content is shown and the summary is hidden: with no control there
# is no state to get wrong, and a screen reader meets the landmark named "Contents" and its
# list, not a "collapsed" button above a list that is open. The words "Contents" are then
# written by the sheet in the summary's place. Where it cannot (older browsers) nothing
# changes: the closed line stays, a real control that works from the keyboard, and on paper
# its summary says to open it first (as a figure description's does).
CONTENTS = f"""\
.toc {{ margin: 1rem auto; max-width: 42rem; padding: 0 1rem; }}
.toc-title {{ font-weight: 700; cursor: pointer; padding: 0.25em 0; }}
.toc ul {{ margin: 0; padding-left: 1.25em; }}
.toc li {{ margin: 0.15em 0; }}
@media (min-width: {WIDE}), print {{
  @supports selector(::details-content) {{
    .toc-details::details-content {{ content-visibility: visible; display: block; }}
    .toc-details > summary {{ display: none; }}
    .toc-details::before {{ content: "{CONTENTS_LABEL}"; display: block; font-weight: 700;
      margin: 0 0 0.25em; }}
  }}
}}
@media print {{
  @supports not selector(::details-content) {{
    .toc-details:not([open]) > summary::after {{
      content: " (open this list before printing)"; font-weight: 400; }}
  }}
}}
"""
PAGE_MARKER = """\
.pagebreak { margin: 1.5em 0 0.5em; padding: 0.25em 0 0; border-top: 1px solid #767676;
  font-size: 0.9em; color: #595959; text-align: right; }
.pagebreak hr { margin: 0; padding: 0; border: 0; height: 0; }
@media screen and (prefers-color-scheme: dark) {
  .pagebreak { border-top-color: #8c8c8c; color: #c4c4c4; }
}
"""
# Text set apart from the body (`<aside>`): a bar at its left edge in the text's own color, so it
# shows on any theme and is not told by color alone (it is also a landmark with a label).
ASIDE = """\
aside { margin: 0 0 1em; padding-left: 1em; border-left: 3px solid currentColor; }
"""
FOOTNOTE = """\
[role="doc-footnote"] { font-size: 0.9em; }
.backlink { margin-left: 0.5em; }
"""
# A figure description. No new colours: the summary is the body text, bold, with the browser's
# own marker. A closed description prints open (F-436): where the browser can draw a closed
# `<details>`' content (`::details-content`) it is shown; where it cannot, the summary says to
# open it first.
FIGURE_DESCRIPTION = """\
.figure-description { margin: -0.75em 0 1.5em; }
.figure-description > summary { cursor: pointer; font-weight: 700; }
.figure-description[open] > summary { margin-bottom: 0.5em; }
@media print {
  .figure-description::details-content { content-visibility: visible; display: block; }
  @supports not selector(::details-content) {
    .figure-description:not([open]) > summary::after {
      content: " (open this description before printing)"; font-weight: 400; }
  }
}
"""
# A lowered and a raised run side by side, written one above the other as they are printed
# (the count and the charge of SO4 2-, the mass number and the atomic number of a nuclide); the
# class names are `inline_text.STACK_CLASS` and `STACK_LOW_FIRST`.
SCRIPT_STACK = (
    ".script-stack { display: inline-flex; flex-direction: column; vertical-align: -0.5em;"
    " font-size: 0.8em; line-height: 1.1; text-align: left; }\n"
    ".script-stack.script-stack-low-first { flex-direction: column-reverse; }\n"
    ".script-stack sup, .script-stack sub { font-size: inherit; vertical-align: baseline;"
    " line-height: inherit; }\n"
)
# Struck text (`inline_text.DELETED_CLASS`): the line through is a text decoration, which
# forced colors keep, in the text's own colour, so it shows on a dark page too; it is drawn
# thicker than a browser's default so it is not lost at small sizes.
DELETED = (
    "del { text-decoration-line: line-through; text-decoration-thickness: 0.1em;"
    " text-decoration-skip-ink: none; }\n"
)


def colour(name: str, rgb: tuple[int, int, int]) -> str:
    """The rules for the class `name` of text printed in `rgb`: the colour at 4.5:1 or more on
    the white page, and, for readers who prefer dark colours, the printed colour lightened as
    far as it needs on the dark page. No colour reaches 4.5:1 on both (that needs a luminance
    of at most 0.18 on one and at least 0.20 on the other), so every class has both rules."""
    shown = contrast.hex_colour(contrast.with_contrast(rgb))
    dark = contrast.hex_colour(contrast.with_contrast(rgb, contrast.DARK_BACKGROUND))
    return (
        f".{name} {{ color: {shown}; }}\n{contrast.DARK_MEDIA} {{ .{name} {{ color: {dark}; }} }}\n"
    )


# An equation estimated at least this wide (em of the maths font) may not fit a sheet: at
# Letter and 150% the text column is about 22 em of it. On paper it is scaled to fit the
# column down to 70% of its size; wider than that it keeps 70% and the page prints a note
# (`PRINT`, SW45 rule 6). The estimate is not calibrated (A23); it errs wide.
PRINT_FLOOR = 0.7  # of the size on screen, whose maths is set at 1.1 em


def print_fit(bucket: int) -> str:
    """The rules of one width step: the width the sheet fits the equation to, and the note
    that shows where even 70% is too wide (the column, in em of its 1.125 rem text, is
    narrower than the equation at 70% of 1.1 em)."""
    narrower = round(bucket * PRINT_FLOOR * 1.1, 2)
    return (
        f"@media print {{ .pw-{bucket} {{ --pw: {bucket}; }}\n"
        f"@container (inline-size < {narrower:g}em) "
        f"{{ .pw-{bucket}::after {{ display: block; }} }} }}\n"
    )


def screen(added: dict[str, str]) -> str:
    """The sheet of one page: the core, then the parts the document used, by name."""
    return CORE + "".join(css for _, css in sorted(added.items()))


# ---------------------------------------------------------------------------------------
# EPUB: what the book leaves to the reading system (F-456, F-457): the page's colors, its
# font, base size and column. A reader's own theme (dark, sepia, large text), margins and font
# choice apply instead of being fought or doubled. The components keep their shape and their
# borders.
_PAGE_ONLY = frozenset(["body", "html", ":root"])
_COLOR_PROPERTIES = frozenset(["color", "background", "background-color", "color-scheme"])
_COLUMN_PROPERTIES = frozenset(["max-width", "margin", "padding"])
# The pictures, framed (the reading system's theme may be dark, and a picture is never
# inverted): the frame is the text's own color, so it shows on any theme.
EPUB_PICTURE_FRAME = "img { outline: 1px solid currentColor; outline-offset: -1px; }"


def _css_rules(css: str) -> list[tuple[str, str]]:
    """The top-level rules of a stylesheet as (prelude, body): `main { ... }` is ("main", " ..."),
    `@media x { a { ... } }` is ("@media x", " a { ... } "). Comments are dropped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: list[tuple[str, str]] = []
    depth = start = last = 0
    prelude = ""
    quote = ""
    for at, char in enumerate(css):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "{":
            if depth == 0:
                prelude, start = css[last:at], at + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                rules.append((prelude.strip(), css[start:at]))
                last = at + 1
    return rules


def epub(css: str) -> str:
    """The page's stylesheet as the book carries it: no page colors, font family, base size or
    column (the rules of `body`, `html` and `:root` go; `main` keeps only its word breaking),
    no color or background on a component (the reader's text color is on the reader's
    background, whichever theme), and no `prefers-color-scheme` or print block. What is
    left is the components' shape."""

    def declarations(body: str, drop: frozenset[str]) -> str:
        kept = [
            d.strip()
            for d in re.split(r";(?![^(]*\))", body)
            if d.strip() and d.split(":", 1)[0].strip().lower() not in drop
        ]
        return " ".join(f"{d};" for d in kept)

    def process(rules: list[tuple[str, str]]) -> list[str]:
        out: list[str] = []
        for prelude, body in rules:
            if prelude.startswith("@"):
                if "prefers-color-scheme" in prelude or re.search(r"\bprint\b", prelude):
                    continue
                if prelude.startswith(("@media", "@supports")):
                    inner = " ".join(process(_css_rules(body)))
                    if inner:
                        out.append(f"{prelude} {{ {inner} }}")
                else:
                    out.append(f"{prelude} {{{body}}}")
                continue
            selectors = [s.strip() for s in prelude.split(",")]
            if all(s in _PAGE_ONLY for s in selectors):
                continue
            drop = _COLOR_PROPERTIES | (_COLUMN_PROPERTIES if "main" in selectors else frozenset())
            kept = declarations(body, drop)
            if kept:
                out.append(f"{prelude} {{ {kept} }}")
        return out

    return "\n".join(process(_css_rules(css)))


# ---------------------------------------------------------------------------------------
# Canvas: what the inliner may carry. The CSS properties it writes into `style` attributes are
# those of the sheet that Canvas also keeps. Every one is in the sanitizer's `css_properties`
# (tests check this set against the encoded allowlist); a property of the sheet that is not
# here is dropped and recorded in `Notes.dropped_css`.
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
CANVAS_REWRITES = {("clip-path", "inset(50%)"): ("clip", "rect(0,0,0,0)")}
# Rules on these bare elements are the page's typography; Canvas has its own for a Page,
# so they are left to it in the paste version (the standalone page has none and keeps them).
CANVAS_BASELINE_TAGS = frozenset(
    ["body", "main", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li"]
)
# The fonts a `<math>` element asks for (T3-8): the stack of the screen sheet. Canvas keeps
# `font-family` in a `style` attribute but has no `@font-face` or style block, so the stack can
# only name fonts the reader's computer has.
CANVAS_MATH_RULE = f"\nmath {{ font-family: {math_font.MATH_FONT_STACK}; }}\n"
