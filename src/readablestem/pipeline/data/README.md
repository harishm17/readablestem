# Data files of the pipeline

## words-en.txt.gz

A list of 63,849 lower-case English words, one per line, sorted, gzip-compressed (168 KB).
`pipeline/hyphens.py` uses it to decide whether a hyphen at the end of a line was put there
by line breaking ("veloc-" and "ity") or belongs to a compound ("well-" and "known"). It is
read only to answer "is this a word"; no text from a document is ever compared against
anything outside the machine.

| Field | Value |
|---|---|
| Source | the Debian/Ubuntu `wamerican` package, version 2020.12.07-4build1 (`/usr/share/dict/american-english`), built from SCOWL (Spell Checker Oriented Word Lists) by Kevin Atkinson, http://wordlist.sourceforge.net/ |
| Licence | the SCOWL permission notice (a permissive licence: use, copy, modify, distribute and sell the lists "for any purpose ... without fee", provided the copyright notice and the permission notice appear in all copies); the text is below and in `THIRD_PARTY_NOTICES.md`. The parts SCOWL takes from the Moby word lists are public domain |
| Filter | only entries made of two or more letters a-z, all lower case: no proper nouns (they start with a capital), no possessives, no single letters, no abbreviations with punctuation |
| SHA-256 of the `.gz` file | `1e2e548ab2be7d641a24470ec121034f6d9b03f36df8a0b66fbe2ecaddbfbb8e` (`tests/test_layout_hyphens.py` fails if it stops matching) |

Rebuild (the file is byte for byte the same on any machine with the same package):

```
python3 -c "import re,gzip,io; ws=sorted({w.strip() for w in open('/usr/share/dict/american-english') if re.fullmatch(r'[a-z]{2,}',w.strip())}); b=io.BytesIO(); f=gzip.GzipFile(fileobj=b,mode='wb',mtime=0,compresslevel=9); f.write(('\n'.join(ws)+'\n').encode()); f.close(); open('words-en.txt.gz','wb').write(b.getvalue())"
```

### Copyright and permission notice (extracted from the SCOWL README, as the package ships it)

    The collective work is Copyright 2000-2011 by Kevin Atkinson as well
    as any of the copyrights mentioned below:

      Copyright 2000-2011 by Kevin Atkinson

      Permission to use, copy, modify, distribute and sell these word
      lists, the associated scripts, the output created from the scripts,
      and its documentation for any purpose is hereby granted without fee,
      provided that the above copyright notice appears in all copies and
      that both that copyright notice and this permission notice appear in
      supporting documentation. Kevin Atkinson makes no representations
      about the suitability of this array for any purpose. It is provided
      "as is" without express or implied warranty.

The word list is general English. A technical word it lacks ("eigen-" and "vector") is
joined as one word (see `hyphens.py`), the way hyphenation breaks are repaired without a
list.

## glyph-names.tsv

Glyph name to character, one `name<TAB>HEX` row per line (135 rows, 3 KB, sorted), after a
notice in `#` lines. `pipeline/glyph_names.py` reads the name an embedded font gives a glyph
and looks it up here, for the glyphs MuPDF cannot decode: TeX's extension fonts (CMEX10,
CMEX9, txex, LMMathExtension10) name their sum and integral signs, radicals, big brackets and
the pieces of built-up ones, and the file's table to Unicode has no row for them. Only the
glyph names of the font in the PDF in hand are read; no text from a document is compared with
anything outside the machine.

| Field | Value |
|---|---|
| Source | one TeX file, as TeX Live (texlive-base 2025, Debian/Ubuntu package) ships it: `tex/latex/latex-lab/glyphtounicode-cmex.tex`, the glyphs of the cmex fonts (Copyright 2008 Han The Thanh, 2014 Peter Selinger, 2018 Ross Moore; part of latex-lab, the LaTeX Project). The unchanged file: https://ctan.org/pkg/latex-lab |
| License | the LaTeX Project Public License 1.3c (TeX Live's `Licenses` file lists `latex-lab: lppl1.3c`; https://www.latex-project.org/lppl/lppl-1-3c/). The table is a changed extract, and says so in its first lines, with the changes below; the authors of the original file do not support it |
| Changes | the variation selectors U+FE00 to U+FE0F, which TeX uses to mark a sized variant, are dropped (`summationdisplay` is the sum sign); a name that stands for more than one character, a private-use code point or a control code says nothing and is left out; the rows under "Added by hand" |
| Added by hand | the pieces the file leaves without a character or gives a combining mark: `integralex` U+23AE, `arrowhorizex` U+23AF, `vextendsingle` and `radicalvertex` U+23D0, `vextenddouble` and `arrowvertexdbl` U+2016; and `lscript` U+2113, the script l of TeX's math italic fonts (`BY_HAND` in the script) |
| Not used | pdfTeX's larger `glyphtounicode.tex` (Adobe's glyph list and lcdf-typetools' TeX names). TeX Live lists its package, and CTAN lists lcdf-typetools, under the GPL, and the file itself has no license line. On 3,491 pages of 282 development files it named one undecoded glyph this table did not (`lscript`, 10 glyphs, now added by hand); MuPDF already reads the names in Adobe's list. A name this table lacks comes out as the gap mark, which the lint fails on, so a missing name is seen and can be added here |
| SHA-256 of the input | `glyphtounicode-cmex.tex` `882b003b5d22a53d6ef52099e5a846e673a05ec0ef211871a2b7f18a0388c4f0` |

Rebuild (the same input gives the same file):

```
python3 scripts/make_glyph_names.py
```
