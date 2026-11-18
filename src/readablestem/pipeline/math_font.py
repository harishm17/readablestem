"""The maths font of the student file (WS21.4, wave 1a).

Native MathML draws with a font that has an OpenType MATH table (stretchy brackets, raised
scripts, radicals). A reader's device may have none, and then the browser falls back to a
text font and the equation reads as a row of letters. Two things help, and both are here:

- the stack of families a `<math>` element names (`MATH_FONT_STACK`, one constant for the
  student file's stylesheet and the Canvas export, which has no style block and writes it on
  each element); it helps a device that has one of them and changes nothing on one with none;
- the font itself, as a file, where a package can hold files: the EPUB (`epub_files`). The
  downloaded HTML is one file, and the page policy of `/view` allows no font file
  (`web.security.VIEW_CSP`), so there the stack is all. Canvas cannot carry a font at all.

The file is Latin Modern Math 1.959 under the GUST Font License (an instance of the LPPL
1.3c), vendored unmodified with its license and manifest for the review screen
(`web/static/vendor/README.md`, `THIRD_PARTY_NOTICES.md`); the license allows distributing a
complete unmodified copy, so the EPUB carries it as it is, with the license text beside it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import readablestem

# The fonts a `<math>` element asks for, in order (T3-8). Native MathML in Chrome, Edge,
# Firefox and Safari needs one with an OpenType MATH table; this list is the families that
# have one: Latin Modern Math and STIX Two Math (TeX distributions, Linux, recent macOS),
# Cambria Math (Windows, Office for Mac), Noto Sans Math, Libertinus, TeX Gyre, XITS, Asana
# and DejaVu (Linux packages), then the browser's own `math` family.
MATH_FONT_STACK = (
    "'Latin Modern Math','STIX Two Math','Cambria Math','Noto Sans Math','Libertinus Math',"
    "'TeX Gyre Pagella Math','XITS Math','Asana Math','DejaVu Math TeX Gyre',math"
)

FONT_FAMILY = "Latin Modern Math"
FONT_FILE = "latinmodern-math-1.959.woff2"
# What must accompany a distributed copy of the font.
LICENSE_FILES = ("GUST-FONT-LICENSE.txt", "MANIFEST-Latin-Modern-Math.txt")
_VENDOR = Path(readablestem.__file__).parent / "web" / "static" / "vendor" / "fonts"

# The EPUB's stylesheet rule: an installed copy is used first, else the file in the book.
EPUB_FONT_FACE = (
    f'@font-face {{ font-family: "{FONT_FAMILY}"; '
    f'src: local("{FONT_FAMILY}"), url(fonts/{FONT_FILE}) format("woff2"); }}'
)


@cache
def epub_files() -> dict[str, bytes]:
    """The font and its license files for an EPUB, by their path inside it ("fonts/..."); empty
    when the vendored files are not there (the book then has the stack alone)."""
    try:
        return {
            f"fonts/{name}": (_VENDOR / name).read_bytes() for name in (FONT_FILE, *LICENSE_FILES)
        }
    except OSError:
        return {}
