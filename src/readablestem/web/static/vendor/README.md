# Vendored front-end files

Served from our own origin so the Content-Security-Policy can stay `script-src 'self'`
(no CDN). The app works without these files; they only enhance it.

| File | Package | Version | Licence | Source |
|---|---|---|---|---|
| `htmx-2.0.11.min.js` | `htmx.org` (npm) | 2.0.11 | 0BSD (Zero-Clause BSD) | `https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz`, file `package/dist/htmx.min.js` |
| `fonts/latinmodern-math-1.959.woff2` | Latin Modern Math, the same font repackaged as WOFF2 (see below) | 1.959 (2014-09-05) | GUST Font License (an instance of the LPPL 1.3c) | made from `fonts/latinmodern-math.otf` |
| `fonts/latinmodern-math.otf` | Latin Modern Math (GUST e-foundry) | 1.959 (2014-09-05) | GUST Font License (an instance of the LPPL 1.3c) | `https://mirrors.ctan.org/fonts/lm-math.zip`, file `lm-math/opentype/latinmodern-math.otf` |
| `fonts/lm-math-hero-1.959.woff2` | Latin Modern Math cut down to the 20 glyphs of the landing hero's equation, and renamed "LM Math Hero" (the GUST Font License asks that a derived font not keep the name) | 1.959 (2014-09-05) | GUST Font License (an instance of the LPPL 1.3c) | made by `scripts/hero_assets.py --font` from `fonts/latinmodern-math.otf`; 7.8 KB against 392 KB |
| `fonts/source-sans-3-5.3.1-{latin,greek}-{400,700}.woff2` | Source Sans 3 (Adobe), from `@fontsource/source-sans-3` | 5.3.1 (Fontsource), upstream v20 | SIL Open Font License 1.1 (`fonts/OFL-source-sans-3.txt`) | `https://registry.npmjs.org/@fontsource/source-sans-3/-/source-sans-3-5.3.1.tgz`, `files/source-sans-3-{latin,greek}-{400,700}-normal.woff2`, renamed |
| `fonts/source-serif-4-5.3.1-{latin,greek}-600.woff2` | Source Serif 4 (Adobe), from `@fontsource/source-serif-4` | 5.3.1 (Fontsource), upstream v15 | SIL Open Font License 1.1 (`fonts/OFL-source-serif-4.txt`) | `https://registry.npmjs.org/@fontsource/source-serif-4/-/source-serif-4-5.3.1.tgz`, `files/source-serif-4-{latin,greek}-600-normal.woff2`, renamed |
| `fonts/source-code-pro-5.3.1-latin-400.woff2` | Source Code Pro (Adobe), from `@fontsource/source-code-pro` | 5.3.1 (Fontsource), upstream v32 | SIL Open Font License 1.1 (`fonts/OFL-source-code-pro.txt`) | `https://registry.npmjs.org/@fontsource/source-code-pro/-/source-code-pro-5.3.1.tgz`, `files/source-code-pro-latin-400-normal.woff2`, renamed |

The Source fonts are the Fontsource woff2 files as published (latin and greek subsets, not
modified; the Greek files are only fetched by a page that has Greek letters, through
`unicode-range` in `tokens.css`). They are one download each, under 22 KB, and a typical page
loads about 59 KB of fonts. `tests/test_design_tokens.py` keeps the set under 120 KB. The
Reserved Font Name clause of the OFL does not apply to unmodified files.

`tests/test_hero_assets.py` checks the hero font's SHA-256 against `static/hero/hero.json`. It is a
derived font, so a new glyph set needs a new file name (the files here are immutable).

Integrity (checked by `tests/test_web_pages.py`):

- SHA-256 of `htmx-2.0.11.min.js`:
  `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`
- SHA-384 (the `integrity` attribute in `templates/base.html`):
  `sha384-2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg`
- npm tarball integrity (verified on download, 2026-10-07):
  `sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==`

Latin Modern Math (checked by `tests/test_web_pages.py`):

- SHA-256 of `fonts/latinmodern-math.otf`:
  `6075562b771f8b82f0c179e363389684f2dd09de30038269e2628e504bd7be0f` (the same file is
  in TeX Live)
- SHA-512 of the CTAN `lm-math.zip` it came from (downloaded 2026-10-09):
  `2f0c58cc16cb9425b227b9cd68d4f36e68d13e99d7132b1bda3c1813a1d605647cc249224bff96329cdfcf80393857095e65d7010fd2861747fb01a8c08dc1e6`
- Beside it, from the same archive (`lm-math/doc/`): `GUST-FONT-LICENSE.txt`,
  `MANIFEST-Latin-Modern-Math.txt`, `README-Latin-Modern-Math.txt`.

The font is not changed (no subsetting, no renaming): the GUST Font License requests that
a derived font be renamed. The review screen draws the proposal of an equation in it
(`app.css`, `.cmp-math`), so the same-size compare shows a LaTeX PDF's Computer Modern
next to the same family; the browser only downloads it when an equation is shown, and uses
an installed copy if there is one. What it downloads is the WOFF2 file (392 KB, against
733 KB for the OpenType file, which stays here as the source and for the tests). WOFF2 is a
lossless packaging of the same tables: every table of the two files is byte for byte the
same except `head`, which has the packaging's flag (bit 11) and its own checksum. Made
with fontTools and Brotli (not dependencies of this project) from the file above:

```
uv run --no-project --with fonttools --with brotli python -c "
from fontTools.ttLib import TTFont
f = TTFont('fonts/latinmodern-math.otf', recalcTimestamp=False)
f.flavor = 'woff2'
f.save('fonts/latinmodern-math-1.959.woff2')"
```

- SHA-256 of `fonts/latinmodern-math-1.959.woff2`:
  `b11060f37967c090457580275129d0d7da1d5fbfc82b04e78c91aa31977f547c`

Files under `vendor/` are served with `Cache-Control: public, max-age=31536000, immutable`
(`web/assets.py`), so a changed file must get a new name, as the htmx and font files do
(the version is in it).

htmx 1.x was BSD-2-Clause; from 2.0 the package is published under 0BSD (its `LICENSE`
file reads "Zero-Clause BSD"). 0BSD requires no attribution, but it is listed in
`THIRD_PARTY_NOTICES.md` anyway.

## Updating

1. Download the new tarball from the npm registry and check its `dist.integrity`.
2. Copy `package/dist/htmx.min.js` here as `htmx-<version>.min.js`; delete the old file.
3. Update the file name and `integrity` in `templates/base.html`, and this table.
4. Run `uv run pytest tests/test_web_pages.py` (hash check) and the browser tests.

htmx is configured in `base.html` (`<meta name="htmx-config">`) to work under the strict
CSP: no `eval`, no injected `<style>`, no script tags from responses, no history cache
(so no document content is copied into `localStorage`), same-origin requests only.
