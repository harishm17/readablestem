# Authored documents of the extended development set

Original documents written for a11y-stem (no third-party text), licence MIT like the code.
`build_authored.py` writes them; the committed copies are under `../dev/authored/<id>/`
(source, untagged PDF, `gold.jsonl`, images). Gold is known by construction: each equation
is set from LaTeX and stored with its MathML (`latex_to_mathml`), each heading, term,
code block and table is the text that was typeset. The text layer of every PDF is checked
against the gold (`--check`).

| Document | Toolchain | What it holds |
|---|---|---|
| `latex-exercises` | LaTeX, Tectonic | 50+ numbered exercises that open with an equation, inline stacked fractions in sentences, braced systems, piecewise functions with words |
| `latex-matrices` | LaTeX, Tectonic | determinants, matrices (round, square, augmented), braced systems, worked multi-row steps |
| `latex-chemistry` | LaTeX, Tectonic | chemical formulas, ions with charges, balanced equations with states, nuclide notation (stacked prefix scripts), units with exponents and chemical scripts in prose |
| `latex-slides` | LaTeX, Tectonic, beamer 16:9 | one large display formula per frame |
| `latex-structure` | LaTeX, Tectonic | multi-line headings, bold sentence headings, repeated labels, code blocks, glossary, data tables and layout tables |
| `latex-hyphenation` | LaTeX, Tectonic | two-column justified prose with words broken at line ends, some at a real hyphen |
| `word-lecture` | python-docx, exported by LibreOffice | Heading 1-3, bold run-in headings, repeated labels, real superscript and subscript runs (units, ions, nuclides), exercises typed as text, glossary, data and layout tables, hyphenation |
| `word-eq-images` | python-docx, exported by LibreOffice | 37 equations pasted as pictures (inline and displayed, some numbered), a data table and a layout table |
| `html-slides` | HTML, printed by Chromium (16:9) | large display equations with MathML, slide titles |
| `html-code-glossary` | HTML, printed by Chromium | code blocks, a key-terms list, data and layout tables |

`latex-exercises`, `latex-chemistry`, `word-lecture` and `word-eq-images` also have a
"check" variant (`<id>-chk`): a fresh draw from the same generators with other numbers,
words, elements and terms. Those four documents are the authored part of the sealed check
set (`../README.md`); dev and check share no heading text and no equation LaTeX (`--check`
proves it).

## Rebuilding

```
uv run --group browser --with python-docx --with pyphen python \
    data/dev-v2/authored/build_authored.py --out /tmp/pool --tectonic PATH
uv run python data/dev-v2/authored/build_authored.py --check /tmp/pool
```

Needs Tectonic 0.17 (a release binary; `tests/fixtures/layout/latex/make_pdfs.py` says how),
LibreOffice (`soffice`), Chromium (`browser` group) and, because python-docx and pyphen are
not project dependencies, `--with`. LibreOffice here has no English hyphenation dictionary,
so the builder takes `hyph_en_US.dic` from the `pyphen` package (or `$A11Y_HYPH_DIR`,
`$DICPATH`, `/usr/share/hyphen`) and passes it through `DICPATH`. Output is deterministic:
seeded generators, no clock, fixed PDF dates and ids, LibreOffice's PDF tagging off, so a
second build gives byte-identical files. No mhchem: chemistry is set with `\mathrm`, because
`latex_to_mathml` cannot read `\ce`.

Gold kinds: `equation` (`latex`, `mathml`, `display`, `appearance` text or image,
`exercise_number`, `equation_number` for pictured numbered ones), `heading`, `script`
(runs with `script`, `value`, `before`, `class`), `code`, `glossary`, `table`
(`html`, `is_layout`, `page`, `cluster`), `hyphen_break` (`left`, `right`, `word` as
printed, `compound`) and `exercise_text`. Every record has `page` (0-based PDF page),
`classes`, `variant`, `toolchain`, `pdf` and the licence. `image` paths are relative to the
gold folder (`dev/`).
