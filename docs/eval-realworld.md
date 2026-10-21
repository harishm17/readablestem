# A real-world evaluation set: arXiv papers, with the author's LaTeX as the answer key (2026-10-21)

Every number in [eval.md](eval.md), [PROOF.md](../PROOF.md) and the other eval pages comes from
Chromium prints of OpenStax HTML: the text layer of those PDFs is clean (one font, no ligature
trouble, every glyph has a Unicode value), which flatters a pipeline that reads the text layer. Real course
material is LaTeX (and Word) PDFs with math fonts, ligatures, accents drawn from several glyphs, figures
with their own text, tables full of mathematics, and glyphs with no ToUnicode map. This page is
the first measurement on that kind of PDF: how it was built, what it can and cannot say, what ReadableSTEM
and the chatbot recipe scored on it, in a development part and in a held-out part that was
registered before any system ran on it.

@@SHORT@@

## What the set is

**Papers and pages.** 40 arXiv papers, under CC BY 4.0 (the licence is read from the arXiv record;
CC BY 3.0 and CC0 would also be accepted, every other licence is skipped), in math-heavy
categories (math.PR, math.NA, math.HO, stat.TH, stat.ME, stat.ML, cs.LG, cs.DS, econ.EM, q-bio.QM,
physics.class-ph, ed-ph, flu-dyn, data-an, cond-mat.stat-mech), half of them lecture-note or tutorial
style by title (a heuristic: some "tutorials" are research papers). The document is the PDF arXiv
serves (compiled by the authors), cut to 4 to 6 chosen pages; the source is used for gold only.

| | Dev | Held-out |
|---|---|---|
| Papers | 25 | 15 |
| PDF pages | 114 | 71 |
| Gold equations | 2,350 | 1,826 |
| of which display / inline | 197 / 2,153 | 87 / 1,739 |
| single-symbol equations (one token) | 608 | 456 |
| typed in running text / a table / a figure / an algorithm | 1,908 / 223 / 177 / 42 | 1,500 / 237 / 69 / 20 |
| Gold equations per page | 20.6 | 25.7 |

Inline mathematics is 92% to 95% of the gold: a research paper's page is mostly `$x$`, `$n \to \infty$`
and `$\mathcal{O}(n^2)$` in running text, with a display equation every few pages. That is the opposite
of the OpenStax sets (where display equations are a third to a half), and it is why the report also gives
the score by display and inline.

**How the papers were chosen** (`src/readablestem/gold/arxiv_corpus.py`, `scripts/realworld_select.py`,
`scripts/realworld_build.py`; deterministic given the cached OAI records):

1. The arXiv OAI-PMH `ListRecords` of 15 sets over a window of months each (the record carries the licence, so
   no paper is fetched before its licence is known): 10,527 records with an accepted licence after de-duplicating
   cross-lists (about 35% to 45% of the records; recent arXiv papers are often CC BY), 10,165 inside the page range.
2. A seeded order (seed `realworld-2026-10-21`, SHA-256 of seed, set and id; sets in turn; tutorial and
   research alternating). The first 40 candidates that pass the gates are the set, and a candidate that fails a
   gate is skipped, never replaced by choice. 129 were visited: 40 accepted, 59 had fewer than 4 usable pages, 12 had
   more than 10% unconvertible equations, 9 no source files, 6 a page count outside 8 to 120, 2 a source that is itself a
   PDF, 1 an http error (`data/realworld/candidates.json`).
3. Pages: 4 to 6 per paper, drawn by hash among the pages where every equation is known (below).
4. Split: 15 held-out and 25 development, by hash, in proportion to style. The held-out split was registered
   (`data/realworld/PREREGISTRATION.md`, hashed in `REGISTRATION.sha256`) before any system had run on any
   part, and an addendum (sizing of the Opus arm) before any held-out run.

The corpus, the PDFs and the gold are kept outside the repository (`~/.cache/readablestem/realworld`), as
the held-out cache is; the repository has the scripts, the manifests (ids, titles, licences, chosen pages, SHA-256 of the PDF,
the source archive, the cut PDF and each gold file), the registration and the results.
`uv run python scripts/realworld_build.py verify` rebuilds the gold of every paper from the cached downloads and
checks it against the manifests.

## How the gold is made

No model is called and no TeX engine is needed (`src/readablestem/gold/arxiv_tex.py`,
`arxiv_gold.py`; tests in `tests/test_arxiv_tex.py` with small fixture sources):

1. **Flatten and expand.** `%` comments are dropped, `\input` and `\include` are followed, and the author's macros are
   read from the whole source (`\newcommand`, `\renewcommand`, `\providecommand`, `\def` with `#n` parameters,
   `\DeclareMathOperator`, `\let`, argument-free `\newenvironment`) and expanded, nested, until nothing changes. This is what lets
   `\be ... \ee` and `\R` and `\norm{x}` become the real thing. A recursive definition, a macro that grows without bound or a
   `\def` with a delimited parameter is recorded as a failure.
2. **Extract.** `equation`, `align`, `gather`, `multline`, `eqnarray`, `flalign`, `alignat`, `displaymath` and the starred
   forms, `$$..$$`, `\[..\]`, `\(..\)`, `$..$` and `math`, in order, with `\$` and `\verb` respected. Verbatim and listings are
   skipped. Floats (`figure`, `table`, `algorithm`) and pictures (`tikzpicture`) are extracted too but kept apart, because they are
   printed somewhere else than they are typed.
3. **Split.** An `align`, `eqnarray` or `flalign` with several numbered rows is one gold equation per numbered row (a
   run of unnumbered rows belongs to the next numbered row), a starred one is a single equation (as an `aligned` block),
   `gather` is one equation per row and `multline` one equation. This follows the layout stage's own rule (a derivation is one
   equation, two numbered rows stay apart; docs/layout.md). It is a convention, and the pipeline and the chatbot may
   write an alignment the other way (see "Limits").
4. **Place.** Prose words (four letters or more, maths removed) of the source are aligned to the words of the PDF
   with word 3-grams that occur once in both and a longest increasing subsequence. An equation lies between the nearest anchors
   before and after it; it is *placed* when both are on the same page and has an interval of pages otherwise. A float or a
   picture is placed by its own words, and when it has none it blocks the pages it may be on.
5. **Convert.** The same code the pipeline uses for its own LaTeX: `equation_step.clean_latex`, then
   `validate.safe_latex_to_mathml` (latex2mathml), so that gold and a prediction in LaTeX are compared like for like. A few
   author-level commands the converter does not know (`\ensuremath`, `\textcolor`, `\mathop`, `\arg`, `\limits`) are rewritten first.
   An equation that comes out with an unknown command, an unresolved `\ref` or `\cite`, or a converter error is *unconvertible*.
6. **Pick pages.** A page is usable when every equation printed on it is placed on that page (none has an interval that includes it),
   at least 90% of the page's words are in the source's prose (so nothing on the page comes from a file the extractor did not
   read), it has at least 5 equations, none of them unconvertible, and it is not page 1 (title, author lines and abstract math
   are in the preamble). Pages are then drawn by hash, 4 to 6 per paper.

The scorers are the document task's own (`eval/pipeline_eval.document_page_item`): gold and prediction are aligned in reading
order by the weighted LCS of the OpenStax runs, an equation is `exact_lenient` when the normalised MathML trees are equal, the symbols
scorer (`eval/equations_symbols.py`) credits splits and merges, and the report keys are those of docs/eval-page-math.md.

## Limits

- **The pages are easier than the papers.** A page with an equation the converter cannot convert (a command latex2mathml does not know, a bracket it
  cannot pair, an equation over the pipeline's 2,000-character LaTeX limit), with a `\ref` in a `\text`, or whose equations the anchors
  cannot place is not used. 59 of the 129 visited papers had fewer than 4 usable pages. A paper that is mostly
  hard mathematics is under-represented.
- **Gold boundaries are the author's math mode, not the visual formula.** An author who types `$R=$ $6.371\times10^6$ m` or
  `$^{21}$Mg` or `$m_n$ = 6` or `$F = q (\vec v \times \vec B$)` has the gold stop where the dollar sign stops. A reader sees the whole `R = 6.371 x 10^6 m`.
  A system that writes the whole formula is scored wrong on the `span` bucket. It applies to every arm, but more to a
  system that cuts by looking (ReadableSTEM's `span` bucket is 8.6% of the gold, the chatbots' 3.5%).
- **Alignments are one convention.** See step 3. A paper has 78 (dev) and 18 (held-out) gold equations from alignments
  out of 2,350 and 1,826: too few to say how a system treats them (the report gives the numbers anyway).
- **Mathematics inside embedded figures is not in the gold** (axis labels of a PDF figure are in the PDF text layer, not in the
  source). A system that reads them writes an equation with no gold partner (counted as unmatched, and as unsupported or not
  by the PDF's text). Math in a `tikzpicture` or in `\caption` is in the gold.
- **The style label is a heuristic** and the sets are only as varied as the sets, windows and seed.
- **No Word and no scans.** These are LaTeX PDFs only.
- **Float order.** Equations in tables and figures are placed on the right page, but inside a page they are ordered by where the
  float was typed, not where it is printed; the aligner's order tolerance absorbs most of it (a table's cells are
  adjacent), but it is a possible source of lost matches. The ReadableSTEM misses in tables below are real misses (it wrote no
  equation for those cells), not alignment artefacts (checked on one paper).
- **One run per arm**, no estimate of run-to-run variation (0.007 on the primary score on OpenStax), and
  15 held-out papers.

@@RESULTS@@
