"""Rebuild `src/readablestem/pipeline/data/glyph-names.tsv`, the glyph-name table.

    python3 scripts/make_glyph_names.py [GLYPHTOUNICODE.tex GLYPHTOUNICODE-CMEX.tex]

The two inputs are TeX's public glyph-name tables, as TeX Live (texlive-base 2025) ships
them: `tex/generic/pdftex/glyphtounicode.tex` (Adobe's glyph list and TeX's names, from
lcdf-typetools) and `tex/latex/latex-lab/glyphtounicode-cmex.tex` (the glyphs of the cmex
fonts). Where the second names a glyph the first also names, the second wins. A row is kept
only when the name stands for one real character: a variation selector (U+FE00 to U+FE0F),
which TeX uses to mark a sized variant, is dropped (`summationdisplay` is the sum sign), and
a name that stands for a private-use code point or a control code says nothing and is left
out, except the pieces in `UNICODE_PIECES`, which replace what the files give.
`pipeline/data/README.md` has the source, the licences and the SHA-256 of each input.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

TEX = Path("/usr/share/texlive/texmf-dist/tex")
DEFAULT_INPUTS = (
    TEX / "generic/pdftex/glyphtounicode.tex",
    TEX / "latex/latex-lab/glyphtounicode-cmex.tex",
)
OUT = Path(__file__).resolve().parent.parent / "src/readablestem/pipeline/data/glyph-names.tsv"
# Pieces of built-up signs that the files leave without a character, or give a combining mark
# that would stick to the character before it: Adobe's list has only a private-use code for the
# integral and arrow extensions (U+F8F5, U+F8E7), and the cmex file gives the vertical
# extensions of tall bars, arrows and radicals an overlay mark. Unicode has a character for each.
UNICODE_PIECES = {
    "integralex": "\u23ae",  # integral extension
    "arrowhorizex": "\u23af",  # horizontal line extension
    "vextendsingle": "\u23d0",  # vertical line extension
    "radicalvertex": "\u23d0",
    "vextenddouble": "\u2016",  # double vertical line
    "arrowvertexdbl": "\u2016",
}
ROW = re.compile(r"\\pdfglyphtounicode\{([^}]+)\}\{([0-9A-Fa-f ]+)\}")


def main(paths: list[Path]) -> None:
    names: dict[str, str] = {}
    for path in paths:
        for name, codes in ROW.findall(path.read_text(errors="replace")):
            names[name] = "".join(
                chr(int(code, 16))
                for code in codes.split()
                if not 0xFE00 <= int(code, 16) <= 0xFE0F
            )
    names.update(UNICODE_PIECES)
    rows = sorted(
        (name, text)
        for name, text in names.items()
        if len(text) == 1 and unicodedata.category(text) not in ("Co", "Cc", "Cn", "Cs")
    )
    OUT.write_text("".join(f"{name}\t{ord(text):04X}\n" for name, text in rows), encoding="ascii")
    print(f"{len(rows)} names -> {OUT}")


if __name__ == "__main__":
    main([Path(arg) for arg in sys.argv[1:]] or list(DEFAULT_INPUTS))
